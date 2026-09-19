# FL 21.2.2 Re-broadcasts Enabled MIDI Inputs to All Numbered Outputs (storm hazard)

- **Date:** 2026-09-20
- **Agent/Author:** Hermes (experimental) / Nicholas
- **Topic:** Every enabled MIDI **input** row in FL 21.2.2 re-broadcasts received traffic out to **all numbered outputs**, closing feedback rings through loopMIDI + external-hardware relays. Outputs have no enable toggle — the port number IS the binding; blank = untargetable.
- **Affected File/API:** FL `Options > MIDI settings` (both tables), `mf_mcp.relay` lane, loopMIDI topology.
- **Context:** First live relay test of the rebuilt MicroFreak lane. Relay attached to the *shared base* `loopMIDI Port` (which FL had as an enabled input and which also carries visualizer traffic). Within 60 s the sendlog recorded 85,553 relay note events (~1,670 ev/s, default velocity 64) with the Freak's keys "pressed by themselves". No FL transport was running and nothing was played.
- **Observation (post-reboot forensic matrix):** brute-force map (write one note on every output while reading every input) showed a single write on ANY output appearing on MULTIPLE unrelated inputs (e.g. `loopMIDI Port` out -> MicroFreak in; `FLStudioMCP RX` out -> `loopMIDI Port 2` in) while FL was open with 4 enabled input rows. After disabling all FL inputs except `FLStudioMCP RX` and unnumbering untended outputs, the matrix collapsed to strict 1:1 same-port loops — the ring was FL's input re-broadcast, amplified by relay -> hardware -> monitor -> cable cycles.
- **Tested Values:** inputs: RX enabled(42) + generic-controller rows disabled; outputs: TX=42, `loopMIDI Port 2`=20, bare `loopMIDI Port`/MicroFreak blanked. Result: single preview note produced exactly 2 sendlog entries (on + off ~215-227 ms later), zero growth, audible one-shot on the Freak.
- **Result:** routing law confirmed; runaway eliminated without touching relay code.
- **Confidence Level:** `measured_repeated` (storm reproduced live once; inverse confirmed once via matrix + clean single-shot).
- **Source/Method:** sendlog rate analysis (`%LOCALAPPDATA%\mf-mcp\sendlog.jsonl`), mido port-enumeration matrix test, live `channel.preview_note`.
- **Laws (add to any FL+loopMIDI+hardware bring-up checklist):**
  1. Enable ONLY controller/automation input rows in FL. A "convenience" enabled input (hardware keyboard, loop monitor) is an echo leg.
  2. Blank the port number of every output nobody explicitly targets (hardware direct-out, spare loopMIDI ports). Numbered = routable.
  3. Relay/hardware lanes need a DEDICATED loopMIDI port with zero other tenants — never the base `loopMIDI Port`.
  4. Launch relays DRY first (log-only) and verify a single-shot on/off in the sendlog before connecting the instrument.
  5. Prefer bounded runs (`--max-seconds`) for any new live relay until the topology is proven.
- **Known Pitfalls (secondary, same night):**
  - WinMM device names carry enumeration suffixes that CHANGE across reboots/FL restarts (`loopMIDI Port 2 4` -> `loopMIDI Port 2 3`); resolve ports by base-name substring at launch, never hardcode the suffixed string.
  - Windows MIDI driver graph can corrupt after port churn while FL holds endpoints (cables stop looping entirely; stale SysEx queues); cure = reboot, not more F10 edits.
  - The MCP daemon's MIDI reader thread can stall: TCP stays alive and replies, heartbeat_age grows linearly, FL side is actually fine (its SysEx visible on the cable with queued backlog). Cure = restart the daemon; check it BEFORE re-tinkering FL.
- **Open Questions:** whether FL's re-broadcast is gated by a "MIDI echo/recall" option on the input row or is unconditional for enabled inputs on 21.2.2 build 3914.
- **Next Recommended Action:** fold the 5 laws into `flstudio-stack-startup.bat` port-drift check (warn if >1 FL input enabled or if relay port number appears on another output row); document in agent onboarding.
