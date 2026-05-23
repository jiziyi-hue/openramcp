# openra_mcp — Claude Code 驱动 OpenRA

> 用 Claude Code 当指挥台, OpenRA 当渲染+物理引擎. 自然语言下令 → MCP 工具调用 → 游戏画面实时执行.

## 这是什么

一个 demo / proof-of-concept, 验证 MCP + Claude Code 能否成为 RTS 游戏的"控制台". 玩家在 Claude Code 中用中文/英文给出意图 (e.g. "派 5 个步兵守右路"), Claude 拆成 atomic MCP 工具调用 (`build`, `train`, `move`, `attack`...), 经 Python MCP server 转 TCP 命令给 OpenRA 内嵌的 `MCPBridgeTrait`, OpenRA 引擎执行并渲染.

跟 `../DESIGN_v4.md` (线列步兵) 是同一家族, 但用 OpenRA 现成资源, 实施快很多.

## 快速开始

### 前置

- Windows 10/11
- .NET 8 SDK (`winget install Microsoft.DotNet.SDK.8`)
- Python 3.10+ + pip
- Git
- Claude Code (https://claude.com/claude-code)

### 步骤

```bash
# 1. clone OpenRA (本仓库不含 OpenRA 源码)
cd D:/openra_mcp
git clone --depth=1 --branch release-20250330 https://github.com/OpenRA/OpenRA.git

# 2. 把我们的 trait 复制进 OpenRA 源码树
copy trait_src\MCPBridgeTrait.cs OpenRA\OpenRA.Mods.Common\Traits\

# 3. 把 trait 注册到 RA mod 的 world.yaml (见 USAGE.md)

# 4. 编译
scripts\build_openra.bat

# 5. 启动 OpenRA + MCP server
scripts\launch.bat

# 6. 在 Claude Code 加上 mcp_server 配置 (见 claude_mcp_config.json)
#    然后跟 Claude Code 说: "看看场上有什么 / 建一个 power plant / 出 3 个步兵"
```

详见 [docs/USAGE.md](docs/USAGE.md).

## 目录结构

```
openra_mcp/
├── OpenRA/                   # clone 自 GitHub (不在仓库)
├── mcp_server/               # Python MCP server (stdio)
│   ├── server.py             # FastMCP 暴露 10 个 atomic tools
│   ├── schema.py             # pydantic 命令模型
│   ├── transport.py          # TCP client → OpenRA
│   └── requirements.txt
├── trait_src/                # 我们写的 C# trait
│   └── MCPBridgeTrait.cs     # 在 OpenRA 内开 TCP server
├── scripts/
│   ├── launch.bat            # 一键启动
│   └── build_openra.bat      # 编译 OpenRA
├── docs/
│   ├── DESIGN.md             # 总设计
│   ├── PROTOCOL.md           # TCP JSON 协议
│   └── USAGE.md              # 玩法 + 配置
├── claude_mcp_config.json    # Claude Code 配置示例
└── README.md                 # 本文件
```

## 架构 (一句话)

```
你 NL → Claude Code (= LLM) → MCP stdio → server.py → TCP 7777 → MCPBridgeTrait → OpenRA → 视觉
```

详见 [docs/DESIGN.md](docs/DESIGN.md).

## 状态

⚠️ Work-in-progress. P0/P1 阶段. MCP server skeleton + 协议已稳; trait C# 实现 + 端到端验证待做.

详见任务清单 (`TaskList`).

## License

我们的代码: MIT (跟枢灵同).  
OpenRA: GPLv3 (clone 单独保存, 不混进我们这边).  
RA art assets: Westwood 商业版权 (OpenRA 让你导入原版 RA 才能玩, 商业作品不可分发).
