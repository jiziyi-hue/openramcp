"""
OpenRA MCP server.

Exposes atomic RTS commands as MCP tools so Claude Code (or any MCP client)
can drive an OpenRA game over a TCP bridge. The C# side is implemented as
the MCPBridgeTrait in `trait_src/MCPBridgeTrait.cs`.

Run (stdio MCP, picked up by Claude Code config):
    python -m mcp_server.server

Or directly:
    python server.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from pathlib import Path

from .transport import OpenRATransport
from . import schema as S
from . import interpreter as I
from .logging import SessionLogger

# Scout log path — written by scout_daemon, read by latest_scout_report.
SCOUT_LOG = Path(os.environ.get(
    "SCOUT_LOG_PATH",
    str(Path(__file__).resolve().parent.parent / "scout_events.jsonl")
))


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

HOST = os.environ.get("OPENRA_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENRA_BRIDGE_PORT", "7777"))

transport = OpenRATransport(host=HOST, port=PORT)
mcp = FastMCP(
    "openra-bridge",
    instructions=(
        "Drive an OpenRA RTS game via natural-language commands. "
        "You (Claude) act as the player's chief of staff: translate intents "
        "like 'build 3 refineries and push the right flank' into atomic tool "
        "calls. Tools return JSON describing world state, ordered units, and "
        "any errors. Always call get_state() before issuing a multi-step plan."
    ),
)


def _send(cmd_model) -> dict:
    """Serialize a pydantic command and send over TCP."""
    payload = cmd_model.model_dump(mode="json")
    return transport.send_command(payload)


# ---------------------------------------------------------------------------
# Information tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_state(include_enemies: bool = True) -> dict:
    """Get current world state: own/enemy units, cash, power, tick, paused.

    Always start a session with this. Returns a compact JSON snapshot.
    """
    return _send(S.CmdGetState(include_enemies=include_enemies))


@mcp.tool()
def list_units(owner: Optional[str] = None, kind: Optional[str] = None) -> dict:
    """List units, filtered by owner ('self' / 'enemy' / None) and kind.

    Example: list_units(owner='self', kind='Soldier') → all your infantry.
    """
    return _send(S.CmdListUnits(owner=owner, kind=kind))


@mcp.tool()
def find_unit(description: str) -> dict:
    """Resolve a fuzzy natural-language description to actor IDs.

    e.g. find_unit('the tank near the eastern refinery') →
    {ok: true, units: [{id: 42, kind: 'HeavyTank', pos: {x:.., y:..}}]}
    """
    return _send(S.CmdFindUnit(description=description))


# ---------------------------------------------------------------------------
# Game flow tools
# ---------------------------------------------------------------------------

@mcp.tool()
def pause() -> dict:
    """Pause the game (single-player only)."""
    return _send(S.CmdPause())


@mcp.tool()
def resume() -> dict:
    """Resume the game."""
    return _send(S.CmdResume())


@mcp.tool()
def screenshot() -> dict:
    """Capture current viewport as base64 PNG.

    Lets Claude visually verify what the player sees. Returns
    {ok: true, screenshot_b64: '...'}.
    """
    return _send(S.CmdScreenshot())


# ---------------------------------------------------------------------------
# Group commands — operate on named cohorts of the player's units.
#
# Strategy: the player's units are auto-partitioned into N named groups (default
# 3, axis = Y, names = north/center/south). First call to list_groups initializes
# the partition. After that, move_group / attack_group / stance_group act on
# every live unit in the named cohort.
#
# New units (trained mid-game) are NOT auto-added to any group. Call
# assign_to_group(group, unit_ids) or rebalance_groups() to include them.
# ---------------------------------------------------------------------------

@mcp.tool()
def list_groups() -> dict:
    """List current groups: name, count, composition by kind, avg HP, center pos.

    First call partitions current player units (axis=Y, N=3 → north/center/south).
    Returns {ok: true, groups: [{name, count, composition: {kind: n}, avg_hp_pct, center: {x,y}, unit_ids: [...]}, ...]}
    """
    return _send(S.CmdListGroups())


@mcp.tool()
def move_group(group: str, target_x: int, target_y: int, attack_move: bool = False) -> dict:
    """Move a named group toward a target cell.

    group: e.g. 'north', 'center', 'south'.
    attack_move: if True, units engage enemies on the way (A-move).
    """
    return _send(S.CmdMoveGroup(group=group, target=S.Vec2(x=target_x, y=target_y), attack_move=attack_move))


@mcp.tool()
def attack_group(group: str, target_id: int) -> dict:
    """Order an entire named group to focus-fire one enemy actor."""
    return _send(S.CmdAttackGroup(group=group, target_id=target_id))


@mcp.tool()
def stance_group(group: str, stance: str) -> dict:
    """Set engagement stance for the whole group.

    stance: HoldFire | ReturnFire | Defend | AttackAnything
    """
    return _send(S.CmdStanceGroup(group=group, stance=stance))


@mcp.tool()
def assign_to_group(group: str, unit_ids: list[int]) -> dict:
    """Move units into a named group (creates the group if missing).

    Removes the units from any other groups first.
    """
    return _send(S.CmdAssignToGroup(group=group, unit_ids=unit_ids))


@mcp.tool()
def rebalance_groups(count: int = 3, axis: str = "y") -> dict:
    """Re-partition all player units into N groups along the chosen axis.

    count: 2 / 3 / N. Auto-names: (3,y)→north/center/south, (3,x)→west/center/east,
    (2,y)→north/south, (2,x)→west/east, else g0..gN.
    """
    return _send(S.CmdRebalanceGroups(count=count, axis=axis))


# ---------------------------------------------------------------------------
# High-level intent dispatcher (DSL → deterministic Python → atomic MCP)
# ---------------------------------------------------------------------------

def _dispatch_logged(intent: dict, meta: Optional[dict], client: str) -> dict:
    """Shared dispatch+log helper used by dispatch_intent / any other tool
    that runs the DSL interpreter and wants logging."""
    import time as _t
    t0 = _t.perf_counter()
    world_before = transport.send_command({"type": "get_state", "include_enemies": True})
    result = I.interpret(intent, transport)
    latency_ms = int((_t.perf_counter() - t0) * 1000)
    world_after = transport.send_command({"type": "get_state", "include_enemies": True})
    try:
        SessionLogger.current().log_decision(
            intent_payload=intent,
            result=result,
            meta=meta,
            world_before=world_before,
            world_after=world_after,
            latency_ms=latency_ms,
            client=client,
        )
    except Exception:
        pass
    return result


@mcp.tool()
def dispatch_intent(intent: dict, meta: Optional[dict] = None) -> dict:
    """Dispatch ONE high-level Intent (DSL JSON) to OpenRA.

    Prefer this tool over chaining the low-level atomics. The interpreter
    resolves names → ids, computes waypoints, and issues the right sequence
    of atomic commands deterministically, so the LLM only has to fill enum
    fields.

    Args:
        intent: the DSL JSON payload (see docs/INTENT_DSL.md).
        meta:   optional dict capturing LLM-side observability for the paper:
                {nl_input: str,             # raw player text
                 llm_model: str,            # e.g. "claude-opus-4-7"
                 llm_latency_ms: int,       # round-trip from prompt to your reply
                 llm_input_tokens: int,
                 llm_output_tokens: int}
                You SHOULD fill `meta` on every call so the decision log can
                track amplification + cost. Leave empty if running scripts.

    Returns: {ok, narrative, actions_taken, error?}.
    """
    return _dispatch_logged(intent, meta, "dispatch_intent")


@mcp.tool()
def end_session(result: str = "draw", end_tick: int = -1,
                notes: str = "") -> dict:
    """Finalize the current decision-logging session and emit session_summary.json.

    Call when the game ends (player says GG / detected by you) so paper metrics
    get computed. result: win | lose | draw. notes: free-text (optional).
    A fresh session starts on next dispatch.

    Returns: {ok, summary: {nl_commands, atomic_orders, mean_amplification_ratio,
              apm, template_switches, ...}}
    """
    summary = SessionLogger.current().finalize({
        "result": result,
        "end_tick": end_tick,
        "notes": notes,
    })
    # Reset so next game uses a new session_id.
    SessionLogger.reset()
    return {"ok": True, "summary": summary}


@mcp.tool()
def session_info() -> dict:
    """Return current logging session metadata (id, paths, started_ts).

    Useful for the LLM to reference logs by id in the chat with the player.
    """
    s = SessionLogger.current()
    return {
        "ok": True,
        "session_id": s.session_id,
        "log_dir": str(s.dir),
        "decisions_path": str(s.decisions_path),
        "summary_path": str(s.summary_path),
        "start_ts": s.start_ts,
    }


# ---------------------------------------------------------------------------
# Vocab (controlled enums for the LLM)
# ---------------------------------------------------------------------------

@mcp.tool()
def vocab() -> dict:
    """Controlled vocabulary for the DSL the LLM emits via dispatch_intent.

    Call this when the player asks 'what can I say?' or to verify enum values
    before dispatching. All lists come from typing.get_args of the DSL enums —
    single source of truth.
    """
    from typing import get_args as _get_args
    from . import intent_dsl as D
    return {
        "ok": True,
        "verbs": [
            "attack", "defend", "retreat", "regroup", "scout",
            "feint", "pincer",
            "harass", "patrol", "escort", "contain", "diversion",
            "set_stance", "report",
        ],
        "defense_state": list(_get_args(D.DefenseState)),
        "scout_priority": list(_get_args(D.ScoutPriority)),
        "stances": list(_get_args(D.Stance)),
        "approaches": list(_get_args(D.Approach)),
        "named_targets": list(_get_args(D.NamedTarget)),
        "named_regions": list(_get_args(D.NamedRegion)),
        "groups": ["north", "center", "south", "all"],
        "report_what": list(_get_args(D.ReportWhat)),
        "filter_fields": [
            "owner", "unit_kind", "hp_below", "hp_above", "in_group",
            "harass_capable",
        ],
        # Alert state engine — invoked via set_alert_state(level=...).
        "alert_states": [s.value for s in _AlertState],
        # Mission objective — invoked via set_objective(name=..., tick=...).
        "objectives": [o.value for o in _Objective],
        "objective_suggested_state": {
            o.value: _objective_to_suggested_state(o).value
            for o in _Objective
        },
    }


@mcp.tool()
def clarify(player_command: str,
            candidates: Optional[list[dict]] = None,
            reason: str = "ambiguous") -> dict:
    """Use when player intent is unclear. Builds a normalized multi-choice
    payload for the LLM to relay back to the player.

    player_command: raw player text
    candidates:     optional list of {label, intent, why} dicts the LLM
                    considered. If empty, vocab is surfaced as a nudge.
    reason:         ambiguous | unknown_term | conflicting_orders | needs_target
                    | missing_force | missing_template

    Returns: {ok, needs_clarification, player_command, reason, candidates,
              vocab_hint, prompt_template}
    """
    voc = vocab()
    cands = candidates or []
    if not cands:
        return {
            "ok": True,
            "needs_clarification": True,
            "player_command": player_command,
            "reason": reason,
            "candidates": [],
            "vocab_hint": voc,
            "prompt_template": (
                f"我没听清 '{player_command}'. 你想:\n"
                f"  • 哪个动作? ({', '.join(voc['verbs'][:8])})\n"
                f"  • 防御态? ({', '.join(voc['defense_state'])})\n"
                f"  • 哪个目标? ({', '.join(voc['named_targets'][:6])})"
            ),
        }
    return {
        "ok": True,
        "needs_clarification": True,
        "player_command": player_command,
        "reason": reason,
        "candidates": cands[:4],
        "prompt_template": (
            f"你说 '{player_command}', 我猜可能是:\n"
            + "\n".join(
                f"  {i + 1}. {c.get('label', '?')} — {c.get('why', '')}"
                for i, c in enumerate(cands[:4])
            )
            + "\n回 1-4 选, 或重新说明."
        ),
    }


# ---------------------------------------------------------------------------
# Scout daemon integration — read events written by scout_daemon.py
# ---------------------------------------------------------------------------

@mcp.tool()
def latest_scout_report(seconds_back: int = 120, max_events: int = 20,
                        min_severity: str = "info") -> dict:
    """Read recent events from the background scout daemon.

    Start the daemon separately: `python -m mcp_server.scout_daemon`
    (or `scripts/run_scout.bat`). It polls every SCOUT_POLL_SECONDS (default
    30s) and writes events to <project_root>/scout_events.jsonl.

    Parameters:
        seconds_back   look back this many seconds (default 120)
        max_events     cap on returned events (default 20)
        min_severity   debug | info | warn | alert | error

    Returns: {ok, events: [...], counts: {kind: n}, daemon_running: bool}
    """
    severity_order = {"debug": 0, "info": 1, "warn": 2, "alert": 3, "error": 4}
    min_level = severity_order.get(min_severity, 1)

    import time as _time
    if not SCOUT_LOG.exists():
        return {"ok": True, "events": [], "counts": {},
                "daemon_running": False,
                "note": f"no scout log at {SCOUT_LOG}. start scout_daemon."}

    cutoff = _time.time() - max(1, seconds_back)
    events = []
    try:
        with open(SCOUT_LOG, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("timestamp", 0) < cutoff:
                    continue
                if severity_order.get(ev.get("severity", "info"), 1) < min_level:
                    continue
                events.append(ev)
    except OSError as e:
        return {"ok": False, "error": f"read failed: {e}", "events": []}

    events = events[-max_events:]
    counts = {}
    for ev in events:
        k = ev.get("kind", "?")
        counts[k] = counts.get(k, 0) + 1

    daemon_running = False
    try:
        daemon_running = (_time.time() - SCOUT_LOG.stat().st_mtime) < 90
    except OSError:
        pass

    return {"ok": True, "events": events, "counts": counts,
            "daemon_running": daemon_running, "log_path": str(SCOUT_LOG)}


# ---------------------------------------------------------------------------
# Watcher — long-poll until an event matches, or timeout.
# ---------------------------------------------------------------------------

@mcp.tool()
def wait_for_event(condition: dict, timeout_s: int = 120,
                   poll_interval_s: int = 5) -> dict:
    """Block up to `timeout_s` seconds polling for an event matching `condition`.

    Intended for use by a background subagent: it calls this, blocks, and
    when it returns the subagent reports back. Main session is free to chat.

    `condition` is a dict. Single-condition forms:

        {"type": "scout_kind", "kind": "enemy_at_base"}
        {"type": "scout_severity_min", "severity": "warn"}
        {"type": "self_count_below", "count": 5}
        {"type": "enemy_count_above", "count": 10}
        {"type": "cash_above", "cash": 5000}
        {"type": "cash_below", "cash": 500}
        {"type": "unit_killed", "actor_id": 42}
        {"type": "tick_reached", "tick": 5000}

    Composite forms:
        {"any_of": [c1, c2, ...]}
        {"all_of": [c1, c2, ...]}

    Returns: {ok, matched, elapsed_s, polls, condition, note?}
    """
    import time as _time

    start = _time.time()
    polls = 0
    while _time.time() - start < timeout_s:
        polls += 1
        if _condition_match(condition):
            return {"ok": True, "matched": True,
                    "elapsed_s": int(_time.time() - start), "polls": polls,
                    "condition": condition}
        _time.sleep(max(1, poll_interval_s))
    return {"ok": True, "matched": False,
            "elapsed_s": int(_time.time() - start), "polls": polls,
            "condition": condition, "note": "timeout"}


def _condition_match(condition: dict) -> bool:
    """Evaluate one condition. Helper for wait_for_event."""
    if "any_of" in condition:
        return any(_condition_match(c) for c in condition["any_of"])
    if "all_of" in condition:
        return all(_condition_match(c) for c in condition["all_of"])

    typ = condition.get("type")

    if typ in ("scout_kind", "scout_severity_min"):
        report = latest_scout_report(seconds_back=10, max_events=50,
                                      min_severity="debug")
        if not report.get("ok"):
            return False
        sev = {"debug": 0, "info": 1, "warn": 2, "alert": 3, "error": 4}
        for ev in report["events"]:
            if typ == "scout_kind" and ev.get("kind") == condition["kind"]:
                return True
            if typ == "scout_severity_min":
                if sev.get(ev.get("severity", "info"), 1) >= \
                   sev.get(condition["severity"], 1):
                    return True
        return False

    # Direct-bridge conditions
    state_resp = transport.send_command({"type": "get_state",
                                          "include_enemies": True})
    if not state_resp.get("ok"):
        return False
    s = state_resp["state"]
    self_count = len(s.get("self_units", []))
    enemy_count = len(s.get("enemy_units", []))
    cash = s.get("self_cash", 0)
    tick = s.get("tick", 0)

    if typ == "self_count_below":
        return self_count < condition["count"]
    if typ == "enemy_count_above":
        return enemy_count > condition["count"]
    if typ == "cash_above":
        return cash > condition["cash"]
    if typ == "cash_below":
        return cash < condition["cash"]
    if typ == "tick_reached":
        return tick >= condition["tick"]
    if typ == "unit_killed":
        target_id = condition["actor_id"]
        all_units = s.get("self_units", []) + s.get("enemy_units", [])
        return not any(u["id"] == target_id for u in all_units)

    return False


# ---------------------------------------------------------------------------
# Tactical engine — daemon-level reaction (engage on contact, cohesion,
# auto-defense). Wired by interpreter.py for offensive intents; explicit
# tools below let the player toggle perimeter defense and inspect status.
# ---------------------------------------------------------------------------

from .tactical import (
    get_engine as _get_tactical_engine,
    AlertState as _AlertState,
    Objective as _Objective,
    objective_to_suggested_state as _objective_to_suggested_state,
    ALERT_STATE_CONFIG as _ALERT_STATE_CONFIG,
)


def _resolve_named_cell(name: str) -> Optional[dict]:
    """Resolve a NamedTarget enum to {x,y} for tactical tools. Returns None
    if the world doesn't expose that landmark right now."""
    st = transport.send_command({"type": "get_state", "include_enemies": True})
    if not st.get("ok"):
        return None
    s = st["state"]
    self_units = s.get("self_units", [])
    enemy_units = s.get("enemy_units", [])

    if name == "self_base":
        for u in self_units:
            if u.get("kind", "").lower() == "fact":
                return {"x": u["pos"]["x"], "y": u["pos"]["y"]}
    if name in ("self_center", "self_base"):
        if not self_units: return None
        return {"x": sum(u["pos"]["x"] for u in self_units) // len(self_units),
                "y": sum(u["pos"]["y"] for u in self_units) // len(self_units)}
    if name in ("enemy_center", "enemy_base"):
        if not enemy_units: return None
        return {"x": sum(u["pos"]["x"] for u in enemy_units) // len(enemy_units),
                "y": sum(u["pos"]["y"] for u in enemy_units) // len(enemy_units)}
    return None


@mcp.tool()
def tactical_status() -> dict:
    """Inspect the tactical daemon — active assaults, retargets, cohesion halts,
    perimeter defense state. Useful for verifying that offensive intents got
    handed to the daemon and that it's reacting."""
    engine = _get_tactical_engine(transport)
    return {"ok": True, **engine.status()}


@mcp.tool()
def enable_auto_defense(center_x: Optional[int] = None,
                        center_y: Optional[int] = None,
                        center_named: Optional[str] = "self_base",
                        radius: int = 18) -> dict:
    """ADD a perimeter auto-defense zone. Multiple zones can be active
    concurrently — every call adds a new zone (does NOT replace existing
    ones). When an enemy mobile unit enters the radius around the center,
    nearby idle combat units focus-fire it without waiting for an LLM
    round-trip.

    Pass `center_x` + `center_y` for an explicit cell, or `center_named`
    ("self_base" / "self_center" / "enemy_center" / "enemy_base"). Default
    center = self_base (the player's first ConstructionYard).

    Returns: {ok, zone_id, center, radius, narrative}.
    """
    if center_x is None or center_y is None:
        if center_named is None:
            return {"ok": False, "error": "need center_x/y or center_named"}
        cell = _resolve_named_cell(center_named)
        if cell is None:
            return {"ok": False, "error": f"could not resolve {center_named}"}
        cx, cy = cell["x"], cell["y"]
    else:
        cx, cy = int(center_x), int(center_y)

    engine = _get_tactical_engine(transport)
    zone_id = engine.enable_auto_defense((cx, cy), radius=radius)
    return {"ok": True, "zone_id": zone_id, "center": [cx, cy], "radius": radius,
            "narrative": f"Auto-defense zone #{zone_id} armed at ({cx},{cy}) radius {radius}."}


@mcp.tool()
def disable_auto_defense(zone_id: Optional[int] = None) -> dict:
    """Disable one perimeter (pass `zone_id`) or ALL perimeters (omit it).

    Returns: {ok, removed, narrative}. `removed` is the number of zones
    that were active and got disabled.
    """
    engine = _get_tactical_engine(transport)
    removed = engine.disable_auto_defense(zone_id)
    if zone_id is None:
        msg = f"Auto-defense disarmed ({removed} zone(s) cleared)."
    else:
        msg = (f"Auto-defense zone #{zone_id} disarmed."
               if removed else f"No active zone with id {zone_id}.")
    return {"ok": True, "removed": removed, "narrative": msg}


@mcp.tool()
def list_defense_perimeters() -> dict:
    """List all active auto-defense perimeters with their zone_ids.

    Returns: {ok, perimeters: [{zone_id, center: [x,y], radius}, ...]}.
    Useful when you want to disable a specific zone (need its id) or just
    verify how many are running.
    """
    engine = _get_tactical_engine(transport)
    return {"ok": True, "perimeters": engine.list_perimeters()}


@mcp.tool()
def cancel_assaults(mission_id: Optional[int] = None) -> dict:
    """Cancel one or all daemon-tracked offensive missions. Pass mission_id
    to cancel one; omit to cancel all. The actors keep their last orders
    until you issue a new dispatch_intent — this only stops the daemon
    from re-engaging."""
    engine = _get_tactical_engine(transport)
    if mission_id is None:
        n = engine.cancel_all_assaults()
        return {"ok": True, "cancelled": n,
                "narrative": f"Cancelled {n} assault(s)."}
    ok = engine.cancel_assault(int(mission_id))
    return {"ok": ok, "cancelled": 1 if ok else 0,
            "narrative": f"{'Cancelled' if ok else 'Not found'} assault {mission_id}."}


@mcp.tool()
def list_pending_missions() -> dict:
    """List missions whose force resolution returned empty and are waiting
    for matching units to appear (e.g. harass queued before any harass-
    capable unit is trained).

    Each entry: {pending_id, intent_kind, intent_payload, queued_at_tick,
                 queued_at_ts, age_s, reason}. The daemon re-attempts force
    resolution every few seconds; once it succeeds the mission is dispatched
    and a `pending_dispatched` event is pushed to scout_events.jsonl.

    Cancel an entry with `cancel_pending(pending_id)`.
    """
    engine = _get_tactical_engine(transport)
    return {"ok": True, "pending": engine.list_pending()}


@mcp.tool()
def cancel_pending(pending_id: int) -> dict:
    """Remove a pending mission entry by pending_id. Returns
    {ok, removed, narrative}."""
    engine = _get_tactical_engine(transport)
    ok = engine.cancel_pending(int(pending_id))
    return {"ok": ok, "removed": 1 if ok else 0,
            "narrative": (f"Cancelled pending #{pending_id}." if ok
                          else f"No pending mission with id {pending_id}.")}


# ---------------------------------------------------------------------------
# Alert state + Mission objective tools
# ---------------------------------------------------------------------------
#
# Alert State is the army's global posture — packages perimeter mode +
# daemon thresholds + default stance/approach + a set of auto-dispatched
# missions. One player utterance ("守一下" / "全力进攻") flips the whole
# table. Mission Objective is orthogonal: it's the declared victory
# condition (destroy_fact / survive_until_tick / ...). Objective only
# *suggests* a matching alert state; the player still picks.
# ---------------------------------------------------------------------------


@mcp.tool()
def set_alert_state(level: str) -> dict:
    """Set the army's global alert state.

    level: peace | watch | alert | combat | lockdown

    - peace    — early game / no defense loops / units free / no auto missions
    - watch    — perimeter on / auto scout patrols / Defend by default
    - alert    — perimeter aggressive / auto patrols + harass / 50% retreat
    - combat   — main push / AttackAnything + charge defaults / no auto missions
    - lockdown — all units recalled / 70% retreat / max perimeter, no sallies

    Switching states cancels auto-dispatched missions from the previous
    state and dispatches the new state's set. Manual LLM-dispatched
    missions (attack / pincer / etc.) are NOT cancelled. Returns the full
    transition report including dispatched mission_ids the daemon now owns.
    """
    try:
        state = _AlertState(level)
    except ValueError:
        return {
            "ok": False,
            "error": f"unknown alert level: {level!r}. "
                     f"valid: {[s.value for s in _AlertState]}",
        }
    engine = _get_tactical_engine(transport)
    return engine.apply_alert_state(state)


@mcp.tool()
def get_alert_state() -> dict:
    """Inspect the current alert state plus the auto-mission ids and
    perimeters the alert table installed. Returns:
        {state, default_stance, default_approach, auto_missions,
         perimeters, force_recall_all}.
    """
    engine = _get_tactical_engine(transport)
    cfg = _ALERT_STATE_CONFIG[engine.current_alert_state]
    return {
        "ok": True,
        "state": engine.current_alert_state.value,
        "default_stance": engine.default_stance,
        "default_approach": engine.default_approach,
        "auto_missions": list(engine.auto_mission_ids),
        "perimeters": engine.list_perimeters(),
        "force_recall_all": cfg.get("force_recall_all", False),
        "config": {
            "perimeter": cfg.get("perimeter"),
            "retreat_hp_threshold": cfg.get("retreat_hp_threshold"),
            "cohesion_max_spread": cfg.get("cohesion_max_spread"),
        },
    }


@mcp.tool()
def set_objective(name: str, tick: Optional[int] = None) -> dict:
    """Set the player-declared victory condition + dispatch objective-owned
    auto-missions (e.g. harass_economy launches a cycle harass on enemy
    economy).

    name: destroy_fact | harass_economy | survive_until_tick | control_map_center
    tick: only for survive_until_tick — target tick to survive to.

    Switching the objective cancels the prior objective's auto-missions
    (kept in objective_mission_ids). Manual LLM-dispatched missions are
    NOT touched. Returns the transition report.
    """
    try:
        obj = _Objective(name)
    except ValueError:
        return {
            "ok": False,
            "error": f"unknown objective: {name!r}. "
                     f"valid: {[o.value for o in _Objective]}",
        }
    params: dict = {}
    if obj == _Objective.SURVIVE_UNTIL_TICK:
        if tick is None:
            return {"ok": False,
                    "error": "survive_until_tick requires `tick` parameter"}
        params["tick"] = int(tick)
    engine = _get_tactical_engine(transport)
    report = engine.set_objective(obj, params)
    suggested = _objective_to_suggested_state(obj)
    return {
        "ok": True,
        "objective": obj.value,
        "params": params,
        "suggested_alert_state": suggested.value,
        "cancelled_mission_ids": report.get("cancelled_mission_ids", []),
        "cancelled_pending_ids": report.get("cancelled_pending_ids", []),
        "dispatched_mission_ids": report.get("dispatched_mission_ids", []),
        "pending_ids": report.get("pending_ids", []),
        "narrative": (
            f"Objective {report.get('previous_objective')} → {obj.value}. "
            f"Cancelled {len(report.get('cancelled_mission_ids', []))} prior "
            f"objective mission(s) + "
            f"{len(report.get('cancelled_pending_ids', []))} pending, "
            f"dispatched {len(report.get('dispatched_mission_ids', []))}, "
            f"{len(report.get('pending_ids', []))} pending. "
            f"Suggested alert: {suggested.value}."
        ),
    }


@mcp.tool()
def set_doctrine(
    alert_state: Optional[str] = None,
    objective: Optional[str] = None,
    survive_tick: Optional[int] = None,
) -> dict:
    """Set the army's overall doctrine in one call — alert state + objective.

    Use this for "framework first" play: declare the big posture at game
    start (or whenever shifting strategy), then issue tactical intents
    inside that frame. Either field may be omitted to leave it unchanged.

    Args:
        alert_state: peace | watch | alert | combat | lockdown (or null)
        objective:   destroy_fact | harass_economy | survive_until_tick |
                     control_map_center (or null)
        survive_tick: required when objective == 'survive_until_tick'

    Internally calls set_alert_state then set_objective, returning a
    merged transition report. Switching either field cancels what that
    layer previously owned; the other layer's missions survive.
    """
    if alert_state is None and objective is None:
        return {"ok": False, "error": "specify at least one of alert_state / objective"}
    engine = _get_tactical_engine(transport)
    out: dict = {"ok": True}

    if alert_state is not None:
        try:
            state = _AlertState(alert_state)
        except ValueError:
            return {
                "ok": False,
                "error": f"unknown alert_state: {alert_state!r}",
            }
        out["alert"] = engine.apply_alert_state(state)

    if objective is not None:
        try:
            obj = _Objective(objective)
        except ValueError:
            return {"ok": False, "error": f"unknown objective: {objective!r}"}
        params: dict = {}
        if obj == _Objective.SURVIVE_UNTIL_TICK:
            if survive_tick is None:
                return {"ok": False,
                        "error": "objective=survive_until_tick requires survive_tick"}
            params["tick"] = int(survive_tick)
        out["objective"] = engine.set_objective(obj, params)

    pieces = []
    if "alert" in out:
        pieces.append(f"alert={alert_state}")
    if "objective" in out:
        pieces.append(f"objective={objective}")
    out["narrative"] = "Doctrine set: " + ", ".join(pieces)
    return out


@mcp.tool()
def get_objective() -> dict:
    """Inspect the current mission objective + its params. Returns
    {objective, params, suggested_alert_state}. `objective` is null when
    none has been set yet."""
    engine = _get_tactical_engine(transport)
    cur = engine.get_objective()
    suggested = None
    if cur["objective"]:
        suggested = _objective_to_suggested_state(
            _Objective(cur["objective"])
        ).value
    return {
        "ok": True,
        "objective": cur["objective"],
        "params": cur["params"],
        "suggested_alert_state": suggested,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    # stdio transport for Claude Code MCP client
    mcp.run()


if __name__ == "__main__":
    main()
