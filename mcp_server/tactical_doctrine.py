"""Combat doctrine tables for the tactical daemon.

Values are intentionally explicit (not learned) so the system stays
deterministic + debuggable. Tweak by hand from playtests.

All keys are OpenRA actor names, lowercase. Anything not listed falls
back to default scores.
"""

from __future__ import annotations

from typing import Dict


# ---------------------------------------------------------------------------
# Target priority — how valuable is it to destroy this enemy actor?
# Higher = pick first. Range 0-100.
#
# Ordering (player intuition, RTS standard playbook):
#   1. Mobile units first (they shoot back, run, kite). Within units:
#      - Glass-cannon high-DPS (v2rl, arty, dtrk) at the very top — kill
#        before they alpha-strike our army.
#      - Anti-armor infantry (e3) next — they melt tanks if left alone.
#      - Harvesters (always relevant, choke enemy economy).
#      - Heavy AFVs.
#      - Light vehicles / scouts.
#      - Basic infantry / cheap defenders.
#   2. Buildings after units. Within buildings:
#      - Power (powr/apwr) — chain effect: kills radar, defenses, super-
#        weapons. Always first.
#      - Defenses (tsla/ftur/sam/pbox/gun/agun) — clear the way.
#      - Production (weap/barr/tent/afld/syrd/spen/hpad/fix) — stop bleed.
#      - Tech / economy structures (proc/dome/stek/atek).
#      - Win-condition (fact + superweapons) last among non-game-ending
#        choices, but still kept high because killing fact ends games.
# ---------------------------------------------------------------------------
TARGET_PRIORITY: Dict[str, int] = {
    # --- units: glass-cannon high-DPS (kill on sight) ---
    "v2rl": 100,
    "arty": 100,
    "dtrk": 100,
    "ttnk": 95,
    "mssb": 90,

    # --- units: anti-armor / hero infantry ---
    "e3":   90,
    "e3r1": 92,
    "e7":   90,   # tanya
    "vlkv": 90,   # volkov
    "shok": 88,   # shock trooper

    # --- units: harvesters (econ choke, always) ---
    "harv": 85,
    "mcv":  85,

    # --- units: heavy AFVs ---
    "4tnk": 80,
    "3tnk": 75,
    "2tnk": 70,
    "1tnk": 65,

    # --- units: light vehicles ---
    "ftrk": 70,   # flak truck, anti-air, kill before air dies
    "apc":  60,
    "jeep": 55,
    "mrj":  60,
    "mgg":  60,
    "mnly": 50,

    # --- units: aircraft (high mobility, hard to catch) ---
    "yak":  88,
    "mig":  88,
    "hind": 85,
    "heli": 85,
    "tran": 80,
    "badr": 85,
    "u2":   50,

    # --- units: basic infantry ---
    "e4":   55,   # flame trooper
    "e2":   45,
    "e1":   40,
    "e1r1": 43,
    "medi": 45,
    "spy":  70,
    "thf":  65,
    "dog":  35,

    # --- units: naval ---
    "ca":   85,   # cruiser, siege from sea
    "dd":   70,
    "ss":   75,
    "pt":   55,

    # --- buildings: power (chain effect — top of buildings) ---
    "powr": 75,
    "apwr": 78,
    "fpwr": 75,

    # --- buildings: defense ---
    "tsla": 70,
    "ftur": 65,
    "sam":  60,
    "agun": 60,
    "gun":  58,
    "pbox": 55,
    "hbox": 58,

    # --- buildings: production ---
    "weap": 60,
    "afld": 58,
    "syrd": 55,
    "spen": 55,
    "barr": 50,
    "tent": 50,
    "hpad": 50,
    "fix":  48,
    "kenn": 35,

    # --- buildings: tech / radar / economy ---
    "dome": 55,   # radar — blinding enemy
    "stek": 55,
    "atek": 55,
    "proc": 55,
    "silo": 30,
    "gap":  50,

    # --- buildings: win-condition / superweapon ---
    "fact": 60,   # kill = game end; still picked first by destroy_fact mission
    "mslo": 75,   # nuclear silo
    "iron": 70,   # iron curtain
    "pdox": 70,   # chronosphere

    # --- passive / walls ---
    "oilb": 25,
    "sbag": 5,
    "brik": 5,
    "barb": 5,
    "cycl": 5,
    "fenc": 5,
}

DEFAULT_PRIORITY = 30


# ---------------------------------------------------------------------------
# Counter matrix — how effective is OUR unit kind vs ENEMY kind.
#
# Lookup: COUNTER[our_kind][enemy_kind] = multiplier (1.0 = neutral).
# Used to weight target choice — we'd rather send rocket troopers at
# tanks than at infantry.
# ---------------------------------------------------------------------------
COUNTER: Dict[str, Dict[str, float]] = {
    # rocket soldier — anti-armor specialist
    "e3":  {"4tnk": 1.8, "3tnk": 1.7, "2tnk": 1.6, "1tnk": 1.4,
            "ttnk": 1.5, "jeep": 1.3, "apc": 1.4, "e1": 0.6, "e3": 0.5,
            "fact": 0.4, "weap": 0.4, "harv": 1.5, "yak": 1.6, "mig": 1.6,
            "hind": 1.6, "heli": 1.6, "tran": 1.4, "badr": 1.2},
    "e3r1": {"4tnk": 2.0, "3tnk": 1.9, "2tnk": 1.8, "ttnk": 1.7,
             "yak": 1.8, "mig": 1.8, "hind": 1.8, "heli": 1.8},

    # rifleman — generic, soft target killer
    "e1":  {"e1": 1.0, "e3": 1.2, "e2": 1.0, "spy": 1.5, "thf": 1.5,
            "2tnk": 0.4, "3tnk": 0.3, "4tnk": 0.2, "pbox": 0.5},
    "e1r1": {"e1": 1.2, "e3": 1.3},

    # flame trooper — area / soft / building
    "e4":  {"e1": 1.8, "e3": 1.5, "fact": 1.4, "barr": 1.4, "tent": 1.4,
            "pbox": 1.2, "2tnk": 0.4, "4tnk": 0.2},

    # tanya (allied hero) — anti-infantry + demo
    "e7":  {"e1": 2.5, "e3": 2.0, "fact": 1.8, "barr": 1.8, "proc": 1.8,
            "4tnk": 0.5},

    # light tank
    "1tnk": {"e1": 1.5, "e3": 0.8, "jeep": 1.3, "2tnk": 0.7, "3tnk": 0.5,
             "harv": 1.4, "pbox": 1.0},
    # medium tank — workhorse
    "2tnk": {"e1": 1.5, "e3": 1.0, "1tnk": 1.4, "2tnk": 1.0, "3tnk": 0.7,
             "4tnk": 0.5, "harv": 1.6, "pbox": 1.2},
    # heavy tank (soviet)
    "3tnk": {"e1": 1.7, "e3": 1.2, "1tnk": 1.6, "2tnk": 1.4, "3tnk": 1.0,
             "4tnk": 0.7, "ttnk": 1.0, "pbox": 1.5, "harv": 1.7},
    # mammoth (soviet apex)
    "4tnk": {"e1": 1.9, "e3": 1.4, "1tnk": 1.8, "2tnk": 1.6, "3tnk": 1.3,
             "4tnk": 1.0, "ttnk": 1.2, "pbox": 1.8, "fact": 1.4,
             "weap": 1.4, "harv": 1.9, "yak": 1.2, "hind": 1.2},
    # tesla tank
    "ttnk": {"e1": 1.6, "e3": 1.3, "2tnk": 1.5, "3tnk": 1.3, "4tnk": 1.1,
             "pbox": 1.6, "fact": 1.3},

    # V2 rocket — siege artillery
    "v2rl": {"fact": 2.0, "weap": 1.8, "barr": 1.7, "tent": 1.7,
             "proc": 1.8, "pbox": 2.5, "tsla": 2.0, "sam": 2.0,
             "gun": 2.0, "atek": 1.8, "stek": 1.8, "mslo": 1.8,
             "e1": 0.8, "e3": 0.8, "harv": 1.4, "4tnk": 0.7},
    # artillery (similar)
    "arty": {"fact": 1.8, "weap": 1.6, "barr": 1.5, "proc": 1.6,
             "pbox": 2.2, "e1": 1.0, "e3": 1.0, "harv": 1.3},

    # demolition truck — one-shot
    "dtrk": {"fact": 5.0, "weap": 4.0, "proc": 4.0, "stek": 4.0,
             "atek": 4.0, "tsla": 3.0, "4tnk": 2.0},

    # APC — transport + light
    "apc":  {"e1": 1.3, "e3": 1.0, "harv": 1.2},

    # jeep / scout
    "jeep": {"e1": 1.4, "e3": 1.1, "harv": 1.3, "spy": 1.5},

    # flak truck (anti-air)
    "ftrk": {"yak": 2.5, "mig": 2.5, "hind": 2.5, "heli": 2.5,
             "tran": 2.0, "badr": 2.0, "u2": 1.5, "e1": 0.8},

    # mobile gap / radar jammer — non-combat
    "mgg":  {},
    "mrj":  {},

    # aircraft
    "yak":  {"harv": 2.0, "v2rl": 1.6, "arty": 1.6, "e1": 1.4,
             "2tnk": 1.2, "tsla": 1.4, "pbox": 1.3, "fact": 1.3,
             "powr": 1.5, "proc": 1.5, "4tnk": 0.9},
    "mig":  {"harv": 2.0, "fact": 1.4, "weap": 1.4, "v2rl": 1.7,
             "e1": 1.4, "tsla": 1.5},
    "hind": {"e1": 1.6, "2tnk": 1.4, "harv": 1.7, "v2rl": 1.4,
             "tsla": 1.4},
    "heli": {"e1": 1.6, "2tnk": 1.4, "harv": 1.7},

    # naval
    "dd":   {"ss": 1.8, "mssb": 1.8, "pt": 1.4},
    "ca":   {"fact": 1.8, "weap": 1.7, "proc": 1.7, "barr": 1.6},
    "ss":   {"dd": 1.6, "ca": 1.6, "lst": 1.7, "harv": 1.7},
}


def counter_score(our_kind: str, enemy_kind: str) -> float:
    """Return multiplier for our_kind vs enemy_kind. Defaults to 1.0."""
    if not our_kind or not enemy_kind:
        return 1.0
    row = COUNTER.get(our_kind.lower())
    if row is None:
        return 1.0
    return row.get(enemy_kind.lower(), 1.0)


def target_priority(enemy_kind: str) -> int:
    """Base priority for destroying this enemy kind."""
    if not enemy_kind:
        return DEFAULT_PRIORITY
    return TARGET_PRIORITY.get(enemy_kind.lower(), DEFAULT_PRIORITY)


# ---------------------------------------------------------------------------
# Range tiers — used by formation rules. Long-range units should stay
# behind, short-range should lead.
# ---------------------------------------------------------------------------
RANGE_TIER: Dict[str, str] = {}

for k in ("v2rl", "arty", "mssb", "ca", "dd", "msam", "yak", "mig"):
    RANGE_TIER[k] = "long"
for k in ("e3", "e3r1", "shok", "ftrk"):
    RANGE_TIER[k] = "mid"
# everything else defaults to "short"


def range_tier(kind: str) -> str:
    return RANGE_TIER.get((kind or "").lower(), "short")


# ---------------------------------------------------------------------------
# Retreat threshold + cooldown
# ---------------------------------------------------------------------------

# HP fraction below which a unit retreats to base. 0.3 = 30% HP left.
RETREAT_HP_THRESHOLD = 0.3

# How long after a unit successfully retreats before it can re-engage.
# Prevents oscillation between retreat (HP just under 0.3) and re-engage
# (HP healed a bit by ServiceDepot).
RETREAT_COOLDOWN_S = 12.0

# HP fraction to which a retreating unit must heal before re-engaging.
REENGAGE_HP_THRESHOLD = 0.7
