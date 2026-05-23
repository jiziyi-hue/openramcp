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

    def resolve_force(self, force) -> List[int]:
        if isinstance(force, D.ForceByGroup):
            return self._force_by_group(force.name)
        if isinstance(force, D.ForceByIds):
            return list(force.unit_ids)
        if isinstance(force, D.ForceByFilter):
            return self._force_by_filter(force)
        raise TypeError(f"unsupported force: {type(force)}")

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

        out = []
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
                # Only fast / kite-able kinds — excludes slow heavy armour and
                # siege artillery that can't escape after touching the economy.
                if kind_lower not in _HARASS_CAPABLE:
                    continue
                if kind_lower in _HARASS_BAD:
                    continue
            out.append(u["id"])
        return out

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
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")

    tid, tpos = wv.resolve_target(intent.target)

    if isinstance(intent.target, D.TargetByName) and intent.target.name in (
        "nearest_enemy", "nearest_enemy_unit", "nearest_enemy_structure"
    ):
        # Pick nearest enemy from force centroid, with a strong bias toward
        # MOBILE threats over static structures. A solo 3tnk at distance 25
        # matters more than an enemy powr at distance 8 — Attack-fire on the
        # building leaves the mobile threat free to shoot us in the back.
        center = wv.force_centroid(ids)
        if center and wv.enemy_units:
            wanted_mobile = intent.target.name != "nearest_enemy_structure"
            wanted_struct = intent.target.name != "nearest_enemy_unit"
            mobile_candidates = [u for u in wv.enemy_units
                                 if wanted_mobile and not _is_building(u["kind"])]
            struct_candidates = [u for u in wv.enemy_units
                                 if wanted_struct and _is_building(u["kind"])]

            # Prefer a mobile target within ENGAGE_RADIUS (cells). If none,
            # fall back to nearest mobile anywhere, then nearest structure.
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

    # Hand the assault to the tactical daemon so it can engage on contact,
    # re-target when the current threat dies, and hold formation. The
    # daemon polls every ~0.6 s — fast enough that LLM lag is no longer
    # the bottleneck. Cautious approach skips registration (distance-keeping
    # is the player's deliberate choice; daemon would override it).
    if intent.approach != "cautious" and tpos is not None:
        try:
            engine = _get_tactical_engine(transport)
            engine.register_assault(
                force_ids=ids,
                final_target_cell=tpos,
                final_target_actor=tid,
                cohesion=(intent.approach != "charge"),  # charge sacrifices cohesion
            )
        except Exception:
            # Daemon registration is best-effort — never block the dispatch.
            pass

    if intent.approach == "frontal":
        target_kind = _target_kind(wv, tid)
        # When the target is a BUILDING, prefer attack_move to its location —
        # plain Attack-on-actor makes units ignore counter-fire from enemy mobile
        # units, leading to suicide rushes. attack_move auto-engages on the way.
        if tid is not None and not _is_building(target_kind):
            _send(transport, {"type": "attack", "unit_ids": ids, "target_id": tid}, actions)
            return _ok(f"frontal attack {len(ids)} unit(s) → actor {tid}", actions)
        # Building target OR no actor: attack-move to coords; units will engage
        # enemies en route and start firing at the building once in range.
        _send(transport,
              {"type": "set_stance", "unit_ids": ids, "stance": "AttackAnything"},
              actions)
        _send(transport,
              {"type": "move", "unit_ids": ids, "target": {"x": tpos[0], "y": tpos[1]},
               "attack_move": True},
              actions)
        return _ok(
            f"frontal attack-move {len(ids)} unit(s) → {tpos}"
            + (f" (building {target_kind})" if target_kind else ""),
            actions)

    if intent.approach in ("flank_left", "flank_right"):
        side = "left" if intent.approach == "flank_left" else "right"
        wp = G.flank_waypoint(force_center, tpos, side, sidestep_cells=12, approach_t=0.55)
        target_kind = _target_kind(wv, tid)
        # Move via flank waypoint first (attack-move so we engage en route).
        _send(transport,
              {"type": "move", "unit_ids": ids,
               "target": {"x": wp[0], "y": wp[1]}, "attack_move": True},
              actions)
        # Then engage target. Building → attack_move to its pos (keeps
        # AttackAnything responsiveness). Unit → direct Attack chases & fires.
        if tid is not None and not _is_building(target_kind):
            _send(transport, {"type": "attack", "unit_ids": ids, "target_id": tid}, actions)
        else:
            _send(transport,
                  {"type": "move", "unit_ids": ids,
                   "target": {"x": tpos[0], "y": tpos[1]}, "attack_move": True},
                  actions)
        return _ok(f"{intent.approach} via waypoint {wp} → target {tid or tpos}", actions)

    if intent.approach == "split":
        # Split ids in half: front goes frontal, rear goes flank_right.
        n = len(ids)
        a = ids[: n // 2]
        b = ids[n // 2:]
        if a:
            _send(transport,
                  {"type": "move", "unit_ids": a,
                   "target": {"x": tpos[0], "y": tpos[1]}, "attack_move": True},
                  actions)
        if b:
            wp = G.flank_waypoint(force_center, tpos, "right", sidestep_cells=14, approach_t=0.55)
            _send(transport,
                  {"type": "move", "unit_ids": b,
                   "target": {"x": wp[0], "y": wp[1]}, "attack_move": True},
                  actions)
        return _ok(f"split: {len(a)} frontal + {len(b)} flank_right → {tpos}", actions)

    if intent.approach == "charge":
        target_kind = _target_kind(wv, tid)
        _send(transport, {"type": "set_stance", "unit_ids": ids,
                          "stance": "AttackAnything"}, actions)
        # Building target → use attack_move so units engage enemy mobile units
        # along the way instead of dying focused-firing the building.
        # Mobile-unit target → safe to issue direct Attack (units pathfind to
        # the actor and shoot while moving).
        if tid is not None and not _is_building(target_kind):
            _send(transport, {"type": "attack", "unit_ids": ids, "target_id": tid}, actions)
        else:
            _send(transport,
                  {"type": "move", "unit_ids": ids,
                   "target": {"x": tpos[0], "y": tpos[1]}, "attack_move": True},
                  actions)
        return _ok(
            f"charge: {len(ids)} units, full aggression → {tid or tpos}"
            + (f" (building {target_kind})" if target_kind and _is_building(target_kind) else ""),
            actions)

    if intent.approach == "cautious":
        engage = G.cautious_engage_point(force_center, tpos, weapon_range_cells=6)
        _send(transport, {"type": "set_stance", "unit_ids": ids,
                          "stance": "ReturnFire"}, actions)
        _send(transport,
              {"type": "move", "unit_ids": ids,
               "target": {"x": engage[0], "y": engage[1]}, "attack_move": True},
              actions)
        return _ok(f"cautious engage at {engage} (kept distance)", actions)

    return _err(f"unknown approach: {intent.approach}", actions, "approach_unknown")


def _do_defend(intent: D.IntentDefend, wv: WorldView, transport) -> dict:
    actions: List[dict] = []
    ids = wv.resolve_force(intent.force)
    if not ids:
        return _err("force empty", actions, "force_resolution_empty")

    center, radius = wv.resolve_region(intent.region)
    _send(transport,
          {"type": "move", "unit_ids": ids,
           "target": {"x": center[0], "y": center[1]}, "attack_move": False},
          actions)
    _send(transport, {"type": "set_stance", "unit_ids": ids,
                      "stance": intent.stance}, actions)

    # Register a ContainmentMission with the daemon so the force auto-engages
    # intruders inside the region radius without leaving (and gets pulled back
    # if it strays). Best-effort — never block the dispatch.
    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_contain(
            force_ids=ids,
            chokepoint=center,
            radius=max(3, radius),
            stance=intent.stance,
        )
    except Exception:
        pass

    msg = (f"defend at {center} (r={radius}) with {len(ids)} unit(s), "
           f"stance={intent.stance}")
    if mission_id is not None:
        msg += f" [daemon contain #{mission_id}]"
    return _ok(msg, actions)


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
    actions: List[dict] = []
    left_ids = wv.resolve_force(intent.left)
    right_ids = wv.resolve_force(intent.right)
    if not left_ids and not right_ids:
        return _err("both arms empty", actions, "force_resolution_empty")

    tid, tpos = wv.resolve_target(intent.target)
    if tpos is None:
        return _err("target unresolved", actions, "target_resolution_failed")

    left_center = wv.force_centroid(left_ids) or tpos
    right_center = wv.force_centroid(right_ids) or tpos

    lwp, rwp = G.pincer_rendezvous(tpos, intent.rendezvous_dist,
                                    left_center, right_center)

    # Hand each arm to the tactical daemon as its own assault. Each arm
    # gets engage-on-contact + cohesion within itself; the final convergence
    # is implicit (both walk toward target after waypoint).
    try:
        engine = _get_tactical_engine(transport)
        if left_ids:
            engine.register_assault(force_ids=left_ids,
                                    final_target_cell=tpos,
                                    final_target_actor=tid,
                                    cohesion=True)
        if right_ids:
            engine.register_assault(force_ids=right_ids,
                                    final_target_cell=tpos,
                                    final_target_actor=tid,
                                    cohesion=True)
    except Exception:
        pass

    if left_ids:
        _send(transport,
              {"type": "move", "unit_ids": left_ids,
               "target": {"x": lwp[0], "y": lwp[1]}, "attack_move": True},
              actions)
    if right_ids:
        _send(transport,
              {"type": "move", "unit_ids": right_ids,
               "target": {"x": rwp[0], "y": rwp[1]}, "attack_move": True},
              actions)
    # follow-up: building → attack_move to pos (engage en route); unit → Attack actor.
    target_kind = _target_kind(wv, tid)
    use_attack_actor = tid is not None and not _is_building(target_kind)
    if left_ids:
        if use_attack_actor:
            _send(transport, {"type": "attack", "unit_ids": left_ids, "target_id": tid}, actions)
        else:
            _send(transport,
                  {"type": "move", "unit_ids": left_ids,
                   "target": {"x": tpos[0], "y": tpos[1]}, "attack_move": True},
                  actions)
    if right_ids:
        if use_attack_actor:
            _send(transport, {"type": "attack", "unit_ids": right_ids, "target_id": tid}, actions)
        else:
            _send(transport,
                  {"type": "move", "unit_ids": right_ids,
                   "target": {"x": tpos[0], "y": tpos[1]}, "attack_move": True},
                  actions)
    return _ok(
        f"pincer: left {len(left_ids)} → {lwp}, right {len(right_ids)} → {rwp}, "
        f"final target {tid or tpos}", actions)


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

    # Initial deploy — move to chokepoint with stance set.
    _send(transport, {"type": "set_stance", "unit_ids": ids,
                      "stance": intent.stance}, actions)
    _send(transport,
          {"type": "move", "unit_ids": ids,
           "target": {"x": cp[0], "y": cp[1]}, "attack_move": False},
          actions)

    mission_id = None
    try:
        engine = _get_tactical_engine(transport)
        mission_id = engine.register_contain(
            force_ids=ids,
            chokepoint=cp,
            radius=intent.radius,
            stance=intent.stance,
        )
    except Exception:
        pass

    msg = f"contain {len(ids)} unit(s) at {cp} (r={intent.radius})"
    if mission_id is not None:
        msg += f" [mission #{mission_id}]"
    return _ok(msg, actions, mission_id=mission_id)


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

    # Daemon owns the timing. Best-effort registration; if it fails we fall
    # back to one-shot orders below.
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
    except Exception:
        pass

    # Issue the opening orders so units start moving even before the daemon's
    # next tick.
    if feint_ids:
        _send(transport, {"type": "set_stance", "unit_ids": feint_ids,
                          "stance": "ReturnFire"}, actions)
        _send(transport,
              {"type": "move", "unit_ids": feint_ids,
               "target": {"x": feint_stop[0], "y": feint_stop[1]},
               "attack_move": False},
              actions)
    if raid_ids:
        first_target = raid_wp or raid_tpos
        _send(transport, {"type": "set_stance", "unit_ids": raid_ids,
                          "stance": "AttackAnything"}, actions)
        _send(transport,
              {"type": "move", "unit_ids": raid_ids,
               "target": {"x": first_target[0], "y": first_target[1]},
               "attack_move": True},
              actions)

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


