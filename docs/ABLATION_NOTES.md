# Phase Ablation Notes (2026-05-25)

Removes ~6700 lines of unused machinery to reflect what the v2 NL-capability
suite actually exercises. Live tactics still pass 10/10. The headline:
9 v2 tactical scenarios use spawn_squad / spawn_squad_batch directly; the
DSL + daemon path was carrying weight nobody stood on.

## Why

The v2 capability tests (T1–T10) and the earlier E7 paradigm
validation showed every tactic — referent resolution, kind/state split,
mid-flight recommand, partial cancel, conditional trigger, path constraint,
formation, time sequence, failure recovery — composed cleanly from two
engine primitives (Assault + Protection) plus a Python loop. The DSL
intents (defend / harass / patrol / escort / contain / diversion …) and the
daemon mission system never fired. They were a parallel execution path
nobody used.

Decision: physically remove the unused machinery. Source preserved in
`archive/ablation_recycle_bin/` and on the `pre-ablation-backup` branch
in case revival is ever needed.

## What changed

| File | Before | After | Notes |
|---|---|---|---|
| `mcp_server/tactical.py` | 3544 | archived | daemon + mission classes |
| `mcp_server/scout_daemon.py` | 221 | archived | push events |
| `mcp_server/defense_daemon.py` | 64 | archived | perimeter |
| `mcp_server/enemy_intent.py` | 172 | archived | intent classifier |
| `mcp_server/tactical_doctrine.py` | 373 | archived | unit strength table |
| `mcp_server/tactical_formation.py` | 161 | archived | formation helpers |
| `mcp_server/server.py` | 1273 | 625 | MCP tools 31 → 17 |
| `mcp_server/interpreter.py` | 1229 | 356 | squad-only |
| `mcp_server/intent_dsl.py` | 371 | 132 | attack/report/raw only |

Net ≈ −6700 LOC in Python, plus one new test-suite file
(`scenarios_v2.py`) and its runner.

## MCP surface

Active tools (17): get_state, list_units, find_unit, pause, resume,
screenshot, dispatch_intent, batch_dispatch_intent, end_session,
session_info, vocab, clarify, spawn_squad, spawn_squad_batch,
spawn_squad_cluster, list_squads, cancel_squad.

Removed tools (14): list_groups, assign_to_group, command_group,
rebalance_groups, latest_scout_report, wait_for_event, tactical_status,
enable_auto_defense, disable_auto_defense, list_defense_perimeters,
cancel_assaults, list_pending_missions, cancel_pending, set_alert_state,
get_alert_state, set_objective, set_doctrine, get_objective.

## DSL surface

Active intents (3): `attack` (→ Assault squad), `report` (read-only),
`raw` (escape hatch).

Removed intents (12): defend, retreat, regroup, scout, pincer, feint,
harass, patrol, escort, contain, diversion, set_stance. All were daemon-
backed. Equivalent tactics are now composed LLM-side — see
`docs/TWO_PRIMITIVES_PARADIGM.md` and `mcp_server/tools/compose_*.py`.

## C# side (Phase D)

OpenRA `SquadType` enum left untouched (Assault / Air / Rush / Protection
/ Naval) — the bot AI uses Air / Rush / Naval internally for the original
single-player AI. The McpBridge handler now rejects any squad_type other
than Assault or Protection on the LLM-facing path. No risk of accidentally
spawning a Rush or Naval squad over MCP.

## Verification

Static: server imports clean (17 tools), DSL parses attack + report,
interpreter dispatches Assault squad correctly via a fake transport.

Live: pending OpenRA restart. v2 should still record 10/10 against the
trimmed code — the spawn_squad / spawn_squad_batch path is unchanged.

## Rollback

```
git checkout pre-ablation-backup
```

…or cherry-pick individual files out of `archive/ablation_recycle_bin/`.
