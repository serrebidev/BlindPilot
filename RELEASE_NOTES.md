# BlindPilot 0.8.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

## Chat mode

The main window now has a **Mode** combo box at the top that switches between the
multi-session Agent experience and a new Chat experience, without opening a second
window or restarting.

Chat talks directly to OpenRouter, OpenAI, Claude, Gemini, Z.AI, Moonshot AI, Kimi,
DeepSeek, OpenCode Go, or any OpenAI-compatible service. API keys are held in the
operating system credential store. **Chat → Accounts** adds and tests a provider,
**Chat → Refresh models** discovers what it offers, and **Chat → Conversation profiles**
supplies a system prompt, default account and model, temperature, token limit, and
streaming preference.

Replies stream in. History can be a native list or a read-only edit field, chosen from
**Chat → History view**, and individual messages can be copied, edited, or regenerated
from their context menu. OpenRouter accounts additionally support file attachments,
cache-aware regeneration, and model ids ending in `:batch`. Provider logs are under
**Chat → Diagnostics**.

Chat data lives in `chat.sqlite3` beside BlindPilot's other configuration. The first
time Chat mode opens, an existing AccessibleAI database and its saved keys are imported
when present, without modifying the original.

## Sessions are tabs again, and only tabs

The Session combo box is gone. Sessions are navigated from a real tab strip below the
Mode combo box: **Ctrl+Tab** and **Ctrl+Shift+Tab** move between them from anywhere in
the window, Shift+Tab from the responses lands on the strip, and the arrow keys walk it.

Because the strip is a native tab control, NVDA announces the conversation name and
"tab 2 of 4" by itself. BlindPilot no longer speaks a second description over the top of
that. The conversation pages sit in a separate container, so moving into a response list
no longer makes Windows announce "tab control" for a strip you did not enter.

## Tab order that goes somewhere

Agent mode now moves Mode → Session tabs → Responses → Prompt → Send and the other
actions → Permission mode → Mode, and the exact reverse with Shift+Tab. Empty responses
are skipped, because there is nothing in them to navigate. Leaving the prompt is briefly
deferred so NVDA can finish the formatting query it schedules on Tab while the edit field
is still valid, which removes the "unknown" announcements that followed it.

The custom focus speech on the prompt, the responses, and Permission mode was removed on
Windows. NVDA already announces each control's name, role, and state; the extra sentence
only talked over it. The announcements remain on macOS, where they work around a real
VoiceOver gap.

## One dialog for updates

**Help → Check for Updates** now opens a single resizable dialog instead of a sequence of
message boxes. It contains readable release notes, a named progress gauge that announces
every ten percent, cancellation that cleans up the partial download, checksum
verification, and an explicit restart step.

Startup checks can be turned off from **Help → Check for updates at startup**. When they
are on, an available update is reported in the status bar and spoken without stealing
focus from whatever you were doing.
