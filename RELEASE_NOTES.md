# BlindPilot 0.43.8

This release repairs how Command Code conversations survive a missing session, found by auditing the Command Code integration end to end.

- A Command Code turn no longer dies with "No session ... found to resume". When the saved conversation is gone -- Command Code cleans old sessions up, or the conversation was saved in a different folder -- the prompt runs once more as a fresh conversation. BlindPilot says the old conversation could not be found and that the new one starts fresh, and continues from there. You no longer have to start a new conversation by hand after the error.
- The tab follows the conversation's new session id, so the fresh start keeps working on every later message instead of repeating the failure, and /compact now takes effect: the compacted replacement session is what your next message continues, rather than the uncompacted original.
- Restoring a conversation from Recent Conversations whose session no longer exists continues as a fresh conversation from the first message.
- Compacting a conversation that no longer exists says so -- "The conversation to compact could not be found ... Nothing was changed" -- instead of saving an empty summary and reporting success.
- Two smaller reporting repairs: a Command Code run that ends cleanly without a result names its exit code, and an error result with no message falls back to Command Code's own error output.
