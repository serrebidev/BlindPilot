"""The Remote Hermes dialog's connection test outlives the dialog.

The test runs on a thread for up to the connect timeout plus thirty seconds,
and reports back through `wx.CallAfter`. Escape in the meantime destroys the
dialog, and the report then enabled a button whose C++ object was gone.
"""

from __future__ import annotations

import blindpilot_app as app


class _Dead:
    def __bool__(self):
        return False

    def __getattr__(self, name):
        raise RuntimeError(f"wrapped C/C++ object has been deleted (asked for {name})")


def test_the_result_of_a_test_arriving_after_escape_is_dropped(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(app, "announce", lambda text, urgent=False: said.append(text))

    app.RemoteHermesDialog._test_done(_Dead(), "")
    app.RemoteHermesDialog._test_done(_Dead(), "refused")

    assert said == []
