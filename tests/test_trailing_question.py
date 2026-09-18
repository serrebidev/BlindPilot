"""Spotting a turn that ended by asking the person something, in prose.

Every backend's question dialog is opened by a structured event -- Claude
Code's AskUserQuestion, Codex's request_user_input, opencode's question,
Hermes' clarify, Muse's userInput. A model that writes its question into its
answer instead sends no such event, so nothing announces it and no dialog
opens: the turn simply ends, and a listener is left with no sign that anything
is waiting on them. Skills that run an interview -- "grill me" is the one that
brought this in -- ask that way as a matter of course, and Command Code has
ask_user_question withheld from headless runs entirely, so there it is the
only way a question can ever arrive.

What is checked here is the judgement call: a turn that ends on a question is
found, and a turn that merely contains a question mark somewhere is left
alone, because a dialog that opens when nobody asked anything is worse than
no dialog at all.
"""

from __future__ import annotations

from agent_backends import trailing_question


def test_a_turn_that_ends_on_a_question_is_found():
    assert trailing_question("Which name do you prefer?") == "Which name do you prefer?"


def test_a_turn_that_ends_on_a_statement_is_not_a_question():
    assert trailing_question("Done. The config file is written and the tests pass.") == ""


def test_empty_and_blank_answers_ask_nothing():
    assert trailing_question("") == ""
    assert trailing_question("   \n\n  ") == ""


def test_only_the_question_is_taken_not_the_paragraph_before_it():
    text = (
        "I looked at the three files that read this setting. All of them take a "
        "string today.\n\nShould the new setting be a string as well?"
    )
    assert trailing_question(text) == "Should the new setting be a string as well?"


def test_a_recommendation_after_the_question_still_counts():
    # The shape the grilling skill asks for: the question, then the answer it
    # would give. The turn is still waiting on a person.
    text = "What should the config file be called?\n\nMy recommendation: `blindpilot.toml`."
    assert trailing_question(text) == "What should the config file be called?"


def test_a_question_the_answer_moves_on_from_is_not_asked_of_anybody():
    # A question mark in the middle of an explanation the turn then continues
    # past. Nothing is waiting: the model asked itself and answered.
    text = (
        "Is the extra lock worth it? No. The writer already holds the state lock "
        "for the whole of that call, so a second one would only ever be taken by "
        "a thread that is about to block on the first, and the ordering between "
        "them is what deadlocks. I removed it and the contract tests still pass, "
        "so the behaviour is unchanged."
    )
    assert trailing_question(text) == ""


def test_a_question_mark_inside_a_code_block_is_not_a_question():
    text = "Here is the check:\n\n```python\nif name.endswith('?'):\n    ask()\n```\n"
    assert trailing_question(text) == ""


def test_a_code_block_does_not_hide_a_real_question_after_it():
    text = "Here it is:\n\n```python\nx = 1\n```\n\nDoes that cover the empty case?"
    assert trailing_question(text) == "Does that cover the empty case?"


def test_bullets_above_the_question_are_left_out_of_it():
    text = (
        "Two ways to do it:\n\n- Keep the lock and narrow what it covers\n"
        "- Drop the lock and make the state immutable\n\nWhich would you rather?"
    )
    assert trailing_question(text) == "Which would you rather?"


def test_markdown_emphasis_is_not_read_out_as_part_of_the_question():
    assert trailing_question("**Which one, then?**") == "Which one, then?"


def test_a_heading_style_question_keeps_its_words_and_loses_its_hashes():
    assert trailing_question("## Where should this live?") == "Where should this live?"


def test_the_last_question_is_the_one_asked():
    text = "Do you want tests as well?\n\nAnd should they run in CI?"
    assert trailing_question(text) == "And should they run in CI?"
