"""
Smoke test: connect to a running OpenRA + McpBridge and call get_state.

Usage:
    python -m mcp_server.test_connect
    python -m mcp_server.test_connect get_state
    python -m mcp_server.test_connect list_units self
    python -m mcp_server.test_connect build Refinery 32 28 2
    python -m mcp_server.test_connect train Soldier 5
"""

import json
import socket
import sys


HOST = "127.0.0.1"
PORT = 7777


def send(cmd: dict) -> dict:
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        cmd = {"type": "get_state"}
    else:
        op = argv[1]
        if op == "get_state":
            cmd = {"type": "get_state"}
        elif op == "list_units":
            cmd = {"type": "list_units"}
            if len(argv) > 2:
                cmd["owner"] = argv[2]
            if len(argv) > 3:
                cmd["kind"] = argv[3]
        elif op == "build":
            cmd = {"type": "build", "structure": argv[2], "count": int(argv[5]) if len(argv) > 5 else 1}
            if len(argv) > 4:
                cmd["near"] = {"x": int(argv[3]), "y": int(argv[4])}
        elif op == "train":
            cmd = {"type": "train", "unit": argv[2], "count": int(argv[3]) if len(argv) > 3 else 1}
        elif op == "move":
            cmd = {"type": "move", "unit_ids": [int(argv[2])], "target": {"x": int(argv[3]), "y": int(argv[4])}}
        elif op == "screenshot":
            cmd = {"type": "screenshot"}
        elif op == "deploy":
            cmd = {"type": "deploy", "unit_ids": [int(x) for x in argv[2:]]}
        elif op == "pause":
            cmd = {"type": "pause"}
        elif op == "resume":
            cmd = {"type": "resume"}
        elif op == "list_groups":
            cmd = {"type": "list_groups"}
        elif op == "move_group":
            cmd = {"type": "move_group", "group": argv[2], "target": {"x": int(argv[3]), "y": int(argv[4])}}
            if len(argv) > 5 and argv[5] in ("a", "attack", "attack_move"):
                cmd["attack_move"] = True
        elif op == "attack_group":
            cmd = {"type": "attack_group", "group": argv[2], "target_id": int(argv[3])}
        elif op == "stance_group":
            cmd = {"type": "stance_group", "group": argv[2], "stance": argv[3]}
        elif op == "rebalance_groups":
            cmd = {"type": "rebalance_groups",
                   "count": int(argv[2]) if len(argv) > 2 else 3,
                   "axis": argv[3] if len(argv) > 3 else "y"}
        else:
            print(f"unknown op: {op}")
            return 2

    try:
        result = send(cmd)
    except (ConnectionRefusedError, socket.timeout) as e:
        print(f"[FAIL] cannot reach OpenRA MCPBridge at {HOST}:{PORT}: {e}")
        print("Is OpenRA running with the McpBridge trait enabled?")
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
