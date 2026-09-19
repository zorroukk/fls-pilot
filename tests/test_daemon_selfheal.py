#!/usr/bin/env python3
"""Daemon self-heal + file-logger unit tests (2026-09 reliability pack).

Proves: raw MIDI-handle failures (OSError/RuntimeError that are NOT our
FLBridgeError hierarchy) reset the bridge singleton so the next request
re-attaches by name; FL-side errors leave the bridge untouched; the
log-dir resolver honours env then repo-cwd then LOCALAPPDATA.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fl_studio_mcp import daemon  # noqa: E402
from fl_studio_mcp.connection import FLBridgeError, FLTimeout  # noqa: E402


class BoomBridge:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.closed = False
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def call(self, command, params=None, *, timeout=None):
        if self.exc is not None:
            raise self.exc
        return {"echo": command}


def test_oserror_resets_bridge(monkeypatch) -> None:
    boom = BoomBridge(OSError("MIDI handle dead"))
    monkeypatch.setattr(daemon, "_bridge", boom)

    res = daemon._handle_request({"op": "call", "cmd": "x"})

    assert res["ok"] is False
    assert res["exc"] == "TransportDead"
    assert res["healed"] is True
    assert boom.closed is True
    assert daemon._bridge is None


def test_runtimeerror_not_flbridge_still_resets(monkeypatch) -> None:
    boom = BoomBridge(RuntimeError("wm_midi_out id gone"))
    monkeypatch.setattr(daemon, "_bridge", boom)

    res = daemon._handle_request({"op": "call", "cmd": "x"})

    assert res["exc"] == "TransportDead"
    assert daemon._bridge is None


def test_fl_side_errors_do_not_reset_bridge(monkeypatch) -> None:
    boom = BoomBridge(FLTimeout("FL did not answer"))
    monkeypatch.setattr(daemon, "_bridge", boom)

    res = daemon._handle_request({"op": "call", "cmd": "x"})

    assert res["exc"] == "FLTimeout"
    assert daemon._bridge is boom
    assert boom.closed is False

    boom2 = BoomBridge(FLBridgeError("fl weirdness"))
    monkeypatch.setattr(daemon, "_bridge", boom2)
    res = daemon._handle_request({"op": "call", "cmd": "x"})
    assert res["exc"] == "FLBridgeError"
    assert daemon._bridge is boom2


def test_next_call_reopens_fresh_bridge(monkeypatch) -> None:
    built: list[BoomBridge] = []

    def factory():
        b = BoomBridge()
        built.append(b)
        return b

    monkeypatch.setattr(daemon, "FLBridge", factory)
    monkeypatch.setattr(daemon, "_bridge", None)

    assert daemon._handle_request({"op": "call", "cmd": "ping"})["ok"] is True
    assert len(built) == 1 and built[0].opened is True

    # Poison it, watch recovery on the next request.
    built[0].exc = OSError("handle died")
    assert daemon._handle_request({"op": "call", "cmd": "ping"})["exc"] == "TransportDead"
    assert daemon._handle_request({"op": "call", "cmd": "ping"})["ok"] is True
    assert len(built) == 2 and built[1].opened is True


def test_log_dir_resolution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLSTUDIO_MCP_LOG_DIR", str(tmp_path / "custom"))
    assert daemon._default_log_dir() == str(tmp_path / "custom")

    monkeypatch.delenv("FLSTUDIO_MCP_LOG_DIR")
    repo = tmp_path / "repo"
    (repo / "scratch").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\n")
    monkeypatch.chdir(repo)
    assert daemon._default_log_dir() == os.path.join(str(repo), "scratch", "logs")

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LA"))
    assert daemon._default_log_dir() == os.path.join(
        str(tmp_path / "LA"), "flstudio-mcp", "logs"
    )


def test_attach_file_logger_survives_bad_dir(monkeypatch, tmp_path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a dir")
    monkeypatch.setenv("FLSTUDIO_MCP_LOG_DIR", str(blocker))
    daemon._attach_file_logger()  # must not raise
