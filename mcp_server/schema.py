"""
Atomic command schema for OpenRA MCP bridge.

These pydantic models define the JSON wire protocol between the Python MCP
server and the OpenRA C# MCPBridgeTrait. Each command is a JSON object with
a 'type' discriminator and command-specific fields.
"""

from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


class Vec2(BaseModel):
    """World cell position. OpenRA uses CPos (cell x, y)."""
    x: int
    y: int


# ============================================================================
# Commands (Claude Code -> OpenRA)
# ============================================================================

class CmdGetState(BaseModel):
    type: Literal["get_state"] = "get_state"
    include_enemies: bool = True


class CmdListUnits(BaseModel):
    type: Literal["list_units"] = "list_units"
    owner: Optional[str] = None  # None = all, "self" = player, "enemy" = hostile
    kind: Optional[str] = None   # e.g. "Soldier", "MCV", "Refinery"


class CmdFindUnit(BaseModel):
    type: Literal["find_unit"] = "find_unit"
    description: str  # natural-ish text, server resolves to actor ids


class CmdBuild(BaseModel):
    type: Literal["build"] = "build"
    structure: str          # e.g. "Refinery", "Barracks"
    near: Optional[Vec2] = None
    count: int = 1


class CmdTrain(BaseModel):
    type: Literal["train"] = "train"
    unit: str               # e.g. "Soldier"
    count: int = 1
    factory_id: Optional[int] = None  # specific factory, else any


class CmdMove(BaseModel):
    type: Literal["move"] = "move"
    unit_ids: list[int]
    target: Vec2
    attack_move: bool = False


class CmdAttack(BaseModel):
    type: Literal["attack"] = "attack"
    unit_ids: list[int]
    target_id: int


class CmdSetStance(BaseModel):
    type: Literal["set_stance"] = "set_stance"
    unit_ids: list[int]
    stance: Literal["HoldFire", "ReturnFire", "Defend", "AttackAnything"]


class CmdCapture(BaseModel):
    """Issue `CaptureActor` order — engineer captures a Capturable building.

    Walks adjacent then runs the capture delay (~8 s for default e6). On
    completion the building's owner changes to the engineer's player and the
    engineer is consumed (Captures.ConsumedByCapture = true).
    """
    type: Literal["capture"] = "capture"
    unit_ids: list[int]
    target_id: int


class CmdPause(BaseModel):
    type: Literal["pause"] = "pause"


class CmdResume(BaseModel):
    type: Literal["resume"] = "resume"


class CmdScreenshot(BaseModel):
    type: Literal["screenshot"] = "screenshot"


class CmdDeploy(BaseModel):
    """Deploy MCV / undeploy / morph transforming units (DeployTransform order)."""
    type: Literal["deploy"] = "deploy"
    unit_ids: list[int]


class CmdStop(BaseModel):
    type: Literal["stop"] = "stop"
    unit_ids: list[int]


class CmdSell(BaseModel):
    type: Literal["sell"] = "sell"
    unit_ids: list[int]  # building actor ids


class CmdScatter(BaseModel):
    type: Literal["scatter"] = "scatter"
    unit_ids: list[int]


# ============================================================================
# Group commands — name a cohort of player units (north / center / south) and
# act on the cohort. Auto-initialized on first list_groups by splitting along
# Y (default) or X. Rebalance to re-partition.
# ============================================================================

class CmdListGroups(BaseModel):
    type: Literal["list_groups"] = "list_groups"


class CmdMoveGroup(BaseModel):
    type: Literal["move_group"] = "move_group"
    group: str
    target: Vec2
    attack_move: bool = False


class CmdAttackGroup(BaseModel):
    type: Literal["attack_group"] = "attack_group"
    group: str
    target_id: int


class CmdStanceGroup(BaseModel):
    type: Literal["stance_group"] = "stance_group"
    group: str
    stance: Literal["HoldFire", "ReturnFire", "Defend", "AttackAnything"]


class CmdAssignToGroup(BaseModel):
    type: Literal["assign_to_group"] = "assign_to_group"
    group: str
    unit_ids: list[int]


class CmdRebalanceGroups(BaseModel):
    type: Literal["rebalance_groups"] = "rebalance_groups"
    count: int = 3
    axis: Literal["x", "y"] = "y"


Command = Union[
    CmdGetState, CmdListUnits, CmdFindUnit,
    CmdBuild, CmdTrain, CmdMove, CmdAttack, CmdSetStance, CmdCapture,
    CmdPause, CmdResume, CmdScreenshot,
    CmdDeploy, CmdStop, CmdSell, CmdScatter,
    CmdListGroups, CmdMoveGroup, CmdAttackGroup, CmdStanceGroup,
    CmdAssignToGroup, CmdRebalanceGroups,
]


# ============================================================================
# Responses (OpenRA -> Claude Code)
# ============================================================================

class UnitInfo(BaseModel):
    id: int
    kind: str
    owner: str
    pos: Vec2
    hp_pct: float
    morale_pct: Optional[float] = None
    activity: Optional[str] = None  # current order summary


class WorldState(BaseModel):
    tick: int
    paused: bool
    self_cash: int
    self_power: int  # net
    self_units: list[UnitInfo]
    enemy_units: list[UnitInfo]
    map_name: str
    map_size: Vec2


class CommandResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    issued_orders: int = 0  # how many actor orders were dispatched
    affected_unit_ids: list[int] = Field(default_factory=list)
    state: Optional[WorldState] = None
    units: Optional[list[UnitInfo]] = None  # for list_units / find_unit
    screenshot_b64: Optional[str] = None    # for screenshot
