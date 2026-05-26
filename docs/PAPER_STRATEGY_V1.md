# Paper Strategy V1

> Working note for the first public preprint / idea-claim version.
> Updated: 2026-05-25.

## Positioning

This project should be framed as a mixed-initiative RTS game co-pilot, not as an
autonomous game-playing agent.

The player owns strategy and judgment. The system translates natural-language
tactical intent into executable MCP calls that control existing units in OpenRA.
The first paper should claim the idea and architecture, then leave local-model
distillation as the next phase.

One-line claim:

> Natural language can serve as a practical tactical control layer for complex
> RTS unit operations when an LLM composes task-level MCP primitives instead of
> driving per-tick atomic actions.

## Main Research Question

Can a human player use natural language to reliably direct complex RTS tactics
in a real game engine, with the LLM acting as a tactical translator and
co-pilot rather than an autonomous player?

## Current Core Claim

The strong version of the paper is:

1. Complex tactical commands can be expressed in natural language.
2. The LLM can translate those commands into auditable MCP tool calls.
3. A small engine-side primitive set is enough: Assault and Protection.
4. Higher-level tactics can be composed on the LLM/Python side.
5. The result is a human-in-the-loop game co-pilot, not an AI replacement for
   the player.

## Evidence Already Available

- v2 NL-capability suite: 10/10 post-ablation pass.
- Live LLM demo: 8 consecutive player commands, 0 fail.
- Route / paraphrase work: already done.
- Manual-control comparison script: already done.
- Complex tactical repertoire: large-unit move, multi-squad split, mixed
  composition, pincer, feint plus raid, eight-direction dispersal, return base,
  attack base.
- Ablation: removing unused daemon/DSL machinery kept the capability suite
  passing, supporting the two-primitives claim.

## Metrics To Extract

Use game-side logs and Claude transcript logs to produce a lightweight metrics
table:

- Scenario pass rate.
- Number of player NL commands.
- Number of MCP tool calls.
- Number of spawned squads / concurrent squads.
- Units controlled per command.
- Median time from user prompt to first tool call.
- Token usage per command where transcript usage is available.
- JSON/tool-call error count.
- Commands with matching `nl_input` in `decisions.jsonl`.

The helper script is:

```powershell
python -m mcp_server.tools.paper_metrics --out logs/paper_metrics.json
```

## Small Local Model Track

For the first preprint, small local models should be future work, with a short
preparation paragraph:

- Use Claude/OpenAI-quality runs to generate supervised pairs:
  `(natural language + compact game state) -> MCP tool call JSON`.
- Evaluate small models on strict JSON validity, enum validity, tool-call
  accuracy, and scenario pass rate.
- The target is low-latency, low-cost local tactical translation, not general
  autonomous RTS play.

Do not claim a trained local model exists until the training and evaluation run
is complete.

## Publication Posture

The first version should be an early preprint / demo system paper:

- Claim the idea, architecture, and working prototype.
- Use careful language: "demonstrates", "suggests", "prototype evidence",
  "initial evaluation".
- Avoid claiming solved general RTS control.
- Put small-model distillation and broader human studies in Future Work.
