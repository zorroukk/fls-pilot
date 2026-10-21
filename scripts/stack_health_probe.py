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
    tok = os.environ.get("FLSTUDIO_MCP_DAEMON_TOKEN")
    req = b'{"op": "health"}\n' if not tok else json.dumps({"op": "health", "token": tok}).encode() + b"\n"
    try:
        with socket.create_connection((HOST, PORT), timeout=3) as sock:
            sock.sendall(req)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError as e:
        print(f"FAIL daemon unreachable on {HOST}:{PORT} ({type(e).__name__}: {e})")
        return 2
    if not buf.strip():
        # Audit D7: old text claimed 'answered non-JSON' when the daemon in fact
        # answered NOTHING (accepted, then closed) -- assert facts, not shapes.
        print(f"FAIL daemon accepted the connection on {HOST}:{PORT} but closed "
              f"WITHOUT replying (wedged daemon or killed mid-reply; restart it)")
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
    # Audit D8/D9: exit 1 with FL64 running is the documented reader-thread-death
    # signature (runbook law 4) -- name the cure the operator can actually act on.
    fl_running = False
    try:
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe"],
                           capture_output=True, text=True, timeout=5)
        fl_running = "FL64.exe" in (r.stdout or "")
    except Exception:
        pass
    hint = ("FL64 is RUNNING -> the daemon's MIDI reader thread is the prime suspect: "
            "RESTART THE DAEMON (kill the process owning the port), do not re-tinker F10"
            if fl_running else
            "FL64 not running -> start FL Studio and load the FLStudioMCP controller")
    print("FAIL daemon up but bridge not alive "
          f"(heartbeat_age={age}). {hint}. daemon error: {res.get('error') or '-'}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
