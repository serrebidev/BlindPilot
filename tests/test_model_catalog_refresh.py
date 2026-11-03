# SPDX-License-Identifier: MIT
"""A long-running window drops a retired model, and says so.

The picker's catalog used to be read only when /model opened or the backend
changed, so a model retired upstream stayed on offer until somebody happened
to open it. A timer now re-reads each tab's catalog as its cached answer ages
out. These tests cover the part a listener depends on: the difference between
what the tab last showed and what the backend offers now is announced, once,
without the picker being opened -- and nothing is announced when nothing
changed, when the read failed, or on the very first read.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import blindpilot_app as app

wx = pytest.importorskip("wx")


class _Panel:
    """A tab, with the real methods under test and nothing else.

    The methods are bound from `SessionPanel` rather than reimplemented, so the
    test exercises the code that ships.
    """

    def __init__(self, backend: str = app.BACKEND_CODEX):
        self.cwd = "/tmp/proj"
        self._backend = backend
        self._cli_model = ""
        self._cli_effort = ""
        self._known_models = None
        self._model_refresh_running = False
        self.notices: list[tuple[str, str]] = []

    def selected_backend(self) -> str:
        return self._backend

    def _on_activity(self, kind: str, text: str, **_kwargs) -> None:
        self.notices.append((kind, text))

    _remember_cli_model = app.SessionPanel._remember_cli_model
    _models_refreshed = app.SessionPanel._models_refreshed
    _announce_model_changes = app.SessionPanel._announce_model_changes
    refresh_models_in_background = app.SessionPanel.refresh_models_in_background


def _options(models: list[str], current: str = "") -> "app.ModelOptions":
    return app.ModelOptions(list(models), ["high"], current_model=current)


class _InstantThread:
    """`threading.Thread` with the work run on the spot, so the probe the
    method would have run in the background is seen synchronously."""

    def __init__(self, target, daemon=False):
        self._target = target

    def start(self) -> None:
        self._target()


def test_a_retired_model_is_announced_as_a_notice():
    panel = _Panel()
    panel._known_models = ["gpt-6-sol", "gpt-5.5"]

    app.SessionPanel._models_refreshed(panel, app.BACKEND_CODEX, _options(["gpt-6-sol"]))

    assert panel.notices == [("notice", "Codex no longer offers gpt-5.5.")]
    assert panel._known_models == ["gpt-6-sol"]


def test_a_new_model_is_announced_as_a_notice():
    panel = _Panel()
    panel._known_models = ["gpt-6-sol"]

    app.SessionPanel._models_refreshed(
        panel, app.BACKEND_CODEX, _options(["gpt-6-sol", "gpt-reserve"])
    )

    assert panel.notices == [("notice", "Codex now offers gpt-reserve.")]


def test_a_model_gone_and_one_arrived_are_both_said():
    panel = _Panel()
    panel._known_models = ["gpt-5.5"]

    app.SessionPanel._models_refreshed(panel, app.BACKEND_CODEX, _options(["gpt-reserve"]))

    assert panel.notices == [
        ("notice", "Codex no longer offers gpt-5.5; now offers gpt-reserve."),
    ]


def test_the_first_catalog_a_tab_reads_is_not_announced():
    """The list the picker already offers is not news, and nobody should be
    greeted by it the first time the timer fires."""
    panel = _Panel()

    app.SessionPanel._models_refreshed(panel, app.BACKEND_CODEX, _options(["gpt-6-sol"]))

    assert panel.notices == []
    assert panel._known_models == ["gpt-6-sol"]


def test_the_same_catalog_read_again_says_nothing():
    panel = _Panel()
    panel._known_models = ["gpt-6-sol"]

    app.SessionPanel._models_refreshed(panel, app.BACKEND_CODEX, _options(["gpt-6-sol"]))

    assert panel.notices == []


def test_a_probe_that_came_back_empty_is_not_a_removal():
    """A failed probe returns no models. That is not the backend dropping
    everything, so it is neither announced nor allowed to replace the baseline
    the next good read is compared against."""
    panel = _Panel()
    panel._known_models = ["gpt-6-sol"]

    app.SessionPanel._models_refreshed(panel, app.BACKEND_CODEX, _options([]))

    assert panel.notices == []
    assert panel._known_models == ["gpt-6-sol"]


def test_a_catalog_for_another_backend_is_left_alone():
    panel = _Panel()
    panel._known_models = ["gpt-6-sol"]

    app.SessionPanel._models_refreshed(
        panel, app.BACKEND_OPENCODE, _options(["opencode/beam-free"])
    )

    assert panel.notices == []


def test_the_background_refresh_leaves_a_fresh_catalog_alone(monkeypatch):
    """The timer runs often; while the cached answer is inside the interval the
    person set, it must not reach for the CLI at all."""
    panel = _Panel()
    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 15)
    monkeypatch.setattr(app, "cached_model_options", lambda *_a, **_k: _options(["gpt-6-sol"]))
    monkeypatch.setattr(
        app,
        "probe_model_options",
        lambda *_a, **_k: pytest.fail("a fresh catalog must not be re-probed"),
    )

    app.SessionPanel.refresh_models_in_background(panel)

    assert panel._model_refresh_running is False


def test_a_setting_of_zero_turns_the_background_read_off(monkeypatch):
    """Off is a real choice: each read starts the provider's CLI, and somebody
    who would rather not pay that sets it to 0."""
    panel = _Panel()
    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 0)
    monkeypatch.setattr(app, "cached_model_options", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app,
        "probe_model_options",
        lambda *_a, **_k: pytest.fail("the refresh is off; nothing should be probed"),
    )

    app.SessionPanel.refresh_models_in_background(panel)

    assert panel._model_refresh_running is False


def test_the_background_refresh_probes_a_stale_catalog_and_announces(monkeypatch):
    panel = _Panel()
    panel._known_models = ["gpt-6-sol", "gpt-5.5"]
    calls: list[str] = []

    def probe(cwd, max_age=0, backend=app.BACKEND_CLAUDE):
        calls.append(backend)
        return _options(["gpt-6-sol"], current="gpt-6-sol")

    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 15)
    monkeypatch.setattr(app, "cached_model_options", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "probe_model_options", probe)
    monkeypatch.setattr(app, "threading", SimpleNamespace(Thread=_InstantThread))
    monkeypatch.setattr(app.wx, "CallAfter", lambda fn, *args: fn(*args))

    app.SessionPanel.refresh_models_in_background(panel)

    assert calls == [app.BACKEND_CODEX]
    assert panel._cli_model == "gpt-6-sol"
    assert panel.notices == [("notice", "Codex no longer offers gpt-5.5.")]


def test_a_second_tick_while_a_probe_is_out_does_not_stack_one(monkeypatch):
    panel = _Panel()
    panel._model_refresh_running = True
    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 15)
    monkeypatch.setattr(app, "cached_model_options", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app, "probe_model_options", lambda *_a, **_k: pytest.fail("a probe is already out")
    )

    app.SessionPanel.refresh_models_in_background(panel)


def test_the_window_asks_every_tab_to_refresh():
    refreshed: list[str] = []

    class _Page:
        def __init__(self, name: str):
            self.name = name

        def refresh_models_in_background(self) -> None:
            refreshed.append(self.name)

    frame = SimpleNamespace(_session_panels=lambda: [_Page("one"), _Page("two"), object()])

    app.MainFrame._refresh_model_catalogs(frame)

    assert refreshed == ["one", "two"]


class _Timer:
    """A `wx.Timer` that only records what was asked of it."""

    def __init__(self, _owner):
        self.started: list[int] = []
        self.stopped = 0

    def Start(self, milliseconds):
        self.started.append(milliseconds)

    def Stop(self):
        self.stopped += 1


def _frame(monkeypatch, existing=None):
    created: list[_Timer] = []

    def make_timer(owner):
        timer = _Timer(owner)
        created.append(timer)
        return timer

    monkeypatch.setattr(app.wx, "Timer", make_timer)
    monkeypatch.setattr(app.wx, "EVT_TIMER", object())
    frame = SimpleNamespace(_model_timer=existing, Bind=lambda *_a, **_k: None)
    frame._sync_model_timer = lambda: app.MainFrame._sync_model_timer(frame)
    return frame, created


def test_the_window_watches_model_catalogs_on_the_interval_set(monkeypatch):
    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 15)
    frame, _created = _frame(monkeypatch)

    app.MainFrame.watch_model_catalogs(frame)

    assert frame._model_timer is not None
    assert frame._model_timer.started == [15 * 60 * 1000]


def test_the_window_keeps_one_timer_and_re_arms_it(monkeypatch):
    timer = _Timer(None)
    frame, created = _frame(monkeypatch, existing=timer)
    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 30)

    app.MainFrame._sync_model_timer(frame)

    assert created == [], "the timer already watched is reused, not replaced"
    assert timer.started == [30 * 60 * 1000]


def test_the_window_disarms_the_timer_when_the_refresh_is_off(monkeypatch):
    timer = _Timer(None)
    frame, _created = _frame(monkeypatch, existing=timer)
    monkeypatch.setattr(app.SETTINGS, "model_refresh_minutes", 0)

    app.MainFrame._sync_model_timer(frame)

    assert timer.stopped == 1
    assert frame._model_timer is None


def test_the_refresh_interval_reads_back_whatever_config_holds():
    assert app._valid_model_refresh_minutes(None) == app.MODEL_REFRESH_MINUTES_DEFAULT
    assert app._valid_model_refresh_minutes("nonsense") == app.MODEL_REFRESH_MINUTES_DEFAULT
    assert app._valid_model_refresh_minutes("30") == 30
    assert app._valid_model_refresh_minutes(0) == 0
    assert app._valid_model_refresh_minutes(-5) == app.MODEL_REFRESH_MINUTES_MIN
    assert app._valid_model_refresh_minutes(9999) == app.MODEL_REFRESH_MINUTES_MAX


def test_the_refresh_interval_round_trips_through_settings(monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(app, "_save_config", lambda cfg: saved.append(dict(cfg)))
    monkeypatch.setattr(app, "_load_config", dict)

    settings = app._Settings()
    assert settings.model_refresh_minutes == app.MODEL_REFRESH_MINUTES_DEFAULT

    settings.model_refresh_minutes = 0
    settings.save()
    assert saved[-1]["model_refresh_minutes"] == 0

    monkeypatch.setattr(app, "_load_config", lambda: saved[-1])
    assert app._Settings().model_refresh_minutes == 0
