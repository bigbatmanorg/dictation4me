# ADR-004: Three kinds of note text

**Status:** accepted.

**Decision:** Original STT transcript immutable. Manual typing saved continuously in one working draft and checkpointed as immutable accepted revisions at meaningful milestones. AI transformations create unaccepted proposals; acceptance creates an accepted revision or new note. Use source hash and monotonic edit sequence to guard asynchronous stale outputs.

**Reason:** one permanent version per keystroke becomes unmanageable; one mutable note blob loses source provenance and can be overwritten by delayed AI.

**Consequences:** a little more schema complexity with substantially simpler, safer user interactions.
