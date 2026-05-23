"""Fixed scenarios for reproducible experiments.

Each scenario is a frozen dict — same seed + map + starting cash + AI level
across runs. The bot_baseline harness uses this seed; the human conditions
match it as far as OpenRA skirmish-setup allows.
"""

from __future__ import annotations

SCENARIOS = {
    "S1_basic_rush": {
        "name": "Basic rush",
        "desc": "Symmetric map, normal AI opponent on rush profile.",
        "map": "shadowfiend-ii",
        "seed": 12345,
        "starting_cash": 5000,
        "ai_difficulty": "Normal",
        "ai_profile": "rush",
        "ai_faction": "soviet",
        "player_faction": "any",
        "time_limit_min": 20,
    },
    "S2_economic_pressure": {
        "name": "Economic pressure",
        "desc": "Low starting cash forces econ play first.",
        "map": "shadowfiend-ii",
        "seed": 22222,
        "starting_cash": 2000,
        "ai_difficulty": "Normal",
        "ai_profile": "normal",
        "ai_faction": "soviet",
        "player_faction": "any",
        "time_limit_min": 30,
    },
    "S3_pincer_test": {
        "name": "Pincer test",
        "desc": "Wide map with two corridors — pincer/feint shines.",
        "map": "shadowfiend-ii",
        "seed": 33333,
        "starting_cash": 5000,
        "ai_difficulty": "Hard",
        "ai_profile": "normal",
        "ai_faction": "allies",
        "player_faction": "any",
        "time_limit_min": 25,
    },
    "S4_turtle_break": {
        "name": "Turtle break",
        "desc": "AI plays turtle profile — needs siege strategy.",
        "map": "shadowfiend-ii",
        "seed": 44444,
        "starting_cash": 5000,
        "ai_difficulty": "Hard",
        "ai_profile": "turtle",
        "ai_faction": "soviet",
        "player_faction": "any",
        "time_limit_min": 35,
    },
    "S5_long_game": {
        "name": "Long game",
        "desc": "Lots of starting cash + bigger map — late-game tech wars.",
        "map": "shadowfiend-ii",
        "seed": 55555,
        "starting_cash": 10000,
        "ai_difficulty": "Hard",
        "ai_profile": "normal",
        "ai_faction": "allies",
        "player_faction": "any",
        "time_limit_min": 45,
    },
}


def get(scenario_id: str) -> dict:
    if scenario_id not in SCENARIOS:
        raise KeyError(f"unknown scenario {scenario_id!r}. valid: {list(SCENARIOS)}")
    return SCENARIOS[scenario_id]


def list_ids() -> list[str]:
    return list(SCENARIOS.keys())
