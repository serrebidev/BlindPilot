"""Regression tests for non-interactive startup checks."""

from __future__ import annotations

import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _startup_check_flag_does_not_leak():
    """`main()` sets `_STARTUP_CHECK` for the life of the process, which is
    right for a check that then exits and wrong for a test file where the next
    test is an ordinary launch. Put back whatever it was."""
    import blindpilot_app

    before = blindpilot_app._STARTUP_CHECK
    try:
        yield
    finally:
        blindpilot_app._STARTUP_CHECK = before


def test_linux_announcements_are_sent_to_orca_without_moving_focus(monkeypatch):
    import blindpilot_app

    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app.platform, "system", lambda: "Linux")
    monkeypatch.setattr(blindpilot_app, "_SPEAKER", None)
    monkeypatch.setattr(blindpilot_app, "_linux_announce", lambda text: spoken.append(text) is None)

    blindpilot_app.announce("Agent response received")

    assert spoken == ["Agent response received"]


def test_linux_announcement_from_worker_is_queued_on_the_gui_thread(monkeypatch):
    import blindpilot_app

    queued: list[tuple] = []
    monkeypatch.setattr(blindpilot_app.wx, "GetApp", lambda: object())
    monkeypatch.setattr(blindpilot_app.wx, "IsMainThread", lambda: False)
    monkeypatch.setattr(blindpilot_app.wx, "CallAfter", lambda *args: queued.append(args))
    monkeypatch.setattr(
        blindpilot_app,
        "_linux_native_announce",
        lambda _text: (_ for _ in ()).throw(AssertionError("called off the GUI thread")),
    )

    assert blindpilot_app._linux_announce("Finished") is True
    assert queued == [(blindpilot_app._linux_announce, "Finished")]


def test_gui_startup_smoke_skips_first_run_wizard(monkeypatch):
    import blindpilot_app

    events: list[object] = []

    class FakeApp:
        def MainLoop(self) -> None:
            events.append("main-loop")

        def SetAppName(self, name: str) -> None:
            events.append(("app-name", name))

        def SetAppDisplayName(self, name: str) -> None:
            events.append(("app-display-name", name))

    class FakeFrame:
        def __init__(self, *, initial_cwd: str) -> None:
            events.append(("frame", initial_cwd))

        def Show(self) -> None:
            events.append("show")

        def Layout(self) -> None:
            events.append("layout")

        def Raise(self) -> None:
            events.append("raise")

        def Close(self) -> None:
            events.append("close")

    def fail_if_wizard_opens(*_args, **_kwargs):
        raise AssertionError("the first-run wizard opened during a GUI smoke test")

    saved: list[dict] = []
    monkeypatch.setattr(blindpilot_app.sys, "argv", ["blind_pilot.py", "--startup-gui-smoke"])
    monkeypatch.setattr(blindpilot_app, "_load_config", dict)
    # Startup moves an old config onto full auto, and that writes. Without this
    # the test would write to the config of whoever ran it.
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda cfg: saved.append(dict(cfg)))
    monkeypatch.setattr(blindpilot_app, "SetupWizard", fail_if_wizard_opens)
    monkeypatch.setattr(blindpilot_app, "MainFrame", FakeFrame)
    monkeypatch.setattr(blindpilot_app, "_bring_to_front", lambda: events.append("front"))
    monkeypatch.setattr(blindpilot_app.wx, "App", lambda _redirect: FakeApp())

    def call_later(delay: int, callback) -> None:
        events.append(("later", delay))
        callback()

    monkeypatch.setattr(blindpilot_app.wx, "CallLater", call_later)

    assert blindpilot_app.main() == 0
    assert ("later", 1500) in events
    assert "close" in events
    assert "main-loop" in events
    # Every backend starts fully automatic, including on an upgrade.
    assert saved and saved[0]["permission_mode"] == "bypassPermissions"


def test_nothing_started_inherits_a_path_into_the_install_folder(monkeypatch, tmp_path):
    """A child that can find our libraries will hold them open past our exit.

    PyInstaller's pywin32 hook puts the packaged DLL folder on PATH, and every
    process BlindPilot starts inherits it. Those processes go on loading the
    Visual C++ runtime and pythoncom out of the install folder long after
    BlindPilot has closed, and the installer then refuses to replace files that
    are in use — the update that reported nothing but "code 5".
    """
    import blindpilot_app

    bundle = tmp_path / "BlindPilot" / "_internal"
    (bundle / "pywin32_system32").mkdir(parents=True)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    polluted = os.pathsep.join(
        [
            str(bundle / "pywin32_system32"),
            str(unrelated),
            str(bundle),
        ]
    )

    monkeypatch.setattr(blindpilot_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(blindpilot_app.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", polluted)

    blindpilot_app.keep_bundle_off_child_path()

    assert os.environ["PATH"] == str(unrelated)


def test_a_path_outside_the_install_folder_is_left_alone(monkeypatch, tmp_path):
    import blindpilot_app

    bundle = tmp_path / "BlindPilot" / "_internal"
    bundle.mkdir(parents=True)
    # A sibling whose name merely starts with the bundle's is a different
    # folder, and stripping it would break whatever put it there.
    neighbour = tmp_path / "BlindPilot" / "_internal-tools"
    neighbour.mkdir()
    kept = os.pathsep.join([str(neighbour), str(tmp_path)])

    monkeypatch.setattr(blindpilot_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(blindpilot_app.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", kept)

    blindpilot_app.keep_bundle_off_child_path()

    assert os.environ["PATH"] == kept


def test_running_from_source_never_touches_the_path(monkeypatch):
    import blindpilot_app

    monkeypatch.delattr(blindpilot_app.sys, "frozen", raising=False)
    monkeypatch.setenv("PATH", "/one/place")

    blindpilot_app.keep_bundle_off_child_path()

    assert os.environ["PATH"] == "/one/place"


def test_a_startup_check_does_not_take_focus_from_whoever_is_working(monkeypatch):
    """An automated check must not pull a screen reader off what it was reading.

    The smoke test ran the whole show-and-raise path a real launch runs, then
    closed a second and a half later. `Show` is the point of the check: it is
    what forces the native controls and the layout to be built. `Raise` and
    `_bring_to_front` only ask the window manager for attention, which verifies
    nothing and, on the machine of somebody running the checks, moves them out
    of whatever they were doing.
    """
    import blindpilot_app

    events: list[object] = []

    class FakeApp:
        def MainLoop(self) -> None:
            events.append("main-loop")

        def SetAppName(self, name: str) -> None:
            events.append(("app-name", name))

        def SetAppDisplayName(self, name: str) -> None:
            events.append(("app-display-name", name))

    class FakeFrame:
        def __init__(self, *, initial_cwd: str) -> None:
            pass

        def Show(self) -> None:
            events.append("show")

        def Layout(self) -> None:
            events.append("layout")

        def Raise(self) -> None:
            events.append("raise")

        def Close(self) -> None:
            events.append("close")

    monkeypatch.setattr(blindpilot_app.sys, "argv", ["blind_pilot.py", "--startup-gui-smoke"])
    monkeypatch.setattr(blindpilot_app, "_load_config", dict)
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda _cfg: None)
    monkeypatch.setattr(blindpilot_app, "MainFrame", FakeFrame)
    monkeypatch.setattr(blindpilot_app, "_bring_to_front", lambda: events.append("front"))
    monkeypatch.setattr(blindpilot_app.wx, "App", lambda _redirect: FakeApp())
    monkeypatch.setattr(blindpilot_app.wx, "CallLater", lambda _delay, callback: callback())
    monkeypatch.setattr(
        blindpilot_app,
        "reserve_hidden_console",
        lambda: events.append("console"),
    )

    assert blindpilot_app.main() == 0

    # Built and laid out, which is what the check is for, and never displayed.
    assert "layout" in events
    assert "show" not in events, "a startup check put a window on screen"
    assert "raise" not in events, "a startup check asked for the foreground"
    assert "front" not in events, "a startup check pulled itself in front"
    # AllocConsole hands back a console that is already visible, and hiding it
    # is the next thing that happens. On somebody's screen that is a window
    # appearing and vanishing. A check creates no terminal, so it needs none.
    assert "console" not in events, "a startup check allocated a console window"


@pytest.mark.parametrize(
    ("backend", "reserved"),
    [
        ("freebuff", True),
        ("claude", False),
        ("codex", False),
        ("opencode", False),
        (None, False),
    ],
)
def test_only_the_backend_that_needs_a_console_gets_one(monkeypatch, backend, reserved):
    """AllocConsole hands back a console that is already visible, and hiding it
    is the next thing that happens - one frame of a window on screen, which
    Windows offers no way to avoid. Only FreeBuff is driven through a
    pseudo-terminal; the other three are ordinary subprocesses spawned with
    CREATE_NO_WINDOW and never need one, so they should not pay for it.
    """
    import blindpilot_app

    claimed: list[bool] = []
    monkeypatch.setattr(
        blindpilot_app,
        "reserve_hidden_console",
        lambda: claimed.append(True) or True,
    )

    blindpilot_app.reserve_console_if_needed(backend)

    assert bool(claimed) is reserved


def test_a_startup_check_never_claims_a_console_even_for_freebuff(monkeypatch):
    import blindpilot_app

    claimed: list[bool] = []
    monkeypatch.setattr(
        blindpilot_app,
        "reserve_hidden_console",
        lambda: claimed.append(True) or True,
    )

    blindpilot_app.reserve_console_if_needed("freebuff", startup_check=True)

    assert claimed == []


def test_the_configured_appearance_is_applied_before_any_window_exists(monkeypatch):
    """Dark or light is decided once, on the App, before the frame is built.

    wxWidgets only honours SetAppearance before the first window exists, so
    it has to run between wx.App() and MainFrame(). A wxPython too old to
    have it is skipped, not crashed on.
    """
    import blindpilot_app

    events: list[object] = []
    if not hasattr(blindpilot_app.wx.App, "Appearance"):
        pytest.skip("this wxPython has no appearance API")
    appearance_enum = blindpilot_app.wx.App.Appearance
    result_enum = blindpilot_app.wx.App.AppearanceResult

    class FakeApp:
        # main() reads the enums off wx.App, which this stands in for.
        Appearance = appearance_enum
        AppearanceResult = result_enum

        def __init__(self, _redirect: bool) -> None:
            pass

        def MainLoop(self) -> None:
            events.append("main-loop")

        def SetAppName(self, name: str) -> None:
            pass

        def SetAppDisplayName(self, name: str) -> None:
            pass

        def SetAppearance(self, appearance) -> object:
            events.append(("appearance", appearance))
            return result_enum.Ok

    class FakeFrame:
        def __init__(self, *, initial_cwd: str) -> None:
            events.append("frame")

        def Show(self) -> None:
            pass

        def Layout(self) -> None:
            pass

        def Raise(self) -> None:
            pass

        def Close(self) -> None:
            pass

    monkeypatch.setattr(blindpilot_app.sys, "argv", ["blind_pilot.py", "--startup-gui-smoke"])
    monkeypatch.setattr(blindpilot_app, "_load_config", lambda: {"appearance": "dark"})
    monkeypatch.setattr(blindpilot_app, "_save_config", lambda cfg: None)
    monkeypatch.setattr(blindpilot_app, "MainFrame", FakeFrame)
    monkeypatch.setattr(blindpilot_app, "_bring_to_front", lambda: None)
    monkeypatch.setattr(blindpilot_app.wx, "App", FakeApp)
    monkeypatch.setattr(blindpilot_app.wx, "CallLater", lambda _delay, callback: callback())

    assert blindpilot_app.main() == 0
    assert ("appearance", appearance_enum.Dark) in events
    assert events.index(("appearance", appearance_enum.Dark)) < events.index("frame")
