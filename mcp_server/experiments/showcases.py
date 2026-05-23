"""10 superhuman-operation showcases for the openra_mcp paper.

Each showcase is a scripted scenario that demonstrates a capability a solo
human player cannot easily achieve in the same time window. We run it via the
MCP transport with a fixed NL command (or DSL payload) and record:

    - the single NL command issued
    - the resulting DSL intent
    - the count of atomic orders emitted by the interpreter
    - timing
    - replay file for visual confirmation

These are the paper's flagship case studies. Run after a game has started
and the McpBridge is alive on 127.0.0.1:7777.

Run:
    python -m mcp_server.experiments.showcases --list
    python -m mcp_server.experiments.showcases --id 1
    python -m mcp_server.experiments.showcases --all
"""

from __future__ import annotations
import argparse
import json
import os
import time
from pathlib import Path
from typing import Callable, Any

from ..transport import OpenRATransport
from ..interpreter import interpret
from ..logging import SessionLogger


# Each showcase: (id, name, nl_phrase, build_intent_fn, why_human_hard)
# build_intent_fn(world) → intent_dict. world is the get_state response.

def _ids(world: dict, predicate: Callable[[dict], bool], limit: int = None) -> list[int]:
    out = []
    for u in world.get("state", {}).get("self_units", []):
        if predicate(u):
            out.append(u["id"])
            if limit is not None and len(out) >= limit:
                break
    return out


def _build_1(world: dict) -> dict:
    """8-way simultaneous scout — split self forces 8 ways."""
    # Use 8 scout intents to 8 rect regions covering the map perimeter.
    # We emit as a list under intent='raw' so one set of atomic orders fans out.
    return {
        "intent": "raw",
        "atomic_calls": [
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 10,  "y": 10},  "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 60,  "y": 10},  "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 110, "y": 10},  "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 110, "y": 60},  "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 110, "y": 110}, "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 60,  "y": 110}, "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 10,  "y": 110}, "attack_move": True},
            {"type": "move", "unit_ids": _ids(world, lambda u: True, limit=1) or [],
             "target": {"x": 10,  "y": 60},  "attack_move": True},
        ],
    }


def _build_2(world: dict) -> dict:
    """残血全员回家 — retreat all units below 30% HP."""
    return {
        "intent": "retreat",
        "force": {"kind": "filter", "owner": "self", "hp_below": 0.3},
        "to": {"kind": "named", "name": "self_base"},
    }


def _build_3(world: dict) -> dict:
    """切 turtle 现在 — instant template switch with hard transition."""
    return {
        "intent": "set_strategy",
        "template": "turtle",
        "defense_state": "full_alert",
        "spend_ratio": "eco_heavy",
        "transition_mode": "hard",
    }


def _build_4(world: dict) -> dict:
    """三路夹击敌主基地 — pincer + center push."""
    return {
        "intent": "pincer",
        "left":  {"kind": "group", "name": "north"},
        "right": {"kind": "group", "name": "south"},
        "target": {"kind": "named", "name": "enemy_fact"},
        "rendezvous_dist": 8,
    }


def _build_5(world: dict) -> dict:
    """所有坦克突击敌总部 — filter by kind + charge."""
    return {
        "intent": "attack",
        "force": {"kind": "filter", "owner": "self", "unit_kind": "2tnk"},
        "target": {"kind": "named", "name": "enemy_fact"},
        "approach": "charge",
        "urgency": "urgent",
    }


def _build_6(world: dict) -> dict:
    """模板热切换到 raid_harass + 设置骚扰焦点."""
    return {
        "intent": "set_strategy",
        "template": "raid_harass",
        "harass_focus": {"kind": "named", "name": "enemy_base"},
        "transition_mode": "hybrid",
        "spend_ratio": "army_heavy",
    }


def _build_7(world: dict) -> dict:
    """佯攻中央, 主力北翼包抄 — set_strategy attack_focus + manual feint."""
    return {
        "intent": "feint",
        "force": {"kind": "group", "name": "center"},
        "target": {"kind": "named", "name": "enemy_base"},
    }


def _build_8(world: dict) -> dict:
    """全员散开 (空袭警报) — scatter all."""
    ids = _ids(world, lambda u: True)
    return {
        "intent": "raw",
        "atomic_calls": [
            {"type": "scatter", "unit_ids": ids},
            {"type": "set_stance", "unit_ids": ids, "stance": "HoldFire"},
        ],
    }


def _build_9(world: dict) -> dict:
    """全员推 + 全 AttackAnything — coordinated charge."""
    return {
        "intent": "attack",
        "force": {"kind": "group", "name": "all"},
        "target": {"kind": "named", "name": "enemy_fact"},
        "approach": "charge",
        "urgency": "urgent",
    }


def _build_10(world: dict) -> dict:
    """全员守家 + 全部到 perimeter — defense_state full_alert + defend region."""
    return {
        "intent": "defend",
        "force": {"kind": "group", "name": "all"},
        "region": {"kind": "around", "center": "self_base", "radius": 12},
        "stance": "Defend",
    }


SHOWCASES = [
    (1,  "8-way simultaneous scout",
     "派 8 路侦察, 全图一次扫", _build_1,
     "Requires 8 distinct cell clicks in <1s; humans serialize them."),
    (2,  "Retreat all low-HP",
     "残血全员回家", _build_2,
     "Per-unit health visual scan + individual retreat clicks."),
    (3,  "Instant turtle switch",
     "切 turtle 现在", _build_3,
     "Switching doctrine mid-game is multi-screen action; here it's 1 NL."),
    (4,  "Three-front pincer",
     "南北钳形, 包敌总部", _build_4,
     "Synchronized 2-arm convergence with timing — humans rarely coordinate."),
    (5,  "All tanks charge enemy HQ",
     "所有坦克突击敌总部, 不要管步兵", _build_5,
     "Filter by unit type without manually selecting each tank."),
    (6,  "Hot-swap to raid_harass with focus",
     "切骚扰流, 焦点敌基地", _build_6,
     "Doctrine + posture + harass target in one step."),
    (7,  "Feint center while main flanks",
     "中央佯攻牵制", _build_7,
     "Two simultaneous coordinated maneuvers (here feint half)."),
    (8,  "Air-raid evade — scatter then ready",
     "全员散开 (空袭)", _build_8,
     "Reflex split-then-collect; usually loses several units."),
    (9,  "Mass coordinated charge",
     "全员冲敌总部", _build_9,
     "Stance + attack + grouping in one NL."),
    (10, "All-units perimeter defense",
     "全员守家", _build_10,
     "Region selection + stance change on every unit."),
]


def run(showcase_id: int, transport: OpenRATransport) -> dict:
    sc = next((s for s in SHOWCASES if s[0] == showcase_id), None)
    if sc is None:
        return {"ok": False, "error": f"unknown showcase id {showcase_id}"}

    id_, name, nl, builder, why = sc
    world = transport.send_command({"type": "get_state", "include_enemies": True})
    intent = builder(world)
    t0 = time.perf_counter()
    result = interpret(intent, transport)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    entry = {
        "showcase_id": id_,
        "name": name,
        "nl_input": nl,
        "why_human_hard": why,
        "intent": intent,
        "ok": result.get("ok"),
        "narrative": result.get("narrative"),
        "atomic_order_count": len(result.get("actions_taken", []) or []),
        "latency_ms": latency_ms,
        "timestamp": time.time(),
    }
    # Persist alongside the session log.
    log_dir = SessionLogger.current().dir
    showcases_dir = log_dir / "showcases"
    showcases_dir.mkdir(parents=True, exist_ok=True)
    (showcases_dir / f"showcase_{id_:02d}.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list showcases and exit")
    ap.add_argument("--id", type=int, default=None, help="run one showcase by id")
    ap.add_argument("--all", action="store_true", help="run all 10 in order")
    ap.add_argument("--host", default=os.environ.get("OPENRA_BRIDGE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("OPENRA_BRIDGE_PORT", "7777")))
    args = ap.parse_args()

    if args.list:
        for id_, name, nl, _b, why in SHOWCASES:
            print(f"  {id_:2d}. {name:38s}  NL: {nl!r}")
            print(f"       why: {why}")
        return

    transport = OpenRATransport(host=args.host, port=args.port)

    targets = []
    if args.id:
        targets = [args.id]
    elif args.all:
        targets = [s[0] for s in SHOWCASES]
    else:
        print("nothing to do — pass --list, --id N, or --all")
        return

    summary = []
    for sid in targets:
        result = run(sid, transport)
        ok_str = "✓" if result.get("ok") else "✗"
        print(f"{ok_str} #{sid:2d} {result.get('name', '?'):38s} "
              f"atomic={result.get('atomic_order_count', 0):3d} "
              f"latency={result.get('latency_ms', 0):4d}ms")
        summary.append(result)

    if len(summary) > 1:
        total_atomic = sum(s.get("atomic_order_count", 0) for s in summary)
        ok_count = sum(1 for s in summary if s.get("ok"))
        print()
        print(f"== {ok_count}/{len(summary)} ok, total {total_atomic} atomic orders ==")
        print(f"   logs at: {SessionLogger.current().dir / 'showcases'}")


if __name__ == "__main__":
    main()
