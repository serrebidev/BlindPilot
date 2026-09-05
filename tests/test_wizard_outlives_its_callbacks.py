"""The setup wizard, escaped while it is still installing.

Installing a backend takes a minute or so. It runs on a thread and reports
progress with `wx.CallAfter`, and the wizard leaves Cancel enabled and Escape
working throughout — deliberately, since being unable to leave would be worse.
Both owners destroy the dialog the moment `ShowModal` returns, and nothing
tells the install thread to stop. `install_backend` keeps downloading and keeps
calling back.

Every one of those callbacks then touches widgets whose C++ objects are gone,
which raises `RuntimeError` rather than doing nothing, out of an event handler
with no turn and no dialog to attribute it to. On first run there is not even a
main window yet.

The wizard already knows the answer: every sign-in callback opens with
`if not self:`. The install and update ones did not.
"""

from __future__ import annotations

import blindpilot_app as app


class _Dead:
    """A destroyed wxPython window: still a Python object, falsey, and every
    C++ method on it raises."""

    def __bool__(self):
        return False

    def __getattr__(self, name):
        raise RuntimeError(f"wrapped C/C++ object has been deleted (asked for {name})")


def _quiet(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))
    return said


def test_installer_output_arriving_after_the_wizard_closed_is_dropped(monkeypatch):
    said = _quiet(monkeypatch)

    app.SetupWizard._cli_log_line(_Dead(), "Downloading...")

    assert said == []


def test_the_install_finishing_after_the_wizard_closed_is_dropped(monkeypatch):
    said = _quiet(monkeypatch)

    app.SetupWizard._on_install_done(_Dead(), "C:/somewhere/claude.exe")

    assert said == []


def test_the_update_finishing_after_the_wizard_closed_is_dropped(monkeypatch):
    said = _quiet(monkeypatch)

    app.SetupWizard._on_update_done(_Dead(), True)

    assert said == []


def test_a_failed_install_finishing_afterwards_is_also_dropped(monkeypatch):
    """The failure path touches more widgets than the success path."""
    said = _quiet(monkeypatch)

    app.SetupWizard._on_install_done(_Dead(), None)

    assert said == []


def test_the_deferred_checks_are_guarded_too(monkeypatch):
    """`_show_step` queues these, so they can land one iteration after a close."""
    app.SetupWizard._check_cli(_Dead())
    app.SetupWizard._check_signin(_Dead())


class _Label:
    def __init__(self):
        self.text = ""

    def SetLabel(self, text):
        self.text = text

    def GetLabel(self):
        return self.text


class _Page:
    def Layout(self):
        pass


def _signin_wizard(monkeypatch):
    stub = type("WizardStub", (), {"Layout": lambda self: None})()
    stub.backend = app.BACKEND_CLAUDE
    stub._login = None
    stub._open_page_btn = type("Btn", (), {"Enable": lambda self, on=True: None})()
    stub._find_selected_cli = lambda: "C:/somewhere/claude.exe"
    stub._signin_status = _Label()
    stub._pages = [_Page(), _Page(), _Page()]
    stub._show_signin_status = lambda text: app.SetupWizard._show_signin_status(stub, text)
    stub._on_signin_checked = lambda backend, ok: app.SetupWizard._on_signin_checked(
        stub, backend, ok
    )
    return stub


def test_the_sign_in_probe_runs_off_the_wizards_own_thread(monkeypatch):
    """`backend_auth_ok` runs a CLI with a 12 to 25 second timeout. On the GUI
    thread that froze the wizard, and the screen reader, for the duration."""
    said = _quiet(monkeypatch)
    probed_on: list[str] = []

    class _Thread:
        def __init__(self, target, daemon=False, name=""):
            self._target = target

        def start(self):
            probed_on.append("thread")
            self._target()

    monkeypatch.setattr(app.threading, "Thread", _Thread)
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn, *a: fn(*a))
    monkeypatch.setattr(app, "backend_auth_ok", lambda backend: probed_on.append("probe") or True)
    wizard = _signin_wizard(monkeypatch)

    app.SetupWizard._check_signin(wizard)

    assert probed_on == ["thread", "probe"], "the probe ran before a thread was started"
    assert "signed in" in wizard._signin_status.text
    assert said[-1] == wizard._signin_status.text


def test_a_probe_for_a_backend_no_longer_chosen_is_ignored(monkeypatch):
    said = _quiet(monkeypatch)
    wizard = _signin_wizard(monkeypatch)
    wizard.backend = app.BACKEND_CODEX

    app.SetupWizard._on_signin_checked(wizard, app.BACKEND_CLAUDE, True)

    assert said == []
