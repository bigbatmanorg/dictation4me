# ADR-007: Two launch platforms, separately qualified

**Status:** accepted.

**Decision:** Ship v1 for Linux KDE Plasma Wayland x64 and Windows x64 only, each with real interactive qualification. macOS unsupported until public NativeHTMLKit runtime supports it. Other Linux desktops are not claimed supported by virtue of being Linux.

**Reason:** Global shortcuts, window focus, clipboard and paste differ by compositor/OS. Unverified 'cross-platform' claims create user-visible failures.

**Consequences:** reusable adapters retain room for macOS later but no premature implementation or unrelated dependency.
