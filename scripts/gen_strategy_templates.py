"""
Generate 5 strategy-template bot-module yaml blocks for the openra_mcp project.

Each template gets its own gated trio:
    BaseBuilderBotModule@<template>    — what to build (fractions/limits)
    UnitBuilderBotModule@<template>    — what to train (ratios/limits)
    SquadManagerBotModule@<template>   — squad sizing + targeting policy

All three are gated by `RequiresCondition: enable-strategy-<template>`, which
StrategyControllerBotModule grants/revokes on the human player's PlayerActor.

The yaml is written to `OpenRA/mods/ra/rules/strategy_templates.yaml`. The mod
manifest must include this file (the script also patches mod.yaml if needed).

Templates:
    tank_rush       — heavy vehicles, small infantry tail, frontal pressure
    infantry_swarm  — e1/e3/e4 mass, multi-barracks, cheap waves
    balanced        — mirrors normal-ai, all-round
    turtle          — heavy static defense + arty/v2 + long power margin
    raid_harass     — light/fast jeep+ftrk+apc, small squads, multi-prong

Run:   python scripts/gen_strategy_templates.py
       (or with --print to stdout instead of writing the file)
"""

from __future__ import annotations
import argparse
import sys
import textwrap
from pathlib import Path

TEMPLATES = [
    # P1 — core 5
    "tank_rush", "infantry_swarm", "balanced", "turtle", "raid_harass",
    # P3 — 4 旗舰 (faction-flavoured)
    "tesla_wall",       # Soviet — tesla coil + tesla tank, ultra defense
    "chrono_blitz",     # Allied — chronosphere + heavy tanks
    "siege_arty",       # Either — v2 / arty mass long-range
    "paratroop_rain",   # Either — c17/badr paradrops + heli
]

# Building fractions — relative weights. Higher = more often built when
# conditions allow. Empty values default to base config behavior.
BASE_BUILDER = {
    # =================================================================
    # NOTE on BuildingFractions semantics (after deep-read of OpenRA
    # BaseBuilderQueueManager.cs:332-390):
    #
    #   BuildingFractions[X] = TARGET % of total buildings that should be X.
    #
    # Algorithm:  if (count[X] * 100 > frac[X] * total_buildings)  → SKIP
    #
    # So weights ARE percentages, not relative weights. With a ~20-building
    # mid-game base:
    #   - 5%   ≈ 1 of that type
    #   - 10%  ≈ 2 of that type
    #   - 25%  ≈ 5 of that type
    # Combined with BuildingLimits (hard caps) the bot stops once either is
    # reached. Tune so percentages reflect what you actually want in the
    # final base composition.
    # =================================================================
    "tank_rush": {
        # Heavy armor push: lots of weap (3 max), low infantry buildings,
        # moderate defense. Power surplus generous (tanks expensive, fix late).
        "MinimumExcessPower": 60,
        "MaximumExcessPower": 200,
        "ExcessPowerIncrement": 40,
        "BuildingFractions": {
            "proc": 22,     # ~4-5 procs out of ~20 buildings
            "weap": 12,     # ~2-3 war factories (heavy armor needs throughput)
            "tent": 3, "barr": 3, "kenn": 3,    # 1 of each at most
            "fix": 4,                            # ~1 service depot
            "dome": 4,                           # radar
            "pbox": 5, "gun": 6, "ftur": 5,     # moderate defense
            "tsla": 4, "agun": 3, "sam": 2,
            "atek": 2, "stek": 2,
        },
        "BuildingLimits": {
            "proc": 4, "barr": 1, "tent": 1, "dome": 1, "weap": 2,
            "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"dome": 1500, "fix": 3000},
    },
    "infantry_swarm": {
        # Mass cheap infantry: multiple barracks (high tent/barr/kenn %),
        # weap dropped to single late, defense ample to cover slow inf.
        "MinimumExcessPower": 50,
        "MaximumExcessPower": 180,
        "ExcessPowerIncrement": 30,
        "BuildingFractions": {
            "proc": 22,
            "tent": 10, "barr": 10, "kenn": 6,  # MANY barracks (2 each + kenn)
            "weap": 4,                            # 1 weap, late
            "fix": 3,
            "dome": 4,
            "pbox": 8, "gun": 5, "ftur": 8, "tsla": 4,    # heavy static def
            "agun": 3, "sam": 2,
            "atek": 2, "stek": 2,
        },
        "BuildingLimits": {
            "proc": 4, "barr": 2, "tent": 2, "kenn": 1, "dome": 1,
            "weap": 1, "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"weap": 6000, "dome": 2000},
    },
    "balanced": {
        # Mirrors OpenRA stock normal-ai. Proven values from original yaml.
        # Keep close to original; only minor cleanup.
        "MinimumExcessPower": 60,
        "MaximumExcessPower": 200,
        "ExcessPowerIncrement": 40,
        "BuildingFractions": {
            "proc": 15, "tent": 3, "barr": 3, "kenn": 2, "dome": 3,
            "weap": 8, "hpad": 4, "afld": 4, "afld.ukraine": 4,
            "fix": 3,
            "pbox": 6, "gun": 6, "ftur": 8, "tsla": 4, "gap": 2,
            "agun": 4, "sam": 2,
            "atek": 2, "stek": 2, "mslo": 2,
        },
        "BuildingLimits": {
            "proc": 4, "barr": 1, "tent": 1, "dome": 1, "weap": 1,
            "hpad": 4, "afld": 4, "afld.ukraine": 4,
            "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"dome": 3000},
    },
    "turtle": {
        # Heavy static defense %. Lots of pbox/gun/ftur/tsla — accounts for
        # ~30% of base. Lower production / tech %, slower offensive tempo.
        "MinimumExcessPower": 60,
        "MaximumExcessPower": 220,
        "ExcessPowerIncrement": 50,
        "BuildingFractions": {
            "proc": 18, "tent": 3, "barr": 3, "kenn": 2,
            "weap": 5,
            "hpad": 2, "afld": 2, "afld.ukraine": 2,
            "fix": 3,
            "pbox": 12, "gun": 12, "ftur": 10, "tsla": 8, "gap": 3,
            "dome": 4, "agun": 6, "sam": 4,
            "atek": 2, "stek": 2, "mslo": 2,
        },
        "BuildingLimits": {
            "proc": 4, "barr": 1, "tent": 1, "kenn": 1, "dome": 1,
            "weap": 1, "hpad": 4, "afld": 4, "afld.ukraine": 4,
            "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"dome": 3000},
    },
    "raid_harass": {
        # Light fast raids: weap up FAST for jeep/ftrk, kenn for dogs, minimal
        # defense (we attack, don't sit). Cheap, low power footprint.
        "MinimumExcessPower": 50,
        "MaximumExcessPower": 170,
        "ExcessPowerIncrement": 30,
        "BuildingFractions": {
            "proc": 22,
            "weap": 10,             # high — jeep/ftrk/apc throughput
            "tent": 4, "barr": 4, "kenn": 5,
            "fix": 4,               # repairs are crucial for raid units
            "dome": 4,
            "pbox": 4, "gun": 4, "ftur": 3, "tsla": 2,   # minimal static def
            "agun": 2, "sam": 1,
            "atek": 2, "stek": 2,
        },
        "BuildingLimits": {
            "proc": 4, "barr": 1, "tent": 1, "kenn": 1, "dome": 1,
            "weap": 1, "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"dome": 2500},
    },
    # =================== P3 旗舰模板 ===================
    "tesla_wall": {
        # Soviet — tesla coils dominate. ~25% of base is tsla. High power.
        "MinimumExcessPower": 100,
        "MaximumExcessPower": 240,
        "ExcessPowerIncrement": 50,
        "BuildingFractions": {
            "proc": 18,
            "tent": 3, "kenn": 3,
            "weap": 6, "fix": 3,
            "dome": 4,
            "pbox": 8, "gun": 8, "ftur": 8,
            "tsla": 25,            # DOMINANT — fully ~25% of base
            "agun": 5, "sam": 4,
            "stek": 2, "iron": 2, "mslo": 2,
        },
        "BuildingLimits": {
            "proc": 4, "tent": 1, "kenn": 1, "dome": 1,
            "weap": 1, "stek": 1, "fix": 1, "iron": 1, "mslo": 1,
            "powr": 4, "apwr": 2,                                # cap power buildings
        },
        "BuildingDelays": {"iron": 6000, "mslo": 9000, "dome": 2000},
    },
    "chrono_blitz": {
        # Allied — heavy tanks + Chronosphere. weap up high, tech path
        # (atek + pdox) prioritized.
        "MinimumExcessPower": 80,
        "MaximumExcessPower": 220,
        "ExcessPowerIncrement": 50,
        "BuildingFractions": {
            "proc": 18,
            "barr": 4, "dome": 4,
            "weap": 12, "fix": 5,              # heavy weap + fix for repairs
            "pbox": 6, "gun": 5, "ftur": 4,
            "agun": 5, "sam": 3,
            "atek": 4, "pdox": 4, "gap": 3,    # tech + chronosphere
        },
        "BuildingLimits": {
            "proc": 4, "barr": 1, "dome": 1,
            "weap": 2, "fix": 1, "atek": 1, "pdox": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"pdox": 7000, "atek": 4500, "dome": 1800},
    },
    "siege_arty": {
        # Mass artillery: weap dominant, decent defense behind for safety.
        "MinimumExcessPower": 60,
        "MaximumExcessPower": 200,
        "ExcessPowerIncrement": 40,
        "BuildingFractions": {
            "proc": 20,
            "tent": 3, "barr": 3, "dome": 4,
            "weap": 14,         # HIGH — arty / v2 throughput
            "fix": 4,
            "pbox": 6, "gun": 6, "ftur": 5, "tsla": 4,
            "agun": 5, "sam": 3,
            "atek": 2, "stek": 2,
        },
        "BuildingLimits": {
            "proc": 4, "barr": 1, "tent": 1, "dome": 1,
            "weap": 2, "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"dome": 2200},
    },
    "paratroop_rain": {
        # Air-heavy: hpad/afld dominate, weap minimal, heavy anti-air defense.
        "MinimumExcessPower": 80,
        "MaximumExcessPower": 220,
        "ExcessPowerIncrement": 50,
        "BuildingFractions": {
            "proc": 18,
            "tent": 3, "barr": 3, "kenn": 2, "dome": 4,
            "weap": 4, "fix": 3,
            "hpad": 18, "afld": 12, "afld.ukraine": 12,  # AIR DOMINANT
            "pbox": 4, "gun": 4,
            "agun": 12, "sam": 10,                        # heavy anti-air
            "atek": 2, "stek": 2,
        },
        "BuildingLimits": {
            "proc": 4, "tent": 1, "barr": 1, "kenn": 1, "dome": 1,
            "weap": 1, "hpad": 4, "afld": 4, "afld.ukraine": 4,
            "atek": 1, "stek": 1, "fix": 1,
            "powr": 4, "apwr": 2,
        },
        "BuildingDelays": {"dome": 1500},
    },
}

# UnitBuilder — what to train. Numbers are relative weights.
UNIT_BUILDER = {
    "tank_rush": {
        "e1": 15, "e3": 25, "harv": 10,
        "jeep": 15, "ftrk": 10,
        "1tnk": 50, "2tnk": 60, "3tnk": 60, "4tnk": 25,
        "ttnk": 25, "v2rl": 20, "arty": 15,
    },
    "infantry_swarm": {
        "e1": 100, "e2": 30, "e3": 60, "e4": 40, "dog": 25,
        "shok": 30, "harv": 10,
        "apc": 15, "1tnk": 10,
    },
    "balanced": {
        "e1": 65, "e2": 15, "e3": 30, "e4": 15, "dog": 15, "shok": 15,
        "harv": 15, "apc": 30, "jeep": 20, "arty": 15, "v2rl": 40,
        "ftrk": 30, "1tnk": 40, "2tnk": 50, "3tnk": 50, "4tnk": 25,
        "ttnk": 25, "heli": 30, "mh60": 30, "mig": 30, "yak": 30,
    },
    "turtle": {
        "e1": 80, "e3": 60, "e4": 30, "shok": 20,
        "harv": 15, "ftrk": 50, "v2rl": 50, "arty": 50,
        "1tnk": 30, "2tnk": 40, "3tnk": 40, "ttnk": 30, "mnly": 2,
    },
    "raid_harass": {
        "e1": 20, "e3": 25, "dog": 25, "harv": 10,
        "jeep": 60, "ftrk": 50, "apc": 50, "1tnk": 25,
        "heli": 20, "mh60": 20,
    },
    # P3
    "tesla_wall": {
        # Stays home and fries everything that walks in. Tesla tanks are roaming siege.
        "e1": 25, "e3": 50, "shok": 30,           # tesla trooper (shok)
        "harv": 10, "ttnk": 80, "v2rl": 20,
    },
    "chrono_blitz": {
        "e1": 30, "e3": 30, "harv": 10,
        "1tnk": 50, "2tnk": 60, "3tnk": 50, "ttnk": 30,
        "mech": 20, "medi": 10,                   # mechanic + medic to support teleport groups
    },
    "siege_arty": {
        "e1": 20, "e3": 60, "harv": 15,
        "arty": 80, "v2rl": 80, "ftrk": 30, "2tnk": 30,
    },
    "paratroop_rain": {
        "e1": 25, "e3": 20, "harv": 10,
        "heli": 60, "mh60": 50, "hind": 60,
        "mig": 30, "yak": 30,
        "jeep": 20, "apc": 20,
    },
}

UNIT_LIMITS = {
    # Limits prevent infinite-spam of cheap units (esp. infantry) when more
    # expensive structures are not yet up. Without these the bot drains cash
    # on e1/e3 and never accumulates enough for weap/hpad/etc.
    "tank_rush":      {"harv": 8, "e1": 6,  "e3": 6,  "jeep": 3, "ftrk": 3},
    "infantry_swarm": {"harv": 8, "e1": 18, "e3": 12, "e4": 6,  "dog": 6},
    "balanced":       {"harv": 8, "e1": 10, "e3": 8,  "dog": 4, "jeep": 4, "ftrk": 4},
    "turtle":         {"harv": 8, "e1": 10, "e3": 12, "dog": 4, "jeep": 4, "ftrk": 6, "mnly": 2},
    "raid_harass":    {"harv": 6, "e1": 4,  "e3": 4,  "dog": 6, "jeep": 8, "ftrk": 6, "apc": 6, "heli": 4},
    # P3
    "tesla_wall":     {"harv": 8, "e3": 12, "shok": 6, "ttnk": 6, "v2rl": 3},
    "chrono_blitz":   {"harv": 8, "e1": 6,  "e3": 6,  "1tnk": 4, "2tnk": 8, "3tnk": 4, "ttnk": 3, "mech": 3, "medi": 2},
    "siege_arty":     {"harv": 8, "e3": 8,  "arty": 8, "v2rl": 8, "ftrk": 4, "2tnk": 3},
    "paratroop_rain": {"harv": 6, "heli": 6, "mh60": 6, "hind": 6, "mig": 4, "yak": 4, "jeep": 3, "apc": 3},
}

# Squad sizes — how many units gather before attacking
SQUAD_SIZE = {
    "tank_rush": 12,
    "infantry_swarm": 20,
    "balanced": 16,
    "turtle": 10,
    "raid_harass": 5,           # small, fast multi-prong
    # P3
    "tesla_wall": 8,            # rarely leaves base
    "chrono_blitz": 10,         # teleport strike groups
    "siege_arty": 14,           # arty needs front-line escorts
    "paratroop_rain": 6,        # air strike packs
}


def emit_indent(lines: list[str], indent: int, text: str):
    """Append a line with tab indentation."""
    lines.append(("\t" * indent) + text)


def emit_dict(lines: list[str], indent: int, key: str, mapping: dict):
    emit_indent(lines, indent, key + ":")
    for k, v in mapping.items():
        emit_indent(lines, indent + 1, f"{k}: {v}")


def emit_template(tmpl: str) -> list[str]:
    """Generate yaml lines for one template (BaseBuilder + UnitBuilder + SquadManager)."""
    cond = f"enable-strategy-{tmpl}"
    L: list[str] = []
    L.append(f"# ----- {tmpl} -----")

    # BaseBuilderBotModule@<tmpl>
    bb = BASE_BUILDER[tmpl]
    emit_indent(L, 1, f"BaseBuilderBotModule@{tmpl}:")
    emit_indent(L, 2, f"RequiresCondition: {cond}")
    emit_indent(L, 2, f"MinimumExcessPower: {bb['MinimumExcessPower']}")
    emit_indent(L, 2, f"MaximumExcessPower: {bb['MaximumExcessPower']}")
    emit_indent(L, 2, f"ExcessPowerIncrement: {bb['ExcessPowerIncrement']}")
    emit_indent(L, 2, "ExcessPowerIncreaseThreshold: 4")
    emit_indent(L, 2, "ConstructionYardTypes: fact")
    emit_indent(L, 2, "RefineryTypes: proc")
    emit_indent(L, 2, "PowerTypes: powr,apwr")
    emit_indent(L, 2, "BarracksTypes: barr,tent")
    emit_indent(L, 2, "VehiclesFactoryTypes: weap")
    emit_indent(L, 2, "ProductionTypes: barr,tent,weap,afld,hpad")
    emit_indent(L, 2, "SiloTypes: silo")
    emit_indent(L, 2, "DefenseTypes: hbox,pbox,gun,ftur,tsla,agun,sam")
    emit_dict(L, 2, "BuildingLimits", bb["BuildingLimits"])
    emit_dict(L, 2, "BuildingFractions", bb["BuildingFractions"])
    if bb.get("BuildingDelays"):
        emit_dict(L, 2, "BuildingDelays", bb["BuildingDelays"])

    # UnitBuilderBotModule@<tmpl>
    emit_indent(L, 1, f"UnitBuilderBotModule@{tmpl}:")
    emit_indent(L, 2, f"RequiresCondition: {cond}")
    emit_dict(L, 2, "UnitsToBuild", UNIT_BUILDER[tmpl])
    emit_dict(L, 2, "UnitLimits", UNIT_LIMITS[tmpl])

    # SquadManagerBotModule@<tmpl>
    # NOTE: gated by `&& !enable-human-macro` so this autonomous combat driver
    # NEVER runs on a human player's PlayerActor. The strategy template still
    # shapes BaseBuilder + UnitBuilder for the human (doctrine drives what
    # gets built), but unit-vs-unit combat stays under the player's / MCP
    # control via dispatch_intent. AI-owned players (no human-macro condition)
    # can pick up these squad modules if a future change assigns templates
    # to bots; for now they're effectively dormant.
    emit_indent(L, 1, f"SquadManagerBotModule@{tmpl}:")
    emit_indent(L, 2, f"RequiresCondition: {cond} && !enable-human-macro")
    emit_indent(L, 2, f"SquadSize: {SQUAD_SIZE[tmpl]}")
    emit_indent(L, 2, "NavalUnitsTypes: ss, msub, dd, ca, lst, pt")
    emit_indent(L, 2, "ExcludeFromSquadsTypes: harv, mcv, dog, badr.bomber, u2, mnly")
    emit_indent(L, 2, "ConstructionYardTypes: fact")
    emit_indent(L, 2, "AirUnitsTypes: mig, yak, heli, hind, mh60")
    emit_indent(L, 2, "AircraftTargetType: AirborneActor")
    emit_indent(L, 2, "ProtectionTypes: harv, mcv, mslo, gap, spen, syrd, iron, pdox, tsla, agun, dome, pbox, hbox, gun, ftur, sam, atek, weap, fact, proc, silo, hpad, afld, afld.ukraine, powr, apwr, stek, barr, kenn, tent, fix")
    emit_indent(L, 2, "IgnoredEnemyTargetTypes: AirborneActor")

    return L


def generate_yaml() -> str:
    header = textwrap.dedent("""\
        # GENERATED by scripts/gen_strategy_templates.py — do not hand-edit.
        # Edit gen_strategy_templates.py + re-run to regenerate.
        #
        # 5 strategy-template module sets (BaseBuilder + UnitBuilder + SquadManager)
        # gated by `RequiresCondition: enable-strategy-<template>`.
        # StrategyControllerBotModule on the human player's PlayerActor grants/revokes
        # these conditions per `set_strategy` command from the MCP bridge.
        #
        # Templates: tank_rush | infantry_swarm | balanced | turtle | raid_harass
        Player:
        """)
    out = [header.rstrip("\n")]
    for t in TEMPLATES:
        out.extend(emit_template(t))
    out.append("")
    return "\n".join(out)


def patch_mod_yaml(mod_yaml: Path, dry_run: bool = False) -> bool:
    """Add strategy_templates.yaml to the Rules: list in mod.yaml if missing."""
    if not mod_yaml.exists():
        return False
    txt = mod_yaml.read_text(encoding="utf-8")
    if "strategy_templates.yaml" in txt:
        return False
    target = "ra|rules/ai.yaml"
    if target not in txt:
        return False
    new_line = "\n\tra|rules/strategy_templates.yaml"
    new_txt = txt.replace(target, target + new_line, 1)
    if dry_run:
        print(f"[dry] would patch {mod_yaml}")
        return True
    mod_yaml.write_text(new_txt, encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print to stdout instead of writing")
    ap.add_argument("--dry-run", action="store_true", help="print + don't patch mod.yaml")
    ap.add_argument(
        "--out",
        default="OpenRA/mods/ra/rules/strategy_templates.yaml",
        help="output path (relative to repo root)",
    )
    args = ap.parse_args()

    yaml_text = generate_yaml()
    if args.print or args.dry_run:
        sys.stdout.write(yaml_text)
        if args.dry_run:
            mod_yaml = Path(__file__).resolve().parent.parent / "OpenRA" / "mods" / "ra" / "mod.yaml"
            patch_mod_yaml(mod_yaml, dry_run=True)
        return

    out_path = Path(__file__).resolve().parent.parent / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text, encoding="utf-8")
    print(f"[OK] wrote {out_path} ({len(yaml_text.splitlines())} lines)")

    mod_yaml = Path(__file__).resolve().parent.parent / "OpenRA" / "mods" / "ra" / "mod.yaml"
    if patch_mod_yaml(mod_yaml):
        print(f"[OK] patched {mod_yaml} to include strategy_templates.yaml")
    else:
        print(f"[INFO] {mod_yaml} already includes strategy_templates.yaml or pattern not found")


if __name__ == "__main__":
    main()
