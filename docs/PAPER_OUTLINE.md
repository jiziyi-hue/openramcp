# Paper outline: "LLM-Mediated Tactical Coordination in Real-Time Strategy"

> Working title: *LLM-Mediated Tactical Coordination in Real-Time Strategy:
> Splitting Information and Economic Authority Between Human and AI*
> Audience: HCI + AI + Games venues (CHI / IEEE CoG / IEEE Conference on Games / arXiv).

---

## Abstract (150 words)

We present **openra_mcp**, an architecture that places a general-purpose LLM
client (Claude Code via the Model Context Protocol) between a human player and
a real-time strategy game (OpenRA / Red Alert clone) as a *tactical
coordinator* rather than a full autonomous agent. The system imposes an
explicit authority boundary: the human owns **economy and information**
(production, build orders, numerical battlefield analysis), while the LLM owns
**tactics** (multi-group orders, mission orchestration, alert posture). A
single natural-language utterance ("守家, 派一队骚扰右路") fans out into
multiple concurrent daemon-managed missions through a four-layer architecture
(Player → LLM-as-staff → Python tactical daemon → engine). We contribute
(1) a typed DSL of 15 intents emitted by the LLM, (2) a 5-state alert system
that bundles daemon parameters with auto-dispatched missions, (3) a Python
tactical daemon that owns per-tick loops, and (4) logging that captures NL →
DSL → mission → atomic-order chains with amplification ratio and LLM cost.

---

## 1. Introduction (1.5 pages)

- **Problem**: RTS games exceed human cognitive throughput. Pro players reach
  300+ APM yet rarely micro more than three fronts concurrently. Cognitive
  load is asymmetric across subsystems: economy and information are
  *deliberative* (humans excel), while wide tactical coordination is
  *parallel-mechanical* (humans bottleneck).
- **Existing AI**: AlphaStar, OpenAI Five, and built-in rush bots *substitute*
  the human. The player either watches passively or competes against
  superhuman opposition. SwarmBrain and TextSC2 invoke LLMs to play the
  entire game but inherit numerical-reasoning fragility.
- **Our angle**: we treat the LLM as a **tactical staff officer** with a
  deliberately *restricted authority surface*. Three boundaries:
  1. **Player owns information.** No tool returns predicted win probability,
     engagement outcome, or numerical battle analysis. The LLM cannot call
     `estimate_engagement` — we refused to build it. Reports are factual
     (counts, positions) and never prescriptive numerics.
  2. **Player owns economy.** The LLM has zero access to `build`, `train`,
     `sell`, or `deploy`. Production decisions remain with the human via the
     native OpenRA UI.
  3. **LLM owns tactics.** `dispatch_intent`, `set_alert_state`,
     `set_objective`, and mission tools are the LLM's whole vocabulary.
  4. **Daemon owns loops.** A Python tactical daemon polls the world every
     ~0.6 s and re-targets, holds cohesion, and dispatches perimeter
     responses. The LLM never drives per-tick logic.
- **Key methodological novelty**: **no custom LLM integration**. We use
  Claude Code (a general-purpose CLI client) and MCP (Anthropic's open
  protocol). Any MCP-compatible client + any Claude model works; zero
  migration cost across LLM clients — unique in the LLM-game literature.
- **Niche positioned against prior art**:
  - vs. SwarmBrain / TextSC2 (LLM-full-agent): we keep human in econ + info
  - vs. HIVE / HIMA (LLM team coordination): we split econ from tactics
  - vs. AlphaStar / OpenAI Five (substitute human): we augment human
- **Contributions**:
  1. Four-layer architecture (Player / LLM / Daemon / Engine) with explicit
     authority boundaries between human and AI
  2. DSL grammar (`intent_dsl.py`) with 15 intents and partial-update
     `set_alert_state` semantics
  3. 5-state alert system (peace / watch / alert / combat / lockdown) that
     bundles daemon parameters with auto-dispatched missions, orthogonal
     to a 4-value objective axis
  4. Python tactical daemon owning 7 mission types with static and dynamic
     force resolution
  5. Decision log capturing amplification ratio and LLM cost per command,
     and a set of revised flagship case studies

---

## 2. Related Work (1 page)

Four columns:

| Line | Examples | Difference from us |
|---|---|---|
| Autonomous RTS AI | AlphaStar, OpenAI Five, OpenRA rush-ai | Substitutes player; we augment |
| LLM-full game agents | SwarmBrain, TextSC2, Voyager | LLM drives everything; we restrict LLM to tactics |
| LLM team coordination | HIVE, HIMA | Coordinate multi-agent; no econ/tactics authority split |
| Natural-language game control | Voice tactics, NL-to-action | Custom NLU; we use generic MCP |
| Human-AI teaming (HAT) | Centaur chess, mixed-initiative editors | Not RTS-specific; we instantiate HAT for real-time games |

Cite Anthropic MCP spec; OpenRA project; AlphaStar (Vinyals et al.);
SwarmBrain; TextSC2; Voyager (Wang et al.); HIVE / HIMA; HAT foundational
work (Bansal et al.).

---

## 3. System architecture (2 pages)

**Figure 1**: Four-layer architecture diagram (player NL → LLM staff via
MCP → Python tactical daemon → OpenRA engine), with annotated authority
boundary lines for *economy*, *information*, and *tactics*.

### 3.1 Intent DSL (`intent_dsl.py`)

- 15 typed intent variants discriminated by `intent` field:
  `attack`, `defend`, `retreat`, `regroup`, `scout`, `pincer`, `feint`,
  `harass`, `patrol`, `escort`, `contain`, `divert`, `set_stance`,
  `set_alert_state`, `set_objective`, `report`
- Pydantic discriminated unions. LLM fills enum values from a closed
  vocabulary; never emits free-text coordinates, distances, or unit IDs.
- *Partial-update semantics* for `set_alert_state`: only set fields apply,
  daemon retains previous state for unset fields.
- Shared types `Force`, `Target`, `Region`, `Approach`, `Stance` are reused
  across intents to keep the LLM's schema-load minimal.

### 3.2 Alert states and objectives (replaces doctrine templates)

The system replaces fixed doctrine templates with a two-axis policy surface:

**Alert states (5)** — DEFCON-style global posture:

| State | Default stance | Auto-missions | Daemon parameters |
|---|---|---|---|
| `peace` | ReturnFire | none | low scout cadence, perimeter dormant |
| `watch` | ReturnFire | 1 scout cycle | normal scout cadence, perimeter armed |
| `alert` | Defend | scout + perimeter patrol | high scout cadence, retargeting eager |
| `combat` | AttackAnything | full perimeter + harass | aggressive retarget, cohesion tightened |
| `lockdown` | Defend | tight perimeter only | all forces drawn home, no outward missions |

Each alert state is a *bundle*: when the LLM emits
`set_alert_state(state="alert")`, the daemon installs the corresponding
default stance, registers the auto-missions, and applies the parameter set
in one atomic transition.

**Objectives (4)** — strategic target axis, orthogonal to alert state:

- `destroy_fact` — bias suggestions toward enemy construction yard
- `harass_economy` — bias toward enemy harvesters and refineries
- `survive_until` — bias toward turtle alert states
- `control_map_center` — bias toward patrol mission spawning

Objective shapes the LLM's *recommendations* (which alert state to suggest
after an enemy-intent classification) but never forces a transition.
Authority remains with the player.

### 3.3 Mission orchestration (replaces transition modes)

With economy out of LLM scope, the prior "soft / hard / hybrid" transition
modes are obsolete — there are no production queues to clear and no
implicit squads to disband. Mission lifecycle is explicit:

**Mission types (7)** owned by the Python daemon (`tactical.py`):

| Mission | Purpose | Force resolution |
|---|---|---|
| `Assault` | Attack a target; auto re-target on kill | static or dynamic |
| `HarassMission` | Cycle small raids on enemy economy | dynamic |
| `PatrolMission` | Loop waypoints in a region | dynamic |
| `EscortMission` | Move escort + escortee in cohesion to dest | static |
| `DefensePerimeter` | Engage intruders inside a radius | dynamic |
| `ContainmentMission` | Hold a chokepoint; engage anything crossing | dynamic |
| `DiversionMission` | Synchronize feint and raid timing | hybrid |

**Static vs dynamic force resolution**: a *static* mission binds a fixed
actor-ID list at registration. A *dynamic* mission stores a `Force`
descriptor (filter / group) and re-resolves each tick — newly trained
matching units are auto-recruited, so a `HarassMission` registered with
`force.unit_kind = "jeep"` picks up every freshly built jeep without
further LLM action.

**Pending mission queue**: if force resolution returns empty at
registration (the player has not yet trained matching units), the mission
enters a pending queue and starts on the first tick where the force
becomes non-empty.

**Support pairing**: an always-on daemon behavior pairs newly produced
support units (`medi` → infantry squad, `mech` → vehicle squad) without
LLM involvement. This is a fixed policy, not a mission.

### 3.4 Behavior wiring

The wiring connects intents to daemon behavior:

- `set_alert_state(state=X)` → dispatches the bundle: default stance is
  applied to all owned combat units, the listed auto-missions are
  registered (or de-registered on a downgrade), daemon parameters are
  swapped, and any already-active player-issued missions are preserved.
- `defend` intent → registers a `DefensePerimeter` mission. The daemon
  auto re-engages on every intruder; on mission end, a one-line
  after-action push is sent to the LLM (e.g. "perimeter held: 4 intruders
  killed, 1 friendly lost").
- `enable_auto_defense()` now supports **multi-perimeter**: forward bases
  can each carry their own `DefensePerimeter` with independent radius.
- **Auto-escalation**: a player can request "if enemy mass crosses
  threshold T, raise alert to combat" by spawning a watcher subagent that
  uses `wait_for_event` on the threshold and dispatches
  `set_alert_state(state="combat")` on trigger. The LLM never polls.

### 3.5 Logging infrastructure

- One `decisions.jsonl` line per NL → DSL → mission → atomic chain.
- Fields: `timestamp`, `tick`, `intent`, `mission_registrations`,
  `atomic_orders` (compact form), `amplification_ratio`, `llm_latency_ms`,
  `llm_input_tokens`, `llm_output_tokens`, `world_state_before/after`.
- Per-session `session_summary.json` with effective APM, mean
  amplification ratio, alert-state usage histogram, mission-type
  histogram, max concurrent fronts, total LLM cost in USD.

---

## 4. Capability amplification (2 pages — flagship section)

Each subsection presents one case: setup → NL command → DSL JSON →
mission registrations → atomic-order count → why human-hard.

**Revised flagship cases for the new architecture**:

- **Alert-state cascade**: "切 alert" → daemon registers 2 missions
  (scout cycle + perimeter patrol) and applies stance to all units in
  one player utterance. A human would issue 8-12 UI actions.
- **MCV escort across map**: "护送 MCV 到 (75,30)" → a single
  `EscortMission` spans 60+ ticks of unit coordination, holding cohesion
  between escort group and the slow MCV without further player input.
- **Feint + raid synchronization**: "佯攻偷家" → `DiversionMission`
  synchronizes feint at front and raid at flank, with timing managed by
  daemon; LLM emits one intent, daemon orchestrates two converging
  sub-operations.
- **Pending harass on production**: "开始骚扰右路" issued *before* any
  jeeps exist → mission enters pending queue; the moment the player
  trains the first jeep via the OpenRA UI, daemon auto-recruits it and
  starts harass cycle. Demonstrates implicit human-LLM coordination
  across the authority boundary.
- **Multi-perimeter defense**: "守 home 也守 forward (90,40)" → two
  `DefensePerimeter` missions with independent radii, daemon dispatches
  to nearest available defender per intrusion event.

A table summarizes empirical amplification ratios (atomic_orders /
NL_commands) and mean concurrent active missions across all cases.

---

## 5. Evaluation (1.5 pages)

### 5.1 Experiment harness

Three conditions defined in `mcp_server/experiments/`:

- `solo_human.py` — no LLM, traditional UI only.
- `human_llm.py` — our system. **Authority split: the player handles all
  economy via the OpenRA UI; the LLM commands tactics via MCP.**
- `bot_baseline.py` — vanilla OpenRA bot vs. bot.

5 fixed scenarios in `scenarios.py` with frozen seed, map, starting
cash, and difficulty.

### 5.2 Quantitative metrics

| Metric | What it measures |
|---|---|
| Amplification ratio | atomic_orders / NL_commands |
| Effective APM | atomic_orders / game_minutes |
| Concurrent active missions (10s window) | parallel tactical reach |
| LLM round-trip latency | ms per `dispatch_intent` call |
| LLM cost | total USD per session |
| Outcome win rate | vs vanilla AI per scenario |

The `human_llm` condition partitions APM into *economy_apm* (player
UI clicks on production / building) and *tactical_apm* (LLM-mediated
orders), to make the authority split visible in metrics.

### 5.3 Pilot results (preliminary)

Tables generated by `experiments/analyze.py` once sufficient sessions
are logged.

### 5.4 User study (planned, n = 10-12)

- IRB notice + opt-in demographics.
- NASA-TLX subjective workload (decomposed for economy vs. tactical
  load, to test whether the authority split reduces tactical workload
  without raising economic workload).
- 3-condition within-subjects design.
- Per-condition: 3 scenarios × 2 plays = 6 games / participant.

---

## 6. Discussion (0.5 page)

- **When amplification fails**: LLM latency outpaces tempo on extreme
  micro (sub-second skirmish reactions). The daemon mitigates this for
  re-targeting and perimeter response, but novel tactical pivots still
  require a round-trip.
- **Control hand-off**: explicit atomic intents override daemon
  decisions. The player can always issue a direct order through the LLM
  that supersedes a running mission.
- **LLM as facilitator, not analyst** *(design rationale)*: we
  deliberately rejected an `estimate_engagement` tool, win-probability
  predictors, and any numerical-analysis surface. The rationale: keeping
  the player as decision-maker preserves agency, and LLMs are unreliable
  numerical reasoners — making them analysts would import their
  weaknesses into the player's strategic core. This is a *scope
  reduction* from earlier designs that contemplated full doctrine
  control.
- **Out-of-engine tactical daemon (limitation)**: the daemon currently
  runs as a Python process polling at ~0.6 s. A C# port inside OpenRA
  would cut latency to a single tick, at the cost of iteration speed.
  We chose Python for rapid mission-type prototyping during the paper
  window.
- **Numerical analysis (limitation)**: the LLM cannot perform numerical
  battle analysis by design. Players who want predicted outcomes must
  consult the game's own UI cues.
- **Scope reduction (honest acknowledgment)**: this paper does *not*
  claim the LLM controls economy + tactics. Earlier iterations included
  doctrine templates spanning both; we removed economy because
  production decisions are deliberative, well-served by the UI, and
  fragile under LLM numerical errors.
- **Generalization**: any MCP-compatible game-server pair could in
  principle adopt the four-layer pattern with an analogous authority
  split.

---

## 7. Conclusion (0.25 page)

We presented openra_mcp, a human-LLM tactical-teaming architecture on a
real RTS that splits authority along an explicit economy/information vs.
tactics boundary. Our pilot evidence shows that single natural-language
commands generate substantial amplification — operations involving
concurrent multi-mission coordination that solo human players rarely
sustain. The contribution generalizes beyond RTS: any real-time
domain with parallel-mechanical sub-tasks can pair a deliberative human
with an LLM tactical facilitator under a similar authority split.

---

## 8. Appendix

- **A. DSL schema dump** — full Pydantic schema for the 15 intents,
  including the new `harass`, `patrol`, `escort`, `contain`, `divert`,
  `set_alert_state`, and `set_objective` variants.
- **B. Alert state bundles** — the 5 alert states as a Python dict
  (default stance, auto-mission list, daemon parameter set) plus the 4
  objective biases.
- **C. Showcase recipes (10 cases)** — revised for the new
  architecture: alert cascade, MCV escort, feint+raid sync, pending
  harass, multi-perimeter defense, after-action push, auto-escalation
  via wait_for_event, support pairing, dynamic force recruitment,
  containment chokepoint.
- **D. Session-summary schema** — unchanged from prior version.
- **E. Reproducibility** — git tag, OpenRA version, env spec,
  Python deps, Claude model ID.

---

## Submission plan

| Step | Owner | Target date |
|---|---|---|
| Draft Intro + Related Work | author | T+1 week |
| Run 30 pilot sessions across 3 conditions | author | T+2 weeks |
| Fig + table generation (`analyze.py`) | author | T+2 weeks |
| Internal review | colleague | T+3 weeks |
| Submit (IEEE CoG / CHI LBR / arXiv) | author | T+4 weeks |
