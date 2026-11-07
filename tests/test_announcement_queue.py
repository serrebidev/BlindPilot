"""The screen-reader queue belongs to the test that filled it.

The queue is drained by a daemon thread that reads the module-level speaker, so
a line an earlier test queued was delivered while the next test had its own
`_make_speaker` patched in. That test counted a speaker build it never asked
for, which is the Windows failure in #81.
"""

from __future__ import annotations

import blindpilot_app as app
from conftest import _settle_announcements


def test_a_line_queued_here_does_not_reach_the_next_test(monkeypatch):
    builds: list[int] = []
    monkeypatch.setattr(app, "_SPEAKER", None)
    monkeypatch.setattr(app, "_SPEAKER_INITIALIZED", False)
    monkeypatch.setattr(app, "_speaker_retry_after", 0.0)
    monkeypatch.setattr(app, "_make_speaker", lambda: builds.append(1) or None)

    app._ANNOUNCEMENT_QUEUE.put("leftover from an earlier test")
    _settle_announcements()

    assert app._ANNOUNCEMENT_QUEUE.unfinished_tasks == 0
    assert app._SPEAKER is None
    assert app._SPEAKER_INITIALIZED is False
