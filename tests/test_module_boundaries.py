"""The focused modules own their symbols.

Question types and parsing live in ``questions``, the question dialog in
``question_dialog``, shared process helpers in ``process_utils``, the Claude
turn worker in ``claude_worker``, the sound cues in ``earcons``, the PATH rules
in ``install_path`` and the portable Node / npm machinery in ``portable_node``.
The old modules import what they still use from there; they do not keep
compatibility re-exports of the moved symbols.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agent_backends
import blindpilot_app
import claude_session
import claude_worker
import earcons
import install_path
import installers
import portable_node
import process_utils
import question_dialog
import questions


def test_question_types_and_parsing_live_in_questions():
    assert questions.Question.__module__ == "questions"
    assert questions.QuestionOption.__module__ == "questions"
    for name in (
        "trailing_question",
        "is_an_offer_to_carry_on",
        "questions_written_in_the_answer",
        "question_summary",
    ):
        assert getattr(questions, name).__module__ == "questions", name
    for name in (
        "Question",
        "QuestionOption",
        "AskQuestions",
        "trailing_question",
        "is_an_offer_to_carry_on",
        "questions_written_in_the_answer",
        "question_summary",
    ):
        assert not hasattr(agent_backends, name), name


def test_process_helpers_live_in_process_utils():
    for name in (
        "no_window_kwargs",
        "own_group_kwargs",
        "end_process_group",
        "hide_console_windows",
    ):
        assert getattr(process_utils, name).__module__ == "process_utils", name
        assert not hasattr(agent_backends, name), name


def test_owned_process_still_owns_windows_job_launch():
    import owned_process

    assert owned_process.owned_popen.__module__ == "owned_process"
    assert not hasattr(process_utils, "owned_popen")


def test_question_dialog_lives_in_question_dialog():
    assert question_dialog.QuestionDialog.__module__ == "question_dialog"
    assert blindpilot_app.QuestionDialog is question_dialog.QuestionDialog


def test_the_claude_turn_worker_lives_in_claude_worker():
    """The window holds the worker; it does not define it or hand it out.

    ``blindpilot_app`` reaches the class as ``claude_worker.ClaudeWorker``, so
    the name is not an attribute of the window module at all: nothing can keep
    reaching it there by accident, and the move cannot be half-done.
    """
    assert claude_worker.ClaudeWorker.__module__ == "claude_worker"
    assert not hasattr(blindpilot_app, "ClaudeWorker")
    # What the worker alone used moved with it, for the same reason.
    for name in (
        "_tool_result_text",
        "_claude_questions",
        "_looks_like_auth_error",
        "_looks_like_refusal",
    ):
        assert getattr(claude_worker, name).__module__ == "claude_worker", name
        assert not hasattr(blindpilot_app, name), name


def test_the_claude_cli_search_lives_in_claude_session():
    """The window's installer and the turn worker both need it, and neither
    may import the other, so it lives in the module they share."""
    assert claude_session._find_claude.__module__ == "claude_session"
    assert claude_session._fallback_claude_paths.__module__ == "claude_session"
    assert claude_worker._find_claude is claude_session._find_claude


def test_the_sound_cues_live_in_earcons():
    """The window constructs the cues; it does not define them.

    ``Earcons`` is reached as ``earcons.Earcons``, so the name is not an
    attribute of the window module and nothing can keep reaching it there by
    accident. The progress mode comes from the settings object the frame hands
    over, so the cue module needs nothing else from the window.
    """
    assert earcons.Earcons.__module__ == "earcons"
    assert not hasattr(blindpilot_app, "Earcons")
    # Only the cues the window still uses came back with the import.
    assert not hasattr(blindpilot_app, "_faster_wav")
    assert blindpilot_app.CUE_PERIODIC is earcons.CUE_PERIODIC
    assert blindpilot_app._valid_progress_cue is earcons._valid_progress_cue


def test_the_path_helpers_live_in_install_path():
    """The window registers a folder on PATH; it does not implement the rules.

    ``blindpilot_app`` reaches them as ``install_path.ensure_on_path``, so the
    names are not attributes of the window module: nothing can keep reaching
    them there by accident, and the move cannot be half-done.
    """
    for name in (
        "_same_dir",
        "_bundle_dir",
        "path_without_bundle_entries",
        "keep_bundle_off_child_path",
        "_windows_persistent_path_dirs",
        "_posix_persistent_path_dirs",
        "_is_on_persistent_path",
        "_broadcast_environment_change",
        "_add_to_process_path",
        "_path_with_entry",
        "_shell_profile_file",
        "_path_export_line",
        "ensure_on_posix_path",
        "ensure_on_path",
        "ensure_on_windows_path",
    ):
        assert getattr(install_path, name).__module__ == "install_path", name
        assert not hasattr(blindpilot_app, name), name
    assert install_path.PATH_STANZA_MARKER == "# Added by BlindPilot"
    assert not hasattr(blindpilot_app, "PATH_STANZA_MARKER")


def test_the_portable_node_helpers_live_in_portable_node():
    """The window calls the Node/npm installer; it does not implement it.

    ``blindpilot_app`` reaches them as ``portable_node.install_portable_node``
    and the like, so the names are not attributes of the window module:
    nothing can keep reaching them there by accident, and the move cannot be
    half-done. The npm command lines, the download and the logged process
    runner all moved, so a clean-machine install runs the same code wherever it
    is called from.
    """
    for name in (
        "_backend_installs_with_npm",
        "_managed_npm_prefix",
        "_managed_npm_bin_dir",
        "_node_runtime_root",
        "_node_archive_spec",
        "_managed_node_dir",
        "_managed_npm",
        "activate_managed_cli_paths",
        "_find_npm",
        "_automatic_npm_install_available",
        "_fetch_url_bytes",
        "_safe_extract_node_archive",
        "install_portable_node",
        "_npm_environment",
        "_npm_install_argv",
        "_npm_update_argv",
        "_managed_backend_binary",
        "_run_logged_process",
    ):
        assert getattr(portable_node, name).__module__ == "portable_node", name
        assert not hasattr(blindpilot_app, name), name
    # The installer that dispatches per backend moved with them: it is the
    # module that drops the Codex and OpenCode processes before npm replaces
    # their executables, and the window now only calls it.
    assert hasattr(installers, "_install_npm_backend")
    assert not hasattr(blindpilot_app, "_install_npm_backend")
    assert not hasattr(portable_node, "_install_npm_backend")


def test_the_installer_and_updater_tables_live_in_installers():
    """The window calls the install and update paths; it does not implement them.

    ``blindpilot_app`` reaches them as ``installers.install_backend`` and
    ``installers.update_backend``, so the names are not attributes of the window
    module: nothing can keep reaching them there by accident, and the move
    cannot be half-done. One table per direction means a new backend has to
    answer both questions, or its completeness test fails.
    """
    for name in (
        "_powershell_exe",
        "_script_installer_argv",
        "_install_argv",
        "_hermes_install_argv",
        "_missing_prereq_message",
        "_hermes_missing_prereq_message",
        "_uv_roaming_python_dir",
        "_uv_local_python_dir",
        "_windows_file_attributes",
        "_folder_is_behind_reparse_point",
        "_folder_is_cloud_synced",
        "_remember_windows_env_var",
        "_hermes_installer_env",
        "_hermes_bootstrap_failure",
        "_hermes_binary_after_install",
        "_muse_install_argv",
        "_muse_missing_prereq_message",
        "_path_shells",
        "_install_failure_message",
        "_install_uv",
        "_install_npm_backend",
        "_executable_version",
        "_repair_claude_native_update",
        "_drop_codex_server",
        "_update_claude",
        "_update_npm_backend",
        "_update_hermes",
        "_update_muse",
        "_update_museai",
        "install_hermes",
        "install_antigravity",
        "install_muse",
        "install_claude",
        "install_museai",
        "install_backend",
        "update_backend",
    ):
        assert getattr(installers, name).__module__ == "installers", name
        assert not hasattr(blindpilot_app, name), name
    assert installers.WINDOWS_INSTALL_PS1_URL == "https://claude.ai/install.ps1"
    assert not hasattr(blindpilot_app, "WINDOWS_INSTALL_PS1_URL")


def test_installers_never_imports_the_window_module():
    """The dependency runs one way: the window imports the installer.

    A cycle here would put wxPython behind every install, so the module is
    checked rather than trusted.
    """
    source = (Path(__file__).resolve().parent.parent / "installers.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "blindpilot_app" not in imported
    assert "wx" not in imported


def test_portable_node_never_imports_the_window_module():
    """The dependency runs one way: the window imports the installer.

    The installer keeps whatever it needs from the window in the modules the
    window already imports from, so importing it can never load wxPython or
    pull the window in behind it.
    """
    source = (Path(__file__).resolve().parent.parent / "portable_node.py").read_text(
        encoding="utf-8"
    )
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "blindpilot_app" not in imported
    assert "wx" not in imported
