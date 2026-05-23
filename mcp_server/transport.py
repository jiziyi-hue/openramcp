"""
TCP transport between Python MCP server and OpenRA MCPBridgeTrait.

Wire protocol: newline-delimited JSON. Each request is one line, server
responds with one line.
"""

import json
import socket
import threading
from typing import Optional


class OpenRATransport:
    """Synchronous TCP client. Reconnects on demand. Thread-safe."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7777, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._buf = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> bool:
        with self._lock:
            if self._sock is not None:
                return True
            try:
                s = socket.create_connection((self.host, self.port), timeout=self.timeout)
                s.settimeout(self.timeout)
                self._sock = s
                self._buf = b""
                return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                return False

    def disconnect(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
                self._buf = b""

    def send_command(self, payload: dict) -> dict:
        """Send one command JSON, read one response JSON. Blocking."""
        if not self.connect():
            return {
                "ok": False,
                "error": "OpenRA bridge not connected (TCP 127.0.0.1:7777). Is OpenRA running with MCPBridgeTrait?",
            }
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            try:
                assert self._sock is not None
                self._sock.sendall(line)
                resp_line = self._read_line()
                if resp_line is None:
                    return {"ok": False, "error": "Connection closed while reading response"}
                return json.loads(resp_line.decode("utf-8"))
            except (socket.timeout, ConnectionResetError, BrokenPipeError, OSError) as e:
                self._sock = None
                self._buf = b""
                return {"ok": False, "error": f"Transport error: {e}"}

    def _read_line(self) -> Optional[bytes]:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                return None
            self._buf += chunk
        line, _, rest = self._buf.partition(b"\n")
        self._buf = rest
        return line
