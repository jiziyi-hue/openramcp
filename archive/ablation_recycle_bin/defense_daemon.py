"""Long-lived helper that holds the TacticalEngine alive.

Tactical engine lives as a module global in tactical.py. It only ticks while
the Python process is running. Short Bash calls (`python -c '...'`) re-import
the module per invocation and the engine dies on script exit.

This daemon keeps a single Python process alive with the engine armed. Use
it when driving the bridge from outside Claude Code's MCP session (i.e. via
ad-hoc scripts). Inside Claude Code, the MCP server itself is already long-
lived, so you don't need this — call enable_auto_defense() through Claude.

Run:
    python -m mcp_server.defense_daemon
    Ctrl-C to stop.

Args via env:
    DEFENSE_RADIUS  default 22
    DEFENSE_CENTER  default "self_base"  (any NamedTarget enum value)
"""

from __future__ import annotations

import os
import sys
import time

from mcp_server import server


def main() -> int:
    radius = int(os.environ.get("DEFENSE_RADIUS", "22"))
    center = os.environ.get("DEFENSE_CENTER", "self_base")
    print(f"[defense] arming auto-defense center={center} radius={radius}",
          flush=True)
    r = server.enable_auto_defense(center_named=center, radius=radius)
    if not r.get("ok"):
        print(f"[defense] FAILED to arm: {r}", flush=True)
        return 1
    print(f"[defense] armed at {r.get('center')} radius {r.get('radius')}",
          flush=True)

    # Keep process alive so tactical_engine's polling thread keeps ticking.
    # Status is logged every 30s so you can see retargets / dispatches.
    try:
        last_status = None
        while True:
            time.sleep(30)
            st = server.tactical_status()
            cur = (st.get("active_assaults"), st.get("tick_count"),
                   st.get("retargets"), st.get("cohesion_halts"),
                   st.get("defense_dispatches"))
            if cur != last_status:
                print(f"[defense] assaults={cur[0]} ticks={cur[1]} "
                      f"retargets={cur[2]} halts={cur[3]} "
                      f"defenses={cur[4]}", flush=True)
                last_status = cur
    except KeyboardInterrupt:
        print("[defense] stopping...", flush=True)
        server.disable_auto_defense()
        return 0


if __name__ == "__main__":
    sys.exit(main())
