# Design Direction — openra_mcp

> Where the project is heading, why each layer exists, what experiments the
> paper needs. Snapshot at 2026-05-22.

---

## 1. One-line elevator

A human plays an OpenRA skirmish. They speak natural language to Claude. Claude
emits **one DSL `dispatch_intent` call**, never coordinates. A deterministic
Python interpreter resolves names → coordinates → atomic engine orders. A
custom C# trait inside OpenRA's process is the only thing that ever touches
the simulation thread. Decisions and amplification metrics are logged on every
turn, so the system doubles as a measurable LLM-game-bridge testbed.

---

## 2. The five paths (路 A-E)

The project's mental model splits into five concurrent "paths" — they share
the same engine and bridge but address different concerns.

| Path | Layer | What it does | State |
|---|---|---|---|
| 路 A | atomic MCP tools | `move`, `attack`, `build`, `train`, … — raw orders. Player or LLM can invoke directly. | Stable |
| 路 B | scout daemon | Background polling of the world, anomaly detection, push events to `scout_events.jsonl`. | Skeleton — narrative integration TODO |
| 路 C | `HumanAssistantBot` (C# trait) | Macro automation for the human player: harvesters, base building, production queues. **Does not touch combat.** | Working, but spends cash uninvited (gap #1) |
| 路 D | `StrategyControllerBotModule` + templates | The 5+4 doctrine system (`tank_rush`, `infantry_swarm`, `balanced`, `turtle`, `raid_harass`, + P3 flagships). Switch with `set_strategy`; the trait grants `enable-strategy-<name>` conditions that gate sibling bot modules in `strategy_templates.yaml`. | Working; leaks SquadManager onto humans (see GAME_MECHANICS.md §2) |
| 路 E | manual tactical override | DSL verbs `attack` / `defend` / `pincer` / `feint` / `set_stance` / `retreat` — used on top of the running doctrine when the player wants explicit micro. | Working |

The **canonical** way to drive the game is 路 D + 路 E. The LLM speaks
high-level doctrine; the player can override with tactical verbs. 路 A exists
as an escape hatch and as the substrate everything else compiles down to.

---

## 3. Layer stack (player → engine)

```
Player (NL: 中文 or English)
        │
        ▼
Claude (LLM)
  • reads CLAUDE.md, INTENT_DSL.md, RA_ACTOR_NAMES.md
  • emits ONE dispatch_intent(intent_json) call per turn
  • fills `meta` for paper metrics (model, latency, tokens)
        │ MCP stdio
        ▼
mcp_server/ (Python)
  ├─ server.py           ── 31 tools, all routed through OpenRATransport
  ├─ intent_dsl.py       ── Pydantic schema, Literal enums = single source of truth
  ├─ interpreter.py      ── DSL → atomic dispatch, deterministic, zero LLM
  ├─ geometry.py         ── flank waypoints, pincer rendezvous, cautious engage points
  ├─ logging.py          ── SessionLogger → decisions.jsonl + session_summary.json
  ├─ transport.py        ── TCP client (newline-delimited JSON over 127.0.0.1:7777)
  └─ scout_daemon.py     ── 路 B; runs as separate process, polls every 30 s
        │ TCP JSON
        ▼
trait_src/ (C# traits, compiled into OpenRA)
  ├─ McpBridge.cs                      ── TCP server, dispatches commands on the game thread
  ├─ HumanAssistantBot.cs              ── 路 C: macro IBotTick modules only
  ├─ StrategyControllerBotModule.cs    ── 路 D: condition-gated template system
  └─ GrantConditionOnHumanOwner.cs     ── yaml-side condition for macro modules
        │
        ▼
OpenRA engine (simulation thread)
  • units move, structures build, resources flow
```

---

## 4. Why DSL — and why one call

The repeating temptation is to let the LLM chain low-level atomics: `train e1`,
`wait`, `move`, `attack`. That has three problems:

1. **Tokens scale linearly with depth.** A pincer is ~6 atomics; emitting them
   one per turn burns input tokens revisiting the world state each time.
2. **Non-determinism.** Two runs of the same player prompt produce different
   atomic chains, breaking reproducibility for research.
3. **Hallucination surface.** Free-form code or JSON lets the LLM invent
   coordinates, unit kinds, stances. Most off-by-one bugs come from there.

The DSL solves all three:

- **One call** — the interpreter expands one intent into N atomics, so the
  LLM pays for one round-trip regardless of plan depth.
- **Literal enums** — every value the LLM fills is constrained by Pydantic.
  Hallucination becomes a validation error caught before any order is issued.
- **Deterministic interpreter** — `interpret(intent_payload)` is pure Python.
  Same intent → same atomic sequence. Reproducible.

This is the architectural claim the paper has to defend: *enum-constrained
single-call DSL + deterministic interpreter is strictly more token-efficient,
hallucination-safe, and reproducible than multi-step plan JSON (HIVE-style) or
raw NL-to-atomic chains.*

---

## 5. The strategy template system (路 D in detail)

Each of the 5 core templates (`tank_rush`, `infantry_swarm`, `balanced`,
`turtle`, `raid_harass`) is implemented as a triplet of bot-module YAML blocks
in `OpenRA/mods/ra/rules/strategy_templates.yaml` (generated by
`scripts/gen_strategy_templates.py`):

- `BaseBuilderBotModule@<template>` — building fractions, limits, power budget
- `UnitBuilderBotModule@<template>` — unit production priorities, limits
- `SquadManagerBotModule@<template>` — squad size, target priorities, exclusions

Every block carries `RequiresCondition: enable-strategy-<template>`.

`StrategyControllerBotModule` holds the current template state. When
`set_strategy(template=...)` arrives, it:

1. Revokes `enable-strategy-<old>`.
2. Applies `transition_mode`: `soft` (drain queues naturally), `hard` (clear
   queues + dissolve squads), `hybrid` (keep in-flight combat, swap idle).
3. Grants `enable-strategy-<new>`.

Result: a whole-doctrine swap with one DSL call. The sibling modules
auto-enable/disable based on conditions; no manual wiring per template.

**4 flagship templates** (`tesla_wall`, `chrono_blitz`, `siege_arty`,
`paratroop_rain`) are scaffolded in `vocab()` but not in the YAML yet —
they're the P3 backlog.

---

## 6. Logging — paper-grade telemetry

Every `dispatch_intent` writes one line to `logs/<session_id>/decisions.jsonl`:

```json
{
  "ts": 1779449058,
  "tick": 5194,
  "nl_input": "派工兵去占领油井",
  "intent_payload": { "intent": "capture", ... },
  "result": { "ok": true, "narrative": "...", "actions_taken": [...] },
  "meta": { "llm_model": "claude-opus-4-7", "llm_latency_ms": 2500,
            "llm_input_tokens": 18000, "llm_output_tokens": 120 },
  "world_before": { ... }, "world_after": { ... }
}
```

`end_session(result="win|lose|draw", end_tick=N)` finalizes
`session_summary.json` with:

- `nl_commands` — total dispatch_intent calls
- `atomic_orders` — sum of atomics emitted by the interpreter
- `mean_amplification_ratio` — `atomic_orders / nl_commands`
- `template_switches` — `set_strategy` count
- `apm`, `latency_p50/p95`, `token_cost_usd`

The amplification ratio is the headline metric. Hypothesis: DSL-mediated play
produces 3–5× more atomic engine events per NL token than direct NL → atomic
chains. This is what the paper has to measure and defend.

---

## 7. Research positioning

### 7.1 The 3-axis niche

After reading HIVE, HIMA, SwarmBrain, TextStarCraft II, and Voyager, the
unfilled cell is at the intersection of:

| Axis | Position |
|---|---|
| Engine | **Real RTS** (OpenRA, C# trait injection) — only SwarmBrain shares this |
| Human | **Human-in-loop chief-of-staff** — only HIVE has human, in a toy env |
| Protocol | **MCP as LLM↔game bridge** — no paper uses MCP today |

No prior paper occupies all three. That's the wedge.

### 7.2 What HIVE could trivially copy

HIVE (2412.11761) already publishes a multi-step JSON plan format very close
to our `dispatch_intent`. If they extend to a real engine, they consume the
niche. Mitigations:

1. **Preprint on arxiv ASAP.** The 6-month window won't last.
2. **Show single-step enum-constrained DSL outperforms multi-step plan JSON**
   on token cost and on reproducibility. The deterministic interpreter is the
   defendable claim — HIVE's plan-as-JSON still re-prompts on plan failures.
3. **Lean on the human-study angle.** HIVE's human-in-toy-env is weaker than
   ours; CHI/CoG reviewers care about real ergonomics.

### 7.3 Required experiments

| # | Experiment | Metric | Status |
|---|---|---|---|
| 1 | Token cost: DSL vs raw NL→atomic vs HIVE-style multi-step plan | input+output tokens / game | Not started |
| 2 | Spatial correctness: LLM-computed vs interpreter-computed coords | error rate (off-by-N cells) | Not started |
| 3 | Win rate vs OpenRA built-in AI across difficulties | wins / total | Not started |
| 4 | NL → unit-action latency | ms (p50, p95) | Logging in place |
| 5 | User study N=10-20 | NASA-TLX, SUS, completion rate | Not started |
| 6 | Ablation: macro bot on/off, scout daemon on/off, DSL vs raw atomics | win rate, token cost | Not started |

### 7.4 Venues (in submission order)

- arxiv preprint (immediate — defensive priority)
- IEEE CoG 2026 (primary target)
- AIIDE 2026
- CHI 2026 (if user study lands)
- COLM 2026 (HIMA's venue precedent)
- NeurIPS workshop (fast slot for a short version)

---

## 8. Intelligence gaps — known UX/correctness debt

From `feedback_intelligence_gaps.md` and the 2026-05-22 session. Ordered as
agreed (quick wins first):

| # | Gap | Fix area | Priority |
|---|---|---|---|
| 1 | Bot macro spends cash without consent | trait_src (HumanAssistantBot flag) + DSL `macro_paused` | P0 — partly done via `set_strategy.macro_paused` |
| 2 | `force.name:"all"` selects buildings | mcp_server (new `force.kind:"mobile"`) | P0 |
| 3 | Water silently blocks ground moves | mcp_server (post-move `Activity` inspection) | P1 |
| 4 | Faction/tech mismatch | server.py (game-start capability self-check) | P1 |
| 5 | `scout` capped at 3 units | interpreter (auto-split or DSL field) | P2 |
| 6 | No `cancel_production` atomic | McpBridge + server.py | **P0 — hit during 2026-05-22 (24 e6 queued)** |
| 7 | No "auto-send new units" standing orders | trait `on_unit_spawn` hook or daemon | P2 |
| 8 | Hidden tactics undiscoverable | `report what:"capabilities"` + narrative hints | P1 |
| 9 | TCP bridge drops on game restart | transport.py retry + back-off | P1 |
| 10 | Strategic feedback weak (force-vs-choke) | interpreter pre-attack heatmap check | P2 |
| 11 | Auto-defense passive on perimeter breach | trait detect + auto-dispatch defend | P1 |
| 12 | No periodic auto-report | scout_daemon narrative push | P2 |
| 13 | No opening battle plan | server.py first-`get_state` triggers 3-phase plan | P2 |

### Newly discovered (2026-05-22)

| # | Gap | Source | Status |
|---|---|---|---|
| 14 | **No `capture` atomic** — engineers cannot capture neutral buildings | GAME_MECHANICS.md §1.4 | **fixed 2026-05-22** — `capture` atomic + `CmdCapture` schema + McpBridge `HandleCapture` |
| 15 | **`SquadManagerBotModule` leaks onto human PlayerActor when template active** — units auto-walk to enemy | GAME_MECHANICS.md §2.3 | **fixed 2026-05-22** — `SquadManagerBotModule@<tmpl>` now requires `&& !enable-human-macro` (see `scripts/gen_strategy_templates.py`) |
| 16 | **`force "all"` includes harvesters + buildings** — "全军出击" stops mining, drags immovables | force-resolve in `interpreter._force_by_group` | **fixed 2026-05-22** — `all`/`mobile` resolve to combat-mobile only; `everything` is the escape hatch for the old behavior |
| 17 | **No fire concentration en route** — AttackMove units pass through skirmishes one-by-one instead of focusing the nearest threat | interpreter (`_do_attack`) lacks engage-on-contact waypoint loop | open — needs background tactical daemon |
| 18 | **No formation cohesion** — fast units (ftrk/jeep) arrive alone, get picked off, slow units trickle in behind | interpreter has no spread / regroup gate during attack-move | open — needs cohesion gate in attack daemon |

---

## 9. Roadmap (current best guess)

### Phase 1 — fix the 2026-05-22 blockers (next week)

- [ ] Add `capture` atomic (McpBridge + server.py + DSL `capture` intent)
- [ ] Audit `strategy_templates.yaml` for `SquadManagerBotModule@*` on humans;
      gate behind a `bot-controlled` condition
- [ ] Add `cancel_production(factory_id, item, count)` atomic
- [ ] Surface path-blocked feedback in move-order responses

### Phase 2 — close the UX gaps (P0/P1 from §8)

- [ ] `force.kind:"mobile"` filter
- [ ] `report what:"capabilities"` + narrative hints when player stuck
- [ ] Auto-defense trait reaction on perimeter breach
- [ ] TCP transport retry/back-off
- [ ] Game-start capability self-check (faction + buildable list)

### Phase 3 — paper experiments (§7.3)

- [ ] Run experiment #1 (token cost ablation) — needs raw-NL and HIVE-mimic baselines
- [ ] Run experiment #3 (win rate vs OpenRA AI difficulties) — needs ~50 games per difficulty
- [ ] Build experiment #5 (user study) protocol — NASA-TLX, SUS instruments
- [ ] Arxiv preprint draft from logs + ablation results

### Phase 4 — flagship templates (P3)

- [ ] `tesla_wall`, `chrono_blitz`, `siege_arty`, `paratroop_rain` YAML +
      generator support
- [ ] Per-template scout / opening / endgame heuristics in narratives

---

## 10. Design principles to keep

1. **Determinism in the interpreter.** Never call the LLM from inside a
   `dispatch_intent` resolution. If a new feature wants LLM judgment in the
   loop, that's a different turn.
2. **Enums everywhere.** New fields → new `Literal` types in `intent_dsl.py`,
   never free-form strings.
3. **One canonical path.** `dispatch_intent` is the default; atomics are
   escape hatches. New verbs go into the DSL first.
4. **Trait edits are sim-thread-only.** Anything from MCP goes through
   `Game.RunAfterTick()` in `McpBridge` — no direct world mutation from the
   TCP accept loop.
5. **Log everything that touches the LLM.** `meta` on every `dispatch_intent`
   call; `end_session` on every game. Without those, the paper has nothing.
6. **Player owns combat. Trait owns macro. LLM owns translation.** When a
   piece of behavior is unclear which side owns it, default to the trait if
   it's reactive/local, the LLM if it requires reading the player's intent.

---

## Cross-references

- Capture / bot / order-string mechanics: [GAME_MECHANICS.md](GAME_MECHANICS.md)
- DSL field reference: [INTENT_DSL.md](INTENT_DSL.md)
- LLM-side instructions: [SYSTEM_PROMPT.md](SYSTEM_PROMPT.md)
- Actor name table: [RA_ACTOR_NAMES.md](RA_ACTOR_NAMES.md)
- Player tutorial: [TUTORIAL.md](TUTORIAL.md)
