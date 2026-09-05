"""The Model menu: what answers you, and what it is allowed to do.

The model and effort picker already existed and worked, but the only way to
reach it was typing `/model` into the prompt. Nothing in the menu bar
mentioned models at all, and a slash command typed into a prompt is not
something anybody finds without being told about it first. Of everything in
the app it was the one feature with no on-screen control of any kind — Attach,
Slash and Permission Mode at least have a button or a dropdown next to the
prompt.

Model, effort and permission mode are per tab, not per application, so every
one of these acts on the visible tab rather than on the window.
"""

from __future__ import annotations

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def wx_app():
    try:
        return wx.App(False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no display for wxPython: {exc}")


@pytest.fixture
def frame(wx_app):
    window = wx.Frame(None)
    try:
        yield window
    finally:
        window.Destroy()


class _Panel(app.SessionPanel):
    """A stand-in the menu can act on without building a session."""

    def __init__(self, mode="default"):
        self.mode = mode
        self.opened: list[str] = []
        self.set_modes: list[str] = []

    def open_model_dialog(self, force_refresh: bool = False) -> None:
        self.opened.append("model")

    def open_connect_dialog(self) -> None:
        self.opened.append("connect")

    def _set_mode(self, value: str, speak: bool = True) -> None:
        self.set_modes.append(value)
        self.mode = value


def _frame_with(page):
    stub = type("FrameStub", (), {})()
    stub.notebook = type("NotebookStub", (), {"GetCurrentPage": lambda _self: page})()
    return stub


# ----- the picker finally has a way in -----
def test_choosing_model_and_effort_opens_the_picker_for_the_visible_tab():
    page = _Panel()
    stub = _frame_with(page)

    app.MainFrame._model_active(stub)

    assert page.opened == ["model"]


def test_choosing_model_and_effort_does_nothing_with_no_session_open():
    stub = _frame_with(None)

    app.MainFrame._model_active(stub)  # must not raise


def test_connect_a_provider_opens_it_for_the_visible_tab():
    page = _Panel()
    stub = _frame_with(page)

    app.MainFrame._connect_active(stub)

    assert page.opened == ["connect"]


# ----- permission mode -----
def test_the_menu_offers_every_permission_mode_there_is(frame):
    """A mode missing from the menu is a mode nobody can reach from it."""
    menu = app.MainFrame._build_permission_mode_menu(frame)
    try:
        items = menu.GetMenuItems()
        assert [item.GetItemLabelText() for item in items] == app._MODE_LABELS
        # Radio, not check: the modes are exclusive, and a screen reader says
        # so when they are built this way.
        assert {item.GetKind() for item in items} == {wx.ITEM_RADIO}
    finally:
        menu.Destroy()


def test_the_menu_shows_the_mode_the_visible_tab_is_actually_in(frame):
    """Mode is per tab, so switching tabs has to move the mark with it."""
    stub = _frame_with(_Panel(mode="plan"))
    stub.Bind = frame.Bind
    menu = app.MainFrame._build_permission_mode_menu(stub)
    try:
        app.MainFrame._refresh_mode_items(stub)

        checked = [value for value, item in stub._mode_items.items() if item.IsChecked()]
        assert checked == ["plan"]
    finally:
        menu.Destroy()


def test_the_mode_menu_survives_being_refreshed_before_there_are_any_tabs(frame):
    """The menu bar is built before the notebook it describes.

    Every test above hands the frame a notebook, so none of them could notice
    that the real window has none yet at the moment the menu is finished. The
    packaged startup smoke test caught it; this keeps it caught.
    """
    stub = type("FrameStub", (), {})()
    stub.Bind = frame.Bind
    menu = app.MainFrame._build_permission_mode_menu(stub)
    try:
        app.MainFrame._refresh_mode_items(stub)  # must not raise
    finally:
        menu.Destroy()


def test_choosing_a_mode_sets_it_on_the_visible_tab():
    page = _Panel()
    stub = _frame_with(page)

    app.MainFrame._set_mode_active(stub, "acceptEdits")

    assert page.set_modes == ["acceptEdits"]


# ----- Connect belongs to one backend only -----
@pytest.mark.parametrize(
    ("backend", "enabled"),
    [
        (app.BACKEND_OPENCODE, True),
        (app.BACKEND_CLAUDE, False),
        (app.BACKEND_CODEX, False),
        (app.BACKEND_FREEBUFF, False),
    ],
)
def test_connect_is_offered_only_where_it_means_something(frame, backend, enabled):
    """Greyed out with a reason, the way Compact already is for backends that
    cannot compact — rather than offered and then refused after the click."""
    menu = wx.Menu()
    stub = type("FrameStub", (), {})()
    stub._connect_item = menu.Append(wx.ID_ANY, "&Connect a Provider…")
    stub._backend = backend
    stub._app_mode = app.APP_MODE_AGENT
    try:
        app.MainFrame._refresh_connect_item(stub)

        assert stub._connect_item.IsEnabled() is enabled
        if not enabled:
            assert "opencode" in stub._connect_item.GetHelp()
    finally:
        menu.Destroy()
