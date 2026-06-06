"""Drive OpenRA with the local fine-tuned small model — pure CLI, no MCP.

Pipeline (the whole co-pilot, end to end):
    你打的字 (NL) → 本地 0.5B 模型 → intent JSON → parse_intent 校验
                  → interpreter.interpret() (算坐标/选兵) → TCP → 引擎

No MCP server, no browser. The MCP layer is only for interactive Claude;
this runner talks to the engine directly through OpenRATransport, exactly
like the DeepSeek baseline runner did.

Usage:
    # dry-run: translate only, NO game needed (test the model)
    python scripts/run_local_model.py --dry-run

    # live: drive a running OpenRA (engine TCP bridge on :7777)
    python scripts/run_local_model.py

    # one-shot
    python scripts/run_local_model.py --once "全军推他老家"
    python scripts/run_local_model.py --dry-run --once "兵分两路夹击他家"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from scripts.intent_translate import Translator  # noqa: E402
from mcp_server import intent_dsl as D  # noqa: E402


def pick_adapter() -> str:
    """Default to the newest trained adapter present."""
    for v in ("qwen05b-lora-v6", "qwen05b-lora-v5", "qwen05b-lora-v4",
              "qwen05b-lora"):
        p = Path("outputs") / v
        if (p / "adapter_model.safetensors").exists():
            return str(p)
    return "outputs/qwen05b-lora-v5"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=None, help="LoRA adapter dir")
    ap.add_argument("--dry-run", action="store_true",
                    help="translate only, do not touch the engine")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--once", default=None, help="run a single command and exit")
    args = ap.parse_args()

    adapter = args.adapter or pick_adapter()
    print(f"[load] adapter={adapter} (CPU)...", file=sys.stderr)
    tr = Translator(adapter=adapter)
    print("[ready]", file=sys.stderr)

    transport = None
    if not args.dry_run:
        from mcp_server.transport import OpenRATransport
        from mcp_server import interpreter as I
        transport = OpenRATransport(host=args.host, port=args.port)
        if not transport.connect():
            print(f"[WARN] can't reach engine at {args.host}:{args.port} — "
                  f"falling back to --dry-run (start OpenRA + bridge to go live)",
                  file=sys.stderr)
            transport = None

    def handle(nl: str):
        intent = tr.translate(nl)
        if intent is None:
            print(f"  ✗ 模型没产出合法 JSON")
            return
        # validate against the real schema
        try:
            D.parse_intent(intent)
        except Exception as e:
            print(f"  ✗ 引擎不认这条命令: {e}")
            print(f"     {json.dumps(intent, ensure_ascii=False)}")
            return
        print(f"  → {json.dumps(intent, ensure_ascii=False)}")
        if transport is not None:
            from mcp_server import interpreter as I
            resp = I.interpret(intent, transport)
            tag = "✓" if resp.get("ok") else "✗"
            print(f"  {tag} {resp.get('narrative') or resp.get('error')}")

    if args.once:
        print(f">> {args.once}")
        handle(args.once)
        return 0

    mode = "DRY-RUN (不连引擎)" if transport is None else f"LIVE → {args.host}:{args.port}"
    print(f"=== 本地小模型指挥 [{mode}] — 输入中文命令, 空行退出 ===")
    while True:
        try:
            nl = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not nl:
            break
        handle(nl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
