"""Synthesize (NL → intent JSON) training pairs by enumerating DSL templates
and asking DeepSeek to back-translate each into Chinese player utterances.

Setup:
    set DEEPSEEK_API_KEY=sk-...           # cmd
    $env:DEEPSEEK_API_KEY = "sk-..."      # PowerShell

Usage:
    python scripts/synthesize_training_data.py
    python scripts/synthesize_training_data.py --per-template 10 --max-templates 50
    python scripts/synthesize_training_data.py --resume  # skip already-done templates

Output:
    data/sft_v1_synth.jsonl   (one JSON per line: {"nl": ..., "intent": ...})

Cost: ~250 templates × 1 DeepSeek call ≈ $0.3 - $1.0 on v4-flash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"  # cheap; v4-pro if quality needed

SYS_PROMPT = """你是 OpenRA RTS 游戏玩家的"参谋翻译陪练". 我给你一个游戏战术指令的
JSON 结构, 你的任务: 用多种不同的中文口语化方式说出**真实玩家在游戏里会怎么说这条指令**.

要求:
- 简短, 像玩家在 OpenRA UI 旁边随口跟 AI 参谋说话, 不超过 25 字一句
- 不要正式军事化 ("指挥所"/"阁下"), 要游戏玩家俚语 ("北线"/"老家"/"切经济"/"佬家")
- 多样化: 有些直白 ("北群推他家"), 有些带情绪 ("快上啊"), 有些短 ("撤"), 有些带术语 ("frontal推")
- 不要每条都加"请"
- 一行一句中文, 不带编号, 不带引号, 不带解释
- 必须严格输出指定条数, 不多不少
"""


# ---------- DSL template enumeration ----------

GROUPS = ["north", "center", "south", "all"]
HARASS_FILTER = {"kind": "filter", "harass_capable": True}
LOW_HP_FILTER = {"kind": "filter", "owner": "self", "hp_below": 0.3}
TANK_FILTER = {"kind": "filter", "owner": "self", "unit_kind": "2tnk"}
NAMED_TARGETS_HIGH = ["enemy_fact", "enemy_base", "nearest_enemy",
                      "nearest_enemy_structure"]
APPROACHES = ["frontal", "flank_left", "flank_right", "charge", "cautious"]
STANCES = ["HoldFire", "ReturnFire", "Defend", "AttackAnything"]
REPORT_WHATS = ["battlefield", "groups", "enemy", "threats", "minimap",
                "resources", "enemy_intent", "group_north", "group_south"]
ALERT_LEVELS = ["peace", "watch", "alert", "combat", "lockdown"]
OBJECTIVES = ["destroy_fact", "destroy_enemy", "harass_economy",
              "survive_until_tick", "control_map_center"]
SQUAD_TYPES = ["Assault", "Protection", "Patrol", "Escort", "Harass", "Explore"]


def gen_attack_templates() -> list[dict]:
    out = []
    for g in GROUPS:
        for tgt in NAMED_TARGETS_HIGH:
            for app in APPROACHES:
                out.append({
                    "intent": "attack",
                    "force": {"kind": "group", "name": g},
                    "target": {"kind": "named", "name": tgt},
                    "approach": app,
                })
    # plus tank-filter attacks
    for tgt in NAMED_TARGETS_HIGH[:2]:
        for app in ["frontal", "charge", "flank_left"]:
            out.append({
                "intent": "attack",
                "force": TANK_FILTER,
                "target": {"kind": "named", "name": tgt},
                "approach": app,
            })
    return out


def gen_defend_templates() -> list[dict]:
    out = []
    for g in GROUPS:
        for r in [8, 12, 15]:
            out.append({
                "intent": "defend",
                "force": {"kind": "group", "name": g},
                "region": {"kind": "around", "center": "self_base",
                           "radius": r},
                "stance": "Defend",
            })
    return out


def gen_retreat_templates() -> list[dict]:
    out = [
        {"intent": "retreat", "force": LOW_HP_FILTER,
         "to": {"kind": "named", "name": "self_base"}},
        {"intent": "retreat",
         "force": {"kind": "filter", "owner": "self", "hp_below": 0.5},
         "to": {"kind": "named", "name": "self_base"}},
    ]
    for g in GROUPS:
        out.append({
            "intent": "retreat",
            "force": {"kind": "group", "name": g},
            "to": {"kind": "named", "name": "self_base"},
        })
    return out


def gen_regroup_templates() -> list[dict]:
    out = []
    for g in GROUPS:
        out.append({
            "intent": "regroup",
            "force": {"kind": "group", "name": g},
            "to": {"kind": "named", "name": "self_center"},
        })
    return out


def gen_scout_templates() -> list[dict]:
    out = []
    for g in ["north", "center", "south"]:
        for center in ["enemy_base", "enemy_fact", "nearest_enemy"]:
            out.append({
                "intent": "scout",
                "force": {"kind": "group", "name": g},
                "region": {"kind": "around", "center": center, "radius": 6},
            })
    return out


def gen_pincer_templates() -> list[dict]:
    out = []
    for left, right in [("north", "south"), ("north", "center"),
                        ("center", "south")]:
        for tgt in ["enemy_fact", "enemy_base"]:
            out.append({
                "intent": "pincer",
                "left": {"kind": "group", "name": left},
                "right": {"kind": "group", "name": right},
                "target": {"kind": "named", "name": tgt},
                "rendezvous_dist": 8,
            })
    return out


def gen_feint_templates() -> list[dict]:
    out = []
    for g in GROUPS:
        for tgt in ["enemy_base", "enemy_fact"]:
            out.append({
                "intent": "feint",
                "force": {"kind": "group", "name": g},
                "target": {"kind": "named", "name": tgt},
            })
    return out


def gen_harass_templates() -> list[dict]:
    out = []
    for center in ["enemy_base", "enemy_fact"]:
        for r in [6, 8, 10]:
            out.append({
                "intent": "harass",
                "force": HARASS_FILTER,
                "region": {"kind": "around", "center": center, "radius": r},
                "cycle": False,
                "withdraw_hp_threshold": 0.5,
            })
    return out


def gen_patrol_templates() -> list[dict]:
    # patrol uses named path symbol — synthesize as "named region" patrol
    out = []
    for g in ["north", "center", "south"]:
        out.append({
            "intent": "patrol",
            "force": {"kind": "group", "name": g},
            "engage_on_contact": "scout",
            "cycle": True,
        })
    return out


def gen_escort_templates() -> list[dict]:
    out = []
    for g in GROUPS[:3]:
        out.append({
            "intent": "escort",
            "force": {"kind": "group", "name": g},
            "engage_radius": 6,
        })
    return out


def gen_contain_templates() -> list[dict]:
    out = []
    for g in ["north", "center", "south"]:
        out.append({
            "intent": "contain",
            "force": {"kind": "group", "name": g},
            "radius": 4,
            "stance": "AttackAnything",
        })
    return out


def gen_diversion_templates() -> list[dict]:
    out = []
    for feint_g, raid_g in [("center", "north"), ("center", "south"),
                            ("north", "south")]:
        out.append({
            "intent": "diversion",
            "feint_force": {"kind": "group", "name": feint_g},
            "feint_target": {"kind": "named", "name": "enemy_base"},
            "raid_force": HARASS_FILTER,
            "raid_target": {"kind": "named", "name": "enemy_fact"},
            "raid_approach": "flank_left",
        })
    return out


def gen_set_stance_templates() -> list[dict]:
    out = []
    for g in GROUPS:
        for st in STANCES:
            out.append({
                "intent": "set_stance",
                "force": {"kind": "group", "name": g},
                "stance": st,
            })
    return out


def gen_report_templates() -> list[dict]:
    return [{"intent": "report", "what": w} for w in REPORT_WHATS]


# Higher-level tools (not dispatch_intent, but tool calls)
def gen_set_alert_state_templates() -> list[dict]:
    return [{"_tool": "set_alert_state", "level": lv} for lv in ALERT_LEVELS]


def gen_set_objective_templates() -> list[dict]:
    out = [{"_tool": "set_objective", "name": o} for o in OBJECTIVES if o != "survive_until_tick"]
    out.append({"_tool": "set_objective", "name": "survive_until_tick", "tick": 18000})
    return out


def gen_set_doctrine_templates() -> list[dict]:
    return [
        {"_tool": "set_doctrine", "alert_state": "combat", "objective": "destroy_enemy"},
        {"_tool": "set_doctrine", "alert_state": "alert", "objective": "harass_economy"},
        {"_tool": "set_doctrine", "alert_state": "lockdown", "objective": "survive_until_tick", "survive_tick": 18000},
        {"_tool": "set_doctrine", "alert_state": "watch"},
        {"_tool": "set_doctrine", "objective": "destroy_fact"},
        {"_tool": "set_doctrine", "alert_state": "peace"},
    ]


def gen_spawn_squad_templates() -> list[dict]:
    out = []
    for st in SQUAD_TYPES:
        out.append({"_tool": "spawn_squad", "squad_type": st})
    out.append({"_tool": "cancel_assaults"})
    out.append({"_tool": "enable_auto_defense"})
    return out


def all_templates() -> list[dict]:
    return (
        gen_attack_templates()
        + gen_defend_templates()
        + gen_retreat_templates()
        + gen_regroup_templates()
        + gen_scout_templates()
        + gen_pincer_templates()
        + gen_feint_templates()
        + gen_harass_templates()
        + gen_patrol_templates()
        + gen_escort_templates()
        + gen_contain_templates()
        + gen_diversion_templates()
        + gen_set_stance_templates()
        + gen_report_templates()
        + gen_set_alert_state_templates()
        + gen_set_objective_templates()
        + gen_set_doctrine_templates()
        + gen_spawn_squad_templates()
    )


# ---------- DeepSeek call ----------

def fingerprint(intent: dict) -> str:
    s = json.dumps(intent, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def call_deepseek(intent: dict, n: int, api_key: str, model: str,
                  timeout: int = 60, retries: int = 3) -> list[str]:
    """Ask DeepSeek to produce n Chinese player utterances for this intent."""
    intent_str = json.dumps(intent, ensure_ascii=False, indent=2)
    user_msg = (
        f"指令 JSON:\n{intent_str}\n\n"
        f"请输出 {n} 条不同的中文玩家话, 一行一条, 不要编号."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.9,  # high diversity
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.post(DEEPSEEK_URL, headers=headers,
                              json=payload, timeout=timeout)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            lines = [
                ln.strip().lstrip("0123456789.、)-．。 \t")
                for ln in content.splitlines()
                if ln.strip()
            ]
            # Filter out empty / clearly non-Chinese-utterance lines
            lines = [ln for ln in lines if 1 <= len(ln) <= 80
                     and not ln.startswith(("{", "```", "//"))]
            if len(lines) < max(2, n // 3):
                raise RuntimeError(f"too few lines parsed: {len(lines)}")
            return lines[:n]
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  ! retry {attempt + 1}/{retries} after {wait}s: {e}",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"DeepSeek failed after {retries} retries: {last_err}")


# ---------- Main loop ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/sft_v1_synth.jsonl")
    ap.add_argument("--per-template", type=int, default=10)
    ap.add_argument("--max-templates", type=int, default=None,
                    help="cap templates (debug)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--resume", action="store_true",
                    help="skip templates already in output by fingerprint")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[ERROR] DEEPSEEK_API_KEY not set in env", file=sys.stderr)
        return 2

    templates = all_templates()
    if args.max_templates:
        templates = templates[: args.max_templates]
    print(f"[INFO] {len(templates)} templates, "
          f"target {len(templates) * args.per_template} pairs")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if args.resume and out_path.exists():
        with out_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done.add(fingerprint(rec["intent"]))
                except Exception:
                    pass
        print(f"[INFO] resume: {len(done)} fingerprints already done")

    n_pairs = 0
    n_fail = 0
    t0 = time.time()
    mode = "a" if args.resume else "w"
    with out_path.open(mode, encoding="utf-8") as out_f:
        for i, tmpl in enumerate(templates, 1):
            fp = fingerprint(tmpl)
            tag = tmpl.get("intent") or tmpl.get("_tool") or "?"
            if fp in done:
                print(f"[{i}/{len(templates)}] skip {tag} ({fp})")
                continue
            print(f"[{i}/{len(templates)}] {tag} ({fp}) ...", end=" ",
                  flush=True)
            try:
                nls = call_deepseek(tmpl, args.per_template, api_key,
                                    args.model)
            except Exception as e:
                print(f"FAIL: {e}")
                n_fail += 1
                continue
            for nl in nls:
                pair = {"nl": nl, "intent": tmpl, "fp": fp}
                out_f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                n_pairs += 1
            out_f.flush()
            print(f"+{len(nls)}  (total pairs: {n_pairs})")
    dt = time.time() - t0
    print("=" * 60)
    print(f"Done. {n_pairs} pairs from {len(templates) - n_fail} templates "
          f"({n_fail} failed) in {dt:.1f}s.")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
