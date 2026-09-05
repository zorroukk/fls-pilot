"""Unit tests for v42 chunked SysEx frames (frame-ceiling workaround).

The MIDI transport on Windows/loopMIDI silently drops single SysEx messages
over ~1024 bytes (bisected 2026-09-05: 983 B passes, 1023 B fails). Large
messages therefore ship as multiple frames prefixed 7E <cont> <seq>.

No FL Studio or MIDI ports required: FLBridge is exercised by feeding
_on_midi synthetic messages directly, never calling open().
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from fl_studio_mcp import protocol
from fl_studio_mcp.connection import FLBridge


def _sixtyfour_step_payload() -> dict:
    """The realistic 64-step grid payload that blocked under v41 (~2.9 KB)."""
    data = {
        "channel": 1,
        "pattern": 1,
        "grid": [bool(i % 3) for i in range(64)],
        "vel": [round(0.5 + i * 0.001, 4) for i in range(64)],
        "pan": [0.0] * 64,
        "shift": [0.0] * 64,
        "rep": [0] * 64,
        "release": [0.0] * 64,
        "mod": [0.0] * 64,
        "pitch": [0] * 64,
        "capabilities": {"release": True, "mod": True, "pitch": True},
    }
    return {"v": protocol.PROTOCOL_VERSION, "ok": True, "data": data}


# -- encode side -------------------------------------------------------------


def test_small_message_stays_single_legacy_frame() -> None:
    payload = {"v": 2, "ok": True, "data": {"bpm": 140.0}}
    body = protocol.encode_message(protocol.DIR_RESPONSE, "abc12345", payload)
    frames = protocol.encode_frames(protocol.DIR_RESPONSE, "abc12345", payload)
    assert frames == [body]  # byte-identical to the legacy wire format
    assert frames[0][0] == protocol.SYSEX_MANUFACTURER  # 7D, not 7E


def test_large_message_splits_under_frame_ceiling() -> None:
    frames = protocol.encode_frames(
        protocol.DIR_RESPONSE, "abc12345", _sixtyfour_step_payload()
    )
    assert len(frames) > 1
    for seq, frame in enumerate(frames):
        # 12 B message header + 3 B chunk header + F0/F7 framing
        assert len(frame) + 15 < 1024, f"frame {seq} exceeds transport ceiling"
        assert frame[0] == protocol.CHUNK_MAGIC
        assert frame[2] == (seq & 0x7F)
        expected_cont = 1 if seq < len(frames) - 1 else 0
        assert frame[1] == expected_cont


# -- reassembler -------------------------------------------------------------


def test_reassembler_roundtrips_large_message() -> None:
    frames = protocol.encode_frames(
        protocol.DIR_RESPONSE, "abc12345", _sixtyfour_step_payload()
    )
    r = protocol.ChunkReassembler()
    assembled = None
    for frame in frames:
        assembled = r.feed(frame)
    assert assembled is not None
    direction, rid, payload = protocol.decode_message(assembled)
    assert (direction, rid) == (protocol.DIR_RESPONSE, "abc12345")
    assert payload["ok"] is True
    assert payload["data"]["channel"] == 1


def test_reassembler_passes_legacy_and_heartbeat_through() -> None:
    r = protocol.ChunkReassembler()
    legacy = protocol.encode_message(
        protocol.DIR_RESPONSE, "abc12345", {"v": 2, "ok": True, "data": {}}
    )
    assert r.feed(legacy) == legacy
    hb = protocol.encode_message(protocol.DIR_HEARTBEAT, "00000000", {"v": 2})
    assert r.feed(hb) == hb


def test_heartbeat_interleaving_does_not_corrupt_reassembly() -> None:
    frames = protocol.encode_frames(
        protocol.DIR_RESPONSE, "abc12345", _sixtyfour_step_payload()
    )
    hb = protocol.encode_message(protocol.DIR_HEARTBEAT, "00000000", {"v": 2})
    r = protocol.ChunkReassembler()
    assembled = None
    for frame in frames:
        assert r.feed(hb) == hb  # passthrough, buffer untouched
        assembled = r.feed(frame)
    assert assembled is not None
    assert protocol.decode_message(assembled)[2]["ok"] is True


def test_gap_in_seq_resyncs_and_restarts_cleanly() -> None:
    frames = protocol.encode_frames(
        protocol.DIR_RESPONSE, "abc12345", _sixtyfour_step_payload()
    )
    r = protocol.ChunkReassembler()
    # Drop frame 1 (gap). The stale continuation must be ignored...
    assert r.feed(frames[0]) is None
    assert r.feed(frames[2]) is None
    # ...and a retried fresh message must reassemble from scratch.
    assembled = None
    for frame in frames:
        assembled = r.feed(frame)
    assert assembled is not None


# -- FLBridge receive path ----------------------------------------------------


class _FakeSlot:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: dict | None = None


def _bare_bridge() -> FLBridge:
    bridge = FLBridge.__new__(FLBridge)  # skip __init__: no mido/ports needed
    bridge._lock = threading.Lock()
    bridge._pending = {}
    bridge._last_heartbeat = 0.0
    bridge._heartbeat_payload = None
    bridge._reassembler = protocol.ChunkReassembler()
    return bridge


def _sysex(data: bytes) -> SimpleNamespace:
    return SimpleNamespace(type="sysex", data=data)


def test_bridge_on_midi_reassembles_chunked_response() -> None:
    bridge = _bare_bridge()
    slot = _FakeSlot()
    bridge._pending["abc12345"] = slot
    frames = protocol.encode_frames(
        protocol.DIR_RESPONSE, "abc12345", _sixtyfour_step_payload()
    )
    for frame in frames[:-1]:
        bridge._on_midi(_sysex(frame))
        assert not slot.event.is_set()  # still incomplete
    bridge._on_midi(_sysex(frames[-1]))
    assert slot.event.is_set()
    assert slot.payload["data"]["pattern"] == 1


def test_bridge_on_midi_heartbeat_between_frames_keeps_bridge_alive() -> None:
    bridge = _bare_bridge()
    slot = _FakeSlot()
    bridge._pending["abc12345"] = slot
    frames = protocol.encode_frames(
        protocol.DIR_RESPONSE, "abc12345", _sixtyfour_step_payload()
    )
    bridge._on_midi(_sysex(frames[0]))
    bridge._on_midi(_sysex(protocol.encode_message(
        protocol.DIR_HEARTBEAT, "00000000", {"v": 2, "ts": 1.0}
    )))
    assert bridge._last_heartbeat > 0.0  # heartbeat counted despite mid-message
    for frame in frames[1:]:
        bridge._on_midi(_sysex(frame))
    assert slot.event.is_set()


def test_bridge_on_midi_legacy_frame_unchanged_path() -> None:
    bridge = _bare_bridge()
    slot = _FakeSlot()
    bridge._pending["abc12345"] = slot
    legacy = protocol.encode_message(
        protocol.DIR_RESPONSE, "abc12345", {"v": 2, "ok": True, "data": {"x": 1}}
    )
    bridge._on_midi(_sysex(legacy))
    assert slot.event.is_set()
    assert slot.payload["data"] == {"x": 1}
