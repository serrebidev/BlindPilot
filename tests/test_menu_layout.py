"""Where things live in the menu bar.

File had grown to fourteen items across six separator groups — fourteen arrow
presses to hear what was in it, mixing "open a session", "stop the running
task" and "quit". Splitting it means the menu bar itself tells you where to
look: File for sessions and the application, Conversation for this
conversation and the next message, Model for what answers you.

The other half of this is that the menu bar should be the *complete* list of
what the app can do. Sighted applications deliberately offer the same command
as a button, a menu item and a shortcut; the menu is where a command is found
and where its shortcut is learnt. Attach, Slash and Jump to Latest Response
each had a control or a chord and no menu entry, so the chord could only be
discovered by being told.
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
    """A bare frame carrying the real append-and-bind helper the builders use."""
    window = wx.Frame(None)
    window._menu_item = lambda *args, **kwargs: app.MainFrame._menu_item(window, *args, **kwargs)
    # Each builder takes a reference to the handler as it appends the item, so
    # all of them have to exist by name. None of them is ever called: that
    # needs a click, and these tests only read labels.
    for name in (
        "_new_session",
        "_open_history",
        "_side_chat_active",
        "_set_projects_folder",
        "_create_desktop_shortcut",
        "_close_current_session",
        "_stop_active",
        "_attach_active",
        "_slash_active",
        "_compact_active",
        "_new_conversation_active",
        "_find_active",
        "_jump_to_latest_response",
    ):
        setattr(window, name, lambda: None)
    try:
        yield window
    finally:
        window.Destroy()


class _Panel(app.SessionPanel):
    """A session panel that is one only as far as `isinstance` is concerned."""

    def __init__(self, cwd):
        self.cwd = cwd


def _labels(menu) -> list[str]:
    return [item.GetItemLabelText() for item in menu.GetMenuItems() if not item.IsSeparator()]


FILE_ITEMS = [
    "New Session",
    "Recent Conversations",
    "Side Chat",
    "Next Session",
    "Previous Session",
    "Projects Folder",
    "Desktop Shortcut",
    "Close Session",
    "Quit",
]

CONVERSATION_ITEMS = [
    "Stop Task",
    "Attach Files",
    "Slash Command",
    "Compact Conversation",
    "Start New Conversation",
    "Find in Responses",
    "Jump to Latest Response",
]


@pytest.mark.parametrize("wanted", FILE_ITEMS)
def test_the_file_menu_holds_sessions_and_the_application(frame, wanted):
    labels = " | ".join(_labels(app.MainFrame._build_file_menu(frame)))
    assert wanted in labels, f"File has no {wanted!r}: {labels}"


@pytest.mark.parametrize("wanted", CONVERSATION_ITEMS)
def test_the_conversation_menu_holds_this_conversation(frame, wanted):
    labels = " | ".join(_labels(app.MainFrame._build_conversation_menu(frame)))
    assert wanted in labels, f"Conversation has no {wanted!r}: {labels}"


def test_neither_menu_is_longer_than_it_can_be_listened_to(frame):
    """The point of the split. Ten was the ceiling chosen; fourteen was the
    problem."""
    for build in (app.MainFrame._build_file_menu, app.MainFrame._build_conversation_menu):
        labels = _labels(build(frame))
        assert len(labels) <= 10, f"{len(labels)} items: {labels}"


def test_nothing_is_offered_in_both_menus(frame):
    """Two homes for one command is worse than the wrong home for it."""
    in_file = set(_labels(app.MainFrame._build_file_menu(frame)))
    in_conversation = set(_labels(app.MainFrame._build_conversation_menu(frame)))

    assert not (in_file & in_conversation)


@pytest.mark.parametrize(
    ("item", "chord"),
    [
        ("Attach Files", "Ctrl+Shift+A"),
        ("Slash Command", "Ctrl+/"),
        ("Jump to Latest Response", "Ctrl+R"),
    ],
)
def test_a_shortcut_only_command_now_says_its_own_chord(frame, item, chord):
    """The menu item is where anybody learns the shortcut exists.

    The chord is written into the label rather than after a tab, because a tab
    would register a second menu accelerator for a key the frame's own
    accelerator table already carries — the same reason Next Session spells
    Ctrl+Tab out in words.
    """
    labels = _labels(app.MainFrame._build_conversation_menu(frame))
    line = next(text for text in labels if item in text)

    assert chord in line


def test_opening_a_side_chat_uses_the_folder_of_the_visible_tab():
    """A side chat is a second conversation in the same folder, so which
    folder depends on which tab is in front."""
    page = _Panel("/work/project")
    stub = type("FrameStub", (), {})()
    stub.notebook = type("NotebookStub", (), {"GetCurrentPage": lambda _s: page})()
    opened: list[tuple] = []
    stub._open_side_chat = lambda cwd, message: opened.append((cwd, message))

    app.MainFrame._side_chat_active(stub)

    assert opened == [("/work/project", "")]


def test_opening_a_side_chat_with_no_session_open_does_nothing():
    stub = type("FrameStub", (), {})()
    stub.notebook = type("NotebookStub", (), {"GetCurrentPage": lambda _s: None})()
    stub._open_side_chat = lambda *_a: pytest.fail("opened a side chat with no tab to copy")

    app.MainFrame._side_chat_active(stub)


# ----- every command BlindPilot owns can be found without being told -----
#
# The menu bar is meant to be the complete inventory, and a slash command that
# nothing in it names can only be discovered by somebody telling you the word.
# `/status` was exactly that: BlindPilot's own command, offered for every
# backend because none of them answers it themselves, reachable only by typing
# it into the prompt.
#
# The mapping is written out rather than guessed at, so adding a command
# without deciding where it lives fails here instead of shipping invisibly.

COMMAND_MENU_ENTRY = {
    "/btw": "Side Chat",
    "/clear": "Start New Conversation",
    "/compact": "Compact Conversation",
    "/exit": "Close Session",
    "/model": "Model and Effort",
    "/resume": "Recent Conversations",
    "/status": "Session Status",
}

# Commands that are deliberately not menu items, and why. A second way to say
# the same thing is not a second command.
NOT_A_MENU_ITEM = {
    "/models": "opens the same dialog as /model, which refreshes in the background anyway",
    "/model [model-id]": "an argument form of /model, not a separate command",
}


def _command_names():
    """The command word, without the argument placeholder."""
    return [command.split(" ")[0] for command, _help in app._BLINDPILOT_SLASH_COMMANDS]


def _model_menu(frame):
    """The Model menu, with its two submenus stubbed out.

    Those read live backend and permission state; what is being checked here
    is the flat items, which is where a command becomes findable.
    """
    frame._build_backend_menu = lambda: wx.Menu()
    frame._build_permission_mode_menu = lambda: wx.Menu()
    for name in ("_model_active", "_connect_active", "_manage_backends", "_status_active"):
        setattr(frame, name, lambda: None)
    return app.MainFrame._build_model_menu(frame)


def _every_label(frame):
    return " | ".join(
        _labels(app.MainFrame._build_file_menu(frame))
        + _labels(app.MainFrame._build_conversation_menu(frame))
        + _labels(_model_menu(frame))
    )


def test_every_blindpilot_command_is_either_in_a_menu_or_deliberately_not(frame):
    """The guard: a new command cannot be added without deciding this."""
    decided = set(COMMAND_MENU_ENTRY) | set(NOT_A_MENU_ITEM)
    for command, _help in app._BLINDPILOT_SLASH_COMMANDS:
        name = command if command in decided else command.split(" ")[0]
        assert name in decided, (
            f"{command} is BlindPilot's own command and nothing says where it lives. "
            "Give it a menu entry, or record here why it has none."
        )


@pytest.mark.parametrize("command", sorted(COMMAND_MENU_ENTRY))
def test_the_menu_entry_for_each_command_is_really_there(frame, command):
    wanted = COMMAND_MENU_ENTRY[command]
    labels = _every_label(frame)

    assert wanted in labels, f"{command} is supposed to be {wanted!r} in a menu: {labels}"


def test_the_model_menu_says_what_this_tab_is_set_to(frame):
    """`/status` reports the backend, model and account, which is what this
    menu is about."""
    labels = _labels(_model_menu(frame))

    assert any("Session Status" in label for label in labels), labels


def test_the_model_menu_still_holds_what_it_held(frame):
    labels = " | ".join(_labels(_model_menu(frame)))

    for wanted in ("Model and Effort", "Manage Backends", "Connect a Provider"):
        assert wanted in labels, f"{wanted} went missing: {labels}"


def test_the_model_menu_is_not_longer_than_it_can_be_listened_to(frame):
    assert len(_labels(_model_menu(frame))) <= 10
