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

import os
import threading
import time
from dataclasses import dataclass, field
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
# Mission state
# ---------------------------------------------------------------------------

@dataclass
class Assault:
    """One active offensive mission shepherded by the daemon."""
    mission_id: int
    force_ids: Set[int]
    final_target_cell: Tuple[int, int]
    final_target_actor: Optional[int]
    cohesion: bool = True
    # runtime
    current_target_actor: Optional[int] = None
    last_seen_target_alive_at: float = 0.0
    halted_units: Set[int] = field(default_factory=set)  # for cohesion gate
    finished: bool = False
    # retreat state: unit_id → ts when it can re-engage (or 0 if still retreating)
    retreating: Dict[int, float] = field(default_factory=dict)
    # cached self_base for retreat target (lazy on first need)
    self_base_cache: Optional[Tuple[int, int]] = None


@dataclass
class DefenseZone:
    """A perimeter we auto-react to when enemies enter."""
    center: Tuple[int, int]
    radius: int = DEFENSE_PERIMETER_RADIUS
    last_dispatch_ts: float = 0.0
    cooldown_s: float = 8.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TacticalEngine:
    """Singleton — owns one polling thread shared by all missions."""

    def __init__(self, transport):
        self.transport = transport
        self._lock = threading.Lock()
        self._assaults: Dict[int, Assault] = {}
        self._defense: Optional[DefenseZone] = None
        self._next_id = 1
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Diagnostic counters surfaced via status().
        self.tick_count = 0
        self.retargets = 0
        self.cohesion_halts = 0
        self.defense_dispatches = 0
        self.last_error: Optional[str] = None

    # --- public surface ------------------------------------------------

    def register_assault(
        self,
        force_ids: List[int],
        final_target_cell: Tuple[int, int],
        final_target_actor: Optional[int] = None,
        cohesion: bool = True,
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
                current_target_actor=final_target_actor,
            )
        self._ensure_thread()
        return mid

    def cancel_assault(self, mission_id: int) -> bool:
        with self._lock:
            return self._assaults.pop(mission_id, None) is not None

    def cancel_all_assaults(self) -> int:
        with self._lock:
            n = len(self._assaults)
            self._assaults.clear()
            return n

    def enable_auto_defense(self, center: Tuple[int, int], radius: int = DEFENSE_PERIMETER_RADIUS):
        with self._lock:
            self._defense = DefenseZone(center=center, radius=radius)
        self._ensure_thread()

    def disable_auto_defense(self):
        with self._lock:
            self._defense = None

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "active_assaults": len(self._assaults),
                "auto_defense_on": self._defense is not None,
                "auto_defense_center": (
                    list(self._defense.center) if self._defense else None
                ),
                "tick_count": self.tick_count,
                "retargets": self.retargets,
                "cohesion_halts": self.cohesion_halts,
                "defense_dispatches": self.defense_dispatches,
                "last_error": self.last_error,
            }

    def stop(self):
        self._stop.set()

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

        with self._lock:
            assaults = list(self._assaults.values())
            defense = self._defense

        for a in assaults:
            if a.finished:
                continue
            self._run_assault(a, self_units, enemy_units)

        # Drop finished assaults.
        with self._lock:
            for mid in [k for k, v in self._assaults.items() if v.finished]:
                self._assaults.pop(mid, None)

        if defense is not None:
            self._run_defense(defense, self_units, enemy_units, assaults)

        self.tick_count += 1

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
        #    perimeter center that are NOT part of an active assault.
        intruders.sort(key=lambda u: _dist2((u["pos"]["x"], u["pos"]["y"]), zone.center))
        threat = intruders[0]

        assault_ids: Set[int] = set()
        for a in assaults:
            assault_ids |= a.force_ids
        garrison_radius2 = int((zone.radius * 1.5) ** 2)
        garrison = [
            u["id"] for u in self_units
            if _is_combat_mobile(u["kind"])
            and u["id"] not in assault_ids
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


# ---------------------------------------------------------------------------
# Module-level singleton helper
# ---------------------------------------------------------------------------

_engine: Optional[TacticalEngine] = None


def get_engine(transport) -> TacticalEngine:
    global _engine
    if _engine is None:
        _engine = TacticalEngine(transport)
    return _engine
