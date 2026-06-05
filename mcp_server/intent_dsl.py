"""
Intent DSL — pydantic schemas for the structured commands Claude emits.

Phase ablation C4 (2026-05-25): trimmed to attack / report / raw. The
daemon-backed intents (defend/retreat/regroup/scout/pincer/feint/harass/
patrol/escort/contain/diversion/set_stance) were removed alongside
tactical.py. Higher-level tactics (pincer / feint+raid / patrol / etc.)
are now composed LLM-side via spawn_squad_batch + the compose_*.py
helpers — see docs/TWO_PRIMITIVES_PARADIGM.md.

Design rules:
  1. Every semantic field is a Literal[...] enum, not free text.
  2. The LLM only fills field values from the enumerated choices.
  3. The interpreter (interpreter.py) maps each intent to deterministic
     atomic MCP commands.
"""

from __future__ import annotations

from typing import Literal, Optional, Union
from pydantic import BaseModel

from .schema import Vec2


# ============================================================================
# Reusable enums
# ============================================================================

ReportWhat = Literal[
    "battlefield",
    "enemy",
    "threats",
    "minimap",
    "resources",
]

NamedTarget = Literal[
    "enemy_fact",
    "enemy_base",
    "enemy_center",
    "self_base",
    "self_center",
    "nearest_enemy",
    "nearest_enemy_unit",
    "nearest_enemy_structure",
    # Map landmarks — resolved from map_size so the LLM can say "go to the
    # centre / a corner" without ever computing coordinates.
    "map_center",
    "map_corner_ne",
    "map_corner_nw",
    "map_corner_se",
    "map_corner_sw",
]


# ============================================================================
# Force selectors
# ============================================================================

class ForceByIds(BaseModel):
    kind: Literal["ids"] = "ids"
    unit_ids: list[int]


class ForceByFilter(BaseModel):
    kind: Literal["filter"] = "filter"
    owner: Literal["self", "enemy", "any"] = "self"
    unit_kind: Optional[str] = None
    hp_below: Optional[float] = None
    hp_above: Optional[float] = None
    harass_capable: Optional[bool] = None
    combat_mobile: Optional[bool] = None
    prefer: Literal["strongest", "fastest", "healthiest", "any"] = "strongest"


Force = Union[ForceByIds, ForceByFilter]


# ============================================================================
# Target selectors
# ============================================================================

class TargetById(BaseModel):
    kind: Literal["id"] = "id"
    actor_id: int


class TargetByPos(BaseModel):
    kind: Literal["pos"] = "pos"
    pos: Vec2


class TargetByName(BaseModel):
    kind: Literal["named"] = "named"
    name: NamedTarget


Target = Union[TargetById, TargetByPos, TargetByName]


# ============================================================================
# Intents
# ============================================================================

class IntentAttack(BaseModel):
    """Spawn an Assault squad against target. Engine FSM owns execution."""
    intent: Literal["attack"] = "attack"
    force: Force
    target: Target


class IntentReport(BaseModel):
    """Read-only snapshot. Interpreter returns narrative text."""
    intent: Literal["report"] = "report"
    what: ReportWhat = "battlefield"


class IntentRaw(BaseModel):
    """Escape hatch — list raw atomic MCP calls. Use rarely."""
    intent: Literal["raw"] = "raw"
    atomic_calls: list[dict]


# --- Coordless squad intents -------------------------------------------------
# Each routes to a spawn_squad squad_type; the interpreter resolves the named
# field into the coordinates / waypoints / escortee actor that spawn_squad
# needs. The LLM never produces a coordinate.

PatrolRoute = Literal[
    "base_perimeter",   # loop around own base
    "front_line",       # loop between own and enemy centre
    "east_lane", "west_lane", "north_lane", "south_lane",  # edge lanes
    "center_loop",      # loop around map centre
]

Escortee = Literal[
    "mcv",              # the MCV
    "harvester",        # nearest own harvester
    "nearest_vehicle",  # nearest own vehicle
    "nearest_infantry", # nearest own infantry
]


class IntentDefend(BaseModel):
    """Spawn a Protection squad holding a named place."""
    intent: Literal["defend"] = "defend"
    force: Force
    where: TargetByName = TargetByName(name="self_base")


class IntentHarass(BaseModel):
    """Spawn a Harass squad against the enemy economy area."""
    intent: Literal["harass"] = "harass"
    force: Force
    target: TargetByName = TargetByName(name="enemy_base")


class IntentScout(BaseModel):
    """Spawn an Explore squad seeded at a named place."""
    intent: Literal["scout"] = "scout"
    force: Force
    where: TargetByName = TargetByName(name="enemy_base")


class IntentPatrol(BaseModel):
    """Spawn a Patrol squad over a named route (interpreter makes waypoints)."""
    intent: Literal["patrol"] = "patrol"
    force: Force
    route: PatrolRoute = "base_perimeter"


class IntentEscort(BaseModel):
    """Spawn an Escort squad shadowing a named friendly unit."""
    intent: Literal["escort"] = "escort"
    force: Force
    escortee: Escortee = "mcv"


Intent = Union[IntentAttack, IntentReport, IntentRaw,
               IntentDefend, IntentHarass, IntentScout,
               IntentPatrol, IntentEscort]
IntentUnion = Intent


def parse_intent(payload: dict) -> Intent:
    """Parse a raw dict into a typed Intent."""
    typ = payload.get("intent")
    mapping = {
        "attack": IntentAttack,
        "report": IntentReport,
        "raw": IntentRaw,
        "defend": IntentDefend,
        "harass": IntentHarass,
        "scout": IntentScout,
        "patrol": IntentPatrol,
        "escort": IntentEscort,
    }
    if typ not in mapping:
        raise ValueError(f"unknown intent type: {typ!r}. valid: {sorted(mapping.keys())}")
    return mapping[typ].model_validate(payload)
