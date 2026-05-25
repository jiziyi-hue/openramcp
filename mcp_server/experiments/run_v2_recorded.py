"""
run_v2_recorded — Run scenarios_v2 with ffmpeg desktop capture per scenario.

For each scenario: start ffmpeg gdigrab → run scenario → stop ffmpeg.
Output: logs/v2_videos/<task_name>_<timestamp>.mp4

Each scenario gets its own clip so the paper supplement can include
individual demos without manual splitting.

Usage:
  python -m mcp_server.experiments.run_v2_recorded --all
  python -m mcp_server.experiments.run_v2_recorded --scenarios T1,T2
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp_server.transport import OpenRATransport
from mcp_server.experiments.scenarios_v2 import (
    ALL_SCENARIOS, CSV_COLUMNS
)


VIDEO_DIR = Path("logs/v2_videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def _start_recorder(out_path: Path) -> subprocess.Popen:
    """Start ffmpeg gdigrab desktop capture. Returns the Popen handle."""
    # 15 fps, no audio, mp4 with libx264 fast preset.
    cmd = [
        "ffmpeg",
        "-y",                       # overwrite
        "-f", "gdigrab",
        "-framerate", "15",
        "-i", "desktop",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-loglevel", "error",
        str(out_path),
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_recorder(proc: subprocess.Popen) -> None:
    """Gracefully stop ffmpeg by sending 'q' on stdin."""
    try:
        proc.stdin.write(b"q")
        proc.stdin.flush()
        proc.wait(timeout=8)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="logs/v2_results_recorded.csv")
    ap.add_argument("--cooldown", type=float, default=5.0)
    args = ap.parse_args()

    if args.all:
        ids = list(ALL_SCENARIOS.keys())
    elif args.scenarios:
        ids = [s.strip() for s in args.scenarios.split(",")]
    else:
        ids = list(ALL_SCENARIOS.keys())

    t = OpenRATransport()
    if not t.connect():
        raise SystemExit("OpenRA bridge not connected.")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    print(f"\n=== Recording {len(ids)} v2 scenarios ===\n")
    for sid in ids:
        fn = ALL_SCENARIOS.get(sid)
        if fn is None:
            print(f"[skip] unknown {sid}")
            continue

        ts = datetime.now().strftime("%H%M%S")
        video_path = VIDEO_DIR / f"{sid}_{fn.__name__}_{ts}.mp4"
        print(f"\n--- {sid} → {video_path.name} ---")

        # start recorder, let it stabilize a moment
        rec = _start_recorder(video_path)
        time.sleep(1.0)

        try:
            r = fn(t)
        except Exception as e:
            r = {
                "task_name": fn.__name__,
                "nl_input": "",
                "unit_count": 0,
                "unit_kinds": {},
                "subtasks_generated": 0,
                "unit_selection_correct": False,
                "reached_target": False,
                "tactical_intent_met": False,
                "total_latency_ms": 0,
                "failure_reason": f"exception: {e}",
                "corrections": 0,
                "recording_path": "",
            }

        # stop recorder
        time.sleep(1.0)  # tail
        _stop_recorder(rec)

        r["recording_path"] = str(video_path)
        results.append(r)
        verdict = "PASS" if r["tactical_intent_met"] else "FAIL"
        print(f"  {verdict}: {r['task_name']}")
        print(f"  nl: {r['nl_input']}")
        print(f"  reached={r['reached_target']} intent_met={r['tactical_intent_met']}")
        print(f"  video: {video_path}")
        if r["failure_reason"]:
            print(f"  fail: {r['failure_reason']}")
        time.sleep(args.cooldown)

    # CSV
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in results:
            row = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
                   for k, v in r.items()}
            w.writerow(row)

    pass_n = sum(1 for r in results if r["tactical_intent_met"])
    print(f"\n=== Summary: {pass_n}/{len(results)} pass ===")
    print(f"CSV: {out_path}")
    print(f"Videos: {VIDEO_DIR}")


if __name__ == "__main__":
    main()
