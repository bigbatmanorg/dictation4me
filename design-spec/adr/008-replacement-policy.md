# ADR-008: Clean replacement in the existing repository

**Status:** accepted, explicit user direction.

**Decision:** Keep `bigbatmanorg/dictation4me` repository/name/history. Replace old source, UI, tests, configuration design and docs without preserving compatibility. No legacy migration path is required or to be fabricated. Preserve project license/provenance accurately; keep license unless explicitly changed by owner. Do not automatically delete old files from real installed user profiles during upgrade.

**Reason:** the earlier 0.1.0 tool is a prototype, not the desired architecture, and preserving incompatible behavior would make the design harder.

**Consequences:** installation docs must clearly label breaking changes. Users who want older artifacts must rely on their manual backups/Git history.
