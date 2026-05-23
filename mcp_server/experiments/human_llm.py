"""human_llm condition runner.

Player + LLM (Claude Code over MCP) vs an OpenRA bot. Our system. Automatic
decision-log capture happens whenever Claude Code calls dispatch_intent —
no extra instrumentation needed at this layer.

This script just:
  1. Sets env vars so SessionLogger tags the run with scenario_id + condition
  2. Prints instructions for the operator to start Claude Code + OpenRA
  3. Optionally launches scout_daemon in background
  4. Waits for the operator to mark game-over (Ctrl+C ends the run + writes summary)

Run:   python -m mcp_server.experiments.human_llm --scenario S1_basic_rush
"""

from __future__ import annotations
import argparse
import os
import sys
import textwrap
import time
import uuid

from . import scenarios
from ..logging import SessionLogger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=scenarios.list_ids())
    ap.add_argument("--player-id", default=None,
                    help="anonymous player tag for the session (opt-in demographic).")
    ap.add_argument("--notes", default="",
                    help="free-text notes attached to session_summary.json")
    args = ap.parse_args()

    sc = scenarios.get(args.scenario)
    session_id = f"hum-llm-{args.scenario}-{uuid.uuid4().hex[:6]}"
    os.environ["OPENRA_LOG_SESSION_ID"] = session_id

    # Reset SessionLogger so its tagged correctly.
    SessionLogger.reset(
        session_id=session_id,
        condition="human_llm",
        scenario_id=args.scenario,
        player_id=args.player_id,
    )

    print(textwrap.dedent(f"""
        === human_llm condition: {sc['name']} ===
        scenario:     {args.scenario}
        map:          {sc['map']}
        seed:         {sc['seed']}
        ai:           {sc['ai_profile']} / {sc['ai_difficulty']}
        cash:         {sc['starting_cash']}
        time limit:   {sc['time_limit_min']} min
        session_id:   {session_id}
        log dir:      {SessionLogger.current().dir}

        Now do:
          1. Launch OpenRA (`scripts\\launch.bat` or your usual flow)
          2. Skirmish → pick map "{sc['map']}" → set AI to "{sc['ai_profile']} bot {sc['ai_difficulty']}"
          3. Start the game.
          4. In Claude Code, chat naturally — every dispatch_intent will be logged.
          5. When the game ends, tell Claude "GG win" / "lose" / "draw".
             Claude will call end_session() to write summary.

        This script idles. Press Ctrl+C to abort (a summary will be written).
        """).strip())

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[abort] writing summary…")
        summary = SessionLogger.current().finalize({
            "result": "aborted",
            "end_tick": -1,
            "notes": args.notes,
        })
        print(f"  duration_min:           {summary.get('duration_minutes')}")
        print(f"  nl_commands:            {summary.get('nl_commands')}")
        print(f"  atomic_orders:          {summary.get('atomic_orders')}")
        print(f"  mean_amplification:     {summary.get('mean_amplification_ratio'):.2f}")
        print(f"  template_switches:      {summary.get('template_switches')}")
        print(f"  estimated_llm_cost_usd: {summary.get('estimated_llm_cost_usd')}")
        print(f"  summary written to:     {SessionLogger.current().summary_path}")


if __name__ == "__main__":
    main()
