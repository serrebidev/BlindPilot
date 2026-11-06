"""The focused modules own their symbols.

Question types and parsing live in ``questions``, the question dialog in
``question_dialog``, shared process helpers in ``process_utils``, and the Claude
turn worker in ``claude_worker``. The old modules import what they still use
from there; they do not keep compatibility re-exports of the moved symbols.
"""

from __future__ import annotations

import agent_backends
import blindpilot_app
import claude_session
import claude_worker
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
