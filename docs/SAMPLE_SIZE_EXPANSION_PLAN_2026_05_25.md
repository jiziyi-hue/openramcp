# Sample Size Expansion Plan for openra_mcp Paper

Date: 2026-05-25  
Project: openra_mcp  
Purpose: plan the next experiment round before revising the Zenodo preprint / paper.

## 1. Current Situation

The project already has a working early result, but the current sample size is too small for a stronger paper.

Current evidence lines:

1. **openra_mcp + DeepSeek-V4-Pro isolated tactical tasks**
   - 3 scenarios.
   - 1 run per scenario.
   - Total: 11 LLM turns / tool calls, 34,290 visible tokens.
   - All 3 scenarios passed.

2. **OpenRA-RL + DeepSeek-V4-Pro full-game baseline**
   - 1 full economy + combat run.
   - 47 LLM responses, 78 tool calls, 994,263 visible tokens.
   - Final result: loss.
   - Full-game log can be post-hoc sliced into tactical episodes.

3. **OpenRA-RL scripted isolated tactical runner**
   - Corresponding isolated tactical tasks, but scripted rather than LLM-driven.
   - Tactical phase: 2,162 environment round-trips.
   - Useful as an atomic-control granularity baseline, not as an LLM-token baseline.

Academic interpretation:

- The result is already good enough to support an early architecture claim.
- It is not yet strong enough for a polished experimental paper because most results are N=1.
- The next goal is not to prove gameplay superiority. The goal is to make the control-cost comparison reproducible and less anecdotal.

## 2. Core Research Claim to Support

The revised paper should support this claim:

> Natural-language RTS co-pilots should expose high-level tactical primitives, such as squad-level FSM commands, rather than atomic per-unit/per-tick action APIs, because atomic APIs push repeated control work and token cost back onto the LLM.

The experiment expansion should therefore measure:

- How many LLM calls are needed.
- How many tool calls are needed.
- How many visible tokens are used.
- How stable the success rate is across repeated tactical tasks.
- How much repeated low-level control work is avoided by the squad-FSM abstraction.

## 3. Priority Order

### P0: Repeat openra_mcp Tactical Scenarios

This is the most important and easiest next step.

Run the existing 3 openra_mcp tactical scenarios multiple times:

| Scenario | Description | Current N | Target N |
|---|---|---:|---:|
| scen1_full_push_BR | All mobile units push to bottom-right | 1 | 3-5 |
| scen2_4corner_split | Split force into 4 squads and move to 4 corners | 1 | 3-5 |
| scen3_50_50_pincer | Split left/right, stage, then converge center | 1 | 3-5 |

Minimum acceptable target: **N=3 per scenario**.  
Better target: **N=5 per scenario**.

Why this matters:

- It converts the current proof-of-concept table into a repeated-measures benchmark.
- It gives success rate, mean token cost, variance, and latency spread.
- It is directly aligned with the paper's main claim.

### P1: Standardize Logging and Aggregation

Before running many trials, make sure every run produces a machine-readable row.

Required per-run fields:

| Field | Meaning |
|---|---|
| system | `openra_mcp` |
| model | e.g. `deepseek-v4-pro` |
| scenario | scenario id |
| run_id | unique id, e.g. `scen1_r03` |
| timestamp | run time |
| n_units_start | number of available controlled units |
| llm_turns | number of LLM responses |
| tool_calls | number of tool calls |
| prompt_tokens | visible prompt tokens |
| completion_tokens | visible completion tokens |
| total_tokens | prompt + completion |
| wallclock_s | scenario wallclock |
| success | true / false |
| outcome_note | e.g. `30/30 arrived`, `4 squads dispatched`, `timeout`, `schema error` |
| failure_mode | empty if success; otherwise short reason |

Output files should include:

- `logs/rl_compare/our_deepseek_results_runs.json`
- `logs/rl_compare/our_deepseek_results_runs.csv`
- `logs/rl_compare/our_deepseek_summary.csv`

### P2: Repeat OpenRA-RL Full-Game Baseline if Feasible

This is useful but more expensive and less controlled.

Current OpenRA-RL full-game DeepSeek run is N=1. If time permits:

| System | Task | Current N | Target N |
|---|---|---:|---:|
| OpenRA-RL + DeepSeek | full economy + combat game | 1 | 2-3 |

Record:

- LLM responses.
- Tool calls.
- Prompt tokens.
- Completion tokens.
- Total tokens.
- Wallclock.
- Game ticks.
- Final result.
- Units killed/lost.
- Buildings killed/lost.
- Tactical slice token cost.

Important caveat:

Do not over-claim gameplay quality from this run. Its main value is cost pressure under a full atomic-control API.

### P3: Repeat OpenRA-RL Scripted Isolated Tactical Runner

The scripted OpenRA-RL isolated runner is not an LLM-token baseline, but it is useful for action granularity.

Current result:

- Setup via ScriptedBot: 1,202 steps.
- Tactical total: 2,162 env round-trips.
- Setup + tactical total: 3,364 env round-trips.

Target:

- Repeat N=3 if easy.
- Report mean and range of environment round-trips.

Metrics:

| Metric | Meaning |
|---|---|
| setup_steps | env steps to build target roster |
| tactical_steps | env steps for scenarios |
| wallclock_s | runtime |
| n_units | units available |
| ticks_used | game ticks advanced |
| arrival_estimate | if available |

Interpretation:

- This is an atomic-control granularity baseline.
- It should be compared against openra_mcp's 11 high-level tool calls only as a control-surface comparison, not as an LLM intelligence comparison.

### P4: Optional DeepSeek-Driven OpenRA-RL Isolated Tasks

This is the cleanest possible comparison, but it may require more setup.

Goal:

- Pre-seed OpenRA-RL with the same or similar roster.
- Ask DeepSeek to execute the same 3 tactical scenarios.
- Measure tokens and tool calls.

Expected difficulty:

- OpenRA-RL container resets each game.
- Need custom map, save-state, or scripted setup phase.
- Unit IDs and map dimensions differ from openra_mcp.

This is optional for the next paper revision. It is valuable, but not a blocker.

## 4. Recommended Experimental Matrix

Minimum next round:

| System | Scenario type | Model/control | Runs |
|---|---|---|---:|
| openra_mcp | isolated tactical | DeepSeek-V4-Pro | 3 per scenario |
| OpenRA-RL | full game | DeepSeek-V4-Pro | keep existing N=1 |
| OpenRA-RL | isolated tactical | scripted atomic runner | keep existing N=1 |

Better next round:

| System | Scenario type | Model/control | Runs |
|---|---|---|---:|
| openra_mcp | isolated tactical | DeepSeek-V4-Pro | 5 per scenario |
| OpenRA-RL | full game | DeepSeek-V4-Pro | 2-3 |
| OpenRA-RL | isolated tactical | scripted atomic runner | 3 |

Stretch goal:

| System | Scenario type | Model/control | Runs |
|---|---|---|---:|
| OpenRA-RL | isolated tactical | DeepSeek-V4-Pro | 3 |

## 5. Reporting Rules

Because sample sizes will still be small, avoid heavy statistical claims.

Use:

- Mean.
- Standard deviation.
- Min / max.
- Individual run table.
- Success rate.
- Ratio ranges.

Avoid:

- p-values.
- significance claims.
- claims that one system is better at playing RTS.
- claims that DeepSeek/OpenRA-RL failure implies all atomic APIs fail.

Safe phrasing:

> Across repeated tactical trials, openra_mcp required a small and stable number of high-level tool calls, while the OpenRA-RL baselines illustrate the much larger control surface and repeated round-trip cost associated with atomic action APIs.

Unsafe phrasing:

> openra_mcp is 29x better than OpenRA-RL.

## 6. Paper Update Plan

After running the extra samples, update the paper in this order:

1. Replace the current single-run tactical table with repeated-run summary.
2. Add a variance-aware table:
   - mean tokens,
   - mean LLM calls,
   - success rate,
   - wallclock.
3. Add a figure comparing:
   - openra_mcp tool calls,
   - OpenRA-RL full-game LLM calls,
   - OpenRA-RL scripted isolated env round-trips.
4. Keep caveats explicit:
   - OpenRA-RL full-game run is not the same as isolated prompt benchmark.
   - scripted isolated runner is not an LLM baseline.
   - DeepSeek hidden reasoning-token accounting may not be fully visible.
5. Move small local model work to either:
   - Future Work, if no dataset/evaluator exists yet.
   - A short feasibility section, if dataset/evaluator is implemented.

## 7. Success Criteria for the Next Revision

The next paper revision is ready when:

- openra_mcp has at least N=3 per tactical scenario.
- Each run has machine-readable logs.
- Aggregate CSV exists.
- The OpenRA-RL full-game baseline is preserved and clearly caveated.
- The OpenRA-RL scripted isolated baseline is described as a granularity baseline.
- The paper avoids overclaiming and frames the result as architecture/control-cost evidence.

## 8. Suggested One-Sentence Summary

> The next experiment round should convert the current N=1 demonstrations into repeated tactical benchmarks, primarily by rerunning the three openra_mcp DeepSeek scenarios 3-5 times each, while using OpenRA-RL full-game logs and scripted isolated tasks as complementary baselines for LLM-token pressure and atomic-control granularity.

