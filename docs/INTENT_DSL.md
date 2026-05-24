# Intent DSL — LLM prompt reference

> **核心规则**: 玩家下战略意图, **不要拼 atomic** (build/train/move/attack).
> 三个顶层工具按职责分工, interpreter 翻底层 order.
>
> **填字段不创造**: 每个字段值都从下面的枚举里选. 不要发明.

---

## 核心原则 (2026-05-23 重构)

1. **玩家拥有信息 + 经济** — 建造 / 探路 / 收资源都是玩家手控. LLM 不下生产令.
2. **LLM 拥有战术** — 把 NL 意图翻成 intent_json, 一句 dispatch_intent 调下去.
3. **Daemon 拥有循环** — 持续行为 (patrol/contain/defend + objective 派的 cycle harass) 由 mission daemon 接管, LLM 不轮询. 单次 intent (harass/escort/diversion) 打完即结束.
4. **LLM 不做数值分析** — 不算坐标 / 距离 / HP 阈值 / 单位 id, 让解释器处理.

---

## 顶层工具 (3 个)

| 工具 | 职责 | 触发 |
|---|---|---|
| `set_alert_state(level)` | 5 级警戒, 调 daemon 主循环 + 防御反应力 | 玩家提及"切 alert / 戒备 / 全员紧急" |
| `set_objective(name, ...)` | 战略赢法目标, LLM 据此偏向建议 | 玩家声明"目标=守 10 分钟 / 拆敌总部 / 卡中场" |
| `dispatch_intent(intent_json)` | 战术意图 (15 个 intent) | 所有具体行动 |

> `set_alert_state` 和 `set_objective` 正交 — alert 决定"反应有多激烈", objective
> 决定"为什么而打". 玩家少说话就用默认 (`alert=normal`, `objective=destroy_fact`).

---

## Intent 类型清单 (15 个)

| intent | 用途 | 备注 |
|---|---|---|
| `attack` | 进攻指定目标 | 一次性 |
| `defend` | 守某区域 | **挂 daemon** — 持续巡防 |
| `retreat` | 撤退到某地 | 一次性 |
| `regroup` | 集结到某点 | 一次性 |
| `scout` | 一次性侦察 | 一次性 |
| `pincer` | 钳形夹击 | 一次性 |
| `feint` | 佯攻 (推到接火停) | 一次性 |
| `set_stance` | 改交战姿态 | 一次性 |
| `report` | 询战况 (只读) | 一次性 |
| **`harass`** | **单次骚扰** (默认 cycle=false) | daemon — engage→withdraw→end. 长效走 objective harass_economy |
| **`patrol`** | **路径点循环视野** | **daemon** — 循环走 waypoints |
| **`escort`** | **护送指定单位** | **daemon** — 跟随 + 拦敌 |
| **`contain`** | **卡敌出口** | **daemon** — 守 chokepoint |
| **`diversion`** | **佯攻 + 偷家协调** | **daemon** — 双队联动 |
| `raw` | 兜底 atomic | 平时**不用** |

---

## Alert State — 5 级警戒

```jsonc
set_alert_state(level: "green" | "yellow" | "orange" | "red" | "black")
```

| level | 含义 | daemon 行为 |
|---|---|---|
| `green` | 和平期, 探路 / 跑经济 | scout daemon 低频, 自动防御只反击靠基地的敌 |
| `yellow` | 警戒, 默认 | 标准 scout, 自动防御标准半径 |
| `orange` | 临战, 敌侦察 / 小股骚扰 | scout 加频, 防御半径放大, 闲兵自动 regroup |
| `red` | 战时, 主力交火 | 全员 stance 升 ReturnFire+, retreat 阈值升高 (残血主动撤) |
| `black` | 绝地, 基地危急 | 全员 AttackAnything, 防御半径覆盖整个基地, 取消所有非防御 mission |

切换语义:
- partial — 只发 level, daemon 立即重排自动行为
- 与 alert 状态正交的 mission (harass/patrol/escort) 保留, 仅调整其"撤退阈值 / 接战半径"
- 玩家可手动 set_stance 覆盖, alert state 不强夺玩家直接令

---

## Objective — 战略赢法

```jsonc
set_objective(
  name: "destroy_fact" | "destroy_enemy" | "harass_economy" | "survive_until_tick" | "control_map_center",
  tick: <int>,                  // 仅 survive_until_tick 用
  region: <Region>              // 仅 control_map_center 用 (默认地图正中)
)
```

| objective | 含义 | LLM 偏向 |
|---|---|---|
| `destroy_fact` | 拆敌建造场 (默认) | 推荐 attack/pincer, 目标 enemy_fact. 不自动派 mission |
| `destroy_enemy` | 总动员灭一切 | **daemon 自动派 cycle attack** 推 enemy_fact, 新训 combat-mobile 自动加入, target 死自动重选 (fact → 最近建筑). 选了这个就**别再手 dispatch attack** |
| `harass_economy` | 切敌经济, 不必决战 | **daemon 自动持续 cycle harass** 敌经济区, 自动吸新 harass-capable, 自动重选目标 |
| `survive_until_tick` | 撑到 tick X | 推荐 defend/contain + alert=orange |
| `control_map_center` | 卡地图中场 | 推荐 contain + patrol |

> objective 是"软指引" — LLM 看到玩家的 objective 后, 主动建议 alert / mission 组合.
> 不是硬规则, 玩家随时可发任意 intent.

---

## 共用字段类型

### `Force` — 谁动 (3 种 kind)

```jsonc
// 1. 按 group name
{"kind": "group", "name": "north" | "center" | "south" | "all" | "mobile" | "everything" | <custom>}
//    all       = combat-mobile self units (excludes harv/mcv/buildings)
//                — "全军 / 全军出击 / all units" 玩家说法都映射到这
//    mobile    = all 的别名
//    everything = 字面意思所有 owned actor (含 harv + 建筑).
//                逃生口; 通常不是对的选择.

// 2. 按 actor id 列
{"kind": "ids", "unit_ids": [12, 17, 33]}

// 3. 按属性 filter
{"kind": "filter",
 "owner": "self" | "enemy" | "any",     // 默认 self
 "unit_kind": "2tnk",                    // 可选, OpenRA actor name 全小写
 "hp_below": 0.3,                        // 可选 0..1
 "hp_above": 0.7,                        // 可选
 "in_group": "north",                    // 可选, 限定群内
 "harass_capable": true,                 // 展开为 {jeep, ftrk, dog, e3, apc, 1tnk}
 "combat_mobile": true,                  // 全部战斗机动 (排 harv/mcv/buildings). destroy_enemy 用
 "prefer": "strongest"}                  // 选拣序: strongest(默认按 priority 选重坦先) | fastest(快单位) | healthiest(满血先) | any(actor_id 序)
```

`harass_capable=true` 是语义快捷: 选低 HP / 高机动单位, 适合骚扰/绕后.

### `Target` — 打谁/去哪 (3 种 kind)

```jsonc
{"kind": "id", "actor_id": 88}
{"kind": "pos", "pos": {"x": 84, "y": 57}}
{"kind": "named", "name": <NamedTarget>}
```

`NamedTarget` 枚举:
- `enemy_fact` — 敌建造场 (最优先), 找不到退到任意敌建筑
- `enemy_base` — 敌单位中心
- `enemy_center` — 敌主力中心 (同上)
- `self_base` — 你的建造场或自方中心
- `self_center` — 你方主力中心
- `nearest_enemy` — 离 force 最近的敌
- `nearest_enemy_unit`
- `nearest_enemy_structure`

### `Region` — 守哪/侦哪 (3 种 kind)

```jsonc
{"kind": "around", "center": <NamedTarget>, "radius": 10}   // center 直接是字符串, 比如 "self_base"
{"kind": "rect", "x1": 30, "y1": 30, "x2": 50, "y2": 50}
{"kind": "named", "name": "self_base_perimeter" | "map_center" | "enemy_approach_lanes"}
```

### `Approach` 枚举 (attack 用)

- `frontal` — 直推
- `flank_left` / `flank_right` — 左/右翼包抄 (途径侧路 waypoint)
- `split` — 队伍劈两半, 一半正面一半右翼
- `charge` — 极速突进, 攻击姿态全开
- `cautious` — 谨慎接战, 距敌 0.7×射程停, ReturnFire 姿态

### `Stance` 枚举

- `HoldFire` — 不开火
- `ReturnFire` — 被打才还击
- `Defend` — 防御 (默认)
- `AttackAnything` — 主动攻击一切

### `Urgency` 枚举 (语义, 暂不影响行为)

- `urgent` / `normal` / `sustained`

### `Report.what` 枚举

- `battlefield` — 全局概览
- `groups` — 所有群
- `group_north` / `group_center` / `group_south` — 单群
- `enemy` — 敌情
- `enemy_intent` — 敌人战术分类 (tank_rush/swarm/air/turtle/...)
- `threats` — 当前威胁
- `minimap` — 截图
- `resources` — 资源/电力

---

## 一次性 Intent 例子

### A. 直接攻击

```jsonc
// 玩家: "用 north 群直推敌总部"
{"intent": "attack",
 "force": {"kind": "group", "name": "north"},
 "target": {"kind": "named", "name": "enemy_fact"},
 "approach": "frontal",
 "urgency": "normal"}
```

### B. 左翼包抄

```jsonc
// 玩家: "south 群左翼包敌"
{"intent": "attack",
 "force": {"kind": "group", "name": "south"},
 "target": {"kind": "named", "name": "enemy_fact"},
 "approach": "flank_left"}
```

### C. 钳形

```jsonc
// 玩家: "南北夹击中路硬推"
{"intent": "pincer",
 "left": {"kind": "group", "name": "north"},
 "right": {"kind": "group", "name": "south"},
 "target": {"kind": "named", "name": "enemy_fact"},
 "rendezvous_dist": 8}
```

### D. 佯攻 (单次)

```jsonc
// 玩家: "center 群假打吸引"
{"intent": "feint",
 "force": {"kind": "group", "name": "center"},
 "target": {"kind": "named", "name": "enemy_base"}}
```

### E. 守 (daemon)

```jsonc
// 玩家: "center 群守基地周围"
{"intent": "defend",
 "force": {"kind": "group", "name": "center"},
 "region": {"kind": "around", "center": "self_base", "radius": 12},
 "stance": "Defend"}
```

defend 现在挂 daemon: 单位会自动散开守半径, 阵亡补员从 force.filter / group 拉, 退出条件由 daemon 监控.

### F. 残血撤退

```jsonc
// 玩家: "把残血的拉回基地"
{"intent": "retreat",
 "force": {"kind": "filter", "owner": "self", "hp_below": 0.3},
 "to": {"kind": "named", "name": "self_base"}}
```

### G. 集中坦克突进

```jsonc
// 玩家: "所有坦克全力压上去打他"
{"intent": "attack",
 "force": {"kind": "filter", "owner": "self", "unit_kind": "2tnk"},
 "target": {"kind": "named", "name": "enemy_fact"},
 "approach": "charge"}
```

### H. 询战况

```jsonc
{"intent": "report", "what": "battlefield"}
{"intent": "report", "what": "group_south"}
{"intent": "report", "what": "enemy"}
{"intent": "report", "what": "enemy_intent"}
```

### I. 改姿态

```jsonc
{"intent": "set_stance",
 "force": {"kind": "group", "name": "all"},
 "stance": "Defend"}
```

### J. 侦察 (单次)

```jsonc
// 玩家: "派几个去看敌方阵地"
{"intent": "scout",
 "force": {"kind": "group", "name": "center"},
 "region": {"kind": "around", "center": "enemy_base", "radius": 6}}
```

---

## Daemon Intent 详细规格 (5 个)

> 这 5 个 intent 注册 mission 到 mission daemon, daemon 每 ~0.6s 检查一次,
> LLM **不需要轮询 / 不需要管循环退出**. 玩家说停就 `cancel_mission(id)` 或
> 通过 `set_alert_state("black")` 全清.

### harass — 单次骚扰 (默认一次性)

```jsonc
{"intent": "harass",
 "force": {"kind": "filter", "harass_capable": true},
 "region": {"kind": "around", "center": "enemy_base", "radius": 8},
 "withdraw_hp_threshold": 0.6,         // float 0..1, 任一单位 HP 跌破即整队撤
 "reengage_hp_threshold": 0.85,        // float 0..1, 撤完养到此才再发动 (仅 cycle=true 时用)
 "withdraw_to": {"kind": "named", "name": "self_base"},
 "cycle": false,                       // false = 打一轮停 (默认), true = 循环
 "max_force_size": null}
```

daemon 状态机: `engaging` → `withdrawing` → 结束 (cycle=false).

**默认行为**: 打一轮, 撤回, mission 结束, 单位归玩家. **不会自动吸收新训单位**.

**长效骚扰**走 objective: `set_objective("harass_economy")`. daemon 内部派 cycle
型 harass mission, 自动重选敌经济目标, 自动吸新 harass-capable 单位.

### patrol — 路径点循环视野

```jsonc
{"intent": "patrol",
 "force": {"kind": "group", "name": "scouts"},
 "waypoints": [{"x": 40, "y": 50},
               {"x": 80, "y": 50},
               {"x": 80, "y": 80},
               {"x": 40, "y": 80}],
 "cycle": true,                        // false = 走完停, true = 循环
 "engage_on_contact": "scout"}         // scout = 见敌即报+绕开, hold = 守路径, attack = 接战
```

### escort — 护送指定单位

```jsonc
{"intent": "escort",
 "force": {"kind": "group", "name": "north"},
 "escortee_id": 87,                    // 被护送的 actor (如 MCV / harv / spy)
 "destination": {"kind": "pos", "pos": {"x": 75, "y": 30}},
 "engage_radius": 6}                   // 离开 escortee 多远内可主动还击
```

daemon: force 跟着 escortee 移动, 任何 engage_radius 内的敌都还击, 到目的地后 mission 结束.

### contain — 卡敌出口

```jsonc
{"intent": "contain",
 "force": {"kind": "group", "name": "south"},
 "chokepoint": {"kind": "pos", "pos": {"x": 50, "y": 60}},
 "radius": 4,                          // 距 chokepoint 多远内反击
 "stance": "AttackAnything"}
```

force 散开在 chokepoint 周围, daemon 不让单位走远, 任何 radius 内的敌都被攻.

### diversion — 佯攻 + 偷家协调

```jsonc
{"intent": "diversion",
 "feint_force": {"kind": "group", "name": "center"},
 "feint_target": {"kind": "named", "name": "enemy_base"},
 "raid_force": {"kind": "filter", "harass_capable": true},
 "raid_target": {"kind": "named", "name": "enemy_fact"},
 "raid_approach": "flank_left"}
```

daemon 双队联动: feint_force 推进到接火距离按 cautious 守位 (吸引敌反击),
raid_force 同时从侧翼接近 raid_target, 直到 raid 命中或被发现.

---

## Mission Force — 静态 vs 动态

mission daemon 的 force 有两种解析模式:

### 静态 (默认 ids / 单次 intent)
- force 注册时**锁定** actor_id 列表
- 阵亡不补员, mission 结束条件: 全员死亡或显式 cancel
- 适合: 一次性 attack / scout / pincer

### 动态 (cycle 型默认 / filter 或 group)
- force = filter 或 group 时, daemon **每 tick 重解**
- 新单位自动加入 (符合 filter 或群名), 阵亡自动剔除
- 可用 `max_force_size: N` 加上限 (daemon 优先选 hp 高的)
- 适合: harass / patrol / contain / defend

### Pending Queue
- force 解析返 0 (没单位符合) 时, mission 不报错, 进入 **pending** 状态
- daemon 持续轮询新单位, 一有符合的就启动
- 响应里返 `pending_id`, 玩家可 `cancel_mission(pending_id)` 清掉

---

## NL → DSL 速查

| 玩家说 | 工具 + intent |
|---|---|
| "切 alert / 戒备" | `set_alert_state("orange"/"red")` |
| "全员紧急" | `set_alert_state("black")` |
| "和平期 / 经济期" | `set_alert_state("green")` |
| "目标守 10 分钟" | `set_objective("survive_until_tick", tick=18000)` |
| "目标拆敌总部" | `set_objective("destroy_fact")` |
| "目标切敌经济" | `set_objective("harass_economy")` |
| "目标卡中场" | `set_objective("control_map_center")` |
| "硬推 / 直接打" | `dispatch_intent(attack, frontal)` |
| "包抄 / 绕侧 / 绕后" | `dispatch_intent(attack, flank_left/right)` |
| "夹击 / 钳形" | `dispatch_intent(pincer)` |
| "佯攻 / 假打 / 牵制" | `dispatch_intent(feint)` |
| "突击 / 不顾一切 / 冲" | `dispatch_intent(attack, charge)` |
| "稳一点 / 拉距离" | `dispatch_intent(attack, cautious)` |
| "守 / 防御 / 不动" | `dispatch_intent(defend)` |
| "撤 / 回来 / 救" | `dispatch_intent(retreat)` |
| "集结 / 集合" | `dispatch_intent(regroup)` |
| "侦察 (一次) / 看看" | `dispatch_intent(scout)` |
| **"打一波他的矿"** (单次) | **`dispatch_intent(harass, cycle=false)`** |
| **"持续骚扰 / 切断经济"** (长效) | **`set_objective("harass_economy")`** |
| **"巡逻 A B C"** | **`dispatch_intent(patrol)`** |
| **"护送 MCV 去 X"** | **`dispatch_intent(escort)`** |
| **"卡死敌出口 / 堵路口"** | **`dispatch_intent(contain)`** |
| **"正面佯攻偷家"** | **`dispatch_intent(diversion)`** |
| "现在啥样 / 战况" | `dispatch_intent(report battlefield)` |
| "敌人在干啥" | `dispatch_intent(report enemy_intent)` |
| "残血的 / 红血" | filter `hp_below: 0.3` |
| "所有坦克" | filter `unit_kind: 2tnk` |
| "骚扰单位" | filter `harass_capable: true` |
| "全员" | force `name: "all"` |

---

## 设计原则 (LLM 自检)

1. ❌ **不要**用 `raw` intent 除非 DSL 完全无法表达
2. ❌ **不要**自己算坐标 / waypoint / 距离阈值
3. ❌ **不要**自己拆"先 train 再 move 再 attack" — 走对应 intent
4. ❌ **不要**轮询 daemon intent — daemon 自己跑, 玩家不问就别报
5. ✅ **必须**从枚举里选, 不发明 approach / stance / NamedTarget / alert level 值
6. ✅ **不确定** force 是哪个 group → 先发 `{intent:"report", what:"groups"}` 看
7. ✅ **不确定** target → 用 `nearest_enemy_*` 或 `enemy_fact` 这些 named
8. ✅ 玩家说"巡逻 / 护送 / 卡 / 佯攻偷家" → 对应 daemon intent. **"骚扰"区分**: 单次 → harass intent; **长效 → set_objective("harass_economy")** 而非反复 dispatch harass
9. ✅ 玩家声明战略目标 → 先 `set_objective`, 再考虑 alert + mission 组合

---

## 接收响应

```jsonc
// 成功:
{
  "ok": true,
  "narrative": "frontal attack 9 unit(s) → actor 84",
  "actions_taken": [
    {"cmd": {...}, "resp": {"ok": true, ...}}
  ],
  "mission_id": 17                     // daemon intent 时返回, 可 cancel_mission(17)
}

// 失败 — force 解析为空:
{
  "ok": false,
  "narrative": "no units match filter, queued as pending",
  "error": "force_resolution_empty",
  "pending_id": 42                     // 排队, 有单位时自动启动
}
```

把 `narrative` 转述给玩家. `actions_taken` 通常不必让玩家看.

---

## 已下线工具 (历史参考)

2026-05-23 以来从 MCP 隐藏的工具:
- 经济: `build` / `train` / `sell` / `deploy` / `capture` — 玩家 UI 操作
- 单 unit atomic: `move` / `attack` / `stop` / `set_stance` / `scatter` — 走 `dispatch_intent`
- 编组 atomic: `move_group` / `attack_group` / `stance_group` — 走 `dispatch_intent` + group force
  (LLM 直发 atomic 会跟 daemon cohesion/retarget 冲突, 详见 memory feedback_no_micromanagement)

如果你看到旧文档/示例还引用上面任一工具, 是过期内容.

---

**End of INTENT_DSL.md**
