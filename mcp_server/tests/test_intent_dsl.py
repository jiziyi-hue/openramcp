"""Unit tests for intent_dsl.py — parse correctness + regression guard.

Run:   python -m pytest mcp_server/tests/test_intent_dsl.py -v
Or:    python mcp_server/tests/test_intent_dsl.py   (uses unittest)
"""

from __future__ import annotations
import unittest

from mcp_server import intent_dsl as D


class TestParseBasicIntents(unittest.TestCase):
    def test_attack_minimal(self):
        i = D.parse_intent({
            "intent": "attack",
            "force": {"kind": "group", "name": "north"},
            "target": {"kind": "named", "name": "enemy_fact"},
        })
        self.assertEqual(i.intent, "attack")
        self.assertEqual(i.approach, "frontal")  # default
        self.assertEqual(i.urgency, "normal")

    def test_defend_with_region(self):
        i = D.parse_intent({
            "intent": "defend",
            "force": {"kind": "group", "name": "all"},
            "region": {"kind": "around", "center": "self_base", "radius": 12},
        })
        self.assertEqual(i.intent, "defend")
        self.assertEqual(i.stance, "Defend")

    def test_set_strategy_intent_rejected(self):
        """set_strategy intent removed in 2026-05-23 refactor."""
        with self.assertRaises(ValueError):
            D.parse_intent({"intent": "set_strategy", "template": "tank_rush"})

    def test_unknown_intent_rejected(self):
        with self.assertRaises(ValueError):
            D.parse_intent({"intent": "bogus_intent"})


class TestNewIntents(unittest.TestCase):
    """New daemon-resident missions added 2026-05-23."""

    def test_harass(self):
        i = D.parse_intent({
            "intent": "harass",
            "force": {"kind": "filter", "harass_capable": True},
            "region": {"kind": "around", "center": "enemy_base", "radius": 8},
        })
        self.assertEqual(i.intent, "harass")

    def test_patrol(self):
        i = D.parse_intent({
            "intent": "patrol",
            "force": {"kind": "group", "name": "north"},
            "waypoints": [{"x": 40, "y": 50}, {"x": 80, "y": 50}],
        })
        self.assertEqual(i.intent, "patrol")
        self.assertEqual(len(i.waypoints), 2)

    def test_diversion(self):
        i = D.parse_intent({
            "intent": "diversion",
            "feint_force": {"kind": "group", "name": "center"},
            "feint_target": {"kind": "named", "name": "enemy_base"},
            "raid_force": {"kind": "filter", "harass_capable": True},
            "raid_target": {"kind": "named", "name": "enemy_fact"},
            "raid_approach": "flank_left",
        })
        self.assertEqual(i.intent, "diversion")

    def test_filter_harass_capable(self):
        i = D.parse_intent({
            "intent": "harass",
            "force": {"kind": "filter", "harass_capable": True},
            "region": {"kind": "around", "center": "enemy_base", "radius": 6},
        })
        self.assertTrue(i.force.harass_capable)


class TestForceFilter(unittest.TestCase):
    def test_filter_hp_below(self):
        i = D.parse_intent({
            "intent": "retreat",
            "force": {"kind": "filter", "owner": "self", "hp_below": 0.3},
            "to": {"kind": "named", "name": "self_base"},
        })
        self.assertEqual(i.force.kind, "filter")
        self.assertEqual(i.force.hp_below, 0.3)

    def test_filter_unit_kind(self):
        i = D.parse_intent({
            "intent": "attack",
            "force": {"kind": "filter", "owner": "self", "unit_kind": "2tnk"},
            "target": {"kind": "named", "name": "enemy_fact"},
        })
        self.assertEqual(i.force.unit_kind, "2tnk")


class TestPincerFeint(unittest.TestCase):
    def test_pincer(self):
        i = D.parse_intent({
            "intent": "pincer",
            "left": {"kind": "group", "name": "north"},
            "right": {"kind": "group", "name": "south"},
            "target": {"kind": "named", "name": "enemy_fact"},
            "rendezvous_dist": 10,
        })
        self.assertEqual(i.rendezvous_dist, 10)

    def test_feint(self):
        i = D.parse_intent({
            "intent": "feint",
            "force": {"kind": "group", "name": "center"},
            "target": {"kind": "named", "name": "enemy_base"},
        })
        self.assertEqual(i.intent, "feint")


if __name__ == "__main__":
    unittest.main()
