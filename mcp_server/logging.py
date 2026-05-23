"""
SessionLogger — captures NL → DSL → atomic chain + world deltas for the
openra_mcp research paper. Writes JSONL per-session under logs/<session_id>/.

Schema versioning: every line carries `schema_version`. Bump on breaking change.

Three writers:
  - decisions.jsonl     — one line per dispatch_intent call
  - world_snapshots.jsonl — 1Hz background snapshot from a daemon thread
  - session_summary.json — once at game end (via end_session() tool)

LLM-side fields (nl_input, latency, tokens) come from a `meta` dict passed by
Claude Code on every dispatch_intent. The server prompt (CLAUDE.md) instructs
the LLM to fill meta on each call.

Designed to never crash dispatch — every write is in try/except.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


SCHEMA_VERSION = "1.0"
LOG_ROOT = Path(os.environ.get(
    "OPENRA_LOG_ROOT",
    str(Path(__file__).resolve().parent.parent / "logs"),
))

# Pricing table for cost estimation. Update as model versions change.
MODEL_PRICING_USD_PER_MTOK: Dict[str, Dict[str, float]] = {
    "claude-opus-4-7":   {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in":  3.0, "out": 15.0},
    "claude-haiku-4-5":  {"in":  1.0, "out":  5.0},
    "claude-opus-4-6":   {"in": 15.0, "out": 75.0},
    # fallback
    "default":           {"in":  3.0, "out": 15.0},
}


class SessionLogger:
    """One instance per Python process. Lazily created on first use."""

    _instance: Optional["SessionLogger"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        session_id: Optional[str] = None,
        condition: str = "human_llm",
        scenario_id: Optional[str] = None,
        player_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id or (
            datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        )
        self.start_ts = time.time()
        self.dir = LOG_ROOT / self.session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.decisions_path = self.dir / "decisions.jsonl"
        self.snapshots_path = self.dir / "world_snapshots.jsonl"
        self.summary_path = self.dir / "session_summary.json"
        self.meta_path = self.dir / "session_meta.json"

        # Aggregate counters for finalize().
        self._counters: Dict[str, int] = {
            "nl_commands": 0,
            "atomic_orders": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        self._intent_hist: Dict[str, int] = {}
        self._template_hist: Dict[str, int] = {}
        self._template_switches = 0
        self._last_template: Optional[str] = None
        self._fronts_log: list[tuple[float, tuple[int, int]]] = []  # (ts, (x,y))
        self._seq = 0
        self._counter_lock = threading.Lock()

        # Write boot meta once.
        self._write_session_meta(condition, scenario_id, player_id)

    # ---------------------------------------------------------------- public

    @classmethod
    def current(cls) -> "SessionLogger":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls, **kwargs) -> "SessionLogger":
        """Start a fresh session (call from end_session or experiment harness)."""
        with cls._lock:
            cls._instance = cls(**kwargs)
            return cls._instance

    def log_decision(
        self,
        intent_payload: dict,
        result: dict,
        meta: Optional[dict],
        world_before: Optional[dict],
        world_after: Optional[dict],
        latency_ms: int,
        client: str = "dispatch_intent",
    ) -> None:
        """Write one decision line. Never raises — failures swallowed."""
        try:
            with self._counter_lock:
                self._seq += 1
                seq = self._seq
                self._counters["nl_commands"] += 1
                atomic_count = len(result.get("actions_taken", []) or [])
                self._counters["atomic_orders"] += atomic_count
                if meta:
                    self._counters["input_tokens"] += int(meta.get("llm_input_tokens", 0) or 0)
                    self._counters["output_tokens"] += int(meta.get("llm_output_tokens", 0) or 0)
                intent_type = (intent_payload or {}).get("intent", "?")
                self._intent_hist[intent_type] = self._intent_hist.get(intent_type, 0) + 1
                if intent_type == "set_strategy":
                    tpl = (intent_payload or {}).get("template")
                    if tpl:
                        self._template_hist[tpl] = self._template_hist.get(tpl, 0) + 1
                        if self._last_template is not None and self._last_template != tpl:
                            self._template_switches += 1
                        self._last_template = tpl
                # Track fronts (any intent with an attack_focus or target)
                af = (intent_payload or {}).get("attack_focus") \
                    or (intent_payload or {}).get("target")
                if isinstance(af, dict) and af.get("kind") == "pos":
                    p = af.get("pos") or {}
                    if "x" in p and "y" in p:
                        self._fronts_log.append((time.time(), (int(p["x"]), int(p["y"]))))

            tick = -1
            if world_before and world_before.get("ok"):
                tick = world_before.get("state", {}).get("tick", -1)

            entry = {
                "schema_version": SCHEMA_VERSION,
                "session_id": self.session_id,
                "seq": seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "tick": tick,
                "client": client,
                "intent_type": intent_payload.get("intent") if intent_payload else None,
                "intent": intent_payload,
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "narrative": result.get("narrative", ""),
                "atomic_orders": _compact_actions(result.get("actions_taken") or []),
                "atomic_order_count": atomic_count,
                "amplification_ratio": atomic_count,
                "latency_ms": latency_ms,
                "world_state_before": _world_summary(world_before),
                "world_state_after": _world_summary(world_after),
            }
            if meta:
                # LLM-side observability
                entry["nl_input"] = meta.get("nl_input")
                entry["llm_model"] = meta.get("llm_model")
                entry["llm_latency_ms"] = meta.get("llm_latency_ms")
                entry["llm_input_tokens"] = meta.get("llm_input_tokens")
                entry["llm_output_tokens"] = meta.get("llm_output_tokens")

            with open(self.decisions_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            # Logging must never break dispatch.
            pass

    def log_snapshot(self, snap: dict) -> None:
        try:
            snap = dict(snap)
            snap.setdefault("ts", time.time())
            snap.setdefault("session_id", self.session_id)
            with open(self.snapshots_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(snap, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def finalize(self, outcome: dict) -> dict:
        """End-of-game summary. outcome = {"result": "win"|"lose"|"draw", "end_tick": int, "notes": str}."""
        try:
            duration_min = (time.time() - self.start_ts) / 60.0
            summary = {
                "schema_version": SCHEMA_VERSION,
                "session_id": self.session_id,
                "duration_minutes": round(duration_min, 2),
                "nl_commands": self._counters["nl_commands"],
                "atomic_orders": self._counters["atomic_orders"],
                "mean_amplification_ratio": (
                    self._counters["atomic_orders"] / max(1, self._counters["nl_commands"])
                ),
                "apm": self._counters["atomic_orders"] / max(0.01, duration_min),
                "player_decisions_per_min": (
                    self._counters["nl_commands"] / max(0.01, duration_min)
                ),
                "total_input_tokens": self._counters["input_tokens"],
                "total_output_tokens": self._counters["output_tokens"],
                "estimated_llm_cost_usd": self._estimate_cost(),
                "intent_type_histogram": self._intent_hist,
                "strategy_template_histogram": self._template_hist,
                "template_switches": self._template_switches,
                "max_concurrent_fronts_10s": self._max_fronts_in_window(10.0),
                "outcome": outcome,
            }
            self.summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return summary
        except Exception as e:
            return {"ok": False, "error": str(e), "session_id": self.session_id}

    # ----------------------------------------------------------- helpers

    def _write_session_meta(self, condition: str, scenario_id: Optional[str], player_id: Optional[str]) -> None:
        try:
            meta = {
                "schema_version": SCHEMA_VERSION,
                "session_id": self.session_id,
                "start_ts": datetime.now(timezone.utc).isoformat(),
                "condition": condition,        # "solo_human" | "human_llm" | "bot_baseline"
                "scenario_id": scenario_id,
                "player_id": player_id,
                "openra_log_root": str(LOG_ROOT),
            }
            self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _estimate_cost(self) -> float:
        # Best effort — uses the dominant model based on intent hist. If multiple
        # models seen, sum proportionally (we don't track per-call here for simplicity).
        # Default fallback if model unknown.
        pricing = MODEL_PRICING_USD_PER_MTOK.get("default")
        cost_in = self._counters["input_tokens"] * pricing["in"] / 1_000_000.0
        cost_out = self._counters["output_tokens"] * pricing["out"] / 1_000_000.0
        return round(cost_in + cost_out, 4)

    def _max_fronts_in_window(self, window_s: float) -> int:
        """Max distinct attack regions (15-cell clusters) seen in any window."""
        if not self._fronts_log:
            return 0
        # Slide window
        events = sorted(self._fronts_log)
        max_distinct = 0
        i = 0
        for j, (tj, pj) in enumerate(events):
            while events[i][0] < tj - window_s:
                i += 1
            window = events[i:j + 1]
            distinct: list[tuple[int, int]] = []
            for _, p in window:
                if not any(_dist(p, q) < 15 for q in distinct):
                    distinct.append(p)
            if len(distinct) > max_distinct:
                max_distinct = len(distinct)
        return max_distinct


# -------------------------- module-level helpers ---------------------------

def _dist(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _world_summary(world_resp: Optional[dict]) -> Optional[dict]:
    """Compact snapshot of get_state response for the decision log."""
    if not world_resp or not world_resp.get("ok"):
        return None
    s = world_resp.get("state", {}) or {}
    self_units = s.get("self_units", []) or []
    enemy_units = s.get("enemy_units", []) or []
    return {
        "tick": s.get("tick"),
        "cash": s.get("self_cash"),
        "power": s.get("self_power"),
        "self_count": len(self_units),
        "enemy_count": len(enemy_units),
        "self_centroid": _centroid(self_units),
        "enemy_centroid": _centroid(enemy_units),
    }


def _centroid(units: list[dict]) -> Optional[tuple[int, int]]:
    if not units:
        return None
    n = len(units)
    sx = sum(u.get("pos", {}).get("x", 0) for u in units) // n
    sy = sum(u.get("pos", {}).get("y", 0) for u in units) // n
    return (sx, sy)


def _compact_actions(actions: list) -> list:
    """Strip noisy fields from actions_taken for the log line."""
    out = []
    for a in actions[:50]:  # cap at 50 to keep line short
        cmd = a.get("cmd", {}) if isinstance(a, dict) else {}
        resp = a.get("resp", {}) if isinstance(a, dict) else {}
        out.append({
            "type": cmd.get("type"),
            "ok": resp.get("ok") if isinstance(resp, dict) else None,
            "n": resp.get("issued_orders") if isinstance(resp, dict) else None,
        })
    if len(actions) > 50:
        out.append({"truncated": len(actions) - 50})
    return out


# -------------------------- snapshot daemon -----------------------------

class SnapshotDaemon:
    """Optional 1Hz background snapshot thread. Calls transport.send_command
    with get_state and writes through SessionLogger.log_snapshot. Best-effort."""

    def __init__(self, transport, interval_s: float = 1.0):
        self.transport = transport
        self.interval = interval_s
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                         name="SnapshotDaemon")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                resp = self.transport.send_command({"type": "get_state", "include_enemies": True})
                if resp.get("ok"):
                    SessionLogger.current().log_snapshot(_world_summary(resp) or {})
            except Exception:
                pass
