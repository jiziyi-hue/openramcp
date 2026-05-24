"""Formation helpers for the tactical daemon.

Builds a wedge-by-range formation: short-range melee/short up front, mid-
range (anti-tank infantry, flak truck) at the centroid line, long-range
(siege artillery, aircraft) behind. The daemon uses these per-unit offsets
to keep the army from arriving as a uniform blob where v2 / arty get into
melee range and die before firing.

The formation is computed lazily on each tick from the current centroid +
target direction, so it self-corrects as units die and the centroid shifts.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from . import tactical_doctrine as DOCTRINE


# Layer offsets along the direction from centroid → target.
# Positive = ahead (closer to enemy), negative = behind (further from enemy).
_LAYER_FORWARD_CELLS = {
    "short": 3,
    "mid": 0,
    "long": -5,
}

# Lateral spread for units in the same layer. Each unit gets a slot index
# 0, 1, -1, 2, -2, ... times this spacing, perpendicular to the forward axis.
_LATERAL_SPACING = 2


def _direction(centroid: Tuple[int, int],
               target: Tuple[int, int]) -> Tuple[float, float]:
    """Unit vector pointing from centroid → target. Falls back to (1,0)."""
    dx = target[0] - centroid[0]
    dy = target[1] - centroid[1]
    mag = math.hypot(dx, dy)
    if mag < 0.5:
        return (1.0, 0.0)
    return (dx / mag, dy / mag)


def compute_formation_targets(
    force_units: List[dict],
    centroid: Tuple[int, int],
    target_cell: Tuple[int, int],
) -> Dict[int, Tuple[int, int]]:
    """Return {unit_id → desired_cell} mapping that places each unit at its
    layer-appropriate slot relative to the centroid + target direction.

    Caller is expected to issue move commands toward these slots ONLY when
    a unit is significantly displaced from its slot (so we don't flood the
    engine with movement orders every tick).
    """
    if not force_units:
        return {}

    fwd_x, fwd_y = _direction(centroid, target_cell)
    # Perpendicular axis for lateral slot spread.
    perp_x, perp_y = -fwd_y, fwd_x

    # Group units by range tier so each tier gets its own forward layer.
    by_tier: Dict[str, List[dict]] = {"short": [], "mid": [], "long": []}
    for u in force_units:
        tier = DOCTRINE.range_tier(u.get("kind", ""))
        by_tier.setdefault(tier, []).append(u)

    targets: Dict[int, Tuple[int, int]] = {}
    for tier, units in by_tier.items():
        if not units:
            continue
        fwd_cells = _LAYER_FORWARD_CELLS.get(tier, 0)
        # Deterministic slot ordering by unit id keeps frame-to-frame stable
        # (a unit's slot doesn't shuffle each tick).
        units.sort(key=lambda u: u.get("id", 0))
        for idx, u in enumerate(units):
            # Slots: 0, 1, -1, 2, -2, ...
            half = (idx + 1) // 2
            sign = 1 if idx % 2 == 0 else -1
            lateral_cells = half * _LATERAL_SPACING * sign
            # Anchor the slot relative to centroid (not the target) so a
            # slow tier keeps its position even if the centroid hasn't
            # reached the target yet.
            cx = centroid[0] + int(round(fwd_cells * fwd_x + lateral_cells * perp_x))
            cy = centroid[1] + int(round(fwd_cells * fwd_y + lateral_cells * perp_y))
            targets[int(u["id"])] = (cx, cy)
    return targets


def displacement(pos: Tuple[int, int], slot: Tuple[int, int]) -> float:
    """Euclidean cells between a unit and its desired slot."""
    return math.hypot(pos[0] - slot[0], pos[1] - slot[1])


# Kinds that count as static enemy defenses for detour computation.
_DEFENSIVE_KINDS = frozenset({
    "pbox", "hbox", "gun", "agun", "sam", "tsla", "ftur",
})


def detour_waypoint(
    centroid: Tuple[int, int],
    target: Tuple[int, int],
    enemy_units: List[dict],
    detour_threshold_cells: float = 12.0,
    sidestep_cells: int = 8,
) -> Tuple[int, int]:
    """Return a waypoint cell that nudges the path around enemy defenses.

    Algorithm: find the centroid of enemy defensive structures (pillbox /
    tesla / sam / etc) whose perpendicular distance from the centroid →
    target line is within `detour_threshold_cells`. If such a cluster
    exists, return a waypoint that sidesteps it (perpendicular to the
    direct path). Otherwise return the original target.
    """
    if not enemy_units:
        return target

    fwd_x, fwd_y = _direction(centroid, target)
    perp_x, perp_y = -fwd_y, fwd_x

    # Direct path length (used to clamp 't' values into [0, 1]).
    dx = target[0] - centroid[0]
    dy = target[1] - centroid[1]
    path_len = math.hypot(dx, dy)
    if path_len < 4.0:
        return target  # too close to bother

    blockers: List[Tuple[float, float]] = []  # (along, lateral) per blocker
    for u in enemy_units:
        if (u.get("kind") or "").lower() not in _DEFENSIVE_KINDS:
            continue
        ux = float(u["pos"]["x"]) - centroid[0]
        uy = float(u["pos"]["y"]) - centroid[1]
        # Project onto forward axis.
        along = ux * fwd_x + uy * fwd_y
        lateral = ux * perp_x + uy * perp_y
        # Only consider blockers that lie ahead and near the line.
        if along < 0 or along > path_len:
            continue
        if abs(lateral) > detour_threshold_cells:
            continue
        blockers.append((along, lateral))

    if not blockers:
        return target

    # Average lateral offset of blockers; sidestep in the opposite direction.
    avg_lateral = sum(b[1] for b in blockers) / len(blockers)
    sidestep_sign = -1 if avg_lateral >= 0 else 1
    # Place the detour cell halfway along the path, offset by sidestep_cells
    # away from the blockers. Caller can either move TO this point first or
    # use it as the target_cell for the next tick.
    mid_along = path_len * 0.5
    wp_x = centroid[0] + int(round(mid_along * fwd_x
                                    + sidestep_sign * sidestep_cells * perp_x))
    wp_y = centroid[1] + int(round(mid_along * fwd_y
                                    + sidestep_sign * sidestep_cells * perp_y))
    return (wp_x, wp_y)
