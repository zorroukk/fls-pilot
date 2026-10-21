# legacy/ - argv-driven probe scripts

These were parked under `tests/` but are **not pytest tests**: they execute
code at import time, read `sys.argv`, and (two of them) need numpy. They were
hidden from CI by a `--ignore` list, which meant nothing ran them (audit R2).
They are renamed `probe_*.py` so pytest never tries to collect them; run them
by hand:

    python scripts/probes/legacy/probe_autoopen.py <pitch>

CI runs `pytest --collect-only` as a gate, so a file that cannot be collected
can never be silently parked in an ignore list again.
