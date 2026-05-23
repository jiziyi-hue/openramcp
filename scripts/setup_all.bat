@echo off
REM ============================================================
REM  One-shot bootstrap. Run on a fresh checkout to set up:
REM    1. .NET 8 SDK (user-local)
REM    2. OpenRA clone
REM    3. McpBridge trait installed
REM    4. OpenRA compiled
REM    5. Python deps
REM ============================================================

setlocal
set ROOT=%~dp0..
set DOTNET_DIR=%LOCALAPPDATA%\dotnet
set OPENRA_DIR=%ROOT%\OpenRA

echo === Step 1/5: .NET 8 SDK =========================================

if exist "%DOTNET_DIR%\dotnet.exe" (
    echo [SKIP] dotnet already at %DOTNET_DIR%
) else (
    echo [INFO] Installing .NET 8 SDK to %DOTNET_DIR% (user-local, no admin)
    powershell -ExecutionPolicy Bypass -Command "& { Invoke-WebRequest -Uri 'https://builds.dotnet.microsoft.com/dotnet/scripts/v1/dotnet-install.ps1' -OutFile $env:TEMP\dotnet-install.ps1; & $env:TEMP\dotnet-install.ps1 -Channel 8.0 -InstallDir %DOTNET_DIR% -NoPath }"
    if errorlevel 1 (
        echo [ERROR] .NET install failed
        exit /b 1
    )
)
set "PATH=%DOTNET_DIR%;%PATH%"
dotnet --version
if errorlevel 1 (
    echo [ERROR] dotnet not callable after install
    exit /b 1
)

echo === Step 2/5: OpenRA clone =======================================

if exist "%OPENRA_DIR%\OpenRA.sln" (
    echo [SKIP] OpenRA already at %OPENRA_DIR%
) else (
    git clone --depth=1 --branch release-20250330 https://github.com/OpenRA/OpenRA.git "%OPENRA_DIR%"
    if errorlevel 1 (
        echo [ERROR] git clone failed
        exit /b 1
    )
)

echo === Step 3/5: Install McpBridge trait ============================

call "%~dp0install_trait.bat"
if errorlevel 1 exit /b 1

echo === Step 4/5: Compile OpenRA =====================================

pushd "%OPENRA_DIR%"
dotnet build OpenRA.sln -c Release
set BUILD_RC=%ERRORLEVEL%
popd
if not "%BUILD_RC%"=="0" (
    echo [ERROR] OpenRA build failed
    exit /b 1
)

echo === Step 5/5: Python deps ========================================

python -m pip install --quiet --upgrade -r "%ROOT%\mcp_server\requirements.txt"
if errorlevel 1 (
    echo [WARN] pip install failed, continuing
)

echo.
echo ==================================================================
echo  SETUP COMPLETE.
echo  Next: 1) Run scripts\launch.bat to start OpenRA + bridge
echo        2) Configure Claude Code MCP server (see claude_mcp_config.json)
echo        3) Talk to Claude Code to drive the game
echo ==================================================================
endlocal
