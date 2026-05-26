# Zenodo Metadata Draft

## DOI

- **Concept DOI** (always resolves to the latest version): `10.5281/zenodo.20377061`
- **Version DOIs:**
  - v1 (2026-05-25): `10.5281/zenodo.20377062`
  - **v2 (2026-05-26)**: `10.5281/zenodo.20393182` — current
- **Record URL**: https://zenodo.org/records/20393182

## Title

Natural-Language Tactical Control for RTS Games via MCP-Based Mixed-Initiative Co-Piloting

## Creators

- JiZiYi
  - Affiliation: Universiti Teknologi Malaysia (UTM)
  - Email: jiziyi@graduate.utm.my

## Resource Type

Publication / Preprint

## License

CC BY 4.0

## Keywords

- natural-language control
- real-time strategy games
- mixed-initiative interaction
- game co-pilot
- Model Context Protocol
- OpenRA
- tactical control
- large language models

## Description

This preprint presents `openra_mcp`, a mixed-initiative RTS game co-pilot that
translates a human player's natural-language tactical intent into executable
MCP tool calls controlling units in OpenRA. The system is framed as a co-pilot
rather than an autonomous game-playing agent: the player retains strategic
judgment, economic control, and battlefield interpretation, while the LLM
serves as a tactical translator over auditable squad-level execution commands.

The release is scoped as an early preprint for the game / RTS branch of
MCP-mediated control. It does not claim to be the first natural-language or MCP
system for multi-entity/swarm control; recent UAV and robot-swarm work is
treated as adjacent related work. The claim is narrower: to our knowledge,
`openra_mcp` is the first open-source plain-natural-language mixed-initiative
tactical co-pilot for a production RTS engine.

## Related Identifiers

- Code / data / videos: included as supplementary files in the same Zenodo
  record where upload size permits.
- Closest adjacent MCP swarm precedent: https://arxiv.org/abs/2605.03788
- Drone command-and-control MCP precedent: https://arxiv.org/abs/2601.15486
