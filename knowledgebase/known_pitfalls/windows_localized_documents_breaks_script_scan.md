# Known pitfall: `~/Documents` lies on localized/OneDrive-redirected Windows

**Date:** 2026-09-20 · **Author:** Hermes experimental / Nicholas · **Confidence:** measured_repeated

`os.path.expanduser("~/Documents/...")` does **not** resolve to the Documents
folder Windows (and therefore FL Studio) actually uses when OneDrive
Known-Folder-Move or a localized OS renamed it — e.g. this rig stores it at
`C:\Users\nicho\OneDrive\Dokumenter`.

Consequences seen live: `apply_notes` "succeeded" writing
`MCP_Apply.pyscript` into a ghost `C:\Users\nicho\Documents\...` tree FL never
scans; the Piano roll Script menu stayed empty across a restart; a second
restart was burned before the path mismatch was found.

## The law

Resolve the real path the way Windows does:

```python
winreg.OpenKey(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
QueryValueEx(key, "Personal")   # then os.path.expandvars(...)
```

Patched into `pyscript_gen._resolve_piano_roll_scripts_dir()` (takes effect on
daemon restart). Until the daemon restarts, copy the generated script from the
stale path to the resolved path before triggering.

Also: FL scans user piano-roll scripts **at startup** — a newly added file
needs an FL restart before it appears in the menu.
