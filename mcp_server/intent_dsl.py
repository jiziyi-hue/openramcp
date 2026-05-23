"""
Intent DSL — pydantic schemas for the structured commands Claude emits.

Design rules:
  1. Every field with semantic meaning is a Literal[...] enum, NOT free text.
  2. The LLM (sonnet or flash) only fills field values from the enumerated
     choices. No coordinate math, no threshold guessing.
  3. The interpreter (mcp_server/interpreter.py) maps each intent to a
     deterministic sequence of atomic MCP commands.
  4. Unresolvable inputs raise — the LLM picks another enum value, does not
     improvise.

Discriminator: every intent has a `type` literal so pydantic + the interpreter
can dispatch with no ambiguity.
"""

from __future__ import annotations

from typing import Literal, Optional, Union
from pydantic import BaseModel, Field

from .schema import Vec2


# ============================================================================
# Reusable enums
# ============================================================================

Stance = Literal["HoldFire", "ReturnFire", "Defend", "AttackAnything"]

Approach = Literal[
    "frontal",      # 直推
    "flank_left",   # 左翼包抄
    "flank_right",  # 右翼包抄
    "split",        # 一队分二, 两面夹
    "charge",       # 极速突进 (忽略路上小目标)
    "cautious",     # 谨慎, 距敌 0.7×射程接火
]

Urgency = Literal["urgent", "normal", "sustained"]

EconomyFocus = Literal["tank", "infantry", "air", "balanced", "turtle"]
EconomyIntensity = Literal["low", "normal", "high"]

ReportWhat = Literal[
    "battlefield",   # 全局概览
    "group_north",   # 指定群状态
    "group_center",
    "group_south",
    "groups",        # 所有群
    "enemy",         # 敌情
    "enemy_intent",  # 敌方策略分类 (tank_rush / infantry_swarm / air / ...)
    "threats",       # 当前威胁列表
    "minimap",       # 截图
    "resources",     # 资源 / 经济
    "capabilities",  # 列出 5 模板 + 可用 enum + 当前 strategy
    "strategy",      # 当前 bot strategy state
]


# ============================================================================
# Strategy enums (used by IntentSetStrategy + capabilities report)
# ============================================================================

StrategyTemplate = Literal[
    # P1 — core 5
    "tank_rush",        # 重坦量产 + 早期推
    "infantry_swarm",   # 步兵海 cheese
    "balanced",         # 默认混编
    "turtle",           # 龟缩高防, 后期决战
    "raid_harass",      # 骚扰, 切敌经济
    # P3 — 4 旗舰
    "tesla_wall",       # 苏方 — 特斯拉墙 + 特斯拉坦克
    "chrono_blitz",     # 盟方 — Chronosphere 闪击重坦
    "siege_arty",       # 火炮 / V2 远程平推
    "paratroop_rain",   # 空军主力 + 空投
]

DefenseState = Literal[
    "passive",          # 不主动反应
    "active",           # 主动派增援
    "full_alert",       # 全军戒备
]

TransitionMode = Literal[
    "soft",             # 自然换 (新令按新模板, 老兵跑完当前 order)
    "hard",             # 清队列 + 解散 squad, 立即重排
    "hybrid",           # 战斗中不动, 闲的立切
]

SpendRatio = Literal["all_eco", "eco_heavy", "balanced", "army_heavy", "all_army"]
ScoutPriority = Literal["off", "low", "normal", "high", "paranoid"]
TechFocus = Literal["none", "tier2", "tier3", "superweapon", "air"]
RetreatThreshold = Literal["never", "low", "normal", "high", "always"]
SupportPowersAuto = Literal["off", "defensive_only", "auto", "aggressive"]
PrimaryObjective = Literal[
    "destroy_enemy",
    "destroy_fact",
    "control_map",
    "survive_until",
    "harass_economy",
]

NamedTarget = Literal[
    "enemy_fact",       # 敌主基地建造场
    "enemy_base",       # 敌基地中心
    "enemy_center",     # 敌主力中心
    "self_base",        # 我方基地
    "self_center",      # 我方主力中心
    "nearest_enemy",    # 离 force 最近的敌
    "nearest_enemy_unit",
    "nearest_enemy_structure",
]

NamedRegion = Literal[
    "self_base_perimeter",
    "map_center",
    "enemy_approach_lanes",
]


# ============================================================================
# Force selectors (谁动)
# ============================================================================

class ForceByGroup(BaseModel):
    """按 group name 选, e.g. 'north', 'center', 'south', 'all'."""
    kind: Literal["group"] = "group"
    name: str


class ForceByIds(BaseModel):
    """按 actor id 列表选."""
    kind: Literal["ids"] = "ids"
    unit_ids: list[int]


class ForceByFilter(BaseModel):
    """按属性 filter 选 (代码解析). 例: HP < 30% 的所有单位."""
    kind: Literal["filter"] = "filter"
    owner: Literal["self", "enemy", "any"] = "self"
    unit_kind: Optional[str] = None           # 例: "2tnk"
    hp_below: Optional[float] = None          # 0..1
    hp_above: Optional[float] = None
    in_group: Optional[str] = None            # 限定群内


Force = Union[ForceByGroup, ForceByIds, ForceByFilter]


# ============================================================================
# Target selectors (打谁/去哪)
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
# Region selectors (守哪/侦哪)
# ============================================================================

class RegionAround(BaseModel):
    kind: Literal["around"] = "around"
    center: NamedTarget               # 比如 "self_base"
    radius: int = 10                  # cells


class RegionRect(BaseModel):
    kind: Literal["rect"] = "rect"
    x1: int
    y1: int
    x2: int
    y2: int


class RegionNamed(BaseModel):
    kind: Literal["named"] = "named"
    name: NamedRegion


Region = Union[RegionAround, RegionRect, RegionNamed]


# ============================================================================
# Intents
# ============================================================================

class IntentAttack(BaseModel):
    intent: Literal["attack"] = "attack"
    force: Force
    target: Target
    approach: Approach = "frontal"
    urgency: Urgency = "normal"


class IntentDefend(BaseModel):
    intent: Literal["defend"] = "defend"
    force: Force
    region: Region
    stance: Stance = "Defend"


class IntentRetreat(BaseModel):
    intent: Literal["retreat"] = "retreat"
    force: Force
    to: Union[TargetByName, TargetByPos]


class IntentRegroup(BaseModel):
    """集结到一点, 准备后续行动."""
    intent: Literal["regroup"] = "regroup"
    force: Force
    at: Union[TargetByName, TargetByPos]


class IntentScout(BaseModel):
    """侦察区域. 派少量单位 attack_move 过去."""
    intent: Literal["scout"] = "scout"
    force: Force
    region: Region


class IntentEconomy(BaseModel):
    """调整 bot macro module 偏好 (路 D 后才真起效, 现在仅记录)."""
    intent: Literal["economy"] = "economy"
    focus: EconomyFocus = "balanced"
    intensity: EconomyIntensity = "normal"


class IntentBotFocus(BaseModel):
    """提示 bot SquadManager 把攻击 squad 派去某点 (路 D 实现后真起效)."""
    intent: Literal["bot_focus"] = "bot_focus"
    target: Target


class IntentPincer(BaseModel):
    """两路夹击. left/right 同时压向 target, 在 rendezvous_dist 距离集结再压."""
    intent: Literal["pincer"] = "pincer"
    left: Force
    right: Force
    target: Target
    rendezvous_dist: int = 8           # 距 target 多远集结


class IntentFeint(BaseModel):
    """佯攻: force 推到接火距离即停, 引敌注意, 不真冲."""
    intent: Literal["feint"] = "feint"
    force: Force
    target: Target


class IntentSetStance(BaseModel):
    intent: Literal["set_stance"] = "set_stance"
    force: Force
    stance: Stance


class IntentReport(BaseModel):
    """读取战况. 解释器返叙事文本, 不发 atomic."""
    intent: Literal["report"] = "report"
    what: ReportWhat = "battlefield"


class IntentRaw(BaseModel):
    """逃生口. DSL 不覆盖时, LLM 直接列底层 atomic. 应少用."""
    intent: Literal["raw"] = "raw"
    atomic_calls: list[dict]           # 形如 [{tool: "move", args: {...}}, ...]


class IntentSetStrategy(BaseModel):
    """Push partial strategy patch to the bot.

    All business fields are Optional — only set fields take effect.
    Unset fields leave the bot's existing state untouched.

    The C# StrategyControllerBotModule receives this patch, swaps the active
    template condition (via GrantCondition), and applies non-template fields
    (defense_state, attack_focus, etc) to its mutable state. Existing yaml-
    gated bot modules (BaseBuilder/UnitBuilder/SquadManager per template)
    pick up the new condition on next tick.
    """
    intent: Literal["set_strategy"] = "set_strategy"

    # ---- macro preset / kill-switch -----------------------------------
    template: Optional[StrategyTemplate] = None
    macro_paused: Optional[bool] = None

    # ---- combat posture -----------------------------------------------
    defense_state: Optional[DefenseState] = None
    attack_focus: Optional[Target] = None
    retreat_threshold: Optional[RetreatThreshold] = None

    # ---- economy / production -----------------------------------------
    spend_ratio: Optional[SpendRatio] = None
    tech_focus: Optional[TechFocus] = None
    counter_pick: Optional[bool] = None

    # ---- intel ---------------------------------------------------------
    scout_priority: Optional[ScoutPriority] = None

    # ---- harass / specialist roles ------------------------------------
    harass_focus: Optional[Target] = None

    # ---- powers --------------------------------------------------------
    support_powers_auto: Optional[SupportPowersAuto] = None

    # ---- meta ----------------------------------------------------------
    auto_adapt: Optional[bool] = None
    verbose_reports: Optional[bool] = None
    primary_objective: Optional[PrimaryObjective] = None

    # ---- focus clearing (separate flags because None is "leave unset")
    clear_attack_focus: Optional[bool] = None
    clear_harass_focus: Optional[bool] = None

    # ---- transition control -------------------------------------------
    transition_mode: TransitionMode = "soft"   # always present, default soft


Intent = Union[
    IntentAttack, IntentDefend, IntentRetreat, IntentRegroup, IntentScout,
    IntentEconomy, IntentBotFocus, IntentPincer, IntentFeint,
    IntentSetStance, IntentReport, IntentRaw,
    IntentSetStrategy,
]


def parse_intent(payload: dict) -> Intent:
    """Parse a raw dict (e.g. from JSON) into a typed Intent."""
    typ = payload.get("intent")
    mapping = {
        "attack": IntentAttack,
        "defend": IntentDefend,
        "retreat": IntentRetreat,
        "regroup": IntentRegroup,
        "scout": IntentScout,
        "economy": IntentEconomy,
        "bot_focus": IntentBotFocus,
        "pincer": IntentPincer,
        "feint": IntentFeint,
        "set_stance": IntentSetStance,
        "report": IntentReport,
        "raw": IntentRaw,
        "set_strategy": IntentSetStrategy,
    }
    if typ not in mapping:
        raise ValueError(f"unknown intent type: {typ!r}. valid: {sorted(mapping.keys())}")
    return mapping[typ].model_validate(payload)
