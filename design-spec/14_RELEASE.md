# Release, operations and rollback

## Release criteria

- All G0–G4 gate evidence and `qualification/acceptance.csv` release-blocking cases satisfied on both target platforms. Unsupported optional features (e.g. guaranteed paste acceptance or focus restore) explicitly excluded from promise, with fallback tested.
- Security code review of local IPC, auth/secret handling, provider transport, spool and export; negative tests clean; no raw content in logs.
- Test `uv tool install .`, start at sign-in, GUI closure without killing service, full service stop, reinstall, `uninstall` preserves notes and `--purge` confirmation.
- Test first run with no microphone, no keyring backend, no provider, no FTS, no display; app shows truthful degraded mode.
- Verify DB migration on fresh and older app-schema DB; old Dictation4me **0.1.0** configuration is deliberately not migrated, and should not be deleted by installer without opt-in.
- Source/documentation versions aligned, lockfile pinned, reproducible build artifacts and hash recorded, README corrected to reflect actual shipped features.

## Release process

1. Version bump and CHANGELOG; run full automated test suite and native sessions; collect evidence with OS, commit, package SHA and test commands.
2. Verify package/entrypoint on independent clean Linux and Windows environments. Confirm no Tk, Node or browser runtime installed transitively.
3. Validate installed Python interpreter used in desktop launchers; verify first-run desktop integration permissions and keyring.
4. Generate wheel/sdist artifacts, inspect manifest and licenses, smoke install **artifact**, not merely source checkout.
5. Review P0/P1 defects and known limitations; release candidate tagged only after gate. Publishing PyPI, GitHub release and editing default branch follows actual assigned authorization; agent must not invent credentials or claim publish without result.
6. On rollback: stop service, back up DB with SQLite Backup API, restore known-good wheel and schema-compatible backup; never roll DB backwards in place without proven migration rollback.

## Support and diagnostics

- `doctor` prints OS/session/platform, NativeHTMLKit SDK/UI/Core versions, audio device and permission state, provider connectivity test status, keyring availability, desktop portal/shortcut/clipboard/paste readiness, SQLite schema/FTS integrity, data path and log file. No secret or transcript printed.
- Support bundle excludes content by default. Opt-in attachments require preview and user consent.
- Runtime logs rotate by size/age, capture structured event IDs, code and duration without raw text.
- `uninstall` does not remove saved notes/keys automatically; `uninstall --purge` explicitly does after confirmation.

## Operations checklist

- Sleep/resume microphone/portal reconnection, hotplug capture device, clipboard daemon reconnect, keyring locked on sign-in.
- Disk full, corrupted database, cache accumulation, inaccessible recordings, duplicate service process, outdated endpoint config.
- Existing user has old 0.1.0 installation: remove/replace old launchers safely; do not silently delete old configs/data; no promises of reading old format.
