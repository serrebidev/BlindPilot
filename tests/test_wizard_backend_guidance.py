"""What the setup wizard says when a backend is not there.

Captured from NVDA on one press of "Install Hermes": a promise, a lie about
npm on a machine that has npm, and a fragment spliced into a sentence. The
root was that the only install machinery was npm's, so a backend that does
not come from npm — Hermes ships its own installer — was offered an install
that runs nothing and then reports npm as the reason.

Hermes is now installed through its own official installer, so every backend
the registry holds is installable in the wizard on Windows, macOS and Linux.
These tests pin what the user hears in every branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import blindpilot_app as app
from agent_backends import BACKEND_CODEX, BACKEND_COMMANDCODE, BACKEND_HERMES

wx = pytest.importorskip("wx")


class _Widget:
    def __init__(self):
        self.text = ""
        self.shown = False
        self.enabled = True

    def SetLabel(self, text):
        self.text = text

    def GetLabel(self):
        return self.text

    def Show(self, show=True):
        self.shown = show

    def Hide(self):
        self.shown = False

    def Enable(self, enabled=True):
        self.enabled = enabled

    def Disable(self):
        self.enabled = False

    def SetValue(self, text):
        self.text = text

    def Wrap(self, _width):
        pass

    def Layout(self):
        pass


class _Wizard:
    """A wizard as far as the check code is concerned: the attributes it
    touches, none of the chrome it would need on screen."""

    def __init__(self, backend):
        self.backend = backend
        self._backend_path = None
        self._step = 1
        self._cli_status = _Widget()
        self._cli_detail = _Widget()
        self._cli_log = _Widget()
        self._cli_install_btn = _Widget()
        self._cli_update_btn = _Widget()
        self._cli_path_btn = _Widget()
        self._cli_check_btn = _Widget()
        self._next_btn = _Widget()
        self._back_btn = _Widget()
        self._pages = [_Widget(), _Widget(), _Widget()]
        self._signin_btn = _Widget()
        self._already_btn = _Widget()
        self._open_page_btn = _Widget()
        self._signin_status = _Widget()
        self._signin_intro = _Widget()
        self._welcome_text = _Widget()
        self._done_text = _Widget()
        self._login = None
        self._hidden_login = None

    def _find_selected_cli(self):
        return None

    def _selected_install_argv(self):
        return app.SetupWizard._selected_install_argv(self)

    def _run_login(self):
        return app.SetupWizard._run_login(self)

    def _run_hidden_login(self, binary):
        return app.SetupWizard._run_hidden_login(self, binary)

    def _stop_hidden_login(self):
        return app.SetupWizard._stop_hidden_login(self)

    def Layout(self):
        pass

    def __bool__(self):
        return True


def _said(monkeypatch):
    spoken: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: spoken.append(text))
    return spoken


def test_an_npm_backend_without_node_promises_node_not_npm(monkeypatch, wx_app):
    """Regression pin for an npm backend on a clean machine: the guidance is
    about Node.js, and the npm lie never appears anywhere."""
    wizard = _Wizard(BACKEND_CODEX)
    monkeypatch.setattr(app, "_find_npm", lambda: None)

    app.SetupWizard._check_npm_backend_cli(wizard)

    assert wizard._cli_install_btn.shown
    assert "Node.js" in wizard._cli_detail.text
    assert "npm could not be installed" not in wizard._cli_detail.text


def test_hermes_is_offered_an_install_of_its_own(monkeypatch, wx_app):
    """Hermes installs through its official installer now, so the wizard
    offers it — with Hermes' instructions, not npm's."""
    wizard = _Wizard(BACKEND_HERMES)
    monkeypatch.setattr(app, "_find_npm", lambda: "C:/npm/npm.cmd")
    monkeypatch.setattr(app, "_hermes_install_argv", lambda: ["powershell.exe", "-Command", "x"])

    app.SetupWizard._check_npm_backend_cli(wizard)

    assert wizard._cli_install_btn.shown
    assert wizard._cli_status.text == "Hermes is not installed."
    assert "official" in wizard._cli_detail.text
    assert "npm" not in wizard._cli_detail.text
    assert "https://hermes-agent.nousresearch.com/docs" in wizard._cli_detail.text


def test_hermes_without_prerequisites_still_gets_its_own_guidance(monkeypatch, wx_app):
    """No PowerShell (or no curl): the message names what is missing and the
    manual instructions, never npm."""
    wizard = _Wizard(BACKEND_HERMES)
    monkeypatch.setattr(app, "_find_npm", lambda: "C:/npm/npm.cmd")
    monkeypatch.setattr(app, "_hermes_install_argv", lambda: None)

    app.SetupWizard._check_npm_backend_cli(wizard)

    assert not wizard._cli_install_btn.shown
    assert wizard._cli_status.text == "Hermes was not found."
    assert "https://hermes-agent.nousresearch.com/docs" in wizard._cli_detail.text
    assert "npm" not in wizard._cli_detail.text


def test_hermes_install_backend_goes_through_its_own_installer(monkeypatch):
    """install_backend routes Hermes to install_hermes — never npm."""
    lines: list[str] = []
    monkeypatch.setattr(
        app,
        "install_hermes",
        lambda log: log("installing through the official installer") or "hermes.exe",
    )

    assert app.install_backend(BACKEND_HERMES, lines.append) == "hermes.exe"
    assert any("official installer" in line for line in lines)


def test_the_failed_install_message_is_a_sentence_for_every_backend():
    """Hermes' install_command is a fragment ("See https://..."), and the old
    message spliced it after "using", which read as "yourself using See".
    The failure message must carry the command without splicing it into a
    sentence, for every backend the registry holds."""
    for backend in app.BACKENDS:
        message = app._install_failure_message(backend)
        command = app.BACKENDS[backend].install_command
        assert f"using {command}" not in message
        assert command in message


def test_installing_hermes_promises_nothing_until_it_is_running(monkeypatch, wx_app):
    """The captured burst: the promise and the failures in one breath. The
    promise now belongs to the install that is actually going to run."""
    spoken = _said(monkeypatch)
    wizard = _Wizard(BACKEND_HERMES)
    monkeypatch.setattr(app, "_hermes_install_argv", lambda: None)

    app.SetupWizard._install_cli(wizard)

    assert not any("This usually takes under a minute" in text for text in spoken)
    assert any("PowerShell" in text or "curl" in text for text in spoken)


class _InlineThread:
    """Runs the wizard's worker on this thread, so the test sees what it did."""

    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def test_command_code_signs_in_through_a_hidden_terminal(monkeypatch, wx_app):
    """Command Code's login needs a terminal only because its Ink UI refuses to
    start without one. It gets a terminal nobody can see, rather than a console
    window that exists for nothing the user reads or answers.
    """
    spawned: list[tuple[list[str], str]] = []
    ended: list[object] = []
    calls: list[tuple[bool, str]] = []

    class _Finished:
        def isalive(self):
            return False

    terminal = _Finished()
    monkeypatch.setattr(
        app,
        "spawn_hidden_terminal",
        lambda args, cwd: spawned.append((args, cwd)) or terminal,
    )
    monkeypatch.setattr(app, "end_hidden_terminal", ended.append)
    monkeypatch.setattr(app, "backend_auth_ok", lambda _backend: True)
    monkeypatch.setattr(app, "announce", lambda *a, **k: None)
    monkeypatch.setattr(app.threading, "Thread", _InlineThread)
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn, *a: fn(*a))

    wizard = _Wizard(BACKEND_COMMANDCODE)
    wizard._backend_path = "C:/npm/command-code.cmd"
    wizard._on_login_done = lambda ok, failure: calls.append((ok, failure))

    app.SetupWizard._do_login(wizard)

    assert spawned == [(["C:/npm/command-code.cmd", "login"], str(Path.home()))]
    assert ended == [terminal]
    # The CLI is the only thing that knows whether the browser round-trip
    # landed, so the wizard reports what it says rather than what it guessed.
    assert calls == [(True, "")]
    assert wizard._login is None


def test_command_code_sign_in_copy_is_about_an_account(monkeypatch, wx_app):
    """Command Code signs in to an account in a browser. Its copy must not
    promise a terminal window to answer, and must not borrow Hermes' wording
    about a provider and model, which describes a different thing entirely."""
    wizard = _Wizard(BACKEND_COMMANDCODE)
    app.SetupWizard._refresh_backend_copy(wizard)
    text = wizard._signin_intro.text
    assert "provider and model" not in text
    assert "terminal window" not in text
    assert "browser" in text
    assert "command-code login" in text


def test_hermes_sign_in_copy_still_names_its_provider_picker(monkeypatch, wx_app):
    """Hermes' "login" is its model picker, so its copy stays as it was."""
    wizard = _Wizard(BACKEND_HERMES)
    app.SetupWizard._refresh_backend_copy(wizard)
    assert "provider and model" in wizard._signin_intro.text
    assert "terminal window" in wizard._signin_intro.text
