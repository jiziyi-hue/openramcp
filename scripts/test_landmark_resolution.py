"""Unit test: coordless command resolution (no game needed).

Feeds a fake get_state through a mock transport and checks that the
interpreter resolves named targets / landmarks into concrete coordinates
itself — i.e. the LLM provides only a NAME, the interpreter computes the
(x,y) and the unit_ids. This validates the new map-landmark support.

Run:
    python scripts/test_landmark_resolution.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import interpreter as I  # noqa: E402

# Fake world: map 85x92. Self has 2 combat units + 1 harvester + 1 building.
# Enemy has a fact + 2 units centred around (64,22).
FAKE_STATE = {
    "ok": True,
    "state": {
        "tick": 1000,
        "map_size": {"x": 85, "y": 92},
        "self_units": [
            {"id": 187, "kind": "e1",   "pos": {"x": 30, "y": 70}, "hp_pct": 1.0},
            {"id": 188, "kind": "3tnk", "pos": {"x": 32, "y": 72}, "hp_pct": 0.9},
            {"id": 189, "kind": "harv", "pos": {"x": 28, "y": 68}, "hp_pct": 1.0},
            {"id": 190, "kind": "fact", "pos": {"x": 25, "y": 75}, "hp_pct": 1.0},
            {"id": 191, "kind": "mcv",  "pos": {"x": 26, "y": 74}, "hp_pct": 1.0},
        ],
        "enemy_units": [
            {"id": 500, "kind": "fact", "pos": {"x": 62, "y": 20}, "hp_pct": 1.0},
            {"id": 501, "kind": "e1",   "pos": {"x": 66, "y": 24}, "hp_pct": 1.0},
            {"id": 502, "kind": "3tnk", "pos": {"x": 64, "y": 22}, "hp_pct": 1.0},
        ],
    },
}


class MockTransport:
    """Stands in for the TCP bridge — no game required."""

    def __init__(self):
        self.spawned: list[dict] = []

    def send_command(self, payload: dict) -> dict:
        t = payload.get("type")
        if t == "get_state":
            return FAKE_STATE
        if t == "spawn_squad":
            self.spawned.append(payload)
            ids = payload.get("unit_ids", [])
            return {"ok": True, "squad_index": len(self.spawned) - 1,
                    "squad_type": payload.get("squad_type"),
                    "unit_count": len(ids), "auto_selected": not ids}
        return {"ok": True}


COMBAT_FILTER = {"kind": "filter", "owner": "self", "combat_mobile": True}


def run_case(name: str, target_named: str, expect_pos):
    mock = MockTransport()
    payload = {
        "intent": "attack",
        "force": COMBAT_FILTER,
        "target": {"kind": "named", "name": target_named},
    }
    resp = I.interpret(payload, mock)
    if not resp.get("ok"):
        return (name, False, f"interpret failed: {resp.get('error')}", None, None)
    if not mock.spawned:
        return (name, False, "no squad spawned", None, None)
    sq = mock.spawned[0]
    got_pos = sq.get("target_pos")
    got_ids = sq.get("unit_ids")
    ok = got_pos == {"x": expect_pos[0], "y": expect_pos[1]}
    return (name, ok, "", got_pos, got_ids)


def main() -> int:
    # Expected coordinates the INTERPRETER should compute (LLM gives none):
    #   enemy_base    -> centroid of enemy units = ((62+66+64)/3,(20+24+22)/3)=(64,22)
    #   enemy_fact    -> the enemy fact at (62,20)
    #   map_center    -> (85//2, 92//2) = (42,46)
    #   map_corner_se -> (85-0.15*85, 92-0.15*92) = (72,78)
    #   map_corner_nw -> (0.15*85, 0.15*92) = (12,13)
    cases = [
        ("attack enemy_base",    "enemy_base",    (64, 22)),
        ("attack enemy_fact",    "enemy_fact",    (62, 20)),
        ("attack map_center",    "map_center",    (42, 46)),
        ("attack map_corner_se", "map_corner_se", (72, 78)),
        ("attack map_corner_nw", "map_corner_nw", (12, 13)),
    ]

    print("=" * 64)
    print("Coordless resolution test (mock state, no game)")
    print("LLM sends only a NAME; interpreter must compute (x,y) + unit_ids")
    print("=" * 64)
    all_ok = True
    for name, target_named, expect in cases:
        nm, ok, err, got_pos, got_ids = run_case(name, target_named, expect)
        mark = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        detail = f"got {got_pos}" + (f"  err={err}" if err else "")
        print(f"  [{mark}] {nm:22s} expect {expect}  {detail}")
        if got_ids is not None:
            print(f"         unit_ids resolved by interpreter: {got_ids} "
                  f"(LLM provided none)")

    # Sanity: combat_mobile filter must drop harvester(189) + building(190)
    mock = MockTransport()
    wv = I.WorldView(mock)
    import mcp_server.intent_dsl as D
    ids = wv.resolve_force(D.ForceByFilter(combat_mobile=True))
    filter_ok = set(ids) == {187, 188}
    all_ok = all_ok and filter_ok
    print(f"  [{'PASS' if filter_ok else 'FAIL'}] combat_mobile filter      "
          f"expect [188,187] got {ids} (harv/fact excluded)")

    # --- coordless squad intents (defend/harass/scout/patrol/escort) ---
    print("-" * 64)
    print("Coordless squad intents -> spawn_squad (interpreter resolves):")

    def run_squad(payload, squad_type, check):
        mock = MockTransport()
        resp = I.interpret(payload, mock)
        if not resp.get("ok") or not mock.spawned:
            return False, f"failed: {resp.get('error')}"
        sq = mock.spawned[0]
        if sq.get("squad_type") != squad_type:
            return False, f"squad_type {sq.get('squad_type')} != {squad_type}"
        return check(sq), str({k: sq.get(k) for k in
                               ("target_pos", "waypoints", "escortee_actor_id")
                               if sq.get(k) is not None})

    F = {"kind": "filter", "owner": "self", "combat_mobile": True}
    squad_cases = [
        ("defend self_base", {"intent": "defend", "force": F,
            "where": {"kind": "named", "name": "self_base"}}, "Protection",
            lambda s: s.get("target_pos") == {"x": 25, "y": 75}),
        ("defend map_center", {"intent": "defend", "force": F,
            "where": {"kind": "named", "name": "map_center"}}, "Protection",
            lambda s: s.get("target_pos") == {"x": 42, "y": 46}),
        ("harass enemy_base", {"intent": "harass", "force": F,
            "target": {"kind": "named", "name": "enemy_base"}}, "Harass",
            lambda s: s.get("target_pos") == {"x": 64, "y": 22}),
        ("scout enemy_base", {"intent": "scout", "force": F,
            "where": {"kind": "named", "name": "enemy_base"}}, "Explore",
            lambda s: s.get("target_pos") == {"x": 64, "y": 22}),
        ("patrol east_lane", {"intent": "patrol", "force": F,
            "route": "east_lane"}, "Patrol",
            lambda s: bool(s.get("waypoints")) and s["waypoints"][0]["x"] > 60),
        ("patrol base_perimeter", {"intent": "patrol", "force": F,
            "route": "base_perimeter"}, "Patrol",
            lambda s: len(s.get("waypoints", [])) == 4),
        ("escort mcv", {"intent": "escort", "force": F,
            "escortee": "mcv"}, "Escort",
            lambda s: s.get("escortee_actor_id") == 191),
        ("escort harvester", {"intent": "escort", "force": F,
            "escortee": "harvester"}, "Escort",
            lambda s: s.get("escortee_actor_id") == 189),
    ]
    for name, payload, st, check in squad_cases:
        ok, detail = run_squad(payload, st, check)
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:22s} {detail}")

    print("=" * 64)
    print("RESULT:", "ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
