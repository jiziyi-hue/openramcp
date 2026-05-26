# Paper Outline V2

> Replaces the stale daemon/15-intent outline. This version follows the
> post-ablation architecture: two engine primitives plus LLM-side tactical
> composition.

## Working Title

Natural-Language Tactical Control for RTS Games via MCP-Based Mixed-Initiative
Co-Piloting

## One-Sentence Thesis

Natural language can serve as a tactical control layer for real-time strategy
games when an LLM translates player intent into auditable, task-level MCP calls
over a small set of engine execution primitives.

## Paper Type

Early preprint / demo system paper.

This is not yet a full human-subjects study and not yet a small-model paper.
The first version should claim the idea, the architecture, and the working
prototype, while positioning local-model distillation as future work.

## Abstract Draft

Real-time strategy games require players to coordinate many units across
multiple simultaneous fronts, making complex tactical control mechanically
demanding even when the strategic decision is clear. We present `openra_mcp`, a
mixed-initiative game co-pilot that lets a human player express tactical intent
in natural language and translates that intent into executable unit-control
commands through the Model Context Protocol (MCP). Unlike autonomous RTS agents,
the system does not replace the player: the human retains strategic judgment,
economic control, and battlefield interpretation, while the LLM acts as a
tactical translator that composes squad-level actions. In the current OpenRA
prototype, the game-side interface is reduced to two execution primitives,
Assault and Protection, while higher-level tactics such as pincer movement,
multi-squad splitting, feint-and-raid coordination, route constraints, and
time-sequenced attacks are composed on the LLM/Python side through MCP calls.
An initial capability suite and live LLM demonstration show that natural
language can express and execute complex RTS unit maneuvers involving dozens of
mixed units. We discuss the architecture, evaluation evidence, limitations, and
a path toward distilling high-quality LLM demonstrations into a small local
low-latency control model.

## Research Questions

RQ1. Can natural-language commands reliably express complex tactical RTS unit
operations such as splitting, pincer movement, route constraints, time
sequencing, and recovery?

RQ2. Can those commands be translated into auditable MCP tool calls in a real
RTS engine without making the LLM an autonomous player?

RQ3. Is a small set of game-side squad primitives sufficient when higher-level
tactical composition is moved to the LLM/Python layer?

## Contributions

1. A mixed-initiative RTS co-pilot architecture that separates human strategic
   judgment from LLM tactical translation.
2. An MCP-based bridge from natural-language player intent to executable
   OpenRA unit-control calls.
3. A two-primitive execution design: Assault for pushing units and Protection
   for holding/defending positions.
4. Demonstrations of complex tactical composition in a real RTS engine:
   multi-squad split, mixed-unit coordination, pincer, feint plus raid, route
   constraints, sequencing, and recovery.
5. A future-work path from high-quality LLM demonstrations to a small local
   low-latency model trained to emit MCP calls.

## Section Plan

### 1. Introduction

- RTS control problem: the player may know the tactic but executing it across
  many units is mechanically burdensome.
- Existing game AI often tries to replace the player.
- This paper instead studies co-piloting: natural language as a tactical
  control layer.
- State the authority split:
  - human owns strategy, economy, and battlefield judgment;
  - LLM owns tactical translation;
  - engine owns pathfinding, collision, attack execution, and unit autonomy.
- Contributions.

### 2. Related Work

Group prior work by role:

- Autonomous RTS/game agents: AlphaStar-style and OpenRA-RL-style work.
- LLM game agents and embodied agents: Voyager, SwarmBrain, TextStarCraftII.
- LLM multi-agent or hierarchical planning systems: HIVE, HIMA.
- Mixed-initiative and human-AI teaming.
- Tool-use protocols and MCP-style interfaces.

Key contrast:

Most prior work asks how an AI can play. This work asks how a human can command
complex tactics through an AI co-pilot.

### 3. System Overview

Core diagram:

```text
Human natural-language intent
        ->
LLM tactical translator
        ->
MCP tool calls / spawn_squad_batch
        ->
OpenRA squad FSM primitives
        ->
Engine-level unit autonomy
```

Explain why the design avoids per-tick LLM control:

- lower latency pressure,
- fewer calls,
- clearer audit trail,
- less dependence on LLM numerical reasoning.

### 4. Two Execution Primitives

Explain the post-ablation design:

- Assault: push a unit set toward a target cell or actor.
- Protection: defend or hold a target cell.

Explain archived alternatives:

- patrol, escort, explore, and harass were initially attempted as engine-side
  FSMs but were less stable or mixed strategy into execution.
- higher-level tactics are now composed above the engine primitives.

### 5. LLM-Side Tactical Composition

Show composition patterns:

- split force by unit kind,
- split force by spatial reference,
- pincer movement,
- feint plus raid,
- route constraint,
- time-sequenced attack,
- failure recovery.

Use examples from `mcp_server/tools/compose_*.py` and `scenarios_v2.py`.

### 6. Evaluation

Tables to include:

1. NL capability suite T1-T10.
2. Live LLM demo sequence: 8 player commands.
3. E7 baseline tactical demonstrations.
4. Ablation: tools/LOC removed while capabilities remain.
5. Optional telemetry table from `paper_metrics.py`.

Important wording:

- The v2 suite reached full pass after targeted retries and threshold fixes.
- Raw CSVs are preserved.
- Development-session telemetry is not the same as a controlled user study.

### 7. Discussion

- Why co-pilot is different from autonomous play.
- Why task-level primitives fit LLM strengths better than per-tick micro.
- Why information discipline matters: the system should not tell the player
  whether they "can win"; it executes declared tactical intent.
- Why local-model distillation is plausible: the target output is structured
  MCP JSON, not open-ended strategic reasoning.

### 8. Limitations

- Sandbox/prepared-unit scenarios are not full competitive matches.
- Clean OpenRA-RL baseline is not yet run.
- The current local small model is not trained yet.
- Current telemetry mixes development and evaluation sessions unless rerun
  cleanly.
- Only a subset of factions/unit compositions has been systematically tested.

### 9. Future Work

- Clean repeated evaluation with mean/std.
- Human-control comparison using clicks/time/action count.
- Small local model trained from LLM-generated demonstrations.
- Stronger technical enforcement of information/economy boundaries.
- More maps, factions, enemies, and online human pilots.

