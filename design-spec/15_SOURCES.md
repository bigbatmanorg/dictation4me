# Verified references (reviewed 2026-11-16)

These sources ground the design; they are **not** substitute for real acceptance testing. If upstream releases change during implementation, refresh versions and record in release evidence.

## Owned repositories

- Existing application target: https://github.com/bigbatmanorg/dictation4me — old implementation and current repository identity.
- NativeHTMLKit Python SDK README and lifecycle: https://github.com/nativehtmlkit/nativehtmlkit-python and https://github.com/nativehtmlkit/nativehtmlkit-python/blob/main/docs/reference/application.md — `Application.start()` re-execution, window lifecycle, clipboard, headless.
- NativeHTMLKit SDK Window reference: https://github.com/nativehtmlkit/nativehtmlkit-python/blob/main/docs/reference/window.md — Wayland visibility/focus restrictions and public window API.
- NativeHTMLKit Core HTML/CSS profile: https://github.com/nativehtmlkit/nativehtmlkit-core/blob/main/docs/guides/html-css-support.md — textarea works; `contenteditable`, `<audio>`/`<video>` playback and script unsupported.
- NativeHTMLKit UI README/limitations: https://github.com/nativehtmlkit/nativehtmlkit_ui and https://github.com/nativehtmlkit/nativehtmlkit_ui/blob/main/docs/LIMITATIONS.md — early 0.1.1 component and computed/layout limitations.
- Platform matrix: https://github.com/nativehtmlkit/nativehtmlkit/blob/main/docs/capabilities/matrix.md and https://github.com/nativehtmlkit/nativehtmlkit/blob/main/docs/capabilities/limitations.md — qualified baseline and Linux wheel glibc floor.

## Operating systems

- XDG Global Shortcuts portal API: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html — consent/session/activation signals; portal may supply activation token, not automatic proof NativeHTMLKit can use it.
- Wayland XDG Activation: https://wayland.app/protocols/xdg-activation-v1 — compositor-controlled activation tokens.
- Windows RegisterHotKey: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey — system-wide shortcut with `MOD_NOREPEAT` and conflict handling.
- Windows SendInput: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput — UI Privilege Isolation/integrity-level limits; event injection is not accepted-text verification.
- Python keyring: https://keyring.readthedocs.io/en/stable/ — Win Credential Locker, SecretService/KWallet with backend availability caveats.

## Provider and storage

- Official file STT API: https://developers.openai.com/api/docs/guides/speech-to-text — `gpt-transcribe` and differences in request fields; official 25MB file limit; older aliases have announced retirement 2027-02-26.
- OpenAI transcription API: https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create — model/file multipart contract.
- SQLite FTS5: https://www.sqlite.org/fts5.html — external content projection with triggers and rebuild requirements.
- vLLM OpenAI-compatible server: https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/ — Chat Completions and STT only when the serving model and endpoint implement them.
- Ollama OpenAI compatibility: https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx — demonstrates endpoint/parameter differences across 'compatible' servers.

## Review annotations

1. Old Dictation4me README notes incomplete KDE and native Windows acceptance; do not copy its verification claims into new release.
2. The NativeHTMLKit public documents describe released surface, **not** unrestricted desktop feature parity. Extra platform acceptance remains necessary.
3. External provider docs can evolve; the app's model name/endpoint/schema are user-configurable, and provider profiles must be tested against target.
