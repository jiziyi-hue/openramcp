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
]


# ============================================================================
# Shared enums (reused by alert-state / objective engines downstream)
# ============================================================================

DefenseState = Literal[
    "passive",          # 不主动反应
    "active",           # 主动派增援
    "full_alert",       # 全军戒备
]

ScoutPriority = Literal["off", "low", "normal", "high", "paranoid"]

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
    """按属性 filter 选 (代码解析). 例: HP < 30% 的所有单位.

    `prefer` controls ordering when max_force_size truncates: pick the
    "best" N units according to the criterion instead of arbitrary actor-id
    order. Defaults to `strongest` (high target_priority value = beefy /
    high-DPS = first picked).
    """
    kind: Literal["filter"] = "filter"
    owner: Literal["self", "enemy", "any"] = "self"
    unit_kind: Optional[str] = None           # 例: "2tnk"
    hp_below: Optional[float] = None          # 0..1
    hp_above: Optional[float] = None
    in_group: Optional[str] = None            # 限定群内
    harass_capable: Optional[bool] = None     # True → only fast/kite-able kinds
                                              #        (jeep/ftrk/dog/e3/apc/1tnk)
                                              #        and explicitly exclude
                                              #        heavy/slow (tnk2-4/arty/v2)
    combat_mobile: Optional[bool] = None      # True → all combat-mobile (excl
                                              #        harv/mcv/buildings).
                                              #        For destroy_enemy etc.
    prefer: Literal["strongest", "fastest", "healthiest", "any"] = "strongest"
    # strongest  — highest target_priority value first (tanks before infantry)
    # fastest    — light units (jeep/dog/e3) first
    # healthiest — highest hp_pct first (don't send wounded into harass)
    # any        — actor-id order (legacy)


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


# ----- daemon-mission intents (long-running, registered into tactical daemon)


class IntentHarass(BaseModel):
    """骚扰: 在敌经济区做打了就跑的循环.

    daemon 跑 engaging → withdrawing → regrouping 状态机. force 优先打
    harv/proc, 任一单位血量低于 withdraw_hp_threshold 整队撤回 withdraw_to,
    avg hp ≥ reengage_hp_threshold 再发动下一轮 (cycle=True).
    """
    intent: Literal["harass"] = "harass"
    force: Force
    region: Region
    withdraw_hp_threshold: float = 0.6
    reengage_hp_threshold: float = 0.85
    withdraw_to: Optional[Union[TargetByName, TargetByPos]] = None  # 默认 self_base
    cycle: bool = False  # 一次性默认: 打一轮就完, 单位归玩家. 长效切经济用 disrupt_economy.
    max_force_size: Optional[int] = None


class IntentPatrol(BaseModel):
    """巡逻: 沿 waypoints 循环走路提供视野.

    contact_stance 决定遇敌姿态 (默认 ReturnFire — 反击但不追). 残血单位
    (hp < 0.4) 自动脱队回家.
    """
    intent: Literal["patrol"] = "patrol"
    force: Force
    waypoints: list[Vec2]
    cycle: bool = True
    contact_stance: Stance = "ReturnFire"


class IntentEscort(BaseModel):
    """护送: force 贴着指定单位移动, 拦截威胁.

    escortee 死 → mission 自动结束 (after-action push).
    """
    intent: Literal["escort"] = "escort"
    force: Force
    escortee_id: int
    destination: Optional[Target] = None      # 终点 (用于偏置)
    escort_radius: int = 4
    engage_radius: int = 6


class IntentContain(BaseModel):
    """卡点: force 守住一个 chokepoint, 半径内打, 不追."""
    intent: Literal["contain"] = "contain"
    force: Force
    chokepoint: Vec2
    radius: int = 4
    stance: Stance = "Defend"


class IntentDiversion(BaseModel):
    """声东击西: 两路同时出 — feint 牵制 + raid 真打.

    daemon 协调时序: 两路同时出, feint 推到 8 格停线 ReturnFire,
    raid 走 raid_approach (flank_left/flank_right) 真打. 任一路 hp < 40%
    撤回 withdraw_to. feint_commits=True 时 raid 接火后 feint 升级到
    AttackAnything 跟进.
    """
    intent: Literal["diversion"] = "diversion"
    feint_force: Force
    feint_target: Target
    raid_force: Force
    raid_target: Target
    raid_approach: Approach = "flank_right"   # 必须 flank_left/flank_right
    feint_commits: bool = False


Intent = Union[
    IntentAttack, IntentDefend, IntentRetreat, IntentRegroup, IntentScout,
    IntentPincer, IntentFeint,
    IntentHarass, IntentPatrol, IntentEscort, IntentContain, IntentDiversion,
    IntentSetStance, IntentReport, IntentRaw,
]

# Alias — older code refers to IntentUnion; both names point to the same Union.
IntentUnion = Intent


def parse_intent(payload: dict) -> Intent:
    """Parse a raw dict (e.g. from JSON) into a typed Intent."""
    typ = payload.get("intent")
    mapping = {
        "attack": IntentAttack,
        "defend": IntentDefend,
        "retreat": IntentRetreat,
        "regroup": IntentRegroup,
        "scout": IntentScout,
        "pincer": IntentPincer,
        "feint": IntentFeint,
        "harass": IntentHarass,
        "patrol": IntentPatrol,
        "escort": IntentEscort,
        "contain": IntentContain,
        "diversion": IntentDiversion,
        "set_stance": IntentSetStance,
        "report": IntentReport,
        "raw": IntentRaw,
    }
    if typ not in mapping:
        raise ValueError(f"unknown intent type: {typ!r}. valid: {sorted(mapping.keys())}")
    return mapping[typ].model_validate(payload)
