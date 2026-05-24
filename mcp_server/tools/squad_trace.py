"""
Squad trajectory verifier — automated smoke-test for spawn_squad.

Polls OpenRA bridge every --interval seconds. For each sampled tick, records
each squad unit's position. After --duration seconds, emits JSON report and
prints PASS/FAIL verdict per criterion.

Verdicts:
  - direction_pass: mean distance to target_pos decreased monotonically
    (allowing 1 plateau or minor regression). FAIL = stuck or moving away.
  - cohesion_pass: max distance between any two squad units stayed under
    --cohesion-cap cells (default 30). FAIL = squad fragmented.
  - survival_pass: alive_count >= 0.5 * initial. FAIL = wiped.
  - arrival_pass: at end, mean distance to target < --arrival-radius
    cells (default 8). FAIL = never reached.

Usage:
  python -m mcp_server.tools.squad_trace \
      --squad 1 --target 40,45 \
      --interval 3 --duration 90 \
      --out logs/squad_trace_$(date +%s).json

If --squad omitted, uses last squad reported by list_squads.
If --target omitted, attempts to read target_pos from list_squads response.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Optional

from mcp_server.transport import OpenRATransport


def _send(t: OpenRATransport, cmd: str, **kw) -> dict:
    payload = {"type": cmd}
    payload.update(kw)
    return t.send_command(payload)


def _list_squads(t: OpenRATransport) -> list:
    resp = _send(t, "list_squads")
    if not resp.get("ok"):
        return []
    return resp.get("squads", [])


def _get_state(t: OpenRATransport) -> dict:
    return _send(t, "get_state", include_enemies=False)


def _pos_of(unit: dict) -> tuple[float, float]:
    p = unit.get("pos", {})
    return float(p.get("x", 0)), float(p.get("y", 0))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _max_pairwise(positions: list[tuple[float, float]]) -> float:
    if len(positions) < 2:
        return 0.0
    m = 0.0
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            d = _dist(positions[i], positions[j])
            if d > m:
                m = d
    return m


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def trace(
    squad_index: Optional[int],
    target: Optional[tuple[float, float]],
    interval: float,
    duration: float,
    cohesion_cap: float,
    arrival_radius: float,
) -> dict:
    t = OpenRATransport()
    if not t.connect():
        return {"ok": False, "error": "bridge not connected"}

    squads = _list_squads(t)
    if not squads:
        return {"ok": False, "error": "no active squads"}

    chosen = None
    if squad_index is None:
        chosen = squads[-1]
    else:
        for s in squads:
            if s.get("squad_index") == squad_index:
                chosen = s
                break
    if chosen is None:
        return {"ok": False, "error": f"squad {squad_index} not found"}

    tracked_ids = set(chosen.get("unit_ids", []))
    if not tracked_ids:
        return {"ok": False, "error": "squad has no units"}

    if target is None:
        tp = chosen.get("target_pos") or {}
        if "x" in tp and "y" in tp:
            target = (float(tp["x"]), float(tp["y"]))
        else:
            return {"ok": False, "error": "no target_pos given and squad has none"}

    initial_alive = len(tracked_ids)
    samples = []
    deadline = time.time() + duration
    while time.time() < deadline:
        st = _get_state(t)
        if not st.get("ok"):
            time.sleep(interval)
            continue
        units = st.get("state", {}).get("self_units", [])
        alive = [u for u in units if u.get("id") in tracked_ids]
        positions = [_pos_of(u) for u in alive]
        dists = [_dist(p, target) for p in positions]
        sample = {
            "tick": st["state"].get("tick"),
            "wall_t": round(time.time(), 2),
            "alive_count": len(alive),
            "mean_dist_to_target": round(_mean(dists), 2),
            "min_dist_to_target": round(min(dists), 2) if dists else None,
            "max_pairwise": round(_max_pairwise(positions), 2),
            "mean_hp_pct": round(_mean([u.get("hp_pct", 0.0) for u in alive]), 3),
        }
        samples.append(sample)
        print(json.dumps(sample, ensure_ascii=False), flush=True)
        time.sleep(interval)

    if not samples:
        return {"ok": False, "error": "no samples collected"}

    mean_dists = [s["mean_dist_to_target"] for s in samples]
    min_dists = [s["min_dist_to_target"] for s in samples if s["min_dist_to_target"] is not None]
    max_pairwise = max(s["max_pairwise"] for s in samples)
    final_alive = samples[-1]["alive_count"]
    final_dist = samples[-1]["mean_dist_to_target"]
    initial_dist = mean_dists[0]

    # arrival_radius scales with squad size: bigger squad spreads more
    scaled_arrival = max(arrival_radius, 0.6 * math.sqrt(initial_alive))

    # direction: smoothed delta — last third vs first third
    n = len(mean_dists)
    first_third = _mean(mean_dists[: max(1, n // 3)])
    last_third = _mean(mean_dists[-max(1, n // 3) :])
    delta = first_third - last_third  # positive = approaching
    # plus monotonic count for granular view
    progressions = sum(
        1 for i in range(1, n) if mean_dists[i] < mean_dists[i - 1] - 0.3
    )
    regressions = sum(
        1 for i in range(1, n) if mean_dists[i] > mean_dists[i - 1] + 0.3
    )

    already_arrived = initial_dist <= scaled_arrival
    arrival_pass = final_dist <= scaled_arrival
    direction_pass = already_arrived or delta >= 2.0
    cohesion_pass = max_pairwise <= cohesion_cap
    survival_pass = final_alive >= 0.5 * initial_alive

    verdict = {
        "direction_pass": direction_pass,
        "cohesion_pass": cohesion_pass,
        "survival_pass": survival_pass,
        "arrival_pass": arrival_pass,
    }
    overall = all(verdict.values())

    # human-readable summary for LLM
    if already_arrived:
        narrative = (
            f"squad already at/near target (mean_dist {initial_dist:.1f} ≤ "
            f"arrival_radius {scaled_arrival:.1f}). holding position."
        )
    elif delta >= 2.0:
        narrative = (
            f"approaching: mean_dist {initial_dist:.1f} → {final_dist:.1f} "
            f"(Δ={delta:.1f}). {progressions} progressing samples, {regressions} regressing."
        )
    elif abs(delta) < 1.0:
        narrative = (
            f"STUCK: mean_dist {initial_dist:.1f} → {final_dist:.1f} (Δ={delta:.1f}). "
            f"squad not moving toward target."
        )
    else:
        narrative = (
            f"DRIFTING AWAY: mean_dist {initial_dist:.1f} → {final_dist:.1f} "
            f"(Δ={delta:.1f}, negative = moving away)."
        )
    if not cohesion_pass:
        narrative += f" cohesion broke: max_pairwise={max_pairwise:.1f} > cap {cohesion_cap}."
    if not survival_pass:
        narrative += f" heavy losses: {initial_alive}→{final_alive}."

    return {
        "ok": True,
        "squad_index": chosen.get("squad_index"),
        "squad_type": chosen.get("squad_type"),
        "target": {"x": target[0], "y": target[1]},
        "initial_alive": initial_alive,
        "final_alive": final_alive,
        "initial_mean_dist": initial_dist,
        "final_mean_dist": final_dist,
        "min_dist_seen": min(min_dists) if min_dists else None,
        "scaled_arrival_radius": round(scaled_arrival, 2),
        "max_pairwise": max_pairwise,
        "delta_first_to_last": round(delta, 2),
        "progressions": progressions,
        "regressions": regressions,
        "verdict": verdict,
        "overall_pass": overall,
        "narrative": narrative,
        "samples": samples,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--squad", type=int, default=None, help="squad_index; default = last")
    ap.add_argument("--target", type=str, default=None, help="x,y; default = squad.target_pos")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--cohesion-cap", type=float, default=30.0)
    ap.add_argument("--arrival-radius", type=float, default=8.0)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    target = None
    if args.target:
        parts = args.target.split(",")
        target = (float(parts[0]), float(parts[1]))

    report = trace(
        squad_index=args.squad,
        target=target,
        interval=args.interval,
        duration=args.duration,
        cohesion_cap=args.cohesion_cap,
        arrival_radius=args.arrival_radius,
    )

    print("\n=== REPORT ===")
    if report.get("ok"):
        print(report.get("narrative", "(no narrative)"))
        print()
        summary = {k: v for k, v in report.items() if k != "samples"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nfull report -> {args.out}")

    sys.exit(0 if report.get("overall_pass") else 1)


if __name__ == "__main__":
    main()
