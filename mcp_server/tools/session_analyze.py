"""
Offline analyzer for session_recorder JSONL output.

Reads a recording produced by session_recorder.py and computes metrics
useful for paper data:

  - Per-squad: trajectory, alive_count over time, position centroid,
    units_lost, mean_dist to nearest enemy fact.
  - Aggregate: peak_concurrent_squads, amplification (units_per_squad),
    enemy_units_lost over time.
  - Per-second roll-up CSV for figures.

Usage:
  python -m mcp_server.tools.session_analyze \
      --in logs/recording_xxx.jsonl --target-named enemy_fact \
      --out logs/recording_xxx.summary.json

Output: a JSON summary + a roll-up CSV next to it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional


def _dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _enemy_fact_pos(sample) -> Optional[tuple[float, float]]:
    for u in sample.get("enemy_units", []):
        if u["kind"] == "fact":
            return (u["x"], u["y"])
    return None


def analyze(in_path: Path, out_path: Path):
    samples = []
    with in_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))

    if not samples:
        print("empty log")
        return

    duration_s = samples[-1]["wall_t"] - samples[0]["wall_t"]
    first_tick = samples[0].get("tick")
    last_tick = samples[-1].get("tick")

    # Per-squad timeline
    squad_history: dict[int, list] = defaultdict(list)
    peak_concurrent = 0
    for s in samples:
        squads = s.get("squads", [])
        peak_concurrent = max(peak_concurrent, len(squads))
        unit_pos = {u["id"]: (u["x"], u["y"], u["hp"]) for u in s["self_units"]}
        fact = _enemy_fact_pos(s)
        for sq in squads:
            idx = sq.get("squad_index")
            uids = sq.get("unit_ids", [])
            alive_pos = [unit_pos[u] for u in uids if u in unit_pos]
            if not alive_pos:
                continue
            cx = sum(p[0] for p in alive_pos) / len(alive_pos)
            cy = sum(p[1] for p in alive_pos) / len(alive_pos)
            dist = _dist(cx, cy, fact[0], fact[1]) if fact else None
            squad_history[idx].append({
                "wall_t": s["wall_t"],
                "tick": s.get("tick"),
                "alive": len(alive_pos),
                "centroid_x": round(cx, 2),
                "centroid_y": round(cy, 2),
                "dist_to_fact": round(dist, 2) if dist is not None else None,
            })

    # Per-squad summary
    per_squad = []
    for idx in sorted(squad_history.keys()):
        h = squad_history[idx]
        first = h[0]
        last = h[-1]
        dists = [p["dist_to_fact"] for p in h if p["dist_to_fact"] is not None]
        per_squad.append({
            "squad_index": idx,
            "samples": len(h),
            "initial_alive": first["alive"],
            "final_alive": last["alive"],
            "lost": first["alive"] - last["alive"],
            "initial_dist": first.get("dist_to_fact"),
            "final_dist": last.get("dist_to_fact"),
            "advance": round((first.get("dist_to_fact") or 0) - (last.get("dist_to_fact") or 0), 2) if first.get("dist_to_fact") else None,
            "min_dist_seen": round(min(dists), 2) if dists else None,
        })

    # Per-second roll-up CSV
    roll_csv = out_path.with_suffix(".rollup.csv")
    with roll_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sample_idx", "wall_t", "tick", "self_count", "enemy_count",
                    "active_squads", "total_squad_units"])
        for s in samples:
            squads = s.get("squads", [])
            total_units = sum(len(sq.get("unit_ids", [])) for sq in squads)
            w.writerow([
                s["sample_idx"], s["wall_t"], s.get("tick"),
                len(s.get("self_units", [])),
                len(s.get("enemy_units", [])),
                len(squads), total_units,
            ])

    summary = {
        "duration_s": round(duration_s, 2),
        "first_tick": first_tick,
        "last_tick": last_tick,
        "samples_total": len(samples),
        "peak_concurrent_squads": peak_concurrent,
        "per_squad": per_squad,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"summary → {out_path}")
    print(f"rollup  → {roll_csv}")
    print(json.dumps({
        "peak_concurrent_squads": peak_concurrent,
        "squads_seen": len(per_squad),
        "duration_s": summary["duration_s"],
    }, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    analyze(Path(args.in_path), Path(args.out))


if __name__ == "__main__":
    main()
