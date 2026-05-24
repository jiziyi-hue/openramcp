# 下个 Claude session 第一件事

> 玩家已厌反复重开 session + OpenRA. 接手前读这个.

---

## ⚠ 当前状态 (2026-05-24)

### 修复完成 (待实测验)
- **P0 #1** — 新单位罚站 bug. `tactical.py:_run_assault` 改 per-actor
  `dispatched_actors: Set[int]` (Assault + ContainmentMission 都改).
  新 recruited 单位每个收一次 set_stance + attack_move.
- **P0 #2** — pincer 同 pool 抢单 bug. `interpreter.py:_do_pincer` 检测
  total overlap → split 一半给 left 一半给 right; partial overlap →
  left 保留, right 去重叠.
- **P1 #8 (fog-of-war)** — `OpenRA/.../McpBridge.cs` HandleGetState 加
  `respect_fog` 参数 (默认 true), 用 `self.Shroud.IsVisible(pos)` 过
  enemy_units. **dll 已 rebuild** (`bin/OpenRA.Mods.Common.dll` 含
  respect_fog UTF-16 字串).

### 待 commit
所有改动 uncommitted (见 `git status`). 实测验过再 commit.

### 待跑
- OpenRA **已退**, 没在跑. 需玩家重启 (启动法见 [LAUNCH_OPENRA.md](LAUNCH_OPENRA.md)).
- Scout daemon 没起.

---

## 验环境 (玩家重启 OpenRA + Claude 后, 第一步)

```
get_state()
```

返 `ok:true` + units → 通.
返 `bridge not connected` → OpenRA 没起或退了, 看 [LAUNCH_OPENRA.md](LAUNCH_OPENRA.md).

---

## 测序 (依次, 任一失败停下报)

### 1. 验 P0 #1 (新单位罚站 fix)

```
set_doctrine(alert_state="combat", objective="destroy_enemy")
```

玩家随手训几个新 combat-mobile (2tnk/3tnk/e3) → 看新单位**是否
自动加入推进** (不站家). 之前 bug: 30+ 单位罚站基地不动.

验通过标志: 新训单位 ~5 秒内出基地往前线走.

### 2. 验 P0 #2 (pincer 抢单 fix)

```
dispatch_intent({
  "intent":"pincer",
  "left":{"kind":"filter","combat_mobile":true},
  "right":{"kind":"filter","combat_mobile":true},
  "target":{"kind":"named","name":"enemy_fact"}
})
```

验通过标志: 部队**不**反复 left/right 重指 ("乱晃"); cohesion_halts
增长合理 (而非暴涨); 实际形成钳形.

### 3. 验 fog-of-war (P1 #8)

```
get_state()
```

返 `enemy_units` 应**只含**当前视野内敌方 (己方单位/雷达覆盖区).
之前: 返完整地图所有敌方 (上帝视角).

若想绕过 fog 看 ground truth (调试用):
```
get_state(respect_fog=False)
```

---

## 实测通过 → commit

3 个测序都通 → commit. 建议分两:
- `fix: P0 罚站 + pincer 抢单 (tactical + interpreter)`
- `feat: fog-of-war in get_state (McpBridge.cs)`

---

## 玩家偏好 (省提问)

- 中文回, fragments OK, 短
- 不要技术分析铺垫, 直接给 next step
- 不要让他重开 OpenRA 没必要时 (重启除外)
- 不要让他再开 Claude session 除非他主动同意
- 启动命令: 见 [LAUNCH_OPENRA.md](LAUNCH_OPENRA.md) — bash 直跑
  `cd /d/openra_mcp/OpenRA && ./bin/OpenRA.exe Engine.EngineDir=.. Game.Mod=ra`

---

## 已知坑

- Git Bash 把 `\openra_mcp\OpenRA` 当 escape, `start /D OpenRA` 死. 用
  forward slash + 直跑, 不用 start.
- MCP server cwd 绑 session 启动时, 重开 Claude 才换. 玩家烦此点.
- C# trait 改动**必须**重 build OpenRA dll 才生效. 这次 fog 改动已 build
  (10:24, `bin/OpenRA.Mods.Common.dll`).

---

## 完整 audit 留档

`docs/AUDIT_2026_05_24.md` 全栈扫描. P0 / P1 / P2 排单都在.
P0 上面修了, P1/P2 待玩家决定优先级.
