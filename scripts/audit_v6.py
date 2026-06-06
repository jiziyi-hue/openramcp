"""Pre-train audit of the v6 dataset — catch label/NL problems BEFORE the
expensive Colab round-trip. Checks validity, NL<->label consistency per
intent, field-specific conflicts, garbage, and coverage.

Run: python scripts/audit_v6.py
"""
from __future__ import annotations
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_server import intent_dsl as D  # noqa: E402

DATA = "data/sft_v6.jsonl"

# expected verb keywords per intent (gross-mismatch detector)
VERB = {
    "attack": ["攻", "打", "推", "冲", "拆", "灭", "干", "轰", "突", "刚", "怼", "上", "压", "杀", "端", "莽", "强攻", "进攻"],
    "defend": ["守", "防", "护住", "保卫", "顶", "别丢", "别让", "看家", "镇守", "驻"],
    "harass": ["骚扰", "切经济", "打钱", "偷矿", "袭扰", "扰", "切断", "放血", "搔扰", "切他", "断他"],
    "scout": ["侦", "探", "看看", "瞧", "瞄", "查探", "爆雾", "摸", "踩点", "看一下", "看下", "探探"],
    "patrol": ["巡", "来回", "转悠", "警戒", "盯", "溜达", "绕"],
    "escort": ["护送", "护", "保护", "跟", "掩护", "送", "陪", "随行"],
    "pincer": ["分两路", "夹击", "钳形", "兵分", "两路", "包抄", "包饺子", "分兵", "两边", "左右", "两面"],
    "report": ["看", "查", "报", "情况", "战况", "啥样", "在哪", "汇报", "瞅", "咋样", "怎么样", "多少", "瞧瞧", "地图", "资源", "电"],
}
ROUTE_DIR = {
    "east_lane": (["东", "右"], ["西", "左", "北", "上", "南", "下"]),
    "west_lane": (["西", "左"], ["东", "右", "北", "上", "南", "下"]),
    "north_lane": (["北", "上"], ["南", "下"]),
    "south_lane": (["南", "下"], ["北", "上"]),
}
ESCORTEE_W = {
    "mcv": (["基地车", "mcv", "MCV", "建造车", "母舰"], ["矿车", "采矿"]),
    "nearest_vehicle": (["车", "载具"], []),
    "nearest_infantry": (["兵", "步兵"], []),
}
REPORT_W = {
    "minimap": (["地图", "小地图", "minimap"], []),
    "resources": (["资源", "电", "钱", "矿", "经济"], []),
}


def main() -> int:
    rows = [json.loads(l) for l in open(DATA, encoding="utf-8") if l.strip()]
    n = len(rows)
    print(f"=== v6 audit: {n} rows ===")

    bad_parse = 0
    verb_miss = defaultdict(list)
    field_conf = defaultdict(list)
    garbage = []
    by_intent = Counter()
    cov_unit = Counter()
    cov_target = Counter()

    for r in rows:
        nl = r["nl"]
        it = r["intent"]
        intent = it.get("intent")
        by_intent[intent] += 1
        # 1. validity
        try:
            D.parse_intent(it)
        except Exception:
            bad_parse += 1
        # 2. garbage
        if len(nl) > 40 or len(nl) < 2 or re.fullmatch(r"[a-zA-Z0-9 ,.]+", nl):
            garbage.append(nl)
        # 3. verb consistency
        kws = VERB.get(intent, [])
        if kws and not any(k in nl for k in kws):
            verb_miss[intent].append(nl)
        # 4. field conflicts
        f = it.get("force", {})
        if f.get("unit_kind"):
            cov_unit[f["unit_kind"]] += 1
        for key in ("target", "where", "left", "right"):
            t = it.get(key)
            if isinstance(t, dict) and t.get("name"):
                cov_target[t["name"]] += 1
        rt = it.get("route")
        if rt in ROUTE_DIR:
            good, bad = ROUTE_DIR[rt]
            if any(b in nl for b in bad) and not any(g in nl for g in good):
                field_conf[f"route:{rt}"].append(nl)
        esc = it.get("escortee")
        if esc in ESCORTEE_W:
            good, bad = ESCORTEE_W[esc]
            if any(b in nl for b in bad):
                field_conf[f"escortee:{esc}"].append(nl)
        wt = it.get("what")
        if wt in REPORT_W:
            good, bad = REPORT_W[wt]
            if not any(g in nl for g in good):
                field_conf[f"report:{wt}"].append(nl)

    print(f"\n[1] parse_intent invalid: {bad_parse}")
    print(f"[2] garbage (too long/short/english): {len(garbage)}")
    for g in garbage[:5]:
        print(f"      {g!r}")
    print(f"\n[3] verb mismatch (NL lacks any expected verb for its intent):")
    for it in by_intent:
        m = verb_miss.get(it, [])
        rate = 100 * len(m) / max(by_intent[it], 1)
        flag = " <-- HIGH" if rate > 15 else ""
        print(f"    {it:8s} {len(m):3d}/{by_intent[it]:4d} ({rate:4.0f}%){flag}")
        for s in m[:3]:
            print(f"        e.g. {s}")
    print(f"\n[4] field conflicts (route dir / escortee / report-what):")
    if not field_conf:
        print("    none")
    for k, v in field_conf.items():
        print(f"    {k}: {len(v)}")
        for s in v[:3]:
            print(f"        {s}")
    print(f"\n[5] coverage:")
    print(f"    intents: {dict(by_intent)}")
    print(f"    unit_kinds present: {sorted(cov_unit)}")
    miss_u = set(["1tnk", "2tnk", "3tnk", "4tnk", "ttnk", "ftrk", "v2rl",
                  "arty", "jeep", "apc", "e1", "e2", "e3", "e4", "dog",
                  "shok"]) - set(cov_unit)
    print(f"    unit_kinds MISSING: {sorted(miss_u) or 'none'}")
    print(f"    targets present: {sorted(cov_target)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
