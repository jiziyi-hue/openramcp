"""
Cell-grid geometry helpers for the DSL interpreter.

OpenRA uses CPos = (X, Y) integer cells. All functions here take/return tuples
of ints. No floating-point math leaks outside.
"""

from __future__ import annotations

import math
from typing import Tuple


Cell = Tuple[int, int]


def add(a: Cell, b: Cell) -> Cell:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Cell, b: Cell) -> Cell:
    return (a[0] - b[0], a[1] - b[1])


def length(v: Cell) -> float:
    return math.hypot(v[0], v[1])


def normalize(v: Cell) -> Tuple[float, float]:
    """Return a float unit vector. Caller scales then rounds."""
    L = length(v)
    if L < 1e-6:
        return (0.0, 0.0)
    return (v[0] / L, v[1] / L)


def scale_int(u: Tuple[float, float], k: float) -> Cell:
    return (int(round(u[0] * k)), int(round(u[1] * k)))


def perpendicular(u: Tuple[float, float], side: str) -> Tuple[float, float]:
    """Rotate a unit vector 90°. side='left' or 'right' (right-handed coord)."""
    if side == "left":
        return (-u[1], u[0])
    if side == "right":
        return (u[1], -u[0])
    raise ValueError(f"perpendicular side must be left|right, got {side!r}")


def midpoint(a: Cell, b: Cell) -> Cell:
    return ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)


def along(a: Cell, b: Cell, t: float) -> Cell:
    """Point t-of-the-way from a to b (t in [0,1])."""
    return (int(round(a[0] + (b[0] - a[0]) * t)),
            int(round(a[1] + (b[1] - a[1]) * t)))


def distance(a: Cell, b: Cell) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# Flank / pincer waypoint algorithms
# ---------------------------------------------------------------------------

def flank_waypoint(force_pos: Cell, target_pos: Cell, side: str,
                   sidestep_cells: int = 12, approach_t: float = 0.6) -> Cell:
    """Compute a waypoint for a flanking approach.

    The waypoint sits offset perpendicular to the force-to-target axis,
    pulled `approach_t` of the way toward the target. The unit moves to
    the waypoint first; the interpreter then issues a follow-up attack
    order on arrival.

    side: 'left' or 'right' (from the force's point of view facing target)
    sidestep_cells: how far off-axis (cells)
    approach_t: how close to target (0=force, 1=target)
    """
    axis = sub(target_pos, force_pos)
    u = normalize(axis)
    perp = perpendicular(u, side)
    base = along(force_pos, target_pos, approach_t)
    return add(base, scale_int(perp, sidestep_cells))


def pincer_rendezvous(target_pos: Cell, rendezvous_dist: int,
                      axis_left: Cell, axis_right: Cell) -> Tuple[Cell, Cell]:
    """Compute the two rendezvous points for a pincer.

    left arm meets at a point `rendezvous_dist` from target, perpendicular-
    left to the (axis_left -> target) line. Same for right.
    """
    def one_arm(force_pos: Cell, side: str) -> Cell:
        axis = sub(target_pos, force_pos)
        u = normalize(axis)
        perp = perpendicular(u, side)
        # back off along -u from target by rendezvous_dist; then offset perp by half
        retreat = scale_int(u, -rendezvous_dist)
        offset = scale_int(perp, rendezvous_dist // 2)
        return add(add(target_pos, retreat), offset)

    return one_arm(axis_left, "left"), one_arm(axis_right, "right")


def feint_stopline(force_pos: Cell, target_pos: Cell,
                   engage_distance: int = 8) -> Cell:
    """佯攻停线: 推到距 target engage_distance 格的位置就停, 不真冲."""
    axis = sub(target_pos, force_pos)
    L = length(axis)
    if L < engage_distance:
        return force_pos
    u = normalize(axis)
    advance = L - engage_distance
    return add(force_pos, scale_int(u, advance))


def cautious_engage_point(force_pos: Cell, target_pos: Cell,
                          weapon_range_cells: int = 6) -> Cell:
    """谨慎接战点: 推到 0.7 × 武器射程距离, 不直插中心."""
    target_offset = int(weapon_range_cells * 0.7)
    return feint_stopline(force_pos, target_pos, engage_distance=target_offset)


# ---------------------------------------------------------------------------
# Region helpers
# ---------------------------------------------------------------------------

def region_center(region_kind: str, **kwargs) -> Cell:
    """Resolve a named/rect/around region down to its center cell."""
    if region_kind == "rect":
        x1, y1, x2, y2 = kwargs["x1"], kwargs["y1"], kwargs["x2"], kwargs["y2"]
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    if region_kind == "around":
        return kwargs["center"]  # caller resolves named center already
    raise ValueError(f"region_center: unsupported kind {region_kind!r}")
