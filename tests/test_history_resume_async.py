# SPDX-License-Identifier: MIT
"""Opening a saved conversation never loads its transcript in the GUI callback."""

import threading
import time

import blindpilot_app as app
import session_history
import wx
from test_tab_strip_focus import _frame, _running_app


def _wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wx.GetApp().Yield()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_resume_returns_before_slow_transcript_load_and_opens_when_ready(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            entry = session_history.HistoryEntry("claude", "saved", "Saved chat", "", 1)
            started = threading.Event()
            release = threading.Event()

            def slow_load(chosen):
                started.set()
                assert release.wait(3)
                return [session_history.HistoryTurn("Question", "Answer")]

            monkeypatch.setattr(app, "load_turns", slow_load)
            before = time.monotonic()
            result = frame._resume_history(entry)
            elapsed = time.monotonic() - before
            assert result is None
            assert elapsed < 0.05
            assert started.wait(1)
            assert frame.notebook.GetPageCount() == 1
            release.set()
            _wait(lambda: frame.notebook.GetPageCount() == 2)
            panel = frame.notebook.GetCurrentPage()
            assert panel._session_id == "saved"
            assert [(turn.prompt, turn.response) for turn in panel._turns] == [
                ("Question", "Answer")
            ]
        finally:
            if "release" in locals():
                release.set()
            progress = getattr(frame, "_history_open_progress", None)
            if progress:
                progress.Destroy()
            if getattr(frame, "_history_open_cancel", None) is not None:
                frame._history_open_cancel.set()
            frame.Destroy()


def test_cancelled_history_load_does_not_open_a_tab(monkeypatch, tmp_path):
    with _running_app():
        frame = _frame(monkeypatch, tmp_path)
        try:
            entry = session_history.HistoryEntry("claude", "saved", "Saved chat", "", 1)
            started = threading.Event()
            release = threading.Event()

            def slow_load(_entry):
                started.set()
                assert release.wait(3)
                return [session_history.HistoryTurn("Question", "Answer")]

            monkeypatch.setattr(app, "load_turns", slow_load)
            frame._resume_history(entry)
            _wait(started.is_set)
            progress = frame._history_open_progress
            assert progress is not None
            progress.Close()
            _wait(lambda: frame._history_open_progress is None)
            assert frame._history_open_cancel.is_set()
            release.set()
            _wait(lambda: frame._history_open_cancel is None)
            assert frame.notebook.GetPageCount() == 1
        finally:
            if "release" in locals():
                release.set()
            progress = getattr(frame, "_history_open_progress", None)
            if progress:
                progress.Destroy()
            if getattr(frame, "_history_open_cancel", None) is not None:
                frame._history_open_cancel.set()
            frame.Destroy()
