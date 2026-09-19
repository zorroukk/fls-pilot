# MIDI Port-Number Routing Collision (FL 21.2.2)

- **Date:** 2026-09-04
- **Agent/Author:** Hermes flstudio integration agent
- **Topic:** `device.midiOutSysex` routes by Port number only; a duplicate number across Output rows silently hijacks controller→server traffic.
- **Affected File/API:** `fl_controller/FLStudioMCP/device_FLStudioMCP.py` (`OnIdle` heartbeat emission, `midiOutSysex`), FL Studio `Options > MIDI settings`.
- **Context:** After a clean FL Studio restart with a correct `init ok` + `[FLStudioMCP] Ready` banner and zero warnings, the daemon received no heartbeats at all. Both loopMIDI ports existed exactly once; the controller was alive.
- **Observation:** FL 21.2.2 re-numbered MIDI settings rows on restart such that the OUTPUT row `FLStudioMCP RX` also carried Port 42 (same number as `FLStudioMCP TX`). A raw loopMIDI tap showed the script's valid heartbeat SysEx frames (dir=3, MCP magic, 2/sec, matching `HEARTBEAT_INTERVAL = 0.5`) arriving on the RX loopback, never on TX. `midiOutSysex` selects an output row by port number and matched RX first.
- **Tested Values:** Output `FLStudioMCP RX` Port 42 → 10 (unique), TX output stays 42, RX input stays enabled/FLStudioMCP/42; reload script.
- **Result:** `fl_ping` returned `alive: true`, heartbeat_age 0.28 s; all four read-only gate tools (`fl_ping`, `fl_get_tempo`, `fl_get_play_state`, `fl_get_project_state`) returned live data on first call.
- **Confidence Level:** `measured_repeated` (symptom reproduced across the whole post-restart session; fix verified immediately and stable at 08:00).
- **Source/Method:** Raw `mido` tap on both loopMIDI ports; decode via `fl_studio_mcp.protocol`; then end-to-end MCP calls through the registered Hermes `flstudio` server.
- **Reproduction Steps:** In FL MIDI settings, give two OUTPUT rows the same Port number, one of them named like the controller's RX; heartbeats vanish from TX and appear on the RX loopback input.
- **Known Pitfalls:**
  1. The Port number must appear EXACTLY ONCE in the FL Output section — on `FLStudioMCP TX` — matching the controller INPUT row's number.
  2. FL's MIDI settings lists Outputs ABOVE Inputs; UI confusion around "which table" is common.
  3. Bindings are NOT persisted reliably across FL restarts: input enable/controller-type AND port numbers must be rechecked after every restart (second occurrence same day: RX input row lost its `FLStudioMCP` controller assignment).
  4. A `Ready` banner proves neither routing nor daemon pairing — pairing must be proven by heartbeat age over the wire.
- **Open Questions:** Why FL 21.2.2 assigned the RX output row 42 on that restart (auto-numbering vs leftover state) — untested.
- **Next Recommended Action:** Add a startup checklist (input row enabled + controller assigned + unique output port) to agent-facing onboarding docs and consider a `fl_ping`-style wire assertion in the daemon log when heartbeats are absent >30 s.
- **Addendum 2026-09-20 (recurrence, second confirmation):** After another FL restart + F10 churn the Output `FLStudioMCP RX` row carried Port 42 again (duplicate of TX) — same silent-hijack symptom (probe reports dead bridge while script beats into the RX cable). Same fix: RX output -> 15, keep TX output == RX input number. Related compound failure that same night: even with numbers correct, the DAEMON reader thread had stalled (queued SysEx backlog visible via fresh mido tap); daemon restart restored heartbeat instantly. Rule: when heartbeat_age grows while FL64 is alive, run the pairing map before re-tinkering FL, and restart the daemon first. See `fl21_midi_input_rebroadcast_storm.md`.
