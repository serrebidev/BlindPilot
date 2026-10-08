"""Recent Conversations in the order you want, under the names you gave.

The list was newest first and nothing else, and a conversation was known only
by its first message. It can now be sorted, renamed and hidden, as The Chat
Place's session list can, all in BlindPilot's own config.
"""

from __future__ import annotations

import blindpilot_app as app
from session_history import HistoryEntry


def _entry(sid, title, modified, folder):
    return HistoryEntry(
        backend="claude", session_id=sid, title=title, path="", modified=modified, folder=folder
    )


ENTRIES = [
    _entry("a", "beta", 1.0, "zed"),
    _entry("b", "Alpha", 3.0, "app"),
    _entry("c", "gamma", 2.0, "app"),
]


def _order(order, title=lambda e: e.title):
    return [e.session_id for e in app.sort_history(ENTRIES, order, title)]


def test_each_order():
    assert _order("Newest first") == ["b", "c", "a"]
    assert _order("Oldest first") == ["a", "c", "b"]
    assert _order("By title") == ["b", "a", "c"]
    assert _order("By folder") == ["b", "c", "a"]


def test_a_name_given_here_is_what_title_order_sorts_by():
    renamed = {"a": "Aardvark"}

    assert _order("By title", lambda e: renamed.get(e.session_id, e.title))[0] == "a"


def test_the_key_names_backend_and_session():
    assert app._history_key(ENTRIES[0]) == "claude:a"
