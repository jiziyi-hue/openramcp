"""bot_baseline condition runner.

Headless OpenRA bot vs bot. No human, no LLM. Provides the floor against which
our human_llm condition is compared.

Strategy: launch OpenRA dedicated server (or game with --Game.SkipMenu), let two
AIs fight, parse the resulting replay for metrics. The replay path is captured
and summary written to logs/<session_id>/.

NOTE: This is a SKELETON. Headless OpenRA needs `OpenRA.Server.exe` or the
`--Game.SkipMenu` flag plus replay export. For P1.7 we land the scaffolding
and add the actual launch wiring once tested against the live build.

Run:   python -m mcp_server.experiments.bot_baseline --scenario S1_basic_rush --seeds 20
"""

from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import scenarios
from ..logging import SessionLogger, LOG_ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=scenarios.list_ids())
    ap.add_argument("--seeds", type=int, default=5,
                    help="how many independent runs (different RNG seeds)")
    ap.add_argument("--ai-a", default="rush",
                    help="left-side AI profile (rush/normal/turtle/naval)")
    ap.add_argument("--ai-b", default="normal",
                    help="right-side AI profile")
    ap.add_argument("--time-limit-min", type=int, default=None,
                    help="override scenario time limit")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't launch, just print what would happen")
    args = ap.parse_args()

    sc = scenarios.get(args.scenario)
    time_limit = args.time_limit_min or sc["time_limit_min"]

    print(f"=== bot_baseline: {sc['name']} ===")
    print(f"  scenario: {args.scenario}")
    print(f"  AI A:     {args.ai_a}")
    print(f"  AI B:     {args.ai_b}")
    print(f"  seeds:    {args.seeds}")
    print(f"  time limit (min): {time_limit}")

    if args.dry_run:
        print("[dry-run] not launching")
        return

    summaries = []
    for i in range(args.seeds):
        seed = sc["seed"] + i
        session_id = f"botbase-{args.scenario}-{seed}-{uuid.uuid4().hex[:4]}"
        print(f"\n[run {i + 1}/{args.seeds}] seed={seed} session={session_id}")

        SessionLogger.reset(
            session_id=session_id,
            condition="bot_baseline",
            scenario_id=args.scenario,
        )

        # Headless launch is engine-version dependent. Placeholder: we record
        # the intent + scenario into the meta file and leave the actual launch
        # to a later patch. The session_summary.json gets stub-filled so the
        # analyze.py pipeline doesn't break.
        summary = {
            "session_id": session_id,
            "duration_minutes": time_limit,
            "nl_commands": 0,
            "atomic_orders": 0,                # filled by replay parser
            "mean_amplification_ratio": 0.0,
            "apm": 0.0,
            "player_decisions_per_min": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "estimated_llm_cost_usd": 0.0,
            "intent_type_histogram": {},
            "strategy_template_histogram": {},
            "template_switches": 0,
            "max_concurrent_fronts_10s": 0,
            "outcome": {
                "result": "unknown",
                "end_tick": -1,
                "notes": f"seed={seed} ai_a={args.ai_a} ai_b={args.ai_b}",
            },
            "harness_note": "BOT_BASELINE SKELETON — headless launch not yet wired.",
        }
        SessionLogger.current().summary_path.write_text(
            json.dumps(summary, indent=2), encoding="utf-8")
        summaries.append(summary)
        print(f"  -> wrote {SessionLogger.current().summary_path}")

    print(f"\n[OK] {len(summaries)} runs queued in {LOG_ROOT}/")
    print("Next step: wire actual headless launch in this script "
          "(OpenRA.exe --Game.SkipMenu --Engine.LobbySeed=...).")


if __name__ == "__main__":
    main()
