# Requirements traceability and proof mapping

These mappings are planning aids; the authoritative test cases and expected outcomes are in `qualification/acceptance.csv`. Release qualification requires tests of all critical edge cases, not merely one green example.

| Requirement | Delivery workstream | Primary acceptance cases |
|---|---|---|
| PR-001 Notes CRUD, trash, archive, search | R1-01, R1-02, R2-01 | ACC-012–ACC-026 |
| PR-002 Folders, tags, pin, recent | R1-01, R2-01 | ACC-021, ACC-022, ACC-024 |
| PR-003 SQLite, drafts, revisions, backup | R1-01, R1-05 | ACC-001–ACC-011, ACC-014–ACC-020, ACC-093 |
| PR-004 Microphone record/capture bounds | R3-01, R3-04 | ACC-027–ACC-034, ACC-087 |
| PR-005 Durable audio/recovery | R1-05, R3-01 | ACC-029–ACC-036, ACC-065 |
| PR-006 Configurable STT client | R3-02, R3-04 | ACC-037–ACC-041 |
| PR-007 Independent LLM client | R4-01 | ACC-041, ACC-042, ACC-046 |
| PR-008 Preset configuration | R4-02 | ACC-042, ACC-047 |
| PR-009 Non-destructive AI versions | R4-02, R4-03 | ACC-025, ACC-043–ACC-048 |
| PR-010 Global shortcut/controller | R5-01–R5-03 | ACC-059–ACC-064, ACC-071–ACC-073, ACC-081–ACC-082 |
| PR-011 Clipboard and honest paste | R5-04, R5-05 | ACC-065–ACC-070, ACC-074–ACC-077, ACC-083–ACC-086 |
| PR-012 Real Linux/Windows qualification | R2-05, R5-05 | ACC-071–ACC-090, ACC-096 |
| PR-013 Async work and restart | R1-03–R1-05, R6-03 | ACC-001–ACC-011, ACC-092 |
| PR-014 Privacy/credentials/logging | R4-04, R6-01 | ACC-049–ACC-058 |
| PR-015 UX, keyboard, responsiveness | R2-01–R2-05, R6-03 | ACC-022, ACC-078–ACC-080, ACC-096–ACC-097 |
| PR-016 Install/uninstall and startup | R0-03, R6-02, R6-04 | ACC-058, ACC-090, ACC-091 |
| PR-017 Doctor, export, recovery CLI | R1-03, R1-05, R6-04 | ACC-035, ACC-051–ACC-053, ACC-065, ACC-095 |
| PR-018 Safe rendering and optional preview | R2-02, R4-04 | ACC-057, ACC-097 |

## Coverage gaps to guard during implementation

Some **P1 optional** items (full audio player UX, Markdown preview usability, generated title quality) do not appear as independent **release-blocking** acceptance cases. If promoted to shipping features, add concrete acceptance IDs and tests before claiming them functional. Conversely, every feature explicitly advertised at v1 must have a test with actual expected behavior.

## Evidence location

`qualification/runs/<UTC date>-<platform>.md` should record evidence per case with commit hash, OS / session / package versions, exact command or user action, result, log/screenshots (redacted), and known limitation. Update mappings when requirements or test IDs change; do not change passed results retroactively without re-running tests.
