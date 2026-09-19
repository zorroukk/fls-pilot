"""Shared protocol constants for the FL Studio MCP bridge.

v0.2: All-MIDI transport. The earlier file-queue design assumed the FL
controller script could write JSON files, but FL's controller-script Python
sandbox blocks every form of file write on at least some builds
(confirmed: FL 24+ MIDI scripting v40, Python 3.12.1). We pivoted to a
MIDI SysEx wire format that works on every FL version that supports MIDI
scripting (20.7+).

Wire format (bytes between SysEx F0 and F7):

    7D 4D 43 50 <dir> <id8> <base64_json>

    7D            non-commercial / private manufacturer ID
    4D 43 50      magic, ASCII "MCP" -- lets us ignore unrelated SysEx
    <dir>         0x01 = request, 0x02 = response, 0x03 = heartbeat
    <id8>         8 ASCII chars [a-z0-9], correlates request and response
    <base64_json> base64 of the UTF-8 JSON payload, fits in 7-bit MIDI bytes

The framing F0 ... F7 is added by the MIDI library (mido) on send and
stripped on receive -- protocol-side helpers work on the payload bytes only.
"""

from __future__ import annotations

import base64
import json
import math
import os
import platform
import secrets
import string

# Bump when the wire format changes incompatibly. Server and FL refuse to
# talk to a mismatched peer.
PROTOCOL_VERSION = 2

# How long the server waits for a heartbeat before declaring FL not running.
HEARTBEAT_STALE_SECONDS = 3.0

# Server-side timeout for any single command round trip.
DEFAULT_TIMEOUT_SECONDS = 15.0

# How often the FL controller emits a heartbeat SysEx.
HEARTBEAT_INTERVAL_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Default MIDI port names
# ---------------------------------------------------------------------------
# These names must match what the user creates in loopMIDI (Windows) or in
# the IAC Driver (macOS Audio MIDI Setup). The names are case-insensitive
# and matched as substrings, so e.g. "FLStudioMCP RX 0" from Windows also
# matches "FLStudioMCP RX".

# Port that carries commands FROM the MCP server TO FL Studio.
# Server opens this as OUTPUT. FL opens this as INPUT.
DEFAULT_PORT_TO_FL = "FLStudioMCP RX"

# Port that carries responses + heartbeats FROM FL Studio TO the MCP server.
# Server opens this as INPUT. FL opens this as OUTPUT.
DEFAULT_PORT_FROM_FL = "FLStudioMCP TX"


def port_to_fl_name() -> str:
    return os.environ.get("FLSTUDIO_MCP_PORT_TO_FL", DEFAULT_PORT_TO_FL)


def port_from_fl_name() -> str:
    return os.environ.get("FLSTUDIO_MCP_PORT_FROM_FL", DEFAULT_PORT_FROM_FL)


# ---------------------------------------------------------------------------
# Command catalogue
# ---------------------------------------------------------------------------
# Unchanged from v0.1 so the existing tool layer keeps working. New commands
# get appended here AND in fl_controller/FLStudioMCP/device_FLStudioMCP.py.

# Transport
CMD_PING = "ping"
CMD_GET_TEMPO = "get_tempo"
CMD_SET_TEMPO = "set_tempo"
CMD_GENERAL_UNDO = "general_undo"
CMD_PLAY = "play"
CMD_STOP = "stop"
CMD_TOGGLE_PLAY = "toggle_play"
CMD_RECORD = "record"
CMD_GET_PLAY_STATE = "get_play_state"
CMD_TRANSPORT_SET_LOOP_MODE = "transport_set_loop_mode"
CMD_GET_SONG_POS = "get_song_position"
CMD_SET_SONG_POS = "set_song_position"

# Project (Phase 1) -- aggregate read
CMD_GET_PROJECT_STATE = "get_project_state"

# Mixer (Phase 2)
CMD_MIXER_LIST_TRACKS = "mixer_list_tracks"
CMD_MIXER_GET_TRACK = "mixer_get_track"
CMD_MIXER_SET_VOLUME = "mixer_set_volume"
CMD_MIXER_SET_PAN = "mixer_set_pan"
CMD_MIXER_SET_MUTE = "mixer_set_mute"
CMD_MIXER_SET_SOLO = "mixer_set_solo"
CMD_MIXER_SET_ARM = "mixer_set_arm"
CMD_MIXER_SET_NAME = "mixer_set_name"
CMD_MIXER_SELECTED = "mixer_selected"
CMD_MIXER_SELECT_TRACK = "mixer_select_track"
CMD_MIXER_SET_STEREO_SEP = "mixer_set_stereo_sep"

# Channels (Phase 1)
CMD_CHANNEL_LIST = "channel_list"
CMD_CHANNEL_GET = "channel_get"
CMD_CHANNEL_SET_VOLUME = "channel_set_volume"
CMD_CHANNEL_SET_PAN = "channel_set_pan"
CMD_CHANNEL_SET_MUTE = "channel_set_mute"
CMD_CHANNEL_SET_SOLO = "channel_set_solo"
CMD_CHANNEL_SELECT = "channel_select"
CMD_CHANNEL_SELECTED = "channel_selected"
CMD_CHANNEL_PREVIEW_NOTE = "channel_preview_note"
CMD_CHANNEL_SET_NAME = "channel_set_name"
CMD_CHANNEL_SET_TARGET = "channel_set_target"

# Patterns (Phase 3)
CMD_PATTERN_LIST = "pattern_list"
CMD_PATTERN_SELECT = "pattern_select"
CMD_PATTERN_RENAME = "pattern_rename"
CMD_PATTERN_GET_LENGTH = "pattern_get_length"

# Plugin params (Phase 1B)
CMD_PLUGIN_LIST = "plugin_list"  # list plugins on a mixer track's slots
CMD_PLUGIN_GET_PARAMS = "plugin_get_params"  # paginated param dump for one plugin
CMD_PLUGIN_LIST_PARAMS = "plugin_list_params"
CMD_PLUGIN_GET_PARAM = "plugin_get_param"
CMD_PLUGIN_SET_PARAM = "plugin_set_param"

# Routing / grouping / cleanup (read surface -- Slice 1)
CMD_MIXER_GET_ROUTING = "mixer_get_routing"  # one track's send destinations
CMD_MIXER_GET_ROUTING_ALL = "mixer_get_routing_all"  # paginated routing matrix
CMD_MIXER_GET_FREE_TRACK = "mixer_get_free_track"
CMD_CHANNEL_ROUTING_SUMMARY = "channel_routing_summary"  # channel -> mixer links

# Routing writes (Slice 2)
CMD_MIXER_SET_ROUTE = "mixer_set_route"  # setRouteTo + afterRoutingChanged

# Level awareness (read) -- meter peaks, meaningful only during playback
CMD_MIXER_GET_PEAKS = "mixer_get_peaks"  # getTrackPeaks L/R/max

# Track / channel color (RGB int 0xRRGGBB). Set accepts r/g/b 0-255 (fresh) or
# an explicit "color" int (rollback re-sends the exact int FL gave us).
CMD_MIXER_SET_COLOR = "mixer_set_color"
CMD_MIXER_GET_COLOR = "mixer_get_color"
CMD_CHANNEL_SET_COLOR = "channel_set_color"
CMD_CHANNEL_GET_COLOR = "channel_get_color"

# Plugin presets (navigate/read) -- op: info | next | prev
CMD_PLUGIN_PRESET = "plugin_preset"  # getPresetCount/next/prev/getName
CMD_PLUGIN_GET_PRESET_NAME = "plugin_get_preset_name"

# API introspection / arrangement probe -- op: dir | ppq | marker_add | undo
CMD_API_PROBE = "api_probe"

# Arrangement primitives (Slice 1) -- pattern create/clone + section markers
CMD_ARRANGE_NEW_PATTERN = "arrange_new_pattern"  # find empty + jumpTo + name
CMD_ARRANGE_CLONE_PATTERN = "arrange_clone_pattern"  # clonePattern + rename
CMD_ARRANGE_ADD_MARKER = "arrange_add_marker"  # addAutoTimeMarker at a bar

# Note-bridge hardening -- ensure the Piano roll is open before a note-write
CMD_ENSURE_PIANO_ROLL = "ensure_piano_roll"  # open/focus Piano Roll, optionally retarget

# Phase A safety baseline expansion commands
CMD_CHANNEL_GET_STEPS = "channel_get_steps"
CMD_PATTERN_GET = "pattern_get"
CMD_PATTERN_SELECTED = "pattern_selected"
CMD_PATTERN_SET_COLOR = "pattern_set_color"
CMD_PATTERN_SET_LENGTH = "pattern_set_length"
CMD_PATTERN_FIND_EMPTY = "pattern_find_empty"
CMD_PLAYLIST_GET_TRACK = "playlist_get_track"
CMD_PLAYLIST_LIST_TRACKS = "playlist_list_tracks"
CMD_PLAYLIST_SET_MUTE = "playlist_set_mute"
CMD_PLAYLIST_SET_SOLO = "playlist_set_solo"
CMD_PLAYLIST_SET_NAME = "playlist_set_name"
CMD_PLAYLIST_SET_COLOR = "playlist_set_color"
CMD_PLAYLIST_SELECT_TRACK = "playlist_select_track"
CMD_MIXER_GET_SLOT = "mixer_get_slot"
CMD_MIXER_SET_SLOT_MIX = "mixer_set_slot_mix"
CMD_MIXER_GET_TRACK_SLOTS = "mixer_get_track_slots"
CMD_MIXER_SET_TRACK_SLOTS = "mixer_set_track_slots"
CMD_MIXER_SET_SLOT_ENABLED = "mixer_set_slot_enabled"
CMD_MIXER_GET_EQ = "mixer_get_eq"
CMD_MIXER_SET_EQ = "mixer_set_eq"
CMD_MIXER_PROBE_EQ_TYPE = "mixer_probe_eq_type"
CMD_GET_TIME_SIG = "get_time_sig"
CMD_SET_TIME_SIG = "set_time_sig"

# Phase 1 Step Sequencer commands
CMD_CHANNEL_SET_STEPS = "channel_set_steps"


# ---------------------------------------------------------------------------
# SysEx wire format
# ---------------------------------------------------------------------------

SYSEX_MANUFACTURER = 0x7D  # MIDI-spec reserved for private use
SYSEX_MAGIC = (0x4D, 0x43, 0x50)  # ASCII "MCP"

DIR_REQUEST = 0x01
DIR_RESPONSE = 0x02
DIR_HEARTBEAT = 0x03

REQUEST_ID_LEN = 8
REQUEST_ID_ALPHABET = string.ascii_lowercase + string.digits

# Total header length: 1 (manuf) + 3 (magic) + 1 (dir) + REQUEST_ID_LEN.
_HEADER_LEN = 1 + 3 + 1 + REQUEST_ID_LEN

# Chunked frames (controller build channels-v42+). Windows/loopMIDI silently
# drops single SysEx messages over ~1024 bytes (bisected 2026-09-05: 983 B
# passes, 1023 B fails). A body longer than _MAX_CHUNK_BODY ships as multiple
# frames whose data (after F0) is: 7E <cont> <seq> <body bytes...>, where
# cont=1 means more frames follow and seq is the 0-based frame index (7 bits).
# Bodies that fit stay in the legacy single 7D frame, so any old/new pairing
# (v41 device <-> v42 server or reverse) keeps working in both directions;
# only genuinely oversized messages use the new format. 900 B body + 12 B
# message header + 3 B chunk header + F0/F7 framing = ~918 B on the wire,
# ~100 B under the measured ceiling. 7E is never byte 0 of a legacy message
# (manufacturer IDs are 0x00-0x7D), so the two formats are unambiguous.
CHUNK_MAGIC = 0x7E
_MAX_CHUNK_BODY = 900
_CHUNK_HEADER_LEN = 3
_MAX_REASSEMBLY_BYTES = 65536  # runaway-buffer guard; legit payloads stay far under


def encode_frames(direction: int, request_id: str, payload: dict) -> list[bytes]:
    """Encode a message as one or more SysEx *data* payloads (no F0/F7).

    Returns a list so callers can emit each frame as its own MIDI message.
    Small payloads yield a single legacy frame; oversized payloads split
    across chunk frames per the format documented on CHUNK_MAGIC above.
    """
    body = encode_message(direction, request_id, payload)
    if len(body) <= _MAX_CHUNK_BODY:
        return [body]
    n = -(-len(body) // _MAX_CHUNK_BODY)  # ceil
    frames = []
    for seq in range(n):
        chunk = body[seq * _MAX_CHUNK_BODY : (seq + 1) * _MAX_CHUNK_BODY]
        cont = 1 if seq < n - 1 else 0
        frames.append(bytes([CHUNK_MAGIC, cont, seq & 0x7F]) + chunk)
    return frames


class ChunkReassembler:
    """Reassemble chunked inbound frames back into the original data payload.

    feed() takes one SysEx data payload (bytes, after F0/F7 are stripped).
    It returns the fully-assembled payload bytes once the last frame of a
    message arrives, or None while more frames are still expected (or when
    the frame is not a chunk frame this reassembler should swallow).

    Legacy single frames pass straight through (returned as-is), so a
    mixed old/new stream is handled without special-casing.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._expect = 0  # next seq we want within the in-progress message

    def reset(self) -> None:
        self._buf = bytearray()
        self._expect = 0

    def feed(self, data: bytes) -> bytes | None:
        if len(data) < _CHUNK_HEADER_LEN or data[0] != CHUNK_MAGIC:
            # Legacy / heartbeat / foreign frame: decode path handles it
            # directly. Do NOT touch the in-progress chunk buffer -- a
            # heartbeat can legally interleave between frames of a large
            # multi-frame message (MIDI baud makes one frame ~190 ms).
            return bytes(data)
        cont = data[1]
        seq = data[2]
        # Out-of-order or gap: a fresh seq==0 restarts cleanly; anything else
        # mid-stream is unrecoverable on this transport, so drop and resync.
        if seq != self._expect:
            self.reset()
            if seq != 0:
                return None
        self._buf.extend(data[_CHUNK_HEADER_LEN:])
        if len(self._buf) > _MAX_REASSEMBLY_BYTES:
            self.reset()
            return None
        self._expect = (seq + 1) & 0x7F
        if cont:
            return None
        assembled = bytes(self._buf)
        self.reset()
        return assembled


def new_request_id() -> str:
    return "".join(secrets.choice(REQUEST_ID_ALPHABET) for _ in range(REQUEST_ID_LEN))


def _reject_json_constant(value: str):
    raise ValueError(f"Non-finite JSON constant is not allowed: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite JSON number is not allowed: {value}")
    return parsed


def encode_message(direction: int, request_id: str, payload: dict) -> bytes:
    """Build the SysEx payload bytes (everything between F0 and F7).

    The serializer (mido.Message('sysex', data=...)) will add the framing.
    """
    if direction not in (DIR_REQUEST, DIR_RESPONSE, DIR_HEARTBEAT):
        raise ValueError(f"Bad direction: {direction!r}")
    rid = request_id.encode("ascii")
    if len(rid) != REQUEST_ID_LEN or any(b > 0x7F for b in rid):
        raise ValueError(f"Bad request id: {request_id!r}")
    if not isinstance(payload, dict):
        raise ValueError("Protocol payload must be a dictionary")

    body_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    body_b64 = base64.b64encode(body_json.encode("ascii"))

    out = bytearray()
    out.append(SYSEX_MANUFACTURER)
    out.extend(SYSEX_MAGIC)
    out.append(direction & 0x7F)
    out.extend(rid)
    out.extend(body_b64)
    return bytes(out)


def decode_message(data) -> tuple[int, str, dict] | None:
    """Decode a SysEx payload. Returns None if not one of ours.

    ``data`` is the bytes between F0 and F7 (mido strips the framing).
    Accepts bytes, bytearray, or any iterable of ints in [0, 127].
    """
    buf = bytes(data)
    if len(buf) < _HEADER_LEN:
        return None
    if buf[0] != SYSEX_MANUFACTURER:
        return None
    if tuple(buf[1:4]) != SYSEX_MAGIC:
        return None
    direction = buf[4]
    rid = buf[5 : 5 + REQUEST_ID_LEN].decode("ascii", errors="replace")
    body = buf[_HEADER_LEN:]
    try:
        decoded_body = base64.b64decode(body, validate=True)
        if base64.b64encode(decoded_body) != body:
            return None
        body_json = decoded_body.decode("utf-8")
        payload = json.loads(
            body_json,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
        if not isinstance(payload, dict):
            return None
    except Exception:
        return None
    return direction, rid, payload


# ---------------------------------------------------------------------------
# Request / response envelope shapes
# ---------------------------------------------------------------------------


def make_request(command: str, params: dict | None = None) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "cmd": command,
        "params": params or {},
    }


def make_response_ok(data) -> dict:
    return {"v": PROTOCOL_VERSION, "ok": True, "data": data}


def make_response_err(error: str, *, code: str = "error") -> dict:
    return {"v": PROTOCOL_VERSION, "ok": False, "error": error, "code": code}


def system_label() -> str:
    """Short OS label, useful in heartbeats and logs."""
    return f"{platform.system()} {platform.release()}"
