"""
DSL Interpreter — turns one Intent into a sequence of atomic MCP commands
sent through the OpenRATransport.

Phase ablation C3 (2026-05-25): trimmed to squad-only paths. The daemon
mission backend was archived along with tactical.py; all surviving intent
handlers either dispatch a spawn_squad (Assault / Protection) or are
read-only (report, regroup, raw). pincer / feint / harass / patrol /
escort / contain / diversion / scout / defend / retreat are removed —
they were daemon-backed and are now composed LLM-side via
spawn_squad_batch + the compose_*.py helpers.

Design rule: NO LLM calls in this file. Deterministic Python only.

Each public handler returns:
  ok: bool
  narrative: str          # human-readable summary for the LLM to relay
  actions_taken: list     # low-level commands actually dispatched
"""

from __future__ import annotations

from typing import Optional, Tuple, List, Dict

from . import intent_dsl as D
from . import geometry as G


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BUILDING_KINDS = frozenset({
    "fact", "powr", "apwr", "proc", "silo", "dome", "fix",
    "barr", "tent", "kenn", "weap", "hpad", "afld", "afld.ukraine",
    "syrd", "spen",
    "pbox", "hbox", "gun", "agun", "sam", "ftur", "tsla",
    "atek", "stek", "mslo", "iron", "pdox", "gap",
    "sbag", "brik", "barb", "cycl", "fenc",
    "oilb",
})
_NON_COMBAT_MOBILE_KINDS = frozenset({"harv", "mcv"})
_HARASS_CAPABLE = frozenset({"jeep", "ftrk", "dog", "e3", "apc", "1tnk"})
_HARASS_BAD = frozenset({"2tnk", "3tnk", "4tnk", "arty", "v2rl", "mcv", "harv"})
_FAST_KINDS = frozenset({"jeep", "dog", "e3", "e1", "ftrk", "spy", "thf"})

# Inlined unit-strength table (was tactical_doctrine.unit_strength).
_UNIT_STRENGTH = {
    "4tnk": 9, "3tnk": 8, "2tnk": 7, "1tnk": 6,
    "ttnk": 8, "v2rl": 7, "arty": 6, "ftrk": 5,
    "apc": 4, "jeep": 3, "e3": 2, "e1": 1,
}


def _is_building(kind: str) -> bool:
    return (kind or "").lower() in _BUILDING_KINDS


def _is_combat_mobile(kind: str) -> bool:
    k = (kind or "").lower()
    return (k not in _BUILDING_KINDS) and (k not in _NON_COMBAT_MOBILE_KINDS)


# ---------------------------------------------------------------------------
# WorldView — cheap snapshot of one game state
# ---------------------------------------------------------------------------

class WorldView:
    """One world snapshot, rebuilt at every dispatch."""

    def __init__(self, transport):
        self.transport = transport
        self.state = transport.send_command({"type": "get_state", "include_enemies": True})
        if not self.state.get("ok"):
            self.tick = -1
            self.self_units: List[dict] = []
            self.enemy_units: List[dict] = []
            self.map_size: Tuple[int, int] = (0, 0)
        else:
            s = self.state["state"]
            self.tick = s["tick"]
            self.self_units = s.get("self_units", [])
            self.enemy_units = s.get("enemy_units", [])
            self.map_size = (s["map_size"]["x"], s["map_size"]["y"])

    # --- Force ---------------------------------------------------------

    def resolve_force(self, force) -> List[int]:
        if isinstance(force, D.ForceByIds):
            return list(force.unit_ids)
        if isinstance(force, D.ForceByFilter):
            return self._force_by_filter(force)
        raise TypeError(f"unsupported force: {type(force)}")

    def _force_by_filter(self, f: D.ForceByFilter) -> List[int]:
        if f.owner == "self":
            pool = self.self_units
        elif f.owner == "enemy":
            pool = self.enemy_units
        else:
            pool = self.self_units + self.enemy_units

        matched: List[dict] = []
        for u in pool:
            kind_lower = (u.get("kind") or "").lower()
            if f.unit_kind and kind_lower != f.unit_kind.lower():
                continue
            hp = u.get("hp_pct", 1.0)
            if f.hp_below is not None and not (hp < f.hp_below):
                continue
            if f.hp_above is not None and not (hp > f.hp_above):
                continue
            if f.harass_capable is True:
                if kind_lower not in _HARASS_CAPABLE:
                    continue
                if kind_lower in _HARASS_BAD:
                    continue
            if f.combat_mobile is True:
                if kind_lower in _NON_COMBAT_MOBILE_KINDS or kind_lower in _BUILDING_KINDS:
                    continue
            matched.append(u)

        prefer = getattr(f, "prefer", "strongest")
        if prefer == "strongest":
            matched.sort(key=lambda u: _UNIT_STRENGTH.get((u.get("kind") or "").lower(), 0),
                         reverse=True)
        elif prefer == "fastest":
            matched.sort(key=lambda u: 0 if (u.get("kind") or "").lower() in _FAST_KINDS else 1)
        elif prefer == "healthiest":
            matched.sort(key=lambda u: u.get("hp_pct", 1.0), reverse=True)
        return [u["id"] for u in matched]

    # --- Target --------------------------------------------------------

    def resolve_target(self, target) -> Tuple[Optional[int], Optional[Tuple[int, int]]]:
        if isinstance(target, D.TargetById):
            for u in self.self_units + self.enemy_units:
                if u["id"] == target.actor_id:
                    return (target.actor_id, (u["pos"]["x"], u["pos"]["y"]))
            return (target.actor_id, None)
        if isinstance(target, D.TargetByPos):
            return (None, (target.pos.x, target.pos.y))
        if isinstance(target, D.TargetByName):
            return self._resolve_named(target.name)
        raise TypeError(f"unsupported target: {type(target)}")

    def _resolve_named(self, name: str) -> Tuple[Optional[int], Optional[Tuple[int, int]]]:
        if name == "enemy_fact":
            for u in self.enemy_units:
                if u["kind"].lower() == "fact":
                    return (u["id"], (u["pos"]["x"], u["pos"]["y"]))
            for u in self.enemy_units:
                if u["kind"].lower() in ("powr", "apwr", "proc", "barr", "tent", "weap"):
                    return (u["id"], (u["pos"]["x"], u["pos"]["y"]))
            return (None, None)
        if name == "self_base":
            for u in self.self_units:
                if u["kind"].lower() == "fact":
                    return (u["id"], (u["pos"]["x"], u["pos"]["y"]))
            return (None, self._centroid(self.self_units))
        if name == "enemy_base":
            return (None, self._centroid(self.enemy_units))
        if name == "self_center":
            return (None, self._centroid(self.self_units))
        if name == "enemy_center":
            return (None, self._centroid(self.enemy_units))
        if name in ("nearest_enemy", "nearest_enemy_unit", "nearest_enemy_structure"):
            return (None, None)
        raise ValueError(f"unknown named target: {name!r}")

    def _centroid(self, units: list) -> Optional[Tuple[int, int]]:
        if not units:
            return None
        n = len(units)
        return (sum(u["pos"]["x"] for u in units) // n,
                sum(u["pos"]["y"] for u in units) // n)

    def force_centroid(self, ids: List[int]) -> Optional[Tuple[int, int]]:
        if not ids:
            return None
        id_set = set(ids)
        return self._centroid([u for u in self.self_units if u["id"] in id_set])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(narrative: str, actions: list, **extra) -> dict:
    out = {"ok": True, "narrative": narrative, "actions_taken": actions}
    out.update(extra)
    return out


def _err(narrative: str, actions: list, error: str) -> dict:
    return {"ok": False, "narrative": narrative, "actions_taken": actions, "error": error}


def _dispatch_squad(transport, squad_type: str, force_ids: List[int],
                    target_pos: Optional[Tuple[int, int]] = None) -> dict:
    """Forward to engine spawn_squad via transport."""
    payload: dict = {"type": "spawn_squad", "squad_type": squad_type}
    if force_ids:
        payload["unit_ids"] = [int(i) for i in force_ids]
    if target_pos is not None:
        payload["target_pos"] = {"x": int(target_pos[0]), "y": int(target_pos[1])}
    return transport.send_command(payload)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def interpret(intent_payload: dict, transport) -> dict:
    """Parse + resolve + dispatch one intent."""
    try:
        intent = D.parse_intent(intent_payload)
    except Exception as e:
        return {"ok": False, "error": f"parse_error: {e}",
                "actions_taken": [], "narrative": ""}

    wv = WorldView(transport)
    if intent.intent == "attack":
        return _do_attack(intent, wv, transport)
    if intent.intent == "report":
        return _do_report(intent, wv, transport)
    if intent.intent == "raw":
        return _do_raw(intent, wv, transport)
    return {"ok": False, "error": f"unhandled intent: {intent.intent}",
            "actions_taken": [], "narrative": ""}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _do_attack(intent: D.IntentAttack, wv: WorldView, transport) -> dict:
    """Spawn an Assault squad. Engine FSM owns execution."""
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")

    tid, tpos = wv.resolve_target(intent.target)

    if isinstance(intent.target, D.TargetByName) and intent.target.name in (
        "nearest_enemy", "nearest_enemy_unit", "nearest_enemy_structure"
    ):
        center = wv.force_centroid(ids)
        if center and wv.enemy_units:
            wanted_mobile = intent.target.name != "nearest_enemy_structure"
            wanted_struct = intent.target.name != "nearest_enemy_unit"
            mobile_candidates = [u for u in wv.enemy_units
                                 if wanted_mobile and not _is_building(u["kind"])]
            struct_candidates = [u for u in wv.enemy_units
                                 if wanted_struct and _is_building(u["kind"])]
            pool = mobile_candidates or struct_candidates
            if pool:
                nearest = min(pool,
                              key=lambda u: G.distance(center, (u["pos"]["x"], u["pos"]["y"])))
                tid = nearest["id"]
                tpos = (nearest["pos"]["x"], nearest["pos"]["y"])

    if tpos is None and tid is None:
        return _err("target unresolved", actions, "target_resolution_failed")

    resp = _dispatch_squad(transport, "Assault", ids, target_pos=tpos)
    actions.append({"cmd": {"type": "spawn_squad", "squad_type": "Assault"}, "resp": resp})
    if not resp.get("ok"):
        return _err(f"squad spawn failed: {resp.get('error')}", actions,
                    "squad_register_failed")
    return _ok(
        f"attack {resp.get('unit_count')} unit(s) → {tid or tpos} "
        f"[squad #{resp.get('squad_index')}]",
        actions, squad_index=resp.get("squad_index"),
    )


def _do_report(intent: D.IntentReport, wv: WorldView, transport) -> dict:
    """Read-only snapshot."""
    if intent.what == "battlefield":
        return _ok(_narrate_battlefield(wv), actions=[],
                   snapshot={"tick": wv.tick,
                             "self_count": len(wv.self_units),
                             "enemy_count": len(wv.enemy_units)})
    if intent.what == "enemy":
        return _ok(_narrate_enemy(wv), actions=[], enemy_units=wv.enemy_units)
    if intent.what == "threats":
        return _ok(_narrate_threats(wv), actions=[], enemy_units=wv.enemy_units)
    if intent.what == "minimap":
        resp = transport.send_command({"type": "screenshot"})
        return _ok("Screenshot queued.",
                   actions=[{"cmd": {"type": "screenshot"}, "resp": resp}])
    if intent.what == "resources":
        s = wv.state.get("state", {})
        return _ok(f"cash={s.get('self_cash', 0)}, power={s.get('self_power', 0)}",
                   actions=[])
    return _err(f"unknown report what: {intent.what}", [], "report_what_unknown")


def _do_raw(intent: D.IntentRaw, wv: WorldView, transport) -> dict:
    """Escape hatch: dispatch arbitrary atomic MCP calls. Use rarely."""
    actions: List[dict] = []
    for call in intent.atomic_calls:
        if isinstance(call, dict) and "type" in call:
            cmd = call
        elif isinstance(call, dict) and "tool" in call:
            cmd = {"type": call["tool"], **(call.get("args") or {})}
        else:
            actions.append({"cmd": call, "resp": {"ok": False, "error": "malformed"}})
            continue
        resp = transport.send_command(cmd)
        actions.append({"cmd": cmd, "resp": resp})
    return _ok(f"dispatched {len(actions)} raw call(s)", actions)


# ---------------------------------------------------------------------------
# Narrators
# ---------------------------------------------------------------------------

def _kind_summary(units: list) -> str:
    counts: Dict[str, int] = {}
    for u in units:
        k = u.get("kind", "?")
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))


def _narrate_battlefield(wv: WorldView) -> str:
    if not wv.state.get("ok"):
        return "battlefield: bridge not connected"
    s = wv.state["state"]
    return (f"Tick {wv.tick}. Map {s.get('map_name', '?')} {wv.map_size}. "
            f"Cash {s.get('self_cash', 0)}, Power {s.get('self_power', 0)}. "
            f"Self: {len(wv.self_units)} [{_kind_summary(wv.self_units)}]. "
            f"Enemy: {len(wv.enemy_units)} [{_kind_summary(wv.enemy_units)}].")


def _narrate_enemy(wv: WorldView) -> str:
    if not wv.enemy_units:
        return "no enemy spotted"
    return (f"Enemy: {len(wv.enemy_units)} unit(s) [{_kind_summary(wv.enemy_units)}] "
            f"centroid={wv._centroid(wv.enemy_units)}")


def _narrate_threats(wv: WorldView) -> str:
    threats = []
    for e in wv.enemy_units:
        ep = (e["pos"]["x"], e["pos"]["y"])
        for s in wv.self_units:
            if G.distance(ep, (s["pos"]["x"], s["pos"]["y"])) < 20:
                threats.append(e)
                break
    if not threats:
        return "no immediate threats"
    return f"Threats: {len(threats)} enemy within 20 cells [{_kind_summary(threats)}]"
