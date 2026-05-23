# openra_mcp — 领域术语表

> 活的术语表. 在拷问会话中, 每解决一个术语就在这里更新.
> 实现细节不放这里, 这文件**只**写术语.

---

## 核心设计原则

下面 4 条是系统行为的**不可违反约束**. 任何特性如果违反它们, 必须拒绝
或重新设计.

### 玩家拥有信息

玩家是唯一的信息消费者. 玩家看屏幕, 派侦察, 读小地图, 自己判断要不要打.
LLM **不做**数值分析 (DPS 对比 / 胜率估算 / 兵力计算) 来替玩家拍板.
LLM 只做转译, 中继, 战报叙述 — 不做分析.

引申: 凡是让 LLM 输出深度战术分析的工具 (engagement estimator / threat
ranker 等) 都在范围外. 如果 LLM 需要 ground truth 才能给建议, 那这个建议
本来就该是**玩家**做的, 不该有这个工具.

### 玩家拥有经济

所有花钱决策 — 建建筑 / 训单位 / MCV 部署 / 修建筑 / 卖建筑 / 占领 /
科技路线 — 都属玩家, 通过 OpenRA UI 操作. LLM **零访问**经济工具.

### LLM 拥有战术

移动 / 攻击目标选择 / 队形 / 交战姿态 / 撤退时机 / 任务编排 — 都属 LLM,
通过 `dispatch_intent` 和一小套高层工具 (`set_alert_state` 等) 完成.

### Daemon 拥有循环

任何需要每 tick 重复执行的战术行为 (cohesion 守门 / 自动 retarget /
骚扰循环 / 巡逻循环 / 周界自卫) 都跑在 Python 战术 daemon 里, 不在 LLM.
LLM **注册一次** mission, daemon **执行循环**. LLM 永远不驱动 per-tick
控制循环.

---

## 角色

### 玩家 (Player)

键盘后面的人类. 拥有**经济** (定义见上). 通过下购买决策 + 读 LLM 参谋
的战报 来驱动游戏.

### LLM (Claude)

玩家的战术参谋. 拥有**战术** (定义见上). 把自然语言战略意图翻成
`dispatch_intent` 调用. **不**花钱, **不**排生产.

### Bot (引擎内 C# 模块)

跑在玩家阵营上的后台自动化. **目前只剩 harv 自动采矿**. 不再产建筑或单位.

### 战术 Daemon (Tactical Daemon)

引擎外的 Python 循环 (`mcp_server/tactical.py`), 每 0.6 秒拉一次世界状态.
拥有 cohesion 守门 / 自动 retarget / 周界自卫 / 残血自动撤退 等行为.
被 `attack` / `pincer` intent 隐式调用.

---

## 行动归属

### 经济 *(玩家专属 — LLM 零访问)*

- 建建筑, 含防御工事 (pbox / gun / tsla / sam)
- 训军单位 (e1 / 2tnk / arty 等)
- MCV 部署 + 二基地扩张
- 修复受损建筑
- 卖建筑
- 科技选择 (建哪些高级建筑)
- 设工厂集结点 (rally point)
- 工程师占领建筑

### 战术 *(LLM 专属)*

- 移动已部署单位
- 攻击指定目标 (集火)
- 撤退单位
- 集结单位
- 侦察区域
- 设交战姿态 (HoldFire / ReturnFire / Defend / AttackAnything)
- 钳形 / 佯攻 / 分兵 机动
- 编组 + 重组命名编队

### 自动 *(Bot, 不需要玩家或 LLM 下指令)*

- Harvester 单位自动派到矿场 (HarvesterBotModule)

---

## 概念

### Intent (意图)

LLM 从自然语言提取出来的一个有类型的动作, 通过 `dispatch_intent` 单次
调用发出. Intent 类型: `attack`, `defend`, `retreat`, `regroup`,
`scout`, `pincer`, `feint`, `set_stance`, `report`, `harass`, `patrol`,
`escort`, `contain`, `diversion`, `raw`.

(**已删**: `economy`, `bot_focus`, `set_strategy`.)

### Alert State (战备状态)

军队的全局姿态. 一个命名状态打包 (daemon 参数 + 自动派的 mission 集
+ 默认 stance + 默认 approach). 玩家一句话切换, LLM 转述.

枚举值:

- `peace` — 早期发育, 无防御循环, 单位自由, 无自动 mission
- `watch` — 周界开, 自动派 scout 巡逻, 默认 Defend
- `alert` — 周界 aggressive, 自动派 scout + harass, 撤退阈值 0.5
- `combat` — 主动进攻, 默认 AttackAnything + charge, 无自动 mission
- `lockdown` — 全员回家, 周界 max, 撤退阈值 0.7, 不出战

替换了旧 "Strategy Template" / "doctrine" / "tactical preset" 等概念.
与 **Mission Objective** (见下) 正交.

### Mission Objective (战略目标)

玩家声明的胜利条件. 与战备状态正交. 影响 LLM 的战略建议 (推荐哪个
alert state, 派哪个 mission).

枚举值:

- `destroy_fact` — 摧毁敌方建造场
- `harass_economy` — 饿死敌方经济
- `survive_until_tick(X)` — 撑到某 tick
- `control_map_center` — 控制地图中央

### Daemon Mission (Daemon 任务)

一个长期跑的战术任务, 注册到战术 daemon. 一旦下发, daemon 自己跑, LLM
不再干预. 用 `cancel_assaults` 取消. Mission 类型:

- `Assault` — 协调推进到目标格, 自动 retarget, cohesion
- `HarassMission` — 在敌经济区做打了就跑的循环
- `PatrolMission` — 路径点循环, 提供视野
- `EscortMission` — 跟随指定单位, 拦截威胁
- `DefensePerimeter` — 基地周界自卫 (支持多个并存)
- `ContainmentMission` — 卡敌方出口
- `DiversionMission` — 佯攻 + 同时偷家小队 (一次调用协调)

Mission 接受**静态** force (锁定 actor id 列表) 或**动态** force
(filter 或 group — daemon 每 tick 重新解析, 自动吸收新训的符合单位,
淘汰阵亡单位). 循环型 mission 默认动态.

### Pending Mission (待机任务)

force 解析返 0 (玩家没合适单位) 时, mission 进入 pending. daemon 持续
轮询, 一旦玩家训出匹配的单位就自动启动. 入队时 LLM 告知玩家需要训啥.

### Support Pairing (后勤配对)

Daemon 自动行为. 闲置 medic 单位自动贴近**半径内**最近的低血友军步兵.
闲置 mechanic 自动贴近最近低血友军车辆. 玩家 + LLM 都不下指令 — 跟
harvester 自动采矿一样常驻.

### Force Filter (兵力过滤器)

用于解析"谁去执行"的谓词. 字段: `owner` / `unit_kind` / `hp_below` /
`hp_above` / `in_group` / `harass_capable`. 其中 `harass_capable`
内部展开为 `{jeep, ftrk, dog, e3, apc, 1tnk}` — 快速 + 能拉风筝的单位,
适合打了就跑.

### Named Target (命名目标)

解释器内部解析的符号化目标 (LLM **不算坐标**): `enemy_fact`, `enemy_base`,
`enemy_center`, `self_base`, `self_center`, `nearest_enemy`,
`nearest_enemy_unit`, `nearest_enemy_structure`.

### Force (兵力)

战术 intent 的执行主体. 三种解析方式: 按 group 名 / 按 actor id 列表 /
按 filter. group 特殊名: `north`, `center`, `south`, `all` (自家可战
机动单位), `mobile` (= `all`), `everything` (字面意义全部含 harv + 建筑).
