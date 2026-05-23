"""solo_human condition runner.

Player vs bot, NO LLM. Traditional UI only. This is the baseline showing what
a human can do unaided. We still want metrics — operator self-reports duration
and APM after the game (computed from OpenRA replay parse), and the script
writes a session_summary.json compatible with the other conditions.

Run:   python -m mcp_server.experiments.solo_human --scenario S1_basic_rush
"""

from __future__ import annotations
import argparse
import os
import textwrap
import time
import uuid

from . import scenarios
from ..logging import SessionLogger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=scenarios.list_ids())
    ap.add_argument("--player-id", default=None)
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    sc = scenarios.get(args.scenario)
    session_id = f"solo-{args.scenario}-{uuid.uuid4().hex[:6]}"

    # Disable LLM dispatch logging by not making any calls. SessionLogger still
    # exists but stays mostly empty — operator inputs metrics manually.
    SessionLogger.reset(
        session_id=session_id,
        condition="solo_human",
        scenario_id=args.scenario,
        player_id=args.player_id,
    )

    print(textwrap.dedent(f"""
        === solo_human condition: {sc['name']} ===
        scenario:     {args.scenario}
        map:          {sc['map']}
        seed:         {sc['seed']}
        ai:           {sc['ai_profile']} / {sc['ai_difficulty']}
        cash:         {sc['starting_cash']}
        session_id:   {session_id}
        log dir:      {SessionLogger.current().dir}

        NO LLM IN THIS RUN. Launch OpenRA the normal way, play with mouse/kb only.

        When you finish the game, return here and answer the prompts so the
        session summary is written for the paper.
        """).strip())

    print()
    result = _ask("game result (win/lose/draw)", default="draw",
                   choices=("win", "lose", "draw"))
    duration_min = float(_ask("duration in minutes (your watch)", default="20"))
    self_apm = float(_ask("rough APM (actions per minute) — your self-estimate",
                          default="60"))
    notes = _ask("notes (free text, optional)", default=args.notes)

    # Synthesize a summary matching the SessionLogger schema so analyze.py
    # can ingest all three conditions uniformly.
    summary = {
        "schema_version": SessionLogger.current().summary_path.parent.parent.name,  # placeholder
        "session_id": session_id,
        "duration_minutes": duration_min,
        "nl_commands": 0,                    # solo: no NL → bot calls
        "atomic_orders": int(self_apm * duration_min),
        "mean_amplification_ratio": 1.0,    # 1:1 — human clicks ARE atomic orders
        "apm": self_apm,
        "player_decisions_per_min": self_apm,   # in solo: every click is a decision
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "estimated_llm_cost_usd": 0.0,
        "intent_type_histogram": {},
        "strategy_template_histogram": {},
        "template_switches": 0,
        "max_concurrent_fronts_10s": 1,     # human ≈ one front at a time
        "outcome": {"result": result, "end_tick": -1, "notes": notes},
    }
    SessionLogger.current().summary_path.write_text(
        __import__("json").dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[OK] wrote {SessionLogger.current().summary_path}")


def _ask(prompt: str, default: str = "", choices=None) -> str:
    while True:
        s = input(f"{prompt} [{default}]: ").strip() or default
        if choices and s not in choices:
            print(f"  must be one of: {choices}")
            continue
        return s


if __name__ == "__main__":
    main()
