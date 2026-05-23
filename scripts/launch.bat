@echo off
REM ============================================================
REM  OpenRA + MCP launcher
REM
REM  Starts OpenRA (RA mod) with the MCP bridge trait active.
REM  The Python MCP server is spawned by Claude Code on demand
REM  (see claude_mcp_config.json).
REM
REM  Once OpenRA is at the main menu, go: Skirmish -> choose map ->
REM  Play. From Claude Code, MCP tools will now reach the game.
REM ============================================================

setlocal

set ROOT=%~dp0..
set OPENRA_DIR=%ROOT%\OpenRA
set MCP_DIR=%ROOT%\mcp_server

REM --- sanity checks -------------------------------------------------------
if not exist "%OPENRA_DIR%\launch-game.cmd" (
    echo [ERROR] OpenRA not found at %OPENRA_DIR%
    echo Run scripts\setup_all.bat first.
    pause
    exit /b 1
)

REM --- use user-local .NET install ---------------------------------------
if exist "%LOCALAPPDATA%\dotnet\dotnet.exe" (
    set "PATH=%LOCALAPPDATA%\dotnet;%PATH%"
    set "DOTNET_ROOT=%LOCALAPPDATA%\dotnet"
)

where dotnet >nul 2>nul
if errorlevel 1 (
    echo [ERROR] dotnet not on PATH. Run scripts\setup_all.bat first.
    pause
    exit /b 1
)

REM --- compile if needed --------------------------------------------------
if not exist "%OPENRA_DIR%\bin\OpenRA.dll" (
    echo [INFO] OpenRA not yet built. Running build...
    call "%~dp0build_openra.bat"
    if errorlevel 1 exit /b 1
)

REM --- ensure runtimeconfig has rollForward (in case of fresh build) -----
call "%~dp0fix_runtime_config.bat"

REM --- ensure Python deps once -------------------------------------------
python -m pip install --quiet -r "%MCP_DIR%\requirements.txt" 2>nul

REM --- launch OpenRA ------------------------------------------------------
echo.
echo [INFO] Starting OpenRA (RA mod). McpBridge will listen on 127.0.0.1:7777.
echo [INFO] When the main menu loads, choose Skirmish to start a game.
echo.
start "OpenRA + McpBridge" /D "%OPENRA_DIR%" cmd /c "bin\OpenRA.exe Engine.EngineDir=.. Engine.LaunchPath=\"%~dpf0\" Game.Mod=ra"

REM --- give bridge time to start -----------------------------------------
timeout /t 6 /nobreak >nul

echo [INFO] OpenRA process started. Test the bridge from another shell:
echo        python mcp_server\test_connect.py get_state
echo.
echo [INFO] To talk via Claude Code, add the openra-bridge MCP server
echo        from claude_mcp_config.json to your Claude Code settings.
echo.
pause
endlocal
