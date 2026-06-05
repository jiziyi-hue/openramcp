"""Merge real (decisions.jsonl) + synth (DeepSeek) NL→intent pairs into a
ChatML-formatted SFT dataset, dedup, train/val split.

Outputs:
    data/sft_train.jsonl   — 90% (messages list per line)
    data/sft_val.jsonl     — 10%
    data/sft_meta.json     — stats / distribution

Usage:
    python scripts/build_sft_dataset.py
    python scripts/build_sft_dataset.py --val-frac 0.1 --seed 42
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

SYSTEM_PROMPT = (
    "你是 OpenRA RTS 游戏的战术参谋翻译器. 玩家用中文下意图, 你输出**严格的 "
    "JSON 命令**, 不加解释. 你**从不**输出坐标或单位编号 — 只用命名目标, "
    "由解释器查实时状态算坐标.\n"
    "两种命令:\n"
    "1. attack — 派突击队打/前往某目标:\n"
    "   {\"intent\":\"attack\", \"force\":{筛选}, \"target\":{\"kind\":\"named\",\"name\":<目标>}}\n"
    "   force 筛选 (kind=filter, owner=self): combat_mobile=true (全部可机动) | "
    "unit_kind=<3tnk/2tnk/1tnk/e1/e3/arty/v2rl/jeep/apc> | harass_capable=true (快速绕后) | "
    "hp_below=0.3 (残血) | prefer=strongest/fastest/healthiest\n"
    "   target name: enemy_fact(敌建造场) | enemy_base(敌基地) | enemy_center | "
    "nearest_enemy | nearest_enemy_unit | nearest_enemy_structure | self_base(回家) | "
    "self_center | map_center(地图中心) | map_corner_ne/nw/se/sw(四角)\n"
    "2. report — 查战况: {\"intent\":\"report\", \"what\":<battlefield/enemy/threats/minimap/resources>}\n"
)


def normalize(s: str) -> str:
    return " ".join(s.strip().split()).lower()


def pair_fingerprint(nl: str, intent: dict) -> str:
    blob = normalize(nl) + "::" + json.dumps(intent, sort_keys=True,
                                              ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def load_jsonl(p: Path) -> list[dict]:
    out = []
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def to_chatml(nl: str, intent: dict) -> dict:
    assistant = json.dumps(intent, ensure_ascii=False, separators=(",", ":"))
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": nl},
            {"role": "assistant", "content": assistant},
        ]
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", default="data/sft_v1.jsonl")
    ap.add_argument("--synth", default="data/sft_v1_synth.jsonl")
    ap.add_argument("--boost", default=None,
                    help="extra synth file for weak classes (optional)")
    ap.add_argument("--cap-per-intent", type=int, default=None,
                    help="cap each intent_type to N examples (rebalance)")
    ap.add_argument("--out-train", default="data/sft_train.jsonl")
    ap.add_argument("--out-val", default="data/sft_val.jsonl")
    ap.add_argument("--meta", default="data/sft_meta.json")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    real = load_jsonl(Path(args.real))
    synth = load_jsonl(Path(args.synth))
    print(f"[load] real:  {len(real)} pairs from {args.real}")
    print(f"[load] synth: {len(synth)} pairs from {args.synth}")

    # Unify shape: both should have "nl" + "intent"
    pairs: list[tuple[str, dict, str]] = []
    for r in real:
        nl = r.get("nl") or r.get("nl_input")
        intent = r.get("intent")
        if nl and intent:
            pairs.append((nl.strip(), intent, "real"))
    for r in synth:
        nl = r.get("nl")
        intent = r.get("intent")
        if nl and intent:
            pairs.append((nl.strip(), intent, "synth"))
    if args.boost:
        boost = load_jsonl(Path(args.boost))
        print(f"[load] boost: {len(boost)} pairs from {args.boost}")
        for r in boost:
            nl = r.get("nl")
            intent = r.get("intent")
            if nl and intent:
                pairs.append((nl.strip(), intent, "boost"))
    print(f"[load] combined: {len(pairs)} pairs")

    # Dedup
    seen: set[str] = set()
    dedup: list[tuple[str, dict, str]] = []
    for nl, intent, src in pairs:
        fp = pair_fingerprint(nl, intent)
        if fp in seen:
            continue
        seen.add(fp)
        dedup.append((nl, intent, src))
    print(f"[dedup] {len(dedup)} unique pairs (dropped {len(pairs) - len(dedup)})")

    # Shuffle (before cap so the cap keeps a random subset)
    random.seed(args.seed)
    random.shuffle(dedup)

    # Cap per intent_type to rebalance (e.g. attack dominance)
    if args.cap_per_intent:
        kept: list[tuple[str, dict, str]] = []
        per: Counter[str] = Counter()
        for nl, intent, src in dedup:
            k = intent.get("intent") or intent.get("_tool") or "?"
            if per[k] >= args.cap_per_intent:
                continue
            per[k] += 1
            kept.append((nl, intent, src))
        print(f"[cap] {len(dedup)} -> {len(kept)} after cap "
              f"{args.cap_per_intent}/intent")
        dedup = kept

    # Split
    n_val = max(20, int(len(dedup) * args.val_frac))
    val_set = dedup[:n_val]
    train_set = dedup[n_val:]
    print(f"[split] train={len(train_set)}  val={n_val}")

    # Write
    Path(args.out_train).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_train, "w", encoding="utf-8") as f:
        for nl, intent, _ in train_set:
            f.write(json.dumps(to_chatml(nl, intent), ensure_ascii=False) + "\n")
    with open(args.out_val, "w", encoding="utf-8") as f:
        for nl, intent, _ in val_set:
            f.write(json.dumps(to_chatml(nl, intent), ensure_ascii=False) + "\n")

    # Meta / stats
    by_intent: Counter[str] = Counter()
    by_src: Counter[str] = Counter()
    for nl, intent, src in dedup:
        k = intent.get("intent") or intent.get("_tool") or "?"
        by_intent[k] += 1
        by_src[src] += 1
    meta = {
        "total_pairs": len(dedup),
        "train_size": len(train_set),
        "val_size": len(val_set),
        "by_source": dict(by_src),
        "by_intent": dict(by_intent.most_common()),
        "system_prompt_chars": len(SYSTEM_PROMPT),
        "seed": args.seed,
    }
    with open(args.meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("=" * 50)
    print(f"by source: {dict(by_src)}")
    print("by intent (top 10):")
    for k, n in by_intent.most_common(10):
        print(f"  {n:4d}  {k}")
    print(f"meta -> {args.meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
