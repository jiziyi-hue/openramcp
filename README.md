# openra_mcp — Natural-Language Tactical Co-Pilot for OpenRA

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20377061.svg)](https://doi.org/10.5281/zenodo.20377061)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Paper](https://img.shields.io/badge/paper-Zenodo-blue)](https://zenodo.org/records/20393182)

> A player speaks the maneuver in plain language; an LLM translates it into
> structured MCP calls; the OpenRA engine runs per-tick movement, pathfinding,
> and combat. Two engine-side execution primitives (`Assault`, `Protection`)
> plus LLM-side composition express ten natural-language tactical capabilities
> — pincer, multi-squad split, feint-and-raid, route constraints,
> time-sequenced attacks — on a production RTS engine with a real AI opponent.

**Paper:** [`papers/openra_mcp_preprint.pdf`](papers/openra_mcp_preprint.pdf)
&nbsp;·&nbsp;
**Zenodo record:** <https://zenodo.org/records/20393182>
&nbsp;·&nbsp;
**Concept DOI (always-latest):** [`10.5281/zenodo.20377061`](https://doi.org/10.5281/zenodo.20377061)

## What this is

A research prototype for *LLM-as-co-pilot* RTS control. The player keeps
strategic judgment, economy, and battlefield interpretation; the LLM
translates plain natural-language tactical intent into auditable squad-level
MCP tool calls. The OpenRA engine owns the per-tick execution loop.

**Paradigm distinction.** Unlike *LLM-as-player* systems (AlphaStar,
TextStarCraft II, SwarmBrain, HIMA, OpenRA-RL) that substitute the human, this
project keeps the human in the loop. The closest prior art is an earlier
open-source OpenRA co-pilot, `hoppinai-mcp-red95` (HOPPINZQ, 2025), which
adopts a per-unit atomic API; we instead use an engine-FSM-minimal MCP
surface (17 tools + 2 squad primitives) so that the LLM does not sit in the
per-tick control loop.

## Citation

```bibtex
@misc{openra_mcp_2026,
  title        = {Natural-Language Tactical Control for RTS Games via MCP-Based Mixed-Initiative Co-Piloting},
  author       = {Ji, Ziyi},
  year         = {2026},
  publisher    = {Zenodo},
  version      = {v2},
  doi          = {10.5281/zenodo.20393182},
  url          = {https://zenodo.org/records/20393182}
}
```

## Architecture

```
Player natural-language intent
        ↓
LLM tactical translator (Claude / DeepSeek / GPT)
        ↓ MCP tool calls
mcp_server/ (Python)
        ↓ TCP 7777
trait_src/McpBridge.cs inside OpenRA
        ↓
Engine: pathfinding · collision · combat · per-tick autonomy
```

The MCP surface exposes 17 tools (`get_state`, `spawn_squad`,
`spawn_squad_batch`, `cancel_squad`, etc.). Higher-level tactics — pincer,
feint-and-raid, time-sequencing, partial cancel — are composed by the LLM on
top of the two engine primitives rather than encoded as specialized engine
FSMs. See [`docs/DESIGN.md`](docs/DESIGN.md) and
[`docs/INTENT_DSL.md`](docs/INTENT_DSL.md) for details.

## Quick start

**Requirements.** Windows 10/11 · .NET 8 SDK · Python 3.10+ · Git · an MCP
client (Claude Code, Cursor, or any tool that speaks MCP stdio).

```bash
# 1. clone this repo
git clone https://github.com/<YOUR-USER>/openra_mcp.git
cd openra_mcp

# 2. clone the OpenRA engine (kept separate due to GPLv3 + size)
git clone --depth=1 --branch release-20250330 https://github.com/OpenRA/OpenRA.git

# 3. copy our C# traits into the OpenRA source tree
cp trait_src/McpBridge.cs                   OpenRA/OpenRA.Mods.Common/Traits/World/
cp trait_src/GrantConditionOnHumanOwner.cs  OpenRA/OpenRA.Mods.Common/Traits/Player/

# 4. build OpenRA (one-time, ~3 min)
cd OpenRA && make all && cd ..

# 5. configure MCP client
cp .mcp.json.example .mcp.json
#   edit .mcp.json — set "cwd" to the absolute path of this folder

# 6. install Python deps
pip install -r mcp_server/requirements.txt

# 7. launch
#   start OpenRA from OpenRA/bin/OpenRA.exe (skirmish, pick a map)
#   then in your MCP client: "send the tanks down the middle and have the APCs
#   flank from both sides"
```

Full walkthrough in [`docs/TUTORIAL.md`](docs/TUTORIAL.md).

## Repository layout

```
openra_mcp/
├── mcp_server/             # Python MCP server (stdio)
│   ├── server.py           # FastMCP — exposes 17 tools
│   ├── interpreter.py      # intent_json → atomic engine orders
│   ├── intent_dsl.py       # pydantic schema (DSL field authority)
│   ├── experiments/        # v2 capability suite + DeepSeek runners
│   └── tools/              # squad_trace, paper_metrics, demos
├── trait_src/              # our C# additions to OpenRA
│   ├── McpBridge.cs        # in-engine TCP server + squad FSMs
│   └── GrantConditionOnHumanOwner.cs
├── docs/                   # design, DSL reference, tutorial, system prompt
├── papers/                 # preprint + Zenodo release metadata + bib
├── scripts/                # build / launch helpers
└── CLAUDE.md               # in-repo system prompt for Claude Code users
```

## Evaluation summary

- **NL capability suite (v2):** 10/10 scenarios pass (referent resolution,
  kind/state split, mid-flight re-command, partial cancel, conditional
  trigger, route constraint, formation, time sequencing, failure recovery).
- **Live LLM demo:** 8 consecutive free-form commands executed end-to-end on
  a 100-unit roster.
- **Repeated DeepSeek-V4-Pro trials:** N=5 per scenario across 30-unit and
  100-unit rosters; per-run logs in
  [`logs/rl_compare/`](logs/rl_compare/) (gitignored locally, mirrored on
  Zenodo).
- **Cross-paradigm cost reference:** OpenRA-RL atomic per-unit API,
  N=3 full games, ~755k tokens per game (architectural cost reference, *not*
  a head-to-head gameplay benchmark).

Full per-run data is in the Zenodo release zip
[`papers/000_UPLOAD_TO_ZENODO_openra_mcp.zip`](https://zenodo.org/records/20393182).

## Status

Research prototype. The paper preprint (v2) is on Zenodo with DOI. The code
here is the snapshot used to generate the paper's results. Issues and PRs are
welcome but not actively maintained — fork freely under the MIT license.

## License

- This repository's original code (Python, C# traits, scripts, docs): MIT
  (see [LICENSE](LICENSE)).
- OpenRA engine: GPLv3 — cloned separately per the Quick Start.
- Red Alert game assets: Westwood / EA commercial copyright — OpenRA requires
  you to provide the original game files yourself; they are not distributed
  with this project.
- Reference paper PDFs (HIMA, HIVE, SwarmBrain, TextStarCraft II, Voyager)
  are intentionally not bundled; fetch them from arXiv using the IDs in
  [`papers/references.bib`](papers/references.bib).
