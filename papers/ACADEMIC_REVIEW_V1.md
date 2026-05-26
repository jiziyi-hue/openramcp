# Academic Review V1

Reviewed artifact: `papers/openra_mcp_preprint.md`  
Review mode: Academic Research Suite / quick editorial + methodology review  
Review date: 2026-05-25

## Editorial Decision

**Decision: Acceptable for Zenodo early preprint after minor cleanup.**

**Decision if submitted as a workshop/demo paper today: Major revision.**

The manuscript has a clear and timely idea: natural language as a tactical
control layer for RTS games, with the LLM acting as a co-pilot rather than an
autonomous player. The project evidence is enough to claim a working prototype
and to reserve the research direction publicly. It is not yet strong enough for
a full empirical paper because the evaluation is still a mix of capability
demos, targeted retries, and development-session telemetry.

## Main Strengths

1. **Clear distinction from autonomous game agents.** The paper repeatedly
   frames the system as human-in-the-loop co-piloting rather than AI playing the
   game. This is the right intellectual wedge.

2. **Strong system idea.** The two-primitives plus LLM-side composition design
   is clean and memorable. It gives the paper a real architectural claim rather
   than only a demo claim.

3. **Concrete tactical evidence.** The v2 suite, live LLM demo, E7
   demonstrations, and ablation give enough material for an early preprint.

4. **Honest limitations.** The draft already avoids overclaiming full RTS play
   and marks telemetry as prototype evidence rather than a controlled benchmark.

5. **Good future-work bridge to small models.** The distillation idea is
   plausible because the output target is structured MCP JSON, not open-ended
   strategic prose.

## Major Issues Before Conference Submission

### 1. Evaluation Is Not Yet A Clean Controlled Study

The manuscript reports a final 10/10 v2 suite after targeted retries and
threshold corrections. This is acceptable for an early preprint if stated
clearly, and the current draft does state it clearly. However, a reviewer for a
conference or journal would ask for a clean rerun with fixed criteria.

**Fix for next version:** run a clean evaluation folder with frozen scripts,
fixed thresholds, and one final CSV. Ideally run each scenario multiple times
and report mean/std or at least pass count across repeats.

### 2. Methods Need More Reproducible Detail

Sections 3-5 describe the architecture, but they do not yet give enough detail
for another researcher to reproduce the system. The paper should specify the
exact MCP tools, the squad payload schema, what the LLM sees, what is hidden,
and how the composition layer chooses units and targets.

**Fix for next version:** add a "Implementation Details" subsection with:

- MCP tools used in the paper path: `get_state`, `spawn_squad`,
  `spawn_squad_batch`, `cancel_squad`, etc.
- Example JSON for one tactical command.
- What fields come from game state.
- What is computed by the LLM/Python layer.
- What is delegated to OpenRA.

### 3. The Title Is Slightly Broader Than The Evidence

The title says "RTS Games," but the evidence is one OpenRA prototype. That is
fine for a preprint if framed carefully, but reviewers may call it too broad.

**Possible safer title:**

Natural-Language Tactical Control in OpenRA via MCP-Based Mixed-Initiative
Co-Piloting

**Possible stronger title, if kept broad:**

Natural-Language Tactical Control for RTS Games: An OpenRA MCP Co-Pilot
Prototype

### 4. Small Local Model Track Should Stay Future Work

The abstract and discussion mention a small local low-latency model. This is a
good direction, but it should not sound like a completed result. Current wording
is mostly safe. Do not move it into the contribution list as an implemented
contribution until training/evaluation exists.

### 5. Data Availability Needs Public-Release Language

The current data statement says artifacts are maintained in the local project
directory. For Zenodo this is acceptable as a draft, but a public paper should
say which artifacts are included in the release and which will be made
available later.

**Fix:** after Zenodo upload, replace local-path wording with DOI-linked
artifact wording.

## Minor Issues

1. Add a short **Conclusion** section. The paper currently ends with Future
   Work and then metadata statements. A conclusion would make it feel more
   complete.

2. Table 1 is readable in the PDF but cramped. For a later conference version,
   split it into two tables or shorten the command column.

3. The related-work section is good enough for preprint but should eventually
   include a more explicit comparison table: autonomous agent vs human-swarm
   coordination vs this work.

4. Writing quality is mostly clean. The automated style scan found no
   high-frequency AI-cliche terms from the ARS checklist. It found four em
   dashes and nineteen semicolons, which is acceptable for a draft but slightly
   higher than the strict ARS style target.

## Preprint Readiness Score

| Dimension | Score | Comment |
|---|---:|---|
| Originality | 4/5 | The co-pilot framing plus MCP/OpenRA execution is a strong niche. |
| Evidence sufficiency | 3/5 | Enough for preprint, not enough for full empirical paper. |
| Method clarity | 3/5 | Architecture is clear; reproducibility details need expansion. |
| Related work fit | 3.5/5 | Good first pass; comparison table would strengthen it. |
| Claim discipline | 4/5 | Mostly careful and appropriately provisional. |
| Release readiness | 4/5 | Zenodo package exists; DOI and public artifact language remain. |

## Recommended Next Actions

### For Zenodo Now

1. Add a short Conclusion section.
2. Keep the current limitations language.
3. Upload the core package.
4. Add DOI back into the manuscript metadata.

### For A Stronger Version Later

1. Run a clean repeated evaluation.
2. Add manual-control comparison results.
3. Add one JSON example of NL-to-MCP translation.
4. Add a comparison table against AlphaStar/OpenRA-RL/TextStarCraft
   II/SwarmBrain/HIVE/HIMA.
5. Start the small-model dataset extraction track.

## Bottom Line

This is ready as an **idea-claiming early preprint**. It is not yet a finished
conference paper, but it is substantially better than a loose project note: it
has a clear research question, a coherent architecture, concrete logs/videos,
and a defensible limitation strategy.

