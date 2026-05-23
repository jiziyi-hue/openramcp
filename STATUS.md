# STATUS — openra_mcp (2026-05-23 重构 + Phase 1-3 优化完)

> 给下一场会话 / VS Code Claude Code 看. 30 秒能掌握现状.
> 详细架构看 [`CONTEXT.md`](CONTEXT.md). LLM 提示看 [`CLAUDE.md`](CLAUDE.md).

---

## TL;DR

**P0 全过 (6/6)**, **P1 部分过 (7/11)**, Phase 1-3 优化完结. 改动:
- 玩家拥经济 + 信息. LLM 拥战术. Daemon 拥循环.
- 删 ~1500 行 (C# 模板模块 + set_strategy DSL + 旧工具). 加 ~750 行 Python.
- **Phase 1**: fix gap — auto-mission force 空入 pending; doc/schema 对齐
- **Phase 2**: harass cycle 默认 False (单次), 长效骚扰走 objective
- **Phase 3**: `set_doctrine` 一句话设大框架; objective 真接管 mission (harass_economy → daemon cycle harass); objective_mission_ids 独立追踪, swap 自动清
- pytest 17/17 全程通过, 31 MCP 工具暴露 (新增 set_doctrine).

**已游戏内验证**: P0 全 + P1 step 7-13 + 18 + Phase 4 (set_doctrine, set_objective harass_economy 真派, harass cycle=false 单次, after-action). 第 1 把测试游戏 win, $0.0134 LLM cost.

**Phase 5 (2026-05-23 下午 第二次大改)**: 加 `destroy_enemy` objective + Assault 动态 force/named target retarget + owner-tagged pending + swap pending prune. 解决"新训部队不上前线"+"target死后愣神就近选目标"问题. pytest 17/17. **未游戏验证**, 留下一把.

---

## 4 责任划分 (不可违反)

| 谁 | 干啥 |
|---|---|
| **玩家** | 经济全包 (build/train/sell/deploy/capture/repair/tech), 看屏幕判断 |
| **LLM (Claude)** | 战术调度 (intent), 战报叙述, 兵种建议. **不做数值分析** |
| **Bot (C#)** | 仅 HarvesterBotModule (harv 自动采矿) |
| **Daemon (Python)** | 所有 per-tick 循环 (mission / 周界 / cohesion / support pairing) |

---

## 顶层工具 (LLM 用)

```
set_doctrine(alert_state?, objective?, survive_tick?)  # 一句话设大框架, 开局首选
set_alert_state(level)        # peace | watch | alert | combat | lockdown
set_objective(name, tick?)    # destroy_fact | harass_economy | survive_until_tick | control_map_center
                              # ↑ 现真接管: harass_economy 自动派 cycle harass; 切目标自动清旧
dispatch_intent(intent_json)  # 15 个 intent: attack/defend/retreat/regroup/scout/pincer/feint/
                              #                set_stance/report/harass(默认单次)/patrol/escort/contain/
                              #                diversion/raw
```

辅助:
- info: `get_state` / `list_units` / `find_unit` / `list_groups` / `screenshot` / `latest_scout_report` / `tactical_status` / `vocab` / `clarify` / `wait_for_event`
- group: `move_group` / `attack_group` / `stance_group` / `assign_to_group` / `rebalance_groups`
- daemon: `enable_auto_defense` (多周界) / `disable_auto_defense` / `list_defense_perimeters` / `cancel_assaults` / `list_pending_missions` / `cancel_pending`
- lifecycle: `pause` / `resume` / `end_session` / `session_info`
- alert/obj: `get_alert_state` / `get_objective`

**LLM 不能调** (已从 MCP 隐藏):
- 经济: `build` `train` `sell` `deploy` `capture`
- 单 unit: `move` `attack` `stop` `set_stance` `scatter`

---

## Alert State 行为表

| 状态 | 时机 | 周界 | 自动 mission | 默认 stance | 默认 approach | 撤退阈值 |
|---|---|---|---|---|---|---|
| `peace` | 开局发育 | off | — | ReturnFire | frontal | 0.3 |
| `watch` | 中期警戒 | on | scout 巡逻 (1) | Defend | cautious | 0.3 |
| `alert` | 已交火 | aggressive | 巡逻 + 骚扰 (2) | Defend | cautious | 0.5 |
| `combat` | 主动进攻 | on (守家) | — | AttackAnything | charge | 0.25 |
| `lockdown` | 被围死守 | aggressive | 全员回家 | Defend | (不出战) | 0.7 |

---

## Mission 类型 (daemon 跑)

| Mission | 谁触发 | force 类型 |
|---|---|---|
| `Assault` | attack/pincer 内部派 | 静态 |
| `HarassMission` | "骚扰" / alert 自动 | 动态 (filter 重解) |
| `PatrolMission` | "巡逻" / watch+alert | 动态 |
| `EscortMission` | "护送 X" | 半动态 (escortee 锁, bodyguard 动) |
| `ContainmentMission` | "卡口" / defend 内部派 | 静态 |
| `DiversionMission` | "佯攻偷家" | 静态 (两路对等) |
| `DefensePerimeter` | enable_auto_defense | 区域级, 支持多个 |

**Pending Queue**: force 解析返 0 时, mission 入队, daemon 每 3s 重试, 玩家训出匹配单位 daemon 自动启动.

**Support Pairing**: medic 自动贴步兵 (hp<0.7, 半径 10), mechanic 自动贴车. 常驻 daemon, 玩家 + LLM 不下令.

**After-Action**: mission 结束 (cancel / wiped / withdrawn / completed / escortee_lost) → 推 1 行 `mission_end` 事件到 scout_events.jsonl, LLM 转告玩家.

---

## 文件改了哪 (改动地图)

### 主 repo (commit `9678c27`)
```
M  CLAUDE.md                              # 新提示 + alert state + objective + 信息纪律
M  docs/SYSTEM_PROMPT.md                  # 中文化 + 同步
M  mcp_server/intent_dsl.py               # 删 17 字段, 加 5 intent + harass_capable filter
M  mcp_server/interpreter.py              # 删 set_strategy/economy/bot_focus 分支, 加 5 _do_* handler
M  mcp_server/server.py                   # 删 13 工具, 加 7 工具 (alert/objective/pending/multi-perim)
M  mcp_server/tactical.py                 # +2106 行: 5 mission + AlertState + SupportPair + PendingQueue
M  mcp_server/schema.py                   # 删 CmdSetStrategy / CmdGetStrategy
M  mcp_server/smoke_test.py               # 期待工具列表更新
M  mcp_server/tests/test_intent_dsl.py    # 删 7 set_strategy test, 加 4 新 intent test
M  mcp_server/tests/test_interpreter.py   # 同上
D  scripts/gen_strategy_templates.py
D  trait_src/HumanAssistantBot.cs
D  trait_src/StrategyControllerBotModule.cs
```

### OpenRA submodule (commit `4ac08b5`, detached HEAD)
```
M  mods/ra/rules/ai.yaml                  # 删 StrategyController + HumanAssistantBot block, 删 BuildingRepair/McvManager 人类挂载
M  mods/ra/mod.yaml                       # 删 strategy_templates.yaml include
D  OpenRA.Mods.Common/Traits/Player/HumanAssistantBot.cs
D  OpenRA.Mods.Common/Traits/Player/StrategyControllerBotModule.cs
+  其他 pre-existing 用户 patch (Player.cs, BaseBuilderBotModule.cs 等) 也一并 commit 当 baseline
```

### 新文件
```
+  CONTEXT.md                             # 领域术语 + 4 核心原则
+  docs/INTENT_DSL.md                     # 重写 (469 行)
+  docs/TUTORIAL.md                       # 重写 (360 行)
+  docs/PAPER_OUTLINE.md                  # 重写 (262 行)
+  docs/DESIGN.md                         # v0.2 重写
+  STATUS.md                              # 这个文件
```

---

## 测试 checklist

### P0 — 必跑 (任一挂 = 严重 bug)

1. `scripts/build_openra.bat` → OpenRA 编. 注意没缺 trait
2. `scripts/launch.bat` → 主菜单进 skirmish, 任选地图
3. harv 自动跑矿场 (HarvesterBotModule 还在)
4. Claude Code 开此项目, 说 "看战况" → `get_state` 返实数据
5. OpenRA UI 自己出 5 步兵 + 建发电厂 → 能造
6. 跟 LLM 说 "建发电厂" → LLM 拒绝 (无 build 工具)

### P1 — 新功能 (一定要试)

7. "切 watch" → daemon 派 scout 巡逻 (或告知队没单位, queue)
8. "切 alert" → + 自动派骚扰队
9. "切 combat" → 取消自动 mission
10. "切 lockdown" → 全员撤回 self_base
11. "切 peace" → 周界关
12. "派部队骚扰" (有 e3/jeep/dog) → HarassMission 注册
13. "派部队骚扰" (没合适单位) → pending queue + LLM 告知训啥
14. 接 13: 训 1 e3 → 几秒后 daemon 自动收编, mission 启动
15. "正面佯攻 + dog 偷家" → DiversionMission, 双路协调
16. "护送 MCV 到 (X,Y)" → EscortMission
17. "守这分矿" + 第 2 周界 → `list_defense_perimeters()` 显示 2 个
18. mission 结束 → LLM 转告 after-action 1 句
19. "GG 我输了" → `end_session`, 看 logs/<id>/session_summary.json

### P2 — 边缘

20. 训 medic + 战场残血步兵 → medic 自动贴
21. 训 mech + 残血坦 → mech 自动贴
22. peace 状态, 敌单位接近基地 < 25 格 → scout_events.jsonl 推 alert_state_suggestion
23. "我打得过吗" → LLM 拒绝分析

---

## 易碎点 (我担心的)

1. **OpenRA build** — 删了 2 个 .cs, 留 GrantConditionOnHumanOwner + McpBridge. 如果 ai.yaml 还有隐藏引用 → 编译挂
2. **decisions.jsonl schema** — 新 intent 类型. `logging.py` 可能要适配. 还没验证
3. **enable_auto_defense 默认** — CLAUDE.md 让 LLM 会话开始**应该**调一次. 但实际玩家可能要主动提
4. **scout_events.jsonl 路径** — daemon push file. 如果路径错或权限错, after-action 静默失败
5. **detached HEAD in OpenRA** — `cd OpenRA && git status` 会警告. 想固化创建 branch: `git switch -c openra-patch-2026-05-23`

---

## 回滚

```bash
# 主 repo 全回
cd D:/openra_mcp && git reset --hard 12aa162

# OpenRA submodule 全回 (回到 upstream b4f3d8a)
cd D:/openra_mcp/OpenRA && git reset --hard b4f3d8a

# 单文件回
git checkout 12aa162 -- path/to/file
```

git 历史:
```
9678c27 refactor!: split player econ vs LLM tactics   ← 改造主提交 (主 repo)
12aa162 safety snapshot before refactor 2026-05-23    ← 安全网 (主 repo)

4ac08b5 patch: strip strategy templates...            ← 改造提交 (OpenRA submodule)
b4f3d8a Increase perf for parsing remote maps         ← OpenRA upstream (干净基线)
```

---

## 任务追踪

### 原重构 task #1-9 (2026-05-23 上午)

| # | 状态 | 描述 |
|---|---|---|
| 1 | ✓ | Doc sync (7 文件) |
| 2 | ✓ | 删 C# + yaml |
| 3 | ✓ | 删 Python 旧代码 |
| 4 | ✓ | 加 5 mission + 多周界 + defend→daemon |
| 5 | ✓ | Alert state + Objective |
| 6 | ✓ | Daemon glue (support / pending / dynamic / after-action) |
| 7 | ✓ | 静态验证 |
| 8 | ⏳ | 改 experiments/showcases.py (低优, 论文实验脚本) |
| 9 | ✓ | 手动跑游戏验证 (P0 全 + P1 部分) |

### P0 测试 (全过)
1-6 全 ✓: build, launch, harv 自动采矿, get_state, UI 经济, LLM 零经济工具

### P1 测试 (跑过的, 2026-05-23 下午第一把)
- 7-11 ✓: peace/watch/alert/combat/lockdown 切换全工作
- 12 ✓: harass dispatch 注册 mission, daemon 接管
- 13 ✓: force 空入 pending queue
- 14 ⏳ 跳过 (pending 自动启动 — 没等到 ttnk 训出就 cancel)
- 15-17 ⏳ 没测 (diversion / escort / multi-perimeter)
- 18 ✓: cancel 触发 mission_end after-action 事件
- 19 ⏳ 没测 (end_session)

### Phase 1-3 优化 (2026-05-23 下午第二会话, 全 done)

| phase | 内容 | 状态 |
|---|---|---|
| 1a | tactical.py:1241 auto-mission 入 pending | ✓ |
| 1b | docs/INTENT_DSL.md 对齐 schema (region/withdraw_hp/center str) | ✓ |
| 2a | IntentHarass.cycle 默认 False | ✓ |
| 2c | 4 doc 文件全更新 (harass=单次, 长效走 objective) | ✓ |
| 3a | set_doctrine MCP 工具 | ✓ |
| 3b | Objective 真接管 — harass_economy 自动派 cycle harass | ✓ |
| 3c | objective_mission_ids 独立 + swap 自动清 | ✓ |

### Phase 4 (第 1 把游戏验证) — DONE
- ✓ set_doctrine 一句话设两层
- ✓ set_objective("harass_economy") 真派 cycle harass + 切目标自动清
- ✓ harass 单次行为 (不再吃新单位)
- ✓ after-action (mission_end 事件)
- ⏳ P1 step 14, 15, 16, 17, 19 没跑 (下把可补)
- ⚠ 发现: Assault target 死后 daemon 就近选目标, 失意图; 后训部队不上前线 (→ Phase 5 修)

### Phase 5 — 代码 DONE, 待游戏验证
- ✓ Assault 加 force_spec + target_named (动态 force, 死敌重选)
- ✓ `destroy_enemy` objective (cycle attack, 全军推, 新训自动加入)
- ✓ `_dispatch_auto_mission` attack 分支 (走 register_assault 动态模式)
- ✓ filter 加 `combat_mobile` (含 3tnk/重装, 排 harv/mcv/buildings)
- ✓ pending 加 `owner` (alert/objective/manual)
- ✓ alert/objective swap 自动 prune 自家 pending (Gap 3+4 修)
- ✓ pytest 17/17, imports clean

### 未做 TODO
- `control_map_center` objective 自动派 contain mission
- `survive_until_tick` 自动切 lockdown + watchdog
- Attack mission 威胁排序 (v2rl/arty/tsla 优先)
- 上把没跑完的 P1 step 14-17 + 19

---

## 工具入口速查

```bash
# 跑 server (一般 MCP 客户端自启)
python -m mcp_server.server

# 跑 scout daemon (独立后台)
python -m mcp_server.scout_daemon

# 单元测试
python -m pytest mcp_server/tests/ -q

# 烟雾测试 (需要 server + OpenRA 都在跑)
python -m mcp_server.smoke_test

# 启动 OpenRA
scripts/launch.bat
# 或手动:
cd OpenRA && bin/OpenRA.exe Engine.EngineDir=.. Game.Mod=ra
```

---

## 记忆系统

Project memory 在 `C:\Users\34681\.claude\projects\D--openra-mcp\memory\`:
- `project_econ_tactics_split.md` — 改造原决定
- `project_alert_state_design.md` — alert state 设计
- `project_refactor_2026_05_23_complete.md` — 改造完结
- `feedback_llm_no_numerical_analysis.md` — 信息纪律规则
- `MEMORY.md` — 索引

未来会话开此项目, 这些自动加载.

---

**End of STATUS — 改造完, 等真游戏验证.**
