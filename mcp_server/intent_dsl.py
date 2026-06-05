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


Intent = Union[IntentAttack, IntentReport, IntentRaw]
IntentUnion = Intent


def parse_intent(payload: dict) -> Intent:
    """Parse a raw dict into a typed Intent."""
    typ = payload.get("intent")
    mapping = {
        "attack": IntentAttack,
        "report": IntentReport,
        "raw": IntentRaw,
    }
    if typ not in mapping:
        raise ValueError(f"unknown intent type: {typ!r}. valid: {sorted(mapping.keys())}")
    return mapping[typ].model_validate(payload)
