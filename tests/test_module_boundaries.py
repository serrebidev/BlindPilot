"""The focused modules from issues #135-#137 own their symbols.

Question types and parsing live in ``questions``, the question dialog in
``question_dialog``, and shared process helpers in ``process_utils``. The
old modules import what they still use from there; they do not keep
compatibility re-exports of the moved symbols.
"""

from __future__ import annotations

import agent_backends
import blindpilot_app
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
