"""End-to-end smoke test against a running OpenRA + McpBridge.

Calls each MCP tool through its Python entry point (not stdio) and prints
a one-line PASS/FAIL summary. Useful to validate the tool surface without
restarting Claude Code.

Assumes OpenRA is running at 127.0.0.1:7777. Trait may be on the menu
shellmap (non-interactive — orders dispatch but units may ignore) or in
an active skirmish (full interactivity). Either way the JSON shape is
asserted.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Callable

from mcp_server import server


PASS = "\x1b[32mPASS\x1b[0m"
FAIL = "\x1b[31mFAIL\x1b[0m"
SKIP = "\x1b[33mSKIP\x1b[0m"


def _check(name: str, fn: Callable[[], dict],
           require_ok: bool = True,
           require_fields: tuple = ()) -> dict:
    try:
        t0 = time.time()
        r = fn()
        dt = (time.time() - t0) * 1000
    except Exception as e:
        print(f"{FAIL}  {name}: exception — {type(e).__name__}: {e}")
        traceback.print_exc()
        return {"ok": False}

    if not isinstance(r, dict):
        print(f"{FAIL}  {name}: returned non-dict ({type(r).__name__})")
        return {"ok": False}

    if require_ok and not r.get("ok"):
        print(f"{FAIL}  {name}: ok=false  err={r.get('error', '?')}  ({dt:.0f}ms)")
        return r

    missing = [f for f in require_fields if f not in r]
    if missing:
        print(f"{FAIL}  {name}: missing fields {missing}  ({dt:.0f}ms)")
        return r

    msg = ""
    if r.get("narrative"):
        msg = "  " + r["narrative"][:80]
    print(f"{PASS}  {name}  ({dt:.0f}ms){msg}")
    return r


def main() -> int:
    print("===== openra_mcp smoke test =====")

    # --- Section 1: state queries
    print("\n[1] state queries")
    s = _check("get_state", lambda: server.get_state(),
               require_fields=("state",))
    state = s.get("state", {}) if s.get("ok") else {}
    map_name = state.get("map_name", "?")
    self_count = len(state.get("self_units", []))
    enemy_count = len(state.get("enemy_units", []))
    tick = state.get("tick", 0)
    paused = state.get("paused", False)
    print(f"        map={map_name}  tick={tick}  paused={paused}"
          f"  self={self_count}  enemy={enemy_count}")

    in_skirmish = self_count + enemy_count > 5

    _check("list_units (self)", lambda: server.list_units(owner="self"),
           require_fields=("units",))
    _check("list_units (enemy)", lambda: server.list_units(owner="enemy"),
           require_fields=("units",))
    _check("find_unit ('fact')", lambda: server.find_unit("fact"),
           require_fields=("units",))
    _check("list_groups", lambda: server.list_groups(),
           require_fields=("groups",))

    # --- Section 2: DSL — reports
    print("\n[2] dispatch_intent — reports")
    _check("intent: report battlefield",
           lambda: server.dispatch_intent({"intent": "report", "what": "battlefield"}),
           require_fields=("narrative",))
    _check("intent: report enemy",
           lambda: server.dispatch_intent({"intent": "report", "what": "enemy"}),
           require_fields=("narrative",))
    _check("intent: report threats",
           lambda: server.dispatch_intent({"intent": "report", "what": "threats"}),
           require_fields=("narrative",))
    _check("intent: report resources",
           lambda: server.dispatch_intent({"intent": "report", "what": "resources"}),
           require_fields=("narrative",))
    _check("intent: report groups",
           lambda: server.dispatch_intent({"intent": "report", "what": "groups"}),
           require_fields=("narrative",))

    # --- Section 3: tactical engine
    print("\n[3] tactical engine")
    _check("tactical_status (initial)", lambda: server.tactical_status(),
           require_fields=("running", "active_assaults"))

    # Try arming auto-defense at self_base.
    r = _check("enable_auto_defense (self_base)",
               lambda: server.enable_auto_defense(center_named="self_base"),
               require_fields=("center", "radius"))

    status = _check("tactical_status (after arm)", lambda: server.tactical_status())
    if status.get("ok"):
        print(f"        running={status.get('running')}"
              f"  defense_on={status.get('auto_defense_on')}"
              f"  center={status.get('auto_defense_center')}")

    # --- Section 4: DSL — combat dispatch (only meaningful in skirmish)
    print("\n[4] dispatch_intent — combat" +
          ("" if in_skirmish else "  (shellmap detected — orders may be ignored)"))

    _check("intent: attack all → nearest_enemy (frontal)",
           lambda: server.dispatch_intent({
               "intent": "attack",
               "force": {"kind": "group", "name": "all"},
               "target": {"kind": "named", "name": "nearest_enemy"},
               "approach": "frontal",
           }))

    time.sleep(2)
    after = _check("tactical_status (after attack)", lambda: server.tactical_status())
    if after.get("ok"):
        print(f"        active_assaults={after.get('active_assaults')}"
              f"  tick_count={after.get('tick_count')}"
              f"  retargets={after.get('retargets')}"
              f"  cohesion_halts={after.get('cohesion_halts')}"
              f"  defense_dispatches={after.get('defense_dispatches')}")

    # --- Section 5: watcher (non-blocking timeout test)
    print("\n[5] watcher")
    _check("wait_for_event tick_reached short timeout",
           lambda: server.wait_for_event(
               condition={"type": "tick_reached", "tick": tick + 5},
               timeout_s=20, poll_interval_s=2),
           require_fields=("matched",))

    # --- Section 6: cleanup
    print("\n[6] cleanup")
    _check("cancel_assaults", lambda: server.cancel_assaults())
    _check("disable_auto_defense", lambda: server.disable_auto_defense())

    # --- Section 7: tools registered
    print("\n[7] tool surface")
    expected_tools = [
        "get_state", "list_units", "find_unit", "build", "train", "move", "attack",
        "set_stance", "pause", "resume", "screenshot",
        "deploy", "stop", "sell", "scatter",
        "list_groups", "move_group", "attack_group", "stance_group",
        "assign_to_group", "rebalance_groups",
        "dispatch_intent", "set_bot_focus",
        "latest_scout_report", "wait_for_event",
        "tactical_status", "enable_auto_defense", "disable_auto_defense",
        "cancel_assaults",
    ]
    found = [t for t in expected_tools if callable(getattr(server, t, None))]
    missing = sorted(set(expected_tools) - set(found))
    print(f"        {len(found)}/{len(expected_tools)} tools callable")
    if missing:
        print(f"        {FAIL} missing: {missing}")
        return 1

    print("\n===== done =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
