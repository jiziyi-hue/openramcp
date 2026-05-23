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

    def test_report_capabilities(self):
        i = D.parse_intent({"intent": "report", "what": "capabilities"})
        self.assertEqual(i.what, "capabilities")

    def test_unknown_intent_rejected(self):
        with self.assertRaises(ValueError):
            D.parse_intent({"intent": "bogus_intent"})


class TestSetStrategy(unittest.TestCase):
    def test_minimal_template(self):
        i = D.parse_intent({"intent": "set_strategy", "template": "tank_rush"})
        self.assertEqual(i.template, "tank_rush")
        self.assertEqual(i.transition_mode, "soft")  # default

    def test_full_patch(self):
        i = D.parse_intent({
            "intent": "set_strategy",
            "template": "turtle",
            "defense_state": "full_alert",
            "spend_ratio": "eco_heavy",
            "transition_mode": "hybrid",
            "macro_paused": False,
            "tech_focus": "tier3",
            "primary_objective": "destroy_fact",
            "scout_priority": "high",
            "retreat_threshold": "normal",
        })
        self.assertEqual(i.template, "turtle")
        self.assertEqual(i.defense_state, "full_alert")
        self.assertEqual(i.spend_ratio, "eco_heavy")
        self.assertEqual(i.transition_mode, "hybrid")
        self.assertFalse(i.macro_paused)
        self.assertEqual(i.tech_focus, "tier3")
        self.assertEqual(i.primary_objective, "destroy_fact")

    def test_attack_focus_target(self):
        i = D.parse_intent({
            "intent": "set_strategy",
            "attack_focus": {"kind": "pos", "pos": {"x": 65, "y": 9}},
        })
        self.assertIsInstance(i.attack_focus, D.TargetByPos)
        self.assertEqual(i.attack_focus.pos.x, 65)

    def test_attack_focus_named(self):
        i = D.parse_intent({
            "intent": "set_strategy",
            "attack_focus": {"kind": "named", "name": "enemy_fact"},
        })
        self.assertIsInstance(i.attack_focus, D.TargetByName)
        self.assertEqual(i.attack_focus.name, "enemy_fact")

    def test_clear_focus_flags(self):
        i = D.parse_intent({
            "intent": "set_strategy",
            "clear_attack_focus": True,
            "clear_harass_focus": True,
        })
        self.assertTrue(i.clear_attack_focus)
        self.assertTrue(i.clear_harass_focus)

    def test_unknown_template_rejected(self):
        with self.assertRaises(Exception):
            D.parse_intent({"intent": "set_strategy", "template": "bogus_template"})

    def test_unknown_defense_state_rejected(self):
        with self.assertRaises(Exception):
            D.parse_intent({"intent": "set_strategy", "defense_state": "neutral"})

    def test_model_dump_excludes_none(self):
        i = D.parse_intent({"intent": "set_strategy", "template": "balanced"})
        d = i.model_dump(exclude_none=True, exclude={"intent", "transition_mode"})
        # Only "template" should be present.
        self.assertEqual(d, {"template": "balanced"})


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
