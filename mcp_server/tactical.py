"""
Tactical Engine — closes the LLM ↔ engine reaction-time gap.

The LLM dispatches at ~1 turn/sec at best. The OpenRA simulation runs at
25 tick/s. Between LLM turns we need a deterministic controller that:

1. Engages on contact (target dies → re-target next closest mobile threat
   instead of leaving the force idle).
2. Holds cohesion (vanguard waits for the rear so we don't trickle in).
3. Reacts to perimeter breaches (auto-defend when enemy enters the home
   radius, no LLM round-trip needed).

This file is the Python in-process daemon. A background thread polls the
TCP bridge every ~POLL_INTERVAL_S seconds and issues the right atomic
orders. The thread is started lazily on first `register_assault` /
`enable_auto_defense` call.

Why Python here instead of a C# trait? Speed of iteration. A C# port
(`AssaultManagerBotModule` + `PerimeterDefenseBotModule`) is the paper-
clean version and can replace this once the semantics are nailed down.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple, Set, Dict

from . import tactical_doctrine as DOCTRINE

# Set TACTICAL_DISABLED=1 in tests to keep register_assault / enable_auto_defense
# from spawning the background polling thread. The engine still records state so
# unit tests can inspect what would have happened.
_DISABLED = os.environ.get("TACTICAL_DISABLED") == "1"

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------

POLL_INTERVAL_S = 0.6        # how often the daemon scans the world
ENGAGE_RADIUS = 8            # cells — enemies within this from force centroid
                             #         are engaged immediately
COHESION_MAX_SPREAD = 9      # cells — stddev cap for force spread before
                             #         the vanguard is forced to halt
DEFENSE_PERIMETER_RADIUS = 18  # cells — default reflex defense radius
RETARGET_GRACE_TICKS = 2     # tolerate brief target-not-found before
                             #         picking next target

# Support-pairing knobs — medic auto-stick / mechanic auto-stick.
SUPPORT_PAIR_RADIUS = 10              # find friendly within this many cells
SUPPORT_STICK_DIST  = 2               # move within this of partner (don't re-issue)
SUPPORT_HEAL_HP_BELOW = 0.7           # only pair to wounded
SUPPORT_POLL_INTERVAL_S = 1.0         # re-evaluate every 1s (throttle on engine tick)

# Pending-mission knobs.
PENDING_RECHECK_S = 3.0               # re-attempt resolution every 3s

# Building / non-combat kinds — copied from interpreter to stay independent.
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

# Support-pairing kind classifications. medi heals infantry, mech repairs
# vehicles (mirrors the OpenRA RA mod's Medic / Mechanic roles).
_MEDIC_KIND     = "medi"
_MECHANIC_KIND  = "mech"
_INFANTRY_KINDS = frozenset({
    "e1", "e2", "e3", "e4", "e6", "e7",
    "spy", "thf", "vlkv", "chan", "dog",
})
_VEHICLE_KINDS = frozenset({
    "1tnk", "2tnk", "3tnk", "4tnk", "jeep", "apc", "v2rl", "arty",
    "ttnk", "ftrk", "mrj", "mgg", "dtrk", "qtnk", "mnly", "harv",
})


def _is_building(kind: str) -> bool:
    return (kind or "").lower() in _BUILDING_KINDS


def _is_combat_mobile(kind: str) -> bool:
    k = (kind or "").lower()
    return k not in _BUILDING_KINDS and k not in _NON_COMBAT_MOBILE_KINDS


def _dist2(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


# ---------------------------------------------------------------------------
# Support Pairing Daemon — medic auto-stick to wounded infantry,
# mechanic auto-stick to wounded vehicles. Always on (like harv auto-mining).
# ---------------------------------------------------------------------------

class SupportPairingDaemon:
    """Stateless per-tick auto-pairing.

    Each tick, scans self_units. For every idle medic, finds the nearest
    wounded friendly infantry within SUPPORT_PAIR_RADIUS and issues a move
    toward it (unless already within SUPPORT_STICK_DIST). For every idle
    mechanic, same for vehicles.

    "Idle" = not currently reserved by any active Mission's force_ids. The
    engine passes the busy-id set in each call so support stays out of the
    way of combat orders.

    Throttled per partner pair: we cache the last (medic_id, partner_id)
    issued and don't re-issue if both still hold (prevents flooding the
    transport with redundant move orders every tick).
    """

    def __init__(self, transport):
        self.transport = transport
        # last_pair[supporter_id] = (partner_id, last_dispatch_ts)
        self.last_pair: Dict[int, Tuple[int, float]] = {}
        self._last_tick_ts: float = 0.0
        # Diagnostic counters surfaced via TacticalEngine.status().
        self.pair_dispatches = 0

    def tick(self, self_units: List[dict], busy_ids: Set[int]) -> None:
        now = time.time()
        if now - self._last_tick_ts < SUPPORT_POLL_INTERVAL_S:
            return
        self._last_tick_ts = now

        # Bucket units by kind so we don't re-scan the list 4x.
        medics: List[dict] = []
        mechanics: List[dict] = []
        wounded_inf: List[dict] = []
        wounded_veh: List[dict] = []

        for u in self_units:
            uid = u.get("id")
            if uid is None:
                continue
            kind = (u.get("kind") or "").lower()
            hp = float(u.get("hp_pct", 1.0))

            if uid in busy_ids:
                # Even busy units may be wounded — they remain valid pair
                # targets (medic can chase a tank-escort infantry). Only
                # the supporter itself must be idle.
                if kind in _INFANTRY_KINDS and hp < SUPPORT_HEAL_HP_BELOW:
                    wounded_inf.append(u)
                elif kind in _VEHICLE_KINDS and hp < SUPPORT_HEAL_HP_BELOW:
                    wounded_veh.append(u)
                continue

            if kind == _MEDIC_KIND:
                medics.append(u)
            elif kind == _MECHANIC_KIND:
                mechanics.append(u)
            elif kind in _INFANTRY_KINDS and hp < SUPPORT_HEAL_HP_BELOW:
                wounded_inf.append(u)
            elif kind in _VEHICLE_KINDS and hp < SUPPORT_HEAL_HP_BELOW:
                wounded_veh.append(u)

        # Forget supporters that died or moved into busy state.
        live_supporters = {u["id"] for u in medics} | {u["id"] for u in mechanics}
        for sid in list(self.last_pair.keys()):
            if sid not in live_supporters:
                self.last_pair.pop(sid, None)

        for medic in medics:
            self._pair_one(medic, wounded_inf, now)
        for mech in mechanics:
            self._pair_one(mech, wounded_veh, now)

    def _pair_one(self, supporter: dict, pool: List[dict], now: float) -> None:
        if not pool:
            return
        spos = (supporter["pos"]["x"], supporter["pos"]["y"])
        sid = supporter["id"]
        r2 = SUPPORT_PAIR_RADIUS * SUPPORT_PAIR_RADIUS

        # Nearest wounded partner within radius.
        best = None
        best_d = r2 + 1
        for u in pool:
            ppos = (u["pos"]["x"], u["pos"]["y"])
            d = _dist2(spos, ppos)
            if d <= r2 and d < best_d:
                best = u
                best_d = d
        if best is None:
            return

        # Already on station? Stick distance check.
        stick_d2 = SUPPORT_STICK_DIST * SUPPORT_STICK_DIST
        if best_d <= stick_d2:
            return

        prev = self.last_pair.get(sid)
        # Skip re-issue if same partner & we issued within the last 2s
        # (avoids piling up move orders while pathing).
        if prev is not None and prev[0] == best["id"] and (now - prev[1]) < 2.0:
            return

        ppos = (best["pos"]["x"], best["pos"]["y"])
        self.transport.send_command({
            "type": "move",
            "unit_ids": [sid],
            "target": {"x": ppos[0], "y": ppos[1]},
            "attack_move": False,
        })
        self.last_pair[sid] = (best["id"], now)
        self.pair_dispatches += 1


# ---------------------------------------------------------------------------
# Alert State + Mission Objective enums
# ---------------------------------------------------------------------------
#
# Alert State packages (perimeter mode + daemon thresholds + default stance +
# default approach + auto-dispatched mission set). It's the "global posture"
# control — one player utterance flips many knobs.
#
# Mission Objective is ORTHOGONAL — it's the declared victory condition.
# Objective only suggests which alert state suits it; the player still picks.
# ---------------------------------------------------------------------------


class AlertState(str, Enum):
    PEACE    = "peace"
    WATCH    = "watch"
    ALERT    = "alert"
    COMBAT   = "combat"
    LOCKDOWN = "lockdown"


class Objective(str, Enum):
    DESTROY_FACT       = "destroy_fact"
    DESTROY_ENEMY      = "destroy_enemy"     # total-war: cycle assault, dynamic recruit
    HARASS_ECONOMY     = "harass_economy"
    SURVIVE_UNTIL_TICK = "survive_until_tick"
    CONTROL_MAP_CENTER = "control_map_center"


# Per-state config. `auto_missions` is a list of mission specs the engine
# auto-dispatches when the state is applied. Each spec is resolved at apply
# time (force filter, target region) and tagged `auto=True` so the next state
# swap can cancel it without touching manual LLM-dispatched missions.
ALERT_STATE_CONFIG: Dict[AlertState, dict] = {
    AlertState.PEACE: {
        "perimeter": "off",                  # off | on | aggressive
        "retreat_hp_threshold": 0.3,
        "cohesion_max_spread": 9,
        "default_stance": "ReturnFire",
        "default_approach": "frontal",
        "auto_missions": [],
        "force_recall_all": False,
    },
    AlertState.WATCH: {
        "perimeter": "on",
        "retreat_hp_threshold": 0.3,
        "cohesion_max_spread": 10,
        "default_stance": "Defend",
        "default_approach": "cautious",
        "auto_missions": [
            {"kind": "patrol",
             "force": {"harass_capable": True},
             "auto_waypoints": "map_perimeter",
             "max_force": 2},
        ],
        "force_recall_all": False,
    },
    AlertState.ALERT: {
        "perimeter": "aggressive",
        "retreat_hp_threshold": 0.5,
        "cohesion_max_spread": 9,
        "default_stance": "Defend",
        "default_approach": "cautious",
        "auto_missions": [
            {"kind": "patrol",
             "force": {"harass_capable": True},
             "auto_waypoints": "map_perimeter",
             "max_force": 2},
            {"kind": "harass",
             "force": {"harass_capable": True},
             "target_region": "enemy_economy",
             "max_force": 4},
        ],
        "force_recall_all": False,
    },
    AlertState.COMBAT: {
        "perimeter": "on",
        "retreat_hp_threshold": 0.25,
        "cohesion_max_spread": 8,
        "default_stance": "AttackAnything",
        "default_approach": "charge",
        "auto_missions": [],
        "force_recall_all": False,
    },
    AlertState.LOCKDOWN: {
        "perimeter": "aggressive",
        "retreat_hp_threshold": 0.7,
        "cohesion_max_spread": 7,
        "default_stance": "Defend",
        "default_approach": "cautious",
        "auto_missions": [],
        "force_recall_all": True,
    },
}


# Objective-owned auto-mission specs. Same shape as ALERT_STATE_CONFIG
# auto_missions entries — _dispatch_auto_mission handles them identically.
# When the objective owns the mission, ids land in objective_mission_ids
# (not auto_mission_ids), so alert-state swaps don't kill objective work.
#
# DESTROY_FACT: no auto-mission — player drives attack/pincer manually.
# SURVIVE_UNTIL_TICK: no auto-mission — players combine with alert lockdown.
# CONTROL_MAP_CENTER: TODO (needs contain auto-spec + map_center resolution).
# HARASS_ECONOMY: cycle harass on enemy economy with dynamic harass_capable.
_OBJECTIVE_MISSIONS: Dict[Objective, List[dict]] = {
    Objective.DESTROY_FACT: [],
    Objective.DESTROY_ENEMY: [
        # Cycle assault that recruits any combat-mobile unit and chases
        # enemy_fact, falling back to nearest structure when none remain.
        # max_force=None (unlimited) so the whole army rolls forward as the
        # player produces.
        {"kind": "attack",
         "force": {"combat_mobile": True},
         "target_named": "enemy_fact",
         "max_force": None},
    ],
    Objective.HARASS_ECONOMY: [
        {"kind": "harass",
         "force": {"harass_capable": True},
         "target_region": "enemy_economy",
         "max_force": 4},
    ],
    Objective.SURVIVE_UNTIL_TICK: [],
    Objective.CONTROL_MAP_CENTER: [],  # TODO: contain @ map_center
}


_OBJECTIVE_TO_SUGGESTED_STATE: Dict[Objective, AlertState] = {
    Objective.DESTROY_FACT:       AlertState.COMBAT,
    Objective.DESTROY_ENEMY:      AlertState.COMBAT,
    Objective.HARASS_ECONOMY:     AlertState.ALERT,
    Objective.SURVIVE_UNTIL_TICK: AlertState.LOCKDOWN,
    Objective.CONTROL_MAP_CENTER: AlertState.WATCH,
}


def objective_to_suggested_state(obj: Objective) -> AlertState:
    return _OBJECTIVE_TO_SUGGESTED_STATE.get(obj, AlertState.WATCH)


# ---------------------------------------------------------------------------
# Mission state
# ---------------------------------------------------------------------------

@dataclass
class Assault:
    """One active offensive mission shepherded by the daemon.

    Static mode (default): force_ids fixed at registration. Mission ends when
    all units die or target dies (retarget picks nearest enemy, falls flat
    when none remain).

    Dynamic mode: force_spec set + target_named set → daemon recruits newly-
    trained matching units every tick (_resolve_dynamic_forces), and when
    the current target dies, re-resolves the named target via WorldView
    (e.g. enemy_fact → finds next enemy fact, or nearest_enemy_structure).
    Used by destroy_enemy objective to keep the army pushing as players
    produce more units.
    """
    mission_id: int
    force_ids: Set[int]
    final_target_cell: Tuple[int, int]
    final_target_actor: Optional[int]
    cohesion: bool = True
    # True iff the alert-state machine dispatched this mission. Manual missions
    # (LLM-dispatched) are auto=False and survive state swaps.
    auto: bool = False
    # Dynamic recruitment: when set, _resolve_dynamic_forces pulls matching
    # newly-trained units into force_ids every tick. Static when None.
    force_spec: Optional[dict] = None
    # When set, daemon retargets via named lookup on target death (replaces
    # 'pick nearest enemy' behavior that bleeds intent to whatever is close).
    target_named: Optional[str] = None
    # Throttle for dynamic force resolve (mirrors HarassMission pattern).
    last_resolve_ts: float = 0.0
    # Cap for dynamic recruitment (None = unlimited).
    max_force_size: Optional[int] = None
    # runtime
    current_target_actor: Optional[int] = None
    last_seen_target_alive_at: float = 0.0
    halted_units: Set[int] = field(default_factory=set)  # for cohesion gate
    finished: bool = False
    # retreat state: unit_id → ts when it can re-engage (or 0 if still retreating)
    retreating: Dict[int, float] = field(default_factory=dict)
    # cached self_base for retreat target (lazy on first need)
    self_base_cache: Optional[Tuple[int, int]] = None
    # After-action tracking — set by engine at registration time.
    started_at_ts: float = 0.0
    initial_force_count: int = 0
    initial_enemy_near_target: int = 0
    # Termination outcome filled by _emit_after_action.
    end_outcome: Optional[str] = None


@dataclass
class DefenseZone:
    """A perimeter we auto-react to when enemies enter.

    Multi-perimeter capable: every DefenseZone has its own zone_id; the
    daemon can hold N concurrent zones, each with independent cooldown.
    """
    zone_id: int
    center: Tuple[int, int]
    radius: int = DEFENSE_PERIMETER_RADIUS
    last_dispatch_ts: float = 0.0
    cooldown_s: float = 8.0


@dataclass
class HarassMission:
    """Hit-and-run cycle on an enemy region.

    State machine: engaging → withdrawing → regrouping → engaging.
    - engaging: focus-fire highest-priority enemy in target_region
    - withdrawing: any unit hp < withdraw_hp_threshold → whole force retreats
                   to withdraw_to (HoldFire). Sets stance back to ReturnFire
                   when arriving.
    - regrouping: at safe distance + avg hp >= reengage_hp_threshold → next
                  cycle. If cycle == False, mission ends.
    Target priority within region: harv > proc > inf > tnk (DOCTRINE).
    Re-resolves target every tick if current dies.
    """
    mission_id: int
    force_ids: Set[int]
    # Region (cached as center + radius for fast in-tick filtering)
    region_center: Tuple[int, int]
    region_radius: int
    withdraw_hp_threshold: float = 0.6
    reengage_hp_threshold: float = 0.85
    withdraw_to: Tuple[int, int] = (0, 0)  # resolved at register time
    cycle: bool = True
    max_force_size: Optional[int] = None
    auto: bool = False                     # set by alert-state dispatch
    # Dynamic force resolution: when set, daemon re-resolves this spec each
    # tick and adds new matching units (without exceeding max_force_size).
    # When None, force is static (legacy behavior).
    force_spec: Optional[dict] = None
    # runtime
    state: str = "engaging"                # engaging | withdrawing | regrouping
    current_target_actor: Optional[int] = None
    last_dispatch_ts: float = 0.0
    last_resolve_ts: float = 0.0
    finished: bool = False
    # After-action accounting.
    started_at_ts: float = 0.0
    initial_force_count: int = 0
    initial_enemy_near_target: int = 0
    recruited_count: int = 0               # lifetime new ids absorbed
    end_outcome: Optional[str] = None


@dataclass
class PatrolMission:
    """Waypoint cycle for vision.

    Force walks waypoints in order, looping at end if cycle == True. Engages
    contact targets per `contact_stance` but does NOT chase off-route. Units
    that drop below 0.4 HP break off to self_base (handled by `withdraw_to`,
    resolved at register time).
    """
    mission_id: int
    force_ids: Set[int]
    waypoints: List[Tuple[int, int]]
    cycle: bool = True
    contact_stance: str = "ReturnFire"
    withdraw_to: Tuple[int, int] = (0, 0)
    low_hp_threshold: float = 0.4
    auto: bool = False                     # set by alert-state dispatch
    max_force_size: Optional[int] = None
    force_spec: Optional[dict] = None      # dynamic force re-resolution
    # runtime
    next_wp_idx: int = 0
    last_dispatch_ts: float = 0.0
    last_arrived_wp_idx: int = -1
    last_resolve_ts: float = 0.0
    finished: bool = False
    # After-action.
    started_at_ts: float = 0.0
    initial_force_count: int = 0
    recruited_count: int = 0
    end_outcome: Optional[str] = None


@dataclass
class EscortMission:
    """Bodyguard a designated unit.

    The force stays within `escort_radius` of escortee, engages threats
    within `engage_radius`. If escortee dies, mission ends.
    """
    mission_id: int
    force_ids: Set[int]
    escortee_id: int
    destination: Optional[Tuple[int, int]] = None
    escort_radius: int = 4
    engage_radius: int = 6
    auto: bool = False                     # set by alert-state dispatch
    max_force_size: Optional[int] = None
    # Half-dynamic: bodyguard force_spec re-resolves (filter recruits new
    # bodyguards if they match), but escortee remains fixed.
    force_spec: Optional[dict] = None
    # runtime
    last_dispatch_ts: float = 0.0
    last_resolve_ts: float = 0.0
    finished: bool = False
    # After-action.
    started_at_ts: float = 0.0
    initial_force_count: int = 0
    recruited_count: int = 0
    end_outcome: Optional[str] = None


@dataclass
class ContainmentMission:
    """Chokepoint denial.

    Force holds a single cell; engages anything inside `radius`. Does NOT
    pursue beyond radius — units that strayed (e.g. mid-engagement) are
    pulled back. Uses `stance` (default Defend) for the force.
    """
    mission_id: int
    force_ids: Set[int]
    chokepoint: Tuple[int, int]
    radius: int = 4
    stance: str = "Defend"
    auto: bool = False                     # set by alert-state dispatch
    # runtime
    current_target_actor: Optional[int] = None
    last_dispatch_ts: float = 0.0
    finished: bool = False
    # After-action.
    started_at_ts: float = 0.0
    initial_force_count: int = 0
    initial_enemy_near_target: int = 0
    end_outcome: Optional[str] = None


@dataclass
class PendingMission:
    """A mission that couldn't dispatch because force resolution returned empty.

    The engine re-attempts force resolution every ~PENDING_POLL_S seconds. Once
    the original `intent_payload` resolves to at least one unit, the mission
    is dispatched for real (via the interpreter) and the pending entry is
    removed.

    Use case: player says "骚扰" while having zero harass-capable units.
    Instead of erroring out, the mission queues; once the player trains a
    jeep / dog / e3 / etc., the daemon starts the harass automatically and
    pushes an event so the LLM can tell the player "骚扰队已启程".
    """
    pending_id: int
    intent_kind: str      # "harass", "patrol", "escort", "contain", "diversion"
    intent_payload: dict  # original IntentXxx model_dump(), re-fed to interpreter
    queued_at_tick: int
    queued_at_ts: float
    reason: str           # e.g. "no harass_capable units available"
    last_check_ts: float = 0.0
    # Tracks which layer queued this pending so swap (alert state / objective)
    # can clean up only what it owns. "manual" = LLM dispatch_intent direct;
    # "alert" / "objective" = auto-dispatched but force was empty.
    owner: str = "manual"


@dataclass
class DiversionMission:
    """Two synchronized prongs — feint + raid.

    Timing:
      1. Both deploy simultaneously.
      2. feint advances to `feint_stopline` (8 cells short of feint_target);
         holds at ReturnFire.
      3. raid arrives at raid_target via flank waypoint and focus-fires.
      4. Once raid is engaged (saw any contact within engage radius), feint
         may upgrade to AttackAnything if `feint_commits == True`.
      5. Any prong below 40% average HP → that prong withdraws (HoldFire +
         move to withdraw_to). Mission ends when both prongs withdrawn or
         raid_target destroyed.
    """
    mission_id: int
    feint_force_ids: Set[int]
    feint_target_cell: Tuple[int, int]
    raid_force_ids: Set[int]
    raid_target_cell: Tuple[int, int]
    raid_target_actor: Optional[int] = None
    raid_waypoint: Optional[Tuple[int, int]] = None  # flank waypoint for raid
    feint_commits: bool = False
    withdraw_to: Tuple[int, int] = (0, 0)
    auto: bool = False                     # set by alert-state dispatch
    # runtime
    raid_engaged: bool = False
    feint_withdrew: bool = False
    raid_withdrew: bool = False
    last_dispatch_ts: float = 0.0
    finished: bool = False
    # After-action.
    started_at_ts: float = 0.0
    initial_feint_count: int = 0
    initial_raid_count: int = 0
    initial_enemy_near_target: int = 0
    end_outcome: Optional[str] = None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TacticalEngine:
    """Singleton — owns one polling thread shared by all missions."""

    def __init__(self, transport):
        self.transport = transport
        self._lock = threading.Lock()
        self._assaults: Dict[int, Assault] = {}
        # Multi-perimeter: list of DefenseZone, each with its own zone_id.
        self._perimeters: Dict[int, DefenseZone] = {}
        # Auto-dispatched perimeter zones (from alert state). Tracked
        # separately so a state swap can disarm only the auto zones and leave
        # manual `enable_auto_defense(...)` zones alone.
        self._auto_perimeter_zone_ids: Set[int] = set()
        # Long-running mission registries (one per type, keyed by mission_id).
        self._harass: Dict[int, HarassMission] = {}
        self._patrol: Dict[int, PatrolMission] = {}
        self._escort: Dict[int, EscortMission] = {}
        self._contain: Dict[int, ContainmentMission] = {}
        self._diversion: Dict[int, DiversionMission] = {}
        # Pending-mission queue: missions whose force resolution returned
        # empty get queued instead of erroring out. Re-attempted every
        # PENDING_RECHECK_S; dispatched once force resolves.
        self._pending: Dict[int, PendingMission] = {}
        self._next_pending_id = 1
        self._next_id = 1
        self._next_zone_id = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Support pairing daemon — always-on auto-stick for medic/mechanic.
        self._support = SupportPairingDaemon(transport)
        # Alert-state engine state.
        self.current_alert_state: AlertState = AlertState.PEACE
        # Defaults seeded from PEACE; updated when apply_alert_state runs.
        self.default_stance: str = ALERT_STATE_CONFIG[AlertState.PEACE]["default_stance"]
        self.default_approach: str = ALERT_STATE_CONFIG[AlertState.PEACE]["default_approach"]
        # Mission IDs auto-dispatched by the current alert state. Cleared on swap.
        self.auto_mission_ids: List[int] = []
        # Objective storage.
        self.current_objective: Optional[Objective] = None
        self.objective_params: dict = {}
        # Mission IDs auto-dispatched by the current objective. Cleared on
        # objective swap. Separate from auto_mission_ids so alert-state and
        # objective lifecycles don't tangle.
        self.objective_mission_ids: List[int] = []
        # Auto-escalation throttle.
        self._last_escalation_alert_ts: float = 0.0
        # Diagnostic counters surfaced via status().
        self.tick_count = 0
        self.retargets = 0
        self.cohesion_halts = 0
        self.defense_dispatches = 0
        self.pending_dispatches = 0
        self.after_action_emits = 0
        self.last_error: Optional[str] = None

    # --- public surface ------------------------------------------------

    def register_assault(
        self,
        force_ids: List[int],
        final_target_cell: Tuple[int, int],
        final_target_actor: Optional[int] = None,
        cohesion: bool = True,
        force_spec: Optional[dict] = None,
        target_named: Optional[str] = None,
        max_force_size: Optional[int] = None,
    ) -> int:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._assaults[mid] = Assault(
                mission_id=mid,
                force_ids=set(force_ids),
                final_target_cell=final_target_cell,
                final_target_actor=final_target_actor,
                cohesion=cohesion,
                force_spec=force_spec,
                target_named=target_named,
                max_force_size=max_force_size,
                current_target_actor=final_target_actor,
                started_at_ts=time.time(),
                initial_force_count=len(force_ids),
                initial_enemy_near_target=self._count_enemies_near(
                    final_target_cell, radius=10),
            )
        self._ensure_thread()
        return mid

    def cancel_assault(self, mission_id: int) -> bool:
        """Cancel one mission by id — searches assault + harass + patrol +
        escort + contain + diversion registries. Returns True if found.
        Emits an after-action event tagged outcome="cancelled"."""
        removed_mission = None
        with self._lock:
            for reg in (self._assaults, self._harass, self._patrol,
                        self._escort, self._contain, self._diversion):
                m = reg.pop(mission_id, None)
                if m is not None:
                    removed_mission = m
                    break
        if removed_mission is not None:
            try:
                self._emit_after_action(removed_mission, outcome="cancelled")
            except Exception:
                pass
            return True
        return False

    def cancel_all_assaults(self) -> int:
        """Cancel ALL daemon-tracked missions (assaults + long-running).
        Perimeter defense is NOT cleared — use disable_auto_defense for that.
        Each cancelled mission emits an after-action event."""
        with self._lock:
            removed: List[Any] = []
            for reg in (self._assaults, self._harass, self._patrol,
                        self._escort, self._contain, self._diversion):
                removed.extend(reg.values())
                reg.clear()
        for m in removed:
            try:
                self._emit_after_action(m, outcome="cancelled")
            except Exception:
                pass
        return len(removed)

    def enable_auto_defense(self, center: Tuple[int, int],
                            radius: int = DEFENSE_PERIMETER_RADIUS) -> int:
        """Add a new perimeter. Returns the zone_id of the new perimeter.

        Multiple concurrent perimeters are supported — each call ADDS a zone,
        does NOT replace the previous one. Use disable_auto_defense(zone_id)
        to remove a specific zone, or disable_auto_defense() to remove all.
        """
        with self._lock:
            zid = self._next_zone_id
            self._next_zone_id += 1
            self._perimeters[zid] = DefenseZone(
                zone_id=zid, center=center, radius=radius
            )
        self._ensure_thread()
        return zid

    def disable_auto_defense(self, zone_id: Optional[int] = None) -> int:
        """Disable one (zone_id given) or all (zone_id is None) perimeters.

        Returns the count of removed zones.
        """
        with self._lock:
            if zone_id is None:
                n = len(self._perimeters)
                self._perimeters.clear()
                return n
            return 1 if self._perimeters.pop(zone_id, None) is not None else 0

    def list_perimeters(self) -> List[dict]:
        with self._lock:
            return [
                {"zone_id": z.zone_id,
                 "center": list(z.center),
                 "radius": z.radius}
                for z in self._perimeters.values()
            ]

    # --- long-running mission registration ----------------------------

    def register_harass(self, force_ids: List[int],
                        region_center: Tuple[int, int],
                        region_radius: int,
                        withdraw_to: Tuple[int, int],
                        withdraw_hp_threshold: float = 0.6,
                        reengage_hp_threshold: float = 0.85,
                        cycle: bool = True,
                        max_force_size: Optional[int] = None,
                        force_spec: Optional[dict] = None) -> int:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._harass[mid] = HarassMission(
                mission_id=mid,
                force_ids=set(force_ids),
                region_center=region_center,
                region_radius=region_radius,
                withdraw_to=withdraw_to,
                withdraw_hp_threshold=withdraw_hp_threshold,
                reengage_hp_threshold=reengage_hp_threshold,
                cycle=cycle,
                max_force_size=max_force_size,
                force_spec=force_spec,
                started_at_ts=time.time(),
                initial_force_count=len(force_ids),
                initial_enemy_near_target=self._count_enemies_near(
                    region_center, radius=region_radius),
            )
        self._ensure_thread()
        return mid

    def register_patrol(self, force_ids: List[int],
                        waypoints: List[Tuple[int, int]],
                        withdraw_to: Tuple[int, int],
                        cycle: bool = True,
                        contact_stance: str = "ReturnFire",
                        low_hp_threshold: float = 0.4,
                        max_force_size: Optional[int] = None,
                        force_spec: Optional[dict] = None) -> int:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._patrol[mid] = PatrolMission(
                mission_id=mid,
                force_ids=set(force_ids),
                waypoints=list(waypoints),
                withdraw_to=withdraw_to,
                cycle=cycle,
                contact_stance=contact_stance,
                low_hp_threshold=low_hp_threshold,
                max_force_size=max_force_size,
                force_spec=force_spec,
                started_at_ts=time.time(),
                initial_force_count=len(force_ids),
            )
        self._ensure_thread()
        return mid

    def register_escort(self, force_ids: List[int], escortee_id: int,
                        destination: Optional[Tuple[int, int]] = None,
                        escort_radius: int = 4,
                        engage_radius: int = 6,
                        max_force_size: Optional[int] = None,
                        force_spec: Optional[dict] = None) -> int:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._escort[mid] = EscortMission(
                mission_id=mid,
                force_ids=set(force_ids),
                escortee_id=escortee_id,
                destination=destination,
                escort_radius=escort_radius,
                engage_radius=engage_radius,
                max_force_size=max_force_size,
                force_spec=force_spec,
                started_at_ts=time.time(),
                initial_force_count=len(force_ids),
            )
        self._ensure_thread()
        return mid

    def register_contain(self, force_ids: List[int],
                         chokepoint: Tuple[int, int],
                         radius: int = 4,
                         stance: str = "Defend") -> int:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._contain[mid] = ContainmentMission(
                mission_id=mid,
                force_ids=set(force_ids),
                chokepoint=chokepoint,
                radius=radius,
                stance=stance,
                started_at_ts=time.time(),
                initial_force_count=len(force_ids),
                initial_enemy_near_target=self._count_enemies_near(
                    chokepoint, radius=radius),
            )
        self._ensure_thread()
        return mid

    def register_diversion(self,
                           feint_force_ids: List[int],
                           feint_target_cell: Tuple[int, int],
                           raid_force_ids: List[int],
                           raid_target_cell: Tuple[int, int],
                           raid_target_actor: Optional[int],
                           raid_waypoint: Optional[Tuple[int, int]],
                           withdraw_to: Tuple[int, int],
                           feint_commits: bool = False) -> int:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            self._diversion[mid] = DiversionMission(
                mission_id=mid,
                feint_force_ids=set(feint_force_ids),
                feint_target_cell=feint_target_cell,
                raid_force_ids=set(raid_force_ids),
                raid_target_cell=raid_target_cell,
                raid_target_actor=raid_target_actor,
                raid_waypoint=raid_waypoint,
                withdraw_to=withdraw_to,
                feint_commits=feint_commits,
                started_at_ts=time.time(),
                initial_feint_count=len(feint_force_ids),
                initial_raid_count=len(raid_force_ids),
                initial_enemy_near_target=self._count_enemies_near(
                    raid_target_cell, radius=8),
            )
        self._ensure_thread()
        return mid

    # --- pending queue ------------------------------------------------

    def queue_pending(self, intent_kind: str, intent_payload: dict,
                      reason: str, owner: str = "manual") -> int:
        """Queue a mission that couldn't dispatch (force empty etc.).

        owner labels which layer queued it ("manual" / "alert" / "objective")
        so a doctrine swap can prune just its own waiting entries.

        Returns the pending_id. The daemon re-attempts force resolution
        every PENDING_RECHECK_S; once it returns a non-empty force, the
        original intent_payload is re-fed to the interpreter and the
        pending entry is removed.
        """
        with self._lock:
            pid = self._next_pending_id
            self._next_pending_id += 1
            # Tick may be unknowable when offline; best-effort.
            tick = 0
            try:
                world = self._snapshot_world()
                if world is not None:
                    tick = int(world.get("tick", 0))
            except Exception:
                pass
            self._pending[pid] = PendingMission(
                pending_id=pid,
                intent_kind=intent_kind,
                intent_payload=intent_payload,
                queued_at_tick=tick,
                queued_at_ts=time.time(),
                reason=reason,
                owner=owner,
            )
        self._ensure_thread()
        return pid

    def cancel_pending(self, pending_id: int) -> bool:
        with self._lock:
            return self._pending.pop(pending_id, None) is not None

    def list_pending(self) -> List[dict]:
        with self._lock:
            return [
                {"pending_id": p.pending_id,
                 "intent_kind": p.intent_kind,
                 "intent_payload": p.intent_payload,
                 "queued_at_tick": p.queued_at_tick,
                 "queued_at_ts": p.queued_at_ts,
                 "age_s": int(time.time() - p.queued_at_ts),
                 "reason": p.reason}
                for p in self._pending.values()
            ]

    def _count_enemies_near(self, center: Tuple[int, int],
                            radius: int = 10) -> int:
        """Best-effort: count enemy mobile units within `radius` of `center`.
        Used for after-action kill estimation baselines. Returns 0 if the
        bridge isn't reachable — we just degrade accuracy, never block."""
        try:
            world = self._snapshot_world()
            if world is None:
                return 0
            r2 = radius * radius
            n = 0
            for u in world.get("enemy_units", []):
                if _is_building(u.get("kind", "")):
                    continue
                p = (u["pos"]["x"], u["pos"]["y"])
                if _dist2(p, center) <= r2:
                    n += 1
            return n
        except Exception:
            return 0

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "active_assaults": len(self._assaults),
                "active_harass": len(self._harass),
                "active_patrol": len(self._patrol),
                "active_escort": len(self._escort),
                "active_contain": len(self._contain),
                "active_diversion": len(self._diversion),
                "pending_missions": len(self._pending),
                "auto_defense_on": len(self._perimeters) > 0,
                "perimeters": [
                    {"zone_id": z.zone_id,
                     "center": list(z.center),
                     "radius": z.radius,
                     "auto": z.zone_id in self._auto_perimeter_zone_ids}
                    for z in self._perimeters.values()
                ],
                "alert_state": self.current_alert_state.value,
                "default_stance": self.default_stance,
                "default_approach": self.default_approach,
                "auto_mission_ids": list(self.auto_mission_ids),
                "objective_mission_ids": list(self.objective_mission_ids),
                "objective": (self.current_objective.value
                              if self.current_objective else None),
                "objective_params": dict(self.objective_params),
                "tick_count": self.tick_count,
                "retargets": self.retargets,
                "cohesion_halts": self.cohesion_halts,
                "defense_dispatches": self.defense_dispatches,
                "pending_dispatches": self.pending_dispatches,
                "support_pair_dispatches": self._support.pair_dispatches,
                "after_action_emits": self.after_action_emits,
                "last_error": self.last_error,
            }

    # --- alert state orchestration ------------------------------------

    def apply_alert_state(self, state: AlertState) -> dict:
        """Switch the army's posture wholesale.

        1. Cancel previous auto-dispatched missions (tracked via auto_mission_ids).
           Manual LLM-dispatched missions survive — they're auto=False.
        2. Tear down previous auto perimeters; reinstall per new state.
        3. Update default stance + approach + daemon retreat / cohesion knobs.
        4. Dispatch the new state's auto_missions (force resolved at dispatch
           time, tagged auto=True, ids stored in auto_mission_ids).
        5. If force_recall_all flag is set, issue HoldFire + move_to_base for
           all combat-mobile self units.

        Returns dict describing what changed.
        """
        prev_state = self.current_alert_state
        cfg = ALERT_STATE_CONFIG[state]

        # --- 1. Cancel previous auto missions (manual ones stay). ---
        cancelled_ids: List[int] = []
        cancelled_pending_ids: List[int] = []
        with self._lock:
            prev_auto_ids = list(self.auto_mission_ids)
            for mid in prev_auto_ids:
                for reg in (self._assaults, self._harass, self._patrol,
                            self._escort, self._contain, self._diversion):
                    if reg.pop(mid, None) is not None:
                        cancelled_ids.append(mid)
                        break
            self.auto_mission_ids.clear()

            # Also prune pending entries owned by the alert layer — otherwise
            # a state swap leaves stale auto_patrol / auto_harass in queue
            # that would later trigger for the previous state's intent.
            for pid in list(self._pending.keys()):
                if self._pending[pid].owner == "alert":
                    self._pending.pop(pid, None)
                    cancelled_pending_ids.append(pid)

            # --- 2a. Tear down previous auto perimeters. ---
            removed_perimeters: List[int] = []
            for zid in list(self._auto_perimeter_zone_ids):
                if self._perimeters.pop(zid, None) is not None:
                    removed_perimeters.append(zid)
            self._auto_perimeter_zone_ids.clear()

        # Pull world snapshot once for force / target resolution below.
        world = self._snapshot_world()

        # --- 2b. Install new perimeter(s) per perimeter mode. ---
        installed_perimeters: List[int] = []
        perim_mode = cfg["perimeter"]
        base = self._resolve_self_base_from_snapshot(world)
        if perim_mode != "off" and base is not None:
            # "on" = radius 18 (default), "aggressive" = radius 24.
            radius = 24 if perim_mode == "aggressive" else DEFENSE_PERIMETER_RADIUS
            zid = self.enable_auto_defense(base, radius=radius)
            with self._lock:
                self._auto_perimeter_zone_ids.add(zid)
            installed_perimeters.append(zid)

        # --- 3. Update daemon defaults. ---
        with self._lock:
            self.default_stance = cfg["default_stance"]
            self.default_approach = cfg["default_approach"]

        # --- 4. Dispatch auto_missions. ---
        dispatched_ids: List[int] = []
        for spec in cfg.get("auto_missions", []):
            mid = self._dispatch_auto_mission(spec, world, owner="alert")
            if mid is not None:
                dispatched_ids.append(mid)

        # --- 5. force_recall_all (LOCKDOWN). ---
        recall_count = 0
        if cfg.get("force_recall_all") and world is not None and base is not None:
            mobile_ids = [
                u["id"] for u in world.get("self_units", [])
                if _is_combat_mobile(u.get("kind", ""))
            ]
            if mobile_ids:
                self.transport.send_command({
                    "type": "set_stance",
                    "unit_ids": mobile_ids,
                    "stance": "HoldFire",
                })
                self.transport.send_command({
                    "type": "move",
                    "unit_ids": mobile_ids,
                    "target": {"x": base[0], "y": base[1]},
                    "attack_move": False,
                })
                recall_count = len(mobile_ids)

        # --- bookkeeping ---
        with self._lock:
            self.current_alert_state = state
            self.auto_mission_ids = list(dispatched_ids)

        narrative = (
            f"Alert state {prev_state.value} → {state.value}. "
            f"Cancelled {len(cancelled_ids)} auto mission(s), "
            f"installed {len(installed_perimeters)} perimeter(s), "
            f"dispatched {len(dispatched_ids)} auto mission(s)"
        )
        if recall_count:
            narrative += f", recalled {recall_count} unit(s) to base"
        narrative += "."

        return {
            "ok": True,
            "previous_state": prev_state.value,
            "new_state": state.value,
            "cancelled_mission_ids": cancelled_ids,
            "cancelled_pending_ids": cancelled_pending_ids,
            "removed_perimeter_zone_ids": removed_perimeters,
            "installed_perimeter_zone_ids": installed_perimeters,
            "dispatched_mission_ids": dispatched_ids,
            "default_stance": cfg["default_stance"],
            "default_approach": cfg["default_approach"],
            "force_recall_all": cfg.get("force_recall_all", False),
            "recalled_unit_count": recall_count,
            "narrative": narrative,
        }

    # --- alert state helpers ------------------------------------------

    def _snapshot_world(self) -> Optional[dict]:
        """One-shot get_state for use by alert-state orchestration."""
        st = self.transport.send_command(
            {"type": "get_state", "include_enemies": True}
        )
        if not st.get("ok"):
            return None
        return st.get("state")

    def _resolve_self_base_from_snapshot(
        self, world: Optional[dict]
    ) -> Optional[Tuple[int, int]]:
        if world is None:
            return None
        self_units = world.get("self_units", [])
        for u in self_units:
            if u.get("kind", "").lower() == "fact":
                return (u["pos"]["x"], u["pos"]["y"])
        if self_units:
            n = len(self_units)
            return (sum(u["pos"]["x"] for u in self_units) // n,
                    sum(u["pos"]["y"] for u in self_units) // n)
        return None

    def _resolve_enemy_economy_center(
        self, world: dict
    ) -> Optional[Tuple[int, int]]:
        """Find the area around the nearest enemy economy structure
        (proc / silo / harv). Returns (x, y) or None if none visible."""
        economy_kinds = {"proc", "silo", "harv"}
        candidates = [u for u in world.get("enemy_units", [])
                      if u.get("kind", "").lower() in economy_kinds]
        if not candidates:
            return None
        # Pick nearest to self_base for the strongest harass target.
        base = self._resolve_self_base_from_snapshot(world)
        if base is not None:
            candidates.sort(
                key=lambda u: _dist2((u["pos"]["x"], u["pos"]["y"]), base)
            )
        target = candidates[0]
        return (target["pos"]["x"], target["pos"]["y"])

    def _resolve_named_target_for_assault(
        self, name: str, enemy_units: List[dict]
    ) -> Optional[Tuple[int, Tuple[int, int]]]:
        """Lookup a fresh enemy actor matching the named target. Used by
        cycle-assault to repick on target death. Returns (actor_id, (x,y))
        or None when nothing matches (mission ends).
        """
        def first_kind(kinds: Set[str]) -> Optional[dict]:
            for u in enemy_units:
                if u.get("kind", "").lower() in kinds:
                    return u
            return None

        candidates: Optional[dict] = None
        if name == "enemy_fact":
            candidates = first_kind({"fact"})
            if candidates is None:
                # Fall back to any structure so the army doesn't park.
                candidates = next(
                    (u for u in enemy_units if u.get("kind", "").lower() in
                     {"powr", "apwr", "proc", "barr", "tent", "weap",
                      "afld", "hpad", "spen", "syrd", "stek", "atek",
                      "dome", "fix", "silo"}),
                    None,
                )
        elif name in ("nearest_enemy_structure", "enemy_base", "enemy_center"):
            candidates = next(
                (u for u in enemy_units if u.get("kind", "").lower() in
                 {"fact", "powr", "apwr", "proc", "barr", "tent", "weap",
                  "afld", "hpad", "spen", "syrd", "stek", "atek", "dome",
                  "fix", "silo"}),
                None,
            )
        elif name in ("nearest_enemy", "nearest_enemy_unit"):
            candidates = next(iter(enemy_units), None)
        if candidates is None:
            return None
        return (int(candidates["id"]),
                (int(candidates["pos"]["x"]), int(candidates["pos"]["y"])))

    def _compute_map_perimeter_waypoints(
        self, world: dict
    ) -> List[Tuple[int, int]]:
        """Return four interior corner waypoints for a patrol loop. Use map
        bounds if available, else self_base ± 18-cell box as a fallback."""
        ms = world.get("map_size") or {}
        mx = int(ms.get("x", 0))
        my = int(ms.get("y", 0))
        if mx > 0 and my > 0:
            # 1/5 inset so we patrol inside the playable area, not the edge.
            ix = max(4, mx // 5)
            iy = max(4, my // 5)
            return [
                (ix, iy),
                (mx - ix, iy),
                (mx - ix, my - iy),
                (ix, my - iy),
            ]
        # Fallback — small box around self_base.
        base = self._resolve_self_base_from_snapshot(world) or (32, 32)
        bx, by = base
        return [(bx - 12, by - 12), (bx + 12, by - 12),
                (bx + 12, by + 12), (bx - 12, by + 12)]

    def _filter_force_ids_from_world(
        self, world: dict, spec: dict, limit: Optional[int],
        exclude_ids: Optional[Set[int]] = None,
    ) -> List[int]:
        """Resolve a `force` spec inside an auto_mission to a list of unit ids.

        `exclude_ids`: explicit set of ids to skip — used by dynamic force
        recruitment to pass `busy_elsewhere - own` so we don't re-recruit
        units we already own (which would otherwise be in `_all_busy_ids`).
        If None, defaults to `_all_busy_ids()` (the original behavior used
        by auto-mission dispatch).

        Supported spec fields: `harass_capable`, plus the same set used by
        ForceByFilter (`owner`, `unit_kind`, `hp_below`, `hp_above`,
        `in_group`).
        """
        self_units = world.get("self_units", [])

        # --- predicate builders (kept inline so this function stays one
        # place to scan when adding new filter fields).
        unit_kind = spec.get("unit_kind")
        hp_below = spec.get("hp_below")
        hp_above = spec.get("hp_above")

        harass_ok = frozenset({"jeep", "ftrk", "dog", "e3", "apc", "1tnk"})
        harass_bad = frozenset({"2tnk", "3tnk", "4tnk", "arty", "v2rl",
                                "mcv", "harv"})
        # Combat-mobile excludes harvesters, MCV, and buildings (anything
        # that can move + fight). Used by destroy_enemy cycle assault.
        non_combat = frozenset({"harv", "mcv"})
        building_kinds = frozenset({
            "fact", "powr", "apwr", "proc", "barr", "tent", "weap", "afld",
            "hpad", "spen", "syrd", "stek", "atek", "dome", "fix", "silo",
            "pbox", "hbox", "gun", "agun", "sam", "ftur", "tsla", "mslo",
            "iron", "pdox", "gap", "sbag", "brik", "barb", "cycl", "kenn",
        })

        def matches(u: dict) -> bool:
            kind = (u.get("kind") or "").lower()
            if spec.get("harass_capable") is True:
                if kind not in harass_ok or kind in harass_bad:
                    return False
            if spec.get("combat_mobile") is True:
                if kind in non_combat or kind in building_kinds:
                    return False
            if unit_kind and kind != unit_kind.lower():
                return False
            hp = float(u.get("hp_pct", 1.0))
            if hp_below is not None and not (hp < hp_below):
                return False
            if hp_above is not None and not (hp > hp_above):
                return False
            return True

        if exclude_ids is None:
            exclude_ids = self._all_busy_ids()

        ids: List[int] = []
        for u in self_units:
            uid = u.get("id")
            if uid is None or uid in exclude_ids:
                continue
            if not matches(u):
                continue
            ids.append(uid)
        if limit is not None and limit > 0:
            ids = ids[:limit]
        return ids

    def _all_busy_ids(self) -> Set[int]:
        """Union of ids reserved by any active mission (under lock)."""
        with self._lock:
            busy: Set[int] = set()
            for a in self._assaults.values():
                busy |= a.force_ids
            for m in self._harass.values():
                busy |= m.force_ids
            for m in self._patrol.values():
                busy |= m.force_ids
            for m in self._escort.values():
                busy |= m.force_ids
            for m in self._contain.values():
                busy |= m.force_ids
            for m in self._diversion.values():
                busy |= m.feint_force_ids | m.raid_force_ids
        return busy

    def _dispatch_auto_mission(
        self, spec: dict, world: Optional[dict],
        owner: str = "alert",
    ) -> Optional[int]:
        """Resolve + dispatch one auto_mission spec. Returns the mission_id
        on success, None when the spec couldn't be resolved (no force, no
        target, etc.) — partial failures are silent so a state swap never
        explodes mid-game.

        `owner` labels the pending entry so a doctrine swap can clean only
        its own waiting work ("alert" or "objective"). Auto-mission specs
        also embed the owner into __auto_spec__ so _tick_pending can pass
        it back when the entry finally dispatches.
        """
        if world is None:
            return None
        kind = spec.get("kind")
        force_spec = spec.get("force", {})
        limit = spec.get("max_force")
        ids = self._filter_force_ids_from_world(world, force_spec, limit)
        if not ids:
            # Force unresolvable now — enqueue so daemon retries when player
            # trains a matching unit. Wrap the raw spec under a sentinel key;
            # _tick_pending detects it and re-invokes _dispatch_auto_mission
            # instead of going through the interpreter (which would need a
            # fully-formed top-level intent dict).
            payload = {"__auto_spec__": dict(spec), "__owner__": owner}
            reason = f"auto_mission '{kind}' force empty; will retry"
            self.queue_pending(
                intent_kind=f"auto_{kind}",
                intent_payload=payload,
                reason=reason,
                owner=owner,
            )
            return None

        try:
            if kind == "patrol":
                wp_spec = spec.get("auto_waypoints")
                if wp_spec == "map_perimeter":
                    waypoints = self._compute_map_perimeter_waypoints(world)
                else:
                    return None
                base = (self._resolve_self_base_from_snapshot(world)
                        or waypoints[0])
                mid = self.register_patrol(
                    force_ids=ids,
                    waypoints=waypoints,
                    withdraw_to=base,
                    cycle=True,
                    contact_stance="ReturnFire",
                )
                # Tag as auto so the next state swap cleans it up.
                with self._lock:
                    m = self._patrol.get(mid)
                    if m is not None:
                        m.auto = True
                return mid

            if kind == "harass":
                region_spec = spec.get("target_region")
                if region_spec == "enemy_economy":
                    center = self._resolve_enemy_economy_center(world)
                else:
                    center = None
                if center is None:
                    return None
                base = self._resolve_self_base_from_snapshot(world) or center
                mid = self.register_harass(
                    force_ids=ids,
                    region_center=center,
                    region_radius=8,
                    withdraw_to=base,
                    cycle=True,
                    max_force_size=limit,
                )
                with self._lock:
                    m = self._harass.get(mid)
                    if m is not None:
                        m.auto = True
                return mid

            if kind == "attack":
                # Cycle-assault used by destroy_enemy objective. Resolves the
                # named target now; mission persists target_named so daemon
                # can re-resolve when it dies.
                target_named = spec.get("target_named", "enemy_fact")
                enemy_units = world.get("enemy_units", [])
                resolved = self._resolve_named_target_for_assault(
                    target_named, enemy_units)
                if resolved is None:
                    return None  # nothing to attack
                tid, tpos = resolved
                mid = self.register_assault(
                    force_ids=ids,
                    final_target_cell=tpos,
                    final_target_actor=tid,
                    cohesion=True,
                    force_spec=dict(force_spec),
                    target_named=target_named,
                    max_force_size=limit,
                )
                with self._lock:
                    m = self._assaults.get(mid)
                    if m is not None:
                        m.auto = True
                return mid
        except Exception as e:
            self.last_error = (
                f"auto_dispatch_failed[{kind}]: {type(e).__name__}: {e}"
            )
            return None

        return None

    # --- objective storage --------------------------------------------

    def set_objective(self, obj: Optional[Objective],
                      params: Optional[dict] = None) -> dict:
        """Store the player-declared victory condition + dispatch objective-
        owned auto-missions. `params` carries objective-specific data (e.g.
        {"tick": 30000} for survive_until).

        Behaviour:
        1. Cancel any mission previously dispatched by the prior objective
           (tracked in objective_mission_ids). Player-issued missions are
           untouched.
        2. Store new objective + params.
        3. Dispatch new objective's auto-mission set (see _OBJECTIVE_MISSIONS).
           force_empty entries enqueue to pending.

        Returns a transition report.
        """
        prev_obj = self.current_objective
        cancelled_ids: List[int] = []
        cancelled_pending_ids: List[int] = []
        with self._lock:
            for mid in list(self.objective_mission_ids):
                if mid in self._assaults:
                    self._assaults.pop(mid, None)
                    cancelled_ids.append(mid)
                if mid in self._harass:
                    self._harass.pop(mid, None)
                    cancelled_ids.append(mid)
                if mid in self._patrol:
                    self._patrol.pop(mid, None)
                    cancelled_ids.append(mid)
                if mid in self._contain:
                    self._contain.pop(mid, None)
                    cancelled_ids.append(mid)
            self.objective_mission_ids.clear()
            # Prune pending entries owned by the objective layer.
            for pid in list(self._pending.keys()):
                if self._pending[pid].owner == "objective":
                    self._pending.pop(pid, None)
                    cancelled_pending_ids.append(pid)
            self.current_objective = obj
            self.objective_params = dict(params or {})

        # Dispatch new objective's auto-missions.
        dispatched_ids: List[int] = []
        pending_ids: List[int] = []
        if obj is not None:
            specs = _OBJECTIVE_MISSIONS.get(obj, [])
            world = self._snapshot_world()
            for spec in specs:
                # Reuse the alert-state dispatcher path; it already handles
                # pending enqueue + tagging via _dispatch_auto_mission.
                pending_before = set(self._pending.keys())
                mid = self._dispatch_auto_mission(spec, world, owner="objective")
                if mid is not None:
                    dispatched_ids.append(mid)
                    with self._lock:
                        self.objective_mission_ids.append(mid)
                else:
                    new_pending = set(self._pending.keys()) - pending_before
                    pending_ids.extend(new_pending)
        return {
            "previous_objective": prev_obj.value if prev_obj else None,
            "new_objective": obj.value if obj else None,
            "params": dict(self.objective_params),
            "cancelled_mission_ids": cancelled_ids,
            "cancelled_pending_ids": cancelled_pending_ids,
            "dispatched_mission_ids": dispatched_ids,
            "pending_ids": pending_ids,
        }

    def get_objective(self) -> dict:
        with self._lock:
            return {
                "objective": (self.current_objective.value
                              if self.current_objective else None),
                "params": dict(self.objective_params),
            }

    # --- auto-escalation (light touch) --------------------------------

    def auto_escalate_check(self, world: Optional[dict] = None,
                            min_interval_s: float = 30.0) -> Optional[dict]:
        """If we're in PEACE and an enemy mobile unit is loitering near
        self_base, emit a one-time suggestion event. Does NOT auto-switch
        the alert state (player override principle, CONTEXT.md). Returns
        the event dict on a fresh suggestion, None otherwise.

        Throttled to one suggestion per `min_interval_s` seconds.
        """
        with self._lock:
            if self.current_alert_state != AlertState.PEACE:
                return None
            now = time.time()
            if now - self._last_escalation_alert_ts < min_interval_s:
                return None

        if world is None:
            world = self._snapshot_world()
        if world is None:
            return None

        base = self._resolve_self_base_from_snapshot(world)
        if base is None:
            return None

        r2 = 25 * 25
        intruders = [
            u for u in world.get("enemy_units", [])
            if not _is_building(u.get("kind", ""))
            and _dist2((u["pos"]["x"], u["pos"]["y"]), base) <= r2
        ]
        if not intruders:
            return None

        with self._lock:
            self._last_escalation_alert_ts = time.time()

        event = {
            "kind": "alert_state_suggestion",
            "severity": "warn",
            "message": (
                f"PEACE state but {len(intruders)} enemy mobile unit(s) "
                f"within 25 cells of base — suggest set_alert_state('watch') "
                f"or 'alert'."
            ),
            "from_state": AlertState.PEACE.value,
            "suggested_state": AlertState.WATCH.value,
            "intruder_count": len(intruders),
            "timestamp": time.time(),
        }

        # Best-effort push to the scout event log so the LLM picks it up via
        # latest_scout_report(). If the log isn't reachable, the event is
        # still returned to the caller.
        try:
            import json as _json
            log_path = os.environ.get("SCOUT_LOG_PATH")
            if log_path:
                from pathlib import Path as _Path
                path = _Path(log_path)
            else:
                from pathlib import Path as _Path
                path = (_Path(__file__).resolve().parent.parent
                        / "scout_events.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fp:
                fp.write(_json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return event

    def stop(self):
        self._stop.set()

    # --- after-action ---------------------------------------------------

    def _emit_after_action(self, mission: Any, outcome: str) -> None:
        """Push a one-line `mission_end` event to scout_events.jsonl.

        Includes mission_id, intent kind, outcome, duration, units_lost
        (initial_force - alive), and a rough kill estimate
        (initial_enemy_near_target - current). LLM picks it up via
        latest_scout_report() and relays the narrative to the player.
        """
        # Already emitted? guard against double-fire on cancel + finished.
        if getattr(mission, "end_outcome", None) is not None:
            return
        try:
            mission.end_outcome = outcome
        except Exception:
            pass

        now = time.time()
        started = float(getattr(mission, "started_at_ts", 0.0) or 0.0)
        duration = round(now - started, 1) if started else 0.0

        kind = self._mission_kind_label(mission)
        units_lost = self._estimate_units_lost(mission)
        kills_est = self._estimate_kills(mission)

        # Lightweight Chinese narrative — paper's prompt uses Chinese by default.
        narrative = (
            f"任务 #{mission.mission_id} ({kind}) 收尾: {outcome}. "
            f"耗时 {int(duration)}s, 损 {units_lost}, 杀 ~{kills_est}."
        )

        event = {
            "kind": "mission_end",
            "severity": "info",
            "mission_id": int(getattr(mission, "mission_id", -1)),
            "intent": kind,
            "outcome": outcome,
            "duration_s": duration,
            "units_lost": units_lost,
            "units_killed_estimate": kills_est,
            "narrative": narrative,
            "timestamp": now,
        }

        self._append_scout_event(event)
        self.after_action_emits += 1

    def _mission_kind_label(self, mission: Any) -> str:
        if isinstance(mission, Assault):
            return "assault"
        if isinstance(mission, HarassMission):
            return "harass"
        if isinstance(mission, PatrolMission):
            return "patrol"
        if isinstance(mission, EscortMission):
            return "escort"
        if isinstance(mission, ContainmentMission):
            return "contain"
        if isinstance(mission, DiversionMission):
            return "diversion"
        return type(mission).__name__.lower()

    def _estimate_units_lost(self, mission: Any) -> int:
        """initial_force_count - current alive (best-effort via last world)."""
        try:
            world = self._snapshot_world()
            alive_ids = {u["id"] for u in (world or {}).get("self_units", [])}
        except Exception:
            alive_ids = set()

        if isinstance(mission, DiversionMission):
            initial = (mission.initial_feint_count
                       + mission.initial_raid_count)
            alive = sum(1 for uid in (mission.feint_force_ids
                                       | mission.raid_force_ids)
                        if uid in alive_ids)
        else:
            initial = int(getattr(mission, "initial_force_count", 0) or 0)
            # Add recruits so "lost" doesn't double-count units we never had.
            recruited = int(getattr(mission, "recruited_count", 0) or 0)
            initial = initial + recruited
            ids = getattr(mission, "force_ids", set()) or set()
            alive = sum(1 for uid in ids if uid in alive_ids)
        return max(0, initial - alive)

    def _estimate_kills(self, mission: Any) -> int:
        """Rough: initial enemy count near target minus current count.
        Conservatively clipped at >= 0. Treat as a hint, not accounting."""
        try:
            target = self._mission_target_for_kills(mission)
            if target is None:
                return 0
            initial = int(getattr(mission, "initial_enemy_near_target", 0) or 0)
            current = self._count_enemies_near(target[0], radius=target[1])
            return max(0, initial - current)
        except Exception:
            return 0

    def _mission_target_for_kills(
        self, mission: Any
    ) -> Optional[Tuple[Tuple[int, int], int]]:
        if isinstance(mission, Assault):
            return (mission.final_target_cell, 10)
        if isinstance(mission, HarassMission):
            return (mission.region_center, mission.region_radius)
        if isinstance(mission, ContainmentMission):
            return (mission.chokepoint, mission.radius)
        if isinstance(mission, DiversionMission):
            return (mission.raid_target_cell, 8)
        return None  # patrol/escort have no fixed kill region

    def _append_scout_event(self, event: dict) -> None:
        """Write one event to scout_events.jsonl. Best-effort — never raises.

        Path resolution mirrors auto_escalate_check: SCOUT_LOG_PATH env
        wins, else <project_root>/scout_events.jsonl.
        """
        try:
            import json as _json
            log_path = os.environ.get("SCOUT_LOG_PATH")
            if log_path:
                from pathlib import Path as _Path
                path = _Path(log_path)
            else:
                from pathlib import Path as _Path
                path = (_Path(__file__).resolve().parent.parent
                        / "scout_events.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fp:
                fp.write(_json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # --- pending re-attempt -------------------------------------------

    def _tick_pending(self) -> None:
        """Re-run interpreter.interpret on any pending mission whose force
        spec now resolves to >= 1 unit. Dispatched ones are removed from
        the queue and an event is pushed so the LLM tells the player.

        Pending re-attempt uses a deferred import to avoid a cycle
        (interpreter imports tactical for register_*).
        """
        now = time.time()
        with self._lock:
            stale = [(p.pending_id, p) for p in self._pending.values()
                     if now - p.last_check_ts >= PENDING_RECHECK_S]
        if not stale:
            return

        try:
            from . import interpreter as _I
        except Exception:
            return

        for pid, pending in stale:
            with self._lock:
                # could have been cancelled between scan and now
                if pid not in self._pending:
                    continue
                self._pending[pid].last_check_ts = now

            payload = pending.intent_payload
            auto_spec = payload.get("__auto_spec__") if isinstance(payload, dict) else None

            if auto_spec is not None:
                # Auto-mission pending — re-invoke the daemon-internal
                # dispatcher with the original spec.
                world = self._snapshot_world()
                if world is None:
                    continue
                force_spec = auto_spec.get("force", {})
                limit = auto_spec.get("max_force")
                if not self._filter_force_ids_from_world(world, force_spec, limit):
                    continue  # still empty, keep waiting
                mid = self._dispatch_auto_mission(auto_spec, world)
                if mid is None:
                    continue  # dispatch failed (e.g. target unresolvable), retry
                with self._lock:
                    self._pending.pop(pid, None)
                    self.pending_dispatches += 1
                    # Track so a doctrine/alert swap can clean it up.
                    if mid not in self.auto_mission_ids:
                        self.auto_mission_ids.append(mid)
                self._append_scout_event({
                    "kind": "pending_dispatched",
                    "severity": "info",
                    "pending_id": pid,
                    "intent_kind": pending.intent_kind,
                    "narrative": f"{pending.intent_kind} 启动 (mission #{mid})",
                    "mission_id": mid,
                    "timestamp": time.time(),
                })
                continue

            # Probe force resolution without dispatching by building a
            # WorldView + resolving the force part of the payload. If it's
            # non-empty, hand the whole intent to the interpreter and drop
            # the pending entry.
            try:
                resolved_count = self._probe_force_count(payload)
            except Exception as e:
                self.last_error = f"pending_probe[{pid}]: {e}"
                continue

            if resolved_count == 0:
                continue

            # Dispatch for real. Best-effort; failures stay in queue so we
            # can re-try next interval.
            try:
                result = _I.interpret(payload, self.transport)
            except Exception as e:
                self.last_error = f"pending_dispatch[{pid}]: {e}"
                continue

            if not result.get("ok"):
                # Don't remove; let it retry. Avoid log churn.
                continue

            with self._lock:
                self._pending.pop(pid, None)
                self.pending_dispatches += 1

            # Push event so latest_scout_report() surfaces it for LLM relay.
            self._append_scout_event({
                "kind": "pending_dispatched",
                "severity": "info",
                "pending_id": pid,
                "intent_kind": pending.intent_kind,
                "narrative": result.get("narrative", ""),
                "mission_id": result.get("mission_id"),
                "timestamp": time.time(),
            })

    def _probe_force_count(self, intent_payload: dict) -> int:
        """Build a WorldView and resolve the intent's force spec to count.
        Diversion: count feint + raid. Others: count force field.

        Returns 0 when the force is unresolvable (e.g. spec missing or
        unparseable) — pending mission stays in queue.
        """
        from . import interpreter as _I
        from . import intent_dsl as _D
        wv = _I.WorldView(self.transport)
        kind = intent_payload.get("intent", "")
        try:
            if kind == "diversion":
                f1 = _D.parse_intent(intent_payload).feint_force
                f2 = _D.parse_intent(intent_payload).raid_force
                return len(wv.resolve_force(f1)) + len(wv.resolve_force(f2))
            parsed = _D.parse_intent(intent_payload)
            return len(wv.resolve_force(parsed.force))
        except Exception:
            return 0

    # --- internals -----------------------------------------------------

    def _ensure_thread(self):
        if _DISABLED:
            return
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="TacticalEngine", daemon=True
            )
            self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa
                self.last_error = f"{type(e).__name__}: {e}"
            self._stop.wait(POLL_INTERVAL_S)

    def _tick(self):
        # Snapshot world once per tick — cheap, and every sub-routine
        # below operates on a consistent view.
        st = self.transport.send_command({"type": "get_state", "include_enemies": True})
        if not st.get("ok"):
            return
        s = st["state"]
        if s.get("paused"):
            return  # game paused, don't issue orders
        self_units = s.get("self_units", [])
        enemy_units = s.get("enemy_units", [])

        # Dynamic force re-resolution (cycle-type missions) before runners
        # so this tick's logic uses the up-to-date force_ids set.
        try:
            self._resolve_dynamic_forces(self_units)
        except Exception as e:
            self.last_error = f"dynamic_resolve: {e}"

        with self._lock:
            assaults = list(self._assaults.values())
            perimeters = list(self._perimeters.values())
            harass_missions = list(self._harass.values())
            patrol_missions = list(self._patrol.values())
            escort_missions = list(self._escort.values())
            contain_missions = list(self._contain.values())
            diversion_missions = list(self._diversion.values())

        for a in assaults:
            if a.finished:
                continue
            self._run_assault(a, self_units, enemy_units)

        for m in harass_missions:
            if m.finished:
                continue
            self._run_harass(m, self_units, enemy_units)

        for m in patrol_missions:
            if m.finished:
                continue
            self._run_patrol(m, self_units, enemy_units)

        for m in escort_missions:
            if m.finished:
                continue
            self._run_escort(m, self_units, enemy_units)

        for m in contain_missions:
            if m.finished:
                continue
            self._run_contain(m, self_units, enemy_units)

        for m in diversion_missions:
            if m.finished:
                continue
            self._run_diversion(m, self_units, enemy_units)

        # Collect finished missions and emit after-action events for each
        # before dropping them. The mission outcome is inferred from the
        # mission type's terminal predicates (see _infer_outcome).
        finished_missions: List[Any] = []
        with self._lock:
            for mid in [k for k, v in self._assaults.items() if v.finished]:
                finished_missions.append(self._assaults.pop(mid))
            for mid in [k for k, v in self._harass.items() if v.finished]:
                finished_missions.append(self._harass.pop(mid))
            for mid in [k for k, v in self._patrol.items() if v.finished]:
                finished_missions.append(self._patrol.pop(mid))
            for mid in [k for k, v in self._escort.items() if v.finished]:
                finished_missions.append(self._escort.pop(mid))
            for mid in [k for k, v in self._contain.items() if v.finished]:
                finished_missions.append(self._contain.pop(mid))
            for mid in [k for k, v in self._diversion.items() if v.finished]:
                finished_missions.append(self._diversion.pop(mid))
        for m in finished_missions:
            try:
                outcome = self._infer_outcome(m)
                self._emit_after_action(m, outcome=outcome)
            except Exception:
                pass

        for zone in perimeters:
            self._run_defense(zone, self_units, enemy_units, assaults)

        # Support-pairing (always on). Pass the busy_id set so medics don't
        # try to wander out of an active escort/assault that already moves them.
        try:
            self._support.tick(self_units, self._all_busy_ids())
        except Exception as e:
            self.last_error = f"support_pair: {e}"

        # Pending mission re-attempt — cheap probe + dispatch if force resolves.
        try:
            self._tick_pending()
        except Exception as e:
            self.last_error = f"pending_tick: {e}"

        # Light-touch escalation check: if we're in PEACE and an intruder is
        # loitering near base, push a one-time alert event so the LLM (via
        # latest_scout_report) can prompt the player to upgrade. We do NOT
        # auto-switch — player override principle.
        try:
            self.auto_escalate_check(world=s)
        except Exception:
            pass  # never let an advisory crash the daemon loop

        self.tick_count += 1

    # --- dynamic force re-resolution ----------------------------------

    def _resolve_dynamic_forces(self, self_units: List[dict]) -> None:
        """For cycle-type missions with a `force_spec`, re-resolve the spec
        against the current world. Add newly-matching ids (capped by
        max_force_size), drop ids no longer present.

        Cheap enough to run every tick — typical mission has < 10 units and
        the world snapshot is already in hand.
        """
        live_ids = {u["id"]: u for u in self_units}
        now = time.time()
        # Re-resolve at most every PENDING_RECHECK_S to avoid thrashing on
        # spec matches that are already saturated.
        with self._lock:
            harass = list(self._harass.values())
            patrol = list(self._patrol.values())
            escort = list(self._escort.values())
            assaults = list(self._assaults.values())

        all_busy = self._all_busy_ids()
        for m in harass:
            self._refresh_mission_force(m, live_ids, all_busy, now)
        for m in patrol:
            self._refresh_mission_force(m, live_ids, all_busy, now)
        for m in escort:
            self._refresh_mission_force(m, live_ids, all_busy, now)
        # Cycle assault (Assault with force_spec set) — recruits newly trained
        # matching units into the push so destroy_enemy objective keeps the
        # army moving as the player produces.
        for m in assaults:
            self._refresh_mission_force(m, live_ids, all_busy, now)

    def _refresh_mission_force(
        self, mission: Any, live_ids: Dict[int, dict],
        busy_elsewhere: Set[int], now: float
    ) -> None:
        spec = getattr(mission, "force_spec", None)
        if not spec:
            # Static force — only prune dead ids; don't recruit.
            dead = {uid for uid in mission.force_ids if uid not in live_ids}
            if dead:
                with self._lock:
                    mission.force_ids -= dead
            return
        if now - getattr(mission, "last_resolve_ts", 0.0) < PENDING_RECHECK_S:
            # Throttle full re-resolve; still prune dead ids cheaply.
            dead = {uid for uid in mission.force_ids if uid not in live_ids}
            if dead:
                with self._lock:
                    mission.force_ids -= dead
            return
        mission.last_resolve_ts = now

        cap = getattr(mission, "max_force_size", None)
        own = set(mission.force_ids)
        # Pass `busy_elsewhere - own` so our existing members aren't filtered
        # out by the busy-ids guard inside _filter_force_ids_from_world.
        excl = busy_elsewhere - own
        try:
            new_ids = self._filter_force_ids_from_world(
                {"self_units": list(live_ids.values())},
                spec, cap, exclude_ids=excl,
            )
        except Exception as e:
            self.last_error = f"refresh[{mission.mission_id}]: {e}"
            return

        candidate = set(new_ids)

        # Cap: keep current alive first, then add new up to cap.
        alive_own = {uid for uid in own if uid in live_ids}
        if cap is not None and cap > 0:
            need = max(0, cap - len(alive_own))
            extra = [uid for uid in candidate if uid not in alive_own][:need]
            final = alive_own | set(extra)
        else:
            final = alive_own | candidate

        with self._lock:
            added = final - mission.force_ids
            mission.force_ids = final
            if added:
                # Track lifetime recruits for after-action narrative.
                mission.recruited_count = (
                    int(getattr(mission, "recruited_count", 0) or 0)
                    + len(added)
                )

    def _infer_outcome(self, mission: Any) -> str:
        """Best-effort outcome label from terminal state.

        - HarassMission: regrouping w/ cycle=False → completed,
                         else withdrawing/dead force → withdrawn,
                         empty force → wiped
        - PatrolMission: cycle exhausted (next_wp_idx > end & !cycle) → completed,
                         empty force → wiped
        - EscortMission: escortee gone → escortee_lost, force gone → wiped
        - ContainmentMission: empty force → wiped, else completed
        - DiversionMission: both prongs withdrew → withdrawn, else completed
        - Assault: target dead → target_dead, force dead → wiped, else completed
        """
        if isinstance(mission, HarassMission):
            if not mission.force_ids:
                return "wiped"
            if mission.state == "regrouping" and not mission.cycle:
                return "completed"
            return "withdrawn"
        if isinstance(mission, PatrolMission):
            if not mission.force_ids:
                return "wiped"
            return "completed"
        if isinstance(mission, EscortMission):
            if not mission.force_ids:
                return "wiped"
            return "escortee_lost"
        if isinstance(mission, ContainmentMission):
            return "wiped" if not mission.force_ids else "completed"
        if isinstance(mission, DiversionMission):
            if mission.feint_withdrew and mission.raid_withdrew:
                return "withdrawn"
            return "completed"
        if isinstance(mission, Assault):
            if not mission.force_ids:
                return "wiped"
            return "completed"
        return "completed"

    # --- assault sub-routine -----------------------------------------

    def _run_assault(self, a: Assault, self_units: List[dict], enemy_units: List[dict]):
        now = time.time()

        # 1. Build alive-force snapshot (id → unit dict for quick lookup).
        force_units = [u for u in self_units if u["id"] in a.force_ids]
        if not force_units:
            a.finished = True
            return
        alive_ids = {u["id"] for u in force_units}

        # 2. Retreat sub-routine. Any unit at low HP and not currently
        #    retreating is yanked back to self_base; units that have healed
        #    back to REENGAGE_HP_THRESHOLD return to the active pool.
        base = self._resolve_self_base(a, self_units)
        retreat_dispatched = self._handle_retreats(a, force_units, base, now)

        # 3. Active = alive minus those currently retreating.
        retreating_now = {uid for uid, ts in a.retreating.items()
                          if ts == 0.0 or ts > now}
        active = [u for u in force_units if u["id"] not in retreating_now]
        if not active:
            return  # whole force is healing; nothing to do this tick

        # 4. Compute force centroid (active only).
        active_pts = [(u["pos"]["x"], u["pos"]["y"]) for u in active]
        cx = sum(p[0] for p in active_pts) // len(active_pts)
        cy = sum(p[1] for p in active_pts) // len(active_pts)
        center = (cx, cy)

        # 5. Cohesion gate.
        active_ids_set = {u["id"] for u in active}
        if a.cohesion and len(active_pts) >= 3:
            d_max = 0
            d_max_id = None
            for u in active:
                d = _dist2((u["pos"]["x"], u["pos"]["y"]), center)
                if d > d_max:
                    d_max = d
                    d_max_id = u["id"]
            spread = d_max ** 0.5
            if spread > COHESION_MAX_SPREAD and d_max_id is not None:
                if d_max_id not in a.halted_units:
                    self.transport.send_command(
                        {"type": "stop", "unit_ids": [d_max_id]}
                    )
                    a.halted_units.add(d_max_id)
                    self.cohesion_halts += 1
            else:
                a.halted_units.clear()

        # 6. Engage-on-contact: priority + counter weighted picker on active force.
        engage_target = self._pick_priority_target(active, enemy_units, center)
        if engage_target is not None:
            tid, tpos = engage_target
            if a.current_target_actor != tid:
                self.retargets += 1
            a.current_target_actor = tid
            firing_ids = active_ids_set - a.halted_units
            if firing_ids:
                self.transport.send_command(
                    {"type": "attack",
                     "unit_ids": list(firing_ids),
                     "target_id": tid}
                )
            return

        # 7. No contact in radius — resume march to final target.
        # Cycle-assault retarget: if final_target_actor died AND mission has a
        # named target, look up a fresh target through WorldView. This stops
        # the army parking at a corpse's coords forever once the original
        # target (e.g. enemy_fact) is destroyed.
        if (a.force_spec is not None and a.target_named is not None
                and a.final_target_actor is not None):
            target_alive = any(u["id"] == a.final_target_actor for u in enemy_units)
            if not target_alive:
                fresh = self._resolve_named_target_for_assault(a.target_named, enemy_units)
                if fresh is not None:
                    a.final_target_actor = fresh[0]
                    a.final_target_cell = fresh[1]
                    self.retargets += 1
                else:
                    # No suitable enemy left — mission complete.
                    a.finished = True
                    return
        a.current_target_actor = a.final_target_actor
        moving = list(active_ids_set - a.halted_units)
        if not moving:
            return
        self.transport.send_command({
            "type": "move",
            "unit_ids": moving,
            "target": {"x": a.final_target_cell[0], "y": a.final_target_cell[1]},
            "attack_move": True,
        })

    # ------------------------------------------------------------------
    # Helpers — retreat + target prioritization
    # ------------------------------------------------------------------

    def _resolve_self_base(self, a: Assault,
                           self_units: List[dict]) -> Optional[Tuple[int, int]]:
        """Return cached or freshly-computed self_base cell, or None.

        Prefers an actual ConstructionYard (fact) if present, else the
        centroid of all owned units (proxy for 'where home is').
        """
        if a.self_base_cache is not None:
            return a.self_base_cache
        fact = next((u for u in self_units if u["kind"].lower() == "fact"), None)
        if fact:
            a.self_base_cache = (fact["pos"]["x"], fact["pos"]["y"])
            return a.self_base_cache
        if self_units:
            n = len(self_units)
            a.self_base_cache = (
                sum(u["pos"]["x"] for u in self_units) // n,
                sum(u["pos"]["y"] for u in self_units) // n,
            )
            return a.self_base_cache
        return None

    def _handle_retreats(self, a: Assault, force_units: List[dict],
                         base: Optional[Tuple[int, int]], now: float) -> int:
        """Mark low-HP units as retreating and send them home; release
        recovered units back into the pool. Returns count of new retreats
        this tick."""
        if base is None:
            return 0

        new_retreats: List[int] = []
        recovered: List[int] = []

        for u in force_units:
            hp = float(u.get("hp_pct", 1.0))
            uid = u["id"]
            already = uid in a.retreating

            if not already and hp < DOCTRINE.RETREAT_HP_THRESHOLD:
                new_retreats.append(uid)
                a.retreating[uid] = 0.0  # 0 = still en route home
            elif already and hp >= DOCTRINE.REENGAGE_HP_THRESHOLD:
                # If cooldown also elapsed, fully release.
                ts = a.retreating[uid]
                if ts != 0.0 and ts <= now:
                    recovered.append(uid)
                    a.retreating.pop(uid, None)
                elif ts == 0.0:
                    # Reached safety + healed → start cooldown clock
                    a.retreating[uid] = now + DOCTRINE.RETREAT_COOLDOWN_S

        if new_retreats:
            self.transport.send_command({
                "type": "set_stance",
                "unit_ids": new_retreats,
                "stance": "HoldFire",
            })
            self.transport.send_command({
                "type": "move",
                "unit_ids": new_retreats,
                "target": {"x": base[0], "y": base[1]},
                "attack_move": False,
            })

        if recovered:
            self.transport.send_command({
                "type": "set_stance",
                "unit_ids": recovered,
                "stance": "Defend",
            })

        return len(new_retreats)

    def _pick_priority_target(self, active: List[dict],
                              enemy_units: List[dict],
                              center: Tuple[int, int]
                              ) -> Optional[Tuple[int, Tuple[int, int]]]:
        """Choose the highest-scoring enemy in engage range.

        Score = base_priority × avg_counter(our force vs target) / max(5, dist).
        Returns (actor_id, (x, y)) or None when no enemy is reachable.
        Considers mobile threats inside ENGAGE_RADIUS first; if none, the
        march sub-routine takes over (we don't sidetrack to far buildings).
        """
        if not active or not enemy_units:
            return None

        r2 = ENGAGE_RADIUS * ENGAGE_RADIUS
        in_range = []
        for u in enemy_units:
            p = (u["pos"]["x"], u["pos"]["y"])
            if _dist2(center, p) <= r2:
                in_range.append((u, p))
        if not in_range:
            return None

        # Average our force counter score vs each candidate enemy.
        best = None
        best_score = -1.0
        for u, p in in_range:
            kind = u.get("kind", "")
            base = DOCTRINE.target_priority(kind)
            if base <= 0:
                continue
            cnt = 0.0
            for ou in active:
                cnt += DOCTRINE.counter_score(ou.get("kind", ""), kind)
            cnt_avg = cnt / max(1, len(active))
            dist = max(5.0, _dist2(center, p) ** 0.5)
            score = base * cnt_avg / dist
            if score > best_score:
                best_score = score
                best = (u["id"], p)
        return best

    def _pick_contact_target(
        self,
        center: Tuple[int, int],
        enemy_units: List[dict],
    ) -> Optional[Tuple[int, Tuple[int, int]]]:
        """Return (id, (x,y)) of nearest MOBILE enemy within ENGAGE_RADIUS,
        else None. Static structures are ignored — they're handled by the
        final-target march and shouldn't pull the force off-course."""
        best = None
        best_d = ENGAGE_RADIUS * ENGAGE_RADIUS + 1
        for u in enemy_units:
            if _is_building(u["kind"]):
                continue
            p = (u["pos"]["x"], u["pos"]["y"])
            d = _dist2(center, p)
            if d <= ENGAGE_RADIUS * ENGAGE_RADIUS and d < best_d:
                best = (u["id"], p)
                best_d = d
        return best

    # --- defense sub-routine -----------------------------------------

    def _run_defense(
        self,
        zone: DefenseZone,
        self_units: List[dict],
        enemy_units: List[dict],
        assaults: List[Assault],
    ):
        # 1. Any enemy mobile unit inside perimeter?
        r2 = zone.radius * zone.radius
        intruders = [
            u for u in enemy_units
            if not _is_building(u["kind"])
            and _dist2((u["pos"]["x"], u["pos"]["y"]), zone.center) <= r2
        ]
        if not intruders:
            return

        # 2. Cooldown gate so we don't re-issue defend orders every tick.
        now = time.time()
        if now - zone.last_dispatch_ts < zone.cooldown_s:
            return

        # 3. Pick the closest intruder and focus-fire with the local
        #    garrison — combat-mobile units within (radius * 1.5) of the
        #    perimeter center that are NOT part of an active assault or
        #    long-running mission (harass/patrol/escort/contain/diversion).
        intruders.sort(key=lambda u: _dist2((u["pos"]["x"], u["pos"]["y"]), zone.center))
        threat = intruders[0]

        busy_ids: Set[int] = set()
        for a in assaults:
            busy_ids |= a.force_ids
        # Long-running missions also reserve their forces from garrison duty.
        with self._lock:
            for m in self._harass.values():
                busy_ids |= m.force_ids
            for m in self._patrol.values():
                busy_ids |= m.force_ids
            for m in self._escort.values():
                busy_ids |= m.force_ids
            for m in self._contain.values():
                busy_ids |= m.force_ids
            for m in self._diversion.values():
                busy_ids |= m.feint_force_ids | m.raid_force_ids

        garrison_radius2 = int((zone.radius * 1.5) ** 2)
        garrison = [
            u["id"] for u in self_units
            if _is_combat_mobile(u["kind"])
            and u["id"] not in busy_ids
            and _dist2((u["pos"]["x"], u["pos"]["y"]), zone.center) <= garrison_radius2
        ]
        if not garrison:
            return

        self.transport.send_command(
            {"type": "set_stance", "unit_ids": garrison, "stance": "Defend"}
        )
        self.transport.send_command(
            {"type": "attack", "unit_ids": garrison, "target_id": threat["id"]}
        )
        zone.last_dispatch_ts = now
        self.defense_dispatches += 1

    # --- harass sub-routine ------------------------------------------

    # Minimum seconds between order re-issues for non-assault missions —
    # avoids spamming move/attack every 0.6s tick.
    _MISSION_COOLDOWN_S = 3.0

    def _force_avg_hp(self, force_units: List[dict]) -> float:
        if not force_units:
            return 0.0
        return sum(float(u.get("hp_pct", 1.0)) for u in force_units) / len(force_units)

    def _force_min_hp(self, force_units: List[dict]) -> float:
        if not force_units:
            return 1.0
        return min(float(u.get("hp_pct", 1.0)) for u in force_units)

    def _run_harass(self, m: HarassMission, self_units: List[dict],
                    enemy_units: List[dict]):
        force_units = [u for u in self_units if u["id"] in m.force_ids]
        if not force_units:
            m.finished = True
            return

        active_ids = [u["id"] for u in force_units]
        avg_hp = self._force_avg_hp(force_units)
        min_hp = self._force_min_hp(force_units)
        now = time.time()

        # State: withdrawing — keep retreating to withdraw_to until force avg
        # heals and distance from withdraw_to is small.
        if m.state == "withdrawing":
            wpos = m.withdraw_to
            if force_units:
                cx = sum(u["pos"]["x"] for u in force_units) // len(force_units)
                cy = sum(u["pos"]["y"] for u in force_units) // len(force_units)
                d = math.hypot(cx - wpos[0], cy - wpos[1])
                if d < 6 and avg_hp >= m.reengage_hp_threshold:
                    m.state = "regrouping"
            return

        # State: regrouping — wait for healing, then re-engage if cycle == True.
        if m.state == "regrouping":
            if avg_hp >= m.reengage_hp_threshold:
                if m.cycle:
                    m.state = "engaging"
                    m.current_target_actor = None
                else:
                    m.finished = True
            return

        # State: engaging
        # Trip wire: any unit hp < withdraw_hp_threshold → whole force withdraws.
        if min_hp < m.withdraw_hp_threshold:
            m.state = "withdrawing"
            self.transport.send_command(
                {"type": "set_stance", "unit_ids": active_ids,
                 "stance": "HoldFire"}
            )
            self.transport.send_command(
                {"type": "move", "unit_ids": active_ids,
                 "target": {"x": m.withdraw_to[0], "y": m.withdraw_to[1]},
                 "attack_move": False}
            )
            m.last_dispatch_ts = now
            return

        # Pick highest-priority enemy in region (harv > proc > inf > tnk via DOCTRINE).
        r2 = m.region_radius * m.region_radius
        in_region = [
            u for u in enemy_units
            if _dist2((u["pos"]["x"], u["pos"]["y"]), m.region_center) <= r2
        ]
        # Re-target check: current_target still alive?
        if m.current_target_actor is not None:
            still_alive = any(u["id"] == m.current_target_actor for u in in_region)
            if not still_alive:
                m.current_target_actor = None

        if not in_region:
            # Nothing to hit — march toward region center if not already there.
            if now - m.last_dispatch_ts < self._MISSION_COOLDOWN_S:
                return
            self.transport.send_command(
                {"type": "move", "unit_ids": active_ids,
                 "target": {"x": m.region_center[0], "y": m.region_center[1]},
                 "attack_move": True}
            )
            m.last_dispatch_ts = now
            return

        # Score by base priority weighted by counter vs our force.
        best = None
        best_score = -1.0
        for u in in_region:
            kind = u.get("kind", "")
            base = DOCTRINE.target_priority(kind)
            if base <= 0:
                continue
            cnt = 0.0
            for ou in force_units:
                cnt += DOCTRINE.counter_score(ou.get("kind", ""), kind)
            cnt_avg = cnt / max(1, len(force_units))
            score = base * cnt_avg
            if score > best_score:
                best_score = score
                best = u

        if best is None:
            return

        if m.current_target_actor != best["id"]:
            if now - m.last_dispatch_ts < self._MISSION_COOLDOWN_S:
                return
            m.current_target_actor = best["id"]
            self.transport.send_command(
                {"type": "set_stance", "unit_ids": active_ids,
                 "stance": "AttackAnything"}
            )
            self.transport.send_command(
                {"type": "attack", "unit_ids": active_ids,
                 "target_id": best["id"]}
            )
            m.last_dispatch_ts = now

    # --- patrol sub-routine ------------------------------------------

    def _run_patrol(self, m: PatrolMission, self_units: List[dict],
                    enemy_units: List[dict]):
        force_units = [u for u in self_units if u["id"] in m.force_ids]
        if not force_units or not m.waypoints:
            m.finished = True
            return

        active_ids = [u["id"] for u in force_units]
        cx = sum(u["pos"]["x"] for u in force_units) // len(force_units)
        cy = sum(u["pos"]["y"] for u in force_units) // len(force_units)
        center = (cx, cy)
        now = time.time()

        # Low-HP units break off to withdraw_to (one-shot per unit).
        wounded = [u["id"] for u in force_units
                   if float(u.get("hp_pct", 1.0)) < m.low_hp_threshold]
        if wounded and now - m.last_dispatch_ts > self._MISSION_COOLDOWN_S:
            self.transport.send_command(
                {"type": "set_stance", "unit_ids": wounded, "stance": "HoldFire"}
            )
            self.transport.send_command(
                {"type": "move", "unit_ids": wounded,
                 "target": {"x": m.withdraw_to[0], "y": m.withdraw_to[1]},
                 "attack_move": False}
            )
            # Remove from active route — they're done patrolling.
            for uid in wounded:
                m.force_ids.discard(uid)
            m.last_dispatch_ts = now
            return

        wp = m.waypoints[m.next_wp_idx]
        d = math.hypot(cx - wp[0], cy - wp[1])
        # Arrived at waypoint — advance pointer.
        if d < 3:
            if m.next_wp_idx != m.last_arrived_wp_idx:
                m.last_arrived_wp_idx = m.next_wp_idx
                m.next_wp_idx += 1
                if m.next_wp_idx >= len(m.waypoints):
                    if m.cycle:
                        m.next_wp_idx = 0
                        m.last_arrived_wp_idx = -1
                    else:
                        m.finished = True
                        return
                wp = m.waypoints[m.next_wp_idx]

        if now - m.last_dispatch_ts < self._MISSION_COOLDOWN_S:
            return
        self.transport.send_command(
            {"type": "set_stance", "unit_ids": active_ids,
             "stance": m.contact_stance}
        )
        self.transport.send_command(
            {"type": "move", "unit_ids": active_ids,
             "target": {"x": wp[0], "y": wp[1]}, "attack_move": True}
        )
        m.last_dispatch_ts = now

    # --- escort sub-routine ------------------------------------------

    def _run_escort(self, m: EscortMission, self_units: List[dict],
                    enemy_units: List[dict]):
        escortee = next((u for u in self_units if u["id"] == m.escortee_id), None)
        if escortee is None:
            # Escortee died (or fog-lost). End mission.
            m.finished = True
            return

        force_units = [u for u in self_units if u["id"] in m.force_ids]
        if not force_units:
            m.finished = True
            return

        active_ids = [u["id"] for u in force_units]
        epos = (escortee["pos"]["x"], escortee["pos"]["y"])
        now = time.time()

        # Engage closest threat within engage_radius of escortee.
        er2 = m.engage_radius * m.engage_radius
        threats = [
            u for u in enemy_units
            if not _is_building(u["kind"])
            and _dist2((u["pos"]["x"], u["pos"]["y"]), epos) <= er2
        ]
        if threats:
            threats.sort(key=lambda u: _dist2((u["pos"]["x"], u["pos"]["y"]), epos))
            t = threats[0]
            if now - m.last_dispatch_ts >= self._MISSION_COOLDOWN_S:
                self.transport.send_command(
                    {"type": "set_stance", "unit_ids": active_ids,
                     "stance": "AttackAnything"}
                )
                self.transport.send_command(
                    {"type": "attack", "unit_ids": active_ids,
                     "target_id": t["id"]}
                )
                m.last_dispatch_ts = now
            return

        # No threat — close formation. Any guard outside escort_radius gets pulled.
        if now - m.last_dispatch_ts < self._MISSION_COOLDOWN_S:
            return
        er = m.escort_radius * m.escort_radius
        stragglers = [
            u["id"] for u in force_units
            if _dist2((u["pos"]["x"], u["pos"]["y"]), epos) > er
        ]
        if stragglers:
            self.transport.send_command(
                {"type": "move", "unit_ids": stragglers,
                 "target": {"x": epos[0], "y": epos[1]},
                 "attack_move": True}
            )
            m.last_dispatch_ts = now

    # --- containment sub-routine -------------------------------------

    def _run_contain(self, m: ContainmentMission, self_units: List[dict],
                     enemy_units: List[dict]):
        force_units = [u for u in self_units if u["id"] in m.force_ids]
        if not force_units:
            m.finished = True
            return

        active_ids = [u["id"] for u in force_units]
        now = time.time()
        r2 = m.radius * m.radius

        # 1. Engage targets inside radius.
        targets_in = [
            u for u in enemy_units
            if not _is_building(u["kind"])
            and _dist2((u["pos"]["x"], u["pos"]["y"]), m.chokepoint) <= r2
        ]
        # Re-target check.
        if m.current_target_actor is not None:
            if not any(u["id"] == m.current_target_actor for u in targets_in):
                m.current_target_actor = None

        if targets_in:
            targets_in.sort(key=lambda u: _dist2(
                (u["pos"]["x"], u["pos"]["y"]), m.chokepoint))
            t = targets_in[0]
            if m.current_target_actor != t["id"]:
                if now - m.last_dispatch_ts < self._MISSION_COOLDOWN_S:
                    return
                m.current_target_actor = t["id"]
                self.transport.send_command(
                    {"type": "set_stance", "unit_ids": active_ids,
                     "stance": m.stance}
                )
                self.transport.send_command(
                    {"type": "attack", "unit_ids": active_ids,
                     "target_id": t["id"]}
                )
                m.last_dispatch_ts = now
            return

        # 2. No targets — pull stragglers back to chokepoint.
        if now - m.last_dispatch_ts < self._MISSION_COOLDOWN_S:
            return
        strayed = [
            u["id"] for u in force_units
            if _dist2((u["pos"]["x"], u["pos"]["y"]), m.chokepoint) > r2
        ]
        if strayed:
            self.transport.send_command(
                {"type": "set_stance", "unit_ids": strayed, "stance": m.stance}
            )
            self.transport.send_command(
                {"type": "move", "unit_ids": strayed,
                 "target": {"x": m.chokepoint[0], "y": m.chokepoint[1]},
                 "attack_move": False}
            )
            m.last_dispatch_ts = now

    # --- diversion sub-routine ---------------------------------------

    def _run_diversion(self, m: DiversionMission, self_units: List[dict],
                       enemy_units: List[dict]):
        feint_units = [u for u in self_units if u["id"] in m.feint_force_ids]
        raid_units = [u for u in self_units if u["id"] in m.raid_force_ids]

        # End condition: both prongs dead or both withdrew.
        if not feint_units and not raid_units:
            m.finished = True
            return

        now = time.time()

        # --- feint prong ---
        if feint_units and not m.feint_withdrew:
            fids = [u["id"] for u in feint_units]
            avg_hp = self._force_avg_hp(feint_units)
            if avg_hp < 0.4:
                # Withdraw feint.
                m.feint_withdrew = True
                self.transport.send_command(
                    {"type": "set_stance", "unit_ids": fids,
                     "stance": "HoldFire"}
                )
                self.transport.send_command(
                    {"type": "move", "unit_ids": fids,
                     "target": {"x": m.withdraw_to[0], "y": m.withdraw_to[1]},
                     "attack_move": False}
                )
                m.last_dispatch_ts = now
            elif now - m.last_dispatch_ts >= self._MISSION_COOLDOWN_S:
                # Hold at the feint stopline. If raid engaged + feint_commits,
                # upgrade to AttackAnything.
                stance = "AttackAnything" if (
                    m.raid_engaged and m.feint_commits
                ) else "ReturnFire"
                self.transport.send_command(
                    {"type": "set_stance", "unit_ids": fids, "stance": stance}
                )
                self.transport.send_command(
                    {"type": "move", "unit_ids": fids,
                     "target": {"x": m.feint_target_cell[0],
                                "y": m.feint_target_cell[1]},
                     "attack_move": False}
                )
                m.last_dispatch_ts = now

        # --- raid prong ---
        if raid_units and not m.raid_withdrew:
            rids = [u["id"] for u in raid_units]
            avg_hp = self._force_avg_hp(raid_units)
            if avg_hp < 0.4:
                m.raid_withdrew = True
                self.transport.send_command(
                    {"type": "set_stance", "unit_ids": rids,
                     "stance": "HoldFire"}
                )
                self.transport.send_command(
                    {"type": "move", "unit_ids": rids,
                     "target": {"x": m.withdraw_to[0], "y": m.withdraw_to[1]},
                     "attack_move": False}
                )
                m.last_dispatch_ts = now
            else:
                # Detect raid engagement: any enemy mobile within 6 cells of any raid unit.
                if not m.raid_engaged:
                    for ru in raid_units:
                        rp = (ru["pos"]["x"], ru["pos"]["y"])
                        for e in enemy_units:
                            if _is_building(e.get("kind", "")):
                                continue
                            if _dist2(rp, (e["pos"]["x"], e["pos"]["y"])) <= 36:
                                m.raid_engaged = True
                                break
                        if m.raid_engaged:
                            break

                # If we have a flank waypoint and haven't reached it yet, go there first.
                cx = sum(u["pos"]["x"] for u in raid_units) // len(raid_units)
                cy = sum(u["pos"]["y"] for u in raid_units) // len(raid_units)
                tgt = m.raid_target_cell
                if m.raid_waypoint is not None:
                    d_wp = math.hypot(cx - m.raid_waypoint[0],
                                      cy - m.raid_waypoint[1])
                    if d_wp > 4:
                        tgt = m.raid_waypoint

                if now - m.last_dispatch_ts >= self._MISSION_COOLDOWN_S:
                    self.transport.send_command(
                        {"type": "set_stance", "unit_ids": rids,
                         "stance": "AttackAnything"}
                    )
                    # If we have a live target actor and reached the target, focus fire.
                    if (m.raid_target_actor is not None
                            and tgt == m.raid_target_cell):
                        self.transport.send_command(
                            {"type": "attack", "unit_ids": rids,
                             "target_id": m.raid_target_actor}
                        )
                    else:
                        self.transport.send_command(
                            {"type": "move", "unit_ids": rids,
                             "target": {"x": tgt[0], "y": tgt[1]},
                             "attack_move": True}
                        )
                    m.last_dispatch_ts = now

        # End condition: both prongs withdrew (force present but disengaged).
        if (m.feint_withdrew or not feint_units) and \
           (m.raid_withdrew or not raid_units):
            m.finished = True


# ---------------------------------------------------------------------------
# Module-level singleton helper
# ---------------------------------------------------------------------------

_engine: Optional[TacticalEngine] = None


def get_engine(transport) -> TacticalEngine:
    global _engine
    if _engine is None:
        _engine = TacticalEngine(transport)
    return _engine
