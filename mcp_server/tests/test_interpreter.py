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
    def test_attack_frontal_building_uses_attack_move(self):
        # Building target → attack_move (not focus-attack actor) so units
        # engage enemy mobile units en route. Regression guard for the
        # suicide-into-base bug fixed in interpreter._do_attack.
        t = MockTransport()
        r = I.interpret({
            "intent": "attack",
            "force": {"kind": "ids", "unit_ids": [2]},
            "target": {"kind": "named", "name": "enemy_fact"},
        }, t)
        self.assertTrue(r["ok"])
        # No focus-fire Attack on building.
        attack_cmds = [c for c in t.commands if c.get("type") == "attack"]
        self.assertEqual(len(attack_cmds), 0)
        # Move with attack_move=True to building location.
        move_cmds = [c for c in t.commands if c.get("type") == "move"]
        self.assertEqual(len(move_cmds), 1)
        self.assertTrue(move_cmds[0]["attack_move"])
        self.assertEqual(move_cmds[0]["target"], {"x": 67, "y": 9})

    def test_attack_frontal_unit_uses_focus_attack(self):
        # Mobile unit target → still uses direct Attack actor (correct path).
        t = MockTransport()
        # Add a tank target to mock world.
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
        attack_cmds = [c for c in t.commands if c.get("type") == "attack"]
        self.assertEqual(len(attack_cmds), 1)
        self.assertEqual(attack_cmds[0]["target_id"], 88)

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

    def test_attack_charge_building_uses_attack_move(self):
        # Regression: previously charge focused on building actor and units
        # ignored enemy fire. Now charge on building uses attack_move + stance.
        t = MockTransport()
        r = I.interpret({
            "intent": "attack",
            "force": {"kind": "ids", "unit_ids": [2]},
            "target": {"kind": "named", "name": "enemy_fact"},
            "approach": "charge",
        }, t)
        self.assertTrue(r["ok"])
        # AttackAnything stance set.
        stance_cmds = [c for c in t.commands if c.get("type") == "set_stance"]
        self.assertEqual(len(stance_cmds), 1)
        self.assertEqual(stance_cmds[0]["stance"], "AttackAnything")
        # Move (attack_move) issued, not Attack-actor.
        attack_cmds = [c for c in t.commands if c.get("type") == "attack"]
        self.assertEqual(len(attack_cmds), 0)
        move_cmds = [c for c in t.commands if c.get("type") == "move"]
        self.assertEqual(len(move_cmds), 1)
        self.assertTrue(move_cmds[0]["attack_move"])

    def test_set_strategy_intent_rejected(self):
        """set_strategy intent removed in 2026-05-23 refactor."""
        t = MockTransport()
        r = I.interpret({"intent": "set_strategy", "template": "tank_rush"}, t)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
