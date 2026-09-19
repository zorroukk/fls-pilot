#!/usr/bin/env python3
"""Focused tests for the Live Audition Pack (Slice P0.6).

Covers channel.preview_note (transient), mixer.set_arm (write-safe with
rollback contract), and transport.set_loop_mode (transient) -- registry
validation, spec contract, and tool-level safety dispatch through a fake
bridge. Live FL availability of the underlying APIs is tracked separately in
knowledgebase/fl_api/live_audition_apis.md (pending channels-v44 battery).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fl_studio_mcp import operations, protocol, safety  # noqa: E402
from fl_studio_mcp.tools import channel as channel_tools  # noqa: E402
from fl_studio_mcp.tools import mixer as mixer_tools  # noqa: E402
from fl_studio_mcp.tools import transport as transport_tools  # noqa: E402


class FakeBridge:
    def __init__(self) -> None:
        self.tracks = {
            1: {
                "i": 1,
                "name": "Track 1",
                "vol_norm": 0.8,
                "pan": 0.0,
                "mute": False,
                "solo": False,
                "arm": False,
            },
        }
        self.loop_mode = "pattern"
        self.notes: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def call(self, command: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        self.calls.append((command, params))

        if command == protocol.CMD_GET_PROJECT_STATE:
            return {"mixer_track_count": len(self.tracks)}

        if command == protocol.CMD_MIXER_GET_TRACK:
            idx = params.get("index", 1)
            return dict(self.tracks[idx])

        if command == protocol.CMD_MIXER_SET_ARM:
            track = self.tracks[params["track"]]
            track["arm"] = bool(params["state"])
            return {"track": params["track"], "arm": track["arm"]}

        if command == protocol.CMD_TRANSPORT_SET_LOOP_MODE:
            self.loop_mode = params["mode"]
            return {"loop_mode": self.loop_mode}

        if command == protocol.CMD_GET_PLAY_STATE:
            return {
                "playing": False,
                "recording": False,
                "loop_mode": self.loop_mode,
            }

        if command == protocol.CMD_CHANNEL_PREVIEW_NOTE:
            self.notes.append(params)
            return {**params, "release_path": "midiNoteOff"}

        raise AssertionError(f"unexpected command: {command!r} params={params!r}")


def _unwrap(result):
    for attr in ("data", "structured_content", "structuredContent"):
        value = getattr(result, attr, None)
        if value is not None:
            return value
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return json.loads(text)
        return _unwrap(first)
    return result


# ---------------------------------------------------------------------------
# Registry: validation + spec contract (offline, no bridge)
# ---------------------------------------------------------------------------


def test_preview_note_defaults_and_bounds() -> None:
    prepared = operations.prepare_operation(
        "channel", "preview_note", {"channel": 0, "note": 60}
    )
    assert prepared.safety_class == "transient"
    assert prepared.command.command == protocol.CMD_CHANNEL_PREVIEW_NOTE
    assert prepared.params["velocity"] == 96
    assert prepared.params["hold_ms"] == 200

    for bad in (
        {"channel": 0, "note": 128},
        {"channel": 0, "note": 60, "velocity": 0},
        {"channel": 0, "note": 60, "velocity": 128},
        {"channel": 0, "note": 60, "hold_ms": 10001},
        {"channel": 0, "note": 60, "extra": 1},
        {"channel": 0},
    ):
        with pytest.raises(operations.OperationValidationError):
            operations.prepare_operation("channel", "preview_note", bad)


def test_set_loop_mode_validation() -> None:
    prepared = operations.prepare_operation("transport", "set_loop_mode", {"mode": "SONG"})
    assert prepared.safety_class == "transient"
    assert prepared.params == {"mode": "song"}
    assert prepared.command.command == protocol.CMD_TRANSPORT_SET_LOOP_MODE

    for bad in ({"mode": "bar"}, {}, {"mode": "song", "extra": 1}):
        with pytest.raises(operations.OperationValidationError):
            operations.prepare_operation("transport", "set_loop_mode", bad)


def test_set_arm_spec_contract() -> None:
    prepared = operations.prepare_operation("mixer", "set_arm", {"track": 1, "state": True})
    assert prepared.safety_class == "write-safe"
    assert prepared.snapshot_scope == "mixer_track:1"
    assert prepared.verify == ("arm", True)

    restore = prepared.build_restore({"arm": False})
    assert restore["command"] == protocol.CMD_MIXER_SET_ARM
    assert restore["params"] == {"track": 1, "state": False}

    with pytest.raises(operations.OperationValidationError):
        operations.prepare_operation("mixer", "set_arm", {"track": 1, "state": "yes"})


# ---------------------------------------------------------------------------
# Tool-level dispatch through the domain tools
# ---------------------------------------------------------------------------


@pytest.fixture
def audition_mcp(monkeypatch, tmp_path):
    bridge = FakeBridge()
    for mod in (mixer_tools, channel_tools, transport_tools):
        monkeypatch.setattr(mod, "get_bridge", lambda: bridge)
    monkeypatch.setattr(mixer_tools, "mixer_track_error", lambda *a, **kw: None)
    safety._log = safety.ChangeLog(tmp_path / "changes.jsonl")
    mcp = FastMCP(name="audition-test")
    mixer_tools.register(mcp)
    channel_tools.register(mcp)
    transport_tools.register(mcp)
    return mcp, bridge


def _call(mcp: FastMCP, tool: str, action: str, params: dict | None = None):
    args = {"action": action}
    if params is not None:
        args["params"] = params
    return _unwrap(asyncio.run(mcp.call_tool(tool, args)))


def test_mixer_set_arm_roundtrip(audition_mcp) -> None:
    mcp, bridge = audition_mcp

    result = _call(mcp, "fl_mixer", "set_arm", {"track": 1, "state": True})

    assert result["ok"] is True
    assert bridge.tracks[1]["arm"] is True
    commands = [cmd for cmd, _ in bridge.calls]
    assert protocol.CMD_MIXER_SET_ARM in commands
    # Snapshot + readback both read the track scope.
    assert commands.count(protocol.CMD_MIXER_GET_TRACK) >= 2


def test_transport_loop_mode_transient(audition_mcp) -> None:
    mcp, bridge = audition_mcp

    result = _call(mcp, "fl_transport", "set_loop_mode", {"mode": "song"})

    assert result["loop_mode"] == "song"
    sent = [params for cmd, params in bridge.calls if cmd == protocol.CMD_TRANSPORT_SET_LOOP_MODE]
    assert sent == [{"mode": "song"}]


def test_channel_preview_note_via_tool(audition_mcp) -> None:
    mcp, bridge = audition_mcp

    result = _call(
        mcp, "fl_channel", "preview_note", {"channel": 3, "note": 72, "velocity": 100}
    )

    assert result["note"] == 72
    assert result["hold_ms"] == 200
    assert bridge.notes == [
        {"channel": 3, "note": 72, "velocity": 100, "hold_ms": 200}
    ]


def test_play_state_read_exposes_loop_mode(audition_mcp) -> None:
    mcp, _bridge = audition_mcp

    result = _call(mcp, "fl_transport", "get_play_state")

    assert result["loop_mode"] == "pattern"
