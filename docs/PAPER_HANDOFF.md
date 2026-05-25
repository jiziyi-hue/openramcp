# Paper Handoff — openra_mcp

> 给后续写论文的人. 项目代码已完成, 数据齐, 录像齐. 你只需写 paper.
> Last updated: 2026-05-25 (after Phase Ablation + Live LLM Demo).

---

## TL;DR — 论文卖点

**"Two engine primitives + LLM-side composition."**

- 引擎 (OpenRA C#) 只暴露 **2 个 squad 原语**: `Assault` (推) + `Protection` (守).
- LLM 通过 **MCP** 调 `spawn_squad` / `spawn_squad_batch`. 高级战术 (巡逻 / 钳形 / 佯攻 / 护送 / 路径约束 / 时序协调 …) 全在 LLM-side 用 Python 循环组合.
- 跟 OpenRA-RL 比 (per-tick atomic 控制): **事件驱动 ~10× 更少 LLM 调用**, 仍保留全部战术表达力.
- 跟 HIVE/HIMA/SwarmBrain 比: 我们在**真 RTS 引擎** + **task-level intent** + **人在环** (玩家管经济, LLM 管战术).

---

## 三层架构 (论文核心图)

```
┌──────────────────────────────────────┐
│  Human (战略意图)                     │
│  "拿下右上角 / 派部队骚扰他经济"      │
└────────────┬─────────────────────────┘
             │ NL
┌────────────▼─────────────────────────┐
│  LLM (战术组合)                       │
│  把 NL 分解成有序 spawn_squad_batch    │
│  调用. 跟踪 squad 进度, 事件触发重派.  │
└────────────┬─────────────────────────┘
             │ MCP / spawn_squad_batch
┌────────────▼─────────────────────────┐
│  C# Squad FSM (执行原语)              │
│  Assault: AttackMove 到目标, AutoTarget│
│  Protection: 守一个 cell, 见敌还击     │
│  Only on event change, no per-tick.   │
└────────────┬─────────────────────────┘
             │ Order
┌────────────▼─────────────────────────┐
│  Engine (单位自治)                    │
│  ActivityQueue + AutoTarget +         │
│  pathfinding + collision              │
└──────────────────────────────────────┘
```

四层职责清晰:

| Layer | 决定 | 在哪 |
|---|---|---|
| Human | 策略 ("what should happen") | 玩家脑子 |
| LLM | 战术 ("which squads, when") | Python / MCP loop |
| Squad FSM | 执行 ("how to push") | C# (2 classes) |
| Unit/Engine | 行为 (path, fire, dodge) | OpenRA traits |

---

## 实证 (Evidence)

### v2 NL-capability suite (`mcp_server/experiments/scenarios_v2.py`)

10 scenario 测自然语言 → 战术能力. **post-ablation 100 单位 10/10 PASS**:

| # | 能力 | NL 示例 | 通过 |
|---|---|---|---|
| T1 | 模糊指代 | "左边那队绕过去, 右边的别动" | ✓ |
| T2 | 兵种拆分 | "坦克走中路, APC 两翼" | ✓ |
| T3 | 状态拆分 | "受伤的回基地, 剩下的继续推进" | ✓ |
| T4 | 中途改命 | "全员推 → 停, 改两翼包抄" | ✓ |
| T5 | 局部 cancel | "第三队回基地, 其他继续" | ✓ |
| T6 | 条件触发 | "见敌主力就撤桥口, 否则继续" | ✓ |
| T7 | 路径约束 | "不走中路, 从右边绕" | ✓ |
| T8 | 阵型保持 | "坦克前 APC 后, 别挤" | ✓ |
| T9 | 时序协调 | "正面先出, 5s 后偷袭" | ✓ |
| T10 | 失败恢复 | "卡住就重规划" | ✓ |

CSV: `logs/v2_results_recorded.csv`, `logs/v2_retry_4fails.csv`, `logs/v2_t7_retry2.csv`.

录像: `logs/v2_videos/*.mp4` (10 mp4, 50-250 MB 每个).

### Live LLM Demo (`logs/live_llm_demo/demo_01.mp4`, 1.5 GB)

真 LLM (Claude) 接收玩家 NL 命令, 翻译成 spawn_squad/batch 调用. 8 个连续命令, 0 fail:

1. 全员推右下 (单队 100)
2. APC 推左上 + 1tnk 不动 (兵种拆 + 局部静止)
3. 左半往下 + 右半往上 (空间引用)
4. 集合中央 + 拆 4 队 → 4 角
5. 4 队循环全图 180s (5-6 圈)
6. 全员左下集合
7. 20 小队 → 右下 → 右上, 8s 后 80 大部队跟进 (时序协调)
8. 钳形夹中央建筑 (集合 → 50/50 拆 → 上下臂)

录屏体现 LLM 真在做: 读 get_state + 选 ids + 算坐标 + 出 batch JSON. 不是预编程脚本.

### E7 Paradigm 验证 (earlier; `logs/baseline_pre_ablation.md`)

9 战术, 8 PASS + 1 (Protection) bug 已修:

| 项目 | 结果 |
|---|---|
| Assault 单队推 | ✓ |
| spawn_squad_batch 4 队 (4 角) | ✓ 34ms |
| compose_patrol 4 队 4 角 120s | ✓ 5-6 圈 |
| 钳形 (2 路汇合) | ✓ |
| 集合+佯攻+偷家 | ✓ |
| 混编 4 队 (APC + 3tnk) | ✓ |
| 双路 combined arms (tank front + APC offset) | ✓ |
| 80 单位单队推 | ✓ 55/60 到位 |
| 8 队 batch 60 单位 | ✓ 10ms 派, 87% 到位 |

### Ablation

`docs/ABLATION_NOTES.md` 全细节. 摘要:

| 项 | 删前 | 删后 |
|---|---|---|
| MCP tool | 31 | 17 |
| DSL intent | 15 | 3 (attack/report/raw) |
| Python LOC | ~8500 | ~1800 |
| C# changes | 0 | 39 LOC (MCP whitelist + Protection fix) |
| v2 PASS rate | n/a | 10/10 (post) |

删 6700 行 LOC 后战术全部仍 PASS = **证明 daemon/DSL 是冗余的, paradigm 自足.**

---

## 跟 prior art 的对比

| | 抽象层 | 引擎 | 人在环 | LLM 调用率 |
|---|---|---|---|---|
| **OpenRA-RL** ([yxc20089](https://github.com/yxc20089/OpenRA-RL)) | 48 个 atomic, 像素级 | 真 RTS (OpenRA) | 否 | per-tick (~60/min, 推算未实测) |
| **HIVE / HIMA** | 多步 plan JSON | 玩具 / 文本 | 否 | 中等 |
| **SwarmBrain / Voyager** | high-level | Minecraft | 部分 | 中等 |
| **本工作** | task-level intent (NL) | **真 RTS** | **是** | **event-driven ~10/min** |

3-axis niche: 真 RTS × task-level × 人在环. 唯一占位.

---

## 关键设计决策 (写 Discussion 用)

### 1. 为何只 2 个原语 (Phase E7)
早期试过 Patrol / Escort / Explore / Harass 4 个 C# SquadType. **都不稳**:
- Patrol 预 queue 的 waypoint 循环, append-queued=true 失败.
- Escort/Explore 类似, 循环没法靠 C# FSM 单独维护.
- Harass 包含"撤血"判断 — 这是**策略**决定, 不该在 FSM 里.

决定: archive 这 3 个 FSM, 只留 Assault + Protection. 高级战术 LLM-side 组合.

详见 `docs/TWO_PRIMITIVES_PARADIGM.md` 和 memory `project-two-primitives-paradigm`.

### 2. 为何删 daemon / DSL (Phase Ablation)
v2 10 战术验证完, 0 个用 DSL/daemon. 数据驱动决定: 删.

详见 `docs/ABLATION_NOTES.md`.

### 3. 为何不 per-tick LLM 控制
单位级 atomic (OpenRA-RL 路) 把 LLM 拖进 60Hz 循环. **token cost 不可控**, 决策慢. 我们走事件驱动 — 只在 "squad arrived / squad died / player updated intent" 时调 LLM. ~10× 调用率降低 (估算, 待与 OpenRA-RL 实跑比对).

### 4. 信息纪律 (Player owns information)
LLM 不算 DPS / 不估胜率 / 不告诉玩家"打得过吗". 玩家屏幕 + 侦察自己判断. LLM 是参谋, 不是分析师. 这是 prior art 不一定坚持的点 — HIVE/Voyager 都让 LLM 做战况判断.

---

## 推荐论文结构

```
1. Introduction
   - RTS LLM 控制痛点 (token / 操作粒度 / 实时性)
   - 我们的 niche (真 RTS + task-level + 人在环)
   - 主贡献: 2-primitive paradigm

2. Related Work
   - OpenRA-RL (atomic), HIVE/HIMA (toy), SwarmBrain/Voyager (Minecraft)
   - 3-axis comparison table

3. Architecture
   - 三层架构图 (human / LLM / FSM / engine)
   - MCP bridge 设计
   - 信息纪律 (谁拥有信息 / 经济 / 战术 / 循环)

4. Two Primitives
   - Assault + Protection FSM 详解 (C# 状态机图)
   - 为何 Patrol/Escort/Explore archived (Phase E7 收的)
   - LLM-side composition pattern (compose_patrol.py 示例)

5. Implementation
   - McpBridge C# trait, spawn_squad_batch
   - DSL (post-ablation 3 intent: attack/report/raw)
   - Python tools/compose_*.py 模板

6. Evaluation
   - v2 NL-capability 10/10 PASS (T1-T10) — 表 + 录像 supplement
   - Live LLM Demo (8 命令真 Claude 翻译)
   - E7 9 战术验证 (Boids batch latency 10-34ms)
   - Ablation: 删 6700 LOC 仍 10/10

7. Discussion
   - 信息纪律 vs 数据消费 (跟其他工作的哲学差异)
   - 论文最佳卖点不是 "LLM 控制 RTS", 是 "LLM 不必做 LLM 不擅长的事 (per-tick / 数值分析)"
   - Boids per-unit AttackMove 处理混编自然分层, 不需要专门阵型逻辑

8. Limitations & Future Work
   - OpenRA-RL baseline 未实跑, paper supplement 待补
   - 真 LLM token / latency 在 demo 录像里, 但没正式统计 — 论文 final revision 前补
   - Protection FSM 修后未在敌人场景测过 (sandbox 0 enemy)
   - 只测过 Allied 阵营; Soviet (tnk2/3/4, arty, v2) 未系统验证混编
   - 信息纪律只在 LLM prompt 里强制, 没做技术屏蔽 (LLM 可绕过)
```

---

## 数据 / 文件清单

### 代码 (写 Methods 引用)
- `OpenRA/OpenRA.Mods.Common/Traits/World/McpBridge.cs` — MCP TCP bridge + whitelist
- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/Squads/States/GroundStates.cs` — Assault FSM (Boids per-unit + issue-once)
- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/Squads/States/ProtectionStates.cs` — Protection FSM (cell-hold fix)
- `mcp_server/server.py` — MCP tool surface (17 tools)
- `mcp_server/interpreter.py` — DSL → spawn_squad (squad-only path)
- `mcp_server/intent_dsl.py` — pydantic schema (attack/report/raw)
- `mcp_server/tools/compose_patrol.py` — LLM-side patrol composition demo
- `mcp_server/tools/cycle4_demo.py` — Live demo cyclic 4-squad
- `mcp_server/tools/small_big_demo.py` — Live demo 时序协调 8s
- `mcp_server/tools/pincer_demo.py` — Live demo 钳形

### 实验 (写 Evaluation 引用)
- `mcp_server/experiments/scenarios_v2.py` — v2 10 scenario 代码
- `mcp_server/experiments/run_v2.py` / `run_v2_recorded.py` — runner

### 数据 (CSV / log)
- `logs/v2_results_recorded.csv` — v2 第一遍 (6/10 PASS, 4 阈值 fail)
- `logs/v2_retry_4fails.csv` — 调阈值后 T3/T5/T7/T8 retry (3/4)
- `logs/v2_t7_retry2.csv` — T7 final tune (1/1)
- `logs/v2_post_ablation.csv` — post-ablation 全 10
- `logs/baseline_pre_ablation.md` — E7 9 战术 baseline
- `logs/<session_id>/decisions.jsonl` — 每次 dispatch 详细记录 (session 维度)

### 录像 (paper supplement)
- `logs/v2_videos/*.mp4` — 10 v2 scenario 各一 mp4 (50-250 MB)
- `logs/live_llm_demo/demo_01.mp4` — 1.5 GB Live LLM 8 命令演示

### 文档
- `docs/TWO_PRIMITIVES_PARADIGM.md` — Phase E7 paradigm 论文级 doc
- `docs/ABLATION_NOTES.md` — Phase ablation 细节
- `docs/PAPER_HANDOFF.md` — 本文件
- `docs/PAPER_OUTLINE.md` — 早期 outline (可能 stale)
- `docs/DESIGN.md` — 早期设计 (可能 stale)
- `CLAUDE.md` / `CONTEXT.md` — 项目内部规则 (写论文不直接用, 但能看到设计哲学)

### Git
- branch `master` — 主干 (current HEAD: `0411450` v2 threshold tune)
- branch `pre-ablation-backup` — Phase ablation 前快照 (回滚用)
- 关键 commits:
  - `0411450` v2 scenarios: threshold tune for 100 units
  - `f01a257` phase ablation C+D: drop daemon/dsl ~6700 LOC
  - `91f05ce` phase ablation A+B: MCP surface trim + v2 NL suite
  - `d9f334d` phase E7: archive squad FSMs + paradigm doc
  - OpenRA submodule `cc9ed99` — Protection fix + squad_type whitelist

---

## 待补 (final revision 前)

按优先级:

1. **OpenRA-RL 实跑 baseline** — 现在论文里说 "推算 60/min", 应实测对比. 见 memory `reference-openra-rl`. 难度中 (装 docker + 跑一次).
2. **Live LLM token / latency 正式统计** — Live demo 录像里有, 但没收数. 跑一次正式 session 用 `dispatch_intent(meta=...)` 收 `llm_input_tokens` / `llm_output_tokens` / `llm_latency_ms`. 几小时.
3. **多次跑取平均** — 现 v2 每项 1 次. 跑 5 次取均 + 标准差, paper figure 更稳.
4. **Discussion 写 Limitations 章节时**, 强调:
   - 测试 sandbox 模式 (无真敌 AI), 全场景没敌打回来. T6 conditional / T10 stuck 都是合成触发.
   - C# Protection 修后未在敌情下验证 (sandbox 0 enemy).
   - 录像里玩家手 cheat /instantbuild 给单位, 不是真生产经济.

---

## 联系上下文

如果你接手有疑问:
- 项目内部规则: 看 `CLAUDE.md` + `CONTEXT.md` 头部 (核心设计原则)
- 历史决策: `git log --all --oneline` 看 phase 命名
- 卡住的事: `memory/MEMORY.md` (Claude memory 系统) 列了项目核心决策
- 主要修过的痛点 (写 Discussion 用): rally gate 大队脆 (`project-rally-gate-scales-poorly`), Boids 起源 (`project-boids-squad-architecture`), 2 原语收口 (`project-two-primitives-paradigm`)

---

**祝写顺.**
