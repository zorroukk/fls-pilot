#!/usr/bin/env python3
"""fl-studio-mcp watchdog -- restarts the daemon on BRIDGE TRUTH, not port truth.

Audits D9/D1/D2: the historic startup/watchdog guards treated "a listener
exists" as healthy, so the documented zombie (MIDI reader thread dead, TCP
still answering, FL beating faithfully) was never restarted by anything. Every
decision here keys on scripts/stack_health_probe.py exit codes:

    0  healthy                  -> reset strikes, sleep
    1  daemon up, bridge dead   -> strike++; at --strikes-kill strikes, kill the
                                   process LISTENING on the port, relaunch, reset
    2  daemon down/unreachable  -> relaunch the daemon (bounded, logged)

Stdlib-only. Run every 5 minutes from Task Scheduler, or:
    python scripts/watchdog.py --loop 300
"""
from __future__ import annotations
import argparse, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROBE = [sys.executable, os.path.join(HERE, "stack_health_probe.py")]


def run_probe() -> int:
    try:
        return subprocess.run(PROBE, capture_output=True, timeout=30).returncode
    except Exception:
        return 2


def port_owner_pid(host_port: str):
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=15).stdout or ""
    except Exception:
        return None
    for line in out.splitlines():
        if host_port in line and "LISTENING" in line.upper():
            m = re.search(r"[ ](\d+)[ ]*$", line.strip())
            if m:
                return int(m.group(1))
    return None


def kill_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=30)
        else:
            os.kill(pid, 15)
        return True
    except Exception:
        return False


def launch_daemon() -> bool:
    exe = os.path.join(REPO, ".venv", "Scripts", "fl-studio-mcp-daemon.exe")
    cmd = [exe] if os.path.exists(exe) else [sys.executable, "-m", "fl_studio_mcp.daemon"]
    cwd = REPO if os.path.isdir(os.path.join(REPO, "src")) else None
    try:
        kw = {"cwd": cwd, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200 | 0x00001000
        subprocess.Popen(cmd, **kw)
        return True
    except Exception as e:
        print("[watchdog] relaunch failed: %s" % e, flush=True)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="seconds between checks; 0 = one-shot")
    ap.add_argument("--port", default=None)
    ap.add_argument("--strikes-kill", type=int, default=2)
    a = ap.parse_args()
    port = a.port or ("127.0.0.1:" + os.environ.get("FLSTUDIO_MCP_TCP_PORT", "9787"))
    strikes = 0
    while True:
        code = run_probe()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        if code == 0:
            strikes = 0
            print("[%s] [watchdog] healthy" % ts, flush=True)
        elif code == 2:
            print("[%s] [watchdog] daemon down/unreachable -> launching" % ts, flush=True)
            launch_daemon()
            strikes = 0
        else:
            strikes += 1
            print("[%s] [watchdog] bridge dead (strike %d/%d)" % (ts, strikes, a.strikes_kill), flush=True)
            if strikes >= a.strikes_kill:
                pid = port_owner_pid(port)
                if pid:
                    print("[%s] [watchdog] killing stale daemon pid %s on %s" % (ts, pid, port), flush=True)
                    kill_pid(pid)
                    time.sleep(2)
                launch_daemon()
                strikes = 0
        if not a.loop:
            return 0
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
