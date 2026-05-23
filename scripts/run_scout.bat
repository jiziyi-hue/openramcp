@echo off
REM Launch the scout daemon. Polls OpenRA every SCOUT_POLL_SECONDS (default 30s)
REM and writes events to D:\openra_mcp\scout_events.jsonl.
REM
REM Main Claude session reads events via the MCP tool `latest_scout_report`.
REM
REM Stop with Ctrl-C in this window.

setlocal
set ROOT=%~dp0..

cd /d "%ROOT%"
python -m mcp_server.scout_daemon

endlocal
