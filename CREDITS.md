# Credits

BlindPilot is a fork of [Claude Code Reader](https://github.com/doubletaponair/claude-code-reader) by [doubletaponair](https://github.com/doubletaponair). The original project built the accessible wxPython window, the navigable response rows, live activity narration, multi-session tabs, and the Claude Code integration. Its contributor history is in the upstream repository. The original specification is kept in this repository as `original-claude-code-reader-spec.html`.

BlindPilot contributors added the Codex, FreeBuff, opencode, Hermes, Muse Code, muse.ai, Command Code, Gemini CLI, Antigravity CLI, and GitHub Copilot CLI backends, the setup wizard and installer, the verified updater, Chat mode, remote Hermes, narration modes, sound cues, and the test suite. The backends BlindPilot drives are Claude Code, Codex, FreeBuff, opencode, Hermes, Muse Code, muse.ai, Command Code, Gemini CLI, Antigravity CLI, and GitHub Copilot CLI.

Chat mode is adapted from AccessibleAI, a sibling project by the same publisher. Its accounts, profiles, provider protocols, credential storage, attachments, and streaming were moved into BlindPilot's main window.

The searchable code-block and link lists, the task list, finding in all conversations, sending a reply to another session and agent conversations, New Session's recent folders, GitHub clone and worktrees, and send-and-save prompts were inspired by [The Chat Place](https://github.com/TheIdeaPlace/AIChat), by Kelly Ford. BlindPilot implements them independently using its existing conversation readers and controls.

## Issue and pull request contributors

Thanks to everyone who opened an issue or a pull request on GitHub. This list is kept up to date on request and was last checked 2026-11-16.

- [blindndangerous](https://github.com/blindndangerous) — 44 pull requests.
- [thebetterfarhan](https://github.com/thebetterfarhan) (Farhan Khan) — 30 pull requests and 9 issues.
- [app/dependabot](https://github.com/apps/dependabot) — 13 dependency-update pull requests.
- [michaldziwisz](https://github.com/michaldziwisz) — 3 pull requests.
- [jage9](https://github.com/jage9) — 2 pull requests and 1 issue.
- [devinprater](https://github.com/devinprater) — 1 pull request and 1 issue.
- [pitermach](https://github.com/pitermach) — 1 pull request.
- [douglawlor](https://github.com/douglawlor) — 8 issues.
- [danielbernardini-pp5ft](https://github.com/danielbernardini-pp5ft) — 1 issue.
- [fastfinge](https://github.com/fastfinge) (Samuel Proulx) — 1 issue.

Pull requests and issues opened by serrebidev, the maintainer, are not listed above.

Claude and Claude Code are Anthropic's. Codex is OpenAI's. Hermes Agent is Nous Research's. FreeBuff and opencode belong to their own projects. The names identify the command-line tools BlindPilot drives; no endorsement is implied.

## Third-party libraries

Runtime libraries, from `requirements.txt`:

- wxPython (the window toolkit; the Ubuntu 24.04 wheel is named in the file).
- httpx and certifi (HTTP and the trust store a packaged build verifies against).
- markdown-it-py (Markdown rendering).
- websocket-client (only for a Hermes gateway on another machine).
- accessible-output2, pywinauto, and pyte (Windows output, computer use, and terminal parsing).
- keyring and pyobjc-framework-Cocoa (platform key storage and macOS bindings).

These belong to their own projects; BlindPilot only depends on them.

BlindPilot is distributed under the MIT License (see [LICENSE](LICENSE)). The upstream snapshot did not contain a LICENSE file when it was forked, so this notice does not claim the upstream project was MIT-licensed.


## About this project

- GitHub profile: [serrebidev](https://github.com/serrebidev)
- Repository (GitHub): [https://github.com/serrebidev/BlindPilo](https://github.com/serrebidev/BlindPilo)
- Repository (Forgejo): [https://git.serrebiradio.com/serrebi/BlindPilo](https://git.serrebiradio.com/serrebi/BlindPilo)
- Projects site: [projects.serrebiradio.com](https://projects.serrebiradio.com)
- Questions and bug reports: [SerrebiProjects Telegram group](https://t.me/SerrebiProjects)
