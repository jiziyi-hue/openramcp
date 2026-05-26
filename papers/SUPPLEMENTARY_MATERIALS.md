# Supplementary Materials

This file lists the artifacts that should accompany the first Zenodo preprint.

## Manuscript

- `papers/openra_mcp_preprint.md`
- `papers/references.bib`

## Metadata

- `papers/ZENODO_METADATA.md`

## Evaluation Data

- `logs/v2_post_ablation.csv`
- `logs/v2_retry_4fails.csv`
- `logs/v2_t7_retry2.csv`
- `logs/baseline_pre_ablation.md`
- `logs/paper_metrics.json`

## Videos

- `logs/live_llm_demo/demo_01.mp4`
- `logs/v2_videos/*.mp4`

## Key Source Files

- `OpenRA/OpenRA.Mods.Common/Traits/World/McpBridge.cs`
- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/Squads/States/GroundStates.cs`
- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/Squads/States/ProtectionStates.cs`
- `mcp_server/server.py`
- `mcp_server/interpreter.py`
- `mcp_server/intent_dsl.py`
- `mcp_server/experiments/scenarios_v2.py`
- `mcp_server/tools/compose_patrol.py`
- `mcp_server/tools/cycle4_demo.py`
- `mcp_server/tools/small_big_demo.py`
- `mcp_server/tools/pincer_demo.py`
- `mcp_server/tools/paper_metrics.py`

## Notes For Release

The video files are large. If Zenodo upload becomes unreliable, upload the
manuscript, source snapshot, CSV files, and metadata first, then add the videos
as a second version or link them from a separate record.

