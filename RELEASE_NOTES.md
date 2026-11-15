# BlindPilot 0.57.9

Test-only release: the macOS release build exposed a test isolation issue where a queued sound's player thread counted as a worker in the GitHub picker test. The test now isolates unrelated sound spawns the same way the CLI-discovery test already did. No product behavior changes.

For the features in it, see the 0.57.8 notes: automatic computer use on Windows — opt-in per conversation, accessibility-tree actions only, automatic by default with ask-before-acting available, Ctrl+Shift+F12 stop key, BlindPilot itself cannot be operated, 25-action turn bound. macOS computer use follows in a later release.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
