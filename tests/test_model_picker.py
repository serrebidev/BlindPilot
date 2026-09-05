"""Unit tests for /model: parsing what the CLI reports, and passing the
chosen model / effort through to the subprocess.

Run from the project root:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_model_picker.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blindpilot_app  # noqa: E402
from blindpilot_app import (  # noqa: E402
    BACKEND_CODEX,
    BACKEND_FREEBUFF,
    _npm_update_argv,
    _repair_claude_native_update,
    _parse_current_model,
    _parse_effort_levels,
    _parse_model_aliases,
    probe_model_options,
)


def test_npm_backend_updates_request_the_latest_package(monkeypatch):
    monkeypatch.setattr(blindpilot_app.shutil, "which", lambda _name: "npm")

    assert _npm_update_argv(BACKEND_CODEX)[-1] == "@openai/codex@latest"
    assert _npm_update_argv(BACKEND_FREEBUFF)[-1] == "freebuff@latest"


def test_claude_update_repairs_an_old_windows_launcher(monkeypatch, tmp_path):
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    newest = tmp_path / ".local" / "share" / "claude" / "versions" / "2.1.226"
    launcher.parent.mkdir(parents=True)
    newest.parent.mkdir(parents=True)
    launcher.write_text("old", encoding="utf-8")
    newest.write_text("new", encoding="utf-8")
    monkeypatch.setattr(blindpilot_app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(blindpilot_app.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        blindpilot_app,
        "_executable_version",
        lambda binary: (
            "2.1.226" if Path(binary).read_text(encoding="utf-8") == "new" else "2.1.225"
        ),
    )

    assert _repair_claude_native_update(str(launcher), lambda _text: None)
    assert launcher.read_text(encoding="utf-8") == "new"


# Verbatim output of `claude -p "/model"` at the time of writing. The point of
# the parsers is that this list is *not* hard-coded anywhere in the app.
MODEL_OUTPUT = (
    "Current model: Opus 5 (effort: medium)\n"
    "Usage: /model <name>. Available: sonnet, opus, haiku, fable, best, "
    "sonnet[1m], opus[1m], fable[1m], opusplan, default, or a full model ID.\n"
)

HELP_OUTPUT = """Options:
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --fallback-model <model>              Enable automatic fallback
"""


def test_model_aliases_are_read_from_the_usage_line():
    assert _parse_model_aliases(MODEL_OUTPUT) == [
        "sonnet",
        "opus",
        "haiku",
        "fable",
        "best",
        "sonnet[1m]",
        "opus[1m]",
        "fable[1m]",
        "opusplan",
        "default",
    ]


def test_model_aliases_drop_the_trailing_prose():
    assert "or" not in " ".join(_parse_model_aliases(MODEL_OUTPUT))


def test_model_aliases_empty_when_the_line_is_missing():
    assert _parse_model_aliases("something else entirely") == []


def test_current_model_and_effort():
    assert _parse_current_model(MODEL_OUTPUT) == ("Opus 5", "medium")


def test_current_model_without_an_effort_note():
    assert _parse_current_model("Current model: Sonnet 5\n") == ("Sonnet 5", "")


def test_effort_levels_come_from_the_wrapped_help_text():
    assert _parse_effort_levels(HELP_OUTPUT) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_effort_levels_empty_when_the_flag_is_gone():
    assert _parse_effort_levels("Options:\n  --model <model>  Model\n") == []


def _probe_with(
    model_output: str, help_output: str, cwd=None, max_age: float = 0
) -> blindpilot_app.ModelOptions:
    def fake_run(binary, args, cwd, timeout):
        _RUNS.append(list(args))
        return help_output if args == ["--help"] else model_output

    real_find, real_run = blindpilot_app._find_claude, blindpilot_app._run_claude
    blindpilot_app._find_claude = lambda: "claude"  # type: ignore[assignment]
    blindpilot_app._run_claude = fake_run  # type: ignore[assignment]
    try:
        return probe_model_options(os.getcwd() if cwd is None else cwd, max_age)
    finally:
        blindpilot_app._find_claude = real_find  # type: ignore[assignment]
        blindpilot_app._run_claude = real_run  # type: ignore[assignment]


# Every fake CLI invocation any probe in this file made, so tests can assert
# that a cached probe did not shell out again.
_RUNS: list[list[str]] = []


def test_probe_reports_what_the_cli_said():
    options = _probe_with(MODEL_OUTPUT, HELP_OUTPUT)
    assert options.models[0] == "sonnet"
    assert options.efforts == ["low", "medium", "high", "xhigh", "max"]
    assert options.current_model == "Opus 5"
    assert options.current_effort == "medium"
    assert options.error == ""


def test_probe_falls_back_and_says_so_when_the_cli_output_changes():
    options = _probe_with("unrecognizable", "unrecognizable")
    assert options.models == blindpilot_app._FALLBACK_MODELS
    assert options.efforts == blindpilot_app._FALLBACK_EFFORTS
    assert "built-in list" in options.error


def test_probe_falls_back_when_claude_is_not_installed():
    real_find = blindpilot_app._find_claude
    blindpilot_app._find_claude = lambda: None  # type: ignore[assignment]
    try:
        options = probe_model_options(None)
    finally:
        blindpilot_app._find_claude = real_find  # type: ignore[assignment]
    assert options.models == blindpilot_app._FALLBACK_MODELS
    assert "not found" in options.error


def _clear_probe_cache() -> None:
    with blindpilot_app._probe_lock:
        blindpilot_app._probe_cache.clear()


def test_a_recent_probe_is_reused_instead_of_shelling_out():
    _clear_probe_cache()
    _RUNS.clear()
    first = _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/proj")
    assert _RUNS, "the first probe should run the CLI"
    _RUNS.clear()

    second = _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/proj", max_age=900)
    assert _RUNS == [], "a cached probe must not run the CLI again"
    assert second.current_model == first.current_model
    assert second.models == first.models
    assert second.from_cache is True and first.from_cache is False


def test_a_stale_probe_is_discarded():
    _clear_probe_cache()
    _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/proj")
    _RUNS.clear()
    # max_age of 0 means "always ask", which is also the default.
    _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/proj", max_age=0)
    assert _RUNS, "an expired entry must be re-probed"


def test_each_directory_is_cached_separately():
    _clear_probe_cache()
    _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/one")
    _RUNS.clear()
    _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/two", max_age=900)
    assert _RUNS, "another directory must be probed on its own"


def test_a_failed_probe_is_not_cached():
    _clear_probe_cache()
    _probe_with("unrecognizable", "unrecognizable", cwd="/tmp/proj")
    _RUNS.clear()
    _probe_with(MODEL_OUTPUT, HELP_OUTPUT, cwd="/tmp/proj", max_age=900)
    assert _RUNS, "a fallback answer must not be reused"


def test_cached_lookup_never_shells_out():
    _clear_probe_cache()
    _RUNS.clear()
    real_find = blindpilot_app._find_claude
    blindpilot_app._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        assert blindpilot_app.cached_model_options("/tmp/cold", 900) is None
    finally:
        blindpilot_app._find_claude = real_find  # type: ignore[assignment]
    assert _RUNS == []


def test_the_keep_entry_names_the_current_model():
    assert blindpilot_app._keep_choice("Opus 5") == "(CLI default), currently Opus 5"
    assert blindpilot_app._keep_choice("") == "(CLI default)"


def test_the_keep_entry_drops_the_backticks_a_cli_puts_round_the_model_name():
    """A CLI that reports "Current model: `Opus 5`" put literal backticks in
    the combo box and the sentence above it."""
    assert blindpilot_app._keep_choice("`Opus 5`") == "(CLI default), currently Opus 5"
    assert blindpilot_app._plain("`Opus 5`") == "Opus 5"


def _worker_command(**kwargs) -> list[str]:
    """Build a worker, let it launch, and return the argv it tried to run."""
    captured: list[list[str]] = []

    def fake_popen(cmd, **_k):
        # The macOS login-shell PATH probe also goes through Popen; it is not
        # the command line under test. It fails on its own, which is fine —
        # the probe is best-effort.
        if cmd and cmd[0] != "claude":
            return
        captured.append(list(cmd))
        raise OSError("stop here — the command line is all we need")

    real_popen, real_find = subprocess.Popen, blindpilot_app._find_claude
    subprocess.Popen = fake_popen  # type: ignore[assignment]
    blindpilot_app._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        blindpilot_app.ClaudeWorker(
            "hi",
            None,
            os.getcwd(),
            "default",
            on_session=lambda _s: None,
            on_started=lambda: None,
            on_activity=lambda _k, _t: None,
            on_complete=lambda _t: None,
            on_failed=lambda _m: None,
            on_done=lambda: None,
            **kwargs,
        ).run()
    finally:
        subprocess.Popen = real_popen  # type: ignore[assignment]
        blindpilot_app._find_claude = real_find  # type: ignore[assignment]
    return captured[0]


def test_model_and_effort_reach_the_command_line():
    cmd = _worker_command(model="opus", effort="high")
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_unset_model_and_effort_are_left_off_entirely():
    cmd = _worker_command()
    assert "--model" not in cmd
    assert "--effort" not in cmd


def test_effort_can_be_set_on_its_own():
    cmd = _worker_command(effort="max")
    assert "--model" not in cmd
    assert cmd[cmd.index("--effort") + 1] == "max"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)


def test_cached_lookup_never_searches_for_the_cli():
    """It runs on the GUI thread when /model opens, and on macOS finding the
    CLI can mean running a login shell with an eight second timeout."""
    _clear_probe_cache()

    def never(*_args, **_kwargs):
        raise AssertionError("searched for the CLI on the GUI thread")

    real_find, real_backend = blindpilot_app._find_claude, blindpilot_app.find_backend_cli
    blindpilot_app._find_claude = never  # type: ignore[assignment]
    blindpilot_app.find_backend_cli = never  # type: ignore[assignment]
    try:
        assert blindpilot_app.cached_model_options("/tmp/cold", 900) is None
        assert blindpilot_app.cached_model_options("/tmp/cold", 900, backend="codex") is None
    finally:
        blindpilot_app._find_claude = real_find  # type: ignore[assignment]
        blindpilot_app.find_backend_cli = real_backend  # type: ignore[assignment]


def test_a_cached_catalog_is_dropped_when_the_cli_changes(tmp_path):
    """An upgrade changes the model list, so the entry remembers the binary
    that answered and goes when that file does."""
    _clear_probe_cache()
    binary = tmp_path / "claude"
    binary.write_text("version one", encoding="utf-8")
    options = blindpilot_app.ModelOptions(["opus"], ["high"])
    blindpilot_app._remember_model_options("claude", "/tmp/proj", str(binary), options)

    cached = blindpilot_app.cached_model_options("/tmp/proj", 900)
    assert cached is not None and cached.models == ["opus"] and cached.from_cache

    binary.write_text("version two, a longer file", encoding="utf-8")

    assert blindpilot_app.cached_model_options("/tmp/proj", 900) is None


def test_the_effort_box_keeps_a_saved_effort_the_backend_no_longer_lists():
    """A read-only box cannot show a value it does not list, so the saved
    effort silently became "" and OK cleared the tab's override."""
    import pytest

    wx = pytest.importorskip("wx")
    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    try:
        options = blindpilot_app.ModelOptions(["opus"], ["low", "high"])
        dlg = blindpilot_app.ModelDialog(frame, options, "", "xhigh", "Codex")
        try:
            assert dlg.effort_box.GetStringSelection() == "xhigh"
            assert dlg.selection() == ("", "xhigh")
        finally:
            dlg.Destroy()
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
        if owns_app:
            app.Destroy()
