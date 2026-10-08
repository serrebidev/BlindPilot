"""Command Code backend for BlindPilot.

Command Code (https://commandcode.ai) is a terminal coding agent installed
with ``npm install -g command-code``. Its package ships four bin names --
``cmd``, ``cmdc``, ``command-code`` and ``commandcode`` -- and this adapter
drives the ``command-code`` one on purpose: a Windows process that looks up
``cmd`` finds ``C:\\Windows\\System32\\cmd.exe`` first, which is the command
interpreter, not the agent.

It does not speak Claude Code's stream-json protocol. Its non-interactive
surface is its own: ``command-code -p --output-format json`` writes one event
object per line and a single final ``result`` line, and a conversation is
carried between runs with ``--resume <session id>`` (measured at 1.53.1, the
shapes this file reads). Discovery, authentication, the model catalog and the
stored session locations live here so ``agent_backends`` stays
provider-neutral; the turn itself is in ``commandcode_worker``.

CLI probes go through ``agent_backends._probe_backend`` and binary discovery
through ``agent_backends.find_backend_cli`` -- resolved at call time, not
imported into this module's namespace -- so the suite's "every backend"
tests, which patch those two in ``agent_backends``, fence this adapter off
from the machine it runs on exactly like the others.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

import agent_backends
from agent_backends import BACKEND_COMMANDCODE

# The reasoning levels the catalog advertises. Measured at 1.69.0: the CLI
# answers `--effort bogus` with "Supported: low, medium, xhigh", and both
# `high` and `max` are refused as unknown, on the default model and on a named
# one alike. Whether a model takes any effort at all is still per-model --
# Kimi K2.7 Code answers that it has no adjustable reasoning effort -- and a
# level sent to such a model ends the run, so the worker retries without it.
COMMANDCODE_EFFORTS = ("low", "medium", "xhigh")

# How long a short CLI probe may take. Command Code is a Node program, so
# every check pays a start-up; it is quick once running, and the wizard's
# sign-in step calls this while a dialog is on screen.
CLI_PROBE_TIMEOUT = 30


def commandcode_home() -> Path:
    """Where Command Code keeps its configuration and sessions."""
    return Path.home() / ".commandcode"


def commandcode_config() -> dict:
    """The CLI's own config.json, read without starting it.

    It holds the model, provider and reasoning effort last used, which is how
    the picker knows what is selected now. The shape is measured at 1.53.1:
    ``{"model": "...", "provider": "...", "reasoningEffort": {"<model>": "..."}}``.
    """
    try:
        payload = json.loads((commandcode_home() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def commandcode_credentials() -> Optional[dict]:
    """The account Command Code stored at sign-in, or None when there is none."""
    try:
        payload = json.loads((commandcode_home() / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload else None


def _probe(binary: str, args: list[str], timeout: int) -> tuple[Optional[int], str]:
    """One short CLI command, through the shared probe the suite can fence."""
    return agent_backends._probe_backend(binary, args, timeout)


def _says_signed_out(text: str) -> bool:
    lowered = text.casefold()
    return any(
        phrase in lowered
        for phrase in ("not authenticated", "not logged in", "no account", "signed out")
    )


def _after(text: str, marker: str) -> str:
    """The value on the line carrying *marker*, for a report that is prose."""
    for line in text.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip(" :\t")
    return ""


def commandcode_auth_ok(timeout: int = 12) -> bool:
    """Best-effort, non-interactive sign-in check.

    Command Code answers this with ``command-code status``, whose exit code is
    the signal (its documented exit 3 is "not authenticated") and whose output
    names the account otherwise. A release that stops answering in a shape
    this reads falls back to the credential file, so a check never reports a
    signed-in user as signed out.
    """
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if binary:
        code, text = _probe(binary, ["status"], timeout)
        if code == 0 and not _says_signed_out(text):
            return True
    return commandcode_credentials() is not None


def commandcode_account_lines() -> list[str]:
    """The sign-in report, as the lines ``/status`` prints them."""
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if binary:
        code, text = _probe(binary, ["status"], 20)
        if code == 0 and not _says_signed_out(text):
            lines = ["Signed in: yes"]
            account = _after(text, "Authenticated as")
            provider = _after(text, "Provider")
            if account:
                lines.append(f"Account: {account}")
            if provider:
                lines.append(f"Provider: {provider}")
            return lines
    credentials = commandcode_credentials()
    if credentials:
        name = str(credentials.get("userName") or "").strip()
        return ["Signed in: yes"] + ([f"Account: {name}"] if name else [])
    return ["Signed in: no"]


# --------------------------------------------------------------------------
# What bypass does not cover
# --------------------------------------------------------------------------


def _settings_layer_paths(cwd: Optional[str]) -> list[Path]:
    """The layers Command Code merges permission rules from, in its own order.

    Measured at 1.54.0: the person's own settings, then the project's, then the
    project's local overrides. This is settings.json, not the config.json
    above -- one holds the model and the theme, the other the permission rules.
    """
    project = Path(cwd) if cwd else Path.cwd()
    return [
        commandcode_home() / "settings.json",
        project / ".commandcode" / "settings.json",
        project / ".commandcode" / "settings.local.json",
    ]


def _permissions_block(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    block = payload.get("permissions")
    return block if isinstance(block, dict) else {}


def _rule_count(block: dict, key: str) -> int:
    rules = block.get(key)
    return len(rules) if isinstance(rules, list) else 0


def commandcode_bypass_limits(cwd: Optional[str] = None) -> list[str]:
    """What a bypass turn will still be refused, read from the CLI's settings.

    "Bypass permissions" does not mean the same thing to Command Code as it
    does to the window. Its permission check consults the settings before it
    consults the mode, so a ``permissions.deny`` or ``permissions.ask`` rule
    refuses in bypass exactly as it does in default, and
    ``permissions.disableBypass`` turns the whole flag off with one line on
    stderr that a windowed run never shows anybody. Each of those is something
    a person who chose bypass would want said out loud before the turn rather
    than discovered as a refusal in the middle of one.
    """
    denies = 0
    asks = 0
    disabled = False
    for path in _settings_layer_paths(cwd):
        block = _permissions_block(path)
        if not block:
            continue
        # Rule lists merge across layers rather than replacing each other, so
        # the counts add up the way Command Code's own merge does.
        denies += _rule_count(block, "deny")
        asks += _rule_count(block, "ask")
        # A later layer can switch it back on, so the last word wins.
        if "disableBypass" in block:
            flag = block.get("disableBypass")
            disabled = flag is True or flag == "disable"
    notes: list[str] = []
    if disabled:
        notes.append(
            "Command Code's settings switch bypass off (permissions.disableBypass), so this "
            "turn runs under its normal rules and anything that would ask is refused instead."
        )
    if denies:
        notes.append(
            f"Command Code still applies {denies} permissions.deny "
            f"rule{'' if denies == 1 else 's'} in bypass."
        )
    if asks:
        notes.append(
            f"Command Code still applies {asks} permissions.ask "
            f"rule{'' if asks == 1 else 's'} in bypass, and a headless turn cannot answer one."
        )
    return notes


# --------------------------------------------------------------------------
# Model catalog
# --------------------------------------------------------------------------


def _looks_like_section(line: str) -> bool:
    """Whether a line is one of the catalog's provider headings.

    The headings are plain words ("Open Source", "Anthropic", "xAI"); every
    other non-model line in the output carries punctuation the headings do
    not ("Available models · 70 models", the "Pass the full id..." note, the
    "Docs:" line), so those are told apart by shape rather than by a list that
    would go stale.
    """
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z &-]{1,30}", line.strip()))


def _models_from_catalog(text: str) -> list[str]:
    """Read the model ids out of ``command-code --list-models``.

    Measured layout: provider headings, a blank line, then ``<id>  <description>``
    rows. A row is only taken once a heading has been seen, so the banner
    ("Available models · 70 models") and the trailing "Docs:" link - the only
    other lines with the two-space gap - cannot be mistaken for a model. The
    heading is not undone by the blank line that follows it, which is why a
    heading is only ever replaced by another heading.
    """
    models: list[str] = []
    in_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = re.match(r"^(\S+)\s{2,}\S", line)
        if match:
            name = match.group(1)
            if in_section and not name.endswith(":") and name not in models:
                models.append(name)
            continue
        if _looks_like_section(line):
            in_section = True
    return models


def commandcode_model_options(
    cwd: Optional[str] = None,
) -> tuple[list[str], list[str], str, str, str]:
    """The picker's answer: (models, efforts, current model, current effort, error)."""
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if not binary:
        return [], [], "", "", "Command Code was not found."
    code, text = _probe(binary, ["--list-models"], CLI_PROBE_TIMEOUT)
    models = _models_from_catalog(text) if code == 0 else []
    config = commandcode_config()
    current = str(config.get("model") or "").strip()
    efforts = config.get("reasoningEffort")
    current_effort = str(efforts.get(current) or "").strip() if isinstance(efforts, dict) else ""
    if not models:
        return [], [], current, current_effort, "Could not read the model list from Command Code."
    return models, list(COMMANDCODE_EFFORTS), current, current_effort, ""


# --------------------------------------------------------------------------
# Slash commands: the agent's own, and the console's
# --------------------------------------------------------------------------
#
# Command Code's console has two kinds of slash command. Skills, mod commands
# and a few built-ins (/compact, /loop, /peek) are the agent's: `command-code
# acp` advertises them (`available_commands_update`, measured at 1.79.1) and a
# headless `-p` run carries them out, so BlindPilot sends them as they are.
# The rest (/context, /usage, /status, /export, /todos, /changelog, ...) exist
# only in the console's own interface. BlindPilot runs those in that console,
# off screen, on the same conversation, and reads the screen back.

_AGENT_COMMANDS: dict[str, tuple[float, list[tuple[str, str]]]] = {}
_AGENT_COMMANDS_TTL = 600.0
_AGENT_COMMANDS_LOCK = threading.Lock()


def commandcode_agent_commands(cwd: Optional[str], wait: bool = False) -> list[tuple[str, str]]:
    """The slash commands Command Code's agent offers in `cwd`, as (/name, description).

    Asking costs a Command Code start-up, so the answer is cached per folder
    for ten minutes. Without `wait` an uncached folder is asked in the
    background and the cached (possibly empty) list comes back at once.
    """
    key = str(cwd or "")
    with _AGENT_COMMANDS_LOCK:
        hit = _AGENT_COMMANDS.get(key)
    if hit and time.monotonic() - hit[0] < _AGENT_COMMANDS_TTL:
        return hit[1]
    if wait:
        return _fetch_agent_commands(key)
    threading.Thread(target=_fetch_agent_commands, args=(key,), daemon=True).start()
    return hit[1] if hit else []


def _fetch_agent_commands(cwd: str) -> list[tuple[str, str]]:
    import queue
    import subprocess

    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if not binary:
        return []
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [binary, "acp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd or None,
            env=agent_backends.subprocess_env(binary),
            **agent_backends.no_window_kwargs(),
        )
    except (OSError, ValueError):
        return []
    lines: "queue.Queue[str]" = queue.Queue()

    def pump() -> None:
        for line in proc.stdout or []:
            lines.put(line)

    threading.Thread(target=pump, daemon=True).start()

    def send(request_id: int, method: str, params: dict) -> None:
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError, AssertionError):
            pass

    commands: Optional[list[tuple[str, str]]] = None
    session = ""
    send(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
    send(2, "session/new", {"cwd": cwd or str(Path.home()), "mcpServers": []})
    deadline = time.monotonic() + 45
    while commands is None and time.monotonic() < deadline:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("id") == 2:
            session = str((message.get("result") or {}).get("sessionId") or "")
        update = (message.get("params") or {}).get("update") or {}
        if update.get("sessionUpdate") == "available_commands_update":
            commands = [
                ("/" + str(c.get("name")), str(c.get("description") or "").strip())
                for c in update.get("availableCommands") or []
                if isinstance(c, dict) and c.get("name")
            ]
    if session:
        send(3, "session/close", {"sessionId": session})
    try:
        proc.terminate()
    except OSError:
        pass
    found = commands or []
    if commands is not None:
        with _AGENT_COMMANDS_LOCK:
            _AGENT_COMMANDS[cwd] = (time.monotonic(), found)
    return found


# What the console paints around every command's output: its banner, the
# input box, hints and footers. None of it is the command's answer.
_CONSOLE_CHROME = re.compile(
    r"^(# Command Code v|# models:|# [~A-Za-z].*[\\/]|Press Esc to return|❯ Ask your question|"
    r"\? for shortcuts|"
    r"» permission|◼ Ran \d+ session start hook|◼ .* is free and uses shared capacity)"
)
_MENU_HINT = re.compile(r"↑/↓|Enter (to )?(confirm|select)|esc to (cancel|close|go back)", re.I)


def _console_text(lines: list[str]) -> str:
    kept: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or "███" in stripped or set(stripped) <= set("─━│┃╭╮╰╯┌┐└┘ "):
            continue
        if _CONSOLE_CHROME.match(stripped):
            continue
        if not kept or kept[-1] != stripped:
            kept.append(stripped)
    return "\n".join(kept)


def commandcode_console_command(
    command: str, cwd: str, session_id: str = "", quiet_seconds: float = 4.0, limit: float = 45.0
) -> str:
    """Run one console slash command in Command Code's own interface, off
    screen, on the tab's conversation, and return what it showed.

    The console is started with the command as its first message (it runs a
    leading slash command the same way as one typed into it) and closed once
    the screen has been still for `quiet_seconds`. A command that opens a
    menu shows the menu's text; choosing from it needs the console itself.
    """
    import pyte

    from freebuff_screen import repaired_history_screen

    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if not binary:
        return "Command Code is not installed."
    args = [binary, "-t", "--skip-onboarding"]
    if session_id:
        args += ["--resume", session_id]
    args.append(command)
    ended = threading.Event()
    terminal, read = agent_backends._spawn_freebuff_pty(args, cwd, ended)
    screen = repaired_history_screen(180, 60, history=4000)
    stream = pyte.Stream(screen)
    started = time.monotonic()
    last_change = started
    answered_language = False
    try:
        while time.monotonic() - started < limit and not ended.is_set():
            data = read(0.25)
            now = time.monotonic()
            if data:
                stream.feed(data)
                last_change = now
                if not answered_language and "Choose a language" in data:
                    # First-run language question: keep English.
                    terminal.write("\r")
                    answered_language = True
            elif now - last_change >= quiet_seconds and now - started > quiet_seconds + 2:
                break
    finally:
        ended.set()
        try:
            terminal.terminate(force=True)
        except Exception:  # noqa: BLE001 - already gone
            pass
    history = [
        "".join(cell.data for _, cell in sorted(line.items())) for line in screen.history.top
    ]
    text = _console_text(history + list(screen.display))
    if _MENU_HINT.search(text):
        text += (
            "\n\nThis command opens a menu in Command Code's console. The choices are listed "
            "above; to pick one, use BlindPilot's own control for it or run the command in "
            "Command Code's console."
        )
    return text or f"Command Code showed nothing for {command}."


_CONSOLE_COMMANDS: list[tuple[str, str]] = []


def commandcode_console_commands() -> list[tuple[str, str]]:
    """The console's built-in slash commands, from `command-code --help`."""
    if _CONSOLE_COMMANDS:
        return _CONSOLE_COMMANDS
    binary = agent_backends.find_backend_cli(BACKEND_COMMANDCODE)
    if not binary:
        return []
    code, text = _probe(binary, ["--help"], CLI_PROBE_TIMEOUT)
    if code != 0:
        return []
    found: list[tuple[str, str]] = []
    in_section = False
    for line in text.splitlines():
        if line.strip() == "Slash Commands":
            in_section = True
            continue
        if in_section and line.strip() and not line.startswith(" "):
            break
        match = re.match(r"^\s+(/[\w:-]+)(.*?)\s{2,}(\S.*)$", line) if in_section else None
        if match:
            found.append((match.group(1), match.group(3).strip()))
    _CONSOLE_COMMANDS.extend(found)
    return found
