# Tutorial — Playing OpenRA via Claude Code

> 你是司令. 我 (Claude) 是参谋. OpenRA 是战场.
> **你**自己经营经济+看屏幕做判断, **我**翻译战略意图给引擎, **daemon** 在后台跑战术循环.

---

## 1. 第一次启动

```cmd
cd /d D:\openra_mcp
set "PATH=%LOCALAPPDATA%\dotnet;%PATH%"
set "DOTNET_ROOT=%LOCALAPPDATA%\dotnet"
start "OpenRA" /D OpenRA cmd /c "bin\OpenRA.exe Engine.EngineDir=.. Game.Mod=ra"
```

(用 `scripts\launch.bat` 一键也可, 但 launch.bat 有 BOM 问题, 用 cmd 手敲更稳.)

可选 — 启动战情官 (后台轮询):

```cmd
start "Scout Daemon" cmd /c "python -m mcp_server.scout_daemon"
```

它每 30 秒报告战况 + 异常 push 到 `scout_events.jsonl`.

OpenRA 主菜单 → **Skirmish** → 选地图 → **Play**.

慢速建议:
- 进游戏后按 `-` 键调速度至 0.5× (跟我对话有时间)
- 关键时刻按空格暂停, 跟我对话, 再继续

---

## 2. 分工 (重要 — 谁干啥)

| 角色 | 干啥 |
|---|---|
| **你 (玩家)** | 经济全包: 建建筑 / 训单位 / MCV 部署 / 卖 / 修 / 占领 / 科技. 看屏幕判断战场, 下战略意图. |
| **我 (LLM)** | 转译你的话 → 调战术工具. 战报叙述. 轻量兵种参谋建议 (不强求). |
| **Daemon (后台)** | 跑 mission 循环 (骚扰 / 巡逻 / 守家). 残血 < 30% 自动撤. 周界自卫. 医生贴步兵, 机械师贴车. |
| **Bot (engine 内)** | 只剩 harv 自动采矿. 不再产任何东西. |

**关键**: 我**不**告诉你"胜率多少 / 这仗能不能赢". 你看屏幕自己判断. 我不替你做数值分析.

---

## 3. 我接收什么命令

3 大类:

### 3.1 战备切换 (DEFCON 风格)

5 状态:

| 状态 | 含义 |
|---|---|
| `peace` | 发育期, 周界关, 不派任务队 |
| `watch` | 警戒, 周界开, 派少量巡逻 |
| `alert` | 战时戒备, 周界 aggressive, 加骚扰队 |
| `combat` | 主动进攻, 取消巡逻+骚扰, 全军 AttackAnything 待命 |
| `lockdown` | 死守, 全员回家, 周界 max |

```
你: 切 watch
你: 升 alert
你: 进 combat
你: 切 lockdown 死守
你: 回 peace 发育
```

### 3.2 战略目标声明

```
你: 这局我要赢就赢摧毁他总部
你: 撑 20 分钟我就赢
你: 我要切断他经济饿死他
你: 控制地图中央
```

5 目标: `destroy_fact` / `destroy_enemy` / `survive_until_tick` / `harass_economy` / `control_map_center`.

### 3.3 战术意图 (dispatch_intent)

```
你: north 直推敌总部
你: 派部队去骚扰敌矿
你: 巡逻东路
你: 护送 MCV 去 (75,30)
你: 卡死敌出口
你: 中路佯攻, dog 偷家
你: 守这分矿
你: 残血回家
你: 全员守势
```

### 3.4 战况询问 (read-only)

```
你: 看看场上
你: 现在啥情况
你: 敌方在哪
你: 残血的有哪些
你: 北线咋样
```

---

## 4. 我不接什么 (规则边界)

- ❌ **经济操作** ("建发电厂 / 出 5 个坦克 / 造矿场") — 你自己 OpenRA UI 干
- ❌ **数值分析** ("我打得过吗 / 胜率多少") — 你看屏幕判断
- ❌ **旧模板名** ("切 tank_rush / turtle / raid_harass / infantry_swarm / balanced") — 已删, 用 alert state 替代
- ❌ **bot 替产** ("让 AI 替我攒坦克") — bot 不再产任何东西
- ❌ 像素级操作 ("把 5 号兵走 (43, 56)") — atomic, 走 test_connect.py
- ❌ 自由发明的战术 — 我 map 到 frontal / flank / pincer / feint / patrol / escort / contain / diversion

---

## 5. 常见对话例

### 5.1 开局

```
你: 我新手, 简单介绍下
我: 你是司令, 我是参谋. 你管经济 (OpenRA UI 自己建造训兵), 
   我管战术翻译. 战备 5 档: peace/watch/alert/combat/lockdown.
   按 - 调慢速 0.5×, 跟我说 "看场上" 报情况.
```

```
你: 看看场上
我: [report battlefield]
   "Tick 1500. Cash 4500. Self 12 [2tnk×4, e1×6, e3×2], 
    Enemy 8 centroid (85,57). Alert: peace. Objective: 未设."
```

### 5.2 设战略目标 + 升档

```
你: 这把我要打他总部, 切战备
我: set_objective("destroy_fact"). 
   建议升 alert, 后面攒够推 combat.

你: 好, 升 alert
我: [set_alert_state("alert")]
   "alert 已切. 周界 aggressive, 派 2 e3 骚扰敌矿区. daemon 接管循环."
```

### 5.3 持续骚扰 + 动态收编 (新场景)

```
你: 用 e3 持续骚扰敌矿
我: [dispatch harass with e3×2]
   "2 e3 派去骚扰敌矿. 残血 60% 自动撤. **新训 e3 自动加入循环**."

[5 分钟后 你训了 6 个新 e3]
daemon: 自动收编进骚扰队, 现 8 e3 在骚扰循环

你: 撤回 2 守家
我: [retreat filter to 2 e3]
   "2 e3 撤回. 6 e3 仍在骚扰循环."
```

### 5.4 部队不够 — pending task (新场景)

```
你: 派部队骚扰 (但你没快速单位)
我: 当前无 harass-capable 单位 (jeep/e3/dog/ftrk/apc/1tnk).
   **任务已 queue**, 你训出第一个 daemon 自动接管.

[你训 1 jeep]
daemon: pending harass 启动, jeep 出发
我: 骚扰任务已启动. 现 1 jeep 在循环.
```

### 5.5 简单进攻

```
你: 北群直推敌总部
我: [dispatch attack frontal north → enemy_fact]
   "frontal attack 4 unit(s) → actor 84"
```

### 5.6 多兵种组合 (佯攻+偷家)

```
你: 中路佯攻, dog 偷家
我: [并发 2 个 dispatch:
    1. diversion center → enemy_base (假推, 接火即停)
    2. attack flank_left filter dog → enemy_fact (绕左偷)]
   "center 5 unit(s) feint to (70,57). 4 dog 绕左 → 总部."
```

### 5.7 护送 / 巡逻 / 卡口

```
你: 护送 MCV 去 (75,30)
我: [dispatch escort north 跟 MCV]
   "north 4 unit(s) 护送 MCV → (75,30)."

你: 巡逻东路
我: [dispatch patrol waypoints (60,30)→(60,50)→(60,70)]
   "2 jeep 巡逻东路, daemon 接管循环."

你: 卡死敌出口
我: [dispatch contain south at (50,60)]
   "south 3 unit(s) 蹲点 (50,60). 看到敌就打."
```

### 5.8 残血撤退 (手动, daemon 也会自动)

```
你: 残血回家
我: [retreat filter hp_below 0.3 → self_base]
   "retreat 2 unit(s) to (32,55). (daemon 也会自动撤 < 30% HP)"
```

### 5.9 防御分矿

```
你: 守这分矿
我: [dispatch defend + enable_auto_defense(center=分矿坐标)]
   "center 守 (45,72) 半径 12. 周界自卫已开."
```

### 5.10 切 combat 主推

```
你: 部队够了, 全员上, 切 combat
我: [set_alert_state("combat")]
   "combat 已切. 取消巡逻+骚扰, 全军 AttackAnything 待命."

你: north 主推, south 包右翼
我: [并发 2 个 dispatch:
    1. attack frontal north → enemy_fact
    2. attack flank_right south → enemy_fact]
   "north 4 直推, south 3 右翼绕击. 钳形成型."
```

### 5.11 战情官中断

```
[scout_daemon] ⚠ 敌方推近基地 (15 格内)
我: 前线急: 敌方近你家. 升 lockdown 死守, 还是 combat 反推?

你: lockdown
我: [set_alert_state("lockdown")]
   "lockdown. 全员回家, 周界 max. 看你顶不顶得住."
```

---

## 6. 困了/慢了 — 节奏控制

- 按 `-` 减速, `+` 加速, 空格暂停
- 跟我 chat 时引擎不停, 想停一下空格
- 复杂多步意图前我会先 pause(), 派完 resume()
- token 节约: 不要每 5 秒问"看场上", 让战情官 push 你
- daemon 后台跑战术循环, 你专心管经济+大方向

---

## 7. 速查 — 你说 → 我听

### 战备状态

| 你说 | alert state |
|---|---|
| "发育 / 攒钱 / 不动" | peace |
| "警戒 / 派点巡逻" | watch |
| "戒备 / 骚扰他" | alert |
| "全军上 / 推 / 主攻" | combat |
| "死守 / 全员回家" | lockdown |

### 战术动词

| 你说 | dispatch intent + approach |
|---|---|
| 推 / 直接打 / 硬上 | attack frontal |
| 包抄 / 绕侧 / 绕后 | attack flank_left / flank_right |
| 夹击 / 钳形 / 包饺子 | pincer |
| 假打 / 佯攻 / 牵制 | diversion / feint |
| 突击 / 冲 / 不要命 | attack charge |
| 谨慎 / 拉距离 | attack cautious |
| 守 / 防御 | defend |
| 撤 / 跑 / 救回来 | retreat |
| 集结 / 集合 | regroup |
| 侦察 / 探 | scout |
| 骚扰 / 偷矿 / 切经济 | harass |
| 巡逻 / 来回看 | patrol |
| 护送 / 跟着 X | escort |
| 卡口 / 蹲 / 堵 | contain |

### 单位选择

| 你说 | force kind |
|---|---|
| 残血 / 红血 | filter hp_below 0.3 |
| 全员 (机动) | force "all" (excludes harv/mcv/building) |
| 所有坦克 | filter unit_kind 2tnk |
| north / 北 / 北群 | group "north" |

---

## 8. 故障

| 症状 | 修 |
|---|---|
| 我说"看场上"返 not connected | OpenRA 没开 / 没进 skirmish |
| dispatch_intent 报 force_resolution_empty | 群名错, 或单位都死了 |
| 单位没动 | 战斗距离不到 / 引擎单帧未到 / 看下回合 |
| 战情官 daemon_running: false | `python -m mcp_server.scout_daemon` 没启 |
| 操作慢 | 按 `-` 调速度 0.5× 或暂停 |
| 你说"切 turtle" 我反问 | 旧模板已删, 是要 watch (警戒) 还是 lockdown (死守)? |
| 你说"建发电厂" 我拒绝 | 经济你自己 OpenRA UI 管, 我不下经济命令 |
| 你说"我打得过吗" 我拒绝 | 你看屏幕判断, 我不算胜率 |
| 骚扰队没动 | 可能 pending (无可用单位), 训一个出来 daemon 自动接 |

---

## 9. 全局心智模型

```
┌──────────────────────────────────────┐
│  你 (玩家)                            │
│   • OpenRA UI: 经济/科技/建造        │
│   • 看屏幕判断                        │
│   • 下战略意图 (NL)                  │
└──────────┬───────────────────────────┘
           │
           ▼ "切 alert / 北群推他总部"
┌──────────────────────────────────────┐
│  我 (Claude)                          │
│   • NL → dispatch_intent JSON        │
│   • 战报叙述                          │
│   • 轻量兵种建议                      │
└──────────┬───────────────────────────┘
           │
           ▼ tool call
┌──────────────────────────────────────┐
│  MCP Server (Python)                  │
│   • interpreter: DSL → atomic order  │
│   • daemon: mission 循环, retarget,   │
│     残血撤, 周界自卫, support pairing │
└──────────┬───────────────────────────┘
           │ TCP
           ▼
┌──────────────────────────────────────┐
│  OpenRA Engine (C# trait)             │
│   • McpBridge: 接 atomic order        │
│   • harv: 自动采矿 (唯一保留 bot 行为)│
└──────────────────────────────────────┘
```

---

**祝玩得开心. 司令同志.**
