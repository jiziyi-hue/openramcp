# OpenRA × MCP × Claude Code — 设计文档

> **版本** v0.2 · **状态** 重构中 (2026-05-23)
> **目标** Claude Code 通过 MCP 工具控制 OpenRA, 用自然语言指挥经典 RTS
>
> **重大重构 2026-05-23**: 责任划分重塑. 旧 v0.1 描述的 "LLM 全包"
> 模型已废. 现行架构详见 [`CONTEXT.md`](../CONTEXT.md).

---

## 0. 项目定位

用 **Claude Code (= LLM)** 当**战术参谋**, **OpenRA (开源经典 RTS 引擎)**
当渲染 + 物理引擎. 玩家 + LLM + Bot/Daemon 三方协作的人机协同 RTS demo.

**核心命题**:
- LLM 不替代玩家, 不替代 NPC. LLM 是**战术指挥能力放大器** — 让一个人
  能管 30+ 单位的战术调度不崩.
- **玩家**主导经济 + 信息判断 + 战略决策.
- **LLM** 转译自然语言 → 战术调度.
- **Daemon** (Python out-of-engine) 跑所有 per-tick 循环 (mission /
  cohesion / 周界 / support pairing).

不做的事:
- ✗ 自画美术 (用 OpenRA RA mod 的 2D sprite)
- ✗ 自做物理 / 寻路 (OpenRA 包了)
- ✗ LLM 替玩家做经济 / 数值分析 (设计原则禁止, 详见 CONTEXT.md)
- ✗ 多人对战 (单人 vs RA 内建 bot)

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│  玩家 (人类)                                                │
│  · 看屏幕 / 派侦察 / 自己判断打不打                         │
│  · 经济全权: 建建筑 / 训单位 / MCV / 卖 / 修 / 占领 / 科技 │
│  · 战略指令: 用自然语言告诉 LLM 想干啥                      │
└───────────────────────┬─────────────────────────────────────┘
                        │ 自然语言 (中或英)
                        ↓
┌─────────────────────────────────────────────────────────────┐
│  Claude Code (= LLM, 战术参谋)                              │
│  · NL → 一次调用 (set_alert_state / set_objective /          │
│         dispatch_intent / 工具)                              │
│  · 战报叙述, 兵种参谋建议                                   │
│  · 不算坐标, 不算 DPS, 不算胜率                              │
└───────────────────────┬─────────────────────────────────────┘
                        │ MCP (stdio JSON-RPC)
                        ↓
┌─────────────────────────────────────────────────────────────┐
│  Python MCP server (mcp_server/server.py)                   │
│  · ~25 工具暴露给 MCP                                       │
│  · interpreter.py — DSL → atomic 调度                       │
│  · tactical.py — Daemon, 0.6s tick, 跑所有 mission 循环     │
│  · scout_daemon.py — 独立, 推 push 事件                     │
└───────────────────────┬─────────────────────────────────────┘
                        │ TCP 127.0.0.1:7777
                        ↓
┌─────────────────────────────────────────────────────────────┐
│  OpenRA process + McpBridge (C#)                            │
│  · TCP server 收 atomic 命令                                │
│  · GrantConditionOnHumanOwner — 给人类盖 enable-human-macro │
│    条件 (仅触发 HarvesterBotModule, 自动采矿)                │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
                  OpenRA 引擎执行
                  · 单位移动 / 战斗 / 建造动画
                  · 玩家在窗口看
```

**关键不变量** (跟 v0.1 同):
- 所有 mutation 走 atomic command → Order → sim tick
- LLM 层失败时 OpenRA 自跑

---

## 2. 主要文档分布

| 文件 | 作用 |
|---|---|
| [`CONTEXT.md`](../CONTEXT.md) | **领域术语 + 核心设计原则** (权威源) |
| [`CLAUDE.md`](../CLAUDE.md) | LLM 项目系统提示 |
| [`docs/SYSTEM_PROMPT.md`](SYSTEM_PROMPT.md) | 备用系统提示 |
| [`docs/INTENT_DSL.md`](INTENT_DSL.md) | DSL 字段权威参考 (开发者) |
| [`docs/TUTORIAL.md`](TUTORIAL.md) | 玩家上手教程 |
| [`docs/RA_ACTOR_NAMES.md`](RA_ACTOR_NAMES.md) | RA mod 单位名速查 |
| [`docs/PAPER_OUTLINE.md`](PAPER_OUTLINE.md) | 论文大纲 |
| [`docs/PROTOCOL.md`](PROTOCOL.md) | TCP 协议 |

---

## 3. 责任划分 (核心)

详见 [`CONTEXT.md`](../CONTEXT.md) "Action ownership" 段.

| 谁 | 干啥 |
|---|---|
| 玩家 | 经济全包 (build/train/sell/deploy/capture/repair/tech), 信息消费, 战略决策 |
| LLM | 战术调度 (intent), 战报叙述, 兵种参谋建议 |
| Bot (C#) | 仅 harv 自动采矿 (HarvesterBotModule) |
| Daemon (Python) | mission 循环 / cohesion / 周界自卫 / support pairing |

---

## 4. 顶层工具

| 工具 | 用途 |
|---|---|
| `set_alert_state(level)` | 切战备 (peace/watch/alert/combat/lockdown) |
| `set_objective(name)` | 设战略目标 (destroy_fact/harass_economy/survive_until/control_map_center) |
| `dispatch_intent(intent_json)` | 战术意图主入口 (15 个 intent) |

详见 [`INTENT_DSL.md`](INTENT_DSL.md).

---

## 5. 模块清单

```
openra_mcp/
├── CONTEXT.md                          # 领域术语 + 原则 (权威)
├── CLAUDE.md                           # 项目系统提示
├── OpenRA/                             # 引擎 clone
├── mcp_server/                         # Python MCP server (stdio)
│   ├── server.py                       # 工具暴露 (FastMCP)
│   ├── intent_dsl.py                   # pydantic DSL schema
│   ├── interpreter.py                  # DSL → atomic 调度
│   ├── tactical.py                     # Daemon (mission / 周界 / cohesion)
│   ├── scout_daemon.py                 # 独立 push 事件
│   ├── logging.py                      # decisions.jsonl + summary
│   ├── transport.py                    # TCP client
│   └── requirements.txt
├── trait_src/                          # 自写 C# trait
│   ├── McpBridge.cs                    # OpenRA 内 TCP server
│   └── GrantConditionOnHumanOwner.cs   # 人类条件 (触发 HarvesterBotModule)
├── scripts/
│   ├── launch.bat                      # 启动 OpenRA + MCP
│   └── build_openra.bat                # 编译 OpenRA + 集成 trait
├── docs/                               # 见上面文档分布表
├── logs/<session_id>/                  # 每局日志 (gitignored)
└── README.md
```

---

## 6. 与 v0.1 的差异 (重构记录)

v0.1 把 LLM 当**全包指挥** (经济 + 战术 + 战略). 实践证明:
- LLM 经济决策不靠谱 (24 e6 / 双 UnitBuilder / 钱漏)
- 跟 SwarmBrain 等 "LLM 全包" 路线撞车, 论文没差异化
- C# 模板模块 (StrategyControllerBotModule) ~500 行无差异化价值

v0.2 (2026-05-23) 彻底分权:
- 玩家拥经济 + 信息
- LLM 拥战术 (高层) + 不做数值分析
- Daemon 拥 per-tick 循环
- 删 C# 模板模块, 删 set_strategy intent, 删 17 字段
- 加 Alert State + Objective + 5 新 daemon mission 类
- 净 -800 行代码, 概念面收窄一半

详见 memory: `project_econ_tactics_split.md`, `project_alert_state_design.md`.

---

## 7. LLM 能力边界

**LLM 做什么**:
- NL → 战术工具调用 (set_alert_state / set_objective / dispatch_intent)
- 状态浓缩转述 ("你 17 个单位都在做什么")
- 战报叙述 (mission 结束 after-action 1 句)
- 兵种参谋建议 (轻量, 不强求)

**LLM 不做什么** (设计原则禁止):
- ✗ 不算坐标 / 距离 / 路径
- ✗ 不算 DPS / HP 对比 / 胜率
- ✗ 不替玩家做经济决策
- ✗ 不串 atomic chain
- ✗ 不驱动 per-tick 循环

---

**End of Design v0.2**
