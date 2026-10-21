#!/usr/bin/env python3
"""Permanent regression guards for the 2026-10-21 audit sweep.

Each test pins one fixed defect by audit id (D=daemon, N=contract, T=tools,
R=repo) and fails if it returns. All checks are offline: fake bridges, fake
MIDI surfaces, no FL Studio, no ports. See docs/AUDIT_2026-10-21.md.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fl_studio_mcp import protocol, safety  # noqa: E402
from fl_studio_mcp.daemon import _Server, _handle_request  # noqa: E402
from fl_studio_mcp import daemon as daemon_mod  # noqa: E402


# --------------------------------------------------------------------------
# D-lane: daemon / transport
# --------------------------------------------------------------------------

def test_second_daemon_cannot_bind_a_live_port(monkeypatch):
    """Audit D1: SO_REUSEADDR on Windows let a phantom daemon double-bind."""
    srv = _Server(("127.0.0.1", 0), daemon_mod._Handler)
    port = srv.server_address[1]
    try:
        with pytest.raises(OSError):
            _Server(("127.0.0.1", port), daemon_mod._Handler)
    finally:
        srv.server_close()


def test_reader_thread_death_self_heals(monkeypatch):
    """Audit D2: FLNotRunning (heartbeat stale, TCP alive) left a zombie."""
    from fl_studio_mcp.connection import FLNotRunning

    class DeadBridge:
        def call(self, *a, **k):
            raise FLNotRunning("heartbeat went stale")

    monkeypatch.setattr(daemon_mod, "_bridge", DeadBridge())
    monkeypatch.setattr(daemon_mod, "_last_notrunning_reset", 0.0)
    res = _handle_request({"op": "call", "cmd": "get_tempo", "params": {}})
    assert res["exc"] == "FLNotRunning"
    assert res["healed"] is True, "the poisoned singleton must be dropped"
    assert daemon_mod._bridge is None


def test_health_survives_a_midi_open_failure(monkeypatch):
    """Audit D6: an OSError in the health branch dropped the connection."""
    def boom():
        raise OSError("rtmidi: corrupted driver graph")
    monkeypatch.setattr(daemon_mod, "_get_bridge", boom)
    res = _handle_request({"op": "health"})
    assert res["alive"] is False
    assert "OSError" in res["error"]


def test_oversized_payload_is_rejected_at_encode_time():
    """Audit D11: oversized bodies used to vanish at the receiver's cap."""
    with pytest.raises(ValueError):
        protocol.encode_frames(protocol.DIR_REQUEST, "abcdefgh",
                               {"blob": "x" * (protocol._MAX_REASSEMBLY_BYTES + 10)})


def test_reassembler_records_why_it_dropped():
    """Audit D11: silent drops are what made over-size look like FL being dead."""
    r = protocol.ChunkReassembler()
    big = protocol._MAX_REASSEMBLY_BYTES + protocol._MAX_CHUNK_BODY
    with pytest.raises(ValueError):
        protocol.encode_frames(protocol.DIR_REQUEST, "abcdefgh", {"blob": "x" * big})
    # feed frames by hand, past the cap
    frames = [bytes([protocol.CHUNK_MAGIC, 1, i]) + b"z" * protocol._MAX_CHUNK_BODY
              for i in range((big // protocol._MAX_CHUNK_BODY) + 1)]
    for f in frames:
        r.feed(f)
    assert r.last_drop is not None and r.last_drop["kind"] == "overflow"


def test_chunk_frames_of_one_message_cannot_interleave():
    """Audit D3: interleaved frames made FL drop the in-flight command."""
    from fl_studio_mcp import connection as conn

    class Sink:
        def __init__(self):
            self.order = []
        def send(self, msg):
            self.order.append((threading.current_thread().name, bytes(msg.data)[2]))

    class FakeMido:
        class Message:
            def __init__(self, kind, data=None):
                self.data = data

    b = conn.FLBridge.__new__(conn.FLBridge)
    b._opened = True
    b._lock = threading.Lock()
    b._send_lock = threading.Lock()
    b._pending = {}
    b.default_timeout = 0.05
    b._out_port = Sink()
    b._reassembler = protocol.ChunkReassembler()
    b._last_heartbeat = time.monotonic()
    b._heartbeat_payload = None
    b.dir_request = protocol.DIR_REQUEST

    payload = {"blob": "x" * 2265}
    frames = protocol.encode_frames(protocol.DIR_REQUEST, "aaaaaaaa", payload)
    assert len(frames) >= 3

    def sender(name):
        b.call("channel_set_steps", payload, timeout=0.05)

    # Both callers use the same code path; the lock must keep each message's
    # frames contiguous. (They will time out -- that is fine and expected.)
    t1 = threading.Thread(target=sender, args=("A",))
    t2 = threading.Thread(target=sender, args=("B",))
    t1.start(); t2.start(); t1.join(); t2.join()

    order = [who for who, _ in b._out_port.order]
    # per-message contiguity: no A,B,A,B pattern anywhere
    runs = []
    for who in order:
        if not runs or runs[-1] != who:
            runs.append(who)
    assert len(runs) == 2, f"frames of different messages interleaved: {runs}"


# --------------------------------------------------------------------------
# N-lane: agent-facing contract
# --------------------------------------------------------------------------

def test_apply_notes_refuses_misnested_payload():
    """Audit N1: the field WIPE (params-nested -> empty replace + hotkey)."""
    res = _handle_request({"op": "apply_notes",
                           "params": {"notes": [{"pitch": 60}], "mode": "replace"}})
    assert res["ok"] is False
    assert res["code"] == "envelope-misnested"


def test_apply_notes_refuses_actionless_request():
    """Audit N1: no notes, no transform must never mean 'clear the roll'."""
    res = _handle_request({"op": "apply_notes"})
    assert res["ok"] is False
    assert res["code"] == "empty-payload"


def test_validate_notes_rejects_out_of_range():
    """Audit N2: raw daemon notes bypassed the pydantic model entirely."""
    from fl_studio_mcp import pianoroll as pr
    for bad in (
        [{"pitch": 999}],
        [{"pitch": -12}],
        [{"pitch": 60, "time_bars": -5.0}],
        [{"pitch": 60, "length_bars": -1.0}],
        [{"pitch": 60, "velocity": 7.0}],
        ["not-a-dict"],
    ):
        with pytest.raises(ValueError):
            pr._validate_notes(bad)


def test_apply_notes_rejects_multiple_actions(monkeypatch, tmp_path):
    """Audit N3: notes + transpose silently discarded the notes.

    Validation runs BEFORE any write, so no script directory is touched."""
    from fl_studio_mcp import pianoroll as pr
    with pytest.raises(ValueError):
        pr.apply_notes([{"pitch": 60, "time_bars": 0, "length_bars": 1}], transpose=2)


def test_dry_run_never_retargets_the_editor(monkeypatch):
    """Audit N5: dry-run retargeted pattern+channel (persisted project state)."""
    from fl_studio_mcp.tools import pianoroll as prtools

    calls = []

    class Bridge:
        def call(self, cmd, params=None, **k):
            calls.append((cmd, params))
            return {}

    safety.set_dry_run(True)
    try:
        res = prtools._ensure_piano_roll(Bridge(), 2, 3)
    finally:
        safety.set_dry_run(False)
    assert calls == [], "dry-run must not issue bridge calls"
    assert res.get("dry_run") is True


def test_unknown_command_is_answered_not_dropped(monkeypatch):
    """Audit D6/N: handler exceptions must produce a JSON reply."""
    class Boom:
        def call(self, *a, **k):
            raise ValueError("kaboom")
    monkeypatch.setattr(daemon_mod, "_get_bridge", lambda: Boom())
    res = _handle_request({"op": "call", "cmd": "get_tempo", "params": {}})
    assert res["ok"] is False and "kaboom" in res["error"]


def test_daemon_token_gate(monkeypatch):
    """Audit N12: optional shared secret for the loopback hatch."""
    monkeypatch.setenv("FLSTUDIO_MCP_DAEMON_TOKEN", "s3cret")
    assert _handle_request({"op": "health"})["code"] == "unauthorized"
    monkeypatch.setattr(daemon_mod, "_get_bridge", lambda: (_ for _ in ()).throw(
        daemon_mod.FLPortMissing("no ports")))
    res = _handle_request({"op": "health", "token": "s3cret"})
    assert res["alive"] is False and "error" in res


# --------------------------------------------------------------------------
# T-lane: tools / safety
# --------------------------------------------------------------------------

def test_step_snapshot_with_read_errors_is_refused(monkeypatch):
    """Audit T5/C2: rollback from an incomplete read wrote invented rows."""
    class Bridge:
        def call(self, cmd, params=None, **k):
            return {"grid": [None], "vel": [None], "read_errors": {"grid": 1}}

    with pytest.raises(RuntimeError):
        safety.take_snapshot(Bridge(), "channel_steps:1:2")


def test_safe_write_verify_mismatch_reports_failure(monkeypatch, tmp_path):
    """Audit T4: verify only retried -- FL no-ops were reported ok:true."""
    safety._log = safety.ChangeLog(tmp_path / "changes.jsonl")

    class Bridge:
        def call(self, cmd, params=None, **k):
            return {}

    monkeypatch.setattr(safety, "_read_scope_with_poll", lambda *a, **k: {"mute": False})
    res = safety.safe_write(
        Bridge(), tool="t", scope="mixer_track:1", command="mixer_set_mute",
        params={"track": 1},
        build_restore=lambda before: {"command": "mixer_set_mute", "params": {}},
        verify=("mute", True),
    )
    assert res["ok"] is False
    assert res["verified"] is False
    assert "readback mismatch" in res["error"]


def test_piano_roll_writes_without_trigger_log_no_undo(monkeypatch, tmp_path):
    """Audit N4: rollback used to be logged even when nothing reached FL."""
    safety._log = safety.ChangeLog(tmp_path / "changes.jsonl")
    res = safety.safe_piano_roll_write(
        object(), tool="t", params={},
        apply=lambda: {"ok": False, "triggered": False, "error": "not armed"},
    )
    assert res["applied"] is False
    assert res["rollback"] is None
    # read the stored entry directly: recent() strips payload fields
    entry = safety._log._dq[-1]
    assert entry.get("restore") is None


def test_bus_layout_rejects_master_as_bus():
    """Audit T3: bus_track=0 emitted src->Master ON then OFF (killed sends)."""
    import asyncio
    import json
    from fastmcp import FastMCP
    from fl_studio_mcp.tools import routing as routing_tools

    calls = []

    class Bridge:
        def call(self, cmd, params=None, **k):
            calls.append((cmd, params or {}))
            if cmd == protocol.CMD_GET_PROJECT_STATE:
                return {"mixer_track_count": 32}
            raise AssertionError(f"no write expected, got {cmd}")

    routing_tools.get_bridge = lambda: Bridge()
    mcp = FastMCP(name="routing-audit")
    routing_tools.register(mcp)
    out = asyncio.run(mcp.call_tool(
        "fl_apply_bus_layout",
        {"buses": [{"bus_track": 0, "name": "DRUMS", "source_tracks": [1, 2]}]},
    ))
    payload = getattr(out, "structured_content", None) or out
    if isinstance(payload, list):
        payload = payload[0]
    text = getattr(payload, "text", None)
    data = json.loads(text) if text else payload
    if "result" in data:
        data = data["result"]
    assert data.get("ok") is False
    assert not [c for c in calls if "route" in str(c[0])], "no route write may be issued"


def test_mix_doctor_plans_by_index_not_name():
    """Audit R1: duplicate track names double-trimmed one track."""
    from fl_studio_mcp.music import mix_doctor as md

    def T(i, n, peak, vol):
        return {"index": i, "name": n, "peak_db": peak, "vol_db": vol, "mute": False,
                "pan": 0.0, "stereo_sep": 0.0, "dests": [], "plugins": []}

    snap = {"playing": True, "levels_valid": True,
            "tracks": [T(0, "Master", -0.4, 0.0), T(3, "Kick", -0.4, 0.0), T(7, "Kick", -0.6, 0.0)]}
    plans = md.plan_fixes(snap)["plans"]
    idxs = sorted(p["track"] for p in plans if p.get("kind") == "trim_volume")
    assert idxs == [3, 7], f"both duplicate-named tracks must be planned: {plans}"


def test_scale_enharmonic_spellings_are_total():
    """Audit R3: E#/B# raised KeyError while Cb/Fb raised ValueError."""
    from fl_studio_mcp.music import scales
    # this module uses the FL-display convention (C4 = 48, C5 = 60)
    assert scales.parse_root_note("E#4") == 53   # == F4
    assert scales.parse_root_note("B#4") == 60   # == C5
    assert scales.parse_root_note("Cb4") == 47   # == B3
    assert scales.parse_root_note("Fb4") == 52   # == E4
    with pytest.raises(ValueError):
        scales.parse_root_note("H4")


def test_fl_version_assurance_classifies_builds():
    """Audit V1: fl_version was shipped by the controller but never consumed."""
    assert protocol.fl_version_assurance("21.2.2 [build 3914]")["fl_version_measured"] is True
    assert protocol.fl_version_assurance("25.2.5 [build 5055]")["fl_version_measured"] is True
    other = protocol.fl_version_assurance("99.0.0-beta7")
    assert other["fl_version_measured"] is False
    assert "unproven" in other["fl_version_warning"]
    assert "FL_VERSION_SUPPORT.md" in other["fl_version_warning"]
