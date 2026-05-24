"""
Tactical scenarios for the daemon-vs-squad A/B paper experiment.

Each scenario is a self-contained tactical task. The runner spawns a fixed
roster of units (via /instantbuild + manual training in sandbox mode),
issues one intent under each backend (daemon, squad), and measures:
  - reached_target  (binary, did the force arrive)
  - units_lost      (alive_before − alive_after)
  - mean_dist_delta (initial mean_dist − final mean_dist)
  - duration_s      (wallclock to verdict)
  - amplification   (atomic orders dispatched / 1 LLM call equivalent)
  - cohesion_max    (max pairwise distance during run)

A scenario does NOT specify a win condition for the whole game — only for
this slice. The OpenRA sandbox uses /instantbuild cheats so the test
focuses purely on tactical commanding, not economy.
"""

from __future__ import annotations

from typing import Optional

# Each scenario fixes the rough setup the human should arrange in sandbox
# mode before invoking the runner. The runner trusts the human to have:
#   - enabled Debug Menu / /instantbuild
#   - trained the suggested roster
#   - parked units in roughly the suggested staging area
#
# The runner queries get_state to confirm and snapshots before/after.

TACTICAL_SCENARIOS = {
    "T1_massive_push": {
        "name": "Massive push",
        "desc": "50+ unit assault toward enemy construction yard. Measures "
                "command scalability and cohesion over distance.",
        "expected_roster_min": 40,
        "suggested_roster": ["2tnk×15", "3tnk×10", "e1×20", "e3×10"],
        "intent_daemon": {
            "intent": "attack",
            "force": {"kind": "group", "name": "all"},
            "target": {"kind": "named", "name": "enemy_fact"},
            "approach": "frontal",
        },
        # Daemon path uses generic attack mission; squad path uses Assault
        # squad. The runner picks the right entrypoint based on backend.
        "intent_squad_type": "Assault",
        "verdict_max_duration_s": 90,
        "verdict_target_named": "enemy_fact",
        "verdict_arrival_radius": 10,
    },

    "T2_defend_perimeter": {
        "name": "Defend perimeter",
        "desc": "20 unit static defense around a forward base cell. Measures "
                "static command + reaction to incoming threats.",
        "expected_roster_min": 15,
        "suggested_roster": ["2tnk×8", "e1×10", "e3×4"],
        "intent_daemon": {
            "intent": "defend",
            "force": {"kind": "group", "name": "all"},
            "region": {"kind": "around", "center": "self_base", "radius": 10},
            "stance": "Defend",
        },
        "intent_squad_type": "Protection",
        "verdict_max_duration_s": 60,
        # Defense doesn't move — verdict = no units left perimeter.
        "verdict_target_named": "self_base",
        "verdict_arrival_radius": 12,
    },

    "T3_harass_economy": {
        "name": "Harass economy",
        "desc": "10 light unit raid on enemy refineries. Measures hit-and-run "
                "cycle + withdraw threshold behavior.",
        "expected_roster_min": 8,
        "suggested_roster": ["jeep×4", "e3×6"],
        "intent_daemon": {
            "intent": "harass",
            "force": {"kind": "filter", "harass_capable": True},
            "region": {"kind": "around", "center": "enemy_base", "radius": 8},
            "cycle": True,
        },
        "intent_squad_type": "Harass",
        "verdict_max_duration_s": 75,
        "verdict_target_named": "enemy_base",
        "verdict_arrival_radius": 12,
    },

    "T4_multi_front": {
        "name": "Multi-front",
        "desc": "Two attack prongs + one defend group simultaneously. Measures "
                "concurrent command capacity.",
        "expected_roster_min": 30,
        "suggested_roster": ["2tnk×10", "3tnk×8", "e1×12"],
        # Compound intent — runner sends both via batch_dispatch_intent.
        "intent_daemon_batch": [
            {"intent": "attack",
             "force": {"kind": "group", "name": "north"},
             "target": {"kind": "named", "name": "enemy_fact"},
             "approach": "flank_left"},
            {"intent": "attack",
             "force": {"kind": "group", "name": "south"},
             "target": {"kind": "named", "name": "enemy_fact"},
             "approach": "flank_right"},
            {"intent": "defend",
             "force": {"kind": "group", "name": "center"},
             "region": {"kind": "around", "center": "self_base", "radius": 10}},
        ],
        # Squad path can't replicate compound — we exercise Assault only.
        "intent_squad_type": "Assault",
        "verdict_max_duration_s": 90,
        "verdict_target_named": "enemy_fact",
        "verdict_arrival_radius": 10,
    },

    "T5_escort": {
        "name": "Escort",
        "desc": "5 unit guard + 1 MCV moving to a target cell. Measures "
                "following + auto-engagement of intercepts.",
        "expected_roster_min": 5,
        "suggested_roster": ["2tnk×3", "e1×4", "mcv×1"],
        "intent_daemon": {
            "intent": "escort",
            "force": {"kind": "filter", "unit_kind": "2tnk"},
            "escortee_id": -1,  # runner fills with first mcv from state
            "destination": {"kind": "pos", "pos": {"x": 42, "y": 46}},
        },
        "intent_squad_type": "Escort",
        "verdict_max_duration_s": 90,
        # Verdict: escortee reached destination.
        "verdict_target_pos": (42, 46),
        "verdict_arrival_radius": 8,
    },
}


def list_scenarios() -> dict:
    """Brief summary for CLI / docs."""
    return {k: v["name"] + " — " + v["desc"] for k, v in TACTICAL_SCENARIOS.items()}
