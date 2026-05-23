# USAGE — 跑起来 + 跟游戏说话

> 本文档假设你已读过 [README.md](../README.md) 和 [DESIGN.md](DESIGN.md).

---

## 0. 前置依赖检查清单

| 项 | 验证命令 | 备注 |
|---|---|---|
| .NET 8 SDK | `dotnet --version` → `8.0.x` | `winget install Microsoft.DotNet.SDK.8` |
| Python 3.10+ | `python --version` | Anaconda 自带也行 |
| Git | `git --version` | |
| Claude Code | 已安装 + 能跑 | https://claude.com/claude-code |

---

## 1. 第一次安装

### 1.1 clone + 拷 trait

仓库不含 OpenRA 源码 (太大 + GPL 独立保存). 你需要:

```bash
cd D:/openra_mcp

# clone OpenRA (一次, ~250 MB)
git clone --depth=1 --branch release-20250330 https://github.com/OpenRA/OpenRA.git

# 把我们的 trait 复制进 OpenRA 源码树
copy trait_src\McpBridge.cs OpenRA\OpenRA.Mods.Common\Traits\World\McpBridge.cs

# 在 RA mod 的 world.yaml 注册 (已自动, 见 scripts/install_trait.bat)
# 手工方式: 在 OpenRA\mods\ra\rules\world.yaml 的 ^BaseWorld: 下加
#   McpBridge:
#       Port: 7777
#       Host: 127.0.0.1
#       Verbose: true
```

### 1.2 编译 OpenRA

```bash
scripts\build_openra.bat
```

首次编译 ~5 分钟. 成功后 `OpenRA\bin\OpenRA.dll` 存在.

### 1.3 装 Python deps

```bash
cd mcp_server
python -m pip install -r requirements.txt
```

### 1.4 注册 MCP server 到 Claude Code

把 `claude_mcp_config.json` 中的 `openra-bridge` 块加到你的 Claude Code 配置 (通常在 `~/.claude/settings.json` 的 `mcpServers` 字段下).

或者命令行注册:

```bash
claude mcp add openra-bridge \
  --command python \
  --args "-m" "mcp_server.server" \
  --cwd "D:\线列步兵\openra_mcp"
```

(具体语法看你 Claude Code 版本.)

---

## 2. 跑起来 (每次玩)

### 2.1 启动游戏 + bridge

```bash
scripts\launch.bat
```

发生的事:
1. OpenRA 客户端启动 (RA mod)
2. `McpBridge` trait 在 `127.0.0.1:7777` 开 TCP 监听
3. (你 Claude Code 端首次调用 MCP 工具时, 自动 spawn Python MCP server)

### 2.2 进入战场

在 OpenRA 主菜单:
1. **Skirmish** (单人对 AI)
2. 选地图 (小图先, e.g. `Forest Path`, `Berthier`)
3. 阵营随便
4. 难度选 `Normal`
5. 点 **Play** 进入战场

OpenRA 会自动出生 MCV. trait 已激活, 在等你 Claude Code 这边的命令.

### 2.3 跟 Claude Code 说话

打开 Claude Code 终端, 跟我说话即可:

```
你: 看看场上有什么
我: [调用 get_state()]
   "你: 1 MCV (id=12), 在 (28, 30). 资源 5000. 电力 0. 地图 Forest Path, 96×96."

你: 部署 MCV
我: [调用 list_units(kind="MCV") + 发 "DeployTransform" order via attack tool? Actually use deploy via trait]
   ... (具体看 trait 实现的命令支持)

你: 建 2 个矿场
我: [调用 build("Refinery", count=2)]
   "已下达 2 个 Refinery 的建造命令. 工兵正在选址."

你: 出 5 步兵, 派去南面
我: [调用 train("Soldier", count=5); list_units(kind="Soldier"); move(ids, target)]
   "5 步兵已下产线. 出来后我会带他们去 (50, 70)."

你: 现在战场什么情况
我: [调用 screenshot() + get_state()]
   [我看图 + 数据, 给出叙事化情报]
```

---

## 3. 命令清单 (跟我说的话, 我会调对应工具)

| 你说 | 我调的工具 |
|---|---|
| "场上什么样" / "情报" | `get_state()` + 可能 `screenshot()` |
| "我有什么单位" | `list_units(owner='self')` |
| "敌方在哪" | `list_units(owner='enemy')` |
| "建 X 个 Y" | `build(structure='Y', count=X)` |
| "出 N 个兵 Z" | `train(unit='Z', count=N)` |
| "派 X 单位去 Y" | `find_unit('X')` 或 `list_units` → `move(ids, target)` |
| "打 X" | `find_unit('X')` → `attack(ids, target)` |
| "X 单位变守势" | `set_stance(ids, 'Defend')` |
| "暂停" / "继续" | `pause()` / `resume()` |
| "看一眼画面" | `screenshot()` |

---

## 4. 常见问题

### Q: OpenRA 启动后 trait 不工作 (log 无 `[McpBridge] listening`)

A: 检查:
- `OpenRA\mods\ra\rules\world.yaml` 是否包含 `McpBridge:` 块
- 重新编译 (`scripts\build_openra.bat`)
- 端口 7777 是否被占用 (`netstat -ano | findstr 7777`)

### Q: Claude Code 调工具说 "OpenRA bridge not connected"

A: trait 没启动 (上 Q) 或 Python MCP server 没起来. 看 OpenRA 控制台日志和 Claude Code MCP server 日志.

### Q: build("Refinery") 返回 `not_buildable`

A: 内部 RA mod 用代号, 不是显示名. 试 `Proc` (Refinery 的内部 actor name). 完整列表见 `mods/ra/rules/structures.yaml`.

### Q: 中文路径编译报错

A: 创建符号链接到英文路径:
```cmd
mklink /D D:\openra_build D:\线列步兵\openra_mcp\OpenRA
```
然后用 `D:\openra_build\` 跑 `make.cmd`.

### Q: 我能否多人对战 + LLM 帮我?

A: 当前 demo 只支持 skirmish (vs RA 内建 bot). 多人对战的命令系统有同步问题, 后期再加.

---

## 5. 调试技巧

### 看 trait 收到了什么

设 `Verbose: true` (默认开). 看 OpenRA 控制台 (启动时打开的 cmd 窗口) 中 `[McpBridge]` 开头的行.

### 看 MCP server 收发

```bash
python -m mcp_server.server
# (stdio, Claude Code spawn 时连)
```

如果想交互测试不通过 Claude Code, 用 `mcp inspect` 工具 (mcp pkg 自带):

```bash
mcp inspect mcp_server.server
```

### 端到端 dry run (无 OpenRA)

Python MCP server 会在 TCP 连不上时返回 `{"ok": false, "error": "...not connected..."}`. Claude Code 可以照常调用工具看 error.

---

**End of USAGE v0.1**
