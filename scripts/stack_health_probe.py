#!/usr/bin/env python3
"""One-shot FL Studio MCP stack health probe.

Asks the daemon for {"op": "health"} and prints a single PASS/FAIL line.
Exit codes: 0 = alive+heartbeat fresh, 1 = daemon up but bridge not alive,
2 = daemon unreachable (or nonsense reply). Made for the startup/watchdog
scripts and the FL Studio lane runbook; keep it dependency-free.
"""

from __future__ import annotations

import json
import os
import socket
import sys

HOST = os.environ.get("FLSTUDIO_MCP_TCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FLSTUDIO_MCP_TCP_PORT", "9787"))


def main() -> int:
    try:
        with socket.create_connection((HOST, PORT), timeout=3) as sock:
            sock.sendall(b'{"op": "health"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError as e:
        print(f"FAIL daemon unreachable on {HOST}:{PORT} ({type(e).__name__}: {e})")
        return 2
    try:
        res = json.loads(buf.split(b"\n", 1)[0])
    except ValueError:
        print(f"FAIL daemon answered non-JSON: {buf[:80]!r}")
        return 2
    age = res.get("heartbeat_age")
    if res.get("alive"):
        detail = (
            f"heartbeat_age={age:.1f}s"
            if isinstance(age, (int, float))
            else "no heartbeat yet"
        )
        print(f"PASS daemon alive ({detail})")
        return 0
    print(
        "FAIL daemon up but bridge not alive "
        f"(heartbeat_age={age}; FL64/controller down? {res.get('error') or ''})"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
