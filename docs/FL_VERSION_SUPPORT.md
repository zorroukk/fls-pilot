# FL Studio version support

**Short version:** this bridge's live evidence exists for **one build** —
FL Studio Producer Edition **v21.2.2 build 3914** (Windows) — plus calibration
data inherited from upstream that was verified on **v25.2.5 build 5055**.
Nothing blocks other builds; plenty of it is simply unmeasured there. Since the
2026-10-21 audit the bridge *says so at runtime* instead of assuming.

## Runtime handshake (what changed)

`fl_transport(action="ping")` now reports:

| field | meaning |
|---|---|
| `fl_version` | controller's own `ui.getVersion()` string |
| `fl_version_measured` | `true` only when the string matches a measured prefix (`21.2.`, `25.2.`) |
| `fl_version_warning` | present when the build is outside those prefixes: "treat unprobe-guarded tools as UNTESTED" |
| `protocol_version_match` | controller protocol vs server protocol (was previously *claimed* to hard-refuse; it never did — now it is actually checked and surfaced) |
| `controller_build` / `controller_build_stale` | deployed script marker vs the marker this server expects (`channels-v45`): catches the "FL still runs the old script" deploy-drift class |
| `midi_port` / `midi_port_match` | the controller's own `device.getPortNumber()` (set `FLSTUDIO_MCP_EXPECTED_MIDI_PORT` to compare): separates a port-number collision from a dead script |

## Evidence classes (per tool, machine-generated)

The table in [capability_table.generated.md](capability_table.generated.md)
grades every registered tool against the Image-Line manual mirror:

- **A — documented** (114 of 166 tools): the manual documents these APIs since
  MIDI scripting v1–v13; expected to behave across builds.
- **B — manual-silent but live-measured** and/or behind a `getattr` probe
  guard: degrades with a named `api_unavailable` error instead of an opaque
  crash.
- **C — manual-silent and never measured** (3 tools): assumed only; use with
  care and report what you see.

Manual-silent APIs currently in use: `midi.REC_Tempo`,
`channels.updateGraphEditor`, `mixer.getFreeTrack`, `plugins.getPluginCount`,
`channels.midiNoteOff`, `utils.RGBToColor`,
`mixer.getPluginMuteState`/`setPluginMuteState`, `mixer.getRouteSendLevel`.
As of the audit, every one of them is either probe-guarded, defaulted to
*unknown* instead of a fabricated value, or both.

## Known build-dependent behaviours

| behaviour | status |
|---|---|
| per-note velocity through the pyscript path lands as FL default 120 | **measured on 21.2.2**, documented in `knowledgebase/known_pitfalls/velocity_flpianoroll.md`; unverified on newer builds |
| `general.setNumerator`/`setDenominator` (time signature writes) | manual grades them above 21.2.2's scripting-API ceiling; now rejected with a named `api_unavailable` rather than an opaque error |
| `patterns.jumpToPattern(<0)` | upstream reports FL 26.x **crashes**; all call sites now guard with `_jump_to_pattern()` rejecting `< 1` |
| `channels.midiNoteOn` takes a **global** channel index while most handlers are **local** | now resolved explicitly (`_global_channel_for`) with a loud fallback log |
| `setStepParameterByIndex` pattern argument | previously written with `pattern-1` while reads used the 1-based number; unified to the 1-based number (LIVE re-verification still owed) |
| FL 21.2.2 MIDI-input re-broadcast storm | empirical-only (not in the manual); see `known_pitfalls/fl21_midi_input_rebroadcast_storm` |

## If you run a different FL build

1. Run the offline suite: `pytest tests/ -q` (196 tests, no FL needed).
2. With FL open and the controller installed: `fl_transport(action="ping")` —
   expect `fl_version_measured: false` + a warning on an unlisted build.
3. Run the live probes in `scripts/probes/` against the build and record what
   answers with `api_unavailable`.
4. Re-probe the two empirical laws above (velocity persistence; step-parameter
   read-write agreement) and open an issue with the FL build string, the ping
   payload, and what you observed. Build evidence is the most valuable
   contribution to this fork.
