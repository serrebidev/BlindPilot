# BlindPilot 0.57.7

This is the sixth release in the feature comparison with The Chat Place and FastChat: sound packs, per-cue custom sounds, and an independent speech voice.

Options, Sounds gains Choose Sound Pack and Custom Cue Sounds. Sound packs import from a zip of audio files named after the four cues (send, received, in_progress, error); unknown file names are reported instead of installed, and the pack folder takes precedence over the built-in sounds. Custom Cue Sounds gives each cue its own file, which wins over the pack.

Options gains System Speech: an independent SAPI voice with its own voice, rate and volume for speaking complete responses. It speaks independently of screen-reader narration and never changes it. Each conversation can follow the global setting, speak here, or never speak here. Stopping playback interrupts the independent voice.

Automatic computer use remains the final stage. It is not included in 0.57.7.
Windows ships an installer and portable archive; macOS ships an Apple Silicon archive. Linux remains source-only.
