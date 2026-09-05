# FL ignores synthetic hotkeys while unfocused (save must be user or foreground)

Background PostMessage Ctrl+S (cua-driver, delivery via Win32 target) reports the
keystroke as delivered but FL Studio 21.2.2 does not process it unless its window
is focused: disk file hash unchanged after two sends; visible state unchanged.
Foreground delivery (window raise) or manual user save is required for hotkey
routes like Ctrl+S. Verified 2026-09-04 21:5x (persistence pass, HERMESproject).

Corollary: verify saves by file mtime/SHA, never by 'input was delivered'.
