# AI Voice Overlay Mode Design

## Goal

Add a separate app mode that reuses EasyDictate's existing recording and transcription pipeline, then sends the resulting transcript to an AI model through OpenRouter and shows the model response in an ephemeral, copyable on-screen overlay. The current EasyDictate dictation mode must remain behaviorally unchanged.

## Non-Goals

- Replacing or altering the current transcript-to-clipboard/paste workflow
- Folding the new AI flow into the existing daemon or existing shortcut
- Making AI responses auto-copy to the clipboard
- Building a persistent normal application window for the AI response
- Guaranteeing compositor-specific overlay behavior beyond best-effort GTK/Wayland support

## Product Boundary

The new feature is a sibling product inside the same repository, not an in-place extension of the current runtime path.

The repository will contain two distinct user-facing modes:

1. EasyDictate
   - Existing behavior
   - Triggered by its existing shortcut
   - Records audio, transcribes speech, copies transcript to clipboard, optionally auto-pastes

2. AI voice overlay mode
   - New behavior
   - Triggered by a separate shortcut such as `CTRL+[`
   - Records audio, transcribes speech, wraps transcript in a prompt template, sends request to OpenRouter, displays the AI response in an ephemeral overlay

Each mode must have a separate entrypoint, app/runtime identity, installer, and service setup.

## Architecture

### Shared layers

The following responsibilities remain shared:

- Config and state path helpers
- Audio recording backend selection and execution
- Whisper transcription request construction and execution
- Error/transcript persistence helpers where applicable

The recording and transcription pipeline should be reusable as a "transcribe-only" path that returns transcript text without forcing clipboard or auto-paste behavior.

### Existing EasyDictate mode

The current dictation mode remains on top of the shared pipeline and preserves its current behavior:

`record -> transcribe -> copy transcript -> optional paste`

Any extraction done to support the AI mode must not change the user-visible behavior of the existing mode.

### New AI voice overlay mode

The new mode uses the same record/transcribe stages, then diverges:

`record -> transcribe -> wrap transcript -> call OpenRouter -> parse response -> show overlay`

Responsibilities owned only by the new mode:

- Prompt template construction
- OpenRouter API request execution
- OpenRouter response parsing
- Overlay display and dismissal behavior
- Separate daemon and shortcut registration
- Separate install script and service/desktop entry setup

## Runtime Components

### Shared transcription path

Add a reusable path that:

- Accepts `state_dir`, `audio_path`, `stop_event`, and optional transcription hints
- Records audio using the existing backend selection logic
- Transcribes the audio using the existing Groq Whisper flow
- Returns transcript text and metadata needed by callers
- Does not perform clipboard writes or paste actions

The current dictation flow should call this shared path and then apply its existing clipboard/paste behavior afterward.

### AI mode orchestration

Add a new orchestration layer for the AI mode that:

- Invokes the shared transcription path
- Wraps the transcript using a fixed prompt template
- Sends the wrapped input to OpenRouter
- Extracts the model's response text
- Displays the response in the overlay
- Avoids all clipboard writes unless the user manually copies from the overlay

### OpenRouter client

Add a focused client module responsible for:

- Reading the OpenRouter API key and model/config values from settings
- Constructing an OpenRouter-compatible chat/completions request
- Applying the prompt template around the transcript
- Validating the response payload and extracting text content
- Raising clear errors on network failures, malformed payloads, or empty responses

The OpenRouter client should not know anything about GTK, shortcuts, or daemon lifecycle.

### Overlay presenter

Add a small GTK-based overlay UI responsible for:

- Displaying the AI response in selectable, copyable text
- Using an undecorated, transient, best-effort overlay-style window
- Attempting to avoid taskbar persistence where supported
- Dismissing on close button, `Esc`, generic keypress, click-away, or focus loss when practical

The overlay is intentionally ephemeral. It is not a normal persistent application window and should not reuse the existing GUI.

## UX Requirements

### Shortcut and recording

- The AI mode must use a separate global shortcut from the existing EasyDictate shortcut.
- Recording and transcription behavior may match the current user expectations from EasyDictate.
- The AI mode should not interfere with or rebind the current EasyDictate shortcut.

### Prompt wrapping

- The transcript must be wrapped in a fixed instruction template before being sent to OpenRouter.
- The prompt template should be configurable in a way that does not affect the current EasyDictate mode.
- The transcript is the user input to the AI mode; the AI response is not treated as clipboard output.

### Response display

- The AI response must be shown on screen in a form the user can manually copy.
- The overlay must not auto-copy the AI response.
- The overlay should be visually lightweight and fast to dismiss.

## Error Handling

Failure behavior must be explicit and not masquerade as a successful AI response.

### Recording or transcription failure

- Do not show a normal AI response overlay
- Persist/log the error similarly to the current application patterns
- Optionally show a lightweight notification describing failure

### OpenRouter failure

- Do not show a misleading empty or partial response as success
- Show either an error overlay or a desktop notification with a clear failure message
- Persist/log enough detail for debugging without exposing secrets

### Overlay failure

- If the response is generated successfully but the overlay cannot be shown, log the UI failure clearly
- Do not silently fall back to copying the AI response to the clipboard

## Configuration

The new mode should have its own configuration surface, separate from the current dictation-mode-specific behavior.

Expected config categories:

- OpenRouter API key
- OpenRouter model identifier
- AI mode hotkey
- AI prompt template
- Overlay dismissal settings only if the implementation requires explicit configurability

Config loading should continue to use the existing repo-local `.env`, user config JSON, and environment override pattern where reasonable, but new AI-specific keys must not change the behavior of the existing dictation mode.

## Packaging and Installation

The AI mode requires its own install path.

Expected deliverables:

- Separate CLI entrypoint or subcommand path for the AI mode runtime
- Separate daemon module for the AI shortcut flow
- Separate desktop entry/app ID for portal registration
- Separate `systemd --user` service unit
- Separate install script for the AI mode

The existing `install.sh` must remain dedicated to the existing EasyDictate mode. The new install script should set up only the new AI mode components unless explicitly designed to support side-by-side installation.

## Testing Strategy

The main safety objective is proving that existing EasyDictate behavior has not regressed.

### Existing-mode protection

- Keep existing tests passing unchanged
- Add tests around any extracted shared transcription path
- Verify the existing dictation flow still performs clipboard and optional auto-paste behavior

### New AI mode tests

- Prompt wrapping tests
- OpenRouter request construction tests
- OpenRouter response parsing tests
- AI orchestration tests proving:
  - transcript text flows into the OpenRouter client
  - overlay display is invoked on success
  - clipboard copy is not invoked
- Installer tests for the second install script and second service/desktop entry

### Test boundaries

- No live OpenRouter dependency in unit tests
- No live GNOME portal dependency in unit tests
- Mock subprocess, GTK launch boundaries, and network/request boundaries as needed

## Delivery Strategy

To minimize risk to the working codebase:

1. Work in a separate git worktree and feature branch
2. Extract the shared transcript-producing path first, with tests
3. Add AI-specific logic in new modules rather than widening existing ones unnecessarily
4. Add the separate daemon and installer only after the AI flow is tested locally
5. Run the full test suite to confirm the current dictation mode still passes

## Risks and Constraints

- Wayland/GNOME overlay behavior is environment-sensitive; skip-taskbar and transient overlay behavior are best-effort, not perfectly portable guarantees
- Introducing shared code for transcription creates regression risk if extraction is too invasive
- A second shortcut daemon/service increases setup complexity and must be clearly separated from the current mode
- API and prompt configuration errors must be isolated to the new mode and must not break the existing mode

## Recommended File-Level Direction

Exact filenames can be decided in the implementation plan, but the structure should follow these boundaries:

- Keep shared config/path logic in the existing shared area
- Keep shared record/transcribe logic in the existing engine area or a closely related shared module
- Add a new AI-specific client module for OpenRouter
- Add a new AI-specific overlay UI module
- Add a new AI-specific daemon/entrypoint
- Add a new install script for the AI mode

## Success Criteria

The design is successful when:

- Existing EasyDictate dictation mode still works exactly as before
- The new shortcut records and transcribes using the shared pipeline
- The transcript is wrapped and sent to OpenRouter
- The returned AI text is displayed in an ephemeral copyable overlay
- The AI response is not auto-copied
- The new mode can be installed independently through its own install script
