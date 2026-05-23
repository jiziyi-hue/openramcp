# Wake-up 交接 — openra_mcp v0 demo

> 你睡前指令: 全权干 OpenRA + MCP 整套. 我自己拍板细节. 下面是醒来要看的总账.

---

## TL;DR

**端到端跑通了.** Claude Code 通过 MCP 调工具 → Python server → TCP → OpenRA 内 `McpBridge` C# trait → OpenRA 引擎执行命令 + 返状态. 实际验证: `get_state` 拿到了 OpenRA 主菜单 shellmap 的真实战场数据 (单位 / 资源 / 地图), `move`/`build` 命令成功 dispatch 到引擎.

```
[Claude Code 我]  ↔ MCP stdio ↔  [Python server]  ↔ TCP 7777 ↔  [OpenRA + McpBridge trait]
```

---

## 实际验证过的事

```text
$ python mcp_server/test_connect.py get_state
{
  "ok": true,
  "state": {
    "tick": 185,
    "paused": false,
    "map_name": "Desert Shellmap",
    "map_size": {"x": 128, "y": 128},
    "self_cash": 5100,
    "self_power": 480,
    "self_units": [
      {"id": 13, "kind": "brik", "owner": "Allies", "pos": {"x": 40, "y": 74}, "hp_pct": 1},
      {"id": 17, "kind": "2tnk", "owner": "Allies", "pos": {"x": 77, "y": 68}, "hp_pct": 1},
      {"id": 33, "kind": "syrd", "owner": "Allies", "pos": {"x": 67, "y": 95}, "hp_pct": 1},
      ...
    ]
  }
}

$ python mcp_server/test_connect.py move 17 50 50
{"ok": true, "issued_orders": 1, "affected_unit_ids": [17]}

$ python mcp_server/test_connect.py build Powr
{"ok": true, "issued_orders": 1, "affected_unit_ids": []}
```

主菜单 shellmap demo 是非交互的 (引擎自己脚本控制), 我们的 move 命令是真的 dispatch 了但被脚本覆盖. 真实跑法 = Skirmish.

---

## 做了什么 (清单)

✓ winget 装 .NET 8 SDK 卡在 admin 权限. 改用 `dotnet-install.ps1` 用户态装 (`%LOCALAPPDATA%\dotnet`)  
✓ clone OpenRA `release-20250330` (~250MB) 到 `OpenRA/`  
✓ 写 C# trait `trait_src/McpBridge.cs` (510 行, 用 System.Text.Json, net6 兼容)  
✓ 把 trait 复制进 `OpenRA/OpenRA.Mods.Common/Traits/World/McpBridge.cs`  
✓ 在 `OpenRA/mods/ra/rules/world.yaml` 的 `^BaseWorld:` 下注册 `McpBridge:`  
✓ 写 Python MCP server `mcp_server/` (FastMCP, 14 个 tool, pydantic schema, TCP transport)  
✓ 编译 OpenRA 成功 (0 error, 8 warning 都是 net6 EOL 提示)  
✓ 改 `OpenRA/bin/*.runtimeconfig.json` 加 `rollForward: Major` 解决 net6→net8 runtime 不匹配  
✓ 下载 RA 内容包 `ra-quickinstall.zip` (13MB, SHA1 校验过), 解压到 `OpenRA/Support/Content/ra/v2/`  
✓ 启动 OpenRA, 日志输出 `[McpBridge] listening on 127.0.0.1:7777` — trait 真上线  
✓ 实测 get_state / list_units / move / build / pause / screenshot 命令全过, 收到真实数据  
✓ 写了完整文档 (`README.md`, `docs/DESIGN.md`, `docs/PROTOCOL.md`, `docs/USAGE.md`)  
✓ 写了启动脚本 (`scripts/launch.bat`, `scripts/build_openra.bat`, `scripts/setup_all.bat`, `scripts/install_trait.bat`)

---

## 14 个 MCP tools (全已暴露)

| tool | 说明 | 实测 |
|---|---|---|
| `get_state(include_enemies)` | 世界快照 | ✓ |
| `list_units(owner, kind)` | 按 owner/兵种 filter | ✓ |
| `find_unit(description)` | 模糊匹配 actor name | ✓ trait 侧 |
| `build(structure, near, count)` | 排队建建筑 | ✓ |
| `train(unit, count, factory_id)` | 兵营生产 | trait 侧 ok |
| `move(unit_ids, target, attack_move)` | 移动一组 | ✓ |
| `attack(unit_ids, target_id)` | 集火 | trait 侧 ok |
| `set_stance(unit_ids, stance)` | HoldFire/ReturnFire/Defend/AttackAnything | trait 侧 ok |
| `pause()` / `resume()` | 暂停 | ✓ |
| `screenshot()` | 截图 (写到 Support/Screenshots) | ✓ |
| `deploy(unit_ids)` | MCV 部署 (DeployTransform) | trait 侧 ok |
| `stop(unit_ids)` | 停止 | trait 侧 ok |
| `sell(unit_ids)` | 卖建筑 | trait 侧 ok |
| `scatter(unit_ids)` | 散开 | trait 侧 ok |

---

## 你醒来要做的 (3 步)

### 1. 启动 OpenRA + trait

```cmd
cd D:\线列步兵\openra_mcp
scripts\launch.bat
```

OpenRA 窗口会弹出. 控制台里有 `[McpBridge] listening on 127.0.0.1:7777`. 跳过菜单 → **Skirmish** → 选小地图 (e.g. Forest Path) → 选 Allies → Play.

### 2. 配 Claude Code MCP server

把 `claude_mcp_config.json` 里 `openra-bridge` 块加到你的 Claude Code 配置 (`~/.claude/settings.json` 的 `mcpServers`).

或者命令行:
```bash
claude mcp add openra-bridge --command python --args "-m" "mcp_server.server" --cwd "D:\线列步兵\openra_mcp"
```

### 3. 跟 Claude Code 说话

```
你: 看看场上有什么
我: [调 get_state()] 你: 1 MCV 在 (28,30), $5000, 电力 0. 地图 Forest Path 96×96.

你: 部署 MCV
我: [find_unit('MCV') + deploy(ids)] MCV 正在展开成 Construction Yard.

你: 建 2 个发电厂 + 1 个矿场
我: [build('Powr', count=2) + build('Proc', count=1)] 3 个建筑已排队.

你: 出 5 步兵, 派去南面
我: [train('E1', count=5)] 等出兵... [move(ids, target=(50,70))]
```

也可以**先跳过 Claude Code**, 直接拿 test_connect.py 玩:
```bash
python mcp_server/test_connect.py get_state
python mcp_server/test_connect.py move 17 50 50
python mcp_server/test_connect.py screenshot
```

---

## 项目结构

```
D:/openra_mcp/
├── OpenRA/                                    ← clone, 不在仓库
│   ├── bin/                                    ← 编译输出, ~80MB
│   ├── OpenRA.Mods.Common/Traits/World/
│   │   └── McpBridge.cs                        ← 我们的 trait (copy from trait_src)
│   ├── mods/ra/rules/world.yaml                ← 已加 McpBridge: 注册
│   └── Support/Content/ra/v2/*.mix             ← RA 内容 (已自动下载)
├── trait_src/
│   └── McpBridge.cs                            ← 源 (~510 行)
├── mcp_server/
│   ├── server.py                               ← FastMCP, 14 tool
│   ├── schema.py                               ← pydantic 命令模型
│   ├── transport.py                            ← TCP client
│   ├── test_connect.py                         ← stand-alone 测试
│   └── requirements.txt
├── scripts/
│   ├── launch.bat                              ← 启动游戏
│   ├── build_openra.bat                        ← 重编译
│   ├── setup_all.bat                           ← 一键从零搭建
│   └── install_trait.bat                       ← 拷贝 trait + 注册 yaml
├── docs/
│   ├── DESIGN.md                               ← 设计文档
│   ├── PROTOCOL.md                             ← TCP JSON 协议
│   └── USAGE.md                                ← 玩法
├── claude_mcp_config.json                      ← Claude Code 配置示范
├── README.md
└── HANDOFF.md                                  ← 本文件
```

---

## 关键技术细节 (避免你日后踩坑)

1. **.NET 8 SDK 不能 admin** — 你用户没 admin 权限. 不能直接装到 Program Files. 我用 `dotnet-install.ps1` 装到 `%LOCALAPPDATA%\dotnet`. 所有脚本都把这个目录加到 PATH.

2. **OpenRA 编译 net6, 运行时拉 net8** — `bin/*.runtimeconfig.json` 我加了 `"rollForward": "Major"`, 允许 net6→net8. 别动这设置. 重编译后可能会被覆盖, 那时再加一次 (或写个 post-build script).

3. **中文路径 `D:\线列步兵\`** — 编译/启动都 OK, 没踩坑. 不需要符号链接.

4. **OpenRA shellmap 是非交互的** — 主菜单背景跑的那个 demo, 我们 dispatch 的命令真上去了但被脚本覆盖. 这是正常的. 进 Skirmish 才是真打.

5. **Order 模型** — 走 `Order.StartProduction(queueActor, name, count)` 和 `new Order("Move", actor, Target.FromCell(world, cpos), queued)`. 通过 `world.IssueOrder` 投递. 全部 dispatch 在 `Game.RunAfterTick(...)` lambda 里, 保证 sim 线程安全.

6. **trait 注册** — `^BaseWorld:` 是所有 RA 地图的世界 actor 模板. 加 `McpBridge:` 在那里, trait 对每张地图都生效. 在 `mods/ra/rules/world.yaml` 第 3 行加了块.

7. **portable Support 目录** — `OpenRA/Support/` 存在时 OpenRA 用它当 SupportDir (不污染 `%APPDATA%\OpenRA`). 内容/设置/日志都在那里. 整个项目可删除.

8. **OpenRA 命名怪** — Refinery = `Proc`, PowerPlant = `Powr`, Barracks = `Tent` (Soviet) / `Barr` (Allied), Soldier = `E1`, MCV = `Mcv`, Med Tank = `2tnk`. 玩家说"步兵", Claude 翻译时记得映射. 完整列表在 `mods/ra/rules/structures.yaml` 和 `infantry.yaml` / `vehicles.yaml`.

---

## 已知限制

- ✗ shellmap (主菜单 demo) 上 trait 工作但命令被脚本盖. 进 Skirmish 才能真控.
- ✗ `attack_move` 只接受目标格, 不接受目标 actor. Move + Attack 二者分开.
- ✗ Production 命令现在只 fire-and-forget. 没有 "建好通知" 的事件推送. 玩家自己 get_state 看.
- ✗ 没做 MCV 自动部署. 你说"部署 MCV", 我得先 find_unit('Mcv') 然后 deploy(ids).
- ✗ Claude Code 这层没 NL→atomic 翻译表. 我读 README/USAGE 自己推. 不行就你纠正.
- ✗ 多人对战 / 网络同步: 完全没做.
- ✗ 截图返 path 而非 base64. (Game.TakeScreenshot 写文件到 Support/Screenshots, 我没回读). 这块要改.

---

## 下一步建议 (优先级)

P1 (跑出真 demo):
- [ ] 玩家实际试一关 skirmish, 用 Claude Code 走完一局, 看体验
- [ ] Claude 端做 OpenRA actor 字母代码 ↔ 中文兵种名 的映射表
- [ ] 截图 base64 化 (读 Support/Screenshots 最新文件并 base64 编码返)

P2 (玩感):
- [ ] LLM 敌方 (替换 RA 内建 bot, 加嘴炮 + 性格 prompt)
- [ ] 事件推送 (trait 主动推 unit_built / unit_killed 给 Python, 协议改全双工)
- [ ] 战前/战后剧情 dialog

P3 (深化):
- [ ] 分层指挥 (玩家 → 参谋 LLM → 战线指挥 LLM → atomic), 类似线列步兵 v4
- [ ] 历史关卡 (RA 战役 mission)

---

## 修车备忘

如果跑不起来:

| 症状 | 修法 |
|---|---|
| `dotnet` not found | `set PATH=%LOCALAPPDATA%\dotnet;%PATH%` |
| OpenRA "must install .NET 6" | 改 `bin/*.runtimeconfig.json` 加 `"rollForward": "Major"` |
| 内容缺失 (启动后报红错 mix file) | 重新解压 ra-quickinstall.zip 到 `OpenRA/Support/Content/ra/v2/` |
| `[McpBridge] listening` 不出现 | 检查 `mods/ra/rules/world.yaml` 含 `McpBridge:` 块; 检查 `bin/OpenRA.Mods.Common.dll` 时间戳 |
| TCP 拒接 | 7777 端口被占, 设 `OPENRA_BRIDGE_PORT` env var, 同步改 world.yaml 的 `Port:` |
| 命令报 NRE | trait 已加 stack trace, error 字段会含位置 |

---

**完工时间** 约 1.5 小时. 卡过的点: winget admin / OpenRA mod search path / runtime version mismatch / shellmap 非交互. 全部解决.

**下次启动直接 `scripts\launch.bat` 即可.**
