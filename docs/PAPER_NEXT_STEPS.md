# Paper Next Steps

> Purpose: turn the current OpenRA MCP prototype into a first public preprint.
> Updated: 2026-05-25.

## Short Answer

You do not need to build a large new feature before writing.

The project is already strong enough for an early preprint / Zenodo release if
we frame the claims carefully:

- Claim: natural language can control complex RTS tactics through auditable MCP
  tool calls.
- Show: complex unit-control scenarios in OpenRA, including split forces,
  pincer movement, time sequencing, route constraints, and failure recovery.
- Limit: do not claim solved autonomous RTS play.
- Future work: distill the NL-to-MCP behavior into a small local low-latency
  model.

## What I Can Do For You

I can handle most of the paper work directly:

1. Consolidate the evidence into clean tables.
2. Rewrite the stale outline around the post-ablation architecture.
3. Draft the preprint in English.
4. Build a references section from the existing PDFs and any extra sources we
   decide to verify.
5. Prepare a Zenodo-ready bundle with manuscript, supplement list, videos, and
   metadata.
6. Start the small-model track by extracting supervised pairs from transcripts:
   `(natural language + compact state) -> MCP call JSON`.

## What You Need To Decide

Only three decisions are really needed from you:

1. **Author metadata**: author name, affiliation, email, ORCID if any.
2. **Release posture**: Zenodo only first, or Zenodo plus arXiv later.
3. **How aggressive the claim should be**:
   - Conservative: "prototype evidence for NL tactical control".
   - Stronger: "a practical tactical control layer for RTS games".

My recommendation: start conservative on Zenodo, then revise upward after one
clean evaluation run.

## Minimum Work Before Zenodo

These are must-do before publishing:

1. **Create the manuscript**
   - Title, abstract, introduction, architecture, implementation, evaluation,
     discussion, limitations, future work.

2. **Make the evidence table honest**
   - Do not say `v2_post_ablation.csv` alone is 10/10.
   - Say the v2 suite reached 10/10 after scenario-threshold correction and
     targeted retries, with all raw CSV files preserved.

3. **Separate clean evidence from development logs**
   - Existing logs are useful, but they mix development and evaluation.
   - For a clean paper table, either rerun a clean session or label current
     metrics as "development-session telemetry".

4. **Write limitations plainly**
   - Sandbox scenarios are not full competitive matches.
   - Some tests use scripted unit spawns / prepared unit rosters.
   - The local small model is planned, not completed.
   - The LLM information-discipline boundary is currently prompt/protocol
     enforced rather than cryptographically enforced.

## Best Next Action

The next concrete step is preparing the release bundle from the current
manuscript, handoff, and evidence. Keep a running release list for:

- clean evaluation rerun,
- exact citation verification,
- Zenodo metadata,
- optional local-model dataset extraction.
