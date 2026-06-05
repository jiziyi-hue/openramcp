"""Synthesize NL->intent pairs for the v5 surface: attack/report + the 5
coordless squad intents (defend/harass/scout/patrol/escort).

Fixes the v4 data problems the player flagged:
  - drops the broken attack->self_base "retreat" hack (you can't attack your
    own base); "守家" is now a proper defend intent.
  - injects explicit Chinese term hints for unit kinds (重坦/中坦/轻坦) and map
    corners (左上=nw ...) so DeepSeek stops mislabelling them.
  - more per-template volume.

Every template is validated by parse_intent before synthesis.

Setup:  set DEEPSEEK_API_KEY=sk-...
Run:    python scripts/synthesize_v5.py --per-template 25 --output data/sft_v5.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.synthesize_training_data import fingerprint  # noqa: E402
from mcp_server import intent_dsl as D  # noqa: E402

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

SYS = """你是 OpenRA RTS 玩家的"参谋陪练". 给你一条游戏指令的 JSON + 术语说明,
你用多种**真实玩家口语**的中文说出这条指令.
要求: 简短 (<25字), 玩家俚语 (老家/重工/分矿/切经济/绕后), 多样化, 一行一句,
不带编号/引号/解释. **严格使用术语说明里的词**, 不要把兵种/方位说错.
必须严格输出指定条数."""

# Canonical Chinese terms — injected so DeepSeek phrases them correctly.
UNIT_ZH = {
    "3tnk": "重型坦克(重坦/重型)", "2tnk": "中型坦克(中坦/中型)",
    "1tnk": "轻型坦克(轻坦/轻型, 不是重坦也不是中坦)",
    "e1": "步枪兵(步兵)", "e3": "火箭兵(火箭筒兵)", "arty": "火炮(炮兵/榴弹炮)",
    "v2rl": "V2火箭车", "jeep": "吉普车(悍马)", "apc": "装甲运兵车(APC)",
}
TARGET_ZH = {
    "enemy_fact": "敌方建造场(他家重工/总部/工厂)",
    "enemy_base": "敌方基地中心(他老家/他家)",
    "enemy_center": "敌军主力中心",
    "nearest_enemy": "最近的敌人", "nearest_enemy_unit": "最近的敌方部队",
    "nearest_enemy_structure": "最近的敌方建筑",
    "self_base": "我方基地(自己老家/家里)", "self_center": "我方主力中心",
    "map_center": "地图中心(中路/正中)",
    "map_corner_ne": "右上角(东北角)", "map_corner_nw": "左上角(西北角)",
    "map_corner_se": "右下角(东南角)", "map_corner_sw": "左下角(西南角)",
}
ROUTE_ZH = {
    "base_perimeter": "绕自己基地周边巡逻", "front_line": "在前线一带来回巡",
    "east_lane": "巡逻东路(右边)", "west_lane": "巡逻西路(左边)",
    "north_lane": "巡逻北路(上边)", "south_lane": "巡逻南路(下边)",
    "center_loop": "绕地图中心巡逻",
}
ESCORTEE_ZH = {
    "mcv": "MCV基地车", "harvester": "采矿车(矿车)",
    "nearest_vehicle": "最近的载具", "nearest_infantry": "最近的步兵",
}
REPORT_ZH = {
    "battlefield": "整体战况", "enemy": "敌情(敌人在哪)",
    "threats": "当前威胁", "minimap": "小地图", "resources": "资源/电力",
}


def hint_for(tmpl: dict) -> str:
    bits = []
    f = tmpl.get("force", {})
    if f.get("unit_kind"):
        bits.append(f"兵种={UNIT_ZH.get(f['unit_kind'], f['unit_kind'])}")
    if f.get("combat_mobile"):
        bits.append("兵种=全部可机动单位(全军/所有兵)")
    if f.get("harass_capable"):
        bits.append("兵种=快速绕后单位(快的/机动单位)")
    if f.get("hp_below"):
        bits.append("兵种=残血单位")
    if f.get("prefer") == "fastest":
        bits.append("挑最快的")
    if f.get("prefer") == "healthiest":
        bits.append("挑满血的")
    for key in ("target", "where"):
        t = tmpl.get(key)
        if isinstance(t, dict) and t.get("name"):
            bits.append(f"目标={TARGET_ZH.get(t['name'], t['name'])}")
    if tmpl.get("route"):
        bits.append(f"动作={ROUTE_ZH.get(tmpl['route'], tmpl['route'])}")
    if tmpl.get("escortee"):
        bits.append(f"护送={ESCORTEE_ZH.get(tmpl['escortee'], tmpl['escortee'])}")
    if tmpl.get("what"):
        bits.append(f"查询={REPORT_ZH.get(tmpl['what'], tmpl['what'])}")
    verb = {"attack": "进攻", "defend": "防守", "harass": "骚扰",
            "scout": "侦察/探图", "patrol": "巡逻", "escort": "护送",
            "report": "查看战况"}.get(tmpl.get("intent"), "")
    if verb:
        bits.insert(0, f"动作类型={verb}")
    return "; ".join(bits)


def call(tmpl: dict, n: int, key: str, model: str, retries: int = 3) -> list[str]:
    msg = (f"指令 JSON:\n{json.dumps(tmpl, ensure_ascii=False)}\n\n"
           f"术语说明: {hint_for(tmpl)}\n\n"
           f"请输出 {n} 条不同的中文玩家话, 一行一条, 严格用术语说明里的词.")
    body = {"model": model, "temperature": 0.9, "max_tokens": 1400,
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": msg}]}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    for a in range(retries):
        try:
            r = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=60)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            lines = [ln.strip().lstrip("0123456789.、)-．。 \t")
                     for ln in txt.splitlines() if ln.strip()]
            lines = [ln for ln in lines if 1 <= len(ln) <= 80
                     and not ln.startswith(("{", "```", "//"))]
            if len(lines) >= max(2, n // 3):
                return lines[:n]
            raise RuntimeError(f"few lines: {len(lines)}")
        except Exception as e:
            last = e
            time.sleep(2 ** a)
    raise RuntimeError(f"deepseek failed: {last}")


# --- templates ---------------------------------------------------------------
SELF = {"kind": "filter", "owner": "self"}
CM = {**SELF, "combat_mobile": True}
HC = {**SELF, "harass_capable": True}
UNIT_KINDS = ["3tnk", "2tnk", "1tnk", "e1", "e3", "arty", "v2rl", "jeep", "apc"]
ENEMY_T = ["enemy_fact", "enemy_base", "enemy_center", "nearest_enemy",
           "nearest_enemy_unit", "nearest_enemy_structure"]
LANDMARKS = ["map_center", "map_corner_ne", "map_corner_nw",
             "map_corner_se", "map_corner_sw"]
SELF_PLACES = ["self_base", "self_center"]
ROUTES = ["base_perimeter", "front_line", "east_lane", "west_lane",
          "north_lane", "south_lane", "center_loop"]
ESCORTEES = ["mcv", "harvester", "nearest_vehicle", "nearest_infantry"]


def named(n):
    return {"kind": "named", "name": n}


def templates() -> list[dict]:
    out = []
    # attack: combat_mobile -> enemy targets + landmarks (NO self_base)
    for t in ENEMY_T + LANDMARKS:
        out.append({"intent": "attack", "force": CM, "target": named(t)})
    for uk in UNIT_KINDS:
        for t in ("enemy_fact", "enemy_base", "nearest_enemy"):
            out.append({"intent": "attack", "force": {**SELF, "unit_kind": uk},
                        "target": named(t)})
    for t in ("enemy_base", "enemy_fact", "nearest_enemy_structure"):
        out.append({"intent": "attack", "force": HC, "target": named(t)})
    for pref in ("fastest", "healthiest"):
        out.append({"intent": "attack", "force": {**CM, "prefer": pref},
                    "target": named("enemy_fact")})
    # report
    for w in REPORT_ZH:
        out.append({"intent": "report", "what": w})
    # defend a place (Protection)
    for p in SELF_PLACES + LANDMARKS:
        out.append({"intent": "defend", "force": CM, "where": named(p)})
    # harass economy
    for t in ("enemy_base", "enemy_fact", "enemy_center"):
        out.append({"intent": "harass", "force": HC, "target": named(t)})
    # scout / explore
    for p in ("enemy_base", "enemy_fact") + tuple(LANDMARKS):
        out.append({"intent": "scout", "force": CM, "where": named(p)})
    # patrol routes
    for r in ROUTES:
        out.append({"intent": "patrol", "force": CM, "route": r})
    # escort
    for e in ESCORTEES:
        out.append({"intent": "escort", "force": CM, "escortee": e})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/sft_v5.jsonl")
    ap.add_argument("--per-template", type=int, default=25)
    ap.add_argument("--model", default="deepseek-v4-flash")
    args = ap.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("[ERROR] DEEPSEEK_API_KEY not set", file=sys.stderr)
        return 2

    tmpls = []
    for t in templates():
        try:
            D.parse_intent(t)
            tmpls.append(t)
        except Exception as e:
            print(f"  ! rejected: {e}", file=sys.stderr)
    from collections import Counter
    by = Counter(t["intent"] for t in tmpls)
    print(f"[INFO] {len(tmpls)} templates {dict(by)} -> "
          f"~{len(tmpls) * args.per_template} pairs")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = nf = 0
    t0 = time.time()
    with out.open("w", encoding="utf-8") as f:
        for i, t in enumerate(tmpls, 1):
            tag = t.get("intent")
            sub = t.get("what") or t.get("route") or t.get("escortee") or \
                (t.get("target") or t.get("where") or {}).get("name")
            print(f"[{i}/{len(tmpls)}] {tag}/{sub} ...", end=" ", flush=True)
            try:
                nls = call(t, args.per_template, key, args.model)
            except Exception as e:
                print(f"FAIL {e}")
                nf += 1
                continue
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
