"""Pytest fixtures + environment. Keeps the tactical daemon from spawning
its polling thread during unit tests so they remain deterministic."""

import os

# Must be set BEFORE mcp_server.tactical is imported.
os.environ.setdefault("TACTICAL_DISABLED", "1")
