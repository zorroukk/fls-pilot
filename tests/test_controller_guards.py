#!/usr/bin/env python3
"""Controller-script guards (audit 2026-10-21, lane C/V).

Loads fl_controller/FLStudioMCP/device_FLStudioMCP.py with fake FL API modules
(nothing touches FL, MIDI, or a port) and pins the defects that the offline
suite could never see, because it never executes the controller script.

Each test names the audit id it guards; see docs/AUDIT_2026-10-21.md.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CTRL = REPO / "fl_controller" / "FLStudioMCP" / "device_FLStudioMCP.py"

FL_MODULES = (
    "channels", "device", "general", "midi", "mixer", "patterns",
    "playlist", "plugins", "transport", "ui", "arrangement", "utils",
)


def mod(name, **attrs):
    m = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(m, key, value)
    return m


def midi_stub(**overrides):
    base = dict(
        pVelocity=1, pPan=2, pShift=3, pRepeat=4, pModX=5, pPitch=6, pFinePitch=7,
        REC_UpdateValue=1, REC_UpdateControl=2, REC_Control=4,
        REC_Tempo=1000, REC_Chan_PianoRoll=2000,
        REC_Mixer_EQ_Type=3000, REC_Mixer_EQ_Gain=3001,
        REC_Mixer_EQ_Freq=3002, REC_Mixer_EQ_Q=3003,
        CT_Sampler=0, CT_Hybrid=1, CT_GenPlug=2, CT_AudioClip=3,
        CT_AutoClip=4, CT_Layer=5, CT_Envelope=6, CT_MIDIOut=7,
        widPianoRoll=1, EE_PR=1,
    )
    base.update(overrides)
    return mod("midi", **base)


def load_controller(modules, name="flctrl_test"):
    for key in FL_MODULES:
        sys.modules.pop(key, None)
    for key, value in modules.items():
        sys.modules[key] = value
    for key in FL_MODULES:
        if key not in sys.modules:
            sys.modules[key] = types.ModuleType(key)
    spec = importlib.util.spec_from_file_location(name, str(CTRL))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def ctrl():
    """A controller module with minimal fake FL modules (fresh per test)."""
    calls = {"sysex": [], "jumps": [], "step_writes": []}

    def send_sysex(framed):
        calls["sysex"].append(bytes(framed))

    device = mod(
        "device", midiOutSysex=send_sysex, getPortNumber=lambda: 42,
        isAssigned=lambda: True, setHasMeters=lambda: None, getName=lambda: "FLStudioMCP",
    )
    ui = mod("ui", getVersion=lambda: "21.2.2 [build 3914]")
    patterns = mod(
        "patterns", patternCount=lambda: 4, patternNumber=lambda: 2,
        jumpToPattern=lambda n: calls["jumps"].append(int(n)),
        getPatternName=lambda i: "P%d" % i,
    )
    channels = mod(
        "channels", getTrackCount=lambda: 1,
        getGridBit=lambda idx, s: False,
        getStepParam=lambda *a, **k: 0,
        setGridBit=lambda *a, **k: True,
        setStepParameterByIndex=lambda *a, **k: calls["step_writes"].append(a),
        getChannelVolume=lambda i, *a: 0.8 if not a else -1.94,
        midiNoteOn=lambda *a, **k: None,
        updateGraphEditor=lambda: None,
        channelNumber=lambda *a: 1,
        selectOneChannel=lambda *a: None,
        getChannelName=lambda i: "ch",
    )
    mixer = mod(
        "mixer", getTrackVolume=lambda i, *a: -6.0, trackCount=lambda: 4,
        getCurrentTempo=lambda: 120000, getTrackName=lambda i: "Track %d" % i,
        getFreeTrack=lambda: 3, getPluginMixLevel=lambda t, s: 0.0,
    )
    plugins = mod("plugins", isValid=lambda t, s: True, getPluginName=lambda t, s: "x",
                  getPluginCount=lambda t: 0)
    general = mod("general", getRecPPB=lambda: 384, getRecPPQ=lambda: 96,
                  processRECEvent=lambda *a: None, undoUp=lambda: None)
    transport = mod("transport", isPlaying=lambda: False)
    playlist = mod("playlist", trackCount=lambda: 1)
    utils = mod("utils", RGBToColor=lambda r, g, b: 0)
    arrangement = types.ModuleType("arrangement")
    m = load_controller({
        "device": device, "ui": ui, "patterns": patterns, "channels": channels,
        "mixer": mixer, "plugins": plugins, "general": general,
        "transport": transport, "playlist": playlist, "utils": utils,
        "arrangement": arrangement, "midi": midi_stub(),
    })
    m._audit_calls = calls
    return m


def test_ping_reports_build_marker_and_midi_port(ctrl):
    """Audit C7/C12: ping must state what is deployed and where it is bound."""
    out = ctrl._h_ping({})
    assert out["build"] == "channels-v45"
    assert out["midi_port"] == 42
    assert out["protocol_version"] == ctrl.PROTOCOL_VERSION


def test_fader_at_zero_never_yields_non_finite_db(ctrl):
    """Audit C1: -inf dB escaped the handler guard (silent-death class)."""
    ctrl.mixer.getTrackVolume = lambda i, *a: float("-inf")
    out = ctrl._mixer_vol_out(1, 0.0)
    assert out["vol_db"] == -120.0
    ctrl._json_dumps(out)  # the protocol encoder must accept it


def test_jump_to_pattern_rejects_nonpositive(ctrl):
    """Audit V3: upstream reports FL 26.x CRASHES on jumpToPattern(<0)."""
    with pytest.raises(ctrl._ClientError):
        ctrl._jump_to_pattern(0)
    with pytest.raises(ctrl._ClientError):
        ctrl._jump_to_pattern(-3)
    ctrl._jump_to_pattern(2)
    assert ctrl._audit_calls["jumps"] == [2]


def test_get_steps_reports_nulls_and_read_errors(ctrl):
    """Audit C2/T5: fabricated read defaults became the rollback snapshot."""
    def boom(*a, **k):
        raise RuntimeError("driver hiccup")
    ctrl.channels.getGridBit = boom
    ctrl.channels.getStepParam = boom
    out = ctrl._h_channel_get_steps({"channel": 0, "steps": 4})
    assert out["grid"] == [None, None, None, None]
    assert out["vel"] == [None, None, None, None]
    assert out["read_complete"] is False
    assert out["read_errors"]["grid"] == 4
    assert out["read_errors"]["vel"] == 4


def test_set_steps_uses_the_same_pattern_basis_as_reads(ctrl):
    """Audit C4/C3: writes used pattern-1 and useGlobalIndex=True."""
    ctrl.channels.getStepParam = lambda *a, **k: 0
    ctrl._h_channel_set_steps(
        {"channel": 2, "pattern": 3,
         "steps": [{"step": 0, "value": True, "velocity": 0.5, "pan": 0.1}]}
    )
    writes = ctrl._audit_calls["step_writes"]
    assert writes, "expected at least one setStepParameterByIndex call"
    for args in writes:
        # (idx, patNum, step, param, value, useGlobalIndex) -- if the flag is
        # positional, it must be the documented default False; pattern must be
        # the 1-based number the read path uses.
        assert args[1] == 3, f"pattern basis drifted: {args!r}"
        if len(args) > 5:
            assert args[5] in (False, None, 0), f"global index flag: {args!r}"


def test_failed_preview_release_retries_then_reports(ctrl, capsys):
    """Audit C5: a failed release used to be dropped with no retry and no log."""
    def boom(*a, **k):
        raise RuntimeError("device busy")
    ctrl.channels.midiNoteOn = boom
    ctrl.channels.midiNoteOff = None
    ctrl._pending_notes.append((1, 60, 0.0, 0))
    ctrl._flush_due_notes(10.0)
    assert ctrl._pending_notes, "failed release must stay queued for retry"
    ctrl._flush_due_notes(10.1)
    assert ctrl._pending_notes, "one retry must still be pending"
    ctrl._flush_due_notes(10.2)
    assert not ctrl._pending_notes, "after the retry budget the entry is given up"
    out = capsys.readouterr().out
    assert "COULD NOT BE RELEASED" in out


def test_on_deinit_releases_held_notes(ctrl):
    """Audit C6: reload/deinit used to strand a held note-on on hardware."""
    released = []
    ctrl.channels.midiNoteOn = lambda ch, note, vel, chn=-1: released.append((ch, note, vel))
    ctrl.channels.midiNoteOff = None
    ctrl._pending_notes.append((2, 64, 999.0, 0))
    ctrl.OnDeInit()
    assert released == [(2, 64, 0)], "OnDeInit must send a velocity-0 release"
    assert ctrl._pending_notes == []
