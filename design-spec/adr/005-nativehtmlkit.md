# ADR-005: NativeHTMLKit public SDK and Markdown source editor

**Status:** accepted.

**Decision:** NativeHTMLKit Python SDK for windows/documents/editor; optional public UI library for basic components. No Core/host/protocol imports from app or UI library. `<textarea>` Markdown source, optional sanitized read-only preview. Platform tray and global shortcuts remain separate OS adapter concerns.

**Reason:** Core rejects contenteditable rich-text, audio/video playback and scripts; UI 0.1.1 has limited dynamic widgets. A custom rich editor/framework or hidden Wayland window would be a high-risk diversion.

**Consequences:** modest native UX with occasional SDK-level DOM updates. Upstream changes requested only with specific reproducible missing primitives.
