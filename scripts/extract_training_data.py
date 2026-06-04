"""Extract (NL → intent JSON) training pairs from MCP decisions.jsonl logs.

Usage:
    python scripts/extract_training_data.py
    python scripts/extract_training_data.py --cutoff 20260523 --output data/sft_v1.jsonl

Reads every logs/*/decisions.jsonl, keeps lines where:
  - nl_input is non-empty
  - ok == True
  - session_id date >= cutoff (architecture refactor cutoff)

Writes a unified JSONL with one record per pair:
  {"nl": "...", "intent": {...}, "intent_type": "...",
   "session_id": "...", "ts": "...", "tick": ...}

Prints stats: total, kept, breakdown by date and intent_type.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="logs", help="logs root dir")
    ap.add_argument(
        "--cutoff",
        default="20260523",
        help="session_id date prefix cutoff (inclusive). "
        "Sessions before this are dropped (architecture refactor).",
    )
    ap.add_argument(
        "--output",
        default="data/sft_v1.jsonl",
        help="output JSONL path",
    )
    ap.add_argument(
        "--keep-pre-cutoff",
        action="store_true",
        help="do NOT drop pre-cutoff sessions (default: drop)",
    )
    args = ap.parse_args()

    logs_root = Path(args.logs)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    decision_files = sorted(logs_root.glob("*/decisions.jsonl"))
    if not decision_files:
        print(f"[ERROR] no decisions.jsonl under {logs_root}/", file=sys.stderr)
        return 1

    total_lines = 0
    kept = 0
    drop_no_nl = 0
    drop_not_ok = 0
    drop_pre_cutoff = 0
    drop_bad_json = 0

    by_date: Counter[str] = Counter()
    by_intent: Counter[str] = Counter()
    by_session_kept: defaultdict[str, int] = defaultdict(int)
    samples: list[dict] = []

    with out_path.open("w", encoding="utf-8") as out_f:
        for df in decision_files:
            session_id = df.parent.name
            date = session_id.split("-")[0] if "-" in session_id else session_id[:8]

            if not args.keep_pre_cutoff and date < args.cutoff:
                # Skip whole session
                with df.open("r", encoding="utf-8", errors="ignore") as f:
                    for _ in f:
                        total_lines += 1
                        drop_pre_cutoff += 1
                continue

            with df.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    total_lines += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        drop_bad_json += 1
                        continue

                    nl = rec.get("nl_input")
                    if not nl or not str(nl).strip():
                        drop_no_nl += 1
                        continue
                    if not rec.get("ok", False):
                        drop_not_ok += 1
                        continue

                    intent = rec.get("intent")
                    if intent is None:
                        drop_no_nl += 1  # treat as malformed
                        continue

                    intent_type = rec.get("intent_type") or (
                        intent.get("intent") if isinstance(intent, dict) else None
                    ) or "unknown"

                    pair = {
                        "nl": str(nl).strip(),
                        "intent": intent,
                        "intent_type": intent_type,
                        "session_id": session_id,
                        "ts": rec.get("ts"),
                        "tick": rec.get("tick"),
                    }
                    out_f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                    kept += 1
                    by_date[date] += 1
                    by_intent[intent_type] += 1
                    by_session_kept[session_id] += 1
                    if len(samples) < 5:
                        samples.append(pair)

    # Stats
    print("=" * 60)
    print(f"Source: {len(decision_files)} decisions.jsonl files")
    print(f"Cutoff: sessions >= {args.cutoff}" + (" (disabled)" if args.keep_pre_cutoff else ""))
    print(f"Output: {out_path}")
    print("-" * 60)
    print(f"Total raw lines:        {total_lines}")
    print(f"  drop pre-cutoff:      {drop_pre_cutoff}")
    print(f"  drop bad JSON:        {drop_bad_json}")
    print(f"  drop no nl_input:     {drop_no_nl}")
    print(f"  drop ok=false:        {drop_not_ok}")
    print(f"KEPT:                   {kept}")
    print("-" * 60)
    print("By date:")
    for d, n in sorted(by_date.items()):
        print(f"  {d}: {n}")
    print("-" * 60)
    print("By intent_type (top 20):")
    for it, n in by_intent.most_common(20):
        print(f"  {n:5d}  {it}")
    print("-" * 60)
    print(f"Sessions with kept pairs: {len(by_session_kept)}")
    if by_session_kept:
        sizes = sorted(by_session_kept.values(), reverse=True)
        print(f"  largest:  {sizes[0]} pairs")
        print(f"  median:   {sizes[len(sizes) // 2]} pairs")
        print(f"  smallest: {sizes[-1]} pairs")
    print("-" * 60)
    print("Sample (first 5 kept):")
    for s in samples:
        nl = s["nl"][:60]
        it = s["intent_type"]
        print(f"  [{it:18s}] {nl}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
