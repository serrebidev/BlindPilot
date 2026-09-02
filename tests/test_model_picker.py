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

import claude_reader  # noqa: E402
from claude_reader import (  # noqa: E402
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
    monkeypatch.setattr(claude_reader.shutil, "which", lambda _name: "npm")

    assert _npm_update_argv(BACKEND_CODEX)[-1] == "@openai/codex@latest"
    assert _npm_update_argv(BACKEND_FREEBUFF)[-1] == "freebuff@latest"


def test_claude_update_repairs_an_old_windows_launcher(monkeypatch, tmp_path):
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    newest = tmp_path / ".local" / "share" / "claude" / "versions" / "2.1.226"
    launcher.parent.mkdir(parents=True)
    newest.parent.mkdir(parents=True)
    launcher.write_text("old", encoding="utf-8")
    newest.write_text("new", encoding="utf-8")
    monkeypatch.setattr(claude_reader.platform, "system", lambda: "Windows")
    monkeypatch.setattr(claude_reader.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        claude_reader,
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
) -> claude_reader.ModelOptions:
    def fake_run(binary, args, cwd, timeout):
        _RUNS.append(list(args))
        return help_output if args == ["--help"] else model_output

    real_find, real_run = claude_reader._find_claude, claude_reader._run_claude
    claude_reader._find_claude = lambda: "claude"  # type: ignore[assignment]
    claude_reader._run_claude = fake_run  # type: ignore[assignment]
    try:
        return probe_model_options(os.getcwd() if cwd is None else cwd, max_age)
    finally:
        claude_reader._find_claude = real_find  # type: ignore[assignment]
        claude_reader._run_claude = real_run  # type: ignore[assignment]


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
    assert options.models == claude_reader._FALLBACK_MODELS
    assert options.efforts == claude_reader._FALLBACK_EFFORTS
    assert "built-in list" in options.error


def test_probe_falls_back_when_claude_is_not_installed():
    real_find = claude_reader._find_claude
    claude_reader._find_claude = lambda: None  # type: ignore[assignment]
    try:
        options = probe_model_options(None)
    finally:
        claude_reader._find_claude = real_find  # type: ignore[assignment]
    assert options.models == claude_reader._FALLBACK_MODELS
    assert "not found" in options.error


def _clear_probe_cache() -> None:
    with claude_reader._probe_lock:
        claude_reader._probe_cache.clear()


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
    real_find = claude_reader._find_claude
    claude_reader._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        assert claude_reader.cached_model_options("/tmp/cold", 900) is None
    finally:
        claude_reader._find_claude = real_find  # type: ignore[assignment]
    assert _RUNS == []


def test_the_keep_entry_names_the_current_model():
    assert claude_reader._keep_choice("Opus 5") == "(CLI default) — currently Opus 5"
    assert claude_reader._keep_choice("") == "(CLI default)"


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

    real_popen, real_find = subprocess.Popen, claude_reader._find_claude
    subprocess.Popen = fake_popen  # type: ignore[assignment]
    claude_reader._find_claude = lambda: "claude"  # type: ignore[assignment]
    try:
        claude_reader.ClaudeWorker(
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
        claude_reader._find_claude = real_find  # type: ignore[assignment]
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
