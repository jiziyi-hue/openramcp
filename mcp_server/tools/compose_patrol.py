"""
compose_patrol — LLM-side patrol composition demo.

Instead of a Patrol squad FSM, we use Assault squads + Python-side
sequencing. 4 squads each follow their own waypoint cycle; when one
arrives at the current waypoint (centroid within 4 cells), we advance
that squad's cursor and re-batch a new spawn_squad command. All four
re-spawns ride in a single spawn_squad_batch call so they look
simultaneous in-game.

This is the "tactical primitives + LLM composition" pattern: the only
engine-side FSM is Assault. Loops, timings, and waypoint sequences live
in this Python helper (the LLM stand-in).
"""

from __future__ import annotations

import argparse
import math
import time
from typing import Optional

from mcp_server.transport import OpenRATransport


def _send(t: OpenRATransport, cmd: str, **kw) -> dict:
    payload = {"type": cmd}
    payload.update(kw)
    return t.send_command(payload)


def _centroid(units: list) -> Optional[tuple[float, float]]:
    if not units:
        return None
    return (
        sum(u["pos"]["x"] for u in units) / len(units),
        sum(u["pos"]["y"] for u in units) / len(units),
    )


def _dist(a: tuple[float, float], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="apc")
    ap.add_argument("--per-squad", type=int, default=10)
    ap.add_argument("--num-squads", type=int, default=4)
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--arrival-radius", type=int, default=4)
    args = ap.parse_args()

    # Four corners, each squad cycles around them clockwise starting from
    # its own corner.
    corners = [(20, 20), (65, 20), (65, 65), (20, 65)]
    if args.num_squads != 4:
        raise SystemExit("demo hardcoded to 4 squads")

    t = OpenRATransport()
    if not t.connect():
        raise SystemExit("bridge not connected")

    state = _send(t, "get_state", include_enemies=False)
    pool = sorted(
        [u["id"] for u in state["state"]["self_units"] if u["kind"] == args.kind],
    )
    if len(pool) < args.per_squad * args.num_squads:
        raise SystemExit(
            f"need {args.per_squad * args.num_squads} {args.kind}s, have {len(pool)}"
        )

    # Squad ids + per-squad waypoint cursor.
    squads = []
    for i in range(args.num_squads):
        ids = pool[i * args.per_squad:(i + 1) * args.per_squad]
        squads.append({
            "unit_ids": ids,
            "cursor": i,  # start at own corner
        })

    _send(t, "cancel_squad")
    time.sleep(0.3)

    # Initial batch: every squad → its current waypoint.
    payloads = [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": s["unit_ids"],
         "target_pos": {"x": corners[s["cursor"]][0], "y": corners[s["cursor"]][1]}}
        for s in squads
    ]
    resp = _send(t, "spawn_squad_batch", squads=payloads)
    print(f"initial batch: ok={resp.get('ok')} count={len(resp.get('results', []))}")
    for i, r in enumerate(resp.get("results", [])):
        wp = corners[squads[i]["cursor"]]
        print(f"  sq{i} → {wp} ok={r.get('ok')}")

    started = time.time()
    while time.time() - started < args.duration:
        time.sleep(2.0)

        # Resolve current positions.
        st = _send(t, "get_state", include_enemies=False)
        if not st.get("ok"):
            continue
        unit_lookup = {u["id"]: u for u in st["state"]["self_units"]}

        # Check arrivals; advance cursors as needed.
        to_rebatch = []
        for i, s in enumerate(squads):
            alive = [unit_lookup[u] for u in s["unit_ids"] if u in unit_lookup]
            if not alive:
                continue
            c = _centroid(alive)
            wp = corners[s["cursor"]]
            if _dist(c, wp) <= args.arrival_radius:
                s["cursor"] = (s["cursor"] + 1) % len(corners)
                to_rebatch.append(i)
                print(f"  sq{i} arrived @ {wp}, advance → {corners[s['cursor']]}")

        if to_rebatch:
            # Cancel + rebatch ALL squads to keep them in lockstep
            # (re-create the spawn list with each squad's current cursor).
            _send(t, "cancel_squad")
            time.sleep(0.2)
            payloads = [
                {"type": "spawn_squad", "squad_type": "Assault",
                 "unit_ids": s["unit_ids"],
                 "target_pos": {"x": corners[s["cursor"]][0], "y": corners[s["cursor"]][1]}}
                for s in squads
            ]
            r = _send(t, "spawn_squad_batch", squads=payloads)
            print(f"  rebatch: ok={r.get('ok')} for {len(squads)} squads")


if __name__ == "__main__":
    main()
