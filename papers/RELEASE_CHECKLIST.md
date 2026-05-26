# Release Checklist

## Before Upload

- [x] Re-run `python -m mcp_server.tools.paper_metrics --out logs/paper_metrics.json`
- [x] Confirm manuscript title, author name, affiliation, and email.
- [x] Confirm all claims use early-preprint wording.
- [x] Confirm `v2_post_ablation.csv` is not described as a standalone 10/10 run.
- [x] Include `v2_retry_4fails.csv` and `v2_t7_retry2.csv` when claiming final 10/10.
- [x] Decide whether videos are uploaded in the same Zenodo record or separate supplement.
- [x] Create final PDF if desired.

Note: large videos are listed as supplementary evidence but intentionally kept
out of the core zip for the first timestamped release.

## Zenodo Fields

- Upload type: Publication
- Publication type: Preprint
- Title: Natural-Language Tactical Control for RTS Games via MCP-Based Mixed-Initiative Co-Piloting
- Creator: JiZiYi
- Affiliation: Universiti Teknologi Malaysia (UTM)
- Email: jiziyi@graduate.utm.my
- License: CC BY 4.0
- Keywords:
  - natural-language control
  - real-time strategy games
  - mixed-initiative interaction
  - game co-pilot
  - Model Context Protocol
  - OpenRA
  - tactical control
  - large language models

## After Upload

- [x] Save DOI. (v2: `10.5281/zenodo.20393182`; concept: `10.5281/zenodo.20377061`; v1: `10.5281/zenodo.20377062`)
- [x] Add DOI to `papers/ZENODO_METADATA.md`.
- [x] Add DOI to manuscript title page or data availability statement.
- [ ] Optionally prepare arXiv submission after DOI is secured. (Skipped per author preference 2026-05-26.)
