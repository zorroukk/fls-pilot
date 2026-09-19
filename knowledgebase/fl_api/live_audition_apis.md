# Live Audition APIs (preview note, track arm, loop mode)

**Date**: 2026-09-19
**Agent/Author**: Hermes (experimental profile) / Nicholas
**Topic**: Availability and behavior of the three FL device APIs backing the Live Audition Pack
**Affected File/API**: `protocol.CMD_CHANNEL_PREVIEW_NOTE` (`channels.midiNoteOn`/`midiNoteOff`),
`protocol.CMD_MIXER_SET_ARM` (`mixer.armTrack`/`isTrackArmed`),
`protocol.CMD_TRANSPORT_SET_LOOP_MODE` (`transport.getLoopMode`/`setLoopMode`)
**Confidence Level**: hypothesis

## Context
The karl-andres/fl-studio-mcp audit (2026-09-19, GitHub lane) surfaced four user-facing
capabilities our 166-tool surface lacked. Three require FL API calls that our own
controller (through channels-v43) has never exercised on this machine's FL
Producer Edition v21.2.2 build 3914: real-time note triggering, mixer record-arm,
and transport loop-mode toggling. The fourth (selected channel) already existed as
`channel.get_selected`.

## Observation (static evidence only)
- The karl-andres controller script calls `channels.midiNoteOn(channel, note, velocity, midi_channel)`
  and `mixer.armTrack(track)` / `mixer.isTrackArmed(track)` with no midiNoteOff counterpart;
  whether FL auto-releases such notes, and whether `channels.midiNoteOff` exists at all on
  21.2.2, is untested here. Our handler schedules note-off device-side from `OnIdle`
  (explicit off, falling back to velocity-0 note-on) precisely because auto-release
  is not trusted.
- `transport.getLoopMode()` is assumed to return 1 for song and 0 for pattern (karl's
  lineage assumption); the actual return type/value range on 21.2.2 is unverified.
  `transport.setLoopMode()` is a bare toggle, not a setter.
- Arm state participates in FLP persistence, so `set_arm` rides the write-safe
  rollback path and gains an `arm` field in every `mixer_track:N` snapshot.

## Tested Values
None yet -- all three are probe-guarded (`getattr` + `code=api_unavailable` before any
mutation). Offline registry/tool tests pass (tests/test_live_audition_pack.py).

## Source/Method
Static audit of `%LOCALAPPDATA%/Temp/fl-studio-mcp-karla` (clone audited 2026-09-19,
see vault repo-assessments-2026-09-19-flstudio-mcp.md) + local FL 21.2.2 manual mirror
(no per-function reference coverage for these module calls in the manual mirror).

## Reproduction Steps (live battery, next stack bring-up)
1. `ping` -> expect build `channels-v44`.
2. `api_probe` dir op for `channels`, `mixer`, `transport` -> record presence of
   `midiNoteOn`, `midiNoteOff`, `armTrack`, `isTrackArmed`, `getLoopMode`, `setLoopMode`.
3. `channel.preview_note` on the `MF MIDI` channel -> confirm audible Freak blip AND a
   balanced note-off in the relay sendlog within ~10 ms of the hold window; repeat with
   daemon killed mid-hold to prove OnIdle auto-release.
4. `mixer.set_arm` on a spare track -> verify readback, rollback, changelog entry.
5. `transport.set_loop_mode` song/pattern both ways -> verify `get_play_state.loop_mode`
   agrees with the FL toolbar state (confirms the 0/1 mapping direction).

## Open Questions
- Does FL 21.2.2 `midiNoteOn` latch (needing our off) or auto-release (making our off redundant but harmless)?
- Is the loop-mode 0/1 mapping inverted on this build?
- Do arm APIs exist at all on 21.2.2, or is arm a newer-API addition (karl develops on FL 26)?

## Next Recommended Action
Run the live battery at next `flstudio-stack-startup.bat` bring-up; raise confidence to
`live-probed`/demote to `api-limited` in `live_audition_apis.json` accordingly, and update
ROADMAP P0.6 checkpoint + API capability audit evidence levels in the same pass.
