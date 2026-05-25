"""
Scout daemon — long-running background process that polls OpenRA's
battlefield state, detects anomalies, and writes them to scout_events.jsonl.

The main Claude Code session can read the latest events via the MCP tool
`latest_scout_report()`.

Run:
    python -m mcp_server.scout_daemon
    (Ctrl-C to stop)

Options via env:
    SCOUT_POLL_SECONDS  default 30
    SCOUT_LOG_PATH      default <project_root>/scout_events.jsonl
    OPENRA_BRIDGE_HOST  default 127.0.0.1
    OPENRA_BRIDGE_PORT  default 7777

Anomalies tracked:
    * enemy_count_change: ±3 vs previous snapshot
    * self_count_drop: ≥2 units lost since last snapshot
    * self_low_hp_count: count of own units with hp_pct < 0.3 changed
    * threats_in_base: enemy unit within self_base radius 15
    * production_idle (warning): cash > 2000 and no production queue moving
    * resource_critical: cash < 300
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from .transport import OpenRATransport


HOST = os.environ.get("OPENRA_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENRA_BRIDGE_PORT", "7777"))
POLL = int(os.environ.get("SCOUT_POLL_SECONDS", "30"))
LOG_PATH = Path(os.environ.get("SCOUT_LOG_PATH", "")) if os.environ.get("SCOUT_LOG_PATH") \
    else Path(__file__).resolve().parent.parent / "scout_events.jsonl"


@dataclass
class Snapshot:
    tick: int
    self_count: int
    enemy_count: int
    self_low_hp_count: int
    self_cash: int
    self_power: int
    enemy_centroid: Optional[tuple] = None
    self_centroid: Optional[tuple] = None
    timestamp: float = 0.0


def take_snapshot(transport: OpenRATransport) -> Optional[Snapshot]:
    """One state poll. Returns Snapshot or None on transport error."""
    resp = transport.send_command({"type": "get_state", "include_enemies": True})
    if not resp.get("ok"):
        return None
    s = resp["state"]
    self_units = s.get("self_units", [])
    enemy_units = s.get("enemy_units", [])
    low_hp = sum(1 for u in self_units if u.get("hp_pct", 1.0) < 0.3)
    return Snapshot(
        tick=s["tick"],
        self_count=len(self_units),
        enemy_count=len(enemy_units),
        self_low_hp_count=low_hp,
        self_cash=s.get("self_cash", 0),
        self_power=s.get("self_power", 0),
        enemy_centroid=_centroid(enemy_units),
        self_centroid=_centroid(self_units),
        timestamp=time.time(),
    )


def _centroid(units: list) -> Optional[tuple]:
    if not units:
        return None
    n = len(units)
    return (sum(u["pos"]["x"] for u in units) // n,
            sum(u["pos"]["y"] for u in units) // n)


def distance(a: tuple, b: tuple) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def detect_anomalies(curr: Snapshot, prev: Optional[Snapshot]) -> list:
    """Return list of event dicts. Each: {severity, kind, message, ...data}."""
    events = []

    if curr.self_cash < 300:
        events.append({
            "severity": "warn",
            "kind": "resource_critical",
            "message": f"Cash low: ${curr.self_cash}",
        })

    if curr.self_power < 0:
        events.append({
            "severity": "warn",
            "kind": "power_negative",
            "message": f"Power deficit: {curr.self_power}",
        })

    if curr.self_low_hp_count > 0:
        events.append({
            "severity": "info" if curr.self_low_hp_count < 3 else "warn",
            "kind": "low_hp",
            "message": f"{curr.self_low_hp_count} unit(s) below 30% HP",
            "count": curr.self_low_hp_count,
        })

    # Threats near base
    if curr.enemy_centroid and curr.self_centroid:
        d = distance(curr.enemy_centroid, curr.self_centroid)
        if d < 18:
            events.append({
                "severity": "alert",
                "kind": "enemy_at_base",
                "message": f"Enemy centroid within {int(d)} cells of self centroid",
                "distance": int(d),
            })

    if prev is not None:
        dropped = prev.self_count - curr.self_count
        if dropped >= 2:
            events.append({
                "severity": "warn",
                "kind": "self_losses",
                "message": f"Lost {dropped} unit(s) since last poll",
                "count": dropped,
            })

        e_delta = curr.enemy_count - prev.enemy_count
        if abs(e_delta) >= 3:
            events.append({
                "severity": "info",
                "kind": "enemy_count_change",
                "message": f"Enemy unit count changed by {e_delta:+d}",
                "delta": e_delta,
                "new_count": curr.enemy_count,
            })

    return events


def main() -> int:
    print(f"[scout] starting. poll={POLL}s log={LOG_PATH}", flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    transport = OpenRATransport(host=HOST, port=PORT)
    prev: Optional[Snapshot] = None

    # truncate log on start (fresh session)
    with open(LOG_PATH, "w", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "kind": "scout_started",
            "timestamp": time.time(),
            "poll_interval_s": POLL,
        }) + "\n")

    while True:
        try:
            curr = take_snapshot(transport)
            if curr is None:
                _append({
                    "severity": "info",
                    "kind": "bridge_disconnected",
                    "message": "OpenRA bridge not reachable",
                    "timestamp": time.time(),
                })
            else:
                events = detect_anomalies(curr, prev)
                # log snapshot every poll (low priority)
                _append({
                    "severity": "debug",
                    "kind": "snapshot",
                    "tick": curr.tick,
                    "self_count": curr.self_count,
                    "enemy_count": curr.enemy_count,
                    "cash": curr.self_cash,
                    "power": curr.self_power,
                    "timestamp": curr.timestamp,
                })
                for ev in events:
                    ev["timestamp"] = curr.timestamp
                    ev["tick"] = curr.tick
                    _append(ev)
                prev = curr
        except KeyboardInterrupt:
            print("[scout] stopped", flush=True)
            return 0
        except Exception as e:
            _append({
                "severity": "error",
                "kind": "daemon_error",
                "message": str(e),
                "timestamp": time.time(),
            })

        time.sleep(POLL)


def _append(event: dict) -> None:
    """Append one event to the JSONL log."""
    line = json.dumps(event, ensure_ascii=False)
    with open(LOG_PATH, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    if event.get("severity") in ("warn", "alert", "error"):
        print(f"[scout] {event.get('severity').upper()}: {event.get('message')}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
