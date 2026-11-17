# UX review — notebook UI (2026-11-17)

Heuristic review of the 0.2.0 NativeHTMLKit notebook (Notes, Quick dictation, Recovery, Settings), done from real KDE Wayland screenshots before the frontend rewrite. Findings follow the format: problem → principle → user impact → change. Severity uses Nielsen's 0–4 scale.

## Context

- **Users:** people who dictate into other apps all day and keep voice/AI-assisted notes. They are perpetual intermediates: they configure the app once, then mostly use the global shortcut.
- **Posture:** the product is mainly *daemonic* (background shortcut and notifications). The window is *transient* (check a transcript, fix a setting) and sometimes *sovereign* (writing notes). The 0.2.0 UI treated every screen like a sovereign admin console.
- **Constraints:** NativeHTMLKit Core 1.0.3. HTML/CSS only, no JavaScript; Python drives the DOM. Grid, flex, sticky, `:has()`, popovers, `<details>`, transitions and inline SVG are available.

## 5-second test (before)

| Screen | What is this? | Primary action? | Verdict |
|---|---|---|---|
| Notes | Clear: a note editor | Unclear: "+ New note", 6 equal toolbar buttons, and an always-open AI panel with a blue "Transform note" all compete | Fail |
| Quick dictation | Clear | "Start recording", but Stop and Cancel sit beside it at the same weight in every state | Partial |
| Recovery | Clear | None (empty void; warning text floats mid-page) | Partial |
| Settings | "A form" | Save is top-right and scrolls away; about 40 fields on one page | Fail |

## Findings

### Severity 4: blocks the task

1. **Save outcome was invisible (H1, H9).** Validation errors went to a 10px footer, so "saving doesn't work" looked like a silent failure. *Fixed already:* feedback next to Save and a sticky header. *Rewrite:* a persistent save bar that appears only with unsaved changes, plus field-level hints.
2. **The shortcut state was untrustworthy (H1, H2).** Settings said "requires service restart", while KDE silently kept a different key. *Rewrite:* a shortcut card that shows the *actual* bound key as a keycap, its status in words, and where to change it.

### Severity 3: major difficulty

3. **Dictation status is hidden (H1, Visibility of system status).** The service state is a tiny pill ("Idle") in a top bar, duplicated on the Quick page only. Users can't see from the Notes screen that a recording is running. *Change:* a dictation status block in the sidebar on every screen, with state dot, words ("Recording · press Ctrl+Alt+D to stop") and one context action.
4. **State-blind controls on Quick dictation (H5, Hick's Law).** Start, Stop and Cancel are all enabled in every state; "Save edits" duplicates autosave; "Deliver text" vs "Copy text" is internal jargon (H2). *Change:* one large primary button whose label follows the state (Start recording → Stop & transcribe → Copy to clipboard → Start new). Cancel appears only while recording or processing. Remove "Save edits" (autosave plus status text).
5. **AI inspector is always open (Aesthetic & minimalist, spec 02_UX).** It takes a third of the editor width, and the spec says "collapsible … only when requested". *Change:* hidden by default and opened by an "AI" toolbar toggle. Revisions and AI history move into collapsible groups there.
6. **Settings is one undifferentiated 40-field form (Miller's Law, Common Region).** STT, AI, preset editor, microphone, shortcut, delivery, retention and privacy all sit on one scroll. The preset editor is mixed into the AI provider card. *Change:* tabs for **Connections**, **Dictation**, **Presets** and **Privacy & data**. Technical fields (API path, request format) go under "Advanced".
7. **Jargon labels (H2).** "API path", "Request format: Minimal: file + model", "Keyring ID / variable name", "Deliver", "Save revision", "Scope". *Change:* "Server URL", "Model", "API key: Not needed / From environment variable / Saved in system keyring", "Copy to clipboard", "Save version".
8. **Key availability is unknowable (H1, H9).** An environment-variable key worked from a terminal but not at login, and nothing in the UI said so. *Change:* each provider card shows "Key: available to the background service ✓" or the reason it isn't, with a one-click "Move key to system keyring".

### Severity 2: minor

9. **Filter chrome dominates the note list (Prägnanz).** Search, collection select and folder select stack as three full-width controls above the list, and folder creation is a permanent input at the bottom. *Change:* search field, then segmented collection buttons (Notes · Archive · Trash), then a compact folder select with a "+" popover.
10. **Duplicated chrome (Aesthetic & minimalist).** A 51px brand top bar sits under the KDE title bar, and its "+ New note" button shows even on Settings. *Change:* remove the top bar. Brand goes in the sidebar; "New note" sits atop the note list.
11. **Destructive and rare actions at top level (Hide the ejector seat).** Pin/Archive/Trash are permanent text buttons. *Change:* Pin stays visible as a star toggle; Archive, Trash, Copy, Save version and Delete forever go into a "More" popover menu.
12. **Recovery empty state (Blank slate pattern).** A blank page with a manual "Refresh". *Change:* an illustrated empty state explaining what lands here; auto-refresh when shown and when the count changes; a count badge in the sidebar.
13. **Type scale too small (readability).** 10–12px for most text, and an editor body of 12px. *Change:* a 13px base, 15px editor body with 1.6 line height, a 68ch measure, and higher-contrast secondary text.

### Severity 1: cosmetic

14. Icon glyphs (▤ ◉ ↺ ⚙) render inconsistently across fonts. Use inline SVG icons with text labels.
15. The footer status bar ("NativeHTMLKit · Local SQLite · 1 shown") is developer information. Replace it with transient toasts for action feedback.

## Design principles for the rewrite

1. **Status first:** what the dictation service is doing is always visible, in words, not only colour.
2. **One obvious next action per state:** the primary button follows the state machine; secondary actions are quieter; destructive ones sit in menus behind confirmation.
3. **Progressive disclosure:** the AI inspector, advanced provider fields, revisions and recordings open on demand.
4. **Plain language:** labels describe outcomes ("Copy to clipboard"), not mechanisms ("Deliver").
5. **Honest platform limits:** say when KDE owns the shortcut, when a key is unavailable to the service, and that pasting into other apps can't be verified.
6. **Calm, readable, dark slate:** keep the spec palette (02_UX), larger type, fewer borders, consistent 4/8px spacing, and a visible keyboard focus ring.

## Verification

The rewrite is checked on linuxDEV (KDE Plasma 6.6 Wayland) with real pointer and keyboard input and screenshots. See `qualification/runs/2026-11-17-ui-rewrite/`.
