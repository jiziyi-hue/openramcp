"""
Session recorder — passive in-game data collector for paper experiments.

Run alongside an OpenRA session. The recorder polls get_state at 1Hz,
samples list_squads, and writes a structured JSONL log to disk. The
player / LLM plays normally — no scenario constraints, no scripted
dispatch. Offline analysis can slice the log any way needed (per-squad
trajectories, per-second alive count, position deltas, etc).

Why this design (vs the scenario runner):
  - Real play has compound dispatches, mid-game decisions, retries.
  - A single run produces a whole timeline, not one row.
  - Different paper questions reuse the same raw log.

Usage:
  python -m mcp_server.tools.session_recorder \
      --out logs/recording_$(date +%s).jsonl \
      --interval 1.0

Stops on Ctrl-C or when bridge disconnects for 10 consecutive polls.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from mcp_server.transport import OpenRATransport


def _send(t: OpenRATransport, cmd: str, **kw) -> dict:
    payload = {"type": cmd}
    payload.update(kw)
    return t.send_command(payload)


def record(out_path: Path, interval_s: float, max_duration_s: Optional[float]):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = OpenRATransport()
    if not t.connect():
        print("ERROR: bridge not connected (OpenRA running with MCPBridge?)")
        return

    f = out_path.open("a", encoding="utf-8")
    consecutive_failures = 0
    started_t = time.time()
    sample_idx = 0

    print(f"recording → {out_path}  (interval {interval_s}s, Ctrl-C to stop)")
    try:
        while True:
            if max_duration_s is not None and (time.time() - started_t) > max_duration_s:
                print(f"reached max duration {max_duration_s}s, stopping")
                break

            wall_t = time.time()
            state_resp = _send(t, "get_state", include_enemies=True)
            squads_resp = _send(t, "list_squads")

            if not state_resp.get("ok"):
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    print("bridge dead 10x consecutive, stopping")
                    break
                time.sleep(interval_s)
                continue
            consecutive_failures = 0

            s = state_resp["state"]
            entry = {
                "sample_idx": sample_idx,
                "wall_t": round(wall_t, 3),
                "tick": s.get("tick"),
                "paused": s.get("paused"),
                "self_cash": s.get("self_cash"),
                "self_power": s.get("self_power"),
                "self_units": [
                    {"id": u["id"], "kind": u["kind"],
                     "x": u["pos"]["x"], "y": u["pos"]["y"],
                     "hp": round(u.get("hp_pct", 0.0), 3)}
                    for u in s.get("self_units", [])
                ],
                "enemy_units": [
                    {"id": u["id"], "kind": u["kind"],
                     "x": u["pos"]["x"], "y": u["pos"]["y"],
                     "hp": round(u.get("hp_pct", 0.0), 3)}
                    for u in s.get("enemy_units", [])
                ],
                "squads": squads_resp.get("squads", []) if squads_resp.get("ok") else [],
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            sample_idx += 1

            time.sleep(interval_s)
    except KeyboardInterrupt:
        print(f"\nstopped by user after {sample_idx} samples")
    finally:
        f.close()
        print(f"wrote {sample_idx} samples to {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="poll interval in seconds (default 1.0)")
    ap.add_argument("--max-duration", type=float, default=None,
                    help="optional stop after N seconds")
    args = ap.parse_args()
    record(Path(args.out), args.interval, args.max_duration)


if __name__ == "__main__":
    main()
