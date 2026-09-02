"""What the setup wizard says when a backend is not there.

Three things a screen-reader user heard on one press of "Install Hermes",
captured from NVDA: a promise ("Installing Hermes. This usually takes under a
minute."), a lie ("npm could not be installed" on a machine that has npm), and
a splice ("install Hermes yourself using See https://..."). The first two came
from the wizard offering Install for a backend BlindPilot cannot install at
all — Hermes ships its own installer and is not on npm — and the third from a
sentence fragment spliced into a sentence.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app
from agent_backends import BACKEND_CODEX, BACKEND_HERMES

wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        return wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")


class _Widget:
    def __init__(self):
        self.text = ""
        self.shown = False
        self.enabled = True

    def SetLabel(self, text):
        self.text = text

    def GetLabel(self):
        return self.text

    def Show(self):
        self.shown = True

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
        self._pages = [_Widget(), _Widget()]

    def _find_selected_cli(self):
        return None

    def _selected_install_argv(self):
        return app.SetupWizard._selected_install_argv(self)

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


def test_hermes_is_never_offered_an_install_button(monkeypatch, wx_app):
    """Hermes is not on npm; Install would run nothing and then lie about it."""
    wizard = _Wizard(BACKEND_HERMES)
    monkeypatch.setattr(app, "_find_npm", lambda: "C:/npm/npm.cmd")

    app.SetupWizard._check_npm_backend_cli(wizard)

    assert not wizard._cli_install_btn.shown
    assert wizard._cli_status.text == "Hermes was not found."
    assert "https://hermes-agent.nousresearch.com/docs" in wizard._cli_detail.text


def test_hermes_install_backend_never_reaches_npm_and_never_lies(monkeypatch):
    """install_backend refuses a backend it cannot install, for its own
    reason, before any npm machinery is consulted."""
    lines: list[str] = []
    monkeypatch.setattr(app, "_find_npm", lambda: "C:/npm/npm.cmd")

    with pytest.raises(NotImplementedError) as excinfo:
        app.install_backend(BACKEND_HERMES, lines.append)

    assert "npm" not in str(excinfo.value)
    assert lines == []


def test_the_failed_install_message_is_a_sentence_for_every_backend():
    """Hermes' install_command is a fragment ("See https://..."), and the old
    message spliced it after "using", which read as "yourself using See".
    The failure message must carry the command without splicing it into a
    sentence, for every backend the wizard offers — and a backend whose
    command is a sentence is never re-fenced into a splice either."""
    for backend in app.BACKENDS:
        message = app._install_failure_message(backend)
        command = app.BACKENDS[backend].install_command
        assert f"using {command}" not in message
        assert command in message


def test_installing_a_backend_blindpilot_cannot_install_says_so_up_front(monkeypatch, wx_app):
    """The promise and the failure must not be spoken in the same breath:
    pressing Install for a backend BlindPilot cannot install refuses at once
    instead of promising a minute it does not have."""
    spoken = _said(monkeypatch)
    wizard = _Wizard(BACKEND_HERMES)
    monkeypatch.setattr(
        app, "install_backend", lambda _b, _log: (_ for _ in ()).throw(AssertionError("reached"))
    )

    app.SetupWizard._install_cli(wizard)

    assert not any("This usually takes under a minute" in text for text in spoken)
    assert any("cannot install" in text for text in spoken)
