# Speech-to-text and AI provider contracts

## Scope

Exactly **two first-class transports**: file STT via OpenAI-compatible `/audio/transcriptions` and text transformation via OpenAI-compatible `/chat/completions`. Users configure independently: URL, model alias, credential source, timeout and optional compatible request extras. They may point to LiteLLM, OpenAI, self-hosted vLLM, SGLang or other compatible servers **only when that endpoint implements the required API**. Do not assume chat support implies audio support.

## STT adapter (batch)

- Input: finalized WAV or supported file, model alias, optional language hints, optional prompt/keywords **only if provider profile enables** them; max file size and timeout.
- Base request: `POST <base_url>/audio/transcriptions`, `multipart/form-data` with `file` + `model`. The default base URL includes `/v1` and joining rules never duplicate `/v1`.
- Validate HTTP status, finite response size, JSON `{text: non-empty string}` with optional metadata. Some compatible servers support plain text; implement a validated optional parser profile, not silent guessing.
- Use **minimal portable fields** by default. OpenAI's evolving `gpt-transcribe` uses `languages[]` and optional `keywords[]`; older/local servers may use singular `language`. Do not send both; model/profile selection controls fields. Never hardcode old `whisper-1` as provider default.
- File size for official OpenAI upload currently documented at 25 MB; other servers vary. Prefer user-configured maximum and preflight splitting for oversize, but **chunk stitching** is v1.1 feature unless tested. Make large recording fail recoverably, never silently drop chunks.
- STT output is **raw source**. Even cleanup punctuation belongs to an explicit separate transformation if LLM needed.
- Retry: explicit only after timeout/uncertain response. Record attempt lineage and generation; late results never overwrite cancelled/superseded work. Redact Authorization, full body, speech content and URL credentials in logs.

## LLM transformation adapter

- Request `POST <base_url>/chat/completions`, `model`, `messages` with concise system instruction and user-supplied text presented as **data**, `stream=false` initially. Optional `temperature` / `max_tokens` enabled per provider capability; avoid relying on provider-specific JSON mode or tool calls.
- The system prompt defines transformation task and fidelity constraints; source text must be fenced/delimited and explicitly treated as **untrusted content**. Text inside the note has no authority to change application behavior, execute commands, request secrets, or alter system instruction.
- Prompt template shape: `You are a careful editor. Rewrite only the supplied text for the selected purpose. Preserve factual claims, names, version numbers, commands and stated uncertainty; do not invent details. Return only the revised text.` + preset instruction + literal source text.
- Maximum source size, response byte length, request time and context limits validated before transmission. If too long, offer *selected text only* or manual split, not hidden truncation.
- Completion response: inspect `choices[0].message.content` string, validate non-empty result, normalize line endings, enforce size. Handle finish_reason length as incomplete and mark proposal, never auto-accept. Other response formats/profiles require explicit adapters later.
- Default AI behavior: off for quick mode, on-demand for note mode. Provider errors do not block editing, copying the raw transcript, or saving notes.
- Store sanitized metadata (model name, preset revision hash, time, source SHA) with accepted revision/proposal. Do not store secret key in provenance. User can view provider/model for transparency.

## Preset contract

Presets have ID, name, description, instruction, output_format=`plain|markdown`, preserve_language, preferred profile optional, and version. Mutable user presets are stored in app DB or serialized config, with defaults seeded from `spec/fixtures/presets.json`. The running job **snapshots** the exact preset instruction and model to ensure provenance remains accurate after edits.

Suggested tests for each preset: factual preservation, code/URL retention, Czech/English punctuation, diacritics, Markdown headings, no hallucinated commitments, reasonable shortening, sensitive input treatment and prompt injection embedded in source text. A default architectural-decision preset must not create made-up alternatives or approval decisions.

## Networking and credentials

- All configured endpoints must show resolved host/scheme before first upload. Warn that remote endpoints receive full audio/text. Local HTTP (`127.0.0.1`, `[::1]` and explicitly trusted LAN gateway) allowed by user's explicit configuration; remote HTTP disabled by default unless user confirms risk. HTTPS validates certificates; never offer *ignore TLS validation* toggle.
- Key sources: OS keyring if backend works; or environment variable selected in settings. If keyring is missing/locked, fail clearly and offer env-based setup. Never silently write plaintext API keys in TOML.
- No request body/URL with embedded credentials in logs. Honor proxies only through explicit option (default trust_env=false), document local network implications.
- No continuous background recording, automatic cloud sync or implicit model discovery that uploads user text.
- `Test connection` can call harmless metadata endpoint or attempt a user-approved short sample; never silently send a real note as a test.

## Contract tests

Use `httpx.MockTransport` for timeouts, redirects, malformed JSON, empty content, 413, 401, 429, 500, slow/cancelled requests, language field variants and oversized replies. Use a loopback fake server to check full multipart WAV and Chat Completions. For live qualification, use user-configured endpoint with explicit sample; record model alias and result without saving transcript in logs.

## Compatibility warning

'OpenAI compatible' is a narrow transport statement, **not** a guarantee any backend supports every model or request feature. Keep provider diagnostics precise. Official OpenAI documentation as of 2026-11-16 recommends `gpt-transcribe` for recorded-speech file STT and announces retirement of several older audio aliases in February 2027; this reinforces user-configured model aliases instead of hardcoding a name. See `docs/15_SOURCES.md`.
