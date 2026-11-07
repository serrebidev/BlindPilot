"""Backend adapters for BlindPilot.

BlindPilot began as Claude Code Reader. Claude's adapter remains in
``blindpilot_app.py``; this module contains the
provider-neutral discovery helpers and the shared turn lifecycle. Codex and
opencode and FreeBuff workers live in ``backend_workers``.
Hermes lives in ``hermes_backend.py`` and ``hermes_worker.py``, imported
on demand so a machine without Hermes pays nothing for it.

Codex's app-server used to be started fresh for every turn and killed at the
end of it. It is now owned by ``backend_pool``: one process shared by every
tab, held across turns instead of torn down between them, so a conversation
does not pay the app-server's start-up -- and, where MCP servers are
configured, all of their child processes' start-up too -- on every message.
``backend_pool.py`` decides how long that process lives; what is here says
only how to start, check, and stop it.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import atexit
import collections
import datetime
import difflib
import json
import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, NamedTuple, Optional, Protocol, Sequence, cast

import backend_pool
import certificates
import diagnostics
from accessible_ai import plan_token
from accessible_ai.providers import plan_relay
from accessible_ai.storage import preferences
from owned_process import _WindowsJob, owned_popen
import questions
import process_utils

from markdown_rows import complete_sentences as _complete_sentences  # noqa: F401 - compatibility alias


BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"
BACKEND_FREEBUFF = "freebuff"
BACKEND_OPENCODE = "opencode"
BACKEND_HERMES = "hermes"
BACKEND_MUSE = "muse"
# muse.ai, Meta's personal agent on its own cloud machine -- not Muse Code.
BACKEND_MUSEAI = "museai"
BACKEND_COMMANDCODE = "commandcode"
BACKEND_GEMINI = "gemini"
BACKEND_ANTIGRAVITY = "antigravity"
BACKEND_COPILOT = "copilot"
# FreeBuff has no model-list or model-selection CLI flags. Its installed
# package and downloaded executable do contain the live picker catalog, so the
# adapter discovers that catalog at runtime and writes the same setting the
# picker uses. GLM 5.3 is the preferred default while FreeBuff still offers it;
# FreeBuff drops and renames models between releases, so this is a preference
# rather than a requirement, and a release without it falls back to FreeBuff's
# own choice rather than failing.
FREEBUFF_PREFERRED_MODEL = "z-ai/glm-5.3-flash"
_FREEBUFF_SETTINGS_LOCK = threading.Lock()
_freebuff_catalog_cache: tuple[tuple[str, float, int], list[str]] | None = None


# ----- Subagents -----
# Every backend can hand work to agents of its own -- Claude Code's Agent tool,
# Codex's spawnAgent, Hermes' delegate_task, Muse's native subagents, opencode's
# task tool, Command Code's agent tool, FreeBuff's spawn_agents -- and each one
# reports them differently. The window lists the ones running and shows what
# each is doing, so every worker translates its provider's reports into one
# call: (agent id, name, status, line). An empty name or status leaves that part
# as it was, and an empty line adds nothing to the agent's log.
SubagentReport = Callable[[str, str, str, str], None]

SUBAGENT_RUNNING = "running"
SUBAGENT_COMPLETED = "completed"
SUBAGENT_FAILED = "failed"
SUBAGENT_STOPPED = "stopped"

_SUBAGENT_STATUS_WORDS = {
    SUBAGENT_RUNNING: (
        "running",
        "inprogress",
        "in_progress",
        "pendinginit",
        "pending",
        "starting",
        "started",
        "accepted",
        "active",
        "queued",
        "recoverypending",
    ),
    SUBAGENT_COMPLETED: (
        "completed",
        "complete",
        "succeeded",
        "success",
        "done",
        "finished",
        "resultready",
        "ok",
    ),
    SUBAGENT_FAILED: (
        "failed",
        "errored",
        "error",
        "notfound",
        "timeout",
        "timed_out",
        "timedout",
        "rejected",
    ),
    SUBAGENT_STOPPED: (
        "stopped",
        "cancelled",
        "canceled",
        "interrupted",
        "shutdown",
        "killed",
        "closed",
        "aborted",
        "closing",
    ),
}


def subagent_status(raw: object) -> str:
    """A provider's word for an agent's state, in the window's four.

    Empty for a word none of them uses, which the window reads as "unchanged":
    a newer provider's new state should not make a running agent look ended.
    """
    word = str(raw or "").strip().casefold().replace("-", "").replace(" ", "")
    for status, words in _SUBAGENT_STATUS_WORDS.items():
        if word in words:
            return status
    return ""


def subagent_line(text: object, limit: int = 300) -> str:
    """One readable line for an agent's log, from whatever the provider sent."""
    words = " ".join(str(text or "").split())
    return words if len(words) <= limit else words[: limit - 1].rstrip() + "…"


def ignore_subagents(_agent_id: str, _name: str, _status: str, _line: str) -> None:
    """The report for a worker created without one, as the tests create them."""


# ----- Mid-run questions -----
#
# Every backend BlindPilot drives can stop mid-turn to ask the person a
# multiple-choice question, and each one describes that question differently:
# Claude Code sends the AskUserQuestion tool's input through a `can_use_tool`
# control request, Codex sends an `item/tool/requestUserInput` JSON-RPC
# request, opencode publishes a `question.asked` event, and FreeBuff draws its
# `ask_user` tool straight onto its terminal. The window should not have to
# know any of that, so each adapter translates its provider's shape into the
# two types below, and translates the answers back on the way out.


@dataclass(frozen=True)
class BackendInfo:
    id: str
    label: str
    executable: str
    install_command: str
    login_args: tuple[str, ...]
    supports_model: bool
    supports_effort: bool
    supports_permissions: bool
    # Whether the provider can summarise a long conversation in place to free
    # up its context window. FreeBuff's CLI has no such command — its only
    # context control is starting a new conversation.
    supports_compaction: bool = False
    # Whether the CLI opens the sign-in page itself. Claude Code does, even
    # when BlindPilot starts it with no console, so that one is left to the CLI
    # and BlindPilot only opens the pages nobody else will. Codex is the
    # counter-example: its own browser launch does not reliably arrive from a
    # hidden process with its output on a pipe, and it has no flag to suppress
    # it, so BlindPilot opens Codex's page itself -- a duplicate tab is the
    # lesser cost of a sign-in page that never opens.
    login_opens_browser: bool = False
    # What the CLI's "paste the code from the browser" prompt looks like. It is
    # written without a newline after it, so it is matched against the output
    # as it arrives rather than line by line. Empty when the CLI never asks.
    login_code_prompt: str = ""
    # Whether signing in has to happen in a terminal the user can type into.
    # Claude and Codex authenticate through a browser and report the result on
    # exit, so BlindPilot can run them hidden and watch. Hermes' equivalent is
    # an interactive picker: run hidden with no stdin it simply fails, so the
    # wizard opens a real console instead of pretending to have signed in.
    login_needs_terminal: bool = False
    # Whether that terminal is kept off screen rather than shown to the user.
    # Hermes' setup asks questions the user has to answer, so its window is
    # theirs to read. Command Code needs a terminal only because its Ink UI
    # refuses to start without one -- the sign-in itself happens in the browser
    # the CLI opens -- so its terminal runs hidden like FreeBuff's and the
    # wizard waits for the command to finish instead of for the user.
    login_terminal_hidden: bool = False
    # A command to run before signing in. Command Code's `login` answers
    # "Already logged in" and exits whenever a credential file exists, so a key
    # the server has revoked can never be replaced from the wizard. Its own
    # troubleshooting says `logout` then `login`; this is that first half.
    login_reset_args: tuple[str, ...] = ()
    # Whether the backend takes an attachment's BYTES rather than its path.
    # The CLI backends run on this machine, so naming the file is enough for
    # them. Hermes may be running somewhere else entirely -- in WSL, or on
    # another machine over the network -- where a path from here means nothing
    # or, worse, means a different file that happens to share the name. Those
    # backends are handed the file itself.
    uploads_attachments: bool = False
    # Whether the sign-in terminal stays open after the sign-in lands. Gemini
    # CLI and Antigravity CLI sign in from inside their own interactive UI,
    # which keeps running afterwards, so the wizard watches for the CLI to
    # report itself signed in rather than for the terminal to close.
    login_watch_until_signed_in: bool = False
    # The npm package BlindPilot installs and updates this backend from. Empty
    # for a backend that ships its own installer, which is what says npm is not
    # the tool to send the user after. One field, so the installer, the update
    # check and the wizard cannot disagree about the package name.
    npm_package: str = ""


BACKENDS = {
    BACKEND_CLAUDE: BackendInfo(
        BACKEND_CLAUDE,
        "Claude Code",
        "claude",
        "See https://claude.com/claude-code",
        # "claude /login" is a slash command typed inside the interactive
        # session, not a command line. Run that way it opens a terminal UI that
        # BlindPilot has no console for, so it sat there until it timed out and
        # no browser ever opened. "claude auth login" is the command line.
        ("auth", "login"),
        True,
        True,
        True,
        supports_compaction=True,
        login_opens_browser=True,
        login_code_prompt=r"[Pp]aste code here[^>]*>",
    ),
    BACKEND_CODEX: BackendInfo(
        BACKEND_CODEX,
        "Codex",
        "codex",
        "npm install -g @openai/codex",
        ("login",),
        True,
        True,
        True,
        supports_compaction=True,
        # Codex opens the auth URL itself when it can, but not reliably from
        # the hidden process BlindPilot starts; its page is opened here instead
        # (see the login_opens_browser comment above).
        login_opens_browser=False,
        npm_package="@openai/codex",
    ),
    BACKEND_FREEBUFF: BackendInfo(
        BACKEND_FREEBUFF,
        "FreeBuff",
        "freebuff",
        "npm install -g freebuff",
        ("login",),
        True,
        False,
        False,
        supports_compaction=False,
        npm_package="freebuff",
    ),
    BACKEND_OPENCODE: BackendInfo(
        BACKEND_OPENCODE,
        "opencode",
        "opencode",
        "npm install -g opencode-ai",
        ("providers", "login"),
        True,
        True,
        True,
        supports_compaction=True,
        # Pictures and text files travel as file parts the model reads, so
        # the worker takes the files themselves rather than their paths.
        uploads_attachments=True,
        npm_package="opencode-ai",
    ),
    BACKEND_HERMES: BackendInfo(
        BACKEND_HERMES,
        "Hermes",
        "hermes",
        "See https://hermes-agent.nousresearch.com/docs",
        ("model",),
        True,
        # Hermes takes a reasoning level as a per-session override on
        # session.create. This said False at first, with a comment claiming the
        # protocol had no such control; it has one, and the effect is
        # measurable -- a session created with "low" reads back "low" while one
        # created without it reads the profile's own level. Saying False here
        # hid the control in the picker AND told the setup wizard to announce
        # that Hermes "does not expose a reasoning effort level".
        True,
        True,
        supports_compaction=True,
        login_needs_terminal=True,
        # Hermes' gateway takes an upload of the file, so an attachment works
        # the same whether Hermes is here, in WSL, or on another machine.
        uploads_attachments=True,
    ),
    BACKEND_MUSE: BackendInfo(
        BACKEND_MUSE,
        "Muse Code",
        "muse",
        "See https://developer.meta.com/ai/products/muse-code/ -- one-line install: "
        "curl -fsSL https://dev.meta.ai/install.sh | bash",
        # `muse login` is the device flow: it prints the sign-in address and
        # waits for the browser approval. It needs no code typed back, so the
        # login runner watches for the URL like Claude's and Codex's.
        ("login",),
        True,
        True,
        True,
        supports_compaction=True,
        # Images travel as base64 parts the model sees, and the rest are
        # named in the prompt by paths translated for the host -- on Windows
        # it runs inside WSL, where a path from here names nothing. Either
        # way the worker takes the files themselves, like Hermes' does.
        uploads_attachments=True,
    ),
    BACKEND_COMMANDCODE: BackendInfo(
        BACKEND_COMMANDCODE,
        "Command Code",
        # The npm package installs four launchers (cmd, cmdc, command-code,
        # commandcode). This one, never `cmd`: on Windows `shutil.which("cmd")`
        # finds the command interpreter in System32, not the agent.
        "command-code",
        "npm install -g command-code",
        # `cmd login` is the OAuth flow, which opens the browser itself.
        ("login",),
        True,
        True,
        True,
        supports_compaction=True,
        # "cmd login" is not a plain sign-in: it mounts an Ink terminal UI for
        # its authentication spinner, and Ink refuses to start when stdin is
        # not a TTY ("Raw mode is not supported on the current process.stdin,
        # which Ink uses as input stream by default"). Run hidden behind the
        # wizard's pipes it died on that mount before it reached the browser,
        # and its crash output -- Ink's own documentation URL included -- was
        # read out as the sign-in address, pointing a listener at
        # github.com/vadimdemedes/ink. It needs a real terminal, but there is
        # nothing in that terminal for anyone to read or answer -- the sign-in
        # is the browser page the CLI opens -- so it gets a hidden one rather
        # than a console window to tab past.
        login_needs_terminal=True,
        login_terminal_hidden=True,
        login_reset_args=("logout",),
        npm_package="command-code",
    ),
    BACKEND_GEMINI: BackendInfo(
        BACKEND_GEMINI,
        "Gemini CLI",
        "gemini",
        "npm install -g @google/gemini-cli",
        # Gemini CLI has no sign-in command line: its first run asks how to
        # sign in, and "Sign in with Google" opens the browser. Started in its
        # screen-reader mode, so that question reads as plain text.
        ("--screen-reader",),
        True,
        # No reasoning-level flag: the model choice is the only lever.
        False,
        True,
        # Its /compress is interactive-only; headless turns cannot ask for it.
        supports_compaction=False,
        login_needs_terminal=True,
        login_watch_until_signed_in=True,
        npm_package="@google/gemini-cli",
    ),
    BACKEND_ANTIGRAVITY: BackendInfo(
        BACKEND_ANTIGRAVITY,
        "Antigravity CLI",
        "agy",
        "See https://antigravity.google/docs/cli/install/ -- macOS and Linux: "
        "curl -fsSL https://antigravity.google/cli/install.sh | bash -- Windows "
        "PowerShell: irm https://antigravity.google/cli/install.ps1 | iex",
        # Running `agy` with no arguments is its Google sign-in: on this
        # machine it opens the browser itself (Antigravity CLI docs).
        (),
        True,
        True,
        True,
        supports_compaction=False,
        login_needs_terminal=True,
        login_watch_until_signed_in=True,
    ),
    # Added last on purpose: a backend's send earcon is pitched by its place
    # in this dict, so new backends go at the end and existing pitches stay.
    BACKEND_MUSEAI: BackendInfo(
        BACKEND_MUSEAI,
        "muse.ai",
        "muse-cli",
        "uv tool install muse-cli",
        # muse-cli signs in by copying the muse.ai cookies out of a Chrome
        # that is signed in with remote debugging on; it prints what to do
        # when that is missing, so the user needs to see its terminal.
        ("auth", "export"),
        # The agent picks its own model and runs its own tools on its own
        # machine; none of these are BlindPilot's to set.
        False,
        False,
        False,
        supports_compaction=False,
        login_needs_terminal=True,
    ),
    BACKEND_COPILOT: BackendInfo(
        BACKEND_COPILOT,
        "GitHub Copilot CLI",
        "copilot",
        "npm install -g @github/copilot",
        # GitHub's device flow: Copilot prints a one-time code and the page to
        # type it into, so the person needs to read its terminal.
        ("login",),
        True,
        True,
        True,
        # Its /compact is interactive-only; a headless turn cannot ask for it.
        supports_compaction=False,
        login_needs_terminal=True,
        npm_package="@github/copilot",
    ),
}

# Alphabetical by label, so every menu and list of backends reads in the same
# predictable order. `BACKENDS` itself stays in the order it was written.
BACKEND_IDS = tuple(sorted(BACKENDS, key=lambda id_: BACKENDS[id_].label.casefold()))
BACKEND_LABELS = {id_: BACKENDS[id_].label for id_ in BACKEND_IDS}
# The order backends were added in, which never changes: what the send
# earcon's pitch is keyed on, so a listener's ear for each backend survives
# the menus being re-sorted.
BACKEND_PITCH_ORDER = tuple(BACKENDS)

# What a "compact this conversation" turn looks like per provider: the text to
# send, and any extra keyword arguments its worker needs.
#
# Claude Code takes ``/compact`` as an ordinary message even in headless
# streaming mode, and acts on it. Codex and opencode have no such message —
# for both it is a request of its own — so their workers are told to compact
# instead, and ignore the text. The text is still shown to the user either
# way, so the row in the list says what was asked for.
_COMPACTION_REQUESTS: dict[str, tuple[str, dict]] = {
    BACKEND_CLAUDE: ("/compact", {}),
    BACKEND_CODEX: ("/compact", {"compact": True}),
    BACKEND_OPENCODE: ("/compact", {"compact": True}),
    # Hermes compacts through a request of its own, like Codex. Its own name
    # for the command is /compress; the text shown to the user says what was
    # asked for in BlindPilot's words, and the worker acts on the flag.
    BACKEND_HERMES: ("/compact", {"compact": True}),
    # Muse compacts the same way, over session/compact; the worker turns the
    # flag into that request.
    BACKEND_MUSE: ("/compact", {"compact": True}),
    BACKEND_COMMANDCODE: ("Summarize this conversation for continuation.", {"compact": True}),
}


def compaction_request(backend: object) -> Optional[tuple[str, dict]]:
    """(message text, extra worker arguments) to compact, or None if it can't."""
    return _COMPACTION_REQUESTS.get(normalize_backend(backend))


def normalize_backend(value: object) -> str:
    """Return a supported backend id, defaulting to Claude Code."""
    if not isinstance(value, str):
        return BACKEND_CLAUDE
    compact = re.sub(r"[\s_-]+", "", value).casefold()
    aliases = {
        "claude": BACKEND_CLAUDE,
        "claudecode": BACKEND_CLAUDE,
        "codex": BACKEND_CODEX,
        "freebuff": BACKEND_FREEBUFF,
        "opencode": BACKEND_OPENCODE,
        "opencodeai": BACKEND_OPENCODE,
        "hermes": BACKEND_HERMES,
        "hermesagent": BACKEND_HERMES,
        "nous": BACKEND_HERMES,
        "muse": BACKEND_MUSE,
        "musecode": BACKEND_MUSE,
        "meta": BACKEND_MUSE,
        "museai": BACKEND_MUSEAI,
        "muse.ai": BACKEND_MUSEAI,
        "musecli": BACKEND_MUSEAI,
        # "cmd" is one of Command Code's launcher names, and a stored settings
        # value using it should resolve here rather than fall back to Claude.
        "commandcode": BACKEND_COMMANDCODE,
        "cmd": BACKEND_COMMANDCODE,
        "gemini": BACKEND_GEMINI,
        "geminicli": BACKEND_GEMINI,
        "googlegemini": BACKEND_GEMINI,
        "antigravity": BACKEND_ANTIGRAVITY,
        "antigravitycli": BACKEND_ANTIGRAVITY,
        "googleantigravity": BACKEND_ANTIGRAVITY,
        "agy": BACKEND_ANTIGRAVITY,
        "copilot": BACKEND_COPILOT,
        "copilotcli": BACKEND_COPILOT,
        "githubcopilot": BACKEND_COPILOT,
        "githubcopilotcli": BACKEND_COPILOT,
    }
    return aliases.get(compact, BACKEND_CLAUDE)


def backend_label(backend: str) -> str:
    return BACKEND_LABELS[normalize_backend(backend)]


def _fallback_cli_paths(name: str) -> tuple[Path, ...]:
    home = Path.home()
    managed = blindpilot_data_dir() / "npm"
    managed_bin = managed if platform.system() == "Windows" else managed / "bin"
    if platform.system() == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        candidates: list[Path] = []
        # No .ps1: Popen cannot run one (WinError 193), so offering it would
        # fail every turn where finding nothing lets the wizard offer to install.
        for suffix in (".exe", ".cmd", ""):
            filename = name + suffix
            candidates.extend(
                [
                    managed_bin / filename,
                    appdata / "npm" / filename,
                    home / ".local" / "bin" / filename,
                    home / ".volta" / "bin" / filename,
                    local / "Microsoft" / "WinGet" / "Links" / filename,
                    local / "Programs" / name / filename,
                    # Antigravity's installer puts agy.exe here.
                    local / name / "bin" / filename,
                ]
            )
        return tuple(candidates)
    return (
        managed_bin / name,
        home / ".local" / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        home / ".npm-global" / "bin" / name,
        home / ".volta" / "bin" / name,
    )


def find_backend_cli(backend: str) -> Optional[str]:
    """Find a provider CLI even in the restricted PATH inherited by a GUI."""
    info = BACKENDS[normalize_backend(backend)]
    if info.id == BACKEND_HERMES:
        # Hermes installs itself under its own home directory rather than into
        # a shared bin, so it has a search of its own that also knows about the
        # virtual environment the gateway is launched from. It is not an npm
        # package, so it returns before the managed-prefix search below.
        from hermes_backend import find_hermes_cli

        return find_hermes_cli()
    if info.id == BACKEND_MUSE:
        # Muse's launcher is a bash script that only runs on macOS and Linux,
        # and on this machine that means inside WSL. The ordinary fallback
        # search would happily find the same-named launcher a user copied into
        # ~/.local/bin on the Windows side -- a file Popen cannot run -- and
        # then every turn would die on WinError 193, so Muse answers from its
        # own adapter, which knows about WSL, before any path-based search.
        from muse_backend import muse_cli_path

        return muse_cli_path()
    # A backend BlindPilot installed itself is complete, writable by this user,
    # and updated through the same prefix. Prefer it over an older system/npm
    # copy that happens to occur earlier on PATH.
    managed = blindpilot_data_dir() / "npm"
    managed_bin = managed if platform.system() == "Windows" else managed / "bin"
    suffixes = (".exe", ".cmd", "") if platform.system() == "Windows" else ("",)
    for suffix in suffixes:
        candidate = managed_bin / f"{info.executable}{suffix}"
        if candidate.is_file():
            return str(candidate)
    found = process_utils.which_without_cwd(info.executable)
    if found:
        return found
    for candidate in _fallback_cli_paths(info.executable):
        if candidate.is_file():
            return str(candidate)
    if platform.system() != "Windows":
        shell = os.environ.get("SHELL")
        if shell and os.path.isfile(shell):
            try:
                proc = subprocess.run(
                    [shell, "-l", "-c", f"command -v {info.executable}"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                result = proc.stdout.strip().splitlines()
                if proc.returncode == 0 and result and os.path.isfile(result[0]):
                    return result[0]
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None


def _muse_version_probe() -> str:
    """Muse's version through its own bridge, patchable like the rest.

    Same indirection as `_muse_signed_in_checked`: the tests that loop over
    every backend patch this rather than booting the real WSL distribution
    this machine happens to have.
    """
    from muse_backend import muse_version

    return muse_version()


def _muse_signed_in_checked() -> bool:
    """The Muse signed-in answer, behind an indirection the suite can patch.

    Hermes gets the same treatment: a name in this module's own namespace is
    what monkeypatch can reach, and the tests that loop over every backend
    must never reach into the real WSL distribution on a machine that has one.
    """
    from muse_backend import muse_signed_in

    return muse_signed_in()


def backend_auth_ok(backend: str, timeout: int = 12) -> bool:
    """Best-effort non-interactive authentication check.

    Each backend answers it in the way it can -- a CLI subcommand, a stored
    credential, a gateway handshake -- and its adapter holds that answer in one
    place rather than in a chain of comparisons here.
    """
    try:
        return _adapter(backend).auth_ok(timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False


def _freebuff_account() -> Optional[dict]:
    """The account FreeBuff stored at sign-in, or None if there is none to read."""
    credential = Path.home() / ".config" / "manicode" / "credentials.json"
    try:
        payload = json.loads(credential.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    account = payload.get("default") if isinstance(payload, dict) else None
    if (
        isinstance(account, dict)
        and not account.get("authToken")
        and account.get("tokenStore") == "keychain"
    ):
        account["authToken"] = _freebuff_keychain_token(credential)
    return account if isinstance(account, dict) else None


def _freebuff_keychain_token(credential: Path) -> str:
    """Read Bun.secrets' FreeBuff entry, without migrating it back to JSON."""
    from accessible_ai.storage.credentials import CredentialStore

    try:
        if platform.system() == "Windows":
            return CredentialStore._windows_read(f"freebuff-cli/{credential}", encoding="utf-8")
        if platform.system() == "Darwin":
            return CredentialStore._fallback_read(str(credential), "freebuff-cli")
        import secretstorage

        connection = secretstorage.dbus_init()
        try:
            for item in secretstorage.search_items(
                connection, {"service": "freebuff-cli", "account": str(credential)}
            ):
                if not item.is_locked():
                    return item.get_secret().decode("utf-8")
        finally:
            connection.close()
    except Exception:
        # A locked or unavailable secure store cannot confirm a sign-in.
        return ""
    return ""


def _freebuff_signed_in(account: Optional[dict]) -> bool:
    fields = (
        ("authToken", "email")
        if account and account.get("email")
        else ("authToken", "fingerprintId", "fingerprintHash")
    )
    return account is not None and all(
        isinstance(account.get(field), str) and account[field].strip() for field in fields
    )


def _opencode_providers() -> list[str]:
    """The providers opencode could run a model on, read without starting it.

    Answered from the credentials opencode stored when a provider was
    connected, which is where /connect puts them, plus its own key in the
    environment. This has to answer without starting a server, because it is
    asked while the setup wizard is on screen.

    Deliberately not "is any ``*_API_KEY`` set": opencode does read a provider's
    key straight out of the environment, but so do plenty of programs that have
    nothing to do with it, and a confident yes that turns into a wall at the
    first message is worse than an "unconfirmed" the wizard lets you walk past.
    """
    providers: list[str] = []
    try:
        payload = json.loads((_opencode_data_dir() / "auth.json").read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            providers = sorted(str(name) for name in payload)
    except (OSError, ValueError):
        providers = []
    if os.environ.get("OPENCODE_API_KEY", "").strip():
        providers.append("OPENCODE_API_KEY in the environment")
    return providers


def _probe_backend(binary: str, args: list[str], timeout: int) -> tuple[Optional[int], str]:
    """Run a short provider command. Returns (exit code, what it printed).

    The exit code is ``None`` when the command could not be run at all, which
    is a different answer from "it ran and said no" and is reported as such.
    """
    try:
        proc = subprocess.run(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**subprocess_env(binary), "PYTHONIOENCODING": "utf-8"},
            **process_utils.no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    # These CLIs draw their own output, so what comes back is wrapped in the
    # escape sequences that would otherwise be spelled out letter by letter.
    stdout = _strip_terminal_noise(proc.stdout)
    stderr = _strip_terminal_noise(proc.stderr)
    # A CLI that painted a banner on stdout and failed on stderr used to hand
    # back only the banner; when both streams have something to say, both are
    # kept, because the error is the half the caller needs.
    return proc.returncode, f"{stdout}\n{stderr}" if stdout and stderr else stdout or stderr


def _claude_account_lines(code: Optional[int], text: str) -> list[str]:
    """Read `claude auth status`, which answers in JSON."""
    if code is None:
        return ["Signed in: could not ask Claude Code"]
    payload: object = None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            payload = None
    if not isinstance(payload, dict):
        # A future release that stops answering in JSON still has something to
        # say, and its own words are better than a shrug.
        return [f"Signed in: {'yes' if code == 0 else 'no'}"] + ([text] if text else [])
    lines = [f"Signed in: {'yes' if payload.get('loggedIn') else 'no'}"]
    for field, caption in (
        ("email", "Account"),
        ("subscriptionType", "Subscription"),
        ("authMethod", "Signed in with"),
        ("orgName", "Organisation"),
    ):
        value = str(payload.get(field) or "").strip()
        if value:
            lines.append(f"{caption}: {value}")
    return lines


def _codex_account_lines(code: Optional[int], text: str) -> list[str]:
    """Read `codex login status`, which answers in one sentence."""
    if code is None:
        return ["Signed in: could not ask Codex"]
    lines = [f"Signed in: {'yes' if code == 0 else 'no'}"]
    if text:
        lines.append(f"Account: {' '.join(text.split())}")
    return lines


def _freebuff_account_lines() -> list[str]:
    """FreeBuff has no status command; its stored credentials are the answer."""
    account = _freebuff_account()
    if account is None:
        return ["Signed in: no"]
    lines = [f"Signed in: {'yes' if _freebuff_signed_in(account) else 'no'}"]
    for field, caption in (("name", "Account"), ("email", "Email")):
        value = str(account.get(field) or "").strip()
        if value:
            lines.append(f"{caption}: {value}")
    return lines


def _opencode_account_lines() -> list[str]:
    """opencode has no status command either; its stored credentials answer.

    Read off disk rather than asked of its server, because /status should not
    be the thing that starts one — a report on what is already set up has no
    business spending ten seconds bringing a server to life to say so.
    """
    providers = _opencode_providers()
    if not providers:
        return ["Signed in: no", "Connected providers: none"]
    return ["Signed in: yes", f"Connected providers: {', '.join(providers)}"]


# ----- Plan usage limits -----
#
# Every backend that meters an account of its own is asked how much of it is
# left, and each is asked in the way it can answer:
#
#   Claude Code  fixed windows -- five-hour, weekly, sometimes weekly for one
#                model -- reported as a percentage and a reset time. There is
#                no command line for it: it answers a `get_usage` control
#                request on the same stream-json channel a turn is driven over.
#   Codex        the same shape, from the `account/rateLimits/read` method on
#                its app-server, reusing the pooled one where a tab has it.
#   FreeBuff     a credit balance rather than a window, spent down over a cycle
#                that refills on a date. Its CLI has no command for this
#                either; the balance comes from the same account endpoint its
#                own usage banner reads, with the credentials it signed in
#                with.
#   Hermes       a pool of provider credentials rather than one account, so
#                what it meters is which of them are spent. It writes each
#                credential's last outcome to the pool file, and a spent one
#                carries the time it may be used again where the provider
#                said so.
#   opencode     nothing of its own. It spends whichever provider account is
#                connected, its server offers no route that reports one, and
#                the rate-limit headers it does read are used to decide a
#                retry rather than kept anywhere that could be asked. So it
#                reports nothing, rather than a number that would be a guess.
#   Command Code nothing of its own either: usage is behind the interactive
#                /usage command and the billing website, and the headless
#                surface has no command that reports it.
#
# What is metered is the account's business, not BlindPilot's. A plan may have
# a five-hour window and no weekly one, a weekly window for one model and not
# another, or none at all while the session runs on an API key, on Bedrock or
# on Vertex. A limit the backend does not report is left out rather than
# written as unknown, and an account that reports none at all leaves the status
# report with no usage section, which is what says there is nothing to show.


@dataclass(frozen=True)
class UsageWindow:
    """One metered window of a subscription, as the backend reports it.

    `minutes` is how long the window is, which is only ever used to order the
    lines: a listener wants the window that empties soonest first, and the
    backends hand them over in the order that suits their own protocol.

    `percent` is how full the window is where the backend measures it that way,
    and None where it does not. Not every backend does: FreeBuff counts credits
    off a balance and Hermes reports a credential as spent or not, so those say
    what they know in `detail` instead of inventing a fraction to say it in.
    """

    label: str
    percent: Optional[float] = None
    resets_at: Optional[float] = None
    minutes: Optional[float] = None
    scoped: bool = False
    detail: str = ""


def _as_number(value: object) -> Optional[float]:
    """A number the backend really sent, or None. Booleans are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _epoch_from_iso(value: object) -> Optional[float]:
    """Claude Code times a window's end as an ISO 8601 string; Codex uses epoch."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        # A time with no zone on it is the server's, and the server speaks UTC.
        when = when.replace(tzinfo=datetime.timezone.utc)
    try:
        return when.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _until_words(seconds: float) -> str:
    """How long until a window empties, in words rather than a bare timestamp.

    A time of day alone makes the listener do the arithmetic, and "resets at
    08:50" is no help at all to somebody who does not already know what time it
    is now.
    """
    if seconds <= 0:
        return "now"
    minutes = max(1, int(seconds // 60))
    if minutes < 60:
        return f"in {minutes} minute{'' if minutes == 1 else 's'}"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        if rest:
            return (
                f"in {hours} hour{'' if hours == 1 else 's'} "
                f"{rest} minute{'' if rest == 1 else 's'}"
            )
        return f"in {hours} hour{'' if hours == 1 else 's'}"
    days, hours = divmod(hours, 24)
    if hours:
        return f"in {days} day{'' if days == 1 else 's'} {hours} hour{'' if hours == 1 else 's'}"
    return f"in {days} day{'' if days == 1 else 's'}"


def _reset_words(epoch: Optional[float]) -> str:
    """When the window empties, said in local time and as a wait."""
    if epoch is None:
        return ""
    try:
        when = datetime.datetime.fromtimestamp(epoch).astimezone()
    except (OverflowError, OSError, ValueError):
        return ""
    return f"resets {when.strftime('%a %d %b %H:%M')} ({_until_words(epoch - time.time())})"


def _percent_words(percent: float) -> str:
    """A fraction of a window, rounded the way it is worth saying out loud."""
    if percent <= 0:
        return "0% used"
    if percent < 1:
        return "under 1% used"
    return f"{percent:.0f}% used"


def _usage_window_line(window: UsageWindow) -> str:
    parts = [window.detail] if window.detail else []
    if window.percent is not None:
        parts.append(_percent_words(window.percent))
    reset = _reset_words(window.resets_at)
    if reset:
        parts.append(reset)
    if not parts:
        # Nothing but a name is not a report, and a line ending in a colon
        # reads as a value that went missing.
        return ""
    return f"{window.label}: {', '.join(parts)}"


def _ordered_windows(windows: Sequence[UsageWindow]) -> list[UsageWindow]:
    """Shortest window first, and the account's own windows before one model's.

    Neither backend hands them over in an order worth reading out: Codex sends
    a map keyed by limit id, and a plan that meters one model separately would
    otherwise put that model's weekly window above the account's own.
    """
    return sorted(windows, key=lambda window: (window.minutes or 0.0, window.scoped))


# Claude Code fetches the utilization figures in the background, so the first
# answer after a process starts often carries `rate_limits: null` -- not "this
# account has no windows", just "not fetched yet". The question is asked again
# a couple of times before that is believed. Measured: the fetch has landed
# within a second or two of start-up.
_CLAUDE_USAGE_ATTEMPTS = 3
_CLAUDE_USAGE_RETRY_SECONDS = 2.0

_CLAUDE_USAGE_WINDOWS = (
    ("five_hour", "Five-hour limit", 300.0),
    ("seven_day", "Weekly limit", 10080.0),
    ("seven_day_opus", "Weekly Opus limit", 10080.0),
    ("seven_day_sonnet", "Weekly Sonnet limit", 10080.0),
)


# What has been said already, so a warning that every turn repeats is heard
# once per window: a new window, or the limit being reached, says it again.
_CLAUDE_LIMITS_SAID: set = set()


def claude_limit_warning(info: object) -> str:
    """The line to say for a stream-json `rate_limit_event`, or "".

    Claude Code sends one with every turn. Only `allowed_warning` (past the
    plan's warning threshold) and `rejected` (the limit is reached) are worth
    interrupting for, and each only once per window.
    """
    if not isinstance(info, dict):
        return ""
    status = info.get("status")
    if status not in ("allowed_warning", "rejected"):
        return ""
    kind = info.get("rateLimitType")
    labels = {field: label for field, label, _minutes in _CLAUDE_USAGE_WINDOWS}
    label = labels.get(kind) if isinstance(kind, str) else None
    resets = _as_number(info.get("resetsAt"))
    key = (kind, resets, status)
    if key in _CLAUDE_LIMITS_SAID:
        return ""
    _CLAUDE_LIMITS_SAID.add(key)
    used = _as_number(info.get("utilization"))
    window = UsageWindow(
        label or "Usage limit",
        100.0 if status == "rejected" else (used * 100 if used is not None else None),
        resets,
    )
    line = _usage_window_line(window) or window.label
    if status == "rejected":
        return f"Claude Code {line}. The limit is reached."
    return f"Claude Code {line}."


def _claude_usage_windows(payload: Optional[dict]) -> list[UsageWindow]:
    """Read the `get_usage` answer. Anything not reported is left out."""
    if not isinstance(payload, dict):
        return []
    if payload.get("rate_limits_available") is False:
        # An API key, Bedrock or Vertex session: the plan windows do not apply
        # to it at all, and `rate_limits` is null for that reason rather than
        # because the figures have not arrived.
        return []
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return []
    windows: list[UsageWindow] = []
    for field, label, minutes in _CLAUDE_USAGE_WINDOWS:
        entry = limits.get(field)
        if not isinstance(entry, dict):
            continue
        percent = _as_number(entry.get("utilization"))
        if percent is None:
            continue
        windows.append(
            UsageWindow(label, percent, _epoch_from_iso(entry.get("resets_at")), minutes)
        )
    # Weekly windows the server names itself, for the models a plan meters
    # separately. Additive, and only ever present when the server sends them.
    scoped = limits.get("model_scoped")
    if isinstance(scoped, list):
        for entry in scoped:
            if not isinstance(entry, dict):
                continue
            percent = _as_number(entry.get("utilization"))
            name = str(entry.get("display_name") or "").strip()
            if percent is None or not name:
                continue
            windows.append(
                UsageWindow(
                    f"Weekly {name} limit",
                    percent,
                    _epoch_from_iso(entry.get("resets_at")),
                    10080.0,
                    scoped=True,
                )
            )
    return windows


def _claude_usage_reply(stdout: "IO[str]", request_id: str) -> Optional[dict]:
    """Read the stream until this control request is answered, or it ends."""
    for raw in stdout:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "control_response":
            continue
        response = event.get("response")
        if not isinstance(response, dict) or response.get("request_id") != request_id:
            continue
        if response.get("subtype") != "success":
            # A release that does not know this request says so rather than
            # answering it, and there is nothing to report.
            return None
        return response.get("response")
    return None


def _claude_usage_exchange(proc: "subprocess.Popen[str]") -> Optional[dict]:
    """Ask a running process for its usage, retrying while the fetch lands."""
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:
        return None
    payload: Optional[dict] = None
    for attempt in range(_CLAUDE_USAGE_ATTEMPTS):
        request_id = uuid.uuid4().hex
        try:
            stdin.write(
                json.dumps(
                    {
                        "type": "control_request",
                        "request_id": request_id,
                        "request": {"subtype": "get_usage", "skip_behaviors": True},
                    }
                )
                + "\n"
            )
            stdin.flush()
        except (OSError, ValueError):
            return payload
        answer = _claude_usage_reply(stdout, request_id)
        if answer is None:
            return payload
        payload = answer
        if _claude_usage_windows(payload) or answer.get("rate_limits_available") is False:
            return payload
        if attempt + 1 < _CLAUDE_USAGE_ATTEMPTS:
            time.sleep(_CLAUDE_USAGE_RETRY_SECONDS)
    return payload


def _claude_usage_payload(binary: str, timeout: int) -> Optional[dict]:
    """Ask a short-lived Claude Code process what the plan windows look like.

    A process of its own rather than the tab's: the pooled one belongs to a
    conversation that may be mid-turn, and the report is asked for from the
    window's own thread. `--no-session-persistence` keeps the question out of
    the conversation list, where a session holding nothing but a control
    request would be offered as something to resume.

    `skip_behaviors` skips a scan of every transcript on this machine. That
    scan is what the CLI's own usage dialog draws its attribution from, and
    none of it is wanted here.
    """
    command = [
        binary,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(Path.home()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=subprocess_env(binary),
            **process_utils.own_group_kwargs(),
            **process_utils.no_window_kwargs(),
        )
    except (OSError, ValueError):
        return None
    # Reading a pipe has no deadline of its own, so the deadline is the
    # process: ending it is what turns a silent CLI into an end of stream
    # rather than a status dialog that never opens.
    watchdog = threading.Timer(
        max(1.0, float(timeout)), lambda: process_utils.end_process_group(proc)
    )
    watchdog.daemon = True
    watchdog.start()
    try:
        return _claude_usage_exchange(proc)
    finally:
        watchdog.cancel()
        process_utils.end_process_group(proc)
        # Ending the process does not hand its pipes back. /status can be asked
        # as often as somebody presses it, and two file handles a press is a
        # leak in an application that stays open all day.
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        try:
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass


_CODEX_WINDOW_FIELDS = ("primary", "secondary")


def _codex_window_name(minutes: Optional[float], field: str = "") -> str:
    """What to call a window of this length.

    Codex names its windows only by how long they are, and which of primary
    and secondary is the five-hour one differs by account: on a plan whose
    default limit is weekly, primary *is* the weekly window. So the length is
    what the line is named after, and the position is fallen back on only
    where the length is missing -- two windows of one limit both reported
    without a duration have nothing else left to tell them apart.
    """
    if minutes is None:
        return field.capitalize() if field else "Usage"
    length = int(minutes)
    if length == 300:
        return "Five-hour"
    if length == 10080:
        return "Weekly"
    if length % 1440 == 0:
        days = length // 1440
        return f"{days}-day"
    if length % 60 == 0:
        hours = length // 60
        return f"{hours}-hour"
    return f"{length}-minute"


def _codex_limit_identity(group: dict) -> tuple:
    """What makes one snapshot the same metered limit as another.

    The figures themselves, rather than what the lines would be called: two
    genuinely different windows can end up under the same name -- a limit with
    no `limitName` and no `windowDurationMins` on either window is named twice
    over by its position alone -- and telling those apart by name would lose
    one of them.
    """
    parts = [str(group.get("limitName") or "").strip()]
    for field in _CODEX_WINDOW_FIELDS:
        entry = group.get(field)
        if not isinstance(entry, dict):
            parts.append("")
            continue
        parts.append(
            "/".join(
                str(_as_number(entry.get(key)))
                for key in ("usedPercent", "windowDurationMins", "resetsAt")
            )
        )
    return tuple(parts)


def _codex_usage_windows(result: object) -> list[UsageWindow]:
    """Read `account/rateLimits/read`. Anything not reported is left out."""
    if not isinstance(result, dict):
        return []
    groups: list[dict] = []
    # The per-limit map first: it is keyed by the metered limit's own id, and
    # it names the windows a plan meters for one model.
    identities: set[tuple] = set()
    by_limit = result.get("rateLimitsByLimitId")
    if isinstance(by_limit, dict):
        for entry in by_limit.values():
            if isinstance(entry, dict):
                groups.append(entry)
                identities.add(_codex_limit_identity(entry))
    # `rateLimits` is the account's default snapshot, which is one of the
    # limits above again under a different key rather than a window of its own.
    # It is added only where the map did not already carry the same figures.
    default = result.get("rateLimits")
    if isinstance(default, dict) and _codex_limit_identity(default) not in identities:
        groups.append(default)
    windows: list[UsageWindow] = []
    for group in groups:
        name = str(group.get("limitName") or "").strip()
        for field in _CODEX_WINDOW_FIELDS:
            entry = group.get(field)
            if not isinstance(entry, dict):
                continue
            percent = _as_number(entry.get("usedPercent"))
            if percent is None:
                continue
            minutes = _as_number(entry.get("windowDurationMins"))
            base = _codex_window_name(minutes, field)
            label = f"{base} {name} limit" if name else f"{base} limit"
            windows.append(
                UsageWindow(
                    label,
                    percent,
                    _as_number(entry.get("resetsAt")),
                    minutes,
                    scoped=bool(name),
                )
            )
    return windows


def _codex_usage_handshake(server: "CodexServer") -> bool:
    """Introduce BlindPilot to a server started for this question alone."""
    sent = server.send(
        {
            "method": "initialize",
            "id": server.next_id(),
            "params": {
                "clientInfo": {
                    "name": "blindpilot",
                    "title": "BlindPilot",
                    "version": _app_version(),
                }
            },
        }
    )
    return sent and server.send({"method": "initialized", "params": {}})


def _codex_rate_limits(server: "CodexServer", timeout: int) -> object:
    """Ask one app-server for the account's windows and wait for the reply."""
    inbox: queue.Queue[object] = queue.Queue()
    request_id = server.next_id()
    server.expect(request_id, inbox)
    try:
        if not server.send({"method": "account/rateLimits/read", "id": request_id, "params": {}}):
            return None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = inbox.get(timeout=remaining)
            except queue.Empty:
                return None
            if not isinstance(message, dict):
                # The server closed under the question.
                return None
            if message.get("id") != request_id:
                continue
            return message.get("result")
    finally:
        server.unexpect([request_id])


def _codex_usage_result(binary: str, timeout: int) -> object:
    """Ask Codex for the account's windows, sharing the app-server if there is one.

    The pooled process is used where one is running: it has been through the
    handshake already, several tabs may be mid-turn on it, and this question
    is a read that does not touch any of their conversations. It is borrowed
    for the length of the question so the reaper cannot stop it underneath.
    Where no process is held, one is started for the question and stopped
    again, the way every other part of this report is asked.

    Asked of the Account the backend is set to use, because that is whose
    windows the report is about: a plan's remaining usage is not the same
    question as the one Codex's own login answers.
    """
    account = codex_plan_account()
    key = codex_pool_key(account)
    shared = backend_pool.pool()
    server: Optional[CodexServer] = None
    with _CODEX_START_LOCK:
        held = shared.take(key)
        if held is not None:
            shared.keep(key, held)
            server = cast(CodexServer, held.handle)
            server.borrow()
    if server is not None:
        try:
            return _codex_rate_limits(server, timeout)
        finally:
            server.give_back()
    try:
        started = _start_codex_server(binary, plan_account=account)
    except OSError:
        return None
    try:
        if not _codex_usage_handshake(started):
            return None
        return _codex_rate_limits(started, timeout)
    finally:
        started.stop()


# FreeBuff's own banner reads this, and reaching it is one request against an
# account that is already signed in, so the report does not wait on a CLI to
# start. The app URL is the one its CLI compiles in, overridable by the same
# environment variable the CLI reads, for anyone pointed at another deployment.
_FREEBUFF_APP_URL = "https://www.codebuff.com"
_FREEBUFF_USAGE_PATH = "/api/v1/usage"
# A cycle runs a month, which is only ever used to order the lines.
_FREEBUFF_CYCLE_MINUTES = 43200.0


def _freebuff_app_url() -> str:
    return (os.environ.get("NEXT_PUBLIC_CODEBUFF_APP_URL", "").strip() or _FREEBUFF_APP_URL).rstrip(
        "/"
    )


def _freebuff_usage_payload(timeout: int) -> Optional[dict]:
    """Ask FreeBuff's account endpoint what this balance looks like.

    Signed out there is nothing to ask with, and asking anyway would be a
    request that can only come back rejected.
    """
    import urllib.error
    import urllib.request

    account = _freebuff_account()
    if account is None or not _freebuff_signed_in(account):
        return None
    body = json.dumps(
        {
            "fingerprintId": str(account.get("fingerprintId") or ""),
            "authToken": str(account.get("authToken") or ""),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        _freebuff_app_url() + _FREEBUFF_USAGE_PATH,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": f"BlindPilot/{_app_version()}"},
        method="POST",
    )
    try:
        # The packaged trust store, not OpenSSL's guess: a frozen
        # build's empty store would fail this call like any other.
        from certificates import open_url

        with open_url(request, timeout=max(1, min(timeout, 10))) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.HTTPError):
        return None
    return payload if isinstance(payload, dict) else None


def _credit_words(amount: float) -> str:
    count = int(round(amount))
    return f"{count:,} credit{'' if count == 1 else 's'}"


def _freebuff_usage_windows(payload: Optional[dict]) -> list[UsageWindow]:
    """Read the balance. What is left is the figure; the fraction is derived.

    The endpoint reports what has been spent this cycle and what is left, and
    what is left is the number worth hearing first -- a percentage of a balance
    that top-ups and referrals move around is the less honest half of it, so it
    is only added where the two figures together say what the cycle held.
    """
    if not isinstance(payload, dict):
        return []
    used = _as_number(payload.get("usage"))
    remaining = _as_number(payload.get("remainingBalance"))
    if used is None and remaining is None:
        return []
    said = []
    if remaining is not None:
        said.append(f"{_credit_words(remaining)} left")
    if used is not None:
        said.append(f"{_credit_words(used)} used this cycle")
    granted = (used or 0.0) + (remaining or 0.0)
    percent = used / granted * 100.0 if used is not None and granted > 0 else None
    return [
        UsageWindow(
            "Credits",
            percent,
            _epoch_from_iso(payload.get("next_quota_reset")),
            _FREEBUFF_CYCLE_MINUTES,
            detail=", ".join(said),
        )
    ]


# What Hermes writes against a credential once it has stopped taking work. Any
# other value is a credential that is still spending, and a pool that is all
# still spending has nothing to report.
_HERMES_SPENT_STATUSES = ("exhausted", "rate_limited", "cooldown", "disabled")


def _hermes_limit_words(credential: dict) -> str:
    """Why this credential stopped, in words rather than the provider's code.

    A rate limit and an empty wallet both stop a credential, and they are not
    the same news: one comes back on its own and the other does not.
    """
    reason = str(credential.get("last_error_reason") or "").strip()
    lowered = reason.lower()
    code = _as_number(credential.get("last_error_code"))
    if code == 429 or "rate" in lowered or "usage_limit" in lowered or "quota" in lowered:
        return "rate limit reached"
    if "credit" in lowered or "balance" in lowered or "billing" in lowered or "payment" in lowered:
        return "out of credits"
    return f"unavailable ({reason})" if reason else "unavailable"


def _hermes_usage_windows(payload: Optional[dict]) -> list[UsageWindow]:
    """Read Hermes' credential pool for the ones it has stopped spending.

    Hermes runs on a pool rather than on one account, and a pool has no single
    figure to be a percentage of. What it does know is which credentials are
    spent and, where the provider said so, when each may be used again -- so
    that is what is reported, and a pool with nothing spent reports nothing.
    """
    if not isinstance(payload, dict):
        return []
    pool = payload.get("credential_pool")
    if not isinstance(pool, dict):
        return []
    windows: list[UsageWindow] = []
    for provider, credentials in sorted(pool.items()):
        if not isinstance(credentials, list):
            continue
        for credential in credentials:
            if not isinstance(credential, dict):
                continue
            status = str(credential.get("last_status") or "").strip().lower()
            if status not in _HERMES_SPENT_STATUSES:
                continue
            label = str(credential.get("label") or "").strip()
            named = f"{provider} ({label})" if label and label != provider else str(provider)
            # "Provider" in front of it, because these sit under the account
            # lines in the report and a bare credential name there reads as a
            # heading rather than as a limit that has been reached.
            name = f"Provider {named}"
            windows.append(
                UsageWindow(
                    name,
                    None,
                    _as_number(credential.get("last_error_reset_at")),
                    detail=_hermes_limit_words(credential),
                )
            )
    return windows


def _hermes_pool_payload() -> Optional[dict]:
    """The pool file Hermes keeps its credential outcomes in.

    Read off disk rather than asked of the CLI: `hermes auth list` prints this
    same state, but starting Hermes to print it costs seconds the status report
    should not spend. A Hermes reached over the network keeps its own pool on
    its own machine, and this file is the local one -- the same caveat the
    settings entry for Hermes' config carries.
    """
    try:
        payload = json.loads((_hermes_home() / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def backend_usage_lines(backend: str, binary: str, timeout: int = 20) -> list[str]:
    """How much of this account's metered allowance is left, as report lines.

    Empty for opencode, which meters nothing of its own, and empty for an
    account whose backend reports no limits: there is nothing truthful to say
    about a limit the backend has not named, and a line saying so would be one
    more thing to arrow past on every other account.
    """
    return _adapter(backend).usage_lines(binary, timeout)


def _hermes_account_lines() -> list[str]:
    """Hermes signs in to a pool of providers, and the pool file is the answer.

    Its `hermes status` does print this, among four screens of everything else
    it knows, and starting Hermes to read it costs seconds. What signing in
    means here is also not one account: Hermes holds a credential per provider
    and moves between them, so what the report names is the provider it is set
    to use and the ones it could fall back to.
    """
    payload = _hermes_pool_payload()
    pool = payload.get("credential_pool") if isinstance(payload, dict) else None
    providers = sorted(
        str(name)
        for name, credentials in (pool or {}).items()
        if isinstance(credentials, list) and credentials
    )
    if not providers:
        return ["Signed in: no", "Connected providers: none"]
    lines = ["Signed in: yes", f"Connected providers: {', '.join(providers)}"]
    active = str((payload or {}).get("active_provider") or "").strip()
    if active:
        lines.insert(1, f"Provider in use: {active}")
    return lines


def _muse_usage_payload() -> object:
    """The usage one of this app's Muse turns last observed, behind the same
    indirection the other Muse answers use so the suite can patch it."""
    from muse_backend import muse_usage_payload

    return muse_usage_payload()


def _muse_usage_windows(payload: object) -> list[UsageWindow]:
    """Read ``usage/read``: a current window and a rolling weekly block.

    Both carry the provider's verbatim used percent with the reset stamp in
    epoch milliseconds; the weekly block has no duration of its own.
    """
    if not isinstance(payload, dict):
        return []
    windows: list[UsageWindow] = []
    for field, fallback in (("window", "Session"), ("weekly", "Weekly")):
        entry = payload.get(field)
        if not isinstance(entry, dict):
            continue
        percent = _as_number(entry.get("usedPercent"))
        if percent is None:
            continue
        resets_at = _as_number(entry.get("resetsAtMs"))
        minutes = _as_number(entry.get("windowDurationMins"))
        base = _codex_window_name(minutes, field) if minutes is not None else fallback
        windows.append(
            UsageWindow(
                f"{base} limit",
                percent,
                resets_at / 1000 if resets_at is not None else None,
                minutes,
            )
        )
    return windows


def _muse_account_lines() -> list[str]:
    """Muse signs in with one Meta account, and what it stored is the answer.

    Muse has no auth-status subcommand, so the credential file is read the
    way FreeBuff's is. The reading itself lives in muse_backend, which knows
    whether the file is on this side of a WSL boundary or not.
    """
    from muse_backend import muse_account_lines

    return muse_account_lines()


def backend_status(backend: str, timeout: int = 20) -> str:
    """What the chosen backend can say about itself, as lines of plain text.

    This is what ``/status`` reports, and every backend answers it. None of
    them answers it themselves in the headless mode BlindPilot drives them in:
    Claude Code's own ``/status`` is interactive-only and replies "/status
    isn't available in this environment" when it is sent as a message, and
    Codex, FreeBuff and opencode have no status command at all, and Hermes'
    prints four screens of everything it knows. So each one is asked in the way
    it can actually answer — a CLI subcommand where there is one, the
    credentials it stored where there is not — and the answers are written the
    same way, so the report reads the same whichever backend is selected.
    """
    backend = normalize_backend(backend)
    lines = [f"Backend: {backend_label(backend)}"]
    binary = find_backend_cli(backend)
    if not binary:
        lines.append("Command line: not installed")
        return "\n".join(lines)
    lines.append(f"Command line: {binary}")
    if backend == BACKEND_MUSE:
        # The "binary" is a bash launcher inside WSL on Windows; Popen cannot
        # run it from here, so the version is asked through Muse's own bridge.
        version = _muse_version_probe()
        if version:
            lines.append(f"Version: {version.splitlines()[0].strip()}")
    else:
        _code, version = _probe_backend(binary, ["--version"], min(timeout, 20))
        if version:
            lines.append(f"Version: {version.splitlines()[0].strip()}")
    lines.extend(_adapter(backend).account_lines(binary, timeout))
    lines.extend(backend_usage_lines(backend, binary, timeout))
    return "\n".join(lines)


# A macOS application launched from Finder or the Dock inherits launchd's PATH
# — /usr/bin:/bin:/usr/sbin:/sbin — and nothing else. Every provider CLI
# installed by npm or Homebrew is a `#!/usr/bin/env node` shim, so a child
# started with that PATH dies on "env: node: No such file or directory" before
# it prints anything a person could act on. Asking the login shell what PATH it
# would have given is the only way to recover the directories the user actually
# installed into. It costs a shell start-up, so it is asked once.
_login_shell_path: Optional[list[str]] = None
_LOGIN_SHELL_PATH_LOCK = threading.Lock()


def login_shell_path_dirs() -> list[str]:
    """The PATH a terminal would have, for handing to children (POSIX only)."""
    global _login_shell_path
    with _LOGIN_SHELL_PATH_LOCK:
        if _login_shell_path is not None:
            return list(_login_shell_path)
        dirs: list[str] = []
        shell = os.environ.get("SHELL")
        if platform.system() != "Windows" and shell and os.path.isfile(shell):
            try:
                proc = subprocess.run(
                    [shell, "-l", "-c", "printf '%s\\n' \"$PATH\""],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                if proc.returncode == 0:
                    dirs = [
                        entry for entry in proc.stdout.strip().split(os.pathsep) if entry.strip()
                    ]
            # A login shell runs the user's own startup files and can fail in
            # ways nothing here can predict. This is a best-effort addition to
            # a PATH that already works for most installs, so a shell that
            # misbehaves costs the addition and nothing else.
            except Exception:
                dirs = []
        _login_shell_path = dirs
        return list(dirs)


class SettingsFile(NamedTuple):
    """One file that configures one backend, and how to describe it.

    `scope` and `note` are both spoken. They are not decoration: the two
    project-level Claude Code files differ only in whether the repository
    carries them, and opening the wrong one silently is how somebody's personal
    settings end up committed to a repository that is not theirs.
    """

    backend: str
    scope: str
    path: Path
    note: str

    @property
    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _hermes_home() -> Path:
    """Where Hermes keeps its configuration, honouring its own override.

    `HERMES_HOME` is how one machine runs several Hermes profiles side by side,
    so reading it is the difference between naming the file somebody is actually
    using and naming a default they abandoned.
    """
    # One rule for the whole app. hermes_backend strips and expands the
    # override; resolving it a second way here meant auth.json and config.yaml
    # were read from a different directory than the CLI and the history reader
    # used whenever HERMES_HOME held a tilde.
    from hermes_backend import hermes_home

    return hermes_home()


def settings_files(cwd: Optional[str] = None) -> list[SettingsFile]:
    """Every settings file BlindPilot knows how to point somebody at.

    Listing them creates nothing. These belong to the CLIs, which write their
    own on first run, and a file invented at a path BlindPilot guessed would
    be worse than no file: it would do nothing while looking like it did.

    Locations are the ones each CLI documents. opencode also reads
    `.opencode/opencode.json` inside a project, and Claude Code reads an
    enterprise policy file above all of these that is not an individual's to
    edit, so neither is offered here.
    """
    home = Path.home()
    project = Path(cwd).expanduser() if cwd else None
    entries = [
        SettingsFile(
            BACKEND_CLAUDE,
            "global",
            home / ".claude" / "settings.json",
            "Applies to every project on this machine.",
        ),
        SettingsFile(
            BACKEND_CODEX,
            "global",
            _codex_home() / "config.toml",
            "Applies to every project. TOML rather than JSON.",
        ),
        SettingsFile(
            BACKEND_FREEBUFF,
            "global",
            home / ".config" / "manicode" / "settings.json",
            "Applies to every project. BlindPilot keeps your model choice here.",
        ),
        SettingsFile(
            BACKEND_OPENCODE,
            "global",
            home / ".config" / "opencode" / "opencode.json",
            "Applies to every project.",
        ),
        SettingsFile(
            BACKEND_HERMES,
            "global",
            _hermes_home() / "config.yaml",
            "Applies to every project. YAML rather than JSON, and it belongs to "
            "the Hermes this backend talks to: a Hermes reached over the network "
            "reads the file on that machine, not this one.",
        ),
        SettingsFile(
            BACKEND_MUSE,
            "global",
            home / ".config" / "muse" / "settings.json",
            "Muse's global settings: MCP servers and workspace trust. On Windows "
            "this file lives inside the WSL distribution the CLI runs in, not on "
            "this side.",
        ),
        SettingsFile(
            BACKEND_MUSEAI,
            "global",
            home / ".config" / "muse-cli" / "config.json",
            "muse-cli's own settings: which muse.ai machine it talks to. The "
            "sign-in cookies sit beside it in cookies.txt. muse.ai's own "
            "settings live on its machine and are changed in the muse.ai app.",
        ),
        SettingsFile(
            BACKEND_COMMANDCODE,
            "global",
            home / ".commandcode" / "config.json",
            "Applies to every project. Holds the chosen model, provider, and reasoning effort.",
        ),
        SettingsFile(
            BACKEND_GEMINI,
            "global",
            home / ".gemini" / "settings.json",
            "Applies to every project. Holds Gemini CLI's model, sign-in method, and tool rules.",
        ),
        SettingsFile(
            BACKEND_ANTIGRAVITY,
            "global",
            home / ".gemini" / "antigravity-cli" / "settings.json",
            "Applies to every project. permissions.allow lists the commands agy may run "
            "unasked in a turn that is not bypassing permissions.",
        ),
        SettingsFile(
            BACKEND_COPILOT,
            "global",
            home / ".copilot" / "settings.json",
            "Applies to every project. Holds Copilot CLI's model and its allowed web addresses.",
        ),
        SettingsFile(
            BACKEND_COPILOT,
            "global",
            home / ".copilot" / "mcp-config.json",
            "Applies to every project. The MCP servers Copilot CLI starts.",
        ),
    ]
    if project is not None:
        entries += [
            SettingsFile(
                BACKEND_CLAUDE,
                "this folder",
                project / ".claude" / "settings.json",
                "Shared: committed to this repository, so anyone who has it gets these.",
            ),
            SettingsFile(
                BACKEND_CLAUDE,
                "this folder, personal",
                project / ".claude" / "settings.local.json",
                "Yours alone: normally ignored by git, so it stays on this machine.",
            ),
            SettingsFile(
                BACKEND_OPENCODE,
                "this folder",
                project / "opencode.json",
                "Applies in this folder, and can pin a model or turn providers off.",
            ),
            SettingsFile(
                BACKEND_MUSE,
                "this folder",
                project / "AGENTS.md",
                "Shared: the project rules Muse reads in this folder, written by "
                "`muse init` and committed to this repository.",
            ),
            SettingsFile(
                BACKEND_COMMANDCODE,
                "this folder",
                project / ".commandcode" / "settings.json",
                "Shared: committed to this repository, so anyone who has it gets "
                "these permission rules and default mode.",
            ),
            SettingsFile(
                BACKEND_COMMANDCODE,
                "this folder, personal",
                project / ".commandcode" / "settings.local.json",
                "Yours alone: normally ignored by git, so it stays on this machine.",
            ),
            SettingsFile(
                BACKEND_GEMINI,
                "this folder",
                project / ".gemini" / "settings.json",
                "Shared: committed to this repository, and overrides your own settings here.",
            ),
        ]
    return entries


def subprocess_env(binary: str) -> dict[str, str]:
    """The environment every provider CLI must be started with.

    The CLI's own directory goes first — an npm or Homebrew shim finds its
    sibling `node` that way — and the login shell's PATH is appended behind
    whatever this process already has, so a Node installed somewhere else
    entirely (nvm, Volta, asdf) is still reachable without displacing a
    runtime BlindPilot manages itself.
    """
    env = os.environ.copy()
    if Path(binary).stem.casefold() == "muse-cli":
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        patch = str(root / "museai_cli_patch")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (patch, env.get("PYTHONPATH", ""))))
    # Even --version/--list-models can start Command Code's detached updater.
    # Keep updates in BlindPilot's logged, hidden npm installer, including
    # when a backend launches Command Code as a child of its own.
    env["COMMANDCODE_SKIP_UPDATES"] = "1"
    entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry.strip()]
    known = set(entries)
    directory = os.path.dirname(binary)
    if directory and directory not in known:
        entries.insert(0, directory)
        known.add(directory)
    for entry in login_shell_path_dirs():
        if entry not in known:
            entries.append(entry)
            known.add(entry)
    env["PATH"] = os.pathsep.join(entries)
    if platform.system() == "Windows":
        # Every CLI is started with `cwd` set to the user's project folder, and
        # CLIs shell out constantly - git, node, npm, sh. Windows has
        # historically searched the current directory for those, and the
        # project folder is usually a repository somebody cloned in order to
        # ask an agent about it, not code they wrote. A `git.exe` committed to
        # it should not be what runs when the agent asks for git.
        #
        # This is the documented way off that search path, read by
        # NeedCurrentDirectoryForExePathW, and it is inherited - so it covers
        # the CLI and everything the CLI goes on to start.
        env["NoDefaultCurrentDirectoryInExePath"] = "1"
    return env


def _codex_app_server_binary(binary: str) -> str:
    """Prefer Codex's native executable over an npm wrapper on Windows.

    Terminating an npm ``.cmd`` launcher does not terminate its native child,
    which leaves the app-server writer lock attached to the thread. Launching
    the packaged executable directly makes the process BlindPilot holds the
    app-server itself; its own children are the tree kill's business.
    """
    if platform.system() != "Windows" or Path(binary).suffix.casefold() == ".exe":
        return binary
    # The platform package is an optional dependency of @openai/codex, and
    # npm installs those hoisted: a sibling of the codex package inside the
    # shared @openai folder, not nested under the package itself. A nested
    # copy appears only when npm could not hoist, so look there first (that
    # is also the copy Node's own resolution finds first) and fall back to
    # the hoisted one. Finding neither leaves the .cmd wrapper, which
    # CreateProcess cannot run at all: Popen raises WinError 5.
    package_root = Path(binary).parent / "node_modules" / "@openai" / "codex"
    hoisted_root = Path(binary).parent / "node_modules" / "@openai"
    candidates: list[Path] = []
    for root in (package_root / "node_modules" / "@openai", hoisted_root):
        try:
            candidates = sorted(root.glob("codex-win32-*/vendor/**/bin/codex.exe"))
        except OSError:
            candidates = []
        if candidates:
            break
    return str(candidates[0]) if candidates else binary


def _codex_model_payload(binary: str, cwd: Optional[str], extra: Sequence[str]) -> object:
    """Run one form of `codex debug models` and decode its JSON, or None.

    ``extra`` is what tells the two apart: empty is the refreshing form, and
    ``--bundled`` is the copy compiled into the installed binary. A failure of
    either - offline, a timeout, output that is no longer JSON - is reported as
    None rather than raising, so the caller can try the other.
    """
    try:
        result = subprocess.run(
            [_codex_app_server_binary(binary), "debug", "models", *extra],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=subprocess_env(binary),
            **process_utils.no_window_kwargs(),
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _codex_catalog(payload: object) -> tuple[list[str], list[str]]:
    """(models, efforts) out of a `debug models` payload.

    Only the models Codex's own picker shows are kept. The catalog also carries
    entries marked ``visibility: "hide"`` - models being retired, and internal
    ones like codex-auto-review - and offering one means choosing it, waiting on
    a model the service refuses, and losing the message.
    """
    models: list[str] = []
    efforts: list[str] = []
    entries = payload if isinstance(payload, list) else None
    if entries is None and isinstance(payload, dict):
        entries = payload.get("models", [])
    if not isinstance(entries, list):
        return models, efforts
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("visibility", "list") != "list":
            continue
        model = entry.get("slug") or entry.get("id") or entry.get("model")
        if isinstance(model, str) and model and model not in models:
            models.append(model)
        supported = entry.get("supported_reasoning_levels") or []
        if isinstance(supported, list):
            for level in supported:
                effort = level.get("effort") if isinstance(level, dict) else level
                if isinstance(effort, str) and effort and effort not in efforts:
                    efforts.append(effort)
    return models, efforts


def codex_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """Read Codex's live model catalog and configured defaults.

    `debug models` refreshes the catalog from the service before dumping it, so
    a model the service has retired drops out and a new one appears without
    waiting for the next Codex release. `--bundled` skips that refresh and dumps
    only the copy compiled into the installed binary, which is how a removed
    model used to stay on offer until the CLI itself was updated; it is kept as
    the fallback for a machine that cannot reach the service.
    """
    binary = find_backend_cli(BACKEND_CODEX)
    efforts: list[str] = []
    if not binary:
        return [], efforts, "", "", "Codex was not found."

    stale = False
    models, efforts = _codex_catalog(_codex_model_payload(binary, cwd, ()))
    if not models:
        models, fallback_efforts = _codex_catalog(_codex_model_payload(binary, cwd, ("--bundled",)))
        if models:
            stale = True
            efforts = fallback_efforts or efforts
    if not efforts:
        efforts = ["low", "medium", "high", "xhigh"]

    current_model = ""
    current_effort = ""
    try:
        import tomllib

        with open(_codex_home() / "config.toml", "rb") as handle:
            config = tomllib.load(handle)
        if isinstance(config.get("model"), str):
            current_model = config["model"]
        if isinstance(config.get("model_reasoning_effort"), str):
            current_effort = config["model_reasoning_effort"]
    except (OSError, ValueError):
        pass
    if not models:
        error = "Codex returned an empty model catalog."
    elif stale:
        # Read from the installed release because the service could not be
        # reached, so a model removed upstream may still be listed here.
        error = "Could not refresh Codex's model list; showing the installed release's copy."
    else:
        error = ""
    return models, efforts, current_model, current_effort, error


def blindpilot_config_dir() -> Path:
    """Where BlindPilot keeps its own settings, separate from any backend's."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "BlindPilot"
    if platform.system() == "Darwin":
        # The one folder a Mac user looks in. The chat database already lives
        # here (see accessible_ai.storage.paths), so settings belong beside it
        # rather than in a Linux-style dot folder.
        return Path.home() / "Library" / "Application Support" / "BlindPilot"
    return Path.home() / ".config" / "blindpilot"


def blindpilot_data_dir() -> Path:
    """Per-user, non-roaming storage for managed runtimes and CLI packages."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "BlindPilot"
    if platform.system() == "Darwin":
        # One subtree of Application Support, so the whole app owns one folder
        # on a Mac -- nothing hidden in ~/.local and ~/.config.
        return Path.home() / "Library" / "Application Support" / "BlindPilot" / "data"
    data = os.environ.get("XDG_DATA_HOME")
    return (Path(data) if data else Path.home() / ".local" / "share") / "blindpilot"


def migrate_macos_legacy_dirs() -> None:
    """Move an older macOS install's folders into the Application Support home.

    Before BlindPilot knew the platform's conventions it kept config in
    ``~/.config/blindpilot`` and managed runtimes in ``~/.local/share/
    blindpilot``, the Linux layout. A Mac update must not strand a user's
    settings or their installed CLIs out there, so the first launch after the
    move relocates both -- never overwriting anything already in the new
    home, and never failing the launch when a move does not go through (the
    old code keeps reading the old folder then).

    Called at import time, before any settings are read, so the config file
    is found in its new home from this launch on. Idempotent and a no-op on
    every other platform.
    """
    if platform.system() != "Darwin":
        return
    home = Path.home()
    legacy_config = home / ".config" / "blindpilot"
    legacy_data = home / ".local" / "share" / "blindpilot"
    for source, destination in (
        (legacy_config, blindpilot_config_dir()),
        (legacy_data, blindpilot_data_dir()),
    ):
        _move_dir_contents(source, destination)


def _move_dir_contents(source: Path, destination: Path) -> None:
    """Move the entries of *source* into *destination*, never overwriting.

    Each entry is moved on its own so one failure leaves the rest behind;
    entries that would collide with something already in the new home are
    skipped rather than replacing it -- the new copy wins, and the old one
    stays where it is, readable by any rollback.
    """
    try:
        if not source.is_dir():
            return
        destination.mkdir(parents=True, exist_ok=True)
        for entry in list(source.iterdir()):
            target = destination / entry.name
            if target.exists() or target.is_symlink():
                continue
            try:
                shutil.move(str(entry), str(target))
            except OSError:
                continue
        # Only a source left with nothing in it is a source that finished.
        if not any(source.iterdir()):
            try:
                source.rmdir()
            except OSError:
                pass
    except OSError:
        pass


def _freebuff_choice_path() -> Path:
    return blindpilot_config_dir() / "freebuff-model.json"


def _read_freebuff_choice() -> str:
    """Return the FreeBuff model BlindPilot last selected, if any."""
    try:
        payload = json.loads(_freebuff_choice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    model = payload.get("model") if isinstance(payload, dict) else None
    return model.strip() if isinstance(model, str) else ""


def _write_freebuff_choice(model: str) -> None:
    """Record the selection somewhere only BlindPilot writes.

    FreeBuff resets ``freebuffModel`` in its own settings to whichever model it
    recommends once a turn has run, so that file cannot be read back as the
    user's choice.  This record is what survives.
    """
    path = _freebuff_choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"model": model}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _freebuff_catalog_cache_path() -> Path:
    return blindpilot_config_dir() / "freebuff-catalog.json"


def _read_freebuff_catalog_cache(stamp: tuple[str, float, int]) -> list[str]:
    """The catalog last read out of this exact FreeBuff release, if any.

    Discovering the catalog means scanning a hundred megabytes of compiled
    FreeBuff, which takes seconds.  Doing that once per installed release rather
    than once per run of BlindPilot is the difference between a message that
    sends immediately and one that waits on a file scan first.
    """
    try:
        payload = json.loads(_freebuff_catalog_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("path") != stamp[0] or payload.get("size") != stamp[2]:
        return []
    mtime = _as_number(payload.get("mtime"))
    if mtime is None or abs(mtime - stamp[1]) > 0.001:
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, str) and model]


def _write_freebuff_catalog_cache(stamp: tuple[str, float, int], models: list[str]) -> None:
    path = _freebuff_catalog_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the file and moved into place, the same as the model
        # choice above: a read that lands mid-write must never see a half-copied
        # catalog, which parses as nothing and throws the cache away.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"path": stamp[0], "mtime": stamp[1], "size": stamp[2], "models": models},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        pass


def _freebuff_package_readme(binary: Optional[str]) -> Optional[Path]:
    if not binary:
        return None
    wrapper = Path(binary)
    candidates = [
        wrapper.parent / "node_modules" / "freebuff" / "README.md",
        wrapper.parent.parent / "lib" / "node_modules" / "freebuff" / "README.md",
    ]
    try:
        resolved = wrapper.resolve()
        candidates.extend([resolved.parent / "README.md", resolved.parent.parent / "README.md"])
    except OSError:
        pass
    return next((path for path in candidates if path.is_file()), None)


def _resolve_minified_string(
    source: str,
    expression: str,
    direct: Optional[dict[str, str]] = None,
    seen: Optional[set[str]] = None,
) -> str:
    expression = expression.strip()
    if seen is None:
        seen = set()
    if expression in seen:
        return ""
    seen.add(expression)
    if len(expression) >= 2 and expression[0] == expression[-1] == '"':
        return expression[1:-1]
    if direct is None:
        direct = dict(re.findall(r'(?<![\w$])([A-Za-z_$][\w$]*)="([^"]+)"', source))
    if expression in direct:
        return direct[expression]
    if "." in expression:
        owner, member = expression.split(".", 1)
        marker = f"{owner}={{"
        owner_start = source.find(marker)
        if owner_start >= 0:
            body_start = owner_start + len(marker)
            body = source[body_start : body_start + 3000].split("}", 1)[0]
            value = re.search(rf'(?:^|,){re.escape(member)}:"([^"]+)"', body)
            if value:
                return value.group(1)
    alias = re.search(
        rf"(?<![\w$]){re.escape(expression)}=([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)",
        source,
    )
    if alias and alias.group(1) != expression:
        return _resolve_minified_string(source, alias.group(1), direct, seen)
    return ""


# FreeBuff stamps a release date onto some display names, and revises it in
# place: "DeepSeek V4 Pro" became "DeepSeek V4 Pro 08/13" without the packaged
# README following. Reading the two as different models drops the dated one.
_MODEL_DATE_SUFFIX_RE = re.compile(r"\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?$")


def _readme_offset(readme_text: str, display_name: str) -> int:
    """Where the README documents this model, or -1 if it does not at all."""
    offset = readme_text.find(display_name)
    if offset >= 0:
        return offset
    undated = _MODEL_DATE_SUFFIX_RE.sub("", display_name)
    return readme_text.find(undated) if undated != display_name else -1


def _freebuff_models_from_install(binary: Optional[str]) -> list[str]:
    """Read the current picker catalog from the installed FreeBuff release."""
    executable = (
        Path.home()
        / ".config"
        / "manicode"
        / ("freebuff.exe" if platform.system() == "Windows" else "freebuff")
    )
    readme = _freebuff_package_readme(binary)
    try:
        stat = executable.stat()
        stamp = (str(executable), stat.st_mtime, stat.st_size)
    except OSError:
        return []

    global _freebuff_catalog_cache
    if _freebuff_catalog_cache and _freebuff_catalog_cache[0] == stamp:
        return list(_freebuff_catalog_cache[1])
    remembered = _read_freebuff_catalog_cache(stamp)
    if remembered:
        _freebuff_catalog_cache = (stamp, list(remembered))
        return list(remembered)

    try:
        source = executable.read_bytes().decode("latin-1", errors="ignore")
        readme_text = readme.read_text(encoding="utf-8") if readme else ""
    except OSError:
        return []

    pattern = re.compile(
        r'([A-Za-z_$][\w$]*)=\{id:([^,}]+),displayName:"([^"]+)"'
        r'[^{}]{0,1000}?availability:"(?:always|off_peak_only)"'
    )
    matches = list(pattern.finditer(source))
    if not matches:
        return []
    start = max(0, min(match.start() for match in matches) - 50_000)
    end = min(len(source), max(match.end() for match in matches) + 10_000)
    catalog_source = source[start:end]
    direct = dict(re.findall(r'(?<![\w$])([A-Za-z_$][\w$]*)="([^"]+)"', catalog_source))
    discovered: list[tuple[int, str]] = []
    for match in matches:
        display_name = match.group(3)
        # The packaged README documents the user-facing regular/earned models.
        # If it is absent, the catalog-shaped object is still better than a
        # stale list compiled into BlindPilot.
        order = _readme_offset(readme_text, display_name) if readme_text else -1
        if readme_text and order < 0:
            continue
        model_id = _resolve_minified_string(source, match.group(2), direct)
        if not model_id or "/" not in model_id:
            continue
        discovered.append((order if order >= 0 else match.start(), model_id))

    models: list[str] = []
    for _order, model_id in sorted(discovered):
        if model_id not in models:
            models.append(model_id)
    if FREEBUFF_PREFERRED_MODEL in models:
        models.remove(FREEBUFF_PREFERRED_MODEL)
        models.insert(0, FREEBUFF_PREFERRED_MODEL)
    _freebuff_catalog_cache = (stamp, list(models))
    _write_freebuff_catalog_cache(stamp, list(models))
    return models


def freebuff_model_options() -> tuple[list[str], str, str]:
    """Return the model choices exposed by the installed FreeBuff release."""
    binary = find_backend_cli(BACKEND_FREEBUFF)
    models = _freebuff_models_from_install(binary)
    chosen = _read_freebuff_choice()
    saved = ""
    try:
        settings = Path.home() / ".config" / "manicode" / "settings.json"
        payload = json.loads(settings.read_text(encoding="utf-8"))
        selected = payload.get("freebuffModel") if isinstance(payload, dict) else None
        if isinstance(selected, str) and selected.strip():
            saved = selected.strip()
    except (OSError, ValueError):
        pass
    if not models:
        models = [FREEBUFF_PREFERRED_MODEL]
        error = "Could not refresh FreeBuff's model catalog; showing the preferred default."
    else:
        error = ""
    # A remembered choice is only worth offering while the catalog is unknown.
    # When it was read successfully, a model missing from it is one this
    # release has dropped, and listing it means picking it, waiting on a picker
    # that will never show it, and losing the message.
    if error:
        for candidate in (chosen, saved):
            if candidate and candidate not in models:
                models.append(candidate)
    # BlindPilot's own record wins over FreeBuff's settings file. FreeBuff
    # rewrites that file to its recommended model after a turn, so honouring it
    # would quietly downgrade every following turn to the recommendation.
    current = (
        chosen
        if chosen in models
        else FREEBUFF_PREFERRED_MODEL
        if FREEBUFF_PREFERRED_MODEL in models
        else saved
        if saved in models
        else models[0]
    )
    return models, current, error


def _freebuff_display_key(text: str) -> str:
    """Letters and digits only, with a version letter before a number dropped.

    The picker paints a display name, never the model id, and the two disagree
    in ways too small to be worth a table and too varied to keep up with:
    ``mimo/mimo-v2.5`` is drawn "MiMo 2.5" and ``deepseek/deepseek-v4-flash``
    is drawn "DeepSeek V4 Flash", so the ``v`` is dropped by one side or the
    other depending on the model. Reducing both sides the same way makes the
    id a substring of the row it belongs to whichever of them carries it.
    """
    return re.sub(r"v(?=\d)", "", re.sub(r"[^a-z0-9]+", "", text.casefold()))


def _freebuff_picker_options(visible: str, models: list[str]) -> tuple[list[str], int]:
    """Return the model IDs painted by FreeBuff's picker and its focused index.

    The index matters as much as the list: the caller moves to a model by
    pressing Down the difference between two positions in it, so a row that is
    on screen and not in this list does not cost that model, it costs every
    model below it. Matching therefore has to recognise every row the picker
    can move to, or recognise none of them.

    FreeBuff 0.0.168 stopped drawing the focus marker altogether: the welcome
    screen opens with the highlight on the recommended card and no ``›``
    anywhere, so a reader that insists on the marker finds no focused row and
    the message is lost. When no row is marked, the content row of the first
    painted card is taken as the focused one — the highlight starts on the
    first card on both the welcome screen and the expanded list. A model card
    opens with a ``┌─┐`` top and its name on the row below, which the
    advertising boxes below the cards do not share; the position is only
    claimed when that first row was itself matched, so a catalog that has
    lost the very first model still reports no focus rather than a wrong one.
    Releases that draw the marker never reach this.
    """
    # Longest first, so a model whose id is a prefix of another's cannot claim
    # the longer one's row.
    keyed = sorted(
        ((model, _freebuff_display_key(model.rsplit("/", 1)[-1])) for model in models),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )
    options: list[str] = []
    focused = -1
    for raw in visible.splitlines():
        if "│" not in raw:
            continue
        row = _freebuff_display_key(raw)
        matched = next((model for model, key in keyed if key and key in row), "")
        if not matched or matched in options:
            continue
        options.append(matched)
        if "›" in raw or re.search(r"(?:^|│)\s*>\s*", raw):
            focused = len(options) - 1
    if focused < 0 and options:
        lines = [line.strip() for line in visible.splitlines()]
        first_top = next(
            (index for index, line in enumerate(lines) if re.fullmatch(r"┌─+┐", line)),
            -1,
        )
        if first_top >= 0 and first_top + 1 < len(lines):
            row = _freebuff_display_key(lines[first_top + 1])
            matched = next((model for model, key in keyed if key and key in row), "")
            if matched and matched in options:
                focused = 0
    return options, focused


def invalidate_backend_cache(backend: str) -> None:
    """Drop version-derived provider data before an explicit runtime refresh."""
    global _freebuff_catalog_cache, _login_shell_path
    # The wizard may have just put a backend on PATH. The answer cached before
    # that happened is precisely the one that made putting it there necessary.
    _login_shell_path = None
    if normalize_backend(backend) == BACKEND_OPENCODE:
        with _OPENCODE_CATALOG_LOCK:
            _opencode_catalog_cache.clear()
    if normalize_backend(backend) == BACKEND_FREEBUFF:
        _freebuff_catalog_cache = None
        try:
            _freebuff_catalog_cache_path().unlink()
        except OSError:
            pass


def set_freebuff_model(model: str) -> None:
    """Select the model in FreeBuff, and record the choice as BlindPilot's own."""
    selected = model.strip()
    if not selected:
        models, current, _error = freebuff_model_options()
        selected = current or (models[0] if models else FREEBUFF_PREFERRED_MODEL)
    settings = Path.home() / ".config" / "manicode" / "settings.json"
    with _FREEBUFF_SETTINGS_LOCK:
        try:
            payload = json.loads(settings.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = {}
        except ValueError as exc:
            raise OSError(f"Could not read FreeBuff settings: {exc}") from exc
        if not isinstance(payload, dict):
            raise OSError("FreeBuff settings must contain a JSON object")
        _write_freebuff_choice(selected)
        if payload.get("freebuffModel") == selected:
            return
        payload["freebuffModel"] = selected
        settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings.with_suffix(".json.blindpilot.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, settings)


# Codex ships `request_user_input` — the tool it stops a turn to ask a
# multiple-choice question with — switched off, and available only in plan mode
# even when switched on. Both are settings rather than protocol, so they are
# passed for this app server alone and nothing is written to the user's
# ~/.codex/config.toml.
_CODEX_QUESTION_FEATURE = "default_mode_request_user_input"
_CODEX_QUESTION_ARGS = (
    "-c",
    "tools.experimental_request_user_input={enabled=true}",
    "--enable",
    _CODEX_QUESTION_FEATURE,
)


# Why a question has to be asked with the tool rather than written out, for
# whichever backend's tool is named. Switching the tool on is not the same as
# being asked through it: a model that writes its question into its answer
# makes no tool call, so nothing announces it and no dialog opens, the turn
# ends, and the person is left waiting on an answer nobody told them was
# wanted. A skill that runs an interview -- "grill me" is the one this was
# found through -- asks in prose as a matter of course, which is why the
# reason is spelled out rather than left as a preference: an instruction that
# only says "prefer this tool" loses to a skill that says to interview in
# prose.
def ask_through_the_tool(tool: str) -> str:
    """BlindPilot's instruction to ask with `tool` instead of asking in prose."""
    return (
        "You are running inside BlindPilot, which a blind person is driving with a "
        "screen reader. When you want an answer from them, ask with the "
        f"{tool} tool rather than writing the question into your reply. "
        "BlindPilot speaks a tool question and opens it in a dialog; a question "
        "written into a reply is neither spoken as a question nor shown as one, so "
        "the turn ends and nothing tells them an answer is wanted. This holds for "
        "every question, including an interview that asks them one at a time."
    )


def _codex_developer_instructions() -> str:
    """BlindPilot's instruction, behind whatever the person wrote themselves.

    ``-c`` replaces a key rather than adding to it, so setting this key blind
    would cost somebody their own ``developer_instructions`` for as long as
    BlindPilot was running. Theirs is read first and kept in front, which is
    both the order they wrote it in and the order that leaves BlindPilot's
    addition reading as the footnote it is.
    """
    theirs = ""
    try:
        import tomllib

        with open(_codex_home() / "config.toml", "rb") as handle:
            theirs = str(tomllib.load(handle).get("developer_instructions") or "")
    except (OSError, ValueError):
        # No config file, or one Codex itself would refuse to load and report
        # on. Either way there is nothing here of theirs to lose.
        theirs = ""
    return f"{theirs.strip()}\n\n{ask_through_the_tool('request_user_input')}".strip()


def codex_question_args() -> tuple[str, ...]:
    """The app-server arguments that make a question reach the dialog.

    Built per launch rather than held as a constant, because the instruction
    half has to read the user's config first. Quoted as a TOML string with
    ``json.dumps``: ``-c`` parses its value as TOML and only falls back to a
    literal when that fails, and a sentence with a quote or a newline in it
    should not be relying on the fallback. ``ensure_ascii`` is off because
    JSON writes a character outside the BMP -- an emoji in somebody's own
    instructions -- as an escaped surrogate pair, which TOML refuses,
    and the refusal would send the whole quoted value through as a literal.
    """
    value = json.dumps(_codex_developer_instructions(), ensure_ascii=False)
    return (*_CODEX_QUESTION_ARGS, "-c", f"developer_instructions={value}")


# ----- Codex on a ChatGPT-plan account -----
#
# Codex is pointed at the plan's Responses endpoint through configuration
# overrides on its own command line, and fetches its own bearer token by
# running this app's token-printing mode on an interval. The token is
# therefore in neither the child's command line nor its environment. The
# endpoint itself is reached through a loopback relay (accessible_ai.providers.
# plan_relay) because the plan token is a subscription-sharing token, and that
# endpoint refuses several item types Codex emits; the relay drops them.

PLAN_PROVIDER_ID = "blindpilot-chatgpt"

# How often Codex runs the token command again. Each run is its own process with
# no token in memory, so each one performs an exchange and rotates the refresh
# token; ten minutes is how often that is worth paying, and a run that fails --
# another process holding the store, a machine that was asleep -- is retried long
# before the token under it expires.
PLAN_TOKEN_REFRESH_MS = 600_000
# Longer than the command's own bounded wait for another process's refresh (two
# 30-second exchanges plus 30), so a refresh that waited is not reported as a
# failing token command.
PLAN_TOKEN_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class PlanAccount:
    """The ChatGPT-plan account a Codex turn runs on.

    The id is what the token command takes; the name is how a failure is
    reported, because an id on its own does not say which account it was.
    """

    account_id: int
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or f"account {self.account_id}"


class PlanTokenError(OSError):
    """The bound Account's token command failed, so no turn can run on it.

    An OSError like every other reason a launch does not happen, so a caller
    that only knows "Codex could not start" still catches it. The distinct type
    is what says the failure names the Account rather than the CLI, which is
    what lets the window stop retrying an Account that has gone.
    """


class _Unbound:
    """No Account taken for this conversation yet: read the saved choice.

    ``None`` is a real answer -- Codex's own login -- so "nothing chosen yet"
    cannot be spelled as ``None`` as well. A tab tells a turn which Account its
    conversation is bound to; one that is not bound yet is told this, and the
    turn reads the saved choice once and keeps it for the rest of the turn.
    """

    def __repr__(self) -> str:
        return "UNBOUND"


UNBOUND = _Unbound()


# ----- Which Account the Codex backend uses -----
#
# Chosen once for the backend, saved beside the window's own preferences, and
# inherited by new conversations: nothing here is per conversation. The value is
# either a specific ChatGPT-plan Account or nothing at all, and nothing at all
# means Codex's own login -- the behaviour every existing setup has, so an
# install that has chosen nothing runs exactly as it did.
#
# A conversation that has begun keeps the Account it began on, so this is read
# once per conversation rather than once per turn: a switch affects the next
# conversation, not the thread somebody is in the middle of. A tab hands its
# bound Account to each of its turns (see `CodexWorker`), and only a turn told
# `UNBOUND` reads this at all.
#
# Read on the thread about to launch a process, never on the window's: it is a
# small file, but it is a file. Kept in this module rather than with the other
# preferences because the Codex launch is what needs it, and the window already
# imports this module rather than the other way round.

# The key the choice is stored under in the settings file. Named for the
# backend, because it is that backend's choice and not a global one.
CODEX_PLAN_ACCOUNT = "codex_plan_account"


def _settings_file() -> Path:
    """The settings file the window keeps its preferences in."""
    return blindpilot_config_dir() / "config.json"


def codex_plan_account() -> Optional[PlanAccount]:
    """The Account the Codex backend is set to use, or None for its own login."""
    saved = preferences.load_config((_settings_file(),)).get(CODEX_PLAN_ACCOUNT)
    if not isinstance(saved, dict):
        return None
    try:
        account_id = int(saved.get("account_id", 0))
    except (TypeError, ValueError):
        # Written by hand, or by a release that meant something else by it.
        # Codex's own login is the answer that always works.
        return None
    if account_id <= 0:
        return None
    return PlanAccount(account_id=account_id, name=str(saved.get("name") or ""))


def save_codex_plan_account(account: Optional[PlanAccount]) -> bool:
    """Remember which Account the Codex backend uses. False if it was not saved.

    Only this key changes: the same file holds the window's preferences, and
    the write is `preferences.update_config`, which reads and writes the file
    under one lock -- the same save path the window uses -- so a choice saved
    here cannot put back a preference saved a moment earlier, and a
    preference saved by the window cannot cost somebody their choice.
    """
    value = (
        None if account is None else {"account_id": int(account.account_id), "name": account.name}
    )
    return preferences.update_config(_settings_file(), {CODEX_PLAN_ACCOUNT: value})


def codex_pool_key(account: Optional[PlanAccount] = None) -> tuple:
    """The pool key for the Codex backend, on this Account if one is chosen.

    One place decides what an Account means to the pool, so the turn that takes
    a process, the drop that gives it up and the usage report that reads it can
    never disagree about which Account's process they are talking about.
    """
    return backend_pool.pool_key(
        BACKEND_CODEX, account=None if account is None else account.account_id
    )


class AccountDrop(NamedTuple):
    """What letting go of an Account's Held process did, in terms the caller says.

    ``waited``: a turn was speaking through the process, so the drop waits for
    that turn to finish and somebody has to be told so. ``choice_cleared``: the
    Codex backend was set to this Account, and is now back on Codex's own login.
    """

    waited: bool = False
    choice_cleared: bool = False


def drop_codex_plan_account(account_id: int) -> bool:
    """Drop what Codex holds for this Account, now or once its turn finishes.

    Signing out of an Account, deleting it, or choosing another one takes its
    Held process away: nothing may be sent on a credential that has just been
    ended, and nothing on a process whose Account is no longer chosen. A turn
    already running through it is never interrupted -- it finishes on the token
    it started with, which is what switching promises -- so the drop waits for
    it and returns True, which is the caller's cue to say the wait out loud.

    The start lock is the turn's own, so a turn between being handed this
    process and being counted in it cannot be dropped underneath. It is never
    waited for here: a turn holds it across a launch, which runs the token
    command and starts a process, and this is called from the window's thread.
    Not getting it means a turn is launching right now, which is reason enough
    to owe the drop and say it is waiting.
    """
    key = codex_pool_key(PlanAccount(account_id=account_id))
    pool = backend_pool.pool()
    if not _CODEX_START_LOCK.acquire(blocking=False):
        pool.wait_to_drop(key)
        return True
    try:
        return pool.drop_when_idle(key)
    finally:
        _CODEX_START_LOCK.release()


def drop_pending_codex_processes() -> None:
    """Carry out the drops owed by finished turns, now that it is safe to.

    Called as a turn hands its process back, which is where a wait owed to that
    turn is paid. The start lock is taken without waiting, because a turn holds
    it from taking a process to being counted in it, and a drop landing in that
    gap would stop the very process the next turn has just been handed. Not
    getting it means a turn is being handed one right now: the keys stay owed,
    and the next turn to finish asks again.

    A choice of Account that was waiting for a turn is applied here too, for
    the same reason and at the same moment: the turn it was waiting for has
    just finished.
    """
    if _CODEX_START_LOCK.acquire(blocking=False):
        try:
            backend_pool.pool().drop_pending()
        finally:
            _CODEX_START_LOCK.release()
    _apply_pending_plan_choice()


# ----- Choosing another Account while a turn is running -----
#
# A turn already running finishes on the Account it started with, so the switch
# cannot take effect while it runs: the choice is made, said, and kept here
# until the turn that blocked it hands its process back (see
# `drop_pending_codex_processes`). Until then the saved choice -- and so the
# Account every new conversation is bound to -- is still the one being left.

_PENDING_CHOICE_LOCK = threading.Lock()
_pending_choice: Optional[PlanAccount] = None
_pending_choice_owed = False


class AccountSwitch(NamedTuple):
    """What choosing another Account for Codex did, in terms the caller says.

    ``saved``: the choice in the settings file is now the one asked for.
    ``waited``: a turn was running on the Account being left, so it is not --
    the switch happens when that turn finishes, and somebody has to be told so.
    """

    saved: bool = False
    waited: bool = False


class PendingSwitch(NamedTuple):
    """A switch waiting for a turn, if there is one, and the Account it names."""

    owed: bool = False
    account: Optional[PlanAccount] = None


def switch_codex_plan_account(account: Optional[PlanAccount]) -> AccountSwitch:
    """Choose the Account the Codex backend uses, now or once its turn finishes.

    Nothing is running: the choice is saved and the Account being left has its
    process dropped, so the next conversation starts on the new Account.

    A turn is running on the Account being left: it finishes on that Account,
    which is what switching promises, so the choice is kept rather than saved
    and takes effect when the turn hands its process back. The caller is told
    it is waiting so it can say so; a switch that silently waits is one nobody
    knows to wait for.

    The start lock is the turn's own, so this answers without queueing behind a
    turn that is launching a process (see `drop_codex_plan_account`).
    """
    global _pending_choice, _pending_choice_owed

    previous = codex_plan_account()
    same = (
        account is not None and previous is not None and account.account_id == previous.account_id
    )
    if previous is None or same:
        # Nothing is being left -- Codex's own login either way, or the same
        # Account under a new name -- so there is no process to wait for.
        return AccountSwitch(save_codex_plan_account(account), waited=False)
    if not drop_codex_plan_account(previous.account_id):
        return AccountSwitch(save_codex_plan_account(account), waited=False)
    with _PENDING_CHOICE_LOCK:
        _pending_choice = account
        _pending_choice_owed = True
    return AccountSwitch(saved=False, waited=True)


def pending_codex_plan_account() -> PendingSwitch:
    """The switch waiting for a turn to finish, if one is waiting."""
    with _PENDING_CHOICE_LOCK:
        return PendingSwitch(_pending_choice_owed, _pending_choice)


def _forget_pending_plan_choice(account_id: int) -> None:
    """Drop a switch that names an Account which is no longer usable."""
    global _pending_choice, _pending_choice_owed

    with _PENDING_CHOICE_LOCK:
        account = _pending_choice
        if account is not None and account.account_id == int(account_id):
            _pending_choice = None
            _pending_choice_owed = False


def _apply_pending_plan_choice() -> None:
    """Save the choice that was waiting for a turn, if that turn has finished.

    Asked where a turn hands its process back. A process another tab is still
    speaking through is left where it is, and the choice stays owed: the wait
    is for all of the turns running on the Account being left, not for
    whichever of them finishes first. A write the file refuses also leaves it
    owed, so the next turn to finish asks again rather than the choice being
    lost in silence.
    """
    global _pending_choice, _pending_choice_owed

    with _PENDING_CHOICE_LOCK:
        if not _pending_choice_owed:
            return
        choice = _pending_choice
    previous = codex_plan_account()
    if previous is not None and backend_pool.pool().busy(codex_pool_key(previous)):
        return
    if not save_codex_plan_account(choice):
        return
    with _PENDING_CHOICE_LOCK:
        _pending_choice = None
        _pending_choice_owed = False


def forget_codex_plan_account(account_id: int) -> AccountDrop:
    """Stop using an Account for Codex, and drop the process it was running on.

    For the three ways an Account stops being usable -- signed out, deleted, or
    switched away from. If it is the Account the backend is set to use, the
    choice goes back to Codex's own login: a signed-out Account cannot pay for
    a turn, and a saved id that nothing can run on is a choice that lies about
    what Codex will use. Everything stays stored but the choice itself, so the
    control that offers it and the file it is saved in agree.

    The drop is the one `drop_codex_plan_account` makes, with the same wait. A
    switch still waiting for a turn to finish is dropped if it names this
    Account, because an Account nobody can run on must not be saved later.
    """
    chosen = codex_plan_account()
    cleared = chosen is not None and chosen.account_id == int(account_id)
    if cleared:
        save_codex_plan_account(None)
    _forget_pending_plan_choice(int(account_id))
    return AccountDrop(drop_codex_plan_account(int(account_id)), cleared)


def plan_token_command(account_id: int) -> tuple[str, ...]:
    """The command that prints one access token for an account and exits.

    Codex runs it itself, from whatever folder it was started in. A frozen
    build's ``sys.executable`` is BlindPilot, whose entry point answers the flag
    before importing the window; from source it is a Python, so it is given the
    entry point by absolute path rather than relying on a working directory.
    """
    tail = (plan_token.FLAG, str(int(account_id)))
    if getattr(sys, "frozen", False):
        return (sys.executable, *tail)
    entry = Path(__file__).resolve().with_name("blind_pilot.py")
    return (sys.executable, str(entry), *tail)


def codex_plan_args(account_id: int) -> tuple[str, ...]:
    """Arguments that put Codex on the plan's Responses endpoint through the relay.

    The token is not among them: Codex runs the token command beside them to
    obtain its own bearer token, and runs it again on an interval. Quoted as
    TOML strings because ``-c`` parses its value as TOML; ``ensure_ascii`` is
    off for the paths, since JSON would write a character outside the BMP as an
    escaped surrogate pair, which TOML refuses.
    """
    provider = f"model_providers.{PLAN_PROVIDER_ID}"
    command = plan_token_command(account_id)
    overrides = (
        ("model_provider", json.dumps(PLAN_PROVIDER_ID)),
        (f"{provider}.name", json.dumps("OpenAI")),
        (
            f"{provider}.base_url",
            json.dumps(plan_relay.plan_base_url(ssl_context=certificates.certificate_context())),
        ),
        (f"{provider}.wire_api", json.dumps("responses")),
        (f"{provider}.supports_websockets", "false"),
        # requires_openai_auth is deliberately absent: Codex's reference says
        # not to combine it with auth, and its default is already false.
        (f"{provider}.auth.command", json.dumps(command[0], ensure_ascii=False)),
        (f"{provider}.auth.args", json.dumps(list(command[1:]), ensure_ascii=False)),
        (f"{provider}.auth.refresh_interval_ms", str(PLAN_TOKEN_REFRESH_MS)),
        (f"{provider}.auth.timeout_ms", str(PLAN_TOKEN_TIMEOUT_MS)),
    )
    args: list[str] = []
    for key, value in overrides:
        args += ["-c", f"{key}={value}"]
    return tuple(args)


def _codex_questions(raw: object) -> tuple[questions.Question, ...]:
    """Read request_user_input's params into BlindPilot's own question shape.

    `isOther` is Codex asking for the "Other" answer its tool description
    tells the model not to write itself.
    """
    return questions._questions(raw, custom_key="isOther", secret_key="isSecret", id_key="id")


# How long a failing Codex turn waits for the last line of stderr, which is
# the one that says why. Long enough for a line already written to arrive,
# short enough that a pipe which never closes cannot hold the turn open.
_CODEX_LAST_WORDS_SECONDS = 1.0

# How long an interrupt is given to be confirmed before the turn's thread is
# treated as wedged. Half of the window's whole teardown budget
# (_CANCEL_JOIN_SECONDS, 3.0), leaving the rest for the join that follows.
_CODEX_INTERRUPT_VERIFY_SECONDS = 1.5

# How long a cancel waits to be told the id of a turn it has already asked for.
# Stop can land in the gap between `turn/start` and the reply that names the
# turn, and a turn with no name cannot be interrupted -- so the last thing a
# stopped turn does is look for that reply. Small: the reply is either already
# on its way or it is not coming, and this is spent before the verify wait, so
# the two together still fit inside the window's teardown budget.
_CODEX_TURN_ID_GRACE_SECONDS = 0.25

# How long a turn waits on its inbox before looking at its own cancelled flag.
# Short enough that Stop is answered promptly, long enough that a quiet turn
# is not a spin.
_CODEX_POLL_SECONDS = 0.1

# How much of the app-server's stderr is kept. The process now outlives
# thousands of turns, and only the tail of it is ever read; an uncapped list
# is a leak on the branch whose whole point is not leaving things behind.
_CODEX_STDERR_LINES = 200

# How many conversations may hold a note about a turn given up on them. A
# thread nobody ever resumes never collects its note, so the notes are capped
# rather than trusted to be read.
_CODEX_ABANDONED_THREADS = 64


class _Closed:
    """What the reader puts in every inbox when the app-server's stdout ends.

    A turn blocked on its queue has to be woken by something; `None` is a
    value the protocol could plausibly carry, so this is a type of its own.
    """


_CODEX_CLOSED = _Closed()


class _Asked:
    """The mark a turn puts in its own inbox when it asks for a turn.

    Everything already queued behind it was said before this turn asked for
    anything, so none of it can be about this turn. Everything after it may
    be. The inbox is first in, first out and both threads write to it in
    order, so the mark separates the two exactly -- which a flag set at send
    time does not, because by then the earlier messages are already queued and
    are read after it.
    """


_CODEX_ASKED = _Asked()


class _TurnWatch:
    """One turn's completion, and how many callers are waiting to hear it."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.watchers = 0


# Only one Codex app-server is ever wanted, so only one turn at a time may go
# looking for one. Without this, two tabs sending their first Codex message
# together both find the pool empty, both launch a server, and the second one
# handed back displaces -- and stops -- the first, killing a live turn.
_CODEX_START_LOCK = threading.Lock()


def _unhandled_request(request_id: object) -> dict:
    return {
        "id": request_id,
        "error": {"code": -32601, "message": "BlindPilot cannot handle this request yet"},
    }


def _declined_request(message: dict) -> dict:
    """The reply that says no to a server request nobody is going to answer.

    Codex holds an unanswered request id open, so silence is not the same as
    refusal. Declining is: the turn it belongs to was interrupted, and the
    person is not going to be shown a question from it.
    """
    method = str(message.get("method") or "")
    request_id = message.get("id")
    if method == "item/tool/requestUserInput":
        questions = _codex_questions((message.get("params") or {}).get("questions"))
        answers: dict[str, dict] = {
            question.id or question.question: {"answers": []} for question in questions
        }
        return {"id": request_id, "result": {"answers": answers}}
    if method.endswith("requestApproval"):
        return {"id": request_id, "result": {"decision": "decline"}}
    return _unhandled_request(request_id)


class CodexServer:
    """One ``codex app-server`` process, shared by every tab using Codex.

    The app-server multiplexes: several threads live in one process, keyed by
    threadId, which is what lets one server serve every tab instead of one per
    tab. Replies are routed back to whichever turn asked, by request id.
    """

    def __init__(self, proc: object) -> None:
        self.proc = proc
        # Two locks, never held together. `_write_lock` guards only the
        # stdin write+flush pair, so a wedged app-server blocking on flush
        # cannot also block every other tab's `next_id()`. `_state_lock`
        # guards only in-process bookkeeping, the id counter and the routing
        # tables. A turn registers its reply queue before sending, and taking
        # the lock `send` uses for I/O there would self-deadlock.
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 10
        # request id -> the queue the waiting turn is reading
        self._waiting: dict[int, "queue.Queue[object]"] = {}
        # Of those, the requests whose reply names a thread to subscribe.
        self._thread_replies: set[int] = set()
        # threadId -> the queue that conversation's turn is reading. Only ever
        # holds conversations somebody is reading right now: a message for a
        # thread with no entry belongs to a turn that has ended, and creating
        # an entry for it would both leak and replay it into the next turn.
        self._threads: dict[str, "queue.Queue[object]"] = {}
        # turnId -> the completion the reader reports, and its watchers.
        self._turns: dict[str, _TurnWatch] = {}
        # threadId -> the turns given up on it that may still be generating.
        # Written when a cancel could not be confirmed, read by the next turn
        # to resume that conversation, and cleared as it is read.
        self._abandoned: dict[str, set[str]] = {}
        # Bounded, because this list now belongs to a process that outlives
        # thousands of turns rather than dying with one of them.
        self._stderr: "collections.deque[str]" = collections.deque(maxlen=_CODEX_STDERR_LINES)
        # How many lines have ever been written, which is what lets a turn
        # read only its own: the deque forgets, so a position in it is not a
        # position in the stream.
        self._stderr_written = 0
        self._readers: list[threading.Thread] = []
        self._stderr_reader: Optional[threading.Thread] = None
        self._closed = False
        self._read_error = ""
        # How many turns are speaking through this process right now.
        self._borrowers = 0

    # ----- lifetime -----

    def start_readers(self) -> None:
        """Read this process's stdout and stderr for as long as it lives.

        One thread per pipe per *process*, not per turn: the app-server
        multiplexes, so a turn that read stdout itself would swallow every
        other tab's notifications.
        """
        if getattr(self.proc, "stdout", None) is not None:
            reader = threading.Thread(target=self._read_stdout, name="codex-stdout", daemon=True)
            self._readers.append(reader)
            reader.start()
        else:
            self._close("")
        if getattr(self.proc, "stderr", None) is not None:
            stderr_reader = threading.Thread(
                target=self._read_stderr, name="codex-stderr", daemon=True
            )
            self._stderr_reader = stderr_reader
            self._readers.append(stderr_reader)
            stderr_reader.start()

    def alive(self) -> bool:
        poll = getattr(self.proc, "poll", None)
        return poll is not None and poll() is None

    def next_id(self) -> int:
        with self._state_lock:
            self._next_id += 1
            return self._next_id

    def send(self, message: dict) -> bool:
        stdin = getattr(self.proc, "stdin", None)
        if stdin is None:
            return False
        try:
            data = json.dumps(message, ensure_ascii=False) + "\n"
            with self._write_lock:
                stdin.write(data)
                stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    # ----- routing -----

    def expect(
        self, request_id: int, listener: "queue.Queue[object]", *, binds_thread: bool = False
    ) -> None:
        """Send this request's reply to that queue. Register before sending.

        `binds_thread` also subscribes the conversation the reply names.
        `thread/start` and `thread/resume` both answer with the conversation's
        id (both `required: ["thread", ...]` in the app server's own schema),
        and the reader binds that conversation to this queue *before* it hands
        the reply on. Everything arrives on one stdout read by one thread, so
        any message routed after that reply finds the binding already in place,
        and nothing has to be buffered for a thread nobody is reading yet.

        Messages routed *before* the reply are a different matter: a threadId
        nothing is bound to yet is dropped. In practice the only notification
        Codex sends between creating a thread and answering for it is
        `mcpServer/startupStatus/updated`, which this worker ignores.
        """
        with self._state_lock:
            closed = self._closed
            if not closed:
                self._waiting[request_id] = listener
                if binds_thread:
                    self._thread_replies.add(request_id)
        if closed:
            listener.put_nowait(_CODEX_CLOSED)

    def unexpect(self, request_ids: Sequence[int]) -> None:
        with self._state_lock:
            for request_id in request_ids:
                self._waiting.pop(request_id, None)
                self._thread_replies.discard(request_id)

    def attach(self, thread_id: str, listener: "queue.Queue[object]") -> None:
        """Read this conversation's notifications from that queue.

        Ordinarily `expect(..., binds_thread=True)` has already done this from
        the reply. This is the belt and braces for a turn that had to fall
        back to the session id it was given, and it is idempotent.
        """
        if not thread_id:
            return
        with self._state_lock:
            closed = self._closed
            if not closed:
                self._threads[thread_id] = listener
        if closed:
            listener.put_nowait(_CODEX_CLOSED)

    def detach_listener(self, listener: "queue.Queue[object]") -> None:
        """Stop reading every conversation bound to this queue.

        By identity rather than by id, because the id is exactly what a turn
        may not have. The reader binds the conversation the moment the reply
        names it; a turn cancelled between that routing and its own reading of
        the reply never learns the id, and detaching by an id it does not have
        would leave the binding there for the life of the process.
        """
        with self._state_lock:
            for thread_id in [k for k, v in self._threads.items() if v is listener]:
                del self._threads[thread_id]

    def borrow(self) -> None:
        """One more turn is speaking through this process."""
        with self._state_lock:
            self._borrowers += 1

    def give_back(self) -> None:
        with self._state_lock:
            self._borrowers = max(0, self._borrowers - 1)

    def borrower_count(self) -> int:
        """How many turns hold this process right now.

        Not the same as how many conversations are bound: a turn that has sent
        `thread/start` and is waiting for the reply holds the process without
        being bound to anything, and is exactly the turn that must not have it
        stopped underneath it.
        """
        with self._state_lock:
            return self._borrowers

    def watch_turn(self, turn_id: str) -> threading.Event:
        """The event the reader sets when this turn completes.

        Counted, because two watchers are ordinary: the turn's own thread
        registers when the turn starts, and a `cancel` on the window's thread
        registers again when it interrupts. The event goes when the last of
        them lets go, so a process that outlives thousands of turns does not
        accumulate one of these for each.
        """
        with self._state_lock:
            watch = self._turns.get(turn_id)
            if watch is None:
                watch = _TurnWatch()
                self._turns[turn_id] = watch
            watch.watchers += 1
            if self._closed:
                watch.done.set()
            return watch.done

    def forget_turn(self, turn_id: str) -> None:
        with self._state_lock:
            watch = self._turns.get(turn_id)
            if watch is None:
                return
            watch.watchers -= 1
            if watch.watchers <= 0:
                del self._turns[turn_id]

    def read_error(self) -> str:
        """Why the reader stopped, if it stopped on an error rather than EOF."""
        with self._state_lock:
            return self._read_error

    def stderr_mark(self) -> int:
        """Where a turn's own stderr begins, to be read back with `stderr_since`."""
        with self._state_lock:
            return self._stderr_written

    def stderr_since(self, mark: int) -> list[str]:
        """Only the lines written after that mark.

        A held process explains itself for every turn it ever runs. Reporting
        a death at turn fifty with a warning from turn three is a wrong reason
        spoken aloud to somebody who cannot scroll back and check.
        """
        with self._state_lock:
            fresh = self._stderr_written - mark
            if fresh <= 0:
                return []
            lines = list(self._stderr)
        return lines[-fresh:] if fresh < len(lines) else lines

    def abandon_turn(self, thread_id: str, turn_id: str) -> None:
        """Remember that this turn was given up while it may still be running.

        Kept on the process rather than the worker, because the worker is one
        turn and the thing that has to act on it is the next one. An
        interrupt Codex never confirmed leaves a turn generating, and its
        words arriving mid-way through the next prompt's turn used to be
        appended to that prompt's answer.
        """
        if not thread_id or not turn_id:
            return
        with self._state_lock:
            self._abandoned.setdefault(thread_id, set()).add(turn_id)
            while len(self._abandoned) > _CODEX_ABANDONED_THREADS:
                # A conversation nobody ever resumes would otherwise keep its
                # note for the life of the process. Oldest first: dicts keep
                # insertion order, and the newest note is the one still live.
                self._abandoned.pop(next(iter(self._abandoned)))

    def take_abandoned_turns(self, thread_id: str) -> set[str]:
        """The turns given up on this conversation, cleared as they are read."""
        with self._state_lock:
            return self._abandoned.pop(thread_id, set())

    def await_last_words(self, timeout: float) -> None:
        """Let the final line of stderr land before a turn reports the death."""
        reader = self._stderr_reader
        if reader is not None:
            reader.join(timeout=timeout)

    def _read_stdout(self) -> None:
        stdout = getattr(self.proc, "stdout", None)
        if stdout is None:
            self._close("")
            return
        try:
            for raw in stdout:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    self._route(message)
                except Exception as exc:
                    # One message nobody anticipated must not be read as the
                    # stream breaking: this reader serves every tab, and
                    # closing here would end all of their turns at once. It is
                    # written down, though: the turn that message was for now
                    # waits on a reply that will not come, and without a record
                    # the only sign of it is a turn that ends at the reaper a
                    # quarter of an hour later. Method and id only -- what the
                    # message was about is not this log's business.
                    try:
                        logging.getLogger("blindpilot.codex").warning(
                            "dropped an app-server message that could not be routed: "
                            "method=%r id=%r (%s)",
                            message.get("method"),
                            message.get("id"),
                            type(exc).__name__,
                        )
                    except Exception:
                        pass
                    continue
        except Exception as exc:
            # Whatever broke the pipe is the reason every turn on this server
            # is about to end, so it is carried rather than lost to a thread
            # nobody joins.
            self._close(f"BlindPilot stopped reading Codex: {exc}")
            return
        self._close("")

    def _read_stderr(self) -> None:
        # Its own reader, not `process_utils.read_stderr_lines`: the cursor a
        # turn uses to read only its own stderr counts every line, so capping
        # the list would make that cursor skip the lines the cap dropped.
        stderr = getattr(self.proc, "stderr", None)
        if stderr is None:
            return
        try:
            for line in stderr:
                if line.strip():
                    with self._state_lock:
                        self._stderr.append(line.strip())
                        self._stderr_written += 1
        except Exception:
            # stderr is only ever read for the reason a turn failed; failing
            # to read it must not become a second failure of its own.
            pass

    def _route(self, message: dict) -> None:
        method = message.get("method")
        if method is None:
            # A reply. Only the turn that asked wants it.
            request_id = message.get("id")
            waiting = None
            if isinstance(request_id, int):
                with self._state_lock:
                    waiting = self._waiting.pop(request_id, None)
                    binds = request_id in self._thread_replies
                    self._thread_replies.discard(request_id)
                    if waiting is not None and binds and not self._closed:
                        result = message.get("result")
                        thread = result.get("thread") if isinstance(result, dict) else None
                        # Shape-checked at every step. This runs on the one
                        # reader thread, and anything raised here would be
                        # caught as a broken stream and end every tab's turn,
                        # where the same malformed reply parsed in a worker
                        # only ever ended that worker's own.
                        thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
                        if thread_id:
                            self._threads[thread_id] = waiting
            if waiting is not None:
                waiting.put_nowait(message)
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        if method == "turn/completed":
            turn = params.get("turn")
            turn_id = str((turn or {}).get("id") or "") if isinstance(turn, dict) else ""
            with self._state_lock:
                watch = self._turns.get(turn_id)
            if watch is not None:
                watch.done.set()
        thread_id = str(params.get("threadId") or "")
        listener: Optional["queue.Queue[object]"] = None
        with self._state_lock:
            if self._closed:
                return
            if thread_id:
                # Nobody reading means the turn it belongs to has ended -- a
                # trailing delta or a `turn/completed` for a turn that was
                # interrupted. Keeping it would hand the *next* turn on this
                # conversation somebody else's ending to act on.
                listener = self._threads.get(thread_id)
            elif len(self._threads) == 1:
                # `threadId` is required on every notification and every server
                # request this worker acts on (checked against the app server's
                # own `generate-json-schema` for 0.149.1). What is left without
                # one is process-wide -- `configWarning`, `thread/started` --
                # and with a single conversation open there is no ambiguity
                # about who should hear it. With more than one there is, so an
                # unnamed message is dropped rather than told to the wrong tab.
                listener = next(iter(self._threads.values()))
        if listener is None:
            if method and "id" in message:
                # A request, though, must still be answered. Codex holds the
                # id open until it is, and the turn it belongs to never ends.
                # Outside the state lock: send takes the write lock, and the
                # two are never held together.
                self.send(_declined_request(message))
            return
        listener.put_nowait(message)

    def _close(self, error: str) -> None:
        """Wake everyone waiting on this server and say why, once."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._read_error = error
            listeners = list(self._waiting.values()) + list(self._threads.values())
            # Replies can no longer arrive, and no further notification can be
            # routed, so nothing is left to deliver to either.
            self._waiting.clear()
            self._thread_replies.clear()
            self._threads.clear()
            # stdout has ended, so this process is finished with every turn it
            # was running. Saying so lets a cancel racing the death answer at
            # once instead of spending its whole verify budget on a pipe that
            # will never speak again.
            turns = list(self._turns.values())
        for listener in listeners:
            listener.put_nowait(_CODEX_CLOSED)
        for watch in turns:
            watch.done.set()

    def confirm_interrupt(self, thread_id: str, turn_id: str, timeout: float) -> bool:
        """Wait for Codex to say the turn stopped. Overridden in tests.

        This runs on the thread that called `cancel`, not the turn's own, so
        it must not read the message loop: it waits on the event the shared
        reader sets when it sees `turn/completed` for this turn id. Returning
        False means nothing confirmed, which is what makes "interrupt, verify,
        abandon the thread if unsure" a decision rather than a guess.
        """
        done = self.watch_turn(turn_id)
        try:
            return done.wait(timeout)
        finally:
            self.forget_turn(turn_id)

    def interrupt(self, thread_id: str, turn_id: str, timeout: float) -> bool:
        """Ask Codex to stop this turn and say whether it confirmed."""
        if not thread_id or not turn_id:
            return False
        # Held across the send, so a completion arriving the instant the
        # interrupt lands is not missed, and given back either way: the caller
        # may be `cancel`, which has no turn of its own to clean up after.
        self.watch_turn(turn_id)
        try:
            if not self.send(
                {
                    "method": "turn/interrupt",
                    "id": self.next_id(),
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            ):
                return False
            return self.confirm_interrupt(thread_id, turn_id, timeout)
        finally:
            self.forget_turn(turn_id)

    def stop(self) -> None:
        try:
            process_utils.end_process_group(self.proc, timeout=2)
        finally:
            # Both happen even when the process had already exited: waiting
            # turns must still be woken, and the pipe handles must still be
            # released.
            self._close("")
            process_utils._close_process_pipes(self.proc)


def _app_version() -> str:
    """BlindPilot's version, for the clientInfo Codex logs.

    Read off the window's module when it is loaded rather than imported from
    it, because that module imports this one.
    """
    app = sys.modules.get("blindpilot_app")
    return str(getattr(app, "APP_VERSION", "") or "unknown")


def _run_token_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run the plan-token command once and hand back what it printed and said."""
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PLAN_TOKEN_TIMEOUT_MS / 1000.0,
        **process_utils.no_window_kwargs(),
    )


def _plan_token_failure(account: PlanAccount, reason: str) -> str:
    return (
        f"The ChatGPT-plan account {account.label} could not be used for Codex: "
        f"its token command failed. {reason}"
    )


def _plan_token_or_report(account: PlanAccount) -> None:
    """Fetch a token now, so a broken credential is reported instead of a turn.

    Codex runs this same command itself before each request, and a failure there
    reaches the person as a turn that came back with nothing said. Running it
    here makes the failure an error naming the account, before any process is
    started. It costs one process and its exchange; the chat path refreshes
    again when it first asks, since nothing but the stored record is shared.
    """
    command = plan_token_command(account.account_id)
    try:
        done = _run_token_command(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlanTokenError(_plan_token_failure(account, str(exc).strip())) from exc
    printed = (done.stdout or "").strip()
    if done.returncode == 0 and printed:
        return
    said = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
    reason = said[-1].removeprefix("error:").strip() if said else ""
    raise PlanTokenError(
        _plan_token_failure(
            account, reason or f"it exited with {done.returncode} and printed no token"
        )
    )


def _start_codex_server(
    binary: str = "", plan_account: Optional[PlanAccount] = None
) -> CodexServer:
    """Launch the shared app-server. Raises OSError if it cannot be started.

    Given a ``plan_account``, the server runs on that ChatGPT-plan account: it is
    pointed at OpenAI's Responses endpoint and runs ``plan_token_command`` to
    obtain its own bearer token. Without one, Codex uses whatever login the user
    already has, which is the behaviour every existing setup relies on.
    """
    binary = binary or find_backend_cli(BACKEND_CODEX) or ""
    if not binary:
        raise OSError("Codex is not installed. Run: npm install -g @openai/codex")
    server_binary = _codex_app_server_binary(binary)
    plan: tuple[str, ...] = ()
    if plan_account is not None:
        _plan_token_or_report(plan_account)
        plan = codex_plan_args(plan_account.account_id)
    proc = owned_popen(
        [server_binary, *plan, *codex_question_args(), "app-server", "--stdio"],
        cwd=str(Path.home()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=subprocess_env(server_binary),
        # npm's `codex` is a Node launcher that runs the real Codex as a child
        # of its own; stopping only the launcher leaves that child holding the
        # app-server session.
        **process_utils.own_group_kwargs(),
        **process_utils.no_window_kwargs(),
    )
    server = CodexServer(proc)
    server.start_readers()
    return server


def codex_adapter() -> backend_pool.Adapter:
    """How the pool starts, checks, interrupts and stops Codex."""

    def alive(server: object) -> bool:
        return bool(cast(CodexServer, server).alive())

    def stop(server: object) -> None:
        cast(CodexServer, server).stop()

    def busy(server: object) -> bool:
        # Borrowing, not reading: a turn that has sent `thread/start` and is
        # waiting for the reply holds the process while bound to no
        # conversation at all, and is exactly the turn the reaper must not
        # stop the process underneath.
        return cast(CodexServer, server).borrower_count() > 0

    return backend_pool.Adapter(
        alive=alive,
        # The pool's generic interrupt has no turn to name. Codex's cancel path
        # goes through CodexServer.interrupt with the ids it holds; reaching
        # here means nothing could be confirmed.
        interrupt=lambda _server, _timeout: False,
        stop=stop,
        busy=busy,
    )


class _TurnWorker(threading.Thread):
    """What every turn in this module is built on, whichever backend runs it.

    The provider workers differ in how they talk to a provider, not in how
    a turn starts and stops: each is handed the same message, conversation and
    six callbacks, each has to say why it stopped exactly once, and each has to
    report a crash rather than leave the window silent with Send re-enabled.
    Written out per backend, that scaffold was three copies, and a fix to one
    of them was a fix to one third of the app.

    A subclass supplies `_do_run` (the turn itself), `_setup` for the fields
    its own protocol needs, and, if it borrowed anything, `_teardown`. It does
    not restate the signature: the window builds whichever worker the backend
    chose from one set of arguments, and writing that set out once is what
    keeps the three of them interchangeable.
    """

    # This backend as `BACKENDS` names it: the diagnostics record is filed
    # under it, and a crash report calls the provider by its label.
    _backend = ""

    def __init__(
        self,
        prompt: str,
        session_id: Optional[str],
        cwd: str,
        permission_mode: str,
        *,
        model: str = "",
        effort: str = "",
        compact: bool = False,
        attachments: tuple[str, ...] = (),
        on_session: Callable[[str], None],
        on_started: Callable[[], None],
        on_activity: Callable[[str, str], None],
        on_complete: Callable[[str], None],
        on_failed: Callable[[str], None],
        on_done: Callable[[], None],
        on_question: Optional[questions.AskQuestions] = None,
        on_permission: Optional[Callable[[str, dict, list], dict]] = None,
        on_subagent: Optional[SubagentReport] = None,
        plan_account: object = UNBOUND,
    ) -> None:
        super().__init__(daemon=True)
        self._prompt = prompt
        self._session_id = session_id
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._model = model
        self._effort = effort
        # Compaction is a request of its own rather than a message, so this
        # turn summarises the conversation instead of adding to it. Backends
        # that cannot compact are never asked to (see `supports_compaction`).
        self._compact = compact
        # Files handed to the turn itself rather than written into the prompt.
        # Only the backends that take them read this; the rest never see one.
        self._attachments = list(attachments or [])
        self._on_session = on_session
        self._on_started = on_started
        self._on_activity = on_activity
        self._on_complete = on_complete
        self._on_failed = on_failed
        self._on_done = on_done
        self._on_question = on_question
        self._on_permission = on_permission
        self._on_subagent = on_subagent or ignore_subagents
        # The Account the turn's conversation is bound to, or `UNBOUND` to take
        # the saved choice. Only Codex reads this; the rest never see one. It
        # is `object` because `None` is itself a binding -- Codex's own login.
        self._plan_account = plan_account
        self._cancelled = False
        # Set once the turn's ending has been reported, whichever way it
        # ended. The first account of a failure is the one that can be acted
        # on; a crash while cleaning up after it is not worth speaking over
        # the top of it. An Event rather than a flag because either thread can
        # get there first -- a turn can end on the reader while the window's
        # own thread is failing it.
        self._settled = threading.Event()
        self._accepting_input = threading.Event()
        self._setup()

    def _setup(self) -> None:
        """The state this backend's turn needs on top of the shared fields."""

    def accepting_input(self) -> bool:
        return self._accepting_input.is_set() and not self._cancelled

    def _fail(self, message: str) -> None:
        """Report why the turn ended, once."""
        if self._settled.is_set():
            return
        self._settled.set()
        diagnostics.log_unfinished_turn(
            self._backend,
            session_id=self._session_id or "(new)",
            permission_mode=self._permission_mode,
            model=self._model or "(default)",
            cancelled=self._cancelled,
            detail=message,
        )
        self._on_failed(message)

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            # `finally` re-enables Send and stops the progress earcon either
            # way, so anything thrown here used to end the turn exactly as a
            # finished one ends - with no answer and nothing said. The
            # traceback went to a stderr the windowed build does not have.
            self._fail(f"BlindPilot stopped reading {backend_label(self._backend)}: {exc}")
        finally:
            self._accepting_input.clear()
            self._teardown()
            self._on_done()

    def _do_run(self) -> None:
        """The turn itself. Every subclass has one."""
        raise NotImplementedError

    def _teardown(self) -> None:
        """Let go of whatever this turn borrowed, however it ended."""


def auto_review_line(params: dict) -> str:
    """What Codex's automatic approval review decided, in one sentence.

    ``item/autoApprovalReview/completed`` (codex-cli 0.164.0, marked unstable):
    ``review`` holds the status, risk level and rationale; ``action`` what was
    asked for. Still in progress, or a shape this does not know, says nothing.
    """
    review = params.get("review")
    action = params.get("action")
    if not isinstance(review, dict):
        return ""
    verdict = {
        "approved": "approved",
        "denied": "denied",
        "timedOut": "timed out on",
        "aborted": "stopped reviewing",
    }.get(str(review.get("status") or ""))
    if not verdict:
        return ""
    what = "a request"
    if isinstance(action, dict):
        kind = action.get("type")
        if kind == "command" and action.get("command"):
            what = f"running {subagent_line(action['command'], 120)}"
        elif kind == "execve" and action.get("program"):
            what = f"running {action['program']}"
        elif kind == "applyPatch":
            what = "a file change"
        elif kind == "networkAccess" and action.get("host"):
            what = f"network access to {action['host']}"
        elif kind == "mcpToolCall" and action.get("toolName"):
            what = f"the {action['toolName']} tool"
    line = f"Automatic review {verdict} {what}"
    risk = review.get("riskLevel")
    if isinstance(risk, str) and risk:
        line += f", {risk} risk"
    rationale = review.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        line += f": {subagent_line(rationale, 200)}"
    return line + ("" if line.endswith(".") else ".")


def _codex_item_label(item: dict) -> str:
    """The line that says what a Codex item is doing as it starts, or nothing."""
    kind = item.get("type")
    if kind == "commandExecution":
        command = item.get("command")
        if isinstance(command, list):
            command = " ".join(map(str, command))
        return f"Running: {command or 'command'}"
    if kind == "fileChange":
        changes = item.get("changes") or []
        paths = [str(c.get("path")) for c in changes if isinstance(c, dict) and c.get("path")]
        return "Editing " + (", ".join(paths) or "files")
    if kind == "mcpToolCall":
        return f"Using {item.get('server') or 'MCP'}: {item.get('tool') or 'tool'}"
    if kind == "webSearch":
        return f"Searching the web: {item.get('query') or ''}".strip()
    if kind == "imageView":
        return f"Viewing {item.get('path') or 'image'}"
    return ""


def _line_change_counts(old: str, new: str) -> tuple[int, int]:
    """Lines added and removed between two versions of a block.

    A real diff rather than a count of the lines involved: replacing a
    twenty-line function to change two of its lines is a two-line edit, and
    "twenty added, twenty removed" would be a worse answer than none.
    """
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += old_end - old_start
        if tag in ("replace", "insert"):
            added += new_end - new_start
    return added, removed


def _lines_phrase(added: int, removed: int) -> str:
    """ ", 5 lines added, 3 removed" - or nothing at all.

    Appended to the line the tool call already has rather than spoken as a
    second one: this narration is long enough to fall behind on a fan-out
    without adding an utterance per edit. Halves that are zero are left out,
    because "0 removed" is a word said for no reason every time.
    """
    parts = []
    if added:
        parts.append(f"{added} line{'' if added == 1 else 's'} added")
    if removed:
        # "added" already carried the word "lines" when both are present.
        word = "" if added else f" line{'' if removed == 1 else 's'}"
        parts.append(f"{removed}{word} removed")
    return ", " + ", ".join(parts) if parts else ""


def _tool_use_label(name: str, params: dict) -> str:
    """One spoken line describing the tool Claude just invoked.

    This is the narration that answers "what is it doing right now" — the CLI
    does not forward thinking blocks in print mode, so the tool calls are the
    live signal. Phrased as an action ("Reading foo.py") rather than a raw tool
    name and JSON blob.
    """

    def first(*keys: str) -> str:
        for key in keys:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        return ""

    target = first("file_path", "path", "notebook_path")
    short = os.path.basename(target) if target else ""

    def text(key: str) -> str:
        value = params.get(key)
        return value if isinstance(value, str) else ""

    if name == "Read":
        return f"Reading {short}" if short else "Reading a file"
    if name in ("Edit", "NotebookEdit", "MultiEdit"):
        # How much it changed is the only sense of scale available to somebody
        # who cannot see the diff, and "changed a line" and "rewrote the file"
        # are the same sentence without it.
        edits = params.get("edits")
        if isinstance(edits, list):
            added = removed = 0
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                one, two = edit.get("old_string"), edit.get("new_string")
                if isinstance(one, str) and isinstance(two, str):
                    more, fewer = _line_change_counts(one, two)
                    added += more
                    removed += fewer
        else:
            added, removed = _line_change_counts(text("old_string"), text("new_string"))
        where = f"Editing {short}" if short else "Editing a file"
        return where + _lines_phrase(added, removed)
    if name == "Write":
        where = f"Writing {short}" if short else "Writing a file"
        written = len(text("content").splitlines())
        if not written:
            return where
        return f"{where}, {written} line{'' if written == 1 else 's'}"
    if name in ("Bash", "PowerShell"):
        cmd = first("command")
        return f"Running: {cmd}" if cmd else f"Running a {name} command"
    if name in ("Grep", "Glob"):
        pattern = first("pattern")
        return f"Searching for {pattern}" if pattern else "Searching"
    if name in ("WebFetch", "WebSearch"):
        what = first("url", "query")
        return f"Fetching {what}" if what else "Searching the web"
    if name == "Task":
        return f"Delegating: {first('description') or 'a subtask'}"
    if name == "TodoWrite":
        return "Updating the task list"
    detail = first("description", "command", "query", "prompt")
    return f"Using {name}: {detail}" if detail else f"Using {name}"


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][A-Z0-9])")


def _strip_terminal_noise(text: str) -> str:
    text = _ANSI_RE.sub("", text).replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _freebuff_chat_folders(cwd: str) -> list[Path]:
    """Every folder a FreeBuff chat for this workspace could be stored under."""
    root = Path.home() / ".config" / "manicode" / "projects"
    candidates = [root / Path(cwd).name / "chats", root / "chats"]
    try:
        candidates.extend(path for path in root.glob("*/chats") if path not in candidates)
    except OSError:
        pass
    return candidates


def _freebuff_chat_dirs(cwd: str) -> dict[str, float]:
    """Return known FreeBuff chat ids and modification times.

    FreeBuff derives its storage bucket from the Git root, which is not always
    the workspace basename supplied with ``--cwd``.  Search every chat bucket
    so a newly-created id can still be captured and resumed.
    """
    found: dict[str, float] = {}
    for folder in _freebuff_chat_folders(cwd):
        try:
            for path in folder.iterdir():
                if path.is_dir():
                    found[path.name] = max(found.get(path.name, 0.0), path.stat().st_mtime)
        except OSError:
            pass
    return found


def _freebuff_chat_path(cwd: str, session_id: str) -> Optional[Path]:
    """Locate one FreeBuff chat regardless of its Git-derived project bucket."""
    if not session_id:
        return None
    for folder in _freebuff_chat_folders(cwd):
        candidate = folder / session_id
        if candidate.is_dir():
            return candidate
    return None


def _freebuff_resume_chat_path(cwd: str, session_id: str) -> Optional[Path]:
    """The CLI only resumes IDs in this project's bucket; otherwise it falls back."""
    chat = _freebuff_chat_path(cwd, session_id)
    project = Path(cwd).resolve().name
    expected = Path.home() / ".config" / "manicode" / "projects" / project / "chats"
    return chat if chat is not None and chat.parent == expected else None


_FREEBUFF_INTERRUPTED = "[response interrupted]"
_FREEBUFF_BOOT_FINGERPRINT = "Enhanced CLI fingerprint generated successfully"

# What FreeBuff's start screen says while it is waiting for a model to be
# chosen. The wording has moved between releases - the card was once labelled
# RECOMMENDED and is now introduced by a heading - so all of it is recognised:
# a chooser nobody answers is a terminal that never reaches its composer, and a
# message that is never sent.
_FREEBUFF_PICKER_RE = re.compile(r"(?i)RECOMMENDED|Start coding for free|See all \d+ models?")

# Either phrase means the full model list is already on screen. 0.0.168 opens
# on the expanded list and says "Show fewer"; earlier releases opened on a
# recommended card with a "See all N models" entry. Navigating straight to a
# model is only correct on the expanded screen — on the collapsed one the
# first Down moves to that entry instead.
_FREEBUFF_PICKER_EXPANDED_RE = re.compile(r"(?i)show fewer|see all \d+ models?")

# The title FreeBuff draws on the box its `ask_user` tool opens. It is the one
# thing on that screen that is always there and never part of an answer, so it
# is what says a turn has stopped to ask something.
_FREEBUFF_QUESTION_MARKER = "Some questions for you"

# 0.0.180 draws each message in the transcript under a divider carrying the
# time it was sent, so a message is two lines rather than one. Measured on the
# release itself: "   [05:23 PM]" then "   Reply with the single word: pong".
# Dropping the message line and leaving the divider behind reads the time out
# as if the answer had said it.
_FREEBUFF_TIMESTAMP_RE = re.compile(r"^\[\d{1,2}:\d{2}\s*[AP]M\]$", re.IGNORECASE)

# A question in that box: collapsed (right-pointing) or open (down-pointing),
# numbered only when there is more than one.
_FREEBUFF_QUESTION_RE = re.compile(r"^([▼▶])\s*(?:\d+\.\s*)?(\S.*?)\s*$")

# One answer in the open question: a radio circle, or a checkbox where the
# question takes more than one answer.
_FREEBUFF_OPTION_RE = re.compile(r"^\s+([○●☐☑])\s+(\S.*?)\s*$")

# FreeBuff adds this entry itself, below the model's own options, and opens a
# text field when it is chosen. BlindPilot's dialog offers the same thing in
# its own words, so the entry is not repeated there.
_FREEBUFF_CUSTOM_LABEL = "Custom"

_FREEBUFF_MULTI_MARKER = "(Select multiple options)"

# The box's bottom-left corner, and the side it draws down both edges.
_FREEBUFF_BOX_BOTTOM = "╰"
_FREEBUFF_BOX_SIDE = "│"
_FREEBUFF_OPEN_MARK = "▼"
_FREEBUFF_CHECKBOXES = ("☐", "☑")

# Keys the box understands. Down moves through the answers, wrapping into the
# next question once it runs off the end of one; Enter chooses; Tab jumps to
# Submit; Escape closes the box, which FreeBuff reports to the model as the
# questions having been skipped.
_KEY_DOWN = "\x1b[B"
_KEY_ENTER = "\r"
_KEY_TAB = "\t"
_KEY_ESCAPE = "\x1b"

# Long enough for OpenTUI to repaint between keystrokes.
_FREEBUFF_KEY_SETTLE = 0.15

# FreeBuff's own tool takes at least one question and puts no ceiling on
# them; this is the point past which a box has stopped being a question
# and started being a loop.
_FREEBUFF_MAX_QUESTIONS = 8


def _freebuff_question_box(visible: str) -> list[str]:
    """The lines of FreeBuff's question box, with its border taken off."""
    lines = visible.split("\n")
    start = next(
        (index for index, line in enumerate(lines) if _FREEBUFF_QUESTION_MARKER in line),
        -1,
    )
    if start < 0:
        return []
    body: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(_FREEBUFF_BOX_BOTTOM):
            break
        if stripped.startswith(_FREEBUFF_BOX_SIDE):
            stripped = stripped[1:]
        if stripped.endswith(_FREEBUFF_BOX_SIDE):
            stripped = stripped[:-1]
        body.append(stripped.rstrip())
    return body


def _freebuff_open_question(visible: str) -> tuple[int, int, Optional[questions.Question]]:
    """(how many questions there are, which one is open, and that question).

    FreeBuff shows one question's answers at a time and keeps the rest folded
    away, so this reads whichever one is open. The count is what says how many
    more are still to come.
    """
    body = _freebuff_question_box(visible)
    if not body:
        return 0, -1, None
    total = 0
    open_index = -1
    text = ""
    options: list[questions.QuestionOption] = []
    multi = False
    for line in body:
        header = _FREEBUFF_QUESTION_RE.match(line)
        if header:
            if header.group(1) == _FREEBUFF_OPEN_MARK:
                if open_index >= 0:
                    # Two open questions at once is a half-drawn frame; keep
                    # the first, which is the one already read.
                    break
                open_index = total
                text = header.group(2)
                options = []
                multi = False
            total += 1
            continue
        if open_index < 0 or total != open_index + 1:
            continue
        if _FREEBUFF_MULTI_MARKER in line:
            multi = True
            continue
        option = _FREEBUFF_OPTION_RE.match(line)
        if option:
            if option.group(1) in _FREEBUFF_CHECKBOXES:
                multi = True
            options.append(questions.QuestionOption(option.group(2)))
    if open_index < 0 or not text:
        return total, -1, None
    if options and options[-1].label == _FREEBUFF_CUSTOM_LABEL:
        options = options[:-1]
    return (
        total,
        open_index,
        questions.Question(question=text, options=tuple(options), multi_select=multi),
    )


# How long a frame must hold still before it is read out. The terminal repaints
# in bursts, so this is only long enough to let one burst land whole.
_FREEBUFF_FRAME_SECONDS = 0.1

# Never wait longer than this to read out a finished sentence, however busy the
# repainting is. Text that arrives faster than the frames settle would otherwise
# never reach the listener until the turn ended.
_FREEBUFF_MAX_LAG_SECONDS = 0.4

# The longest a single FreeBuff turn is listened to. Reaching it is not the
# turn finishing, and is reported as what it is - see the end of `_do_run`.
_FREEBUFF_TURN_SECONDS = 60 * 60

# How long a starting FreeBuff may paint nothing at all before the message is
# given up on. This bounds the wait by silence rather than by the clock: any
# repaint at all -- a download progress bar, a splash, the model picker -- is
# progress and starts it again, so a slow first launch that is visibly doing
# something is never cut off. What it does catch is a FreeBuff that starts,
# connects, and then paints nothing ever again, which is what 0.0.163 does
# here: the turn's own deadline is an hour, so waiting that out cost the
# message and an hour of silence with it. Two minutes because a first launch
# downloads a 125MB FreeBuff and then unpacks it, and only the download half
# draws a progress bar.
_FREEBUFF_STARTUP_SILENCE_SECONDS = 120.0

# How long a mid-turn drop is listened to before it is called. A session can
# rejoin on its own — the reconnection lands within a second when it does —
# and a turn abandoned at the first word of trouble would throw away work that
# a moment of patience would have saved.
_FREEBUFF_DROP_PATIENCE_SECONDS = 30.0

# How long the model picker is walked before the chosen model is given up on.
# The navigation above it is a fixed number of arrow keys, so this only ever
# elapses when the model is not on the cards at all — see the swap below.
_FREEBUFF_PICKER_PATIENCE_SECONDS = 5.0


def _unwrap_screen_text(text: str) -> str:
    """Rejoin the lines the terminal broke, keeping the ones the answer meant.

    A captured frame is text laid out to the width of FreeBuff's box, so most of
    its newlines belong to the terminal rather than the answer.  Left in, every
    one of them reads as the end of a sentence, and the reading stops in the
    middle of a clause.  A line that runs the full width was broken because it
    ran out of room and continues below; a short one ended because the text did.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    if len(lines) < 2:
        return text.strip()
    width = max(len(line) for line in lines)
    if width < 24:
        return text.strip()
    pieces: list[str] = []
    for index, line in enumerate(lines[:-1]):
        pieces.append(line)
        following = lines[index + 1]
        wrapped = bool(line) and bool(following) and len(line) >= width - 12
        pieces.append(" " if wrapped else "\n")
    pieces.append(lines[-1])
    return "".join(pieces).strip()


def _keyed(text: str, letters_only: bool = False) -> tuple[str, list[int]]:
    """A comparable form of ``text``, and where each kept character came from.

    Whitespace always goes, because the terminal decides where the answer
    breaks its lines and revises that as the text grows: the same words can be
    one line in one frame and two in the next. Dropping everything except
    letters and digits goes further, and makes the answer as the terminal drew
    it comparable with the Markdown it was drawn from.
    """
    kept: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(text):
        if character.isspace() or (letters_only and not character.isalnum()):
            continue
        folded = character.casefold() if letters_only else character
        kept.append(folded)
        # One position per character of the *key*, not per character of the
        # text. `casefold` is not length-preserving - German "ss" comes from a
        # single letter, Turkish and the ligatures do the same - and a map with
        # one entry per input character then runs short, so the index used to
        # cut the answer pointed at the wrong letter or off the end entirely.
        positions.extend([index] * len(folded))
    # The sentinel, so an answer that was read out in full is still indexable.
    positions.append(len(text))
    return "".join(kept), positions


def _freebuff_echo(text: str) -> tuple[str, str]:
    """What to look for on screen for a message typed into FreeBuff.

    FreeBuff writes what it is given into the transcript as plain text, with
    nothing to mark it as the person's own words, so an echo is recognised by
    its content: the first line, which is what a transcript line is matched
    against, and the whole message reduced to letters and digits, which is what
    says whether the lines below that match are the rest of the echo or the
    reply's first line.
    """
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:60], _keyed(text, letters_only=True)[0]


def _unspoken_tail(narrated: str, answer: str) -> str:
    """The part of the finished answer that was never read out.

    What was read came off the screen, laid out for a terminal; the finished
    answer comes from the saved chat, written as Markdown. Comparing only the
    letters and digits is what makes the two comparable, so that an answer
    which scrolled out of view while it was being written is still finished
    aloud, and one that was read in full is not read twice.
    """
    spoken_key, _spoken_positions = _keyed(narrated, letters_only=True)
    answer_key, positions = _keyed(answer, letters_only=True)
    if not answer_key:
        return ""
    if not spoken_key:
        return answer.strip()
    if answer_key.startswith(spoken_key):
        return answer[positions[len(spoken_key)] :].strip()
    if answer_key in spoken_key:
        return ""
    # The two do not line up at all, which means the reading was of something
    # else. Reading the answer again is better than never reading it.
    return answer.strip()


def _append_delta(spoken: str, current: str) -> str:
    """The part of ``current`` that has not been read out yet.

    The screen is a window onto the answer, not the whole of it: once the answer
    is taller than the terminal, the top scrolls away and a frame becomes a
    continuation rather than an extension.  Matching the longest overlap between
    the end of what was read and the start of what is on screen keeps the
    reading in one piece across that scroll, instead of repeating a paragraph or
    silently dropping one.
    """
    if not current:
        return ""
    if not spoken:
        return current
    spoken_key, _spoken_positions = _keyed(spoken)
    current_key, positions = _keyed(current)
    if not current_key:
        return ""
    if current_key.startswith(spoken_key):
        return current[positions[len(spoken_key)] :]
    # The tail of what was read is normally still on screen, so find it there
    # first; that is one string search rather than a scan over every overlap.
    anchor = spoken_key[-160:]
    if anchor:
        position = current_key.rfind(anchor)
        if position >= 0:
            return current[positions[position + len(anchor)] :]
    for size in range(min(len(spoken_key), len(current_key), 400), 0, -1):
        if spoken_key.endswith(current_key[:size]):
            return current[positions[size] :]
    return current


def _freebuff_answer_message(payload: object) -> Optional[dict]:
    """Return the newest AI message that actually carries a reply.

    FreeBuff writes a ``mode-divider`` message ahead of every turn, and it also
    has ``variant: "ai"``.  Skipping messages with no reply blocks keeps the
    identity of the turn's real answer stable from the moment it appears.
    """
    if not isinstance(payload, list):
        return None
    for item in reversed(payload):
        if not isinstance(item, dict) or item.get("variant") != "ai":
            continue
        blocks = item.get("blocks")
        if isinstance(blocks, list) and any(
            isinstance(block, dict) and block.get("type") in ("text", "agent") for block in blocks
        ):
            return item
    return None


def _freebuff_answer_id(chat: Optional[Path]) -> str:
    """Identify the newest answer already in a chat, before a new turn starts."""
    if chat is None:
        return ""
    try:
        payload = json.loads((chat / "chat-messages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    message = _freebuff_answer_message(payload)
    return str(message.get("id") or "") if message else ""


def _freebuff_chat_snapshot(
    chat: Optional[Path],
) -> tuple[str, str, str, list[tuple[str, str, str, tuple[str, ...]]]]:
    """Read the newest answer's id, reasoning, text, and agent states.

    Each agent comes with its log so far: the task it was given, each tool
    it called, and what it wrote.

    The id is what tells one turn's answer from the one before it: FreeBuff
    rewrites the whole file on every save, so text alone cannot distinguish new
    output from a resumed conversation's history.
    """
    if chat is None:
        return "", "", "", []
    try:
        payload = json.loads((chat / "chat-messages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", "", "", []
    message = _freebuff_answer_message(payload)
    if message is None:
        return "", "", "", []
    message_id = str(message.get("id") or "")

    thinking: list[str] = []
    answer: list[str] = []
    agents: list[tuple[str, str, str, tuple[str, ...]]] = []
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        blocks = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            content = str(block.get("content") or "").strip()
            # Closing the hidden terminal stamps this marker onto whatever the
            # turn had produced, so it has to come off the end as well as being
            # rejected on its own.
            if content.endswith(_FREEBUFF_INTERRUPTED):
                content = content[: -len(_FREEBUFF_INTERRUPTED)].strip()
            if not content:
                continue
            if block.get("textType") == "reasoning":
                thinking.append(content)
            else:
                answer.append(content)
        elif kind == "agent":
            name = str(block.get("agentName") or block.get("agentType") or "agent").strip()
            status = str(block.get("status") or "running").strip().casefold()
            agent_id = str(block.get("agentId") or f"{index}:{name}")
            agents.append((agent_id, name, status, _freebuff_agent_log(block)))
    return message_id, "\n\n".join(thinking), "\n\n".join(answer), agents


def _freebuff_agent_log(block: dict) -> tuple[str, ...]:
    """One spawned agent's log, from its block in the saved chat."""
    lines: list[str] = []
    task = subagent_line(block.get("initialPrompt"))
    if task:
        lines.append(f"Task: {task}")
    inner = block.get("blocks")
    for step in inner if isinstance(inner, list) else []:
        if not isinstance(step, dict):
            continue
        kind = step.get("type")
        if kind == "tool":
            params = step.get("input")
            lines.append(
                _tool_use_label(
                    str(step.get("toolName") or "tool"), params if isinstance(params, dict) else {}
                )
            )
        elif kind == "text":
            text = subagent_line(step.get("content"))
            if text:
                lines.append(text)
        elif kind == "agent":
            lines.append(f"Started {step.get('agentName') or step.get('agentType') or 'an agent'}")
    return tuple(lines)


def _freebuff_chat_stamp(chat: Optional[Path]) -> tuple:
    """A cheap fingerprint of a chat's files, to skip re-reading them.

    The loop looks at the chat many times a second so that the end of a turn is
    noticed the moment it happens; asking the filesystem what changed is orders
    of magnitude cheaper than parsing the whole conversation to find out.
    """
    if chat is None:
        return ()
    marks: list[tuple[int, int]] = []
    for name in ("chat-messages.json", "log.jsonl"):
        try:
            stat = (chat / name).stat()
            marks.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            marks.append((0, 0))
    return tuple(marks)


def _freebuff_run_status(chat: Optional[Path], offset: int = 0) -> str:
    """Return complete/cancelled/disconnected from FreeBuff's per-chat log.

    "session over" alone is not a drop: 0.0.168 opens every new chat with
    "Freebuff session over; holding queued messages until rejoin" and then
    reconnects within a second. A drop is a "session over" that no later
    "Reconnection detected" or "Start agent" line answers, so the reader
    walks the lines in order and only reports what is still pending at the
    end. Complete and cancelled stay whole-log readings, since a turn that
    finished is finished however the session ended afterwards.
    """
    if chat is None:
        return ""
    tail = _freebuff_log_tail(chat, offset)
    if not tail:
        return ""
    messages = _freebuff_log_messages(tail)
    if any(message.startswith("Agent run cancelled by user") for message in messages):
        return "cancelled"
    if "Main prompt finished" in messages:
        return "complete"
    if _freebuff_log_pending_drop(tail):
        return "disconnected"
    return ""


def _freebuff_log_messages(tail: str) -> list[str]:
    """Read event names, never prompt or answer text inside the log data."""
    messages = []
    for line in tail.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            # The final line can still be in the middle of being written.
            continue
        if isinstance(entry, dict) and isinstance(entry.get("msg"), str):
            messages.append(entry["msg"])
    return messages


def _freebuff_log_pending_drop(tail: str) -> bool:
    """Whether a log's last word on the session is a drop nobody answered.

    The lines are walked in order: "session over" opens a pending drop and a
    later "Reconnection detected" or "Start agent" closes it. What is left
    pending at the end is the session's state right now.
    """
    pending = False
    for line in _freebuff_log_messages(tail):
        if line.startswith("[chat-runtime] Freebuff session over;"):
            pending = True
        elif (
            line.startswith(("Reconnection detected", "Start agent "))
            or line == _FREEBUFF_BOOT_FINGERPRINT
        ):
            pending = False
    return pending


def _freebuff_log_tail(chat: Path, offset: int = 0) -> str:
    """The per-chat log from ``offset`` on, decoded lossily."""
    try:
        with (chat / "log.jsonl").open("rb") as handle:
            handle.seek(max(0, offset))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


# Said once, in the same words, wherever a genuine drop is noticed: a turn
# accepted and then never answered, on a session that logged "session over"
# and never reconnected.
_FREEBUFF_REJOIN_MESSAGE = (
    "FreeBuff dropped its session and is holding the message until it rejoins, "
    "but nothing is answering. Quit and reopen FreeBuff, then send the message again."
)


def _freebuff_log_boot_finished(tail: str) -> bool:
    """Whether a launch's log shows a FreeBuff that can take a message.

    Every measured boot (0.0.180, 2026-09-01; 0.2.26, 2026-10-12) writes the
    same sequence: "session over", then the enhanced-fingerprint line once
    sign-in is settled, then -- in the older release -- "Reconnection
    detected". 0.2.26 never writes the reconnection line, so the fingerprint
    is the last boot milestone both releases share. A boot is finished when
    that line is in the log, or when a session that dropped has visibly
    reconnected. Nothing earlier counts: the MCP-servers line is first, and
    a tail with no drop yet is a boot that has not reached the drop either.
    """
    messages = _freebuff_log_messages(tail)
    if _freebuff_log_pending_drop(tail):
        return False
    if _FREEBUFF_BOOT_FINGERPRINT in messages:
        return True
    return any(
        message.startswith(("Reconnection detected", "Start agent ")) for message in messages
    )


def _freebuff_boot_ready(
    cwd: str,
    before: dict[str, float],
    chat_path: Optional[Path],
    log_offset: int,
    pid: int = 0,
) -> bool:
    """Whether FreeBuff's chat runtime has reconnected and can take a message.

    0.0.168 paints its composer before it has finished starting, and a message
    given to that composer is lost silently: the chat log records no send at
    all. That is still true in 0.2.26, and worse: measured live, keystrokes
    sent 1.4 seconds after launch vanish without reaching the composer,
    while the same send at 2.6 seconds is answered. The recovery used to be
    visible in the chat's own log -- "session over" answered by
    "Reconnection detected" within a second -- but 0.2.26 writes no
    reconnection line, so readiness is the boot's last shared milestone (see
    :func:`_freebuff_log_boot_finished`). For a conversation this launch
    created, the whole log is read and the milestone must be seen; for a
    resumed one, only the lines written since the launch started are read,
    and the milestone must appear in those new lines too. A launch that has
    created no chat yet cannot be ready either: its
    chat is created when the terminal starts, so a missing chat is a boot
    still in progress, and the startup-silence failure is the honest end of
    a launch that never gets there. Taking it as ready was the hole the
    1.4-second send fell through.
    """
    if chat_path is not None:
        tail = _freebuff_log_tail(chat_path, log_offset)
        if not tail.strip():
            return False
        return _freebuff_log_boot_finished(tail)
    new_tail = _freebuff_new_chat_log(before, cwd, pid)
    if new_tail is None:
        return False
    if not new_tail.strip():
        # A chat folder with nothing logged yet is a boot still in progress;
        # the recovery line arrives moments later.
        return False
    return _freebuff_log_boot_finished(new_tail)


def _freebuff_new_chat_path(before: dict[str, float], cwd: str, pid: int = 0) -> Optional[Path]:
    """Find this terminal's chat using the process id FreeBuff logs at boot."""
    after = _freebuff_chat_dirs(cwd)
    new_ids = set(after) - set(before)
    if not new_ids:
        return None
    family = process_utils._descendant_pids({pid}) if pid else set()
    for chat_id in sorted(new_ids, key=lambda chat_id: after[chat_id], reverse=True):
        target = _freebuff_chat_path(cwd, chat_id)
        if target is None:
            continue
        if not pid:
            return target
        try:
            with (target / "log.jsonl").open("rb") as log:
                first_lines = log.read(8192).decode("utf-8", "replace")
        except OSError:
            continue
        for line in first_lines.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("pid") in family:
                return target
    return None


def _freebuff_new_chat_log(before: dict[str, float], cwd: str, pid: int = 0) -> Optional[str]:
    """The whole log of the chat this launch created, or None if it made none."""
    target = _freebuff_new_chat_path(before, cwd, pid)
    if target is None:
        return None
    return _freebuff_log_tail(target, 0)


def _freebuff_dropped_new_chat(before: dict[str, float], cwd: str, pid: int = 0) -> bool:
    """Whether a chat created during this launch is waiting on a dead session.

    "session over" is also the first line of a healthy boot, so the question
    is not whether it was logged but whether anything ever answered it. Only
    chats created during this launch are consulted: a drop logged by an
    earlier session must not fail this one.
    """
    tail = _freebuff_new_chat_log(before, cwd, pid)
    return bool(tail and tail.strip()) and _freebuff_log_pending_drop(cast(str, tail))


def _last_visible_line(visible: str) -> str:
    """The last non-blank line on the screen, short enough to be spoken."""
    lines = [line.strip() for line in _strip_terminal_noise(visible).splitlines()]
    reason = next((line for line in reversed(lines) if line), "")
    if len(reason) > 200:
        reason = reason[:199] + "…"
    return reason


def _freebuff_launch_failure(visible: str) -> str:
    """Say why FreeBuff's terminal closed, in its own words where it gave any.

    A terminal that dies during start-up has usually printed one line saying
    why. ``env: node: No such file or directory`` is the common one on macOS,
    where an application started from the Dock inherits a PATH holding no Node
    at all, and it is far more use than a guess at reinstalling.
    """
    reason = _last_visible_line(visible)
    if not reason:
        return (
            "FreeBuff's terminal closed before it was ready for a prompt, "
            "without saying why. Check that FreeBuff runs in a terminal, then "
            "try again."
        )
    if "node" in reason.casefold() and "not found" in reason.casefold().replace(
        "no such file or directory", "not found"
    ):
        return (
            f"FreeBuff could not start: {reason}. FreeBuff runs on Node.js, and "
            "BlindPilot could not find it. Install Node.js, or use File \u2192 "
            "Manage Backends to let BlindPilot install one for you."
        )
    return f"FreeBuff's terminal closed before it was ready for a prompt: {reason}"


def _freebuff_startup_silence(visible: str, seconds: float) -> str:
    """Say that FreeBuff started, went quiet, and never asked for a prompt.

    A terminal that dies prints why on its way out, and
    :func:`_freebuff_launch_failure` reports that sentence. This is the other
    shape of the same lost message: FreeBuff is still running, still connected,
    and simply never paints a composer to type into.
    """
    waited = int(seconds)
    reason = _last_visible_line(visible)
    if reason:
        return (
            f"FreeBuff stopped short of a prompt and printed nothing for {waited} "
            f"seconds. The last thing it showed was: {reason}"
        )
    return (
        f"FreeBuff started but printed nothing at all for {waited} seconds, so "
        "there was never a prompt to send the message to. Run freebuff in a "
        "terminal to see what it does there: if it hangs there too, the "
        "installed FreeBuff release is at fault rather than BlindPilot."
    )


class FreebuffTerminal(Protocol):
    """The pseudo-terminal handle, whichever implementation produced it.

    _ConPtyTerminal on Windows and pexpect everywhere else hand back objects
    with nothing in common but the three calls made on them here. What those
    calls give back differs too, and none of it is used, so none of it is named.
    """

    def write(self, text: str, /) -> object: ...

    def terminate(self, force: bool = False) -> object: ...

    def close(self, force: bool = False) -> object: ...


def _drain_to_queue(
    read_one: Callable[[], Optional[str]], stream_ended: threading.Event
) -> Callable[[float], str]:
    """Pump a terminal into a queue on a thread of its own, and hand back a reader.

    Draining starts at launch rather than when somebody first asks for
    output: a prewarmed terminal has no worker reading it yet, and a full PTY
    buffer blocks the TUI before it can process input even though its
    connection log says ready.

    ``read_one`` hands back whatever has arrived, "" for nothing yet, and
    None once nothing more ever will. Reading to that rather than while the
    terminal is alive is what keeps the last line of a terminal that died at
    startup -- the one that says why -- still buffered after isalive() goes
    false.
    """
    chunks: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        try:
            while not stream_ended.is_set():
                data = read_one()
                if data is None:
                    break
                if data:
                    chunks.put(data)
        finally:
            # Nothing more will ever arrive. Saying so turns a terminal that
            # died at startup into a reported failure instead of an hour of
            # silence.
            stream_ended.set()

    threading.Thread(target=pump, daemon=True).start()

    def read(timeout: float) -> str:
        try:
            return chunks.get(timeout=timeout)
        except queue.Empty:
            return ""

    return read


class _ConPtyTerminal:
    """A process in a pseudoconsole this process created itself.

    pywinpty cannot be used for this: its native module calls AllocConsole
    in the host process before CreatePseudoConsole (the import is in the
    shipped ``_winpty`` binary), so a windowless BlindPilot gains a real
    console window -- hidden again by its own watcher only after it has
    appeared and taken screen-reader focus, which NVDA announces as
    "terminal". Calling CreatePseudoConsole directly allocates nothing in
    this process: the pseudoconsole's hosting window is not created, and
    the only console host spawned is OpenConsole in headless mode.
    """

    def __init__(
        self, args: list[str], cwd: str, env: dict[str, str], rows: int, cols: int
    ) -> None:
        import codecs
        import ctypes
        import ctypes.wintypes as wintypes

        self._ctypes = ctypes

        class _Coord(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        self._coord = _Coord
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pid = 0
        self._closed = False
        kernel32 = ctypes.windll.kernel32
        handle = ctypes.c_void_p
        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(handle),
            ctypes.POINTER(handle),
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.CreatePseudoConsole.argtypes = [
            self._coord,
            handle,
            handle,
            wintypes.DWORD,
            ctypes.POINTER(handle),
        ]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [
            handle,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = kernel32.ReadFile.argtypes
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [handle]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [handle, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [handle, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [handle, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ClosePseudoConsole.argtypes = [handle]
        kernel32.PeekNamedPipe.argtypes = [
            handle,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.PeekNamedPipe.restype = wintypes.BOOL

        input_read, input_write = handle(), handle()
        output_read, output_write = handle(), handle()
        if not kernel32.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), None, 0):
            raise OSError("CreatePipe failed for the terminal's input")
        if not kernel32.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), None, 0):
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(input_write)
            raise OSError("CreatePipe failed for the terminal's output")
        self._input_write = input_write
        self._output_read = output_read
        self._pseudo = handle()
        self._process = None
        self._job: Optional[_WindowsJob] = None
        try:
            pseudo = handle()
            result = kernel32.CreatePseudoConsole(
                self._coord(cols, rows), input_read, output_write, 0, ctypes.byref(pseudo)
            )
            if result != 0:
                raise OSError(f"CreatePseudoConsole failed: HRESULT {result & 0xFFFFFFFF:#x}")
            self._pseudo = pseudo
            self._process, pid = self._create_process(args, cwd, env, pseudo)
        except Exception:
            self.close()
            raise
        finally:
            # The pseudoconsole keeps its own references to these two ends.
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(output_write)
        self.pid = int(pid)

    def _create_process(self, args: list[str], cwd: str, env: dict[str, str], pseudo: object):
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32

        class _StartupInfo(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", ctypes.c_void_p),
                ("hStdOutput", ctypes.c_void_p),
                ("hStdError", ctypes.c_void_p),
            ]

        class _StartupInfoEx(ctypes.Structure):
            _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

        class _ProcessInformation(ctypes.Structure):
            _fields_ = [
                ("hProcess", ctypes.c_void_p),
                ("hThread", ctypes.c_void_p),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            ]

        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
        # STARTF_USESTDHANDLES with no handles named: the kernel then gives
        # the child the pseudoconsole as its standard handles. Left at the
        # default, the child inherits this process's standard handles
        # instead (measured: a python child's prints reached BlindPilot's
        # own stdout, bypassing the terminal entirely), while through
        # pywinpty the same child reported proper console handles.
        startup.StartupInfo.dwFlags = 0x00000100
        startup.StartupInfo.hStdInput = None
        startup.StartupInfo.hStdOutput = None
        startup.StartupInfo.hStdError = None
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        startup.lpAttributeList = ctypes.cast(buffer, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(
            startup.lpAttributeList, 1, 0, ctypes.byref(size)
        ):
            raise OSError("InitializeProcThreadAttributeList failed")
        try:
            # PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
            if not kernel32.UpdateProcThreadAttribute(
                startup.lpAttributeList,
                0,
                ctypes.c_size_t(0x00020016),
                pseudo,
                ctypes.sizeof(ctypes.c_void_p),
                None,
                None,
            ):
                raise OSError("UpdateProcThreadAttribute failed for the pseudoconsole")
            argv = list(args)
            command = subprocess.list2cmdline(argv)
            if argv and argv[0].lower().endswith((".cmd", ".bat")):
                # CreateProcess cannot run a batch file; cmd hosts it, as it
                # did under pywinpty (its /c invocation is what process
                # listings of the old path show).
                comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
                # Expand each argument once inside quotes: literal % and shell
                # operators in a folder name must never become cmd commands.
                if any('"' in arg for arg in argv):
                    raise OSError("Batch launcher arguments cannot contain double quotes")
                env = dict(env)
                for index, arg in enumerate(argv):
                    env[f"BLINDPILOT_PTY_ARG_{index}"] = arg
                arguments = " ".join(
                    f'"%BLINDPILOT_PTY_ARG_{index}%"' for index in range(len(argv))
                )
                command = f'{subprocess.list2cmdline([comspec])} /d /v:off /s /c "{arguments}"'
            command_line = ctypes.create_unicode_buffer(command)
            env_block = ctypes.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"
            )
            info = _ProcessInformation()
            # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT |
            # CREATE_SUSPENDED: no child can run before Job assignment.
            if not kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                False,
                0x00080000 | 0x00000400 | 0x00000004,
                env_block,
                cwd,
                ctypes.byref(startup),
                ctypes.byref(info),
            ):
                raise ctypes.WinError(kernel32.GetLastError())
        finally:
            kernel32.DeleteProcThreadAttributeList(startup.lpAttributeList)
        try:
            job = _WindowsJob()
            job.assign_handle(info.hProcess)
            if kernel32.ResumeThread(info.hThread) == 0xFFFFFFFF:
                raise OSError("ResumeThread failed for the FreeBuff backend")
            self._job = job
        except BaseException:
            kernel32.TerminateProcess(info.hProcess, 1)
            kernel32.CloseHandle(info.hProcess)
            if "job" in locals():
                job.close()
            raise
        finally:
            kernel32.CloseHandle(info.hThread)
        return info.hProcess, int(info.dwProcessId)

    def read_chunk(self) -> Optional[str]:
        """One read; None when nothing more will ever arrive.

        The output pipe stays open while the pseudoconsole does, even after
        the process has exited, so a plain blocking ReadFile would never
        report the end: bytes are taken only when the pipe says some are
        waiting, and the end is the exited process with an empty pipe. The
        host can still be forwarding the last output just after the exit,
        so an empty pipe has to stay empty briefly before it is believed.
        """
        ctypes = self._ctypes
        kernel32 = ctypes.windll.kernel32
        empty_since: Optional[float] = None
        idle_since: Optional[float] = None
        while True:
            if self._closed:
                return None
            available = ctypes.wintypes.DWORD(0)
            if not kernel32.PeekNamedPipe(
                self._output_read, None, 0, None, ctypes.byref(available), None
            ):
                return None
            if available.value:
                buffer = ctypes.create_string_buffer(min(4096, available.value))
                read = ctypes.wintypes.DWORD(0)
                if (
                    not kernel32.ReadFile(
                        self._output_read, buffer, len(buffer), ctypes.byref(read), None
                    )
                    or not read.value
                ):
                    return None
                return self._decoder.decode(buffer.raw[: read.value])
            delay = 0.01
            if self.isalive():
                empty_since = None
                # A live but silent terminal (an agent thinking, a prompt
                # waiting) was polled 100 times a second for as long as
                # it sat there. Back off once it has been quiet a second;
                # arriving data is still picked up within 50 ms.
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since > 1.0:
                    delay = 0.05
            elif empty_since is None:
                empty_since = time.monotonic()
            elif time.monotonic() - empty_since > 0.5:
                return None
            time.sleep(delay)

    def write(self, text: str) -> None:
        if self._closed:
            raise OSError("the terminal's input pipe is closed")
        ctypes = self._ctypes
        kernel32 = ctypes.windll.kernel32
        data = text.encode("utf-8")
        view = (ctypes.c_char * len(data)).from_buffer_copy(data)
        written_total = 0
        while written_total < len(data):
            written = ctypes.wintypes.DWORD(0)
            if not kernel32.WriteFile(
                self._input_write,
                ctypes.byref(view, written_total),
                len(data) - written_total,
                ctypes.byref(written),
                None,
            ):
                raise OSError("the terminal's input pipe is closed")
            written_total += written.value

    def isalive(self) -> bool:
        if self._closed:
            return False
        kernel32 = self._ctypes.windll.kernel32
        code = self._ctypes.wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(self._process, self._ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE

    def terminate(self, force: bool = False) -> None:
        kernel32 = self._ctypes.windll.kernel32
        if self._job is not None:
            self._job.close()
        if self.isalive():
            kernel32.TerminateProcess(self._process, 1)

    def close(self, force: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        kernel32 = self._ctypes.windll.kernel32
        # Closing the pseudoconsole ends every process attached to it --
        # FreeBuff and the tools it started -- which terminate alone, aimed
        # only at the first process, would leave running.
        for handle in (self._input_write, self._output_read):
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
        # End owned descendants before closing the pseudoconsole, which can
        # otherwise wait forever for a child still writing to it.
        if self._job is not None:
            self._job.close()
        if self._pseudo:
            kernel32.ClosePseudoConsole(self._pseudo)
        if self._process is None:
            return
        if kernel32.WaitForSingleObject(self._process, 2000) == 258:  # WAIT_TIMEOUT
            kernel32.TerminateProcess(self._process, 1)
        kernel32.CloseHandle(self._process)


def _spawn_freebuff_pty(
    args: list[str], cwd: str, stream_ended: threading.Event
) -> tuple[FreebuffTerminal, Callable[[float], str]]:
    """Start FreeBuff under a pseudo-terminal, off screen, and read it.

    Returns the terminal and a call that yields whatever it has produced.
    Output is pumped into a queue by a thread of its own, so a terminal that
    started before anyone asked for its output keeps every byte of it.
    """
    if platform.system() == "Windows":
        # No console is claimed for this process, and none is needed: the
        # pseudoconsole is created directly (see _ConPtyTerminal), because
        # the library that used to create it allocated a console in this
        # process on the way. The watcher below stays as the safety net for
        # consoles FreeBuff's own tools can still raise.
        roots = {os.getpid()}

        def hide_terminal() -> None:
            # Poll hard while the terminal is starting, then slowly: the
            # tools FreeBuff itself starts can each raise a console of
            # their own, and any of them would take the reader's focus.
            # Tearing the terminal down raises one last console, after the
            # output has stopped, so this outlives the stream it guards.
            started = time.monotonic()
            grace_until: Optional[float] = None
            while True:
                now = time.monotonic()
                if stream_ended.is_set():
                    if grace_until is None:
                        grace_until = now + 5
                    elif now > grace_until:
                        return
                try:
                    process_utils.hide_console_windows(roots)
                except Exception:
                    # Never let this thread die: it is the only thing
                    # keeping the terminal off the screen for the whole run.
                    pass
                quick = now - started < 8 or grace_until is not None
                # 25 ms still catches a console within a frame or two of
                # it appearing; the old 5 ms cadence held a core busy in
                # EnumWindows sweeps for the whole fast phase.
                time.sleep(0.025 if quick else 0.25)

        threading.Thread(target=hide_terminal, daemon=True).start()

        try:
            pty = _ConPtyTerminal(args, cwd, subprocess_env(args[0]), rows=60, cols=180)
        except Exception:
            # No pump will ever set this, and the watcher above polls
            # EnumWindows until it is set.
            stream_ended.set()
            raise
        if pty.pid:
            roots.add(int(pty.pid))

        def read_one() -> Optional[str]:
            # ConPTY has no timeout of its own: the read blocks until either
            # bytes or the end of the terminal, and anything it raises is
            # the end of it.
            try:
                return pty.read_chunk()
            except Exception:
                return None

        return pty, _drain_to_queue(read_one, stream_ended)

    import pexpect

    child = pexpect.spawn(
        args[0],
        args[1:],
        cwd=cwd,
        encoding="utf-8",
        # Without this the terminal inherits whatever PATH the window was
        # started with. Launched from the macOS Dock that is launchd's, which
        # holds no Node, and FreeBuff's `#!/usr/bin/env node` shim dies before
        # it can paint anything.
        env=subprocess_env(args[0]),
        dimensions=(60, 180),
        timeout=0.25,
    )

    def read_one_posix() -> Optional[str]:
        # pexpect reads with a timeout of its own, so a quiet terminal is a
        # TIMEOUT rather than the end of one.
        try:
            return cast(str, child.read_nonblocking(4096, timeout=0.25))
        except pexpect.TIMEOUT:
            return ""
        except (pexpect.EOF, OSError):
            return None

    return child, _drain_to_queue(read_one_posix, stream_ended)


class _HiddenConsoleProcess:
    """The handle for a command given a console Windows created hidden.

    The wizard asks only whether the command is still running and tells it to
    stop, which are the same two questions it asks of a pseudo-terminal, so the
    two are interchangeable from there.
    """

    def __init__(self, proc: "subprocess.Popen[bytes]") -> None:
        self._proc = proc

    def isalive(self) -> bool:
        return self._proc.poll() is None

    def terminate(self, force: bool = False) -> None:
        process_utils.end_process_group(self._proc)

    def close(self, force: bool = False) -> None:
        try:
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _start_owned(*args, **kwargs):
    """Start a hidden console as an owned process.

    A seam of its own so the ownership rule can be tested without patching a
    name this module imported by value, which proves nothing about the call.
    """
    return owned_popen(*args, **kwargs)


def _spawn_hidden_console(args: list[str], cwd: str) -> object:
    """Start a command with a console of its own that is never shown.

    ``CREATE_NEW_CONSOLE`` gives it the real terminal that commands like these
    need, and the startup show flag has Windows create that console hidden
    rather than hiding it afterwards. The alternative, ``AllocConsole``, hands
    back a console that has already appeared and hiding it is the next thing
    that happens: a frame of a console window, titled with our own executable,
    in front of the user. A sign-in creates no window rather than hiding one.
    """
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    # Owned, so terminating it can end the tree rather than the wrapper: a
    # .cmd launcher's node child otherwise outlives it and keeps the sign-in
    # state nobody can see or release.
    proc = _start_owned(
        args,
        cwd=cwd,
        env=subprocess_env(args[0]),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
        close_fds=True,
    )
    # Belt and braces. Nothing was shown a moment ago, and nothing should be
    # shown while this runs either: a console window is the one thing this must
    # never put in front of the user. The same watcher FreeBuff runs hides
    # anything Windows raises anyway.
    roots = {os.getpid(), proc.pid}

    def watch() -> None:
        started = time.monotonic()
        while proc.poll() is None:
            try:
                process_utils.hide_console_windows(roots)
            except Exception:
                # Never let this thread die: it is the only thing keeping the
                # console off screen for the whole run.
                pass
            time.sleep(0.025 if time.monotonic() - started < 8 else 0.25)

    threading.Thread(target=watch, daemon=True).start()
    return _HiddenConsoleProcess(proc)


def spawn_hidden_terminal(args: list[str], cwd: str) -> object:
    """Run a command in a terminal nobody can see, and hand back its handle.

    A backend whose sign-in mounts a terminal UI needs a real terminal even
    when the user never looks at it: Command Code's login renders an Ink
    spinner that refuses to start without one. The alternative to a hidden
    terminal is a console window that appears, can take focus and screen-reader
    focus with it, and exists only to satisfy the UI.

    Windows gets a console created hidden (see :func:`_spawn_hidden_console`).
    Everywhere else there is no window to avoid, so it is the off-screen
    pseudo-terminal FreeBuff runs in, whose output is drained and thrown away:
    a terminal buffer nobody reads fills up and stops the command writing into
    it. `end_hidden_terminal` stops either.
    """
    if platform.system() == "Windows":
        return _spawn_hidden_console(args, cwd)
    ended = threading.Event()
    pty, read = _spawn_freebuff_pty(args, cwd, ended)

    def drain() -> None:
        while not ended.is_set():
            read(0.25)

    threading.Thread(target=drain, daemon=True).start()
    return pty


# A FreeBuff terminal takes seconds to reach its composer, and that wait is the
# largest part of how long a message takes to start answering. One is therefore
# started ahead of time, for the conversation the next message will most likely
# continue, and handed to the run that claims it.
_FREEBUFF_PREWARM_LOCK = threading.Lock()
_freebuff_prewarm: Optional[dict] = None
_freebuff_prewarm_generation = 0
# How long an unclaimed terminal is kept before a timer closes it. Long enough
# to cover thinking time between messages, short enough that a terminal started
# for a message that never comes does not sit there all day.
_FREEBUFF_PREWARM_TTL = 15 * 60


def end_hidden_terminal(pty: object) -> None:
    """End the terminal and give the pseudo-terminal itself back.

    Both calls are wanted, not the first one that works: `terminate` stops
    FreeBuff, `close` releases the handle the terminal was reached through.
    Returning after a successful terminate left that handle open every time.
    """
    for method, arguments in (("terminate", (True,)), ("close", (True,))):
        call = getattr(pty, method, None)
        if call is None:
            continue
        try:
            call(*arguments)
        except Exception:
            continue


def _end_prewarm(holding: dict) -> None:
    """Close a waiting terminal that will not be claimed. Not under the lock."""
    holding["ended"].set()
    timer = holding.get("timer")
    if timer is not None:
        timer.cancel()
    end_hidden_terminal(holding["pty"])


def discard_freebuff_prewarm() -> None:
    """Throw away any terminal held for the next message."""
    global _freebuff_prewarm, _freebuff_prewarm_generation
    with _FREEBUFF_PREWARM_LOCK:
        _freebuff_prewarm_generation += 1
        holding, _freebuff_prewarm = _freebuff_prewarm, None
    if holding is not None:
        _end_prewarm(holding)


def prewarm_freebuff(cwd: str, session_id: Optional[str], model: str, delay: float = 0.0) -> None:
    """Start the terminal the next FreeBuff message will use.

    Doing nothing here is always safe: a message that finds no terminal waiting,
    or one started for a different conversation, simply starts its own.
    """
    global _freebuff_prewarm_generation
    # Resolved the same way the turn resolves it, or a fresh install with no
    # recorded choice prewarms under "" and the turn, looking for the
    # preferred model, kills the waiting terminal every time.
    key = (
        os.path.abspath(cwd),
        session_id or "",
        model or _read_freebuff_choice() or FREEBUFF_PREFERRED_MODEL,
    )
    with _FREEBUFF_PREWARM_LOCK:
        holding = _freebuff_prewarm
        if holding is not None and holding["key"] == key and not holding["ended"].is_set():
            # One is already waiting for exactly this. Starting another would
            # throw away a terminal that has finished starting for one that has
            # not, which is the opposite of the point.
            return
        _freebuff_prewarm_generation += 1
        generation = _freebuff_prewarm_generation

    def work() -> None:
        if delay:
            time.sleep(delay)
        binary = find_backend_cli(BACKEND_FREEBUFF)
        if not binary:
            return
        # Claiming a terminal must wait for an in-progress spawn to publish
        # it. Otherwise Send launches a second CLI and chat discovery can
        # follow the idle background chat forever. Delayed starts must also
        # be invalidated when Send or a newer warmup has already taken over.
        with _FREEBUFF_PREWARM_LOCK:
            if generation != _freebuff_prewarm_generation:
                return
            launch(binary)

    def launch(binary: str) -> None:
        chat = _freebuff_resume_chat_path(cwd, session_id) if session_id else None
        if session_id and chat is None:
            return
        # FreeBuff reads the selected model once, at launch, and rewrites the
        # setting to its own recommendation after a turn. Applying the choice
        # before the terminal starts is what makes a waiting one usable.
        try:
            set_freebuff_model(key[2])
        except OSError:
            return
        args = [binary, "--cwd", cwd, "--trust-agents"]
        if session_id:
            args.extend(["--continue", session_id])
        # A new conversation's chat is created when the terminal starts, not
        # when it is given a message, so the record of which chats existed
        # beforehand has to be taken here. Taken later it would already include
        # this one, and the message would finish without ever learning the id of
        # the conversation it had just had.
        before = _freebuff_chat_dirs(cwd)
        log_offset = 0
        if chat is not None:
            try:
                log_offset = (chat / "log.jsonl").stat().st_size
            except OSError:
                pass
        ended = threading.Event()
        try:
            pty, read = _spawn_freebuff_pty(args, cwd, ended)
        except Exception:
            return
        holding: dict = {
            "key": key,
            "pty": pty,
            "read": read,
            "ended": ended,
            "before": before,
            "log_offset": log_offset,
            "expires": time.monotonic() + _FREEBUFF_PREWARM_TTL,
        }

        def expire() -> None:
            global _freebuff_prewarm
            with _FREEBUFF_PREWARM_LOCK:
                if _freebuff_prewarm is not holding:
                    # Claimed or replaced meanwhile; it is somebody else's now.
                    return
                _freebuff_prewarm = None
            _end_prewarm(holding)

        timer = threading.Timer(_FREEBUFF_PREWARM_TTL, expire)
        timer.daemon = True
        holding["timer"] = timer
        global _freebuff_prewarm
        stale, _freebuff_prewarm = _freebuff_prewarm, holding
        timer.start()
        if stale is not None:
            _end_prewarm(stale)

    threading.Thread(target=work, daemon=True).start()


def _take_freebuff_prewarm(cwd: str, session_id: Optional[str], model: str) -> Optional[dict]:
    """Claim the waiting terminal, if it is the one this message needs."""
    key = (os.path.abspath(cwd), session_id or "", model)
    global _freebuff_prewarm, _freebuff_prewarm_generation
    with _FREEBUFF_PREWARM_LOCK:
        _freebuff_prewarm_generation += 1
        holding = _freebuff_prewarm
        if holding is None:
            return None
        expired = time.monotonic() > holding["expires"]
        _freebuff_prewarm = None
        if holding["key"] == key and not expired and not holding["ended"].is_set():
            timer = holding.get("timer")
            if timer is not None:
                timer.cancel()
            return holding
    # Whatever is waiting cannot serve this message. Drop it rather than leave
    # a terminal running for a conversation nobody resumed.
    _end_prewarm(holding)
    return None


atexit.register(discard_freebuff_prewarm)


# ------------------------------------------------------------------------
# opencode
#
# opencode is driven through its own headless HTTP server rather than through
# one process per turn, because the server is the same surface its terminal
# interface talks to and is the only one that exposes everything BlindPilot
# needs from a provider: a streaming answer, steering a turn that is already
# running, answering a permission request, compaction, the catalog behind
# ``/model``, and the provider catalog behind ``/connect``. One server is
# started on first use and shared by every tab. It listens on the loopback
# interface behind a password generated for this run, so nothing else on the
# machine can drive it.

_OPENCODE_SERVER_LOCK = threading.Lock()
_opencode_server: Optional["OpencodeServer"] = None
_OPENCODE_CATALOG_LOCK = threading.Lock()
# One catalog per working directory: a project can pin its own model list.
_opencode_catalog_cache: dict[str, dict] = {}

# Effort levels are per-model in opencode — a model's "variants". The picker
# wants one list, so the levels every model offers are pooled and shown in the
# order a person would expect rather than the order they were discovered in.
_OPENCODE_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "max", "thinking")

# What BlindPilot's provider-neutral permission modes mean to opencode. A rule
# set is applied in order, so the wildcard comes first and the exceptions
# follow, which is the shape opencode's own agents use. "default" is absent on
# purpose: it means "whatever opencode itself is configured to do", so no rule
# set is sent at all.
_OPENCODE_PERMISSIONS: dict[str, list[dict[str, str]]] = {
    "acceptEdits": [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "bash", "pattern": "*", "action": "ask"},
        {"permission": "external_directory", "pattern": "*", "action": "ask"},
        {"permission": "doom_loop", "pattern": "*", "action": "ask"},
    ],
    "plan": [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "edit", "pattern": "*", "action": "deny"},
        {"permission": "bash", "pattern": "*", "action": "ask"},
        {"permission": "external_directory", "pattern": "*", "action": "deny"},
    ],
    "auto": [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "external_directory", "pattern": "*", "action": "ask"},
    ],
    "bypassPermissions": [
        {"permission": "*", "pattern": "*", "action": "allow"},
    ],
}


def _opencode_data_dir() -> Path:
    """Where opencode keeps its database and credentials, on every platform."""
    override = os.environ.get("OPENCODE_DATA")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "opencode"


def _free_port() -> int:
    """A port the operating system says is free right now."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _opencode_server_binary(binary: str) -> str:
    """Prefer opencode's own executable over an npm wrapper on Windows.

    Same reason Codex gets the same treatment: terminating a ``.cmd`` launcher
    does not terminate the native child it started, and a server nobody owns
    keeps both its port and its database open for the rest of the session.
    """
    if platform.system() != "Windows" or Path(binary).suffix.casefold() == ".exe":
        return binary
    candidate = Path(binary).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    return str(candidate) if candidate.is_file() else binary


def opencode_error_text(error: object, fallback: str) -> str:
    """A sentence worth speaking out of whatever the server or urllib raised."""
    import urllib.error

    if isinstance(error, urllib.error.HTTPError):
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("message"), str):
                return data["message"]
            for key in ("message", "error"):
                if isinstance(payload.get(key), str) and payload[key]:
                    return str(payload[key])
        return f"{fallback} (HTTP {error.code})"
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return data["message"]
        for key in ("message", "name"):
            if isinstance(error.get(key), str) and error[key]:
                return str(error[key])
    text = str(error).strip()
    return text or fallback


class OpencodeServer:
    """The one ``opencode serve`` process BlindPilot talks to."""

    def __init__(self, binary: str) -> None:
        import base64
        import secrets
        from collections import deque

        password = secrets.token_urlsafe(24)
        env = subprocess_env(binary)
        env["OPENCODE_SERVER_PASSWORD"] = password
        self._log: "deque[str]" = deque(maxlen=50)
        self._url = ""
        self._auth = "Basic " + base64.b64encode(f"opencode:{password}".encode("utf-8")).decode(
            "ascii"
        )
        # The probe in _free_port closes before this child can bind, so
        # another process may take the port in between; a second attempt on a
        # fresh port turns that race into a slower start instead of a failed
        # turn. The child exits promptly when the port is gone, so a failed
        # attempt does not wait out the 60 seconds.
        # shortcut: three attempts, enough for a busy machine; raise with the
        # wait above if start-up races stay common.
        for _attempt in range(3):
            self._listening = threading.Event()
            # Started from the home directory rather than any one project: one
            # server serves them all, and the ``directory`` query parameter on each
            # request is what says which project a call is about.
            self._proc = owned_popen(
                [binary, "serve", "--port", str(_free_port()), "--hostname", "127.0.0.1"],
                cwd=str(Path.home()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
                **process_utils.own_group_kwargs(),
                **process_utils.no_window_kwargs(),
            )
            threading.Thread(target=self._pump, daemon=True).start()
            self._listening.wait(60)
            if self._url:
                break
            self.stop()
        if not self._url:
            detail = "\n".join(list(self._log)[-5:]).strip()
            raise OSError(detail or "opencode's server did not start.")

    # The reader doubles as the thing that keeps the pipe from filling up, so
    # it runs for the life of the process rather than only until start-up.
    def _pump(self) -> None:
        stdout = self._proc.stdout
        if stdout is not None:
            for line in stdout:
                text = line.rstrip()
                if text:
                    self._log.append(text)
                if not self._url:
                    found = re.search(r"listening on (http://\S+)", text)
                    if found:
                        self._url = found.group(1).rstrip("/")
                        self._listening.set()
        # The process ended. Release anyone still waiting to be told the URL.
        self._listening.set()

    @property
    def base_url(self) -> str:
        return self._url

    def alive(self) -> bool:
        return self._proc.poll() is None

    def open(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: object = None,
        timeout: Optional[float] = 120,
    ):
        """The raw response, for callers that want to read it as it arrives."""
        import urllib.parse
        import urllib.request

        url = self._url + path
        query = {key: value for key, value in (params or {}).items() if value}
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", self._auth)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(request, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: object = None,
        timeout: Optional[float] = 120,
    ) -> object:
        with self.open(method, path, params, body, timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else None

    def stop(self) -> None:
        proc = self._proc
        try:
            if proc.poll() is None:
                if platform.system() != "Windows":
                    # It owns a SQLite database, so it is asked to close before
                    # it is made to. On Windows terminate() is TerminateProcess,
                    # which asks nothing and would leave its children behind, so
                    # there the tree is ended in one go below.
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                process_utils.end_process_group(proc, timeout=5)
        finally:
            # Released whether the process was stopped just now or had already
            # exited on its own: a dead server's stdout pipe is still a handle
            # until something closes it.
            process_utils._close_process_pipes(proc)


def opencode_server() -> OpencodeServer:
    """The shared server, started on first use and reused from then on."""
    global _opencode_server
    with _OPENCODE_SERVER_LOCK:
        running = _opencode_server
        if running is not None and running.alive():
            return running
        binary = find_backend_cli(BACKEND_OPENCODE)
        if not binary:
            raise OSError("opencode is not installed. Run: npm install -g opencode-ai")
        _opencode_server = OpencodeServer(_opencode_server_binary(binary))
        return _opencode_server


def stop_opencode_server() -> None:
    """Shut the shared server down — on exit, and before an update replaces it."""
    global _opencode_server
    with _OPENCODE_SERVER_LOCK:
        running, _opencode_server = _opencode_server, None
    if running is not None:
        running.stop()


atexit.register(stop_opencode_server)


def _opencode_catalog(cwd: Optional[str] = None, refresh: bool = False) -> dict:
    """opencode's providers, their models, and the defaults it would pick.

    Asked per directory, because a project's own ``opencode.json`` can pin a
    model or turn providers off, and cached per directory for the same reason.
    Caching matters: the catalog is hundreds of models across nearly two
    hundred providers, and the picker is opened far more often than a
    project's set of providers changes.
    """
    key = cwd or ""
    with _OPENCODE_CATALOG_LOCK:
        cached = _opencode_catalog_cache.get(key)
    if cached is not None and not refresh:
        return cached
    server = opencode_server()
    params = {"directory": cwd} if cwd else None
    providers = server.request("GET", "/config/providers", params=params, timeout=60)
    config = server.request("GET", "/config", params=params, timeout=60)
    commands = server.request("GET", "/command", params=params, timeout=60)
    providers = providers if isinstance(providers, dict) else {}
    config = config if isinstance(config, dict) else {}
    catalog = {
        "providers": providers.get("providers") or [],
        "default": providers.get("default") or {},
        "model": config.get("model") or "",
        "commands": _opencode_command_list(commands),
    }
    with _OPENCODE_CATALOG_LOCK:
        _opencode_catalog_cache[key] = catalog
    return catalog


def _opencode_command_list(payload: object) -> list[tuple[str, str]]:
    """opencode's slash commands, as (name, description).

    Only the commands proper: opencode lists installed skills here as well, and
    a picker a person arrows through is worth keeping to the length of what
    they typed a slash to find.
    """
    commands: list[tuple[str, str]] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict) or entry.get("source") != "command":
            continue
        name = str(entry.get("name") or "")
        if name:
            commands.append((name, str(entry.get("description") or "")))
    commands.sort()
    return commands


def opencode_commands(cwd: Optional[str] = None) -> list[tuple[str, str]]:
    """The commands read for this directory, or none if it has not been read.

    Deliberately never asks the server: the slash picker opens on a key press,
    and the catalog is already read in the background whenever opencode is the
    chosen backend.
    """
    with _OPENCODE_CATALOG_LOCK:
        catalog = _opencode_catalog_cache.get(cwd or "")
    commands = catalog.get("commands") if catalog else None
    return list(commands) if isinstance(commands, list) else []


def _opencode_models_from_cli() -> list[str]:
    """The catalog as the CLI prints it, for when the server will not start."""
    binary = find_backend_cli(BACKEND_OPENCODE)
    if not binary:
        return []
    try:
        result = subprocess.run(
            [binary, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=subprocess_env(binary),
            **process_utils.no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    models: list[str] = []
    for line in (result.stdout or "").splitlines():
        name = line.strip()
        if name and "/" in name and " " not in name and name not in models:
            models.append(name)
    return models


def opencode_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """Every model opencode can reach, the effort levels they offer, and the
    model it would use if BlindPilot named none."""
    try:
        catalog = _opencode_catalog(cwd)
    except (OSError, ValueError) as exc:
        models = _opencode_models_from_cli()
        if not models:
            return [], [], "", "", opencode_error_text(exc, "opencode's model list is unavailable.")
        return (
            models,
            list(_OPENCODE_EFFORT_ORDER),
            "",
            "",
            "Could not reach opencode's server; showing the list its CLI prints.",
        )

    models = []
    efforts: list[str] = []
    for provider in catalog["providers"]:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        entries = provider.get("models")
        if not provider_id or not isinstance(entries, dict):
            continue
        for model_id, model in sorted(entries.items()):
            models.append(f"{provider_id}/{model_id}")
            variants = model.get("variants") if isinstance(model, dict) else None
            if isinstance(variants, dict):
                for variant in variants:
                    if variant not in efforts:
                        efforts.append(variant)

    known = [effort for effort in _OPENCODE_EFFORT_ORDER if effort in efforts]
    efforts = known + sorted(effort for effort in efforts if effort not in known)
    if not models:
        return [], efforts, "", "", "opencode has no connected providers yet. Type /connect."
    return models, efforts, opencode_default_model(catalog=catalog), "", ""


def opencode_default_model(cwd: Optional[str] = None, catalog: Optional[dict] = None) -> str:
    """The ``provider/model`` opencode itself would run, or "" if it has none."""
    try:
        catalog = catalog if catalog is not None else _opencode_catalog(cwd)
    except (OSError, ValueError):
        return ""
    configured = catalog.get("model")
    if isinstance(configured, str) and "/" in configured:
        return configured
    defaults = catalog.get("default") or {}
    for provider in catalog.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        model_id = defaults.get(provider_id)
        if provider_id and isinstance(model_id, str) and model_id:
            return f"{provider_id}/{model_id}"
    return ""


def opencode_split_model(model: str) -> tuple[str, str]:
    """``provider/model`` as the two halves the server asks for separately."""
    provider, _, name = (model or "").partition("/")
    return (provider.strip(), name.strip()) if name.strip() else ("", "")


def opencode_model_efforts(model: str, cwd: Optional[str] = None) -> list[str]:
    """The effort levels this one model offers, so an unusable one is dropped.

    opencode rejects a variant a model does not define, and the picker offers
    the levels pooled across every model, so the choice has to be checked
    against the model it is about to be sent with.
    """
    provider_id, model_id = opencode_split_model(model)
    if not provider_id:
        return []
    try:
        catalog = _opencode_catalog(cwd)
    except (OSError, ValueError):
        return []
    for provider in catalog["providers"]:
        if isinstance(provider, dict) and provider.get("id") == provider_id:
            entry = (provider.get("models") or {}).get(model_id)
            variants = entry.get("variants") if isinstance(entry, dict) else None
            return list(variants) if isinstance(variants, dict) else []
    return []


# ---- /connect: the providers, and the credentials that reach them ----


def opencode_providers() -> tuple[list[tuple[str, str]], set[str], str]:
    """Every provider opencode knows, and which of them are already connected.

    This is what its ``/connect`` command offers. The ones already reachable
    come first, so the list opens on what is actually in use.
    """
    try:
        server = opencode_server()
        payload = server.request("GET", "/provider", timeout=60)
    except (OSError, ValueError) as exc:
        return [], set(), opencode_error_text(exc, "Could not read opencode's provider list.")
    if not isinstance(payload, dict):
        return [], set(), "opencode returned no provider list."
    connected = {str(name) for name in (payload.get("connected") or [])}
    everything: list[tuple[str, str]] = []
    for provider in payload.get("all") or []:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "")
        if provider_id:
            everything.append((provider_id, str(provider.get("name") or provider_id)))
    everything.sort(key=lambda item: (item[0] not in connected, item[1].casefold()))
    return everything, connected, ""


def opencode_auth_methods(provider_id: str) -> list[dict]:
    """How this provider can be signed in to.

    Providers with nothing special to say are absent from opencode's own list;
    for them the only way in is an API key, which is what the fallback says.
    """
    try:
        server = opencode_server()
        payload = server.request("GET", "/provider/auth", timeout=60)
    except (OSError, ValueError):
        payload = None
    methods = payload.get(provider_id) if isinstance(payload, dict) else None
    if isinstance(methods, list) and methods:
        return [method for method in methods if isinstance(method, dict)]
    return [{"type": "api", "label": "Manually enter API key"}]


def _opencode_write(
    method: str, path: str, body: Optional[dict], timeout: int, failure: str
) -> str:
    """Change what opencode has stored. Returns "" on success, else the error.

    Every one of these writes leaves the cached provider list stale, so the
    invalidation belongs with the write rather than with each caller: forget
    it once and the sign-in screen goes on showing an account that has just
    been disconnected.
    """
    try:
        opencode_server().request(method, path, body=body, timeout=timeout)
    except (OSError, ValueError) as exc:
        return opencode_error_text(exc, failure)
    invalidate_backend_cache(BACKEND_OPENCODE)
    return ""


def opencode_connect_api_key(provider_id: str, key: str, metadata: Optional[dict] = None) -> str:
    """Store an API key for a provider. Returns "" on success, else the error."""
    body: dict = {"type": "api", "key": key}
    if metadata:
        body["metadata"] = {str(k): str(v) for k, v in metadata.items() if v}
    return _opencode_write(
        "PUT", f"/auth/{provider_id}", body, 60, f"Could not connect {provider_id}."
    )


def opencode_disconnect(provider_id: str) -> str:
    """Forget a provider's credentials. Returns "" on success, else the error."""
    return _opencode_write(
        "DELETE", f"/auth/{provider_id}", None, 60, f"Could not disconnect {provider_id}."
    )


def opencode_oauth_start(
    provider_id: str, method: int, inputs: Optional[dict] = None
) -> tuple[dict, str]:
    """Begin a browser sign-in. Returns (authorization, error).

    The authorization carries the URL to open, instructions worth reading out,
    and whether the provider finishes on its own ("auto") or hands back a code
    the user has to paste ("code").
    """
    body: dict = {"method": method}
    if inputs:
        body["inputs"] = {str(k): str(v) for k, v in inputs.items() if v}
    try:
        payload = opencode_server().request(
            "POST", f"/provider/{provider_id}/oauth/authorize", body=body, timeout=120
        )
    except (OSError, ValueError) as exc:
        return {}, opencode_error_text(exc, f"Could not start sign-in for {provider_id}.")
    return (payload if isinstance(payload, dict) else {}), ""


def opencode_oauth_finish(provider_id: str, method: int, code: str = "") -> str:
    """Complete a browser sign-in. Returns "" on success, else the error."""
    body: dict = {"method": method}
    if code:
        body["code"] = code
    return _opencode_write(
        "POST",
        f"/provider/{provider_id}/oauth/callback",
        body,
        300,
        f"Could not finish signing in to {provider_id}.",
    )


def _opencode_tool_label(name: str, arguments: object) -> str:
    """One spoken line for a tool that has just started."""
    values = arguments if isinstance(arguments, dict) else {}
    detail = ""
    for key in ("command", "filePath", "path", "pattern", "query", "url", "description"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            detail = " ".join(value.split())
            break
    verbs = {
        "bash": "Running",
        "edit": "Editing",
        "write": "Writing",
        "read": "Reading",
        "glob": "Finding files",
        "grep": "Searching",
        "list": "Listing",
        "webfetch": "Fetching",
        "websearch": "Searching the web",
        "task": "Delegating",
    }
    verb = verbs.get(name, f"Using {name}")
    return f"{verb}: {detail}" if detail else verb


# An assistant message the provider refuses on replay poisons the whole
# conversation: opencode rebuilds the request from its stored history every
# step, so once one malformed message is in, every later turn fails with the
# same 400 until the message is gone. DeepSeek-class providers report it as
# "content or tool_calls must be set"; the paired complaint about tool results
# left dangling is the same break in a different coat.
_POISON_HISTORY_RE = re.compile(
    r"Invalid assistant message|tool_calls['?]? must be followed by tool messages"
    r"|missing in assistant tool call message|reasoning_content.*must be passed back",
    re.IGNORECASE,
)


def _opencode_entry_info(entry: dict) -> dict:
    """The `info` block of a history entry, or the entry itself.

    opencode nests message metadata under `info` in some shapes and inlines it
    in others. Written inline as a conditional this called `.get` twice, so the
    guard tested one value and the code then used another - harmless in
    practice, and exactly the shape that stops being harmless after an edit.
    """
    found = entry.get("info")
    return found if isinstance(found, dict) else entry


def _poison_history_error(text: str) -> bool:
    """Whether a backend error says the stored history itself was refused."""
    return bool(_POISON_HISTORY_RE.search(text or ""))


def _opencode_questions(raw: object) -> tuple[questions.Question, ...]:
    """Read a question.asked event into BlindPilot's own question shape.

    opencode's own flags are `multiple` for more than one answer and `custom`
    for a typed one. Only `multiple` is trusted as written: its tool tells the
    model not to offer an "Other" of its own because the client adds one, so a
    typed answer is offered whether or not `custom` was set.
    """
    return questions._questions(raw, multi_key="multiple")


# Magic bytes to media types for attached pictures. The server has no upload
# endpoint, so bytes travel inline with the prompt as data: URLs.
_OPENCODE_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# Anything past this is named by path rather than sent as bytes: the model
# gets a file it can open instead of a payload that dwarfs the prompt.
_OPENCODE_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


def _opencode_file_part(path: str) -> Optional[dict]:
    """One attached file as a prompt file part, or None to name it in text.

    Pictures travel as data: URLs with their media type (measured: a 1x1 PNG
    round-tripped through prompt_async and was stored back with its filename),
    and so does anything that reads as text; anything else -- an unreadable
    file, a picture past the cap, bytes no model would read -- is named by its
    path in the prompt text instead.
    """
    import base64

    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > _OPENCODE_MAX_ATTACHMENT_BYTES:
        return None
    mime = ""
    for magic, kind in _OPENCODE_IMAGE_MAGIC:
        if raw.startswith(magic):
            mime = kind
            break
    if not mime and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        mime = "image/webp"
    if not mime:
        try:
            raw.decode("utf-8")
        except ValueError:
            return None
        mime = "text/plain"
    return {
        "type": "file",
        "mime": mime,
        "filename": Path(path).name,
        "url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
    }


def _opencode_file_name(value: object) -> str:
    """The bare filename of a path the server wrote, whichever slashes it used."""
    text = str(value or "")
    return text.replace("\\", "/").rsplit("/", 1)[-1] if text else ""


class AgentWorker(Protocol):
    """The part of a backend's worker that the window actually drives.

    All four workers are threads, but a thread is not what the window wants
    from them: it wants to start a turn, ask whether it is still running, stop
    it, and wait for it to let go. Saying that here is what lets the window
    hold whichever worker the backend chose without knowing which one it is —
    and lets `cancel` be a method the code is allowed to call, rather than one
    a reader has to take on trust.
    """

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def steer(self, text: str) -> bool: ...

    def join(self, timeout: Optional[float] = None) -> None: ...

    def cancel(self) -> None: ...


# Kept for the old worker_class construction path while the window migrates to
# backend_workers.create_worker.
AgentWorkerFactory = Callable[..., AgentWorker]


# ----- What every backend must answer -----
#
# The app asks the same three account questions of every backend: is it signed
# in, what can it say about the account, and how much allowance is left. Worker
# selection now belongs to backend_workers, alongside its typed request.


@dataclass(frozen=True)
class BackendAdapter:
    """How one backend answers the questions asked of every backend."""

    auth_ok: Callable[[int], bool]
    account_lines: Callable[[str, int], list[str]]
    usage_lines: Callable[[str, int], list[str]]


def _cli_signed_in(backend: str, args: tuple[str, ...]) -> Callable[[int], bool]:
    """Signed in when this CLI subcommand exits successfully."""

    def check(timeout: int) -> bool:
        binary = find_backend_cli(backend)
        if not binary:
            return False
        proc = subprocess.run(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=subprocess_env(binary),
            **process_utils.no_window_kwargs(),
        )
        return proc.returncode == 0

    return check


def _stored_signed_in(backend: str, read: Callable[[], bool]) -> Callable[[int], bool]:
    """Signed in when the CLI is installed and its stored credential says so."""

    def check(_timeout: int) -> bool:
        return bool(find_backend_cli(backend)) and read()

    return check


def _usage_lines(windows: Sequence[UsageWindow]) -> list[str]:
    return [line for line in map(_usage_window_line, _ordered_windows(windows)) if line]


def _hermes_auth_ok(timeout: int) -> bool:
    from hermes_backend import hermes_auth_ok

    return hermes_auth_ok(timeout=max(timeout, 25))


def _museai_auth_ok(timeout: int) -> bool:
    from museai_backend import museai_auth_ok

    return museai_auth_ok(timeout=max(timeout, 20))


def _commandcode_auth_ok(timeout: int) -> bool:
    # `command-code status` is a Node start-up plus a request; the default
    # twelve seconds has been enough, but give it room like Hermes.
    from commandcode_backend import commandcode_auth_ok

    return commandcode_auth_ok(timeout=max(timeout, 25))


def _gemini_auth_ok(_timeout: int) -> bool:
    from google_backend import gemini_auth_ok

    return gemini_auth_ok()


def _antigravity_auth_ok(timeout: int) -> bool:
    from google_backend import antigravity_auth_ok

    return antigravity_auth_ok(timeout=max(timeout, 30))


def _copilot_auth_ok(timeout: int) -> bool:
    from copilot_backend import copilot_auth_ok

    return copilot_auth_ok(timeout=max(timeout, 20))


def _museai_account_lines() -> list[str]:
    from museai_backend import museai_account_lines

    return museai_account_lines()


def _commandcode_account_lines() -> list[str]:
    from commandcode_backend import commandcode_account_lines

    return commandcode_account_lines()


def _gemini_account_lines() -> list[str]:
    from google_backend import gemini_account_lines

    return gemini_account_lines()


def _antigravity_account_lines() -> list[str]:
    from google_backend import antigravity_account_lines

    return antigravity_account_lines()


def _copilot_account_lines() -> list[str]:
    from copilot_backend import copilot_account_lines

    return copilot_account_lines()


ADAPTERS: dict[str, BackendAdapter] = {
    BACKEND_CLAUDE: BackendAdapter(
        auth_ok=_cli_signed_in(BACKEND_CLAUDE, ("auth", "status")),
        account_lines=lambda binary, timeout: _claude_account_lines(
            *_probe_backend(binary, ["auth", "status"], timeout)
        ),
        usage_lines=lambda binary, timeout: _usage_lines(
            _claude_usage_windows(_claude_usage_payload(binary, timeout))
        ),
    ),
    BACKEND_CODEX: BackendAdapter(
        auth_ok=_cli_signed_in(BACKEND_CODEX, ("login", "status")),
        account_lines=lambda binary, timeout: _codex_account_lines(
            *_probe_backend(binary, ["login", "status"], timeout)
        ),
        usage_lines=lambda binary, timeout: _usage_lines(
            _codex_usage_windows(_codex_usage_result(binary, timeout))
        ),
    ),
    BACKEND_FREEBUFF: BackendAdapter(
        auth_ok=_stored_signed_in(
            BACKEND_FREEBUFF, lambda: _freebuff_signed_in(_freebuff_account())
        ),
        account_lines=lambda _binary, _timeout: _freebuff_account_lines(),
        usage_lines=lambda _binary, timeout: _usage_lines(
            _freebuff_usage_windows(_freebuff_usage_payload(timeout))
        ),
    ),
    BACKEND_OPENCODE: BackendAdapter(
        auth_ok=_stored_signed_in(BACKEND_OPENCODE, lambda: bool(_opencode_providers())),
        account_lines=lambda _binary, _timeout: _opencode_account_lines(),
        usage_lines=lambda _binary, _timeout: [],
    ),
    BACKEND_HERMES: BackendAdapter(
        auth_ok=_hermes_auth_ok,
        account_lines=lambda _binary, _timeout: _hermes_account_lines(),
        usage_lines=lambda _binary, _timeout: _usage_lines(
            _hermes_usage_windows(_hermes_pool_payload())
        ),
    ),
    BACKEND_MUSE: BackendAdapter(
        auth_ok=lambda _timeout: _muse_signed_in_checked(),
        account_lines=lambda _binary, _timeout: _muse_account_lines(),
        usage_lines=lambda _binary, _timeout: _usage_lines(
            _muse_usage_windows(_muse_usage_payload())
        ),
    ),
    BACKEND_MUSEAI: BackendAdapter(
        auth_ok=_museai_auth_ok,
        account_lines=lambda _binary, _timeout: _museai_account_lines(),
        usage_lines=lambda _binary, _timeout: [],
    ),
    BACKEND_COMMANDCODE: BackendAdapter(
        auth_ok=_commandcode_auth_ok,
        account_lines=lambda _binary, _timeout: _commandcode_account_lines(),
        usage_lines=lambda _binary, _timeout: [],
    ),
    BACKEND_GEMINI: BackendAdapter(
        auth_ok=_gemini_auth_ok,
        account_lines=lambda _binary, _timeout: _gemini_account_lines(),
        usage_lines=lambda _binary, _timeout: [],
    ),
    BACKEND_ANTIGRAVITY: BackendAdapter(
        auth_ok=_antigravity_auth_ok,
        account_lines=lambda _binary, _timeout: _antigravity_account_lines(),
        usage_lines=lambda _binary, _timeout: [],
    ),
    BACKEND_COPILOT: BackendAdapter(
        auth_ok=_copilot_auth_ok,
        account_lines=lambda _binary, _timeout: _copilot_account_lines(),
        usage_lines=lambda _binary, _timeout: [],
    ),
}


def _adapter(backend: str) -> BackendAdapter:
    """This backend's answers, or a KeyError naming the one that is missing."""
    backend = normalize_backend(backend)
    adapter = ADAPTERS.get(backend)
    if adapter is None:
        raise KeyError(f"no backend adapter for {backend}")
    return adapter


def worker_class(backend: str, claude_worker: AgentWorkerFactory) -> AgentWorkerFactory:
    """Legacy selector for callers that still pass constructor keywords."""
    from backend_workers import worker_type

    return worker_type(normalize_backend(backend), claude_worker=claude_worker)


def __getattr__(name: str):
    """Temporary imports for callers still using agent_backends workers."""
    if name == "CodexWorker":
        from backend_workers.codex_worker import CodexWorker

        return CodexWorker
    if name == "FreebuffWorker":
        from backend_workers.freebuff_worker import FreebuffWorker

        return FreebuffWorker
    if name == "OpencodeWorker":
        from backend_workers.opencode_worker import OpencodeWorker

        return OpencodeWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
