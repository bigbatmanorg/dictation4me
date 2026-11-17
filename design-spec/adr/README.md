# Accepted architecture decisions

All ADRs are accepted as the initial baseline. Reversals require a measured reason, revised acceptance tests and an updated ADR. The application is purposely small; these are guardrails, not a design-document bureaucracy.

- [ADR-001](001-resident-service.md) — Separate resident service from native UI.
- [ADR-002](002-storage.md) — SQLite + private durable audio spool.
- [ADR-003](003-shortcuts-and-delivery.md) — Deterministic shortcut, clipboard-first output.
- [ADR-004](004-ai-revisions.md) — Raw transcript, draft, accepted revision and AI proposal separation.
- [ADR-005](005-nativehtmlkit.md) — Public NativeHTMLKit SDK only; Markdown textarea.
- [ADR-006](006-provider-boundary.md) — Two configurable OpenAI-compatible endpoints, no plugin framework.
- [ADR-007](007-target-platforms.md) — KDE Wayland and Windows only at v1.
- [ADR-008](008-replacement-policy.md) — Clean replacement without legacy migration.
