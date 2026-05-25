"""Enemy intent classifier.

Reads the current world snapshot and classifies what the enemy is doing
based on their unit + building composition. The classifier is heuristic
(no ML, no LLM) so it runs in milliseconds and the LLM can call it as a
report intent.

Outputs:
    {
        "primary": "tank_rush" | "infantry_swarm" | "air" | "turtle"
                   | "mass_artillery" | "naval" | "tech_up" | "unknown",
        "confidence": 0.0..1.0,
        "evidence": [{kind, count, weight}, ...],
        "counter_recommendation": "...",
        "stage": "opening" | "midgame" | "lategame",
    }

The LLM can call this via the `enemy_intent` report.what to decide
strategy switches: "敌方在 tank rush, 我转 turtle 防御 + e3 反坦".
"""

from __future__ import annotations

from typing import Dict, List


# --------------------------------------------------------------------------
# Weighted indicators per intent profile.
#
# Each "intent" maps actor kinds → weight. Higher weight = stronger signal
# that the enemy is playing that profile. Buildings are weaker indicators
# than mobile units (which directly show army composition).
# --------------------------------------------------------------------------

PROFILE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "tank_rush": {
        # heavy AFVs
        "4tnk": 3.0, "3tnk": 2.5, "2tnk": 2.0, "ttnk": 2.5, "1tnk": 1.0,
        "weap": 1.5,  # war factory implies tank intent
        "harv": 0.3,  # economy
    },
    "infantry_swarm": {
        "e1": 1.5, "e3": 1.2, "e1r1": 1.6, "e3r1": 1.4, "e2": 1.2,
        "e4": 1.4, "shok": 2.0, "e7": 2.5, "vlkv": 2.5,
        "tent": 1.2, "barr": 1.2, "kenn": 0.8,
    },
    "air": {
        "yak": 3.0, "mig": 3.0, "hind": 3.0, "heli": 3.0, "tran": 1.5,
        "u2": 1.0, "badr": 2.0, "afld": 2.0, "hpad": 2.0,
    },
    "turtle": {
        "pbox": 2.0, "hbox": 2.5, "gun": 2.0, "agun": 1.5, "sam": 1.5,
        "ftur": 2.0, "tsla": 2.5,
        "sbag": 0.3, "brik": 0.3, "barb": 0.3, "cycl": 0.3,
        "fix": 1.0,
        # turtling also implies stockpiling money
        "silo": 1.5, "proc": 0.8, "apwr": 0.5,
    },
    "mass_artillery": {
        "v2rl": 3.5, "arty": 3.5, "mssb": 3.0, "ca": 3.0, "dd": 2.0,
    },
    "naval": {
        "ss": 3.0, "mssb": 3.0, "dd": 2.5, "ca": 2.5, "pt": 1.5, "lst": 1.0,
        "syrd": 2.0, "spen": 2.0,
    },
    "tech_up": {
        "atek": 4.0, "stek": 4.0, "mslo": 5.0, "iron": 5.0, "pdox": 5.0,
        "dome": 1.0, "apwr": 1.0,
    },
}


# Total enemy actor cap below which we say "opening". Above 30 → midgame.
# Above 80 → lategame.
OPENING_MAX = 12
MIDGAME_MAX = 35


# Counter-recommendation per detected intent. The LLM picks up these as
# hints for strategy switches.
COUNTER_RECS: Dict[str, str] = {
    "tank_rush": "Switch to e3 (rocket soldiers) + v2rl mass; or template "
                 "tesla_wall (Soviet) / chrono_blitz (Allied). Mass anti-armor.",
    "infantry_swarm": "Build 2tnk / e4 flame trooper en masse; AoE units "
                       "(arty, v2rl) crush infantry blobs. Consider chrono_blitz "
                       "to disengage from melee.",
    "air": "Mass ftrk (mobile flak) + sam sites; build mig/yak for air "
           "superiority; protect harv and v2rl with anti-air escorts.",
    "turtle": "Mass v2rl / arty for siege range; demo trucks for hard targets; "
              "consider chrono_blitz/paratroop_rain to bypass perimeter.",
    "mass_artillery": "Aggressive flanks with fast units (jeep/1tnk); kill arty "
                       "before they unload; spread out to avoid splash.",
    "naval": "Build sub pen / naval yard; mass ss/mssb; deny harv on water.",
    "tech_up": "Disrupt before they finish — raid_harass + cut economy "
               "(target harv/proc). Buy time to match tech.",
    "unknown": "Mixed force; default balanced response and scout for more info.",
}


def classify_enemy(self_units: List[dict], enemy_units: List[dict]) -> dict:
    """Classify enemy intent from the current state.

    Args are the lists from get_state() ("self_units", "enemy_units").
    """
    # Aggregate enemy counts by kind.
    counts: Dict[str, int] = {}
    for u in enemy_units:
        k = u.get("kind", "?").lower()
        counts[k] = counts.get(k, 0) + 1

    if not counts:
        return {
            "primary": "unknown",
            "confidence": 0.0,
            "evidence": [],
            "counter_recommendation": COUNTER_RECS["unknown"],
            "stage": "opening",
            "enemy_total": 0,
        }

    # Score each profile.
    scores: Dict[str, float] = {}
    evidence: Dict[str, List[dict]] = {}
    for profile, weights in PROFILE_WEIGHTS.items():
        s = 0.0
        ev: List[dict] = []
        for kind, n in counts.items():
            w = weights.get(kind, 0.0)
            if w <= 0:
                continue
            contrib = w * n
            s += contrib
            ev.append({"kind": kind, "count": n, "weight": w,
                       "contrib": round(contrib, 2)})
        ev.sort(key=lambda e: -e["contrib"])
        scores[profile] = s
        evidence[profile] = ev[:5]  # top 5 indicators

    # Pick top profile.
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    total = sum(scores.values())

    # Confidence — top must lead the rest by ≥30% of total.
    if total <= 0 or top_score < 5.0:
        primary = "unknown"
        confidence = 0.0
    else:
        margin = top_score - second_score
        confidence = round(min(1.0, margin / max(1.0, total)), 2)
        if confidence < 0.15:
            primary = "unknown"

    # Stage classification.
    enemy_total = len(enemy_units)
    if enemy_total < OPENING_MAX:
        stage = "opening"
    elif enemy_total < MIDGAME_MAX:
        stage = "midgame"
    else:
        stage = "lategame"

    return {
        "primary": primary,
        "confidence": confidence,
        "evidence": evidence.get(primary, [])[:5],
        "all_scores": {k: round(v, 1) for k, v in ranked[:5]},
        "counter_recommendation": COUNTER_RECS[primary],
        "stage": stage,
        "enemy_total": enemy_total,
    }
