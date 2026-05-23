@echo off
REM Install MCP bridge + macro/strategy traits into OpenRA source tree.
REM Run once after cloning OpenRA, before first build. Idempotent — re-run
REM whenever trait_src\*.cs is updated.

setlocal
set ROOT=%~dp0..
set TRAIT_SRC=%ROOT%\trait_src
set WORLD_DIR=%ROOT%\OpenRA\OpenRA.Mods.Common\Traits\World
set PLAYER_DIR=%ROOT%\OpenRA\OpenRA.Mods.Common\Traits\Player
set COND_DIR=%ROOT%\OpenRA\OpenRA.Mods.Common\Traits\Conditions
set RA_WORLD=%ROOT%\OpenRA\mods\ra\rules\world.yaml

if not exist "%WORLD_DIR%" (
    echo [ERROR] OpenRA source not found at %WORLD_DIR%. Did you clone OpenRA?
    exit /b 1
)

echo [INFO] Copying McpBridge.cs                  → World\
copy /Y "%TRAIT_SRC%\McpBridge.cs"                  "%WORLD_DIR%\McpBridge.cs" >nul
if errorlevel 1 ( echo [ERROR] Copy McpBridge failed & exit /b 1 )

echo [INFO] Copying HumanAssistantBot.cs           → Player\
copy /Y "%TRAIT_SRC%\HumanAssistantBot.cs"          "%PLAYER_DIR%\HumanAssistantBot.cs" >nul
if errorlevel 1 ( echo [ERROR] Copy HumanAssistantBot failed & exit /b 1 )

echo [INFO] Copying StrategyControllerBotModule.cs → Player\
copy /Y "%TRAIT_SRC%\StrategyControllerBotModule.cs" "%PLAYER_DIR%\StrategyControllerBotModule.cs" >nul
if errorlevel 1 ( echo [ERROR] Copy StrategyControllerBotModule failed & exit /b 1 )

echo [INFO] Copying GrantConditionOnHumanOwner.cs  → Conditions\
copy /Y "%TRAIT_SRC%\GrantConditionOnHumanOwner.cs" "%COND_DIR%\GrantConditionOnHumanOwner.cs" >nul
if errorlevel 1 ( echo [ERROR] Copy GrantConditionOnHumanOwner failed & exit /b 1 )

REM Check if McpBridge already registered in world.yaml
findstr /C:"McpBridge:" "%RA_WORLD%" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Registering McpBridge in mods\ra\rules\world.yaml
    powershell -ExecutionPolicy Bypass -Command ^
        "$path = '%RA_WORLD%'; $content = Get-Content -Raw -Encoding UTF8 $path; $insert = \"`tMcpBridge:`r`n`t`tPort: 7777`r`n`t`tHost: 127.0.0.1`r`n`t`tVerbose: true`r`n\"; $content = $content -replace '(?m)^(\^BaseWorld:\r?\n\tInherits: \^Palettes\r?\n\tAlwaysVisible:\r?\n)', \"`$1$insert\"; Set-Content -Path $path -Value $content -Encoding UTF8 -NoNewline"
) else (
    echo [INFO] McpBridge already registered in world.yaml — skipping
)

echo [OK] Trait installed. Now run scripts\build_openra.bat.
endlocal
