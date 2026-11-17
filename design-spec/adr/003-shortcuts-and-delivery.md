# ADR-003: One shortcut, clipboard-first delivery

**Status:** accepted.

**Decision:** Idle→Record, Recording→Stop/Transcribe, Ready→Copy/(conditional one paste), Busy→No-op notice, Error→Recovery. No implicit global cancel via the same key; explicit cancel command. Clipboard-only default; auto-paste opt-in and platform-qualified. Report copy and key injection separately, never assert target accepted text. No attempt to restore original Wayland focus.

**Reason:** Wayland focus rules, Windows UIPI and clipboard manager races prevent blanket paste guarantees. A deterministic state machine prevents double insertions or data loss.

**Consequences:** power users may choose safe auto-paste mode; Linux focused-target semantics remain best effort and must be disclosed.
