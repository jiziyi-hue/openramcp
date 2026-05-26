"""
Extract paper-facing metrics from OpenRA MCP logs and Claude transcripts.

This script is deliberately offline: it reads existing JSONL files and writes a
compact JSON summary that can be quoted or turned into tables later.

Inputs:
  - logs/**/decisions.jsonl from the game-side SessionLogger
  - %USERPROFILE%/.claude/projects/d--openra-mcp/*.jsonl by default

Examples:
  python -m mcp_server.tools.paper_metrics --out logs/paper_metrics.json
  python -m mcp_server.tools.paper_metrics --claude-project "C:/Users/me/.claude/projects/d--openra-mcp"
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_mean(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _safe_median(values: list[float]) -> float | None:
    return round(median(values), 2) if values else None


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                yield {"_parse_error": str(e), "_path": str(path), "_line": line_no}


def analyze_decisions(log_root: Path) -> dict[str, Any]:
    paths = sorted(log_root.glob("**/decisions.jsonl"))
    sessions: dict[str, dict[str, Any]] = {}
    intent_hist: Counter[str] = Counter()
    client_hist: Counter[str] = Counter()
    llm_model_hist: Counter[str] = Counter()
    nl_inputs: list[str] = []
    latency_ms: list[float] = []
    llm_latency_ms: list[float] = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    atomic_order_counts: list[int] = []
    ok_count = 0
    error_count = 0

    for path in paths:
        session_id = path.parent.name
        session = sessions.setdefault(
            session_id,
            {
                "path": str(path),
                "commands": 0,
                "ok": 0,
                "errors": 0,
                "intents": Counter(),
                "has_nl_input": 0,
                "total_atomic_orders": 0,
            },
        )
        for row in _read_jsonl(path):
            if row.get("_parse_error"):
                continue
            session["commands"] += 1
            client_hist[row.get("client") or "unknown"] += 1
            intent = row.get("intent_type") or "unknown"
            intent_hist[intent] += 1
            session["intents"][intent] += 1

            if row.get("ok"):
                ok_count += 1
                session["ok"] += 1
            else:
                error_count += 1
                session["errors"] += 1

            if row.get("nl_input"):
                nl_inputs.append(str(row["nl_input"]))
                session["has_nl_input"] += 1
            if row.get("llm_model"):
                llm_model_hist[str(row["llm_model"])] += 1
            if isinstance(row.get("latency_ms"), (int, float)):
                latency_ms.append(float(row["latency_ms"]))
            if isinstance(row.get("llm_latency_ms"), (int, float)):
                llm_latency_ms.append(float(row["llm_latency_ms"]))
            if isinstance(row.get("llm_input_tokens"), int):
                input_tokens.append(int(row["llm_input_tokens"]))
            if isinstance(row.get("llm_output_tokens"), int):
                output_tokens.append(int(row["llm_output_tokens"]))
            atomic_count = int(row.get("atomic_order_count") or 0)
            atomic_order_counts.append(atomic_count)
            session["total_atomic_orders"] += atomic_count

    serial_sessions = {}
    for session_id, data in sessions.items():
        data = dict(data)
        data["intents"] = dict(data["intents"])
        serial_sessions[session_id] = data

    total_commands = ok_count + error_count
    return {
        "log_root": str(log_root),
        "decision_files": len(paths),
        "sessions": serial_sessions,
        "total_commands": total_commands,
        "ok_commands": ok_count,
        "error_commands": error_count,
        "success_rate": round(ok_count / total_commands, 4) if total_commands else None,
        "commands_with_nl_input": len(nl_inputs),
        "unique_nl_inputs": len(set(nl_inputs)),
        "intent_histogram": dict(intent_hist),
        "client_histogram": dict(client_hist),
        "llm_model_histogram": dict(llm_model_hist),
        "latency_ms": {
            "mean": _safe_mean(latency_ms),
            "median": _safe_median(latency_ms),
            "n": len(latency_ms),
        },
        "llm_latency_ms": {
            "mean": _safe_mean(llm_latency_ms),
            "median": _safe_median(llm_latency_ms),
            "n": len(llm_latency_ms),
        },
        "tokens": {
            "input_total": sum(input_tokens),
            "output_total": sum(output_tokens),
            "input_mean_per_logged_call": _safe_mean([float(v) for v in input_tokens]),
            "output_mean_per_logged_call": _safe_mean([float(v) for v in output_tokens]),
            "logged_calls": max(len(input_tokens), len(output_tokens)),
        },
        "atomic_orders": {
            "total": sum(atomic_order_counts),
            "mean_per_command": _safe_mean([float(v) for v in atomic_order_counts]),
            "median_per_command": _safe_median([float(v) for v in atomic_order_counts]),
        },
        "sample_nl_inputs": nl_inputs[:20],
    }


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts).strip()


def _tool_uses(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    tools = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            tools.append(item)
    return tools


def analyze_claude_project(project_dir: Path) -> dict[str, Any]:
    paths = sorted(project_dir.glob("*.jsonl")) if project_dir.exists() else []
    sessions: dict[str, dict[str, Any]] = {}
    tool_hist: Counter[str] = Counter()
    openra_tool_hist: Counter[str] = Counter()
    model_hist: Counter[str] = Counter()
    total_input_tokens = 0
    total_output_tokens = 0
    user_text_turns = 0
    assistant_turns = 0
    assistant_tool_turns = 0
    tool_events = 0
    openra_tool_events = 0
    time_to_first_tool_ms: list[float] = []
    time_to_first_openra_tool_ms: list[float] = []

    for path in paths:
        rows = [r for r in _read_jsonl(path) if not r.get("_parse_error")]
        session_id = None
        for row in rows:
            session_id = row.get("sessionId") or row.get("session_id") or session_id
        session_id = session_id or path.stem
        session = sessions.setdefault(
            session_id,
            {
                "path": str(path),
                "user_text_turns": 0,
                "assistant_turns": 0,
                "tool_events": 0,
                "openra_tool_events": 0,
                "models": Counter(),
                "tools": Counter(),
                "openra_tools": Counter(),
            },
        )

        pending_user_ts: datetime | None = None
        pending_openra_user_ts: datetime | None = None
        for row in rows:
            msg = row.get("message") or {}
            role = msg.get("role")
            ts = _parse_ts(row.get("timestamp"))

            if role == "user":
                text = _message_text(msg.get("content"))
                if text:
                    user_text_turns += 1
                    session["user_text_turns"] += 1
                    pending_user_ts = ts
                    pending_openra_user_ts = ts
                continue

            if role != "assistant":
                continue

            assistant_turns += 1
            session["assistant_turns"] += 1
            model = msg.get("model")
            if model:
                model_hist[str(model)] += 1
                session["models"][str(model)] += 1

            usage = msg.get("usage") or {}
            total_input_tokens += int(usage.get("input_tokens") or 0)
            total_input_tokens += int(usage.get("cache_creation_input_tokens") or 0)
            total_input_tokens += int(usage.get("cache_read_input_tokens") or 0)
            total_output_tokens += int(usage.get("output_tokens") or 0)

            tools = _tool_uses(msg.get("content"))
            if not tools:
                continue
            assistant_tool_turns += 1
            tool_events += len(tools)
            session["tool_events"] += len(tools)
            for tool in tools:
                name = str(tool.get("name") or "unknown")
                tool_hist[name] += 1
                session["tools"][name] += 1
                if name.startswith("mcp__openra-bridge__"):
                    openra_tool_events += 1
                    openra_tool_hist[name] += 1
                    session["openra_tool_events"] += 1
                    session["openra_tools"][name] += 1
            if pending_user_ts and ts:
                delta_ms = (ts - pending_user_ts).total_seconds() * 1000.0
                if delta_ms >= 0:
                    time_to_first_tool_ms.append(delta_ms)
                pending_user_ts = None
            if pending_openra_user_ts and ts and any(
                str(tool.get("name") or "").startswith("mcp__openra-bridge__")
                for tool in tools
            ):
                delta_ms = (ts - pending_openra_user_ts).total_seconds() * 1000.0
                if delta_ms >= 0:
                    time_to_first_openra_tool_ms.append(delta_ms)
                pending_openra_user_ts = None

    serial_sessions = {}
    for session_id, data in sessions.items():
        data = dict(data)
        data["models"] = dict(data["models"])
        data["tools"] = dict(data["tools"])
        data["openra_tools"] = dict(data["openra_tools"])
        serial_sessions[session_id] = data

    return {
        "project_dir": str(project_dir),
        "transcript_files": len(paths),
        "sessions": serial_sessions,
        "user_text_turns": user_text_turns,
        "assistant_turns": assistant_turns,
        "assistant_tool_turns": assistant_tool_turns,
        "tool_events_all": tool_events,
        "openra_tool_events": openra_tool_events,
        "tool_histogram": dict(tool_hist),
        "openra_tool_histogram": dict(openra_tool_hist),
        "model_histogram": dict(model_hist),
        "tokens": {
            "input_total_including_cache": total_input_tokens,
            "output_total": total_output_tokens,
        },
        "time_to_first_any_tool_ms": {
            "mean": _safe_mean(time_to_first_tool_ms),
            "median": _safe_median(time_to_first_tool_ms),
            "n": len(time_to_first_tool_ms),
        },
        "time_to_first_openra_tool_ms": {
            "mean": _safe_mean(time_to_first_openra_tool_ms),
            "median": _safe_median(time_to_first_openra_tool_ms),
            "n": len(time_to_first_openra_tool_ms),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-root", default="logs", help="OpenRA MCP log root")
    default_project = Path.home() / ".claude" / "projects" / "d--openra-mcp"
    ap.add_argument("--claude-project", default=str(default_project))
    ap.add_argument("--out", default="logs/paper_metrics.json")
    args = ap.parse_args()

    log_root = Path(args.log_root)
    claude_project = Path(args.claude_project)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "openra_decisions": analyze_decisions(log_root),
        "claude_transcripts": analyze_claude_project(claude_project),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    print(json.dumps({
        "decision_commands": summary["openra_decisions"]["total_commands"],
        "commands_with_nl_input": summary["openra_decisions"]["commands_with_nl_input"],
        "openra_tool_events": summary["claude_transcripts"]["openra_tool_events"],
        "time_to_first_openra_tool_ms_median": summary["claude_transcripts"]["time_to_first_openra_tool_ms"]["median"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
