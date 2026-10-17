# BlindPilot 0.47.10

This release makes backend updates visible in the setup wizard.

- A shared backend update check reports current, update-available, or failure status accessibly in the setup wizard: npm-backed CLIs are compared against the latest published version, and official-installer backends report installed status only. Results are cached briefly with a manual refresh, and installing an update stays a manual step. Thanks to Farhan Khan.
