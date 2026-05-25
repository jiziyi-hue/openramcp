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


# Group commands removed Phase ablation C2 (2026-05-25): list_groups /
# assign_to_group / command_group / rebalance_groups never used in any E7
# or v2 test. LLM addresses units by actor_id list or filter, not group
# name. Source moved out; reactivate via git if ever needed.


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
def batch_dispatch_intent(intents: list[dict], meta: Optional[dict] = None) -> dict:
    """Dispatch MULTIPLE intents in one MCP round-trip.

    Use this when the player gives a compound order that cleanly decomposes
    into independent intents — e.g. "north 群推总部, south 群守分矿":

        batch_dispatch_intent([
            {"intent":"attack", "force":{...}, "target":{...}},
            {"intent":"defend", "force":{...}, "region":{...}}
        ])

    Each intent is interpreted independently and in order; we do NOT
    serialize them as a single mission. If one parse fails, later intents
    still run. Each entry's narrative + actions_taken come back in `results`.

    Borrowed conceptually from OpenRA-RL's `batch(actions)` MCP tool but
    adapted to our high-level intent DSL — saves a full LLM round-trip when
    the player issues a compound order, while keeping each intent's
    deterministic interpreter path.

    Args:
        intents: list of intent DSL dicts (same shape as dispatch_intent).
        meta:    same as dispatch_intent. Applied to each sub-call.

    Returns: {ok, count, results: [{ok, narrative, actions_taken, error?}, ...]}.
    """
    results = []
    any_ok = False
    for intent in intents:
        r = _dispatch_logged(intent, meta, "batch_dispatch_intent")
        results.append(r)
        if r.get("ok"):
            any_ok = True
    return {"ok": any_ok, "count": len(results), "results": results}


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



# Scout/wait_for_event/tactical_status/auto_defense/cancel_assaults/
# pending_mission tools removed Phase ablation C2 (2026-05-25).


# ---------------------------------------------------------------------------
# Bot squad tools (Phase B): drive engine-side SquadManagerBotModule@human
# via spawn_squad / list_squads / cancel_squad. The bot's GroundStates FSM
# owns leader-based regroup, AttackOrFleeFuzzy, retarget. LLM only declares
# intent; we forward to the McpBridge handler.
# ---------------------------------------------------------------------------


@mcp.tool()
def spawn_squad(squad_type: str,
                unit_ids: Optional[list[int]] = None,
                target_actor_id: Optional[int] = None,
                target_pos: Optional[dict] = None,
                rally_point: Optional[dict] = None,
                waypoints: Optional[list[dict]] = None,
                escortee_actor_id: Optional[int] = None) -> dict:
    """Spawn a bot squad. LLM declares intent; engine FSM owns execution.

    squad_type:
        Assault     — push toward target_actor or target_pos; cohesion-gated.
        Protection  — defend the target_pos cell; engage threats nearby.
        Harass      — engage→withdraw→reengage loop against enemy economy
                      structures (proc/silo/harv/...). Withdraws at avg HP
                      < 55%, re-engages once back to ≥ 85%. Pair with
                      rally_point so the squad has somewhere safe to retreat.
        Patrol      — cycles through waypoints in order. Opportunistically
                      attacks anything within 8 cells of the leader.
        Escort      — shadows escortee_actor_id; auto-attacks hostiles within
                      6 cells. Ends when escortee dies.
        Explore     — 8-spoke spiral outward from target_pos to lift fog of
                      war. Briefly engages contacts then keeps exploring.
        Air         — air units, vanilla FSM.
        Rush / Naval — legacy ground / naval FSM.
    unit_ids:        OPTIONAL. Actor ids to add. Omit (preferred) to let
                     SquadManager auto-pick idle combat-mobile units of the
                     right type.
    target_actor_id: OPTIONAL specific enemy actor. Wins over target_pos.
    target_pos:      OPTIONAL {"x":int,"y":int} cell. Doubles as the seed
                     for Explore and the march/rally point for Assault.
    rally_point:     OPTIONAL {"x":int,"y":int} cell. For Harass: cell to
                     withdraw to during the cooldown phase. If omitted,
                     squad picks a random own building.
    waypoints:       OPTIONAL list of {"x":int,"y":int} cells for Patrol.
                     Squad walks them in order and loops.
    escortee_actor_id: OPTIONAL actor id to shadow (Escort squads).

    The squad runs entirely inside the engine: leader-based regroup, fuzzy
    attack-or-flee, retarget on invalid target. Initial cohesion gate (Phase
    D3) keeps fast units from sprinting ahead of slow ones. All units get
    AttackAnything stance (Phase D4) so they autonomously hit nearby
    buildings while marching. Use list_squads() to inspect, cancel_squad()
    to disband.

    Returns: {ok, squad_index, squad_type, unit_count, auto_selected,
              target_actor?, target_pos?}.
    """
    payload: dict = {
        "type": "spawn_squad",
        "squad_type": squad_type,
    }
    if unit_ids is not None and len(unit_ids) > 0:
        payload["unit_ids"] = [int(i) for i in unit_ids]
    if target_actor_id is not None:
        payload["target_actor_id"] = int(target_actor_id)
    if target_pos is not None:
        payload["target_pos"] = {
            "x": int(target_pos["x"]),
            "y": int(target_pos["y"]),
        }
    if rally_point is not None:
        payload["rally_point"] = {
            "x": int(rally_point["x"]),
            "y": int(rally_point["y"]),
        }
    if waypoints:
        payload["waypoints"] = [
            {"x": int(w["x"]), "y": int(w["y"])} for w in waypoints
        ]
    if escortee_actor_id is not None:
        payload["escortee_actor_id"] = int(escortee_actor_id)
    return transport.send_command(payload)


@mcp.tool()
def spawn_squad_batch(squads: list[dict]) -> dict:
    """Atomically spawn N bot squads in a single MCP round-trip.

    Each entry in `squads` is the same payload that `spawn_squad` accepts
    (squad_type, unit_ids, target_pos, target_actor_id, waypoints,
    escortee_actor_id). The bridge processes all squads inside one
    handler invocation so they appear to start simultaneously from the
    player's perspective — no per-spawn TCP round-trip.

    Use when dispatching multiple coordinated squads (4 prongs to 4 corners,
    pincer left+right, simultaneous patrol + escort, etc.). For a single
    squad, `spawn_squad` is fine.

    Returns: {ok, results: [<spawn_squad result>, ...]}.
    """
    payloads = []
    for s in squads:
        p = {"type": "spawn_squad", "squad_type": s["squad_type"]}
        if s.get("unit_ids"):
            p["unit_ids"] = [int(i) for i in s["unit_ids"]]
        if s.get("target_actor_id") is not None:
            p["target_actor_id"] = int(s["target_actor_id"])
        if s.get("target_pos") is not None:
            p["target_pos"] = {
                "x": int(s["target_pos"]["x"]),
                "y": int(s["target_pos"]["y"]),
            }
        if s.get("waypoints"):
            p["waypoints"] = [
                {"x": int(w["x"]), "y": int(w["y"])} for w in s["waypoints"]
            ]
        if s.get("escortee_actor_id") is not None:
            p["escortee_actor_id"] = int(s["escortee_actor_id"])
        payloads.append(p)
    return transport.send_command({"type": "spawn_squad_batch", "squads": payloads})


@mcp.tool()
def spawn_squad_cluster(squad_type: str,
                        unit_ids: list[int],
                        target_pos: dict,
                        cluster_size: int = 20,
                        target_jitter_cells: int = 4,
                        stagger_ms: int = 250) -> dict:
    """Spatially cluster unit_ids by current position, then spawn one
    bot squad per cluster — each marching to a slightly jittered target.

    Why: A single 40-unit squad with one target_pos pushes all units to the
    same cell, causing path contention, leader rally-gate thrash, and the
    AttackMove→Idle re-entry loop (T8 finding 2026-05-24). Splitting into
    e.g. 2× 20-unit squads with 4-cell-jittered targets gives each squad
    its own approach lane.

    Algorithm:
      1. get_state → look up (x, y) for each requested unit id.
      2. K = ceil(len(unit_ids) / cluster_size).
      3. Sort units along the longer axis (x range vs y range) and slice
         into K contiguous chunks. Simple, deterministic, locale-aware:
         neighbors stay together.
      4. For chunk i, target = (target_pos.x + offset_i.x,
                                target_pos.y + offset_i.y) where
         offset_i orbits target_pos at `target_jitter_cells` distance.
      5. spawn_squad each chunk with the jittered target, sleeping
         stagger_ms between calls so the engine's SquadManager.CleanSquads
         tick doesn't sweep newly-spawned squads before their FSM gets a
         move order out.

    Args:
        squad_type: same as spawn_squad (Assault / Protection / Harass / ...).
        unit_ids:   actor ids to partition. Must be player-owned.
        target_pos: {"x": int, "y": int} — the shared rough target.
        cluster_size: target units per squad. Final K = ceil(N / cluster_size).
        target_jitter_cells: how far each sub-target sits from target_pos.
        stagger_ms: delay between spawn calls (mitigates squad eviction).

    Returns: {ok, spawned: [{squad_index, unit_count, target_pos}, ...],
              cluster_count, total_units}.
    """
    import math
    import time as _time

    if not unit_ids:
        return {"ok": False, "error": "empty unit_ids"}

    # 1. Resolve current positions
    state = transport.send_command({"type": "get_state", "include_enemies": False})
    if not state.get("ok"):
        return {"ok": False, "error": "get_state failed"}
    pos_map = {u["id"]: (u["pos"]["x"], u["pos"]["y"])
               for u in state["state"].get("self_units", [])}
    located = [(uid, pos_map[uid]) for uid in unit_ids if uid in pos_map]
    if not located:
        return {"ok": False, "error": "none of unit_ids found in state"}

    # 2. K clusters
    n = len(located)
    k = max(1, math.ceil(n / max(1, int(cluster_size))))

    # 3. Sort along longer axis
    xs = [p[1][0] for p in located]
    ys = [p[1][1] for p in located]
    if (max(xs) - min(xs)) >= (max(ys) - min(ys)):
        located.sort(key=lambda p: p[1][0])  # along x
    else:
        located.sort(key=lambda p: p[1][1])  # along y

    chunks: list[list[int]] = []
    per = math.ceil(n / k)
    for i in range(k):
        chunk = [p[0] for p in located[i * per:(i + 1) * per]]
        if chunk:
            chunks.append(chunk)

    # 4. Jittered targets — orbit target_pos
    tx, ty = int(target_pos["x"]), int(target_pos["y"])
    spawned = []
    for i, chunk in enumerate(chunks):
        angle = (2 * math.pi * i) / max(1, len(chunks))
        ox = int(round(target_jitter_cells * math.cos(angle)))
        oy = int(round(target_jitter_cells * math.sin(angle)))
        sub_target = {"x": tx + ox, "y": ty + oy}
        payload = {
            "type": "spawn_squad",
            "squad_type": squad_type,
            "unit_ids": [int(u) for u in chunk],
            "target_pos": sub_target,
        }
        resp = transport.send_command(payload)
        spawned.append({
            "squad_index": resp.get("squad_index"),
            "unit_count": resp.get("unit_count"),
            "target_pos": sub_target,
            "ok": resp.get("ok"),
            "error": resp.get("error"),
        })
        if i < len(chunks) - 1 and stagger_ms > 0:
            _time.sleep(stagger_ms / 1000.0)

    return {
        "ok": all(s["ok"] for s in spawned),
        "spawned": spawned,
        "cluster_count": len(chunks),
        "total_units": sum(s["unit_count"] or 0 for s in spawned),
    }


@mcp.tool()
def list_squads() -> dict:
    """List active bot squads (engine-side) owned by the local player.

    Each entry: {squad_index, squad_type, is_valid, unit_count, unit_ids,
                 target_actor?}.
    """
    return transport.send_command({"type": "list_squads"})


@mcp.tool()
def cancel_squad(squad_index: Optional[int] = None) -> dict:
    """Cancel one bot squad (pass squad_index) or ALL (omit). Cancelled
    squads release their units back to the player; units keep their last
    orders until you issue a new intent.

    Returns: {ok, cancelled_squads, ...}.
    """
    payload: dict = {"type": "cancel_squad"}
    if squad_index is not None:
        payload["squad_index"] = int(squad_index)
    return transport.send_command(payload)


# Alert state + objective tools removed Phase ablation C2 (2026-05-25).
# set_alert_state / get_alert_state / set_objective / set_doctrine /
# get_objective never used outside lab tests.

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    # stdio transport for Claude Code MCP client
    mcp.run()


if __name__ == "__main__":
    main()
