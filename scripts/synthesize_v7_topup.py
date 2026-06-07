"""Targeted top-up synthesis for v7: fills the thin slots only.

Adds pairs for:
  - harass (currently 27): per-unit-kind harass × multiple targets
  - escort (currently 60): more verb variety per escortee
  - unit_kinds (currently 28-40 each): a 3rd attack target (nearest_enemy)

Reuses synthesize_v7.call() / hint_for() / fingerprint().

Setup: set DEEPSEEK_API_KEY=sk-...
Run:   python scripts/synthesize_v7_topup.py --output data/sft_v7_topup.jsonl
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.synthesize_v7 import call, hint_for, UNIT_KINDS, AIR_KINDS  # noqa
from scripts.synthesize_training_data import fingerprint  # noqa
from mcp_server import intent_dsl as D  # noqa


SELF = {"kind": "filter", "owner": "self"}
HC = {**SELF, "harass_capable": True}
CM = {**SELF, "combat_mobile": True}


def named(n):
    return {"kind": "named", "name": n}


def topup_templates() -> list[dict]:
    out = []

    # 1. ATTACK: every unit kind also gets a nearest_enemy target
    #    (boosts each unit_kind by ~22 pairs)
    for uk in UNIT_KINDS + AIR_KINDS:
        out.append({"intent": "attack", "force": {**SELF, "unit_kind": uk},
                    "target": named("nearest_enemy")})

    # 2. HARASS — really thin (27). New combinations:
    #    - per-unit-kind harass (fast units esp.)
    for uk in ("jeep", "dog", "e3", "1tnk", "apc", "ftrk"):
        for t in ("enemy_base", "enemy_fact", "nearest_enemy_structure"):
            out.append({"intent": "harass",
                        "force": {**SELF, "unit_kind": uk},
                        "target": named(t)})
    #    - harass with prefer fastest/healthiest
    for pref in ("fastest", "healthiest"):
        for t in ("enemy_base", "enemy_fact"):
            out.append({"intent": "harass",
                        "force": {**HC, "prefer": pref},
                        "target": named(t)})
    #    - harass nearest_enemy_unit (raid enemy patrols, not just base)
    out.append({"intent": "harass", "force": HC,
                "target": named("nearest_enemy_unit")})

    # 3. ESCORT — also thin. Add more variety per escortee.
    #    Use specific unit kinds as escorts (重坦护送 etc.)
    for esc in ("mcv", "nearest_vehicle", "nearest_infantry"):
        for uk in ("3tnk", "4tnk", "apc"):
            out.append({"intent": "escort",
                        "force": {**SELF, "unit_kind": uk},
                        "escortee": esc})

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/sft_v7_topup.jsonl")
    ap.add_argument("--per-template", type=int, default=22)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--resume", action="store_true",
                    help="skip templates whose fingerprint already in output")
    args = ap.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("[ERROR] DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 2

    tmpls = []
    for t in topup_templates():
        try:
            D.parse_intent(t)
            tmpls.append(t)
        except Exception as e:
            print("  ! rejected:", e, file=sys.stderr)
    from collections import Counter
    by = Counter(t["intent"] for t in tmpls)
    print(f"[INFO] {len(tmpls)} top-up templates {dict(by)} "
          f"~{len(tmpls)*args.per_template} pairs")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    done_fps: set[str] = set()
    n = nf = 0
    if args.resume and out.exists():
        for line in out.open("r", encoding="utf-8"):
            try:
                done_fps.add(json.loads(line)["fp"])
                n += 1
            except Exception:
                pass
        print(f"[resume] {len(done_fps)} fingerprints already done, "
              f"{n} pairs in file")
    t0 = time.time()
    mode = "a" if args.resume else "w"
    with out.open(mode, encoding="utf-8") as f:
        for i, t in enumerate(tmpls, 1):
            fp = fingerprint(t)
            tag = t["intent"]
            sub = (t.get("target") or t.get("where") or {}).get("name") \
                  or t.get("escortee")
            uk = t.get("force", {}).get("unit_kind")
            label = f"{tag}/{uk}/{sub}" if uk else f"{tag}/{sub}"
            if fp in done_fps:
                continue
            print(f"[{i}/{len(tmpls)}] {label} ...", end=" ", flush=True)
            try:
                nls = call(t, args.per_template, key, args.model)
            except Exception as e:
                print(f"FAIL {e}"); nf += 1; continue
            for nl in nls:
                f.write(json.dumps({"nl": nl, "intent": t,
                                    "fp": fingerprint(t)},
                                   ensure_ascii=False) + "\n")
                n += 1
            f.flush()
            print(f"+{len(nls)} ({n})")
    print(f"Done. {n} pairs, {nf} failed, {time.time()-t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
