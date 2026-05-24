"""Unit tests for interpreter.py — dispatch + Target resolution with mock transport."""

from __future__ import annotations
import unittest

from mcp_server import interpreter as I


class MockTransport:
    """Records sent commands; returns canned responses keyed by 'type'."""

    def __init__(self, world_state: dict | None = None, set_strategy_resp: dict | None = None):
        self.commands: list[dict] = []
        self.world = world_state or {
            "ok": True,
            "state": {
                "tick": 100,
                "self_cash": 1000,
                "self_power": 50,
                "self_units": [
                    {"id": 1, "kind": "fact", "owner": "Commander",
                     "pos": {"x": 14, "y": 79}, "hp_pct": 1.0},
                    {"id": 2, "kind": "2tnk", "owner": "Commander",
                     "pos": {"x": 20, "y": 80}, "hp_pct": 0.2},
                ],
                "enemy_units": [
                    {"id": 99, "kind": "fact", "owner": "bot",
                     "pos": {"x": 67, "y": 9}, "hp_pct": 1.0},
                ],
                "map_size": {"x": 128, "y": 128},
            },
        }
        self.set_strategy_resp = set_strategy_resp or {
            "ok": True,
            "applied": {},
            "rejected": {},
            "repurposed_units": 0,
            "strategy": {"template": "balanced"},
        }

    def send_command(self, cmd: dict) -> dict:
        self.commands.append(cmd)
        t = cmd.get("type")
        if t == "get_state":
            return self.world
        if t == "list_groups":
            return {"ok": True, "groups": []}
        if t == "set_strategy":
            r = dict(self.set_strategy_resp)
            r["applied"] = cmd.get("patch", {})
            return r
        if t == "get_strategy":
            return {"ok": True, "strategy": self.set_strategy_resp.get("strategy", {})}
        return {"ok": True, "issued_orders": 1, "affected_unit_ids": []}


class TestDispatch(unittest.TestCase):
    def test_attack_frontal_registers_assault_no_atomics(self):
        # Architectural guard: _do_attack must ONLY register a daemon
        # assault and NOT send move/attack/stance atomics. Daemon owns
        # per-tick unit control end-to-end.
        t = MockTransport()
        r = I.interpret({
            "intent": "attack",
            "force": {"kind": "ids", "unit_ids": [2]},
            "target": {"kind": "named", "name": "enemy_fact"},
        }, t)
        self.assertTrue(r["ok"])
        # Interpreter must not have issued any engine-side tactical command.
        engine_cmds = [c for c in t.commands
                       if c.get("type") in ("attack", "move", "set_stance")]
        self.assertEqual(engine_cmds, [])
        # Mission was registered.
        self.assertIn("mission_ids", r)
        self.assertTrue(len(r["mission_ids"]) >= 1)

    def test_attack_frontal_unit_target_still_registers(self):
        # Mobile unit target still goes through register_assault — daemon
        # picks the actor as final_target_actor and chases it.
        t = MockTransport()
        t.world["state"]["enemy_units"].append({
            "id": 88, "kind": "2tnk", "owner": "bot",
            "pos": {"x": 50, "y": 50}, "hp_pct": 1.0,
        })
        r = I.interpret({
            "intent": "attack",
            "force": {"kind": "ids", "unit_ids": [2]},
            "target": {"kind": "id", "actor_id": 88},
        }, t)
        self.assertTrue(r["ok"])
        engine_cmds = [c for c in t.commands
                       if c.get("type") in ("attack", "move", "set_stance")]
        self.assertEqual(engine_cmds, [])
        self.assertIn("mission_ids", r)

    def test_retreat_filter_hp(self):
        t = MockTransport()
        r = I.interpret({
            "intent": "retreat",
            "force": {"kind": "filter", "owner": "self", "hp_below": 0.3},
            "to": {"kind": "named", "name": "self_base"},
        }, t)
        self.assertTrue(r["ok"])
        # Should select unit 2 (hp 0.2 < 0.3), not unit 1.
        move_cmds = [c for c in t.commands if c.get("type") == "move"]
        self.assertEqual(len(move_cmds), 1)
        self.assertEqual(move_cmds[0]["unit_ids"], [2])

    def test_attack_charge_registers_with_cohesion_off(self):
        # Charge approach should still go through register_assault but with
        # cohesion=False so the daemon doesn't gate vanguards.
        t = MockTransport()
        r = I.interpret({
            "intent": "attack",
            "force": {"kind": "ids", "unit_ids": [2]},
            "target": {"kind": "named", "name": "enemy_fact"},
            "approach": "charge",
        }, t)
        self.assertTrue(r["ok"])
        engine_cmds = [c for c in t.commands
                       if c.get("type") in ("attack", "move", "set_stance")]
        self.assertEqual(engine_cmds, [])
        self.assertIn("mission_ids", r)

    def test_set_strategy_intent_rejected(self):
        """set_strategy intent removed in 2026-05-23 refactor."""
        t = MockTransport()
        r = I.interpret({"intent": "set_strategy", "template": "tank_rush"}, t)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
