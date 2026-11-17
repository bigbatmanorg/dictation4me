# ADR-001: Independent resident service

**Status:** accepted.

**Context:** NativeHTMLKit's current GUI lifecycle requires a real window and closing the last window shuts down the host. KDE Wayland cannot reliably show/refocus an existing NativeHTMLKit window on demand.

**Decision:** Run a Python resident service without NativeHTMLKit dependency. Service owns microphone, OS shortcut, clipboard, provider tasks and SQLite writer. GUI is an on-demand NativeHTMLKit Python process connecting by authenticated loopback JSON IPC; close of GUI does not stop service. CLI is an IPC client.

**Alternatives rejected:** invisible NativeHTMLKit window (Wayland unsupported), privileged OS service (unnecessary), HTTP daemon/web GUI (unnecessary), two independent DB writers (race-prone).

**Consequences:** one extra local process and IPC protocol; clean lifetime boundaries and reliable shortcut availability. IPC security and service supervision are mandatory.
