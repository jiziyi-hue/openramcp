@echo off
REM Compile OpenRA from source. Run once before first launch.

setlocal
set ROOT=%~dp0..
set OPENRA_DIR=%ROOT%\OpenRA

if not exist "%OPENRA_DIR%\OpenRA.sln" (
    echo [ERROR] OpenRA source missing at %OPENRA_DIR%
    exit /b 1
)

REM Use user-local .NET install if not on PATH
if exist "%LOCALAPPDATA%\dotnet\dotnet.exe" (
    set "PATH=%LOCALAPPDATA%\dotnet;%PATH%"
)

where dotnet >nul 2>nul
if errorlevel 1 (
    echo [ERROR] dotnet not on PATH. Install .NET 8 SDK.
    exit /b 1
)

pushd "%OPENRA_DIR%"
echo [INFO] Building OpenRA via dotnet build...
dotnet build OpenRA.sln -c Release
if errorlevel 1 (
    echo [ERROR] Build failed
    popd
    exit /b 1
)
popd

REM Patch runtime configs (idempotent)
call "%~dp0fix_runtime_config.bat"

echo [INFO] Build done. bin\ should contain OpenRA.dll.
pause
endlocal
