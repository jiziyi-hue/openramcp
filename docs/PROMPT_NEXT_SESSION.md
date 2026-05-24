# 下个 Claude session 第一段输入 (copy-paste 给新 LLM)

---

我是 openra_mcp 玩家. 你是战术参谋. 上局 (2026-05-24) 完成 Phase A +
B1-B5 + **Phase D1-D5 代码修复**. 这局任务是 **Phase D6: smoke-test
新 spawn_squad 4 改动**, 然后 commit + push.

## 立即做

1. 调 `get_state()` 验 OpenRA bridge 通. OpenRA 已重启, 我已进 skirmish.
2. 验 `spawn_squad` 新 schema: `unit_ids` 现在是 **optional**, 加了
   `target_pos: {x,y}`. 调 `spawn_squad(squad_type="Assault")` 不传 ids,
   返应该带 `auto_selected: true` + unit_count>0.
   - 若 schema 老的, 你这边 MCP server 没重启, 告我 `/mcp restart`.

## Phase D 4 改动 (你要测的)

- **D1 auto-select**: `spawn_squad(squad_type)` 不传 unit_ids → C#
  `SquadManager.PickIdleCombatUnits(type)` 挑闲兵组队. 不再要 LLM 挑 ids.
- **D2 target_pos**: `spawn_squad(squad_type, target_pos={"x":N,"y":M})`
  没敌方 actor 时给个坐标作集结/推进点. Squad 用 `Target.FromCell`.
- **D3 rally gate**: `GroundUnitsIdleState` 先查 leader 半径
  `max(4, count/3)` cells 内单位数, 少于全员 → leader stop + stragglers
  AttackMove 到 leader. 步兵不会再冲先.
- **D4 stance**: HandleSpawnSquad 后 issue `SetUnitStance AttackAnything`
  给所有 squad units → 路上自动打周边建筑/单位.

## 测序

1. 拉 `get_state()` 看自己有什么单位 (闲兵).
2. `spawn_squad(squad_type="Assault")` — **不传** unit_ids, **不传** target.
   验返 `auto_selected: true`, unit_count>0, squad_index=0.
3. `list_squads()` 看 squad 内容.
4. 等 5-10s, `get_state()` 看 squad 单位是否**先 rally 到 leader** (D3
   验证), 而不是立刻散开冲. 玩家也会看屏幕告你.
5. `cancel_squad()` 清, 再测 D2:
   `spawn_squad(squad_type="Assault", target_pos={"x":42,"y":46})` →
   验 squad 朝地图中央 (42,46) 推进.
6. 推进中观察 (D4 验证): squad 路过敌建筑应该**自动停下打**, 不只追
   target. 玩家看屏幕告你.
7. 最后玩家说 GG → `end_session(result=..., end_tick=...)`.

## 上局已做 (背景)

- **Phase A**: P0 fix + yaml `SquadManagerBotModule@human EnableAutoSpawn=false`.
- **Phase B1-B3**: McpBridge `spawn_squad/list_squads/cancel_squad` 3
  handler, python MCP wrapper.
- **Phase B5 game-test**: 端到端 work — spawn_squad 推 50 单位到敌总部.
  但发现 4 缺陷 (LLM 挑 ids / 无 rally / 无 target_pos / 不打周边建筑).
- **Phase D 代码修**: 4 缺陷全修, dotnet build Release 通过 0 错.
- Commits 状态: Phase D 改动**未 commit**, 你测过 OK 后才 commit.

## 修改文件 (待 commit)

- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/Squads/Squad.cs` —
  加 `SetCellToTarget(CPos)`
- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/SquadManagerBotModule.cs` —
  加 `PickIdleCombatUnits(SquadType)`
- `OpenRA/OpenRA.Mods.Common/Traits/BotModules/Squads/States/GroundStates.cs` —
  `using System;` + `GroundUnitsIdleState` rally gate + 无敌目标也推进
- `OpenRA/OpenRA.Mods.Common/Traits/World/McpBridge.cs` —
  `HandleSpawnSquad` 重写, 加 unit_ids 可选 + target_pos + AttackAnything stance
- `mcp_server/server.py` — `spawn_squad` Python wrapper 加 target_pos +
  unit_ids 可选

## Phase D7 (测 OK 后)

1. `cd /d/openra_mcp/OpenRA && git status` 验 4 C# 文件改了
2. 主仓 git status 验 server.py 改了 + handoff 文档
3. OpenRA submodule commit:
   ```
   cd /d/openra_mcp/OpenRA && git add -A && git commit -m "phase D: spawn_squad fixes — auto-select, target_pos, rally gate, AttackAnything stance"
   ```
4. 主仓 commit + push.

## 玩家偏好 (省提问)

- 中文回, fragments OK, 短 (caveman mode)
- 不要技术分析铺垫
- 每次答前必先 `get_state()` (fresh snapshot, 不复用旧)
- 我下指令前你**只**告我做什么, **不**自己决策派兵
- 兵种缺克制看到了**简短**提一句, 不强求
- spawn_squad 现在**优先用 auto-select** (不传 unit_ids)

## 紧急 / 已知问题

- `spawn_squad` 老签名失败 = MCP server 没重启
- C# build 失败 = check `dotnet build OpenRA.sln -c Release` 在
  `/d/openra_mcp/OpenRA` 里
- contain mission 全员回家 bug 仍存 — 用 spawn_squad(Protection) 替代

## 完了之后 (Phase E)

加新 squad type (Harass/Patrol/Escort/Explore) + 退役 Python daemon mission
系统 (~2000 行删, 用 bot squad 替代). 计划在
`docs/AUDIT_2026_05_24.md` + memory `feedback_squad_auto_select` /
`project_squad_no_rally_phase`.
