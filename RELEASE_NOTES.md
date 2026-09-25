# BlindPilot 0.29.20

Muse Code works properly now, including every time it asks for permission.

- Muse no longer freezes when it asks permission. Before, choosing Allow or Reject, pressing Stop, or steering a running turn did nothing with Muse 1.3.0, so any turn that needed your approval waited forever. All of these now reach Muse. If Muse turns one down, you hear why instead of silence.
- The permission dialog now shows Muse's own choices. You will hear Allow once, Always allow in this workspace, and Reject, which is exactly what Muse offers. The old menu offered two answers Muse did not accept.
- Commands joined with a pipe, for example git status piped into head, now run. Muse asks about each part of a pipe separately, and only the first part used to get an answer.
- Muse's thinking is read as thinking. Its reasoning summary used to be read and saved as though it were the answer.
- Each Muse answer is read once. Before, it was read as it arrived and then read again when it finished. When Muse sent several messages in one turn, only the last one was kept.
- Muse no longer says "agentMessage" or "Reminder child session" on every turn. Those were Muse's own behind-the-scenes steps.
- Picking a different model for a Muse conversation you reopened now takes effect. Before, the model was only set when a conversation first started.
- Muse's max reasoning effort is now in the picker.
- On every backend, a blank piece of a streamed answer no longer makes your screen reader say just the backend's name.
