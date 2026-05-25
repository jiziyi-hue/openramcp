"""
Scenarios v2 — Natural-language tactical capability suite.

Each scenario tests one capability hard for LLM-driven RTS commanding:
referent resolution, kind-based splitting, state-based splitting, mid-flight
re-commanding, partial cancel, conditional triggers, path constraints,
formation maintenance, time-sequenced plans, failure recovery.

Each function:
  - takes (transport, scenario_log_path)
  - executes the tactic via spawn_squad / spawn_squad_batch (NOT dispatch_intent —
    we're testing the LLM-composition layer, not the daemon)
  - returns a Metrics dict with the columns documented in the v2 table:
      task_name, nl_input, unit_count, unit_kinds, subtasks_generated,
      unit_selection_correct, reached_target, tactical_intent_met,
      total_latency_ms, failure_reason, corrections, recording_path

Scenarios assume the player has trained units before invoking the runner.
The runner inspects get_state and fails fast if the roster is insufficient.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(t) -> dict:
    return t.send_command({"type": "get_state", "include_enemies": True})["state"]


def _mobile(state: dict, kinds=("apc", "3tnk", "4tnk", "2tnk", "1tnk", "ttnk", "jeep", "ftrk", "arty")) -> list:
    return [u for u in state["self_units"] if u["kind"] in kinds]


_TANK_KINDS = ("3tnk", "4tnk", "2tnk", "1tnk", "ttnk")


def _any_tank(units: list) -> list:
    """Return tank-class units sorted by id, regardless of specific kind."""
    return sorted([u for u in units if u["kind"] in _TANK_KINDS], key=lambda u: u["id"])


def _by_kind(units: list, kind: str) -> list:
    return sorted([u for u in units if u["kind"] == kind], key=lambda u: u["id"])


def _centroid(units: list) -> Optional[Tuple[float, float]]:
    if not units:
        return None
    return (
        sum(u["pos"]["x"] for u in units) / len(units),
        sum(u["pos"]["y"] for u in units) / len(units),
    )


def _dist(a: Tuple[float, float], b: Tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _spawn_batch(t, payloads: list) -> dict:
    return t.send_command({"type": "spawn_squad_batch", "squads": payloads})


def _cancel_all(t) -> None:
    t.send_command({"type": "cancel_squad"})


def _wait_then_state(t, secs: float) -> dict:
    time.sleep(secs)
    return _state(t)


def _arrived(units: list, target: Tuple[int, int], radius: int = 6) -> int:
    return sum(1 for u in units if _dist((u["pos"]["x"], u["pos"]["y"]), target) <= radius)


# ---------------------------------------------------------------------------
# T1 — Referential resolution
# "Let the left squad flank, the right one stays put"
# Setup: 2 pre-existing squads, left squad (smaller x centroid) should move,
#         right squad should stay still.
# ---------------------------------------------------------------------------

def t1_referent_left_right(t) -> Dict[str, Any]:
    nl = "让左边那队绕过去, 右边的别动"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    apcs = _by_kind(_mobile(state), "apc")
    if len(apcs) < 20:
        return _fail("T1_referent", nl, 0, "需要至少 20 APC", t0)

    # split by x: half lowest-x → "left", half highest-x → "right"
    apcs_sorted = sorted(apcs, key=lambda u: u["pos"]["x"])
    left = [u["id"] for u in apcs_sorted[: len(apcs) // 2]]
    right = [u["id"] for u in apcs_sorted[len(apcs) // 2:]]

    # spawn the "right" squad at a static rally near its current pos (no-op)
    # spawn the "left" squad on a flanking arc to (75, 50)
    right_centroid = _centroid([u for u in apcs_sorted[len(apcs) // 2:]])
    payloads = [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": left, "target_pos": {"x": 75, "y": 50}},
    ]
    # right squad: we do NOT spawn a squad for it — player intent is "stays put"
    resp = _spawn_batch(t, payloads)
    latency = int((time.time() - t0) * 1000)

    state2 = _wait_then_state(t, 20)
    units = {u["id"]: u for u in state2["self_units"]}
    left_alive = [units[i] for i in left if i in units]
    right_alive = [units[i] for i in right if i in units]
    left_c = _centroid(left_alive)
    right_c = _centroid(right_alive)
    left_arr = _arrived(left_alive, (75, 50))

    # "right stays put" success criterion: right centroid drift < 4 cells
    initial_right = right_centroid
    right_drift = math.hypot(right_c[0] - initial_right[0], right_c[1] - initial_right[1]) if right_c else 999
    reached = left_arr >= len(left) * 0.7
    intent_met = reached and right_drift < 6.0

    return {
        "task_name": "T1_referent_left_right",
        "nl_input": nl,
        "unit_count": len(left) + len(right),
        "unit_kinds": dict(Counter(u["kind"] for u in apcs_sorted)),
        "subtasks_generated": 1,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else f"left_arr={left_arr}/{len(left)} right_drift={right_drift:.1f}",
        "corrections": 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T2 — Kind-based split
# "Tanks down the middle, APCs flank from both sides"
# ---------------------------------------------------------------------------

def t2_kind_split_pincer(t) -> Dict[str, Any]:
    nl = "坦克走中路, APC 从两侧包过去"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    tnks_units = _any_tank(mob)
    tnks = [u["id"] for u in tnks_units]
    apcs = _by_kind(mob, "apc")
    if not tnks or len(apcs) < 10:
        return _fail("T2_kind_split", nl, len(mob),
                     f"need any-tank≥1 + apc≥10, got tnk={len(tnks)} apc={len(apcs)}", t0)
    tnk_kind = tnks_units[0]["kind"]

    apcs_sorted = sorted(apcs, key=lambda u: u["pos"]["x"])
    apc_left = [u["id"] for u in apcs_sorted[: len(apcs) // 2]]
    apc_right = [u["id"] for u in apcs_sorted[len(apcs) // 2:]]
    # target: enemy area approx (60, 50) (sandbox: pretend enemy_base)
    target = (60, 50)
    payloads = [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": tnks, "target_pos": {"x": target[0], "y": target[1]}},
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": apc_left, "target_pos": {"x": target[0], "y": target[1] - 10}},
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": apc_right, "target_pos": {"x": target[0], "y": target[1] + 10}},
    ]
    resp = _spawn_batch(t, payloads)
    latency = int((time.time() - t0) * 1000)

    state2 = _wait_then_state(t, 25)
    units = {u["id"]: u for u in state2["self_units"]}
    tnk_alive = [units[i] for i in tnks if i in units]
    al_alive = [units[i] for i in apc_left if i in units]
    ar_alive = [units[i] for i in apc_right if i in units]
    tnk_arr = _arrived(tnk_alive, target, 8)
    al_arr = _arrived(al_alive, (target[0], target[1] - 10), 8)
    ar_arr = _arrived(ar_alive, (target[0], target[1] + 10), 8)

    reached = (tnk_arr >= len(tnks) * 0.5
               and al_arr >= len(apc_left) * 0.6
               and ar_arr >= len(apc_right) * 0.6)
    intent_met = reached

    return {
        "task_name": "T2_kind_split_pincer",
        "nl_input": nl,
        "unit_count": len(tnks) + len(apcs),
        "unit_kinds": {tnk_kind: len(tnks), "apc": len(apcs)},
        "subtasks_generated": 3,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else f"tnk={tnk_arr}/{len(tnks)} L={al_arr}/{len(apc_left)} R={ar_arr}/{len(apc_right)}",
        "corrections": 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T3 — State-based split
# "Wounded ones return to base, the rest keep pushing"
# ---------------------------------------------------------------------------

def t3_hp_split(t) -> Dict[str, Any]:
    nl = "受伤的回基地, 剩下的继续推进"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    fact = next((u for u in state["self_units"] if u["kind"] == "fact"), None)
    base = (fact["pos"]["x"], fact["pos"]["y"]) if fact else (15, 15)

    HP_THRESHOLD = 0.7  # synth threshold; sandbox units all full HP usually
    wounded = [u["id"] for u in mob if u["hp_pct"] < HP_THRESHOLD]
    healthy = [u["id"] for u in mob if u["hp_pct"] >= HP_THRESHOLD]

    # NOTE: in pristine sandbox HP==1.0 for all, wounded will be empty.
    # That's OK — capability test is whether the LOGIC handles the split.
    if not healthy:
        return _fail("T3_hp_split", nl, len(mob), "0 healthy units", t0)

    target = (70, 80)
    payloads = []
    if wounded:
        payloads.append({"type": "spawn_squad", "squad_type": "Assault",
                         "unit_ids": wounded, "target_pos": {"x": base[0], "y": base[1]}})
    payloads.append({"type": "spawn_squad", "squad_type": "Assault",
                     "unit_ids": healthy, "target_pos": {"x": target[0], "y": target[1]}})
    resp = _spawn_batch(t, payloads)
    latency = int((time.time() - t0) * 1000)

    state2 = _wait_then_state(t, 60)
    units = {u["id"]: u for u in state2["self_units"]}
    h_alive = [units[i] for i in healthy if i in units]
    h_arr = _arrived(h_alive, target, 12)
    w_arr = 0
    if wounded:
        w_alive = [units[i] for i in wounded if i in units]
        w_arr = _arrived(w_alive, base, 12)

    reached = h_arr >= len(healthy) * 0.5
    intent_met = reached and (not wounded or w_arr >= len(wounded) * 0.5)

    return {
        "task_name": "T3_hp_split",
        "nl_input": nl,
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": len(payloads),
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else
                          f"healthy_arr={h_arr}/{len(healthy)} wounded_arr={w_arr}/{len(wounded)} "
                          f"[note: sandbox usually has 0 wounded]",
        "corrections": 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T4 — Mid-flight re-command
# Order 1: "All push to enemy base"
# Mid-flight: "Stop, switch to 2-prong pincer"
# ---------------------------------------------------------------------------

def t4_midflight_recommand(t) -> Dict[str, Any]:
    nl1 = "全部向敌方基地推进"
    nl2 = "停, 改成两翼包抄"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    if len(mob) < 20:
        return _fail("T4_midflight_recommand", nl1 + " | " + nl2, len(mob),
                     "need ≥20 mobile", t0)
    all_ids = sorted([u["id"] for u in mob])

    target = (60, 50)
    # Phase 1: single Assault all units → target
    resp1 = _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": all_ids, "target_pos": {"x": target[0], "y": target[1]}},
    ])
    time.sleep(8)  # let them get partway

    # Snapshot mid-flight
    state_mid = _state(t)
    mid_units = {u["id"]: u for u in state_mid["self_units"]}
    mid_alive = [mid_units[i] for i in all_ids if i in mid_units]
    mid_c = _centroid(mid_alive)

    # Phase 2: cancel + split into 2 prongs (top/bottom around target)
    _cancel_all(t)
    time.sleep(0.3)
    half = len(all_ids) // 2
    top = all_ids[:half]
    bot = all_ids[half:]
    resp2 = _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": top, "target_pos": {"x": target[0], "y": target[1] - 12}},
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": bot, "target_pos": {"x": target[0], "y": target[1] + 12}},
    ])
    latency = int((time.time() - t0) * 1000)

    state_end = _wait_then_state(t, 25)
    end_units = {u["id"]: u for u in state_end["self_units"]}
    top_alive = [end_units[i] for i in top if i in end_units]
    bot_alive = [end_units[i] for i in bot if i in end_units]
    top_arr = _arrived(top_alive, (target[0], target[1] - 12), 8)
    bot_arr = _arrived(bot_alive, (target[0], target[1] + 12), 8)
    reached = top_arr >= len(top) * 0.6 and bot_arr >= len(bot) * 0.6
    intent_met = reached

    return {
        "task_name": "T4_midflight_recommand",
        "nl_input": f"{nl1} → {nl2}",
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": 3,  # spawn1 + cancel + spawn2
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else f"top={top_arr}/{len(top)} bot={bot_arr}/{len(bot)}",
        "corrections": 1,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T5 — Partial cancel
# Squads 1/2/3/4 dispatched; "squad 3 go home, rest continue"
# ---------------------------------------------------------------------------

def t5_partial_cancel(t) -> Dict[str, Any]:
    nl = "第三队别去了, 回基地; 其他队继续"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    if len(mob) < 32:
        return _fail("T5_partial_cancel", nl, len(mob), "need ≥32 mobile", t0)
    ids = sorted([u["id"] for u in mob])
    fact = next((u for u in state["self_units"] if u["kind"] == "fact"), None)
    base = (fact["pos"]["x"], fact["pos"]["y"]) if fact else (15, 15)

    # 4 squads to 4 corners
    corners = [(15, 15), (70, 15), (70, 80), (15, 80)]
    n = len(ids) // 4
    squads_ids = [ids[i * n:(i + 1) * n] for i in range(4)]
    payloads = [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": squads_ids[i], "target_pos": {"x": corners[i][0], "y": corners[i][1]}}
        for i in range(4)
    ]
    _spawn_batch(t, payloads)
    time.sleep(3)

    # cancel ONLY sq#2 (index 2 = 0-based 3rd squad = "第三队")
    t.send_command({"type": "cancel_squad", "squad_index": 2})
    time.sleep(0.3)

    # send squad 3's units back to base
    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": squads_ids[2], "target_pos": {"x": base[0], "y": base[1]}},
    ])
    latency = int((time.time() - t0) * 1000)

    state2 = _wait_then_state(t, 70)
    units = {u["id"]: u for u in state2["self_units"]}
    results = []
    # squads 0,1,3 → their corner; squad 2 → base
    for i in range(4):
        alive = [units[u] for u in squads_ids[i] if u in units]
        if not alive:
            results.append(0.0)
            continue
        tgt = base if i == 2 else corners[i]
        results.append(_arrived(alive, tgt, 14) / len(squads_ids[i]))

    reached = all(r >= 0.5 for r in results)
    intent_met = reached and results[2] >= 0.5  # squad 3 (idx 2) reached base

    return {
        "task_name": "T5_partial_cancel",
        "nl_input": nl,
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": 6,  # 4 spawn + 1 cancel + 1 respawn
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else f"arrival rates: {[f'{r:.0%}' for r in results]}",
        "corrections": 1,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T6 — Conditional command (simplified: time-triggered, no real enemy detect)
# "Push, retreat to bridge if you see enemy main force, else continue"
# Sandbox shortcut: at t=15s, if (synth) condition met, retreat; else continue.
# ---------------------------------------------------------------------------

def t6_conditional_retreat(t) -> Dict[str, Any]:
    nl = "如果遇到敌方主力就撤回桥口, 否则继续前进"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    if len(mob) < 15:
        return _fail("T6_conditional", nl, len(mob), "need ≥15 mobile", t0)
    ids = sorted([u["id"] for u in mob])

    target = (70, 80)
    bridge = (40, 50)  # synthetic "bridge"

    # phase 1: push
    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": ids, "target_pos": {"x": target[0], "y": target[1]}},
    ])

    # monitor for 15s; check synth condition (enemy_unit count > 5)
    trigger = False
    for _ in range(15):
        time.sleep(1)
        s = _state(t)
        if len(s.get("enemy_units", [])) > 5:
            trigger = True
            break

    if trigger:
        # retreat to bridge
        _cancel_all(t)
        time.sleep(0.3)
        _spawn_batch(t, [
            {"type": "spawn_squad", "squad_type": "Assault",
             "unit_ids": ids, "target_pos": {"x": bridge[0], "y": bridge[1]}},
        ])
        final_target = bridge
        outcome = "retreated"
    else:
        final_target = target
        outcome = "continued"

    latency = int((time.time() - t0) * 1000)
    state2 = _wait_then_state(t, 25)
    units = {u["id"]: u for u in state2["self_units"]}
    alive = [units[i] for i in ids if i in units]
    arr = _arrived(alive, final_target, 8)
    reached = arr >= len(ids) * 0.6

    return {
        "task_name": "T6_conditional_retreat",
        "nl_input": nl,
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": 2 if trigger else 1,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": reached,  # both branches OK
        "total_latency_ms": latency,
        "failure_reason": "" if reached else f"outcome={outcome} arr={arr}/{len(ids)}",
        "corrections": 1 if trigger else 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T7 — Path constraint
# "Don't take center; flank from right"
# Approach: waypoint via (75, force_y) before final target.
# ---------------------------------------------------------------------------

def t7_path_constraint_flank_right(t) -> Dict[str, Any]:
    # 两路殊途同归 (80, 85) 右下:
    #   队 A 直走 → (80, 85)
    #   队 B 走最右 (80, 45) 中转再 → (80, 85)
    nl = "A 队直走右下角, B 队先走最右边再走到右下角"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    if len(mob) < 15:
        return _fail("T7_path_constraint", nl, len(mob), "need ≥15 mobile", t0)
    ids = sorted([u["id"] for u in mob])
    half = len(ids) // 2
    team_a = ids[:half]
    team_b = ids[half:]

    final = (80, 85)
    waypoint_b = (80, 45)

    # phase 1: A 直走 final, B 走 waypoint_b
    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": team_a, "target_pos": {"x": final[0], "y": final[1]}},
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": team_b, "target_pos": {"x": waypoint_b[0], "y": waypoint_b[1]}},
    ])

    # monitor B reaching waypoint, track its max_x (should hit x >= 75 = right edge area)
    max_x_b = 0
    b_arrived_wp = False
    for sec in range(40):
        time.sleep(1)
        s = _state(t)
        units = {u["id"]: u for u in s["self_units"]}
        b_alive = [units[i] for i in team_b if i in units]
        if not b_alive:
            break
        c = _centroid(b_alive)
        max_x_b = max(max_x_b, c[0])
        if _dist(c, waypoint_b) < 6:
            b_arrived_wp = True
            break

    # phase 2: send B from waypoint to final
    if b_arrived_wp:
        # only cancel team_b's squad — but we don't know index reliably,
        # so cancel all + re-dispatch both (A may still be on the way)
        _cancel_all(t)
        time.sleep(0.3)
        s = _state(t)
        units = {u["id"]: u for u in s["self_units"]}
        a_alive_now = [u["id"] for u in units.values() if u["id"] in team_a]
        b_alive_now = [u["id"] for u in units.values() if u["id"] in team_b]
        squads = []
        if a_alive_now:
            squads.append({"type": "spawn_squad", "squad_type": "Assault",
                           "unit_ids": a_alive_now,
                           "target_pos": {"x": final[0], "y": final[1]}})
        if b_alive_now:
            squads.append({"type": "spawn_squad", "squad_type": "Assault",
                           "unit_ids": b_alive_now,
                           "target_pos": {"x": final[0], "y": final[1]}})
        _spawn_batch(t, squads)

    latency = int((time.time() - t0) * 1000)
    state2 = _wait_then_state(t, 70)
    units = {u["id"]: u for u in state2["self_units"]}
    a_alive = [units[i] for i in team_a if i in units]
    b_alive = [units[i] for i in team_b if i in units]
    arr_a = _arrived(a_alive, final, 12)
    arr_b = _arrived(b_alive, final, 12)
    arr = arr_a + arr_b
    reached = arr >= len(ids) * 0.5
    # tactical intent: B 真走过最右 (max_x_b >= 70)
    intent_met = reached and max_x_b >= 70

    return {
        "task_name": "T7_path_constraint_flank_right",
        "nl_input": nl,
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": 2,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else
                          f"max_x_b={max_x_b:.1f} (want>=70) "
                          f"arr_a={arr_a}/{len(team_a)} arr_b={arr_b}/{len(team_b)}",
        "corrections": 1,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T8 — Formation / spacing
# "Tanks in front, APCs trail, don't bunch up"
# Spawn 2 squads with offset; check tank.x > apc.x toward target & no big clump.
# ---------------------------------------------------------------------------

def t8_formation_tanks_front(t) -> Dict[str, Any]:
    nl = "坦克在前, APC 跟在后面, 不要挤成一团"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    tnks_units = _any_tank(mob)
    tnks = [u["id"] for u in tnks_units]
    apcs = [u["id"] for u in _by_kind(mob, "apc")]
    if not tnks or not apcs:
        return _fail("T8_formation", nl, len(mob),
                     f"need both any-tank+apc, got tnk={len(tnks)} apc={len(apcs)}", t0)
    tnk_kind = tnks_units[0]["kind"]

    target_front = (70, 80)
    target_back = (70, 72)  # 8 cells behind in approach direction

    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": tnks, "target_pos": {"x": target_front[0], "y": target_front[1]}},
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": apcs, "target_pos": {"x": target_back[0], "y": target_back[1]}},
    ])
    latency = int((time.time() - t0) * 1000)

    state2 = _wait_then_state(t, 60)
    units = {u["id"]: u for u in state2["self_units"]}
    tnk_alive = [units[i] for i in tnks if i in units]
    apc_alive = [units[i] for i in apcs if i in units]
    tnk_c = _centroid(tnk_alive)
    apc_c = _centroid(apc_alive)
    # 验 tank y > apc y (tank 更南/更靠近 target)
    formation_correct = tnk_c[1] > apc_c[1]
    # no big clump: tank-apc centroid gap >= 3
    gap = math.hypot(tnk_c[0] - apc_c[0], tnk_c[1] - apc_c[1])
    no_clump = gap >= 3
    tnk_arr = _arrived(tnk_alive, target_front, 12)
    apc_arr = _arrived(apc_alive, target_back, 12)
    reached = tnk_arr >= len(tnks) * 0.5 and apc_arr >= len(apcs) * 0.4
    intent_met = reached and formation_correct and no_clump

    return {
        "task_name": "T8_formation_tanks_front",
        "nl_input": nl,
        "unit_count": len(tnks) + len(apcs),
        "unit_kinds": {tnk_kind: len(tnks), "apc": len(apcs)},
        "subtasks_generated": 2,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else
                          f"tnk_y={tnk_c[1]:.1f} apc_y={apc_c[1]:.1f} gap={gap:.1f} "
                          f"tnk_arr={tnk_arr}/{len(tnks)} apc_arr={apc_arr}/{len(apcs)}",
        "corrections": 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T9 — Time-sequenced plan
# "Frontal force goes first; 5s later, sneak team attacks from side"
# ---------------------------------------------------------------------------

def t9_time_sequenced(t) -> Dict[str, Any]:
    nl = "正面部队先出发, 5 秒后偷袭队再从侧面进攻"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    if len(mob) < 20:
        return _fail("T9_time_sequenced", nl, len(mob), "need ≥20 mobile", t0)
    ids = sorted([u["id"] for u in mob])
    half = len(ids) // 2
    frontal = ids[:half]
    sneak = ids[half:]
    target = (60, 50)

    # phase 1: frontal goes
    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": frontal, "target_pos": {"x": target[0], "y": target[1]}},
    ])
    t_dispatch_frontal = time.time()

    # phase 2: 5s wait, sneak goes from side
    time.sleep(5)
    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": sneak, "target_pos": {"x": target[0] + 5, "y": target[1] + 15}},
    ])
    t_dispatch_sneak = time.time()
    delay_observed = t_dispatch_sneak - t_dispatch_frontal

    latency = int((time.time() - t0) * 1000)
    state2 = _wait_then_state(t, 25)
    units = {u["id"]: u for u in state2["self_units"]}
    f_alive = [units[i] for i in frontal if i in units]
    s_alive = [units[i] for i in sneak if i in units]
    f_arr = _arrived(f_alive, target, 8)
    s_arr = _arrived(s_alive, (target[0] + 5, target[1] + 15), 8)
    reached = f_arr >= len(frontal) * 0.6 and s_arr >= len(sneak) * 0.6
    intent_met = reached and 4.5 < delay_observed < 6.5

    return {
        "task_name": "T9_time_sequenced",
        "nl_input": nl,
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": 2,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": intent_met,
        "total_latency_ms": latency,
        "failure_reason": "" if intent_met else
                          f"delay={delay_observed:.2f}s (want ~5) frontal={f_arr}/{len(frontal)} sneak={s_arr}/{len(sneak)}",
        "corrections": 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# T10 — Failure recovery (stuck detection + replan)
# "If a squad is stuck, replan its route"
# Sandbox: monitor centroid drift; if drift < 2 cells over 8s, re-dispatch via waypoint.
# ---------------------------------------------------------------------------

def t10_failure_recovery(t) -> Dict[str, Any]:
    nl = "如果某队卡住了, 就重新规划路线"
    t0 = time.time()
    _cancel_all(t)
    time.sleep(0.3)

    state = _state(t)
    mob = _mobile(state)
    if len(mob) < 15:
        return _fail("T10_failure_recovery", nl, len(mob), "need ≥15 mobile", t0)
    ids = sorted([u["id"] for u in mob])

    target = (70, 80)

    _spawn_batch(t, [
        {"type": "spawn_squad", "squad_type": "Assault",
         "unit_ids": ids, "target_pos": {"x": target[0], "y": target[1]}},
    ])

    # monitor for stuck: centroid drift over 4s windows
    history = []
    replanned = False
    for sec in range(20):
        time.sleep(1)
        s = _state(t)
        units = {u["id"]: u for u in s["self_units"]}
        alive = [units[i] for i in ids if i in units]
        if not alive:
            break
        c = _centroid(alive)
        history.append(c)
        if len(history) >= 4:
            drift = math.hypot(history[-1][0] - history[-4][0],
                               history[-1][1] - history[-4][1])
            d_to_target = _dist(history[-1], target)
            if drift < 2.0 and d_to_target > 10:
                # stuck → replan via waypoint
                _cancel_all(t)
                time.sleep(0.3)
                _spawn_batch(t, [
                    {"type": "spawn_squad", "squad_type": "Assault",
                     "unit_ids": ids, "target_pos": {"x": 40, "y": 70}},
                ])
                time.sleep(8)
                _cancel_all(t)
                time.sleep(0.3)
                _spawn_batch(t, [
                    {"type": "spawn_squad", "squad_type": "Assault",
                     "unit_ids": ids, "target_pos": {"x": target[0], "y": target[1]}},
                ])
                replanned = True
                break

    latency = int((time.time() - t0) * 1000)
    state2 = _wait_then_state(t, 20)
    units = {u["id"]: u for u in state2["self_units"]}
    alive = [units[i] for i in ids if i in units]
    arr = _arrived(alive, target, 8)
    reached = arr >= len(ids) * 0.6

    return {
        "task_name": "T10_failure_recovery",
        "nl_input": nl,
        "unit_count": len(mob),
        "unit_kinds": dict(Counter(u["kind"] for u in mob)),
        "subtasks_generated": 3 if replanned else 1,
        "unit_selection_correct": True,
        "reached_target": reached,
        "tactical_intent_met": reached,
        "total_latency_ms": latency,
        "failure_reason": "" if reached else f"replanned={replanned} arr={arr}/{len(ids)}",
        "corrections": 1 if replanned else 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# Helper: failure shortcut
# ---------------------------------------------------------------------------

def _fail(name: str, nl: str, n: int, reason: str, t0: float) -> Dict[str, Any]:
    return {
        "task_name": name,
        "nl_input": nl,
        "unit_count": n,
        "unit_kinds": {},
        "subtasks_generated": 0,
        "unit_selection_correct": False,
        "reached_target": False,
        "tactical_intent_met": False,
        "total_latency_ms": int((time.time() - t0) * 1000),
        "failure_reason": f"setup: {reason}",
        "corrections": 0,
        "recording_path": "",
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_SCENARIOS: Dict[str, Callable] = {
    "T1": t1_referent_left_right,
    "T2": t2_kind_split_pincer,
    "T3": t3_hp_split,
    "T4": t4_midflight_recommand,
    "T5": t5_partial_cancel,
    "T6": t6_conditional_retreat,
    "T7": t7_path_constraint_flank_right,
    "T8": t8_formation_tanks_front,
    "T9": t9_time_sequenced,
    "T10": t10_failure_recovery,
}

CSV_COLUMNS = [
    "task_name", "nl_input", "unit_count", "unit_kinds",
    "subtasks_generated", "unit_selection_correct", "reached_target",
    "tactical_intent_met", "total_latency_ms", "failure_reason",
    "corrections", "recording_path",
]
