"""Unit tests for signing in to a backend from a window that has no console.

BlindPilot runs the provider CLIs with no terminal attached, so everything the
sign-in needs — the address to visit, the code the page hands back, the reason
it failed — has to be pulled out of the CLI's own output and put in front of
the user. These tests drive :class:`BackendLogin` against scripted transcripts
taken verbatim from the real CLIs, so a change in how their output is read is
caught here rather than by a user staring at a wizard that never moves.

Nothing here launches a real CLI or opens a real browser.

Run from the project root:

    python -m pytest tests/ -q
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blindpilot_app  # noqa: E402
from blindpilot_app import BackendLogin, _first_login_url, _login_speech  # noqa: E402
from agent_backends import BACKENDS  # noqa: E402


# Verbatim from `claude auth login`, including the code prompt written with no
# newline after it — the detail that made this look like it had frozen.
CLAUDE_OUTPUT = (
    "Opening browser to sign in…\n"
    "If the browser didn't open, visit: "
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a&state=abc\n"
    "Paste code here if prompted > "
)

# Verbatim from `codex login`. The callback server is announced before the page
# anyone actually signs in on.
CODEX_OUTPUT = (
    "Starting local login server on http://localhost:1455.\n"
    "If your browser did not open, navigate to this URL to authenticate:\n"
    "\n"
    "https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_EM\n"
    "\n"
    "On a remote or headless machine? Use `codex login --device-auth` instead.\n"
)

# Verbatim from `freebuff login`, which prints the address and refuses to open it.
FREEBUFF_OUTPUT = (
    "\nFreebuff Login\n\n"
    "Generating login URL...\n\n"
    "Open this URL in your browser to log in:\n\n"
    "https://freebuff.com/login?auth_code=9OAXDN8Ps\n\n"
    "Please open the URL above manually to complete login.\n\n"
    "Waiting for login...\n"
)


class _Pipe:
    """A text stream fed from the test, read one character at a time."""

    def __init__(self):
        self._chars: queue.Queue = queue.Queue()

    def feed(self, text: str) -> None:
        for char in text:
            self._chars.put(char)

    def close(self) -> None:
        self._chars.put("")

    def read(self, _count: int = 1) -> str:
        return self._chars.get()


class _Stdin:
    def __init__(self):
        self.written: list[str] = []

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        pass


class _FakeProc:
    """Just enough of Popen for the reader, with the ending under test control."""

    def __init__(self):
        self.stdout = _Pipe()
        self.stdin = _Stdin()
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.close()

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self.stdout.close()


class _Run:
    """A sign-in running on its own thread, with everything it said recorded."""

    def __init__(self, backend: str, *, timeout: float = 5.0):
        self.proc = _FakeProc()
        self.argv: list = []
        self.opened: list[str] = []
        self.progress: list[str] = []
        self.urls: list[tuple[str, bool]] = []
        self.prompts: list[str] = []
        self.rc = None
        self._prompted = threading.Event()
        self.login = BackendLogin(
            backend,
            f"/usr/bin/{backend}",
            timeout=timeout,
            opener=self._open,
            popen=self._popen,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _popen(self, args, **_kwargs) -> Any:
        self.argv = list(args)
        return self.proc

    def _open(self, url: str) -> bool:
        self.opened.append(url)
        return True

    def _on_prompt(self, prompt: str, _url: str) -> None:
        self.prompts.append(prompt)
        self._prompted.set()

    def _run(self) -> None:
        self.rc = self.login.run(
            self.progress.append,
            lambda url, opened: self.urls.append((url, opened)),
            self._on_prompt,
        )

    def wait_for_prompt(self, timeout: float = 5.0) -> bool:
        return self._prompted.wait(timeout)

    def finish(self, code: int = 0):
        self.proc.finish(code)
        self._thread.join(timeout=5)
        return self.rc

    def join(self):
        self._thread.join(timeout=10)
        return self.rc


def test_claude_signs_in_with_a_command_line_not_a_slash_command():
    """`claude /login` is typed inside a session; it is not a command line.

    Run as one it opened a terminal UI BlindPilot has no console for, so the
    Sign In button sat there until it timed out and no browser ever appeared.
    """
    assert BACKENDS["claude"].login_args == ("auth", "login")
    run = _Run("claude")
    run.proc.stdout.feed(CLAUDE_OUTPUT)
    assert run.wait_for_prompt()
    run.finish(0)
    assert run.argv == ["/usr/bin/claude", "auth", "login"]


def test_claude_code_prompt_is_seen_before_its_missing_newline():
    """The prompt ends in "> " with no newline; a line reader never sees it."""
    run = _Run("claude")
    run.proc.stdout.feed(CLAUDE_OUTPUT)
    assert run.wait_for_prompt()
    assert run.prompts == ["Paste code here if prompted >"]
    run.login.submit_code("abc#123")
    run.proc.stdout.feed("Login successful.\n")
    assert run.finish(0) == 0
    assert run.proc.stdin.written == ["abc#123\n"]
    assert "Login successful." in run.progress


def test_claude_sign_in_page_is_left_to_the_cli_that_already_opened_it():
    """Two tabs on the same authorization is worse than one."""
    run = _Run("claude")
    run.proc.stdout.feed(CLAUDE_OUTPUT)
    assert run.wait_for_prompt()
    run.finish(0)
    assert run.opened == []
    assert run.urls[0][0].startswith("https://claude.com/cai/oauth/authorize")
    assert run.urls[0][1] is False


def test_open_sign_in_page_opens_it_anyway_when_asked_by_hand():
    """The wizard's button is for when the CLI's own browser never arrived."""
    run = _Run("claude")
    run.proc.stdout.feed(CLAUDE_OUTPUT)
    assert run.wait_for_prompt()
    run.finish(0)
    assert run.login.open_page() is True
    assert run.opened == [run.login.url]


def test_codex_login_ignores_the_callback_server_it_announces_first():
    """http://localhost:1455 is where the browser comes back, not where it goes."""
    run = _Run("codex")
    run.proc.stdout.feed(CODEX_OUTPUT)
    assert run.finish(0) == 0
    assert [url for url, _ in run.urls] == [
        "https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_EM"
    ]
    assert run.opened == []


def test_freebuff_login_page_is_opened_by_blindpilot():
    """FreeBuff prints the address and tells you to open it. Nobody could."""
    run = _Run("freebuff")
    run.proc.stdout.feed(FREEBUFF_OUTPUT)
    assert run.finish(0) == 0
    assert run.opened == ["https://freebuff.com/login?auth_code=9OAXDN8Ps"]
    assert run.urls == [("https://freebuff.com/login?auth_code=9OAXDN8Ps", True)]


def test_a_sign_in_that_went_wrong_says_why():
    """Silence and "it did not complete" are the same sentence to a user."""
    run = _Run("claude")
    run.proc.stdout.feed(CLAUDE_OUTPUT)
    assert run.wait_for_prompt()
    run.login.submit_code("wrong")
    run.proc.stdout.feed("Login failed: Request failed with status code 400\n")
    run.finish(1)
    assert run.login.failure == "Login failed: Request failed with status code 400"


def test_a_cli_that_never_finishes_is_stopped_rather_than_left_running():
    run = _Run("freebuff", timeout=0.4)
    run.proc.stdout.feed(FREEBUFF_OUTPUT)
    assert run.join() == -1
    assert run.proc.killed


def test_a_cli_that_asks_for_a_code_forever_is_stopped():
    """A re-prompt loop must not keep reopening the dialog for good."""
    run = _Run("claude", timeout=5.0)
    for _ in range(4):
        run.proc.stdout.feed("Paste code here if prompted > ")
    assert run.join() == -1
    assert len(run.prompts) == 3
    assert run.proc.killed


def test_a_cli_that_cannot_be_started_is_reported_not_awaited():
    def refuse(*_args, **_kwargs):
        raise OSError("no such file")

    login = BackendLogin("codex", "/nowhere/codex", popen=refuse)
    assert login.run(lambda _t: None, lambda _u, _o: None, lambda _p, _u: None) == -2


def test_loopback_addresses_are_never_the_sign_in_page():
    assert _first_login_url("Starting local login server on http://localhost:1455.") == ""
    assert _first_login_url("listening on http://127.0.0.1:8976/callback") == ""
    assert _first_login_url("Open this URL: https://freebuff.com/login?token=abc123.") == (
        "https://freebuff.com/login?token=abc123"
    )
    assert _first_login_url("Waiting for login") == ""


def test_progress_is_said_without_the_terminal_around_it():
    assert _login_speech("\x1b[2GOpening browser\x1b[0m") == "Opening browser"
    assert _login_speech("Opening browser to sign in�") == "Opening browser to sign in"
    assert _login_speech("   ") == ""


def test_browser_that_will_not_open_is_not_reported_as_opened():
    login = BackendLogin("freebuff", "/usr/bin/freebuff", opener=lambda _u: False)
    login.url = "https://freebuff.com/login?auth_code=x"
    assert login.open_page() is False

    def explode(_url):
        raise RuntimeError("no browser here")

    boom = BackendLogin("freebuff", "/usr/bin/freebuff", opener=explode)
    boom.url = "https://freebuff.com/login?auth_code=x"
    assert boom.open_page() is False


def test_nothing_to_open_before_the_cli_has_said_anything():
    assert BackendLogin("claude", "/usr/bin/claude").open_page() is False


def test_module_alias_still_exposes_the_login_runner():
    assert blindpilot_app.BackendLogin is BackendLogin


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
