"""Synthesize NL->intent pairs for the REAL current command surface only.

Unlike the old synth (which targeted the deprecated 15-intent DSL), this
generates ONLY commands the current interpreter actually accepts:
  - attack  {force: filter, target: named}   (incl. map landmarks)
  - report  {what: ...}

Every template is validated with mcp_server.intent_dsl.parse_intent BEFORE
asking DeepSeek to back-translate it — so the training set cannot contain a
command the engine would reject. The force is always a filter and the target
always a name, so the LLM never needs coordinates or unit ids.

Setup:  set DEEPSEEK_API_KEY=sk-...
Run:    python scripts/synthesize_v4_real.py --per-template 20 --output data/sft_real.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.synthesize_training_data import call_deepseek, fingerprint  # noqa: E402
from mcp_server import intent_dsl as D  # noqa: E402

# --- real, coordless surface --------------------------------------------

TARGETS = [
    "enemy_fact", "enemy_base", "enemy_center",
    "nearest_enemy", "nearest_enemy_unit", "nearest_enemy_structure",
    "self_base", "self_center",
    "map_center", "map_corner_ne", "map_corner_nw",
    "map_corner_se", "map_corner_sw",
]

# Force filters (no ids — those need live state). unit_kind values are RA names.
UNIT_KINDS = ["3tnk", "2tnk", "1tnk", "e1", "e3", "arty", "v2rl", "jeep", "apc"]
REPORT_WHATS = ["battlefield", "enemy", "threats", "minimap", "resources"]


def attack(force: dict, target_name: str) -> dict:
    return {"intent": "attack", "force": force,
            "target": {"kind": "named", "name": target_name}}


def build_templates() -> list[dict]:
    out: list[dict] = []

    # 1. all combat-mobile units -> every named target (the core "全军->X")
    for t in TARGETS:
        out.append(attack({"kind": "filter", "owner": "self",
                            "combat_mobile": True}, t))

    # 2. each unit kind -> the 3 most common combat targets
    for uk in UNIT_KINDS:
        for t in ("enemy_fact", "enemy_base", "nearest_enemy"):
            out.append(attack({"kind": "filter", "owner": "self",
                               "unit_kind": uk}, t))

    # 3. harass-capable fast units -> economy / nearest structure
    for t in ("enemy_base", "enemy_fact", "nearest_enemy_structure"):
        out.append(attack({"kind": "filter", "owner": "self",
                           "harass_capable": True}, t))

    # 4. prefer variants (fastest / healthiest) -> fact, for "派最快的/满血的去"
    for pref in ("fastest", "healthiest"):
        out.append(attack({"kind": "filter", "owner": "self",
                           "combat_mobile": True, "prefer": pref}, "enemy_fact"))

    # 5. low-hp units pulled back to base ("残血的回家") — attack toward self_base
    out.append(attack({"kind": "filter", "owner": "self", "hp_below": 0.3},
                      "self_base"))

    # 6. report
    for w in REPORT_WHATS:
        out.append({"intent": "report", "what": w})

    return out


def validate(tmpl: dict) -> bool:
    """Guarantee the engine accepts this command. Skip if not."""
    try:
        D.parse_intent(tmpl)
        return True
    except Exception as e:
        print(f"  ! template rejected by parse_intent: {e}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/sft_real.jsonl")
    ap.add_argument("--per-template", type=int, default=20)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[ERROR] DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 2

    templates = [t for t in build_templates() if validate(t)]
    print(f"[INFO] {len(templates)} validated templates, "
          f"target {len(templates) * args.per_template} pairs")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if args.resume and out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(fingerprint(json.loads(line)["intent"]))
            except Exception:
                pass

    n_pairs = n_fail = 0
    t0 = time.time()
    mode = "a" if args.resume else "w"
    with out_path.open(mode, encoding="utf-8") as f:
        for i, tmpl in enumerate(templates, 1):
            fp = fingerprint(tmpl)
            tag = tmpl.get("intent")
            sub = tmpl.get("what") or (tmpl.get("target", {}).get("name"))
            if fp in done:
                print(f"[{i}/{len(templates)}] skip {tag}/{sub}")
                continue
            print(f"[{i}/{len(templates)}] {tag}/{sub} ...", end=" ", flush=True)
            try:
                nls = call_deepseek(tmpl, args.per_template, api_key, args.model)
            except Exception as e:
                print(f"FAIL: {e}")
                n_fail += 1
                continue
            for nl in nls:
                f.write(json.dumps({"nl": nl, "intent": tmpl, "fp": fp},
                                   ensure_ascii=False) + "\n")
                n_pairs += 1
            f.flush()
            print(f"+{len(nls)}  (total {n_pairs})")
    print("=" * 56)
    print(f"Done. {n_pairs} pairs, {n_fail} failed, {time.time()-t0:.0f}s.")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
