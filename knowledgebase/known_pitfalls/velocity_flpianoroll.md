# Known pitfall: `flpianoroll` per-note velocity silently lands as FL's default

**Date:** 2026-10-21 · **Author:** hardened-fork audit sweep + live 2026-09-21 battery · **Confidence:** measured_once (FL 21.2.2 b3914)

The Piano-roll scripting manual documents `Note.velocity` as 0.0..1.0 with
"MIDI velocity of 127 corresponds to FL velocity of 1.0". Measured on
21.2.2 build 3914: notes written through the generated `flp.Note` path with
velocities 0.818-0.850 ALL land at FL's default **120** in the saved .flp
(byte-exact proof via the FLP note-record dump). The drop is invisible:
`ok:true, count:N` only proves serialization into the script.

**Consequences:** `velocity_ramp` and per-note dynamics through
`fl_write_piano_roll_notes` are ADVISORY on this build. Tool descriptions now
say so (audit T1).

**Cures:** shape dynamics in the Piano roll velocity lane; byte-patch the
saved .flp's velocity field after the owner saves; or re-probe on a newer FL
build (open question: does 25.x/26.x persist it?).

**Law:** manual-documented != measured-working on the target build. Feature
tables must grade DOCUMENTED+MEASURED vs DOCUMENTED-ONLY separately.
