"""
run_v2 — Run scenarios_v2 NL-capability suite and write results CSV.

Usage:
  python -m mcp_server.experiments.run_v2 --scenarios T1,T2,T8
  python -m mcp_server.experiments.run_v2 --all
  python -m mcp_server.experiments.run_v2 --all --out logs/v2_results.csv

Each scenario assumes the player has trained appropriate units (≥40 mobile
preferred, mix of apc + 3tnk). The runner inspects state and skips
scenarios whose roster requirements aren't met.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import List

from mcp_server.transport import OpenRATransport
from mcp_server.experiments.scenarios_v2 import (
    ALL_SCENARIOS, CSV_COLUMNS
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="",
                    help="comma-separated scenario ids (T1,T2,...). Default: --all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="logs/v2_results.csv",
                    help="output CSV path")
    ap.add_argument("--cooldown", type=float, default=5.0,
                    help="seconds between scenarios (let units re-idle)")
    args = ap.parse_args()

    if args.all:
        ids = list(ALL_SCENARIOS.keys())
    elif args.scenarios:
        ids = [s.strip() for s in args.scenarios.split(",")]
    else:
        ids = list(ALL_SCENARIOS.keys())

    t = OpenRATransport()
    if not t.connect():
        raise SystemExit("OpenRA bridge not connected. Is OpenRA running with MCPBridgeTrait?")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    print(f"\n=== Running {len(ids)} v2 scenarios ===\n")
    for sid in ids:
        fn = ALL_SCENARIOS.get(sid)
        if fn is None:
            print(f"[skip] unknown scenario {sid}")
            continue
        print(f"\n--- {sid} ---")
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
        results.append(r)
        verdict = "PASS" if r["tactical_intent_met"] else "FAIL"
        print(f"  {verdict}: {r['task_name']}")
        print(f"  nl: {r['nl_input']}")
        print(f"  units: {r['unit_count']} kinds={r['unit_kinds']}")
        print(f"  reached={r['reached_target']} intent_met={r['tactical_intent_met']}")
        print(f"  subtasks={r['subtasks_generated']} corrections={r['corrections']} latency={r['total_latency_ms']}ms")
        if r['failure_reason']:
            print(f"  fail: {r['failure_reason']}")
        time.sleep(args.cooldown)

    # write CSV
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


if __name__ == "__main__":
    main()
