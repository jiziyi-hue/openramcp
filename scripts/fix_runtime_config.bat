@echo off
REM Patch OpenRA's runtimeconfig.json files to allow running on .NET 8
REM when only the 8.0 runtime is installed (default user-local install).
REM Idempotent. Safe to run repeatedly.

setlocal
set ROOT=%~dp0..
set BIN=%ROOT%\OpenRA\bin

for %%F in (OpenRA.runtimeconfig.json OpenRA.Server.runtimeconfig.json OpenRA.Utility.runtimeconfig.json) do (
    if exist "%BIN%\%%F" (
        findstr /C:"rollForward" "%BIN%\%%F" >nul 2>nul
        if errorlevel 1 (
            echo [INFO] Patching %%F with rollForward: Major
            powershell -ExecutionPolicy Bypass -Command ^
                "$path = '%BIN%\%%F'; $j = Get-Content -Raw $path | ConvertFrom-Json; $j.runtimeOptions | Add-Member -Force -NotePropertyName 'rollForward' -NotePropertyValue 'Major'; $j | ConvertTo-Json -Depth 10 | Set-Content -Path $path -Encoding ASCII"
        )
    )
)
endlocal
