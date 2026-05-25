"""
cluster_dispatch — CLI to spatially cluster a unit list and spawn one
bot squad per cluster, each marching to a slightly jittered target.

Bypasses the MCP layer (talks directly to OpenRA via TCP) so we can
exercise the clustering logic without restarting Claude Code's stdio
MCP server.

Usage:
  python -m mcp_server.tools.cluster_dispatch \
      --kind apc --count 160 \
      --target 18,18 --target 18,108 --target 108,18 --target 108,108 \
      --cluster-size 20 --stagger-ms 250 --target-jitter 4
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


def _cluster_dispatch(t: OpenRATransport, ids: list[int], target: tuple[int, int],
                      squad_type: str, cluster_size: int,
                      target_jitter: int, stagger_ms: int,
                      tag: str = "") -> list[dict]:
    state = _send(t, "get_state", include_enemies=False)
    if not state.get("ok"):
        print("get_state failed")
        return []
    pos_map = {u["id"]: (u["pos"]["x"], u["pos"]["y"])
               for u in state["state"].get("self_units", [])}
    located = [(uid, pos_map[uid]) for uid in ids if uid in pos_map]
    if not located:
        print(f"[{tag}] no units found")
        return []

    n = len(located)
    k = max(1, math.ceil(n / max(1, cluster_size)))
    xs = [p[1][0] for p in located]
    ys = [p[1][1] for p in located]
    if (max(xs) - min(xs)) >= (max(ys) - min(ys)):
        located.sort(key=lambda p: p[1][0])
    else:
        located.sort(key=lambda p: p[1][1])

    per = math.ceil(n / k)
    chunks = [[p[0] for p in located[i * per:(i + 1) * per]] for i in range(k)]
    chunks = [c for c in chunks if c]

    # Build batch payload — all sub-squads spawned in one bridge handler call.
    tx, ty = target
    payloads = []
    for i, chunk in enumerate(chunks):
        angle = (2 * math.pi * i) / max(1, len(chunks))
        ox = int(round(target_jitter * math.cos(angle)))
        oy = int(round(target_jitter * math.sin(angle)))
        sub_target = {"x": tx + ox, "y": ty + oy}
        payloads.append({
            "type": "spawn_squad",
            "squad_type": squad_type,
            "unit_ids": chunk,
            "target_pos": sub_target,
        })

    resp = t.send_command({"type": "spawn_squad_batch", "squads": payloads})
    results = resp.get("results", []) if resp.get("ok") else []
    for i, r in enumerate(results):
        tp = payloads[i]["target_pos"]
        print(f"[{tag}] sub {i+1}/{len(payloads)} → ({tp['x']},{tp['y']}) "
              f"n={len(payloads[i]['unit_ids'])} ok={r.get('ok')} sq#{r.get('squad_index')}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="apc", help="unit kind filter (apc / 4tnk / e3 / ...)")
    ap.add_argument("--count", type=int, default=160, help="how many to pull from the pool")
    ap.add_argument("--target", action="append", required=True,
                    help="target as 'x,y' — repeat for each prong (e.g. 4 corners)")
    ap.add_argument("--squad-type", default="Assault")
    ap.add_argument("--cluster-size", type=int, default=20)
    ap.add_argument("--target-jitter", type=int, default=4)
    ap.add_argument("--stagger-ms", type=int, default=250)
    args = ap.parse_args()

    targets = []
    for s in args.target:
        x, y = s.split(",")
        targets.append((int(x), int(y)))

    t = OpenRATransport()
    if not t.connect():
        print("bridge not connected")
        return

    state = _send(t, "get_state", include_enemies=False)
    pool = sorted(
        [u["id"] for u in state["state"]["self_units"] if u["kind"] == args.kind],
    )[:args.count]
    if not pool:
        print(f"no '{args.kind}' units found")
        return

    per_target = math.ceil(len(pool) / len(targets))
    for ti, target in enumerate(targets):
        slice_ids = pool[ti * per_target:(ti + 1) * per_target]
        if not slice_ids:
            continue
        _cluster_dispatch(t, slice_ids, target, args.squad_type,
                          args.cluster_size, args.target_jitter, args.stagger_ms,
                          tag=f"target{ti+1}")


if __name__ == "__main__":
    main()
