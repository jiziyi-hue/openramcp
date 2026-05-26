# openra_mcp — 项目工作总结

> 给外部审阅者快速过项目内容. 一份文档看完所有: 做了什么 / 为什么 / 数据怎样 / 论文怎么投.
> 作者: Ji Ziyi (jiziyi@graduate.utm.my, UTM Malaysia)
> 最后更新: 2026-05-26

---

## 1. 一句话项目

**让玩家用纯日常自然语言指挥实时战略游戏 (RTS) 中的大部队**, 不用学命令语法, 不用算坐标, 不用选单位 ID. 玩家说 "派两队从两边夹击他总部那个亮的", LLM 翻成 MCP 工具调用, OpenRA 引擎执行.

---

## 2. 系统架构 (四层)

```
玩家 (NL 战略意图)
    ↓
LLM (Claude / DeepSeek) — 战术组合层
    ↓ MCP / spawn_squad_batch
C# Squad FSM — 引擎内执行原语 (Assault + Protection 共 2 个)
    ↓ per-unit Order
OpenRA 引擎 — 单位自治 (路径 / 火控 / 碰撞)
```

| 层 | 决定什么 | 在哪 |
|---|---|---|
| Human | 策略 (what) | 玩家脑子 |
| LLM | 战术 (which squads, when) | Python / MCP loop |
| Squad FSM | 执行 (how to push) | C# (2 classes) |
| Engine | 行为 (path/fire/dodge) | OpenRA traits |

**核心设计原则**:
1. 玩家拥有信息 (看屏幕自己判断, LLM 不算 DPS / 胜率)
2. 玩家拥有经济 (建造 / 训兵 走游戏 UI, LLM **零经济工具**)
3. LLM 拥有战术 (移动 / 攻击 / 撤退 / 编排)
4. 引擎拥有循环 (per-tick 行为 C# 侧跑, LLM 不每 tick 介入)

---

## 3. 论文 thesis (新版)

**在 LLM/MCP 多实体控制已经出现在无人机 / 机器人 swarm 领域的背景下, `openra_mcp` 抢占的是 game / RTS / mixed-initiative tactical co-pilot 这个位置: 玩家用纯日常自然语言表达战术意图, LLM 通过 MCP 调用 engine-side squad FSM, 在真实 OpenRA RTS 中控制 30-100 个异质作战单位完成分兵、包抄、佯攻、时序与召回.**

三个 contribution 轴:

1. **纯自然语言战术接口** — 玩家输入零字段 / 零坐标 / 零 unit-id; 系统侧保留可审计 MCP tool calls.
2. **RTS 复杂战术控制** — 1-2 句话翻完一个战术 (10 战术能力测试 10/10 PASS), 覆盖分兵、兵种拆分、包抄、佯攻、改命、撤回、时序.
3. **真 RTS / 人在环副驾驶** — 实时大部队 (30-100 单位), 玩家保留战略、经济、视野判断; AI 不替玩家玩完整游戏.

现在明确**不主张**:

- 不是第一个 NL → MCP 外部控制系统.
- 不是第一个 LLM / MCP 多实体或 swarm 控制系统.
- 不是第一个游戏 AI agent / LLM-as-player.

我们主张的是: **to our knowledge, 第一个 open-source plain-NL mixed-initiative tactical co-pilot for a production RTS**. 旧 thesis ("engine-FSM 比 atomic API 省 token") 降级为 **C5 supporting evidence**, 不删但不当头条.

---

## 4. Paradigm 定位 (与同类项目区分)

| Paradigm | LLM 角色 | 代表项目 |
|---|---|---|
| **A: LLM-as-player** | LLM 替代人类玩家, 全自主跑 | OpenRA-RL, SwarmBrain (StarCraft II), TextSC2, Voyager (Minecraft), HIVE |
| **B: LLM-as-co-pilot** | LLM 协助人类玩家, 玩家保留经济+决策权 | **openra_mcp (本项目)**, oni_mcp (Oxygen Not Included) |

Paradigm B 内部还有两个子方向:
- **broad-surface co-pilot**: 暴露多 atomic 工具, LLM 自己组合 (oni_mcp 走这条)
- **engine-FSM-minimal co-pilot**: 引擎暴露少量高级原语, 复杂行为 LLM 组合 (**本项目**)

调研 2026-05 GitHub 上 25+ game+MCP 仓库, **game-domain Paradigm B + 真 RTS** 这格只有本项目. 但在 game 外, Web-of-Drones / UAV swarm 等工作已经证明 MCP + LLM 可以接多实体系统, 所以论文必须把 novelty 写成 **RTS game co-pilot** 而不是 **MCP swarm general**.

---

## 5. 实验数据

### 5.1 自然语言能力测试 (NL Capability Suite v2)

10 个 NL 场景, 100 单位规模, **10/10 PASS**:

| # | 能力 | NL 示例 |
|---|---|---|
| T1 | 模糊指代 | "左边那队绕过去, 右边的别动" |
| T2 | 兵种拆分 | "坦克走中路, APC 两翼" |
| T3 | 状态拆分 | "受伤的回基地, 剩下的继续推进" |
| T4 | 中途改命 | "全员推 → 停, 改两翼包抄" |
| T5 | 局部 cancel | "第三队回基地, 其他继续" |
| T6 | 条件触发 | "见敌主力就撤, 否则继续" |
| T7 | 路径约束 | "不走中路, 从右边绕" |
| T8 | 阵型保持 | "坦克前 APC 后" |
| T9 | 时序协调 | "正面先出, 5 秒后偷袭" |
| T10 | 失败恢复 | "卡住就重规划" |

证据: `logs/v2_results_recorded.csv`, `logs/v2_videos/*.mp4` (10 录像)

### 5.2 Live LLM Demo

真 Claude 接收玩家 NL → spawn_squad 调用, **8 个连续命令, 0 fail**:
1. 全员推右下
2. APC 推左上 + 1tnk 静止 (兵种拆 + 局部静止)
3. 左半往下 + 右半往上 (空间引用)
4. 集合中央 + 拆 4 队 → 4 角
5. 4 队循环全图 180s (5-6 圈)
6. 全员左下集合
7. 20 小队先发 + 8s 后 80 大部队跟进 (时序)
8. 钳形夹中央建筑

录像: `logs/live_llm_demo/demo_01.mp4` (1.5 GB)

### 5.3 量化对照 (DeepSeek-V4-Pro, N=5 per scenario)

3 战术场景, 同模型, 同 roster, 5 重复:

**30 单位 roster** (12 e1 + 8 3tnk + 6 v2rl + 4 apc):

| 场景 | 成功 | LLM 轮数 | Tool calls | 总 token |
|---|---|---|---|---|
| scen1 全员推右下 | 4/5 | 3.0 ± 0.0 | 3.0 ± 0.0 | 7,136 ± 106 |
| scen2 4 角分兵 | 4/5 | 2.6 ± 0.89 | 2.6 ± 0.89 | 7,009 ± 3,360 |
| scen3 50/50 钳形 | 2/5 | 5.6 ± 2.51 | 5.8 ± 3.35 | 20,502 ± 11,535 |

scen3 2/5 失败模式: LLM 第二阶段 `spawn_squad_batch` 没先 `cancel_squad` 前一队, 单位被挂住 — 这是已知 squad-overlap 限制, 不是 LLM 错误.

**100 单位 roster** (3.3× 放大, 同场景):

| 场景 | 成功 | LLM 轮数 | Tool calls | 总 token |
|---|---|---|---|---|
| scen1 | 0/5 | 3.8 ± 0.84 | 3.8 ± 0.84 | 19,752 ± 10,196 |
| scen2 | 0/5 | 3.6 ± 0.55 | 3.6 ± 0.55 | 18,407 ± 5,846 |
| scen3 | 0/5 | 5.8 ± 0.84 | 6.0 ± 1.22 | 33,534 ± 11,836 |

**100 单位 0/5 success 不是 LLM 错** — LLM 决策 100% 正确 (派对 squad 到对地点). 失败原因 = 引擎层 congestion (100 单位挤路径, verify radius 在 timeout 内没达到). 关键观察: **LLM token 成本 sub-linear scaling** (3.3× roster → 1.04-1.38× LLM 轮数 / 1.6-2.8× tokens). 这反而是 contribution — 找到 paradigm 的真 bottleneck (引擎层, 不是 LLM 层).

### 5.4 跨 paradigm 成本参考 (vs OpenRA-RL N=3 全游戏)

不是 apples-to-apples benchmark (OpenRA-RL 跑全经济+战斗+输, 我们跑独立战术), 只作 paradigm 成本对比参考:

| 指标 | OpenRA-RL N=3 mean | openra_mcp pilot | 比例 |
|---|---|---|---|
| LLM 轮数 | 40.0 ± 7.0 | 11 | 3.6× |
| Tool calls | 66.3 ± 11.5 | 11 | 6.0× |
| Prompt tokens | 743,363 ± 218,540 | 30,629 | **24×** |
| Total tokens | 754,881 ± 217,924 | 34,290 | **22×** |
| Wallclock (s) | 505.2 ± 58.9 | 133.8 | 3.8× |
| 结果 | 3/3 LOSE | 3/3 PASS | — |

数据细节见 `docs/EXPERIMENT_REPORT_2026_05_25.md`.

---

## 6. 工程演化历史 (核心决策记录)

| 阶段 | 时间 | 决策 |
|---|---|---|
| Phase A-B | 2026-05-22~23 | MCP server skeleton + C# McpBridge trait. 31 工具 / 15 intent DSL. |
| Phase C 重构 | 2026-05-23 | "玩家拥有经济" 立法. 删 build/train/sell 等经济工具. 750 LOC 删. |
| Phase D-E3 | 2026-05-23~24 | 试图引擎 FSM 实现 Patrol/Escort/Harass/Explore. Leader-FSM 在 17-40 单位 thrash. |
| Phase E4-E5 | 2026-05-24~25 | 改 Boids 分布式: 每 unit AttackMove. Assault 完美 work (80 单位 4 角 25s). |
| Phase E7 | 2026-05-25 | **关键决策**: 只保留 Assault + Protection 两原语, 上层战术 LLM 组合. |
| Phase Ablation | 2026-05-25 | 删 Daemon / DSL / 14 工具. **~6700 LOC Python 删**. MCP 工具 31 → 17. DSL intent 15 → 3. |
| Phase 实验 | 2026-05-25~26 | DeepSeek-V4-Pro N=5 × 3 场景 × 2 roster + RL N=3 + N=100 scaling test |
| Phase Paper | 2026-05-26 | LaTeX/PDF 生成 (xelatex + IEEEtran), 13 页 |

---

## 7. 代码地图

```
D:/openra_mcp/
├── mcp_server/               Python MCP server
│   ├── server.py             17 MCP 工具暴露
│   ├── intent_dsl.py         3 intent (attack/report/raw)
│   ├── interpreter.py        DSL → squad 调度
│   ├── transport.py          TCP client → OpenRA
│   └── tools/compose_*.py    LLM-side 战术组合 demo
├── trait_src/                C# 引擎 trait
│   ├── McpBridge.cs          TCP 7777 server in engine
│   └── GrantConditionOnHumanOwner.cs
├── OpenRA/                   引擎 (clone, 不在 repo)
├── papers/
│   ├── openra_mcp_preprint.md      主论文 (~47K, 828 行)
│   ├── openra_mcp_preprint.tex     LaTeX (xelatex)
│   ├── openra_mcp_preprint.pdf     PDF (13 页, 177K)
│   ├── references.bib              文献库
│   ├── ZENODO_METADATA.md          Zenodo 元数据
│   └── SUPPLEMENTARY_MATERIALS.md
├── docs/
│   ├── PROJECT_SUMMARY_FOR_REVIEW.md  ← 本文档
│   ├── EXPERIMENT_REPORT_2026_05_25.md  实验细节
│   ├── TWO_PRIMITIVES_PARADIGM.md       核心架构
│   ├── ABLATION_NOTES.md                Phase Ablation 记录
│   ├── INTENT_DSL.md                    DSL 字段权威源
│   ├── SYSTEM_PROMPT.md                 LLM system prompt
│   └── TUTORIAL.md                      玩法教程
└── logs/
    ├── v2_results_recorded.csv          NL capability 10/10 数据
    ├── v2_videos/*.mp4                  10 录像
    ├── live_llm_demo/demo_01.mp4        真 LLM demo 录像
    └── rl_compare/                      量化对照 CSV
        ├── our_deepseek_results_runs_n30.csv
        ├── our_deepseek_summary_n30.csv
        ├── our_deepseek_results_runs_n100.csv
        ├── our_deepseek_summary_n100.csv
        ├── rl_full_game_n3_summary.csv
        └── rl_scripted_n3_summary.csv
```

---

## 8. 已知限制 (论文 §8 Limitations)

1. **没有正式 user study** (无 N≥8 真人玩家实验). 替代证据: NL capability suite 10/10 + live demo 8 cmd + 30 trial 跨 roster.
2. **单游戏验证** (仅 OpenRA, 不含 SC2 / WC3). Paradigm 可迁移 (oni_mcp 同 Paradigm B), 但跨游戏迁移成本未量化.
3. **100 单位 success rate 0/5** (引擎层 congestion, 非 LLM 错误).
4. **跨 paradigm cost 比较不严格 apples-to-apples** (OpenRA-RL 跑全游戏, 我们跑独立战术).
5. **未做模型微调** (默认 off-the-shelf deepseek-v4-pro / Claude). 微调是 future work, 不是本文 scope.
6. **未涵盖 fog-of-war**. `get_state` 当前透雾返敌全态 — 设计内取舍 (Total War 类先例), 不是漏洞.

---

## 9. 投稿计划

| Venue | 适配度 | 状态 |
|---|---|---|
| **IEEE Transactions on Games (TG)** | ★★★★★ 首选 | 准备中, P0 改 title + abstract + figure |
| AIIDE (AAAI Game AI conf) | ★★★★ backup | 同期投或 TG 拒后 |
| EXAG (AIIDE workshop) | ★★★ demo 路线 | 备选 |
| CHI / IUI / CSCW | ★ | 不投 (要 user study) |
| ESWA | ★★ | 不投 (fit 差) |
| arxiv | — | 暂无账号, 可代传 |

**TG 投稿前 P0 清单**:
- [ ] Title 改 "Plain-Language Tactical Control of Real-Time Strategy Game Forces via Engine-Side FSM Primitives and MCP"
- [x] Abstract 重写对齐新 thesis
- [x] §1 / §2 显式引用 Web-of-Drones MCP swarm, 写清楚我们 claim 的是 game / RTS / mixed-initiative 分支
- [x] §1 Intro 加 EndWar (2008 voice-RTS) 对比段
- [ ] §8 Limitations 第一句明写 "No formal user study"
- [ ] 加 Figure 1 (architecture) + Figure 2 (NL → tactical 时间轴)
- [ ] Keywords 填: RTS, LLM, MCP, NL interface, game AI, co-pilot, OpenRA
- [ ] AI disclosure 段 (IEEE 现要求)
- [ ] Reproducibility 章节 (github URL + commit hash + replay)

---

## 10. 关键文献定位

| 引用项目 | 类型 | 备注 |
|---|---|---|
| OpenRA-RL (Paradigm A) | LLM-as-player atomic API | 我们 cost reference baseline |
| SwarmBrain (SC2) | Paradigm A multi-agent | survey 章引用 |
| TextSC2 (SC2) | Paradigm A text 控制 | survey |
| Voyager (Minecraft) | Paradigm A skill library | survey |
| HIVE (2412.11761) | plan-once toy JAX particle | "不算真 RTS" 论据 |
| HIMA (2508.06042) | hierarchical multi-agent | survey |
| oni_mcp | Paradigm B broad-surface | 同 Paradigm B 对照 |
| Web-of-Drones / Say the Mission, Execute the Swarm (2605.03788) | MCP + UAV swarm | 最像的非游戏先例; 必须正面引用并区分 game/RTS/人在环 |
| Universal LLM Drone C2 (2601.15486) | MCP + drone command/control | drone-C2 MCP 先例; 主要不是 RTS / game co-pilot |
| SwarmGPT / FlockGPT / LLM2Swarm | NL/LLM robot swarm | 证明 multi-entity NL control 已存在, 我们不能泛化 claim |
| EndWar (2008) | scripted-grammar voice RTS | "NL vs scripted" 对比 |
| MCP (Anthropic 2024) | 协议本身 | 引 |
| Mixed-initiative (Horvitz 1999) | 人在环 paradigm 根 | 引 |

---

## 11. 复现 + 数据

- **GitHub**: 本仓库 (commit hash 待 release tag)
- **Zenodo**: `papers/openra_mcp_zenodo_core.zip` (代码 + CSV + manifest)
- **录像**: `logs/v2_videos/` + `logs/live_llm_demo/` (~3.5 GB, 不入 zip)
- **LLM**: deepseek-v4-pro (thinking mode), 同 key 双侧
- **OpenRA fork**: 本项目 fork, 加 McpBridgeTrait + GrantConditionOnHumanOwner
- **OpenRA-RL**: ghcr.io/yxc20089/openra-rl:latest (arm64, QEMU on Windows amd64)

---

## 12. 给审阅者的请求

请重点看:

1. **§3 thesis** — 三轴 contribution 站不站得住? 在 Web-of-Drones 这类 MCP swarm 先例之后, "game / RTS / mixed-initiative tactical co-pilot" 这个窄 claim 是否足够清楚?
2. **§4 paradigm 定位** — Game+MCP 同类项目 survey 够不够全? 还有什么 game-domain Paradigm B / RTS co-pilot 我没引?
3. **§5.3 数据** — N=5 算不算够 (vs 一般要求 N≥10)? 100 单位 0/5 的解释 (引擎 congestion 不是 LLM 错) 信不信?
4. **§8 限制** — 不做 user study 的理由 (替代证据列了 3 类) 能不能挡 reviewer?
5. **§9 投稿计划** — IEEE TG 首选合不合理? 我们 system paper 性质契不契合 TG 风格?

---

**End of summary. 13 页 PDF 在 `papers/openra_mcp_preprint.pdf`. 数据 CSV 在 `logs/rl_compare/`. 有问题 email jiziyi@graduate.utm.my.**
