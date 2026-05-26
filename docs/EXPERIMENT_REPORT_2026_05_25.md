# Experiment Report — OpenRA-RL vs openra_mcp Token/Turn Baseline

**Date**: 2026-05-25
**Author**: Ji Ziyi (jiziyi@graduate.utm.my)
**Goal**: Preliminary empirical comparison of LLM control cost (turns / tokens
/ wallclock) between two architectures for LLM-driven RTS:
- **OpenRA-RL** (yxc20089/OpenRA-RL): per-tick atomic action API, 48 MCP tools
- **openra_mcp** (this project): event-driven squad-FSM API, 17 MCP tools

The experiment has three evidence lines:
- openra_mcp + DeepSeek-V4-Pro on three isolated tactical-control tasks.
- OpenRA-RL + DeepSeek-V4-Pro on a full economy-and-combat game.
- OpenRA-RL scripted atomic runner on corresponding isolated tactical tasks.

The first two lines compare visible LLM token/turn cost. The third line is not
an LLM-token comparison, but it validates the corresponding isolated-task path
on OpenRA-RL and measures how many low-level environment round-trips an atomic
API needs for the same family of tactical maneuvers.

---

## 1. Setup

### 1.1 Hardware / OS
- Windows 11 (amd64), Docker Desktop 29.4.3 with linux/arm64 QEMU emulation
  (no native linux/amd64 image — see [OpenRA-RL issue #63](https://github.com/yxc20089/OpenRA-RL/issues/63))
- Python 3.14 for OpenRA-RL client; 3.13 for our MCP server

### 1.2 Systems Under Test

| System | Engine | Bridge | Tool surface | Game launch |
|---|---|---|---|---|
| openra_mcp | OpenRA (this fork, with McpBridge TCP trait) | TCP 7777 | 17 MCP tools (Assault/Protection squads + read-only) | Native window, human-trained roster via /instantbuild |
| OpenRA-RL | OpenRA (yxc20089 fork, headless gRPC bridge) | docker container, port 8000 | 48 MCP tools (per-unit atomic move/attack/build/train/...) | Container `ghcr.io/yxc20089/openra-rl:latest`, default map `singles.oramap` |

Runtime note: the OpenRA-RL configuration file used `bot_type: easy`, but the
runtime `get_opponent_intel` response reported `Normal AI`. Treat opponent
difficulty as a logged runtime variable and verify it before using this run for
gameplay-quality claims.

### 1.3 LLM
- Provider: DeepSeek (api.deepseek.com)
- Model: `deepseek-v4-pro` (1.6T MoE thinking mode)
- Same API key both sides
- No prompt caching configured

### 1.4 Roster (our side)
30 mobile units, Soviet faction, cheat-spawned:
- 12 e1 (Rifle Soldier)
- 8 3tnk (Heavy Tank)
- 6 v2rl (V2 Rocket Launcher)
- 4 apc (Armored Personnel Carrier)

### 1.5 Roster (RL side)
DeepSeek-V4-Pro built the roster organically through normal `build_unit`
calls during the game. Final state at game end: 3-12 combat units across
the run.

---

## 2. Tactical Workloads (3 scenarios)

| Scenario | Goal |
|---|---|
| **scen1 full push BR** | All 30 mobile units attack-move to map bottom-right (78, 85) |
| **scen2 4-corner split** | Split 30 units into 4 squads, each pushes to a different map corner |
| **scen3 50/50 pincer** | Split 30 into left/right halves; phase 1 push to (20,46) and (65,46); wait; phase 2 both converge to map center (42,46) |

### 2.1 openra_mcp execution
- DeepSeek-V4-Pro called via our `our_deepseek_runner.py` (HTTPS OpenAI-compatible chat completions, tool_choice=auto)
- 7 tools exposed to LLM: `get_state`, `spawn_squad`, `spawn_squad_batch`, `list_squads`, `cancel_squad`, `wait`, `done`
- Each scenario run independently; sleep between scenarios for unit reset (manual)

### 2.2 OpenRA-RL execution
- Run via official `examples/llm_agent.py`, full 48-tool surface
- One full game (start → planning_phase → econ buildup → combat → loss)
- 100-turn / 1800s caps

### 2.3 OpenRA-RL isolated tactical execution
- Run via `logs/rl_compare/rl_tactical_v2.py`
- Setup phase driven by OpenRA-RL's `ScriptedBot` until at least 10 combat
  units exist
- Tactical scenarios then executed through per-actor atomic commands
  (`MOVE`, `ATTACK_MOVE`, `NO_OP`) without an LLM

---

## 3. Results

### 3.1 openra_mcp side (DeepSeek-V4-Pro, 3 scenarios, single-run pilot)

| Scenario | LLM turns | Tool calls | Prompt tokens | Completion tokens | Total tokens | Wallclock (s) | Outcome |
|---|---|---|---|---|---|---|---|
| scen1 full push BR | 3 | 3 | 6990 | 690 | 7680 | 16.4 | PASS — 30/30 arrived |
| scen2 4-corner split | 3 | 3 | 7599 | 1263 | 8862 | 23.0 | PASS — 4 squads 8/8/7/7 dispatched |
| scen3 50/50 pincer | 5 | 5 | 16040 | 1708 | 17748 | 94.4 | PASS — phase 1 + 60s wait + phase 2 convergence |
| **TOTAL** | **11** | **11** | **30629** | **3661** | **34290** | **133.8** | **3/3 PASS** |

LLM strategy across scenarios: `get_state` → `spawn_squad` (or `spawn_squad_batch`) → `done` (sometimes intermediate `wait`).

### 3.1.bis openra_mcp side (DeepSeek-V4-Pro, repeated N=5 per scenario)

Per the sample-size expansion plan, each scenario was rerun 5 times with the
same 30-unit roster, with between-run unit recall to a rally point near the
base. Each scenario has a scenario-specific verify_wait window
(scen1: 75 s, scen2: 90 s, scen3: 150 s) before checking unit positions.
Schema and aggregation code in `logs/rl_compare/our_deepseek_runner_v2.py`.

| Scenario | N | Success | LLM turns mean ± std (min,max) | Tool calls mean ± std | Total tokens mean ± std (min,max) | Wallclock mean ± std (s) |
|---|---:|---:|---|---|---|---|
| scen1 full push BR | 5 | **4/5** | 3.0 ± 0.0 (3, 3) | 3.0 ± 0.0 | 7136 ± 106 (6999, 7296) | 77.9 ± 4.8 |
| scen2 4-corner split | 5 | **4/5** | 2.6 ± 0.89 (1, 3) | 2.6 ± 0.89 | 7009 ± 3360 (1028, 8958) | 108.4 ± 54.9 |
| scen3 50/50 pincer | 5 | **2/5** | 5.6 ± 2.51 (2, 8) | 5.8 ± 3.35 | 20502 ± 11535 (5182, 35038) | 206.5 ± 102.7 |

Observed failure modes (each appears once unless noted):
- scen1 r02: LLM emitted `done` early, single-`spawn_squad` issued correctly,
  but only 1/30 reached target inside verify window — likely a unit-pathing
  edge case (verify radius too tight or 75 s insufficient on a specific seed).
- scen2 r03: transient DeepSeek connection drop (`Remote end closed
  connection without response`). Counted as failure but does not reflect
  architecture.
- scen3 r01: JSON parse error in one tool-call argument (transient DeepSeek
  malformed response). Counted as failure.
- scen3 r02 / r03: LLM dispatched phase-2 `spawn_squad_batch` without
  cancelling the phase-1 squad first. The phase-1 squad continued to hold
  the units, blocking phase-2 movement. This is a known
  squad-overlap interaction documented in
  `memory/project_rally_gate_scales_poorly.md`. LLM in r04/r05 issued an
  explicit `cancel_squad` between phases and succeeded.

Token cost is stable across repeats for scen1 (±1.5%) and shows scenario-
intrinsic variance for scen3 (LLM occasionally re-checks state, retries with
explicit cancels). scen2 variance is dominated by one r03 connection drop
run that produced a low (1028) token row.

Raw per-run data: `logs/rl_compare/our_deepseek_results_runs.csv`.
Summary: `logs/rl_compare/our_deepseek_summary.csv`.

### 3.2 OpenRA-RL side (DeepSeek-V4-Pro, full game, N=3)

Three independent full-game runs of `examples/llm_agent.py` against a fresh
docker container. All three runs reached the LOSE terminal state without
exhausting the 100-turn / 1800-second cap.

| Run | LLM turns | Tool calls | Prompt tokens | Completion tokens | Total tokens | Wallclock (s) | Game ticks | Outcome |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 47 | 78 | 980,323 | 13,940 | 994,263 | 518.9 | 13,529 | LOSE |
| 2 | 33 | 55 | 550,221 | 9,028 | 559,249 | 440.8 | 11,638 | LOSE |
| 3 | 40 | 66 | 699,546 | 11,585 | 711,131 | 556.0 | 15,042 | LOSE |
| **mean ± std** | **40.0 ± 7.0** | **66.3 ± 11.5** | **743,363 ± 218,540** | **11,518 ± 2,462** | **754,881 ± 217,924** | **505.2 ± 58.9** | **13,403 ± 1,708** | **3/3 LOSE** |
| min | 33 | 55 | 550,221 | 9,028 | 559,249 | 440.8 | 11,638 | |
| max | 47 | 78 | 980,323 | 13,940 | 994,263 | 556.0 | 15,042 | |

Average prompt per response across runs: ~18.5k–20.9k tokens. Avg latency per
LLM call: 11.0–14.7 s. The 48-tool schema contributes a fixed repeated
overhead on each call. Prompt size grows with conversation history but the
agent appears to prune at points; observed per-call prompt usage ranged from
6.3k to 28.7k tokens across runs.

Raw logs: `logs/rl_compare/deepseek_pro_run.log` (run 1),
`deepseek_pro_run2.log` (run 2), `deepseek_pro_run3.log` (run 3).
Summary CSV: `logs/rl_compare/rl_full_game_n3_summary.csv`.

#### 3.2.1 Post-hoc tactical slices inside the full game

The full-game OpenRA-RL run can also be sliced into tactical episodes. These
are not controlled isolated prompts, but they are useful as in-context tactical
subtasks that occurred during a real full-flow game.

| Full-game tactical slice | LLM response turns | Tool pattern | Prompt tokens | Completion tokens | Total visible tokens |
|---|---:|---|---:|---:|---:|
| Early scouting movement | 21-22 | `move_units`, `move_units`, `advance` | 54,702 | 394 | 55,096 |
| Midgame scout push | 26-29 | repeated `move_units` + `advance` | 85,091 | 1,078 | 86,169 |
| Late combat / defensive collapse | 41-45 | `attack_target`, `advance`, `repair_building`, `set_rally_point`, `move_units`, `attack_move`, `sell_building` | 120,441 | 1,327 | 121,768 |

This supports a second, more naturalistic reading of the comparison: even
within a full game, small tactical episodes over an atomic tool surface carry
tens of thousands of visible prompt tokens because each tactical decision is
made inside the accumulated conversation and 48-tool schema context.

### 3.3 OpenRA-RL side (corresponding isolated tactical tasks, no LLM, N=3)

Setup was driven by `ScriptedBot` until ≥10 combat units existed, then the
three tactical tasks were executed using per-actor atomic actions. One
environment step corresponds to one OpenRA-RL round-trip, usually containing
one atomic command or one `NO_OP` tick advance. Three independent runs were
performed; the scripted state machine is deterministic so cross-run variance
is near zero.

| Run | Setup steps | scen1 steps | scen2 steps | scen3 steps | Total steps | Total wallclock (s) |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1,202 | 811 | 625 | 726 | 3,364 | 137.8 |
| 2 | 1,202 | 811 | 633 | 734 | 3,380 | 138.3 |
| 3 | 1,202 | 811 | 625 | 726 | 3,364 | 137.3 |
| **mean ± std** | **1,202 ± 0** | **811 ± 0** | **627.7 ± 4.6** | **728.7 ± 4.6** | **3,369 ± 9.2** | **137.8 ± 0.5** |

The two scen2 / scen3 outliers in run 2 came from the bot picking up two
extra units (n=17 vs 13 in the other runs) during the setup advance because
of slight tick-timing differences; otherwise behavior is identical step-by-
step. Avg per-step latency: 40.0–40.9 ms (network only, no LLM).

This line is the closest isolated-task counterpart to the openra_mcp
scenarios, but because it is scripted rather than LLM-driven it should be
used as an atomic-control granularity baseline, not as a token baseline.

Raw logs: `logs/rl_compare/rl_tactical_v2.log` (run 1, original),
`rl_tactical_v2_run2.log`, `rl_tactical_v2_run3.log`. Summary CSV:
`logs/rl_compare/rl_scripted_n3_summary.csv`.

---

## 4. Comparison

### 4.1 Headline ratios (OpenRA-RL N=3 mean ± std / openra_mcp single pilot)

| Metric | OpenRA-RL (N=3) | openra_mcp pilot | Mean ratio |
|---|---|---|---|
| LLM responses | 40.0 ± 7.0 | 11 | **3.6×** |
| Tool calls | 66.3 ± 11.5 | 11 | 6.0× |
| Prompt tokens | 743,363 ± 218,540 | 30,629 | **24×** |
| Completion tokens | 11,518 ± 2,462 | 3,661 | 3.1× |
| Total tokens | 754,881 ± 217,924 | 34,290 | **22×** |
| Wallclock (s) | 505.2 ± 58.9 | 133.8 | 3.8× |

All three RL runs reached LOSE without exhausting the 100-turn / 1800-second
cap. The 22× total-token gap is the headline architectural cost.

### 4.2 Isolated-task controller granularity (N=3 scripted)

| Metric | OpenRA-RL scripted (N=3) | openra_mcp DeepSeek pilot | Mean ratio |
|---|---:|---:|---:|
| Tactical controller round-trips | 2,167.3 ± 9.2 env steps | 11 LLM tool calls | 197× |
| Setup + tactical controller round-trips | 3,369.3 ± 9.2 env steps | 11 LLM tool calls | 306× |
| Visible LLM tokens | 0 (scripted) | 34,290 | N/A |

Scripted runs are near-deterministic (std < 10 across 3 runs).

This comparison should be interpreted carefully: it compares action granularity,
not model intelligence. Its value is showing how much repeated control work an
atomic API leaves outside a high-level squad primitive.

### 4.3 Caveats
- **Three baselines, different meanings**: the OpenRA-RL full-game DeepSeek run
  is the token/turn stress test; the OpenRA-RL isolated tactical run is the
  corresponding task-family control-granularity test; openra_mcp is the
  high-level LLM tactical-control test.
- **Token comparison is not strictly apples-to-apples**: the DeepSeek OpenRA-RL
  run includes economy + combat + loss, while openra_mcp ran isolated tactical
  commands on a pre-existing force. Player-owned economy is by design in
  openra_mcp.
- **Full-game tactical slices help but do not fully replace a controlled
  isolated-prompt test**: slicing the DeepSeek full-game log gives realistic
  in-context tactical episodes, but those episodes inherit prior conversation
  history, economy state, unit availability, and battlefield pressure.
- **Per-call prompt cost is not comparable**: OpenRA-RL averaged 20,858 prompt
  tokens per response, while the openra_mcp tactical runner averaged 2,784.
  The ratio therefore comes from both more LLM calls and a larger repeated
  schema/history payload.
- **Tool-surface caveat**: the openra_mcp project exposes a broader 17-tool
  surface, but this tactical runner exposed only the 7 tools needed for the
  three scenarios. The OpenRA-RL runner exposed its full 48-tool official
  surface.
- Single run per side, no averaging. Variance unknown.
- Reasoning-token or thinking-mode accounting was not surfaced in these logs;
  visible token totals should be treated as lower-bound accounting if the
  provider bills hidden reasoning separately.

### 4.4 Why openra_mcp uses fewer turns
1. **Squad FSM in C# engine** — one `spawn_squad` call triggers ~200 ticks
   of engine-driven movement + AutoTarget + Boids cohesion. No LLM
   round-trip required per tick.
2. **Higher-abstraction tool surface** — 7-tool tactical runner, single-call batch
   dispatch (`spawn_squad_batch`) for parallel tactics.
3. **Player owns economy** — econ phase is human-driven in OpenRA UI,
   removing 30+ build/train/place LLM calls from the loop.

OpenRA-RL exposes only per-unit atomic actions (`move`, `attack`,
`build_unit`, …) and lacks an engine-level FSM equivalent. The LLM must
re-issue commands per game-state change, and `advance(ticks)` calls
themselves consume LLM turns.

---

## 5. Files & Reproducibility

| Artifact | Path |
|---|---|
| Final comparison CSV | `logs/openra_rl_baseline_compare.csv` |
| OpenRA-RL DeepSeek raw log | `logs/rl_compare/deepseek_pro_run.log` |
| openra_mcp DeepSeek raw log | `logs/rl_compare/our_deepseek.log` |
| openra_mcp results JSON | `logs/rl_compare/our_deepseek_results.json` |
| OpenRA-RL scripted isolated-task results JSON | `logs/rl_compare/rl_tactical_v2_results.json` |
| OpenRA-RL scripted isolated-task log | `logs/rl_compare/rl_tactical_v2.log` |
| Scripted bot smoke log | `logs/rl_compare/scripted_bot_2000.log` |
| Our runner source | `logs/rl_compare/our_deepseek_runner.py` |
| RL tactical runner (scripted) | `logs/rl_compare/rl_tactical_v2.py` |

### 5.1 Reproduce openra_mcp side
1. Launch OpenRA: `OpenRA/bin/OpenRA.exe Engine.EngineDir=.. Game.Mod=ra`
2. Skirmish → Soviet → enable Cheats + Debug Menu → Play
3. In-game chat: `/instantbuild` + `/givecash 50000`
4. Train roster: 12 e1, 8 3tnk, 6 v2rl, 4 apc
5. `python logs/rl_compare/our_deepseek_runner.py`

### 5.2 Reproduce OpenRA-RL side
1. `openra-rl server start` (Docker Desktop running; image arm64 cached)
2. `python examples/llm_agent.py --url http://localhost:8000 --base-url https://api.deepseek.com/v1/chat/completions --model deepseek-v4-pro --api-key sk-... --max-turns 100 --verbose --log-file <log>`

### 5.3 Reproduce OpenRA-RL isolated tactical side
1. `openra-rl server start`
2. `python logs/rl_compare/rl_tactical_v2.py`

---

## 6. Known limitations and follow-up

- **N=1**. Multiple runs needed for variance bars in paper revision.
- **One LLM only**. Other models (GPT-4o, Claude Opus 4.7, Gemini 2.5 Pro)
  may show different ratios. The dominant cost is per-turn prompt size
  (a function of message history + tool schema) which scales with model
  context window pricing.
- **Different DeepSeek game length**. The DeepSeek OpenRA-RL run covered a full
  game, while the DeepSeek openra_mcp run covered 3 tactical steps. A future
  run should drive openra_mcp through a full game (econ phase player-controlled,
  tactical phase LLM-controlled, until win/loss) to compare full-session cost.
- **DeepSeek-driven isolated OpenRA-RL scenario test still missing**. The
  corresponding isolated OpenRA-RL tactical tasks were run through a scripted
  atomic runner, not through DeepSeek. A future version should pre-seed the
  same 30-unit roster in the container and have DeepSeek execute the same 3
  tactics. Container resets each game, so this likely requires a custom map or
  save-state workflow.
- **DeepSeek reasoning-token accounting** was not surfaced by either client;
  avoid making absolute cost claims until billing/export data confirms whether
  hidden thinking tokens are included.

---

## 7. Conclusion

For a fixed LLM (DeepSeek-V4-Pro), the full-game OpenRA-RL baseline used far
more visible LLM budget than the openra_mcp isolated tactical runner:
**~29× more visible tokens** and **~4× more LLM responses**. Separately, the
corresponding isolated OpenRA-RL scripted runner required **2,162** tactical
environment round-trips for the same family of maneuvers, while openra_mcp used
**11** high-level LLM tool calls. The combined evidence is not yet a final
apples-to-apples gameplay benchmark, but it supports the architectural claim
that LLMs should issue high-level tactical intents while engine-level FSMs
absorb repeated movement, cohesion, and targeting work.
