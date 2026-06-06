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
_NON_COMBAT_MOBILE_KINDS = frozenset({
    # economy / utility vehicles
    "harv", "mcv", "truk", "mnly", "mgg", "mrj",
    # support / infiltrator infantry (don't send these in an assault)
    "e6", "medi", "mech", "spy", "thf",
    # campaign VIPs (non-combat)
    "einstein", "delphi", "chan", "gnrl",
})
_HARASS_CAPABLE = frozenset({"jeep", "ftrk", "dog", "e3", "apc", "1tnk"})
_HARASS_BAD = frozenset({"2tnk", "3tnk", "4tnk", "arty", "v2rl", "mcv", "harv"})
_FAST_KINDS = frozenset({"jeep", "dog", "e3", "e1", "ftrk", "spy", "thf"})
# Aircraft — a separate control category. Excluded from ground combat_mobile;
# commanded via air=true / unit_kind and routed to the Air squad FSM.
_AIR_KINDS = frozenset({"mig", "yak", "hind", "heli", "badr", "u2",
                        "tran", "mh60"})
_AIR_COMBAT = frozenset({"mig", "yak", "hind", "heli"})  # attack-capable

# Inlined unit-strength table (was tactical_doctrine.unit_strength).
_UNIT_STRENGTH = {
    "4tnk": 9, "3tnk": 8, "2tnk": 7, "1tnk": 6,
    "ttnk": 8, "v2rl": 7, "arty": 6, "ftrk": 5,
    "apc": 4, "jeep": 3,
    # infantry
    "shok": 4, "e4": 3, "e2": 2, "e3": 2, "e1": 1, "dog": 1,
}


def _is_building(kind: str) -> bool:
    return (kind or "").lower() in _BUILDING_KINDS


def _is_combat_mobile(kind: str) -> bool:
    k = (kind or "").lower()
    return (k not in _BUILDING_KINDS) and (k not in _NON_COMBAT_MOBILE_KINDS) \
        and (k not in _AIR_KINDS)


def _force_is_air(force) -> bool:
    """True if this force explicitly selects aircraft (air=true or an air
    unit_kind) — routes the attack to the Air squad FSM instead of Assault."""
    f = getattr(force, "air", None)
    if f is True:
        return True
    uk = (getattr(force, "unit_kind", None) or "").lower()
    return uk in _AIR_KINDS


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
                if (kind_lower in _NON_COMBAT_MOBILE_KINDS
                        or kind_lower in _BUILDING_KINDS
                        or kind_lower in _AIR_KINDS):  # ground only — no aircraft
                    continue
            if f.air is True and kind_lower not in _AIR_COMBAT:
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
        if name.startswith("map_"):
            return (None, self._resolve_landmark(name))
        raise ValueError(f"unknown named target: {name!r}")

    def _resolve_landmark(self, name: str) -> Optional[Tuple[int, int]]:
        """Resolve a map landmark to a cell using map_size + a 15% inset so
        squads march toward a reachable cell, not the literal map edge."""
        w, h = self.map_size
        if w <= 0 or h <= 0:
            return None
        mx, my = w * 0.15, h * 0.15  # inset from the edges
        # OpenRA: origin top-left, x grows east (right), y grows south (down).
        coords = {
            "map_center":    (w // 2,       h // 2),
            "map_corner_nw": (mx,           my),
            "map_corner_ne": (w - mx,       my),
            "map_corner_sw": (mx,           h - my),
            "map_corner_se": (w - mx,       h - my),
        }
        c = coords.get(name)
        return (int(c[0]), int(c[1])) if c else None

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

    # --- Route / escortee (for patrol / escort) ------------------------
    def resolve_route(self, route: str) -> List[Tuple[int, int]]:
        """Turn a named patrol route into a list of waypoints from map_size.
        LLM gives a route name; interpreter computes the cells."""
        w, h = self.map_size
        if w <= 0 or h <= 0:
            return []
        mx, my = int(w * 0.15), int(h * 0.15)
        cx, cy = w // 2, h // 2
        if route == "base_perimeter":
            base = self._resolve_named("self_base")[1] or (cx, cy)
            bx, by = base
            r = max(6, min(w, h) // 6)
            return [(bx + r, by), (bx, by + r), (bx - r, by), (bx, by - r)]
        if route == "front_line":
            s = self._centroid(self.self_units) or (cx, cy)
            e = self._centroid(self.enemy_units) or (cx, cy)
            midx, midy = (s[0] + e[0]) // 2, (s[1] + e[1]) // 2
            return [(midx, my), (midx, h - my)]
        if route == "center_loop":
            r = max(6, min(w, h) // 5)
            return [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
        if route == "east_lane":
            return [(w - mx, my), (w - mx, h - my)]
        if route == "west_lane":
            return [(mx, my), (mx, h - my)]
        if route == "north_lane":
            return [(mx, my), (w - mx, my)]
        if route == "south_lane":
            return [(mx, h - my), (w - mx, h - my)]
        return [(cx, cy)]

    def resolve_escortee(self, name: str) -> Optional[int]:
        """Resolve a named friendly unit to an actor id from live state."""
        def first(kinds):
            for u in self.self_units:
                if (u.get("kind") or "").lower() in kinds:
                    return u["id"]
            return None
        if name == "mcv":
            return first({"mcv"})
        if name == "harvester":
            return first({"harv"})
        if name == "nearest_vehicle":
            for u in self.self_units:
                k = (u.get("kind") or "").lower()
                if k not in _BUILDING_KINDS and k not in (
                        "e1", "e2", "e3", "e4", "e6", "medi", "mech"):
                    return u["id"]
            return None
        if name == "nearest_infantry":
            return first({"e1", "e2", "e3", "e4", "e6"})
        return None


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
                    target_pos: Optional[Tuple[int, int]] = None,
                    waypoints: Optional[List[Tuple[int, int]]] = None,
                    escortee_actor_id: Optional[int] = None,
                    rally_point: Optional[Tuple[int, int]] = None) -> dict:
    """Forward to engine spawn_squad via transport."""
    payload: dict = {"type": "spawn_squad", "squad_type": squad_type}
    if force_ids:
        payload["unit_ids"] = [int(i) for i in force_ids]
    if target_pos is not None:
        payload["target_pos"] = {"x": int(target_pos[0]), "y": int(target_pos[1])}
    if waypoints:
        payload["waypoints"] = [{"x": int(x), "y": int(y)} for x, y in waypoints]
    if escortee_actor_id is not None:
        payload["escortee_actor_id"] = int(escortee_actor_id)
    if rally_point is not None:
        payload["rally_point"] = {"x": int(rally_point[0]), "y": int(rally_point[1])}
    return transport.send_command(payload)


# Cluster threshold: forces larger than this fan out into sub-squads with
# jittered targets so they walk in parallel lanes (not single file).
_CLUSTER_MIN = 8
_CLUSTER_SIZE = 6            # ~6 units per sub-squad (smaller = shorter columns)
_CLUSTER_JITTER = 10         # cells around the named target (wider lanes)
_CLUSTER_STAGGER_MS = 250    # delay between sub-spawns


def _dispatch_squad_clustered(transport, squad_type: str,
                               force_ids: List[int],
                               target_pos: Tuple[int, int],
                               wv: "WorldView",
                               cluster_size: int = _CLUSTER_SIZE,
                               jitter: int = _CLUSTER_JITTER,
                               stagger_ms: int = _CLUSTER_STAGGER_MS) -> dict:
    """Spawn N sub-squads with positionally-clustered units and jittered
    targets on an orbit around target_pos. Avoids the single-file pathing
    that happens when many units share one target cell. Mirrors
    server.spawn_squad_cluster, but inlined so the interpreter doesn't have
    to call back into the @mcp.tool layer."""
    import math
    import time as _time

    pos_map = {u["id"]: (u["pos"]["x"], u["pos"]["y"])
               for u in wv.self_units}
    located = [(uid, pos_map[uid]) for uid in force_ids if uid in pos_map]
    if not located:
        return {"ok": False, "error": "none of force_ids located"}

    n = len(located)
    k = max(1, math.ceil(n / max(1, int(cluster_size))))

    xs = [p[1][0] for p in located]
    ys = [p[1][1] for p in located]
    if (max(xs) - min(xs)) >= (max(ys) - min(ys)):
        located.sort(key=lambda p: p[1][0])
    else:
        located.sort(key=lambda p: p[1][1])

    per = math.ceil(n / k)
    chunks = [[p[0] for p in located[i * per:(i + 1) * per]]
              for i in range(k)]
    chunks = [c for c in chunks if c]

    tx, ty = int(target_pos[0]), int(target_pos[1])
    spawned: List[dict] = []
    for i, chunk in enumerate(chunks):
        angle = (2 * math.pi * i) / max(1, len(chunks))
        ox = int(round(jitter * math.cos(angle)))
        oy = int(round(jitter * math.sin(angle)))
        sub_target = (tx + ox, ty + oy)
        resp = _dispatch_squad(transport, squad_type, chunk,
                               target_pos=sub_target)
        spawned.append({
            "squad_index": resp.get("squad_index"),
            "unit_count": resp.get("unit_count"),
            "target_pos": {"x": sub_target[0], "y": sub_target[1]},
            "ok": resp.get("ok"),
        })
        if i < len(chunks) - 1 and stagger_ms > 0:
            _time.sleep(stagger_ms / 1000.0)

    return {
        "ok": all(s["ok"] for s in spawned),
        "squad_index": spawned[0]["squad_index"] if spawned else None,
        "unit_count": sum(s["unit_count"] or 0 for s in spawned),
        "cluster_count": len(chunks),
        "spawned": spawned,
    }


def _cancel_squads_containing(ids: List[int], transport) -> List[int]:
    """Cancel every existing squad that contains ANY of these unit ids.

    Why: lets the player chain "send force to A" -> "send force to B".
    Without this, the old squad's FSM keeps issuing 'move to A' orders that
    fight the new 'move to B' — units jitter. Cancel old squads first so the
    new spawn has a clean slate.

    Cancels from highest squad_index down so the indices below don't shift
    mid-loop (engine's RemoveAt re-indexes).
    """
    if not ids:
        return []
    resp = transport.send_command({"type": "list_squads"})
    if not resp.get("ok"):
        return []
    needle = set(ids)
    matched = []
    for sq in resp.get("squads", []) or []:
        sq_ids = set(sq.get("unit_ids") or [])
        if sq_ids & needle:
            matched.append(int(sq.get("squad_index", -1)))
    cancelled = []
    for idx in sorted(matched, reverse=True):
        if idx < 0:
            continue
        r = transport.send_command({"type": "cancel_squad", "squad_index": idx})
        if r.get("ok"):
            cancelled.append(idx)
    return cancelled


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
    if intent.intent == "defend":
        return _do_defend(intent, wv, transport)
    if intent.intent == "harass":
        return _do_harass(intent, wv, transport)
    if intent.intent == "scout":
        return _do_scout(intent, wv, transport)
    if intent.intent == "patrol":
        return _do_patrol(intent, wv, transport)
    if intent.intent == "escort":
        return _do_escort(intent, wv, transport)
    if intent.intent == "pincer":
        return _do_pincer(intent, wv, transport)
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

    # aircraft -> Air squad FSM (airfield/rearm), ground -> Assault
    squad_type = "Air" if _force_is_air(intent.force) else "Assault"
    # Re-direct support: cancel any old squad still holding these units so
    # the new order isn't fought by leftover FSM. Enables "去 A → 去 B".
    cancelled = _cancel_squads_containing(ids, transport)
    if cancelled:
        actions.append({"cmd": {"type": "auto_cancel"}, "resp": {"ok": True, "cancelled": cancelled}})
    # Big forces fan out into sub-squads with jittered targets so they walk
    # in parallel lanes (avoids single-file pathing).
    if tpos is not None and len(ids) > _CLUSTER_MIN and squad_type == "Assault":
        resp = _dispatch_squad_clustered(transport, squad_type, ids, tpos, wv)
        actions.append({"cmd": {"type": "spawn_squad_cluster",
                                 "squad_type": squad_type}, "resp": resp})
    else:
        resp = _dispatch_squad(transport, squad_type, ids, target_pos=tpos)
        actions.append({"cmd": {"type": "spawn_squad", "squad_type": squad_type}, "resp": resp})
    if not resp.get("ok"):
        return _err(f"squad spawn failed: {resp.get('error')}", actions,
                    "squad_register_failed")
    return _ok(
        f"attack {resp.get('unit_count')} unit(s) → {tid or tpos} "
        f"[squad #{resp.get('squad_index')}]",
        actions, squad_index=resp.get("squad_index"),
    )


def _squad_intent(squad_type: str, intent, wv: WorldView, transport,
                  target_pos=None, waypoints=None, escortee=None) -> dict:
    """Shared body for the coordless squad intents."""
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    # Re-direct support — see _do_attack for rationale.
    cancelled = _cancel_squads_containing(ids, transport)
    if cancelled:
        actions.append({"cmd": {"type": "auto_cancel"}, "resp": {"ok": True, "cancelled": cancelled}})
    # Cluster for big positional pushes (defend/harass/scout) — same single-
    # file fix. Skip for Patrol/Escort which use waypoints/escortee.
    if (target_pos is not None and waypoints is None and escortee is None
            and len(ids) > _CLUSTER_MIN):
        resp = _dispatch_squad_clustered(transport, squad_type, ids,
                                          target_pos, wv)
    else:
        resp = _dispatch_squad(transport, squad_type, ids, target_pos=target_pos,
                               waypoints=waypoints, escortee_actor_id=escortee)
    actions.append({"cmd": {"type": "spawn_squad", "squad_type": squad_type},
                    "resp": resp})
    if not resp.get("ok"):
        return _err(f"squad spawn failed: {resp.get('error')}", actions,
                    "squad_register_failed")
    dest = target_pos or (waypoints[0] if waypoints else escortee)
    return _ok(
        f"{intent.intent} {resp.get('unit_count')} unit(s) → {dest} "
        f"[{squad_type} squad #{resp.get('squad_index')}]",
        actions, squad_index=resp.get("squad_index"),
    )


def _do_defend(intent: D.IntentDefend, wv: WorldView, transport) -> dict:
    _tid, tpos = wv.resolve_target(intent.where)
    if tpos is None:
        return _err("place unresolved", [], "target_resolution_failed")
    return _squad_intent("Protection", intent, wv, transport, target_pos=tpos)


def _do_harass(intent: D.IntentHarass, wv: WorldView, transport) -> dict:
    _tid, tpos = wv.resolve_target(intent.target)
    return _squad_intent("Harass", intent, wv, transport, target_pos=tpos)


def _do_scout(intent: D.IntentScout, wv: WorldView, transport) -> dict:
    _tid, tpos = wv.resolve_target(intent.where)
    if tpos is None:
        return _err("place unresolved", [], "target_resolution_failed")
    return _squad_intent("Explore", intent, wv, transport, target_pos=tpos)


def _do_patrol(intent: D.IntentPatrol, wv: WorldView, transport) -> dict:
    wps = wv.resolve_route(intent.route)
    if not wps:
        return _err("route unresolved", [], "route_resolution_failed")
    return _squad_intent("Patrol", intent, wv, transport, waypoints=wps)


def _do_escort(intent: D.IntentEscort, wv: WorldView, transport) -> dict:
    aid = wv.resolve_escortee(intent.escortee)
    if aid is None:
        return _err(f"no {intent.escortee} to escort", [], "escortee_not_found")
    return _squad_intent("Escort", intent, wv, transport, escortee=aid)


def _do_pincer(intent: D.IntentPincer, wv: WorldView, transport) -> dict:
    """Split the force by position into two prongs → two Assault squads."""
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    pos = {u["id"]: (u["pos"]["x"], u["pos"]["y"])
           for u in wv.self_units if u["id"] in set(ids)}
    ordered = sorted(ids, key=lambda i: pos.get(i, (0, 0))[0])  # west→east
    half = max(1, len(ordered) // 2)
    left_ids = ordered[:half]
    right_ids = ordered[half:] or ordered[:half]
    _l, lpos = wv.resolve_target(intent.left)
    _r, rpos = wv.resolve_target(intent.right)
    if lpos is None or rpos is None:
        return _err("pincer target unresolved", actions, "target_resolution_failed")
    # Re-direct support — cancel any old squad holding these units first.
    cancelled = _cancel_squads_containing(ids, transport)
    if cancelled:
        actions.append({"cmd": {"type": "auto_cancel"}, "resp": {"ok": True, "cancelled": cancelled}})
    # Each prong: cluster if big enough (parallel-lane walk, not single file)
    def _send(squad_ids, pos):
        if len(squad_ids) > _CLUSTER_MIN:
            return _dispatch_squad_clustered(transport, "Assault",
                                              squad_ids, pos, wv)
        return _dispatch_squad(transport, "Assault", squad_ids, target_pos=pos)
    r1 = _send(left_ids, lpos)
    actions.append({"cmd": {"squad": "Assault", "prong": "left"}, "resp": r1})
    r2 = _send(right_ids, rpos)
    actions.append({"cmd": {"squad": "Assault", "prong": "right"}, "resp": r2})
    if not (r1.get("ok") and r2.get("ok")):
        return _err("pincer squad spawn failed", actions, "squad_register_failed")
    return _ok(
        f"pincer: {len(left_ids)} → {lpos} / {len(right_ids)} → {rpos} "
        f"[squads #{r1.get('squad_index')}, #{r2.get('squad_index')}]",
        actions, squad_indices=[r1.get("squad_index"), r2.get("squad_index")],
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
