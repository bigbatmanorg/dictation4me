# ADR-006: Two fixed OpenAI-compatible provider clients

**Status:** accepted.

**Decision:** Independent STT file-transcription and LLM chat-completions clients with configured URL, alias, key source and capabilities. No general-purpose provider plugin system, web search, agent runtime, prompt chaining or model hosting.

**Reason:** local LiteLLM and other compatible endpoints usually need only these protocols; compatibility is not identical across providers. Tiny explicit clients are more maintainable and testable.

**Consequences:** some model-specific options are supported through explicit request profiles; Responses API and realtime transcription are future options.
