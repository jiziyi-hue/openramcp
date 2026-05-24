"""
DSL Interpreter — turns one Intent into a sequence of atomic MCP commands
sent through the OpenRATransport.

Design rule: NO LLM calls in this file. All logic is deterministic Python.
The LLM (Claude / flash) emits one Intent JSON; this file resolves names
and dispatches the actions.

Each public handler returns a dict with:
  ok: bool
  narrative: str          # human-readable summary for the LLM to relay
  actions_taken: list     # list of low-level commands actually dispatched
  errors: list            # collected non-fatal errors
"""

from __future__ import annotations

from typing import Optional, Tuple, List, Dict, Any

from . import intent_dsl as D
from . import geometry as G
from .schema import Vec2
from .tactical import get_engine as _get_tactical_engine


# ---------------------------------------------------------------------------
# Resolver: world-state queries via transport
# ---------------------------------------------------------------------------

class WorldView:
    """Cheap cache of one world snapshot; rebuilt at the start of every
    dispatch so each interpret() call sees a consistent picture."""

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

        self.groups_resp = transport.send_command({"type": "list_groups"})
        if self.groups_resp.get("ok"):
            self.groups = {g["name"]: g for g in self.groups_resp.get("groups", [])}
        else:
            self.groups = {}

    # --- Force ---------------------------------------------------------

    def resolve_force(self, force, exclude_ids: Optional[set] = None) -> List[int]:
        """Resolve a force spec to actor ids.

        exclude_ids: optional set of actor ids to filter out. Used by multi-
        mission dispatches (e.g. pincer) to give the second arm a disjoint
        unit pool — otherwise both arms resolve the same `combat_mobile`
        filter and steal units from each other every tick (confirmed
        bug 2026-05-23 in a destroy_enemy pincer).
        """
        if isinstance(force, D.ForceByGroup):
            ids = self._force_by_group(force.name)
        elif isinstance(force, D.ForceByIds):
            ids = list(force.unit_ids)
        elif isinstance(force, D.ForceByFilter):
            ids = self._force_by_filter(force)
        else:
            raise TypeError(f"unsupported force: {type(force)}")
        if exclude_ids:
            ids = [uid for uid in ids if uid not in exclude_ids]
        return ids

    def _force_by_group(self, name: str) -> List[int]:
        # "all" and "mobile" both mean combat-mobile self units.
        # Player intent "全军" / "全部" / "all units" never includes harvesters
        # (must keep mining) or buildings (immobile). Use "everything" as the
        # escape hatch if literally every owned actor id is wanted.
        if name in ("all", "mobile"):
            return [u["id"] for u in self.self_units if _is_combat_mobile(u["kind"])]
        if name == "everything":
            return [u["id"] for u in self.self_units]
        g = self.groups.get(name)
        if not g:
            return []
        return list(g.get("unit_ids", []))

    def _force_by_filter(self, f: D.ForceByFilter) -> List[int]:
        pool = []
        if f.owner == "self":
            pool = self.self_units
        elif f.owner == "enemy":
            pool = self.enemy_units
        else:
            pool = self.self_units + self.enemy_units

        if f.in_group:
            ids_in = set(self._force_by_group(f.in_group))
            pool = [u for u in pool if u["id"] in ids_in]

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
                if kind_lower in _NON_COMBAT or kind_lower in _BUILDING_KINDS:
                    continue
            matched.append(u)

        # Order by `prefer` so a downstream max_force_size truncation picks
        # the units the player would have picked, not whoever has the lowest
        # actor id (older units first by default).
        prefer = getattr(f, "prefer", "strongest")
        if prefer == "strongest":
            try:
                from . import tactical_doctrine as _DOCTRINE
                matched.sort(
                    key=lambda u: _DOCTRINE.unit_strength(u.get("kind", "")),
                    reverse=True,
                )
            except Exception:
                pass
        elif prefer == "fastest":
            # Light/fast kinds first, others after.
            matched.sort(key=lambda u: 0 if (u.get("kind") or "").lower() in _FAST_KINDS else 1)
        elif prefer == "healthiest":
            matched.sort(key=lambda u: u.get("hp_pct", 1.0), reverse=True)
        # "any" — leave actor-id order
        return [u["id"] for u in matched]

    # --- Target --------------------------------------------------------

    def resolve_target(self, target) -> Tuple[Optional[int], Optional[Tuple[int, int]]]:
        """Return (actor_id, (x,y)). Either may be None depending on the target type."""
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
        # ---- enemy_fact: pick the first 'fact' on enemy side
        if name == "enemy_fact":
            for u in self.enemy_units:
                if u["kind"].lower() == "fact":
                    return (u["id"], (u["pos"]["x"], u["pos"]["y"]))
            # fallback: any enemy structure (building-ish kinds)
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
            return (None, None)  # caller fills in based on force

        raise ValueError(f"unknown named target: {name!r}")

    def _centroid(self, units: list) -> Optional[Tuple[int, int]]:
        if not units:
            return None
        n = len(units)
        sx = sum(u["pos"]["x"] for u in units) // n
        sy = sum(u["pos"]["y"] for u in units) // n
        return (sx, sy)

    def force_centroid(self, ids: List[int]) -> Optional[Tuple[int, int]]:
        if not ids:
            return None
        id_set = set(ids)
        units = [u for u in self.self_units if u["id"] in id_set]
        return self._centroid(units)

    # --- Region --------------------------------------------------------

    def resolve_region(self, region) -> Tuple[Tuple[int, int], int]:
        """Return (center, radius)."""
        if isinstance(region, D.RegionAround):
            named = D.TargetByName(name=region.center)
            _, pos = self.resolve_target(named)
            if pos is None:
                pos = (self.map_size[0] // 2, self.map_size[1] // 2)
            return (pos, region.radius)
        if isinstance(region, D.RegionRect):
            return (((region.x1 + region.x2) // 2, (region.y1 + region.y2) // 2),
                    max(region.x2 - region.x1, region.y2 - region.y1) // 2)
        if isinstance(region, D.RegionNamed):
            if region.name == "self_base_perimeter":
                _, pos = self._resolve_named("self_base")
                return (pos or (self.map_size[0] // 4, self.map_size[1] // 2), 12)
            if region.name == "map_center":
                return ((self.map_size[0] // 2, self.map_size[1] // 2), 10)
            if region.name == "enemy_approach_lanes":
                _, pos = self._resolve_named("self_base")
                if pos is None:
                    return ((self.map_size[0] // 2, self.map_size[1] // 2), 15)
                ec = self._centroid(self.enemy_units) or pos
                mid = (((pos[0] + ec[0]) // 2), ((pos[1] + ec[1]) // 2))
                return (mid, 15)
            raise ValueError(f"unknown named region: {region.name!r}")
        raise TypeError(f"unsupported region: {type(region)}")


# ---------------------------------------------------------------------------
# Dispatch: one Intent -> list of atomic MCP commands
# ---------------------------------------------------------------------------

def interpret(intent_payload: dict, transport) -> dict:
    """Public entry. Parse + resolve + dispatch one intent."""
    try:
        intent = D.parse_intent(intent_payload)
    except Exception as e:
        return {"ok": False, "error": f"parse_error: {e}",
                "actions_taken": [], "narrative": ""}

    wv = WorldView(transport)
    if intent.intent == "attack":
        return _do_attack(intent, wv, transport)
    if intent.intent == "defend":
        return _do_defend(intent, wv, transport)
    if intent.intent == "retreat":
        return _do_retreat(intent, wv, transport)
    if intent.intent == "regroup":
        return _do_regroup(intent, wv, transport)
    if intent.intent == "scout":
        return _do_scout(intent, wv, transport)
    if intent.intent == "pincer":
        return _do_pincer(intent, wv, transport)
    if intent.intent == "feint":
        return _do_feint(intent, wv, transport)
    if intent.intent == "harass":
        return _do_harass(intent, wv, transport)
    if intent.intent == "patrol":
        return _do_patrol(intent, wv, transport)
    if intent.intent == "escort":
        return _do_escort(intent, wv, transport)
    if intent.intent == "contain":
        return _do_contain(intent, wv, transport)
    if intent.intent == "diversion":
        return _do_diversion(intent, wv, transport)
    if intent.intent == "set_stance":
        return _do_set_stance(intent, wv, transport)
    if intent.intent == "report":
        return _do_report(intent, wv, transport)
    if intent.intent == "raw":
        return _do_raw(intent, wv, transport)
    return {"ok": False, "error": f"unhandled intent: {intent.intent}",
            "actions_taken": [], "narrative": ""}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _ok(narrative: str, actions: list, **extra) -> dict:
    out = {"ok": True, "narrative": narrative, "actions_taken": actions}
    out.update(extra)
    return out


def _err(narrative: str, actions: list, error: str) -> dict:
    return {"ok": False, "narrative": narrative, "actions_taken": actions, "error": error}


def _send(transport, cmd: dict, log: list):
    resp = transport.send_command(cmd)
    log.append({"cmd": cmd, "resp": resp})
    return resp


# RA actor names that are buildings (occupy footprint; cannot be focus-fired
# productively while taking incoming from enemy mobile units — units following
# an "Attack target_id=building" order ignore counter-fire from adjacent enemies
# even with AttackAnything stance, causing the suicide-into-base behavior).
# Source: docs/RA_ACTOR_NAMES.md. Keep this in sync.
_BUILDING_KINDS = frozenset({
    "fact", "powr", "apwr", "proc", "silo", "dome", "fix",
    "barr", "tent", "kenn", "weap", "hpad", "afld", "afld.ukraine",
    "syrd", "spen",
    "pbox", "hbox", "gun", "agun", "sam", "ftur", "tsla",
    "atek", "stek", "mslo", "iron", "pdox", "gap",
    "sbag", "brik", "barb", "cycl", "fenc",
    "oilb",  # neutral oil derrick
})

# Non-combat mobile units. Harvesters must keep mining; MCV is meant for
# deploying a new CY, not for combat. When the player says "全军出击" they
# mean combat units only — these stay home.
_NON_COMBAT_MOBILE_KINDS = frozenset({
    "harv", "mcv",
})

# Harass-capable kinds — fast and / or kite-able units that can attack the
# enemy economy and then disengage. The whitelist matches the CONTEXT.md
# definition (Force Filter). _HARASS_BAD is an explicit blacklist for the
# rare case a unit ends up in both (e.g. someone adds 1tnk to BAD later).
_HARASS_CAPABLE = frozenset({"jeep", "ftrk", "dog", "e3", "apc", "1tnk"})
_HARASS_BAD = frozenset({"2tnk", "3tnk", "4tnk", "arty", "v2rl", "mcv", "harv"})
_NON_COMBAT = frozenset({"harv", "mcv"})
# NOTE: _BUILDING_KINDS authoritative definition is earlier in this file
# (line ~298). It already includes oilb / afld.ukraine / fenc which the old
# phase-6 redefinition here was missing — that re-binding silently let
# combat_mobile filter pick neutral oil derricks as "units". Removed.
_FAST_KINDS = frozenset({"jeep", "dog", "e3", "e1", "ftrk", "spy", "thf"})


def _is_building(kind: str) -> bool:
    return (kind or "").lower() in _BUILDING_KINDS


def _is_combat_mobile(kind: str) -> bool:
    k = (kind or "").lower()
    return (k not in _BUILDING_KINDS) and (k not in _NON_COMBAT_MOBILE_KINDS)


def _target_kind(wv: "WorldView", tid: int | None) -> str:
    if tid is None:
        return ""
    for u in wv.self_units + wv.enemy_units:
        if u.get("id") == tid:
            return u.get("kind", "")
    return ""


def _pending_reason(mission_kind: str, force) -> str:
    """Human-readable, LLM-friendly reason string for a pending-queue entry.
    Tells the LLM what to suggest the player trains (so the daemon's later
    re-dispatch actually fires)."""
    if isinstance(force, D.ForceByFilter):
        if force.harass_capable:
            return ("需要骚扰单位 (jeep / ftrk / dog / e3 / apc / 1tnk) — "
                    "训出来后 daemon 自动启动 " + mission_kind)
        if force.unit_kind:
            return f"需要 {force.unit_kind} — 训出来后 daemon 自动启动 {mission_kind}"
        return f"force filter 当前匹配 0 单位 — 训出符合的后自动启动 {mission_kind}"
    if isinstance(force, D.ForceByGroup):
        return f"group '{force.name}' 当前空 — 等单位进组后自动启动 {mission_kind}"
    if isinstance(force, D.ForceByIds):
        return (f"指定的 actor id 当前都不存在 — {mission_kind} 不会自动恢复, "
                f"考虑改用 filter")
    return f"force resolution returned empty for {mission_kind}"


def _do_attack(intent: D.IntentAttack, wv: WorldView, transport) -> dict:
    """Register an Assault mission with the tactical daemon.

    Architecture: interpreter ONLY validates the DSL, resolves names → ids,
    and registers the mission. The daemon owns ALL engine commands going
    forward (move / attack / stance / stop), starting from its next tick.
    Do not send atomic engine orders from here — that creates a race where
    LLM-issued commands fight the daemon's per-tick formation control.
    """
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")

    tid, tpos = wv.resolve_target(intent.target)

    if isinstance(intent.target, D.TargetByName) and intent.target.name in (
        "nearest_enemy", "nearest_enemy_unit", "nearest_enemy_structure"
    ):
        # Pick nearest enemy from force centroid, with a strong bias toward
        # MOBILE threats over static structures.
        center = wv.force_centroid(ids)
        if center and wv.enemy_units:
            wanted_mobile = intent.target.name != "nearest_enemy_structure"
            wanted_struct = intent.target.name != "nearest_enemy_unit"
            mobile_candidates = [u for u in wv.enemy_units
                                 if wanted_mobile and not _is_building(u["kind"])]
            struct_candidates = [u for u in wv.enemy_units
                                 if wanted_struct and _is_building(u["kind"])]
            ENGAGE_RADIUS = 35
            near_mobile = [u for u in mobile_candidates
                           if G.distance(center, (u["pos"]["x"], u["pos"]["y"])) <= ENGAGE_RADIUS]
            pool = near_mobile or mobile_candidates or struct_candidates
            if pool:
                nearest = min(pool,
                              key=lambda u: G.distance(center, (u["pos"]["x"], u["pos"]["y"])))
                tid = nearest["id"]
                tpos = (nearest["pos"]["x"], nearest["pos"]["y"])

    if tpos is None and tid is None:
        return _err("target unresolved", actions, "target_resolution_failed")

    force_center = wv.force_centroid(ids) or tpos
    target_named = (intent.target.name
                    if isinstance(intent.target, D.TargetByName)
                    else None)

    # Approach influences mission parameters the daemon respects:
    #   - charge      → cohesion=False, daemon doesn't gate vanguards
    #   - flank_*     → use a waypoint instead of straight line; daemon
    #                   marches to waypoint first via final_target_cell rewrite
    #   - split       → register TWO assaults (front + flank)
    #   - cautious    → keep distance at weapon range; cohesion on
    #   - frontal     → straight line, cohesion on (default)
    final_cell = tpos
    second_assault = None  # for split
    cohesion = True
    if intent.approach == "charge":
        cohesion = False
    elif intent.approach in ("flank_left", "flank_right"):
        side = "left" if intent.approach == "flank_left" else "right"
        wp = G.flank_waypoint(force_center, tpos, side, sidestep_cells=12, approach_t=0.55)
        final_cell = (wp[0], wp[1])
    elif intent.approach == "split":
        n = len(ids)
        front_ids = ids[: n // 2]
        flank_ids = ids[n // 2:]
        wp = G.flank_waypoint(force_center, tpos, "right", sidestep_cells=14, approach_t=0.55)
        ids = front_ids                  # primary registration
        second_assault = (flank_ids, (wp[0], wp[1]))
    elif intent.approach == "cautious":
        engage = G.cautious_engage_point(force_center, tpos, weapon_range_cells=6)
        final_cell = (engage[0], engage[1])

    # Backend = squad: hand off to engine-side Assault FSM. Approach is
    # collapsed (squad FSM doesn't know flank/cautious/split). The squad
    # cohesion gate (Phase D3) handles spacing; AttackAnything stance
    # (Phase D4) handles opportunistic strikes on the way.
    if intent.backend == "squad":
        resp = _dispatch_squad(
            transport, "Assault", ids,
            target_pos=final_cell if final_cell else tpos,
        )
        actions.append({"cmd": {"type": "spawn_squad", "squad_type": "Assault"},
                        "resp": resp})
        if not resp.get("ok"):
            return _err(f"squad backend failed: {resp.get('error')}", actions,
                        "squad_register_failed")
        return _ok(
            f"attack (squad backend) {resp.get('unit_count')} unit(s) → "
            f"{tid or final_cell} [squad #{resp.get('squad_index')}]",
            actions, squad_index=resp.get("squad_index"),
        )

    mission_ids: List[int] = []
    try:
        engine = _get_tactical_engine(transport)
        mid = engine.register_assault(
            force_ids=ids,
            final_target_cell=final_cell,
            final_target_actor=tid,
            cohesion=cohesion,
            target_named=target_named,
        )
        if mid is not None:
            mission_ids.append(mid)
        if second_assault is not None and second_assault[0]:
            mid2 = engine.register_assault(
                force_ids=second_assault[0],
                final_target_cell=second_assault[1],
                final_target_actor=tid,
                cohesion=True,
                target_named=target_named,
            )
            if mid2 is not None:
                mission_ids.append(mid2)
    except Exception as e:
        return _err(f"daemon registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"{intent.approach}: {len(ids)} unit(s) → {tid or tpos} [mission(s) {mission_ids}]",
        actions, mission_ids=mission_ids)


def _do_defend(intent: D.IntentDefend, wv: WorldView, transport) -> dict:
    """Register a ContainmentMission with the daemon. Daemon owns all
    engine commands — move to position, set stance, auto-engage intruders,
    pull strays back. Interpreter does not issue atomics here.
    """
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")

    center, radius = wv.resolve_region(intent.region)

    if intent.backend == "squad":
        # Defend → Protection squad (engages threats near a cell).
        resp = _dispatch_squad(
            transport, "Protection", ids, target_pos=center,
        )
        actions.append({"cmd": {"type": "spawn_squad",
                                "squad_type": "Protection"},
                        "resp": resp})
        if not resp.get("ok"):
            return _err(f"squad backend failed: {resp.get('error')}", actions,
                        "squad_register_failed")
        return _ok(
            f"defend (squad backend / Protection) {resp.get('unit_count')} "
            f"unit(s) at {center} [squad #{resp.get('squad_index')}]",
            actions, squad_index=resp.get("squad_index"),
        )

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_contain(
            force_ids=ids,
            chokepoint=center,
            radius=max(3, radius),
            stance=intent.stance,
        )
    except Exception as e:
        return _err(f"daemon registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"defend at {center} (r={radius}) with {len(ids)} unit(s), "
        f"stance={intent.stance} [mission #{mission_id}]",
        actions, mission_id=mission_id)


def _do_retreat(intent: D.IntentRetreat, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    _, pos = wv.resolve_target(intent.to)
    if pos is None:
        return _err("retreat target unresolved", actions, "target_resolution_failed")
    _send(transport, {"type": "set_stance", "unit_ids": ids,
                      "stance": "HoldFire"}, actions)
    _send(transport,
          {"type": "move", "unit_ids": ids,
           "target": {"x": pos[0], "y": pos[1]}, "attack_move": False},
          actions)
    return _ok(f"retreat {len(ids)} unit(s) to {pos}", actions)


def _do_regroup(intent: D.IntentRegroup, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    _, pos = wv.resolve_target(intent.at)
    if pos is None:
        return _err("regroup target unresolved", actions, "target_resolution_failed")
    _send(transport,
          {"type": "move", "unit_ids": ids,
           "target": {"x": pos[0], "y": pos[1]}, "attack_move": False},
          actions)
    return _ok(f"regroup {len(ids)} unit(s) at {pos}", actions)


def _do_scout(intent: D.IntentScout, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    # Pick the fastest few (just take first 3 for now)
    scout_ids = ids[:3]
    center, _ = wv.resolve_region(intent.region)
    _send(transport,
          {"type": "move", "unit_ids": scout_ids,
           "target": {"x": center[0], "y": center[1]}, "attack_move": True},
          actions)
    return _ok(f"scout {len(scout_ids)} unit(s) to {center}", actions)


def _do_pincer(intent: D.IntentPincer, wv: WorldView, transport) -> dict:
    """Register two Assault missions (left + right arms) with the daemon.

    Daemon owns per-arm cohesion + engage-on-contact + retarget. We compute
    the waypoint pair here so each arm's daemon-driven push approaches from
    the chosen flank, then converges on the target.
    """
    actions: List[dict] = []
    # Pre-resolve left/right separately so we can detect pool overlap. When
    # both arms reference the SAME pool (e.g. both filter combat_mobile=true),
    # naive resolution returns 100% overlap → each daemon tick the two
    # missions fight over identical actor ids and the army oscillates. Split
    # the overlap roughly in half: half goes to left, half to right. Disjoint
    # specs (e.g. group=north vs group=south) are unaffected.
    left_raw = wv.resolve_force(intent.left)
    right_raw = wv.resolve_force(intent.right)
    overlap = set(left_raw) & set(right_raw)
    if overlap and (set(left_raw) == set(right_raw)):
        # Total overlap — split in half by priority order already imposed by
        # the resolver's `prefer` setting (strongest first by default).
        half = (len(left_raw) + 1) // 2
        left_ids = left_raw[:half]
        right_ids = [uid for uid in right_raw if uid not in set(left_ids)]
    elif overlap:
        # Partial overlap — let left keep the overlap, strip it from right.
        left_ids = left_raw
        right_ids = [uid for uid in right_raw if uid not in overlap]
    else:
        left_ids, right_ids = left_raw, right_raw
    if not left_ids and not right_ids:
        return _err("both arms empty", actions, "force_resolution_empty")

    tid, tpos = wv.resolve_target(intent.target)
    if tpos is None:
        return _err("target unresolved", actions, "target_resolution_failed")

    left_center = wv.force_centroid(left_ids) or tpos
    right_center = wv.force_centroid(right_ids) or tpos

    lwp, rwp = G.pincer_rendezvous(tpos, intent.rendezvous_dist,
                                    left_center, right_center)
    target_named = (intent.target.name
                    if isinstance(intent.target, D.TargetByName)
                    else None)

    mission_ids: List[int] = []
    try:
        engine = _get_tactical_engine(transport)
        if left_ids:
            mid = engine.register_assault(force_ids=left_ids,
                                          final_target_cell=lwp,
                                          final_target_actor=tid,
                                          cohesion=True,
                                          target_named=target_named)
            if mid is not None:
                mission_ids.append(mid)
        if right_ids:
            mid = engine.register_assault(force_ids=right_ids,
                                          final_target_cell=rwp,
                                          final_target_actor=tid,
                                          cohesion=True,
                                          target_named=target_named)
            if mid is not None:
                mission_ids.append(mid)
    except Exception as e:
        return _err(f"daemon registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"pincer: left {len(left_ids)} via {lwp}, right {len(right_ids)} via {rwp}, "
        f"target {tid or tpos} [mission(s) {mission_ids}]",
        actions, mission_ids=mission_ids)


def _do_feint(intent: D.IntentFeint, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    _, tpos = wv.resolve_target(intent.target)
    if tpos is None:
        return _err("feint target unresolved", actions, "target_resolution_failed")
    force_center = wv.force_centroid(ids) or tpos
    stop = G.feint_stopline(force_center, tpos, engage_distance=8)
    _send(transport, {"type": "set_stance", "unit_ids": ids,
                      "stance": "ReturnFire"}, actions)
    _send(transport,
          {"type": "move", "unit_ids": ids,
           "target": {"x": stop[0], "y": stop[1]}, "attack_move": False},
          actions)
    return _ok(f"feint: {len(ids)} unit(s) advance to {stop}, hold short of target",
               actions)


def _dispatch_squad(transport, squad_type: str, force_ids: List[int],
                    target_pos: Optional[Tuple[int, int]] = None,
                    rally_point: Optional[Tuple[int, int]] = None,
                    waypoints: Optional[List[Tuple[int, int]]] = None,
                    escortee_actor_id: Optional[int] = None) -> dict:
    """Shared helper: route an intent to spawn_squad over the bridge.

    Used by the squad-backend branch of harass/patrol/escort/contain. The
    payload mirrors what mcp_server.server.spawn_squad sends.
    """
    payload: dict = {
        "type": "spawn_squad",
        "squad_type": squad_type,
    }
    if force_ids:
        payload["unit_ids"] = [int(i) for i in force_ids]
    if target_pos is not None:
        payload["target_pos"] = {"x": int(target_pos[0]), "y": int(target_pos[1])}
    if rally_point is not None:
        payload["rally_point"] = {"x": int(rally_point[0]), "y": int(rally_point[1])}
    if waypoints:
        payload["waypoints"] = [{"x": int(x), "y": int(y)} for (x, y) in waypoints]
    if escortee_actor_id is not None:
        payload["escortee_actor_id"] = int(escortee_actor_id)
    return transport.send_command(payload)


def _do_harass(intent: D.IntentHarass, wv: WorldView, transport) -> dict:
    """Register a HarassMission with the tactical daemon.

    Resolves force + region to ids/coords, then hands the mission to the
    daemon which runs the engaging/withdrawing/regrouping state machine
    autonomously. LLM does not need to drive the cycle.

    If the force spec resolves to zero units (e.g. player wants harass but
    has no harass_capable units yet), enqueue as a PendingMission instead of
    erroring out. The daemon re-attempts the dispatch every few seconds; the
    LLM tells the player what to train.
    """
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if intent.max_force_size is not None:
        ids = ids[: intent.max_force_size]

    if not ids:
        reason = _pending_reason("harass", intent.force)
        try:
            engine = _get_tactical_engine(transport)
            pid = engine.queue_pending(
                "harass", intent.model_dump(mode="json"), reason
            )
        except Exception as e:
            return _err(f"queue_pending failed: {e}", actions,
                        "queue_pending_failed")
        return _ok(
            f"harass queued (no matching units yet). {reason} "
            f"[pending #{pid}, retries automatically]",
            actions, pending_id=pid,
        )

    center, radius = wv.resolve_region(intent.region)

    # Withdraw destination — default to self_base.
    if intent.withdraw_to is None:
        _, wpos = wv.resolve_target(D.TargetByName(name="self_base"))
    else:
        _, wpos = wv.resolve_target(intent.withdraw_to)
    if wpos is None:
        wpos = wv.force_centroid(ids) or center

    # Backend = squad: hand off to engine-side Harass FSM, skip daemon.
    if intent.backend == "squad":
        resp = _dispatch_squad(
            transport, "Harass", ids,
            target_pos=center, rally_point=wpos,
        )
        actions.append({"cmd": {"type": "spawn_squad", "squad_type": "Harass"},
                        "resp": resp})
        if not resp.get("ok"):
            return _err(f"squad backend failed: {resp.get('error')}", actions,
                        "squad_register_failed")
        return _ok(
            f"harass (squad backend) {resp.get('unit_count')} unit(s) at "
            f"{center}, rally={wpos} [squad #{resp.get('squad_index')}]",
            actions, squad_index=resp.get("squad_index"),
        )

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_harass(
            force_ids=ids,
            region_center=center,
            region_radius=radius,
            withdraw_to=wpos,
            withdraw_hp_threshold=intent.withdraw_hp_threshold,
            reengage_hp_threshold=intent.reengage_hp_threshold,
            cycle=intent.cycle,
            max_force_size=intent.max_force_size,
            # Dynamic re-resolution: daemon absorbs newly-trained matching
            # units mid-mission. Only meaningful for filter/group specs;
            # ids-specs work as static (daemon never recruits more).
            force_spec=intent.force.model_dump(mode="json"),
        )
    except Exception as e:
        return _err(f"harass registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"harass cycle on region {center} (r={radius}) with {len(ids)} unit(s), "
        f"withdraw_to={wpos} [mission #{mission_id}]",
        actions, mission_id=mission_id,
    )


def _do_patrol(intent: D.IntentPatrol, wv: WorldView, transport) -> dict:
    """Register a PatrolMission — daemon walks waypoints loop, engages on
    contact per `contact_stance`, breaks off wounded units to self_base.

    Empty force → queue as pending. Patrol is cycle-type so the daemon will
    pick it up the moment the player trains a scout / matching unit.
    """
    actions: List[dict] = []
    if not intent.waypoints:
        return _err("patrol needs at least one waypoint", actions,
                    "waypoints_empty")
    ids = wv.resolve_force(intent.force)

    if not ids:
        reason = _pending_reason("patrol", intent.force)
        try:
            engine = _get_tactical_engine(transport)
            pid = engine.queue_pending(
                "patrol", intent.model_dump(mode="json"), reason
            )
        except Exception as e:
            return _err(f"queue_pending failed: {e}", actions,
                        "queue_pending_failed")
        return _ok(
            f"patrol queued (no matching units yet). {reason} [pending #{pid}]",
            actions, pending_id=pid,
        )

    waypoints = [(wp.x, wp.y) for wp in intent.waypoints]

    _, wpos = wv.resolve_target(D.TargetByName(name="self_base"))
    if wpos is None:
        wpos = wv.force_centroid(ids) or waypoints[0]

    if intent.backend == "squad":
        resp = _dispatch_squad(
            transport, "Patrol", ids, waypoints=waypoints,
        )
        actions.append({"cmd": {"type": "spawn_squad", "squad_type": "Patrol"},
                        "resp": resp})
        if not resp.get("ok"):
            return _err(f"squad backend failed: {resp.get('error')}", actions,
                        "squad_register_failed")
        return _ok(
            f"patrol (squad backend) {resp.get('unit_count')} unit(s) "
            f"on {len(waypoints)} waypoint(s) [squad #{resp.get('squad_index')}]",
            actions, squad_index=resp.get("squad_index"),
        )

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_patrol(
            force_ids=ids,
            waypoints=waypoints,
            withdraw_to=wpos,
            cycle=intent.cycle,
            contact_stance=intent.contact_stance,
            force_spec=intent.force.model_dump(mode="json"),
        )
    except Exception as e:
        return _err(f"patrol registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"patrol {len(ids)} unit(s) on {len(waypoints)} waypoint(s), "
        f"cycle={intent.cycle} [mission #{mission_id}]",
        actions, mission_id=mission_id,
    )


def _do_escort(intent: D.IntentEscort, wv: WorldView, transport) -> dict:
    """Register an EscortMission — guards stay within escort_radius of
    escortee, engage threats within engage_radius. Ends when escortee dies.

    Half-dynamic: bodyguard force re-resolves (filter recruits new
    bodyguards), but escortee remains fixed. Empty bodyguard pool → pending.
    Missing escortee → hard error (it's player data, not a queueable cond).
    """
    actions: List[dict] = []

    # Escortee missing is an unconditional error; don't queue something that
    # depends on an actor id that might never come back.
    escortee_present = any(u["id"] == intent.escortee_id for u in wv.self_units)
    if not escortee_present:
        return _err(f"escortee actor {intent.escortee_id} not found among self units",
                    actions, "escortee_not_found")

    ids = wv.resolve_force(intent.force)
    if not ids:
        reason = _pending_reason("escort", intent.force)
        try:
            engine = _get_tactical_engine(transport)
            pid = engine.queue_pending(
                "escort", intent.model_dump(mode="json"), reason
            )
        except Exception as e:
            return _err(f"queue_pending failed: {e}", actions,
                        "queue_pending_failed")
        return _ok(
            f"escort queued (no bodyguards yet). {reason} [pending #{pid}]",
            actions, pending_id=pid,
        )

    dest_pos = None
    if intent.destination is not None:
        _, dest_pos = wv.resolve_target(intent.destination)

    if intent.backend == "squad":
        resp = _dispatch_squad(
            transport, "Escort", ids,
            escortee_actor_id=intent.escortee_id,
            target_pos=dest_pos,
        )
        actions.append({"cmd": {"type": "spawn_squad", "squad_type": "Escort"},
                        "resp": resp})
        if not resp.get("ok"):
            return _err(f"squad backend failed: {resp.get('error')}", actions,
                        "squad_register_failed")
        return _ok(
            f"escort (squad backend) {resp.get('unit_count')} unit(s) → "
            f"actor {intent.escortee_id} [squad #{resp.get('squad_index')}]",
            actions, squad_index=resp.get("squad_index"),
        )

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_escort(
            force_ids=ids,
            escortee_id=intent.escortee_id,
            destination=dest_pos,
            escort_radius=intent.escort_radius,
            engage_radius=intent.engage_radius,
            force_spec=intent.force.model_dump(mode="json"),
        )
    except Exception as e:
        return _err(f"escort registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"escort {len(ids)} unit(s) → actor {intent.escortee_id} "
        f"(escort r={intent.escort_radius}, engage r={intent.engage_radius}) "
        f"[mission #{mission_id}]",
        actions, mission_id=mission_id,
    )


def _do_contain(intent: D.IntentContain, wv: WorldView, transport) -> dict:
    """Register a ContainmentMission — force holds chokepoint, engages in
    radius, doesn't pursue. Empty force → pending."""
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        reason = _pending_reason("contain", intent.force)
        try:
            engine = _get_tactical_engine(transport)
            pid = engine.queue_pending(
                "contain", intent.model_dump(mode="json"), reason
            )
        except Exception as e:
            return _err(f"queue_pending failed: {e}", actions,
                        "queue_pending_failed")
        return _ok(
            f"contain queued (no matching units yet). {reason} [pending #{pid}]",
            actions, pending_id=pid,
        )

    cp = (intent.chokepoint.x, intent.chokepoint.y)

    if intent.backend == "squad":
        # Map contain → Protection squad (defends a cell).
        resp = _dispatch_squad(
            transport, "Protection", ids, target_pos=cp,
        )
        actions.append({"cmd": {"type": "spawn_squad",
                                "squad_type": "Protection"},
                        "resp": resp})
        if not resp.get("ok"):
            return _err(f"squad backend failed: {resp.get('error')}", actions,
                        "squad_register_failed")
        return _ok(
            f"contain (squad backend / Protection) {resp.get('unit_count')} "
            f"unit(s) at {cp} [squad #{resp.get('squad_index')}]",
            actions, squad_index=resp.get("squad_index"),
        )

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_contain(
            force_ids=ids,
            chokepoint=cp,
            radius=intent.radius,
            stance=intent.stance,
        )
    except Exception as e:
        return _err(f"daemon registration failed: {e}", actions,
                    "daemon_register_failed")

    return _ok(
        f"contain {len(ids)} unit(s) at {cp} (r={intent.radius}) "
        f"[mission #{mission_id}]",
        actions, mission_id=mission_id)


def _do_diversion(intent: D.IntentDiversion, wv: WorldView, transport) -> dict:
    """Register a DiversionMission — feint + raid prongs coordinated by the
    daemon. feint holds at stopline, raid attacks via flank waypoint.

    Both prongs empty → queue as pending. If only one prong is empty we
    proceed (the daemon's tick logic handles a missing prong as withdrew).
    """
    actions: List[dict] = []
    feint_ids = wv.resolve_force(intent.feint_force)
    raid_ids = wv.resolve_force(intent.raid_force)
    if not feint_ids and not raid_ids:
        reason = "no units match either feint_force or raid_force"
        try:
            engine = _get_tactical_engine(transport)
            pid = engine.queue_pending(
                "diversion", intent.model_dump(mode="json"), reason
            )
        except Exception as e:
            return _err(f"queue_pending failed: {e}", actions,
                        "queue_pending_failed")
        return _ok(
            f"diversion queued (no matching units yet). {reason} [pending #{pid}]",
            actions, pending_id=pid,
        )

    _, feint_tpos = wv.resolve_target(intent.feint_target)
    raid_tid, raid_tpos = wv.resolve_target(intent.raid_target)
    if feint_tpos is None or raid_tpos is None:
        return _err("diversion target unresolved", actions,
                    "target_resolution_failed")

    # Compute feint stopline (8 cells short of feint_target from feint centroid).
    feint_center = wv.force_centroid(feint_ids) or feint_tpos
    feint_stop = G.feint_stopline(feint_center, feint_tpos, engage_distance=8)

    # Compute raid waypoint per approach (flank_left / flank_right only).
    raid_center = wv.force_centroid(raid_ids) or raid_tpos
    raid_wp = None
    if intent.raid_approach in ("flank_left", "flank_right"):
        side = "left" if intent.raid_approach == "flank_left" else "right"
        raid_wp = G.flank_waypoint(raid_center, raid_tpos, side,
                                   sidestep_cells=12, approach_t=0.55)

    # Withdraw target — self_base.
    _, withdraw_pos = wv.resolve_target(D.TargetByName(name="self_base"))
    if withdraw_pos is None:
        withdraw_pos = feint_center

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_diversion(
            feint_force_ids=feint_ids,
            feint_target_cell=feint_stop,
            raid_force_ids=raid_ids,
            raid_target_cell=raid_tpos,
            raid_target_actor=raid_tid,
            raid_waypoint=raid_wp,
            withdraw_to=withdraw_pos,
            feint_commits=intent.feint_commits,
        )
    except Exception as e:
        return _err(f"daemon registration failed: {e}", actions,
                    "daemon_register_failed")

    msg = (f"diversion: feint {len(feint_ids)} → {feint_stop} (stopline), "
           f"raid {len(raid_ids)} → {raid_tpos} via {intent.raid_approach}")
    if raid_wp is not None:
        msg += f" (wp {raid_wp})"
    if mission_id is not None:
        msg += f" [mission #{mission_id}]"
    return _ok(msg, actions, mission_id=mission_id)


def _do_set_stance(intent: D.IntentSetStance, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")
    _send(transport, {"type": "set_stance", "unit_ids": ids,
                      "stance": intent.stance}, actions)
    return _ok(f"set stance {intent.stance} on {len(ids)} unit(s)", actions)


def _do_report(intent: D.IntentReport, wv: WorldView, transport) -> dict:
    """Read-only intent. Returns a structured snapshot for the LLM to narrate."""
    if intent.what == "battlefield":
        return _ok(_narrate_battlefield(wv), actions=[],
                   snapshot={"tick": wv.tick,
                             "self_count": len(wv.self_units),
                             "enemy_count": len(wv.enemy_units)})
    if intent.what == "groups":
        return _ok(_narrate_groups(wv), actions=[],
                   groups=wv.groups_resp.get("groups", []))
    if intent.what.startswith("group_"):
        name = intent.what[len("group_"):]
        g = wv.groups.get(name)
        if not g:
            return _err(f"group '{name}' not found", [], "group_not_found")
        return _ok(_narrate_group(g), actions=[], group=g)
    if intent.what == "enemy":
        return _ok(_narrate_enemy(wv), actions=[],
                   enemy_units=wv.enemy_units)
    if intent.what == "threats":
        return _ok(_narrate_threats(wv), actions=[],
                   enemy_units=wv.enemy_units)
    if intent.what == "enemy_intent":
        from . import enemy_intent as EI
        cls = EI.classify_enemy(wv.self_units, wv.enemy_units)
        narrative = (
            f"敌方意图: {cls['primary']} "
            f"(信心 {int(cls['confidence']*100)}%, 阶段 {cls['stage']}, "
            f"共 {cls['enemy_total']} 单位). 反制: {cls['counter_recommendation']}"
        )
        return _ok(narrative, actions=[], classification=cls)
    if intent.what == "minimap":
        resp = transport.send_command({"type": "screenshot"})
        return _ok("Screenshot queued (will write to OpenRA Support/Screenshots).",
                   actions=[{"cmd": {"type": "screenshot"}, "resp": resp}])
    if intent.what == "resources":
        s = wv.state.get("state", {})
        return _ok(f"cash={s.get('self_cash', 0)}, power={s.get('self_power', 0)}",
                   actions=[])
    return _err(f"unknown report what: {intent.what}", [], "report_what_unknown")


def _do_raw(intent: D.IntentRaw, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    for call in intent.atomic_calls:
        # We accept either {tool, args} or a raw command dict.
        if isinstance(call, dict) and "type" in call:
            cmd = call
        elif isinstance(call, dict) and "tool" in call:
            cmd = {"type": call["tool"], **(call.get("args") or {})}
        else:
            actions.append({"cmd": call, "resp": {"ok": False, "error": "malformed"}})
            continue
        _send(transport, cmd, actions)
    return _ok(f"dispatched {len(actions)} raw call(s)", actions)


# ---------------------------------------------------------------------------
# Narrators (deterministic strings; the LLM may paraphrase but data is here)
# ---------------------------------------------------------------------------

def _narrate_battlefield(wv: WorldView) -> str:
    if not wv.state.get("ok"):
        return "battlefield: bridge not connected"
    s = wv.state["state"]
    self_summary = _kind_summary(wv.self_units)
    enemy_summary = _kind_summary(wv.enemy_units)
    lines = [
        f"Tick {wv.tick}. Map {s.get('map_name', '?')} {wv.map_size}.",
        f"Cash {s.get('self_cash', 0)}, Power {s.get('self_power', 0)}.",
        f"Self: {len(wv.self_units)} unit(s) [{self_summary}].",
        f"Enemy: {len(wv.enemy_units)} unit(s) [{enemy_summary}].",
    ]
    if wv.groups:
        lines.append("Groups: " + ", ".join(
            f"{n}={g.get('count', 0)}" for n, g in wv.groups.items()))
    return " ".join(lines)


def _narrate_groups(wv: WorldView) -> str:
    if not wv.groups:
        return "no groups yet (auto-initializes on first list_groups call)"
    parts = []
    for name, g in wv.groups.items():
        parts.append(_narrate_group(g))
    return " | ".join(parts)


def _narrate_group(g: dict) -> str:
    comp = g.get("composition", {})
    comp_s = ",".join(f"{k}:{v}" for k, v in comp.items())
    center = g.get("center", {})
    return (f"{g['name']}: {g.get('count', 0)} unit(s) "
            f"[{comp_s}] center=({center.get('x', '?')},{center.get('y', '?')}) "
            f"avg_hp={g.get('avg_hp_pct', 1):.0%}")


def _narrate_enemy(wv: WorldView) -> str:
    if not wv.enemy_units:
        return "no enemy spotted (fog or none alive)"
    summary = _kind_summary(wv.enemy_units)
    centroid = wv._centroid(wv.enemy_units)
    return f"Enemy: {len(wv.enemy_units)} unit(s) [{summary}] centroid={centroid}"


def _narrate_threats(wv: WorldView) -> str:
    # rudimentary: pick enemies within 20 cells of any self unit
    threats = []
    for e in wv.enemy_units:
        ep = (e["pos"]["x"], e["pos"]["y"])
        for s in wv.self_units:
            sp = (s["pos"]["x"], s["pos"]["y"])
            if G.distance(ep, sp) < 20:
                threats.append(e)
                break
    if not threats:
        return "no immediate threats (no enemy within 20 cells of any of yours)"
    summary = _kind_summary(threats)
    return f"Threats: {len(threats)} enemy unit(s) within 20 cells [{summary}]"


def _kind_summary(units: list) -> str:
    counts: Dict[str, int] = {}
    for u in units:
        k = u.get("kind", "?")
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{k}×{c}" for k, c in sorted(counts.items(), key=lambda kv: -kv[1]))


