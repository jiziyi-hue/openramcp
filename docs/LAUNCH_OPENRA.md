# OpenRA 启动速查

> 每局开始用. Claude Code 帮起时也照这套. Git Bash 路径转义坑多, 记最稳法.

---

## 最稳启动法 (bash 直跑)

```bash
cd /d/openra_mcp/OpenRA
export PATH="$LOCALAPPDATA/dotnet:$PATH"
export DOTNET_ROOT="$LOCALAPPDATA/dotnet"
./bin/OpenRA.exe Engine.EngineDir=.. Game.Mod=ra
```

后台跑加 `&` 或用 Claude `run_in_background:true`.

验跑了:

```bash
cmd //c "tasklist | findstr /I openra"
```

应见 `OpenRA.exe   <PID>   Console   1   <RAM>K`.

---

## 进游戏

1. 主菜单 → **Skirmish**
2. 选小图 (1v1)
3. AI = **Normal**
4. **Play**
5. 战场内按 `-` 调慢速 0.5× (跟 LLM 对话有时间)
6. 按 `空格` 暂停 / 继续

---

## Scout Daemon (可选, 30s push 战报)

```bash
cd /d/openra_mcp                                # 主仓
# 或: cd /d/openra_mcp/.claude/worktrees/<branch> # worktree 测时
python -m mcp_server.scout_daemon
```

后台跑加 `&`.

---

## 验 bridge 连通

进战场后 Claude 内:

```
get_state()
```

返 `ok: true` + units/cash/tick → 通.
返 `OpenRA bridge not connected (TCP 127.0.0.1:7777)` → bridge trait 没挂 (没进战场, 或 trait 编译 fail).

---

## 坑 (避雷)

### Git Bash 路径转义

```bash
# ✗ 死: bash 把 \O \o 当 escape, cmd 收到 path 残
cmd //c "cd /d D:\openra_mcp && ..."
cmd //c "D: && cd \openra_mcp\OpenRA && ..."

# ✓ 活: cd 用 forward slash, OpenRA.exe 用相对路径
cd /d/openra_mcp/OpenRA
./bin/OpenRA.exe Engine.EngineDir=.. Game.Mod=ra
```

### `start "title"` + backslash path 死

```cmd
# ✗ start 把 bin\OpenRA.exe 解析怪
start "OpenRA" /D OpenRA cmd /c "bin\OpenRA.exe ..."

# ✓ 直接跑, 不用 start
cd OpenRA && bin\OpenRA.exe ...
```

### MCP server cwd vs worktree

- MCP server **绑 session 启动时 cwd**, 重开 Claude session 切 cwd 才换
- 测 worktree code: 必从 worktree cwd 起 Claude session
- `pwd` 验当前

### Python 进程冲突

scout_daemon 残留 / 旧 session 占 TCP 端口:

```bash
cmd //c "tasklist | findstr /I python"
cmd //c "taskkill /F /IM python.exe"           # 清干净 (慎, 杀所有 python)
```

---

## 一键启动 script (TODO)

```bash
# scripts/launch.sh — 一键起 OpenRA + scout daemon
#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PATH="$LOCALAPPDATA/dotnet:$PATH"
export DOTNET_ROOT="$LOCALAPPDATA/dotnet"
(cd OpenRA && ./bin/OpenRA.exe Engine.EngineDir=.. Game.Mod=ra) &
sleep 3
python -m mcp_server.scout_daemon &
echo "OpenRA + scout daemon 起. 进 skirmish 后回 Claude."
```

(注: `scripts/launch.bat` 已存但 BOM 问题, sh 版未写.)
