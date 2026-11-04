# SPDX-License-Identifier: MIT
"""Arming the primitives a GUI-thread callback may not touch.

The project's rule is that nothing blocks the GUI thread, and until now nothing
checked it. A handler that starts a process, waits for one, joins a thread,
waits with no deadline, or reaches the network is an offender; these helpers
turn that into a deterministic failing test that names the handler, instead of
a stall somebody has to feel and then guess at.

Detection is at runtime rather than by reading the source: the handler is
called for real with those entry points replaced, so the failure lands on the
first call that would have stalled the window.

Three things deliberately stay allowed. Reading files, because a settings read
is well inside the budget. Bounded waits, because a deadline is a delay rather
than a stall. And anything done on a worker thread, because moving work off the
GUI thread is the fix, not the offence — so only the thread that has to stay
free is bound. A modal dialog is answered Cancel at once, so a handler that
opens one does not sit waiting on a person who is not there.
"""

from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
import urllib.request
from contextlib import contextmanager
from typing import Callable, Iterable, Iterator

STARTED_A_PROCESS = "started a process"
WAITED_FOR_A_PROCESS = "waited for a process to end"
JOINED_A_THREAD = "joined a thread"
WAITED_WITH_NO_DEADLINE = "waited with no deadline"
WAITED_ON_A_QUEUE = "waited on a queue"
REACHED_THE_NETWORK = "reached the network"


class BlockedTheGuiThread(AssertionError):
    """A GUI-thread callback touched something it may not touch."""


def _on_the_gui_thread() -> bool:
    """Whether the call being guarded is the GUI thread's own.

    A worker the handler started may do all of this freely — that is the fix —
    so only the GUI thread is bound.
    """
    return threading.current_thread() is threading.main_thread()


def _called_from_thread_startup() -> bool:
    """Whether a wait came from CPython starting the thread we just asked for.

    `Thread.start` waits, with no deadline, on the thread it has just created.
    That wait is the mechanics of moving work off the GUI thread rather than a
    stall on it, so it is the one unbounded wait this guard allows.
    """
    frame = sys._getframe(2)
    while frame is not None:
        if frame.f_code.co_filename.endswith("threading.py"):
            return True
        frame = frame.f_back
    return False


def _always(*_args: object, **_kwargs: object) -> bool:
    return True


def _unbounded(_self: object, timeout: object = None, **_kwargs: object) -> bool:
    """A wait with no deadline, which is the kind that stalls a window."""
    return timeout is None and not _called_from_thread_startup()


def _make_guard(
    original: Callable[..., object], what: str, offends: Callable[..., bool]
) -> Callable[..., object]:
    """Wrap one entry point so it raises on the GUI thread when it would block."""

    def guarded_call(*args: object, **kwargs: object) -> object:
        if not _on_the_gui_thread() or not offends(*args, **kwargs):
            return original(*args, **kwargs)
        raise BlockedTheGuiThread(what)

    return guarded_call


# What a handler on the GUI thread may not do, and what the failure says it did.
_GUARDED: tuple[tuple[object, str, str, Callable[..., bool]], ...] = (
    (subprocess, "run", STARTED_A_PROCESS, _always),
    (subprocess, "Popen", STARTED_A_PROCESS, _always),
    (subprocess, "call", STARTED_A_PROCESS, _always),
    (subprocess, "check_output", STARTED_A_PROCESS, _always),
    (subprocess, "check_call", STARTED_A_PROCESS, _always),
    (subprocess.Popen, "wait", WAITED_FOR_A_PROCESS, _always),
    (subprocess.Popen, "communicate", WAITED_FOR_A_PROCESS, _always),
    (threading.Thread, "join", JOINED_A_THREAD, _always),
    (threading.Event, "wait", WAITED_WITH_NO_DEADLINE, _unbounded),
    (threading.Condition, "wait", WAITED_WITH_NO_DEADLINE, _unbounded),
    (queue.Queue, "get", WAITED_ON_A_QUEUE, _always),
    (socket.socket, "connect", REACHED_THE_NETWORK, _always),
    (socket, "create_connection", REACHED_THE_NETWORK, _always),
    (urllib.request, "urlopen", REACHED_THE_NETWORK, _always),
)


@contextmanager
def gui_thread_may_not_block() -> Iterator[None]:
    """Arm the forbidden primitives. Touching one raises, on the GUI thread."""
    # (owner, name, was the owner's own attribute): a name inherited from a C
    # base — socket.socket.connect is one — is deleted on the way out rather
    # than shadowed by a copy of the descriptor it already had.
    saved: list[tuple[object, str, object, bool]] = []
    try:
        for owner, name, what, offends in _GUARDED:
            original = getattr(owner, name, None)
            if original is None:
                continue
            saved.append((owner, name, original, name in getattr(owner, "__dict__", {})))
            setattr(owner, name, _make_guard(original, what, offends))
        undo_dialogs = _answer_dialogs_at_once()
        try:
            yield
        finally:
            undo_dialogs()
    finally:
        for owner, name, original, was_its_own in saved:
            if was_its_own:
                setattr(owner, name, original)
                continue
            try:
                delattr(owner, name)
            except AttributeError:
                pass


def _answer_dialogs_at_once() -> Callable[[], None]:
    """Answer a modal dialog Cancel, and hand back the undo.

    Without this, driving a real handler that opens a dialog would sit inside
    `ShowModal` until somebody closed a dialog they cannot see. A machine with
    no wxPython has no dialogs to answer.
    """
    try:
        import wx
    except Exception:  # pragma: no cover - depends on the machine
        return lambda: None
    original_modal = wx.Dialog.ShowModal
    original_box = wx.MessageBox
    wx.Dialog.ShowModal = lambda _self: wx.ID_CANCEL
    wx.MessageBox = lambda *_args, **_kwargs: wx.ID_CANCEL

    def undo() -> None:
        wx.Dialog.ShowModal = original_modal
        wx.MessageBox = original_box

    return undo


def drive(handler: Callable[..., object], *args: object) -> BaseException | None:
    """Call one handler, returning whatever it raised rather than propagating.

    The finding is the return value: a `BlockedTheGuiThread` names an offence,
    while any other exception is a handler that could not run here for a reason
    that has nothing to do with blocking — nothing selected, no session open —
    and a caller can tell the two apart instead of counting both.
    """
    try:
        handler(*args)
    except BaseException as exc:  # the caller decides; the finding is the value
        return exc
    return None


def offences(driven: Iterable[tuple[str, BaseException | None]]) -> list[str]:
    """The handler names whose driving reported a blocked GUI thread."""
    return [name for name, error in driven if isinstance(error, BlockedTheGuiThread)]
