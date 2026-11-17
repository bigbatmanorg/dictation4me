# Critical review: from a dictation widget to Dictation4me

## Design verdict

The basic proposal is sound, but if implemented literally it would fail daily-use expectations on Linux Wayland and risk destroying notes during asynchronous operations. The design was revised before any code is written. Changes below are **decisions**, not discussion topics.

| Earlier assumption / risk | Why it fails | Final decision |
|---|---|---|
| Main notebook window doubles as always-running utility | NativeHTMLKit's current host expects a window; closing it ends the GUI lifecycle. On KDE Wayland the SDK refuses hiding/refocusing an existing native window | Independent non-GUI **Python resident service**; NativeHTMLKit starts only when needed |
| Show floating quick window every shortcut | On Wayland, focus and placement are compositor controlled; an overlay can steal typing focus, defeating reliable paste | **Non-focus-stealing notifications and sound indicator first**; review in normal window on request; optional overlay only after proven native platform capability |
| Third press always pastes to original field | Neither Wayland nor Windows can guarantee accepted insertion; focus may change, terminals require another shortcut; elevated windows block Windows SendInput | Clipboard is deterministic fallback. On Windows verify best-effort foreground target; on KDE Wayland offer opt-in paste into **currently focused** target; never claim verified field insertion |
| Every rewrite is a note version | AI generates transient candidates and users edit frequently; version list becomes unusable | Immutable **accepted revisions**, one **autosaved working draft** per note, and separate **AI proposals** awaiting acceptance |
| Audio kept in memory until request completes | Crash, OOM or network outage loses irreplaceable recording | Stream capture into bounded private spool on disk, finalize atomically, track recovery jobs; keep failed audio until user resolves/deletes it |
| A default general-purpose speech model name | OpenAI APIs evolve, and local OpenAI-compatible servers have different model names/accepted fields | Independent model **aliases** and request capability profile; bare multipart `file`+`model` as compatibility floor; no hardcoded `whisper-1` |
| AI silently cleans every transcription | Hidden transformation can change facts or destroy voice tone and incurs network costs | Raw transcript remains immutable; AI cleaning opt-in per mode or explicitly selected as preset, with transparent provider disclosure |
| One history for all transcriptions | Quick messages may be personal and should not silently become permanent notes | **Note mode** saves; **Quick mode** uses short recovery retention and an explicit **Save as note** command |
| General cross-platform promise | NativeHTMLKit Core 1.0.3 targets KDE Wayland and Windows x64, not macOS; other Linux desktops vary | Release gate per platform. Portable Python architecture is not the same as platform qualification |
| Build an app using only NativeHTMLKit UI 0.1.1 | The UI layer does not bind `<textarea>` or provide rich per-row dynamic widgets; it has early-version bugs | Use the public **Python SDK directly** for editor, list and dialogs; use the small UI library selectively for stable forms/layout, never private Core APIs |
| Persist everything as plain Markdown files in the filesystem | Versions/relationships/FTS/integrity become difficult to manage atomically | SQLite WAL for metadata/drafts/revisions + private audio files; Markdown export for portability, not primary store |
| Automatically retry failed remote calls | Can incur repeat charges, race stale responses, and duplicate paste | No automatic replay of uncertain requests/paste. Explicit user retry; discard results from cancelled/superseded generations |
| Full rich-text/WYSIWYG editor | NativeHTMLKit current Core rejects editable HTML (`contenteditable=true`) | **Plain Markdown textarea** plus safe read-only preview. WYSIWYG deferred until Core gains real public capability |
| Store API keys directly in private TOML | File ACL is not equivalent to keyring; copied configs expose secrets | Use OS keyring where supported, env var option, and fail visibly when secure storage is unavailable; never silently downgrade |
| Build an extensible provider/plugin framework | Overengineering for two stable transport families | Two **tiny** interfaces (`SpeechClient`, `TextModelClient`) and one transport each; configurable base URLs and request variants, no plugin loader |

## Core principles

1. Capture safely even if all providers are offline.
2. The fastest path must not open a major UI window or change target focus.
3. Plain text is a first-class, safe output. AI is an additional explicit step, not a dependency for dictation.
4. User text wins over delayed model output. A model reply cannot overwrite a changed draft.
5. Only claim actions the app can verify: *copied*, *shortcut sent*, *note saved*; not *pasted into field* unless proven by a supported platform mechanism.
6. Prefer a boring testable state machine over clever shortcut ambiguity.
7. Preserve simplicity in code and dependency graph. Never invent an internal UI framework or abstraction hierarchy.

## Locked scope decisions

- Use existing Git repository `bigbatmanorg/dictation4me` and package/command name `dictation4me`.
- Rewrite cleanly; no migration/backward-compatibility features. Existing users should export old data manually if they need it. Do not silently delete legacy user data on upgrade.
- Primary desktop Linux **KDE Plasma Wayland**, second **Windows x64**; run actual GUI acceptance tests on both.
- No macOS support claim until NativeHTMLKit ships and is qualified on macOS.
- SQLite is the only database; not an external DB server or vector store.
- OpenAI-compatible STT upload and Chat Completions text transform as initial transport protocols; each endpoint may use a different URL/model/key.
- Full notes mode and quick dictation mode share backend, differ in retention and interaction policy.
- Use `/v1/chat/completions` by default, with user-specified path override; no function calling/tools/agents/chat memory in v1.
- Accessibility, privacy, retention, and failure modes are release requirements, not future niceties.

## What remains technically unproven

These are **qualification gates**, not reasons to block repository implementation: KDE portal binding on the user's compositor/build, safe paste backend and uinput authorization, clipboard ownership races, permissionless notification presentation, native Windows hotkey conflicts, Windows UIPI behavior on target applications, and native screen-reader/IME usability. Tests must show exact support level; no green gate from source inspection alone.
