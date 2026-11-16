# BlindPilot 0.59.1

This release is about credit where it is due.

Help, About now names the author and gives the GitHub and Forgejo pages, the projects site at projects.serrebiradio.com and the Telegram channel at t.me/SerrebiProjects, then points to CREDITS.md for the full list. It still lists every backend BlindPilot can drive, and a new test keeps that list honest whenever a backend is added.

CREDITS.md is new. It credits Claude Code Reader by doubletaponair, the project BlindPilot grew from, the AccessibleAI code Chat mode is adapted from, the upstream code an audit of the source turned up, and everyone who opened an issue or pull request. The README follows the same layout as the other serrebi projects.

One test change: the Freebuff turn-deadline test now leaves its fake turn enough room past the composer settle, so it no longer fails on a busy machine.
