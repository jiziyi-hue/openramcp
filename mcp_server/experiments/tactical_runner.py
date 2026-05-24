"""
Tactical A/B runner — run one scenario under daemon and squad backends,
record metrics, write a CSV row per (scenario, backend, run).

Usage:
  python -m mcp_server.experiments.tactical_runner \
      --scenario T1_massive_push --backend squad --run-id 1 \
      --duration 60 --out logs/tactical_ab.csv

The script:
  1. Snapshots get_state (initial roster, positions, alive count).
  2. Resolves the intent template (fills escortee_id, etc).
  3. Issues the intent under the requested backend.
  4. Polls get_state at 1Hz until verdict_max_duration_s.
  5. Computes metrics and appends a row to the CSV.

The human is responsible for arranging the sandbox roster beforehand
(/instantbuild + manual training). The runner does NOT spawn units.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Optional

from mcp_server.transport import OpenRATransport
from mcp_server.experiments.tactical_scenarios import TACTICAL_SCENARIOS


def _send(t: OpenRATransport, cmd: str, **kw) -> dict:
    payload = {"type": cmd}
    payload.update(kw)
    return t.send_command(payload)


def _get_state(t: OpenRATransport) -> dict:
    return _send(t, "get_state", include_enemies=True)


def _self_units(state: dict) -> list:
    return state.get("state", {}).get("self_units", [])


def _alive_combat(self_units: list) -> list:
    """Approximate combat units — exclude buildings, harv, mcv."""
    excluded = {"fact", "powr", "apwr", "proc", "silo", "barr", "tent",
                "weap", "hpad", "afld", "syrd", "spen", "dome", "fix",
                "stek", "atek", "ftur", "pbox", "hbox", "gun", "agun",
                "sam", "tsla", "iron", "pdox", "gap", "mslo", "harv",
                "mcv", "oilb"}
    return [u for u in self_units if u.get("kind") not in excluded]


def _pos(u: dict) -> tuple[float, float]:
    p = u.get("pos", {})
    return float(p.get("x", 0)), float(p.get("y", 0))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _resolve_named_pos(t: OpenRATransport, name: str) -> Optional[tuple[float, float]]:
    """Use get_state to find the cell for a named target."""
    state = _get_state(t)
    if not state.get("ok"):
        return None
    enemy = state["state"].get("enemy_units", [])
    self_ = state["state"].get("self_units", [])

    if name == "enemy_fact":
        ef = next((u for u in enemy if u.get("kind") == "fact"), None)
        return _pos(ef) if ef else None
    if name == "enemy_base":
        bs = [_pos(u) for u in enemy if u.get("kind") in ("fact", "powr", "proc")]
        if bs:
            return (sum(p[0] for p in bs) / len(bs), sum(p[1] for p in bs) / len(bs))
        return None
    if name == "self_base":
        sf = next((u for u in self_ if u.get("kind") == "fact"), None)
        return _pos(sf) if sf else None
    return None


def _resolve_intent(scenario: dict, t: OpenRATransport) -> dict:
    """Patch the intent template with live state (e.g. escortee_id)."""
    intent = json.loads(json.dumps(scenario["intent_daemon"]))  # deep copy

    # Escort needs a real actor id from state.
    if intent.get("intent") == "escort" and intent.get("escortee_id") == -1:
        state = _get_state(t)
        self_units = _self_units(state.get("state", {}))
        mcv = next((u for u in self_units if u.get("kind") == "mcv"), None)
        if mcv is None:
            # Fall back to first non-combat heavy unit.
            mcv = next((u for u in self_units if u.get("kind") in ("4tnk", "3tnk")), None)
        if mcv is not None:
            intent["escortee_id"] = int(mcv["id"])

    return intent


def _run_daemon_backend(t: OpenRATransport, scenario: dict, intent_id: str) -> dict:
    """Single-intent path."""
    if "intent_daemon_batch" in scenario:
        # Compound: send each through dispatch_intent.
        ok_any = False
        for sub in scenario["intent_daemon_batch"]:
            resp = _send(t, "dispatch_intent", intent=sub)
            ok_any = ok_any or resp.get("ok", False)
        return {"ok": ok_any, "compound": True}

    intent = _resolve_intent(scenario, t)
    intent["backend"] = "daemon"
    return _send(t, "dispatch_intent", intent=intent)


def _run_squad_backend(t: OpenRATransport, scenario: dict, intent_id: str) -> dict:
    """Squad path: invoke spawn_squad directly with the squad_type from the
    scenario, picking up target_pos / waypoints / escortee as needed."""
    squad_type = scenario["intent_squad_type"]
    payload = {"type": "spawn_squad", "squad_type": squad_type}

    # Map verdict target into target_pos when present.
    tname = scenario.get("verdict_target_named")
    tpos = scenario.get("verdict_target_pos")
    if tpos is not None:
        payload["target_pos"] = {"x": tpos[0], "y": tpos[1]}
    elif tname is not None:
        pos = _resolve_named_pos(t, tname)
        if pos is not None:
            payload["target_pos"] = {"x": int(pos[0]), "y": int(pos[1])}

    # Harass squad wants rally_point.
    if squad_type == "Harass":
        rally = _resolve_named_pos(t, "self_base")
        if rally is not None:
            payload["rally_point"] = {"x": int(rally[0]), "y": int(rally[1])}

    # Escort: escortee actor id.
    if squad_type == "Escort":
        state = _get_state(t)
        units = _self_units(state.get("state", {}))
        mcv = next((u for u in units if u.get("kind") == "mcv"), None)
        if mcv is None:
            mcv = next((u for u in units if u.get("kind") in ("4tnk", "3tnk")), None)
        if mcv is not None:
            payload["escortee_actor_id"] = int(mcv["id"])

    return t.send_command(payload)


def _measure(t: OpenRATransport, scenario: dict, initial: dict,
             max_duration_s: float, interval_s: float = 1.0) -> dict:
    """Poll get_state until duration elapses or target reached."""
    target_pos = scenario.get("verdict_target_pos")
    if target_pos is None and scenario.get("verdict_target_named"):
        target_pos = _resolve_named_pos(t, scenario["verdict_target_named"])
    if target_pos is None:
        target_pos = (0, 0)  # fallback; won't trigger early stop

    radius = scenario.get("verdict_arrival_radius", 8)
    initial_combat = _alive_combat(_self_units(initial["state"]))
    initial_alive = len(initial_combat)
    initial_ids = {u["id"] for u in initial_combat}
    initial_dist = (sum(_dist(_pos(u), target_pos) for u in initial_combat)
                    / max(1, initial_alive))

    samples = []
    deadline = time.time() + max_duration_s
    reached = False
    while time.time() < deadline:
        time.sleep(interval_s)
        st = _get_state(t)
        if not st.get("ok"):
            continue
        combat = _alive_combat(_self_units(st["state"]))
        # Track only units that existed at start (avoid newly-trained noise).
        tracked = [u for u in combat if u["id"] in initial_ids]
        if not tracked:
            break
        dists = [_dist(_pos(u), target_pos) for u in tracked]
        mean_dist = sum(dists) / len(dists)
        min_dist = min(dists)
        samples.append({
            "tick": st["state"].get("tick"),
            "wall_t": round(time.time(), 2),
            "alive": len(tracked),
            "mean_dist": round(mean_dist, 2),
            "min_dist": round(min_dist, 2),
        })
        if min_dist <= radius:
            reached = True
            break

    final_alive = samples[-1]["alive"] if samples else 0
    final_mean = samples[-1]["mean_dist"] if samples else initial_dist
    duration = samples[-1]["wall_t"] - samples[0]["wall_t"] if len(samples) >= 2 else 0.0

    return {
        "reached_target": reached,
        "initial_alive": initial_alive,
        "final_alive": final_alive,
        "units_lost": initial_alive - final_alive,
        "initial_mean_dist": round(initial_dist, 2),
        "final_mean_dist": final_mean,
        "delta_mean_dist": round(initial_dist - final_mean, 2),
        "duration_s": round(duration, 2),
        "samples_count": len(samples),
    }


def run_once(scenario_id: str, backend: str, run_id: int, duration_s: float,
             out_csv: Path) -> dict:
    if scenario_id not in TACTICAL_SCENARIOS:
        return {"ok": False, "error": f"unknown scenario {scenario_id}"}
    scenario = TACTICAL_SCENARIOS[scenario_id]

    t = OpenRATransport()
    if not t.connect():
        return {"ok": False, "error": "bridge not connected"}

    initial = _get_state(t)
    if not initial.get("ok"):
        return {"ok": False, "error": "initial get_state failed"}

    initial_combat = _alive_combat(_self_units(initial["state"]))
    if len(initial_combat) < scenario["expected_roster_min"]:
        return {
            "ok": False,
            "error": f"roster too small: have {len(initial_combat)}, "
                     f"need ≥ {scenario['expected_roster_min']} "
                     f"({', '.join(scenario['suggested_roster'])})",
        }

    # Clean slate: cancel any existing squads + assaults to avoid bleed.
    t.send_command({"type": "cancel_squad"})
    t.send_command({"type": "cancel_assaults"})
    time.sleep(0.5)

    # Issue command.
    intent_id = f"{scenario_id}-{backend}-r{run_id}"
    t0 = time.time()
    if backend == "daemon":
        dispatch_resp = _run_daemon_backend(t, scenario, intent_id)
    elif backend == "squad":
        dispatch_resp = _run_squad_backend(t, scenario, intent_id)
    else:
        return {"ok": False, "error": f"unknown backend {backend}"}

    t1 = time.time()
    dispatch_latency_s = round(t1 - t0, 3)

    # Refresh initial after the dispatch (state may have advanced).
    measure_initial = _get_state(t)

    max_d = min(duration_s, scenario.get("verdict_max_duration_s", 90))
    metrics = _measure(t, scenario, measure_initial, max_d)

    row = {
        "scenario_id": scenario_id,
        "backend": backend,
        "run_id": run_id,
        "dispatch_ok": dispatch_resp.get("ok", False),
        "dispatch_latency_s": dispatch_latency_s,
        **metrics,
    }

    # Write CSV (append, write header if new).
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out_csv.exists()
    with out_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new_file:
            writer.writeheader()
        writer.writerow(row)

    return {"ok": True, "row": row}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=list(TACTICAL_SCENARIOS.keys()))
    ap.add_argument("--backend", choices=["daemon", "squad"])
    ap.add_argument("--run-id", type=int, default=1)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--out", type=str, default="logs/tactical_ab.csv")
    ap.add_argument("--list", action="store_true",
                    help="List scenarios and exit")
    args = ap.parse_args()

    if args.list:
        from mcp_server.experiments.tactical_scenarios import list_scenarios
        for k, v in list_scenarios().items():
            print(f"  {k}: {v}")
        return

    if not args.scenario or not args.backend:
        ap.error("--scenario and --backend required (unless --list)")

    result = run_once(args.scenario, args.backend, args.run_id,
                      args.duration, Path(args.out))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
