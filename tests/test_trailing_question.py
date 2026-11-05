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

import pytest

from questions import (
    is_an_offer_to_carry_on,
    questions_written_in_the_answer,
    trailing_question,
)


def test_a_turn_that_ends_on_a_question_is_found():
    assert trailing_question("Which name do you prefer?") == "Which name do you prefer?"


def test_a_turn_that_ends_on_a_statement_is_not_a_question():
    assert trailing_question("Done. The config file is written and the tests pass.") == ""


def test_empty_and_blank_answers_ask_nothing():
    assert trailing_question("") == ""
    assert trailing_question("   \n\n  ") == ""


@pytest.mark.parametrize(
    ("text", "question"),
    [
        (
            "I looked at the three files that read this setting. All of them take a "
            "string today.\n\nShould the new setting be a string as well?",
            "Should the new setting be a string as well?",
        ),
        (
            "Two ways to do it:\n\n- Keep the lock and narrow what it covers\n"
            "- Drop the lock and make the state immutable\n\nWhich would you rather?",
            "Which would you rather?",
        ),
    ],
    ids=["a paragraph above it", "a bullet list above it"],
)
def test_only_the_question_is_taken_not_what_leads_up_to_it(text, question):
    assert trailing_question(text) == question


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


def test_markdown_emphasis_is_not_read_out_as_part_of_the_question():
    assert trailing_question("**Which one, then?**") == "Which one, then?"


def test_a_heading_style_question_keeps_its_words_and_loses_its_hashes():
    assert trailing_question("## Where should this live?") == "Where should this live?"


def test_the_last_question_is_the_one_asked():
    text = "Do you want tests as well?\n\nAnd should they run in CI?"
    assert trailing_question(text) == "And should they run in CI?"


# ----- Which of those questions is the turn actually waiting on -----
def test_an_offer_to_do_more_work_is_a_sign_off():
    # The work is done and this asks about the next piece of it. Nothing is
    # held open: the answer is the next turn whenever it is given.
    for offer in (
        "Want me to run the tests as well?",
        "Do you want me to commit this?",
        "Would you like me to open a PR?",
        "Should I also update the README?",
        "Shall I push it?",
        "And should I bump the version too?",
        "Ready for me to delete the old one?",
    ):
        assert is_an_offer_to_carry_on(offer), offer


def test_a_closing_courtesy_is_a_sign_off():
    for closer in (
        "Anything else?",
        "Any other questions?",
        "Does that make sense?",
        "Sound good?",
        "Is that okay?",
        "All good?",
        "Good to go?",
    ):
        assert is_an_offer_to_carry_on(closer), closer


def test_a_question_only_the_person_can_settle_is_not_a_sign_off():
    for question in (
        "Which name do you prefer?",
        "What should the config file be called?",
        "Which would you rather?",
        "Do you want tests as well?",
        "Should they run in CI?",
        "How many retries do you want?",
    ):
        assert not is_an_offer_to_carry_on(question), question


def test_an_offer_that_names_a_fork_is_still_a_question():
    # An offer by its grammar, a decision by its content. Which way the work
    # goes from here is not something the turn can pick for itself.
    assert not is_an_offer_to_carry_on("Should I use tabs or spaces?")
    assert not is_an_offer_to_carry_on("Want me to keep the lock or drop it?")


def test_markdown_around_a_sign_off_does_not_hide_it():
    assert is_an_offer_to_carry_on("**Want me to commit it?**")


# ----- The block a turn writes its questions into, options and all -----
#
# Command Code cannot be asked through a question tool at all, so on that
# backend a block written into the reply is the only way any options reach the
# dialog. Everywhere else it is a second way to say the same thing.
_BLOCK = (
    "```questions\n"
    '[{"question": "Tabs or spaces?", "header": "Indent",\n'
    '  "options": [{"label": "Tabs", "description": "One per level."},\n'
    '              {"label": "Spaces"}]}]\n'
    "```\n"
)


def test_a_question_block_carries_its_questions_and_their_options():
    questions = questions_written_in_the_answer("Settling two things.\n\n" + _BLOCK)

    assert len(questions) == 1
    assert questions[0].question == "Tabs or spaces?"
    assert questions[0].header == "Indent"
    assert [option.label for option in questions[0].options] == ["Tabs", "Spaces"]
    assert questions[0].options[0].description == "One per level."


def test_a_block_asks_every_question_in_it():
    text = (
        "```questions\n"
        '[{"question": "Which name?"}, {"question": "Which path?", '
        '"options": [{"label": "/a"}, {"label": "/b"}]}]\n'
        "```\n"
    )

    questions = questions_written_in_the_answer(text)

    assert [question.question for question in questions] == ["Which name?", "Which path?"]


def test_a_block_can_ask_for_more_than_one_answer():
    text = (
        "```questions\n"
        '[{"question": "Which of these?", "multiSelect": true, '
        '"options": [{"label": "a"}, {"label": "b"}]}]\n'
        "```\n"
    )

    assert questions_written_in_the_answer(text)[0].multi_select is True


def test_a_question_with_no_options_has_none_to_offer():
    text = '```questions\n[{"question": "What should it be called?"}]\n```\n'

    questions = questions_written_in_the_answer(text)

    assert len(questions) == 1
    assert questions[0].options == ()
    # Nothing to pick from, so typing is the only answer left.
    assert questions[0].allow_custom is True


def test_a_reply_with_no_block_has_no_questions_to_read():
    assert questions_written_in_the_answer("Which name do you want?") == ()


def test_a_block_that_is_not_json_is_ignored():
    # A block with nothing usable in it leaves the prose reading to try, which
    # is what the turn would have had without a block at all.
    assert questions_written_in_the_answer("```questions\nnot json at all\n```\n") == ()


def test_a_fence_that_does_not_say_questions_is_not_a_block():
    # Every other fenced block in a reply is somebody's code sample.
    text = '```json\n[{"question": "Not this one?"}]\n```\n'

    assert questions_written_in_the_answer(text) == ()


def test_the_last_block_is_the_one_asked():
    draft = '```questions\n[{"question": "The draft?"}]\n```\n'
    revision = '```questions\n[{"question": "The revision?"}]\n```\n'

    questions = questions_written_in_the_answer(draft + "\n" + revision)

    assert [question.question for question in questions] == ["The revision?"]


def test_a_block_is_not_read_as_prose_as_well():
    # The prose reading takes fenced code out, so a turn that asked with a
    # block is not asked a second time over the question mark inside it.
    assert trailing_question(_BLOCK) == ""


def test_a_later_block_that_is_not_the_json_does_not_hide_the_question():
    # The shape watched in the wild: the question first, then the turn going on
    # to write about the block -- a sample of one, shown in a diff, where the
    # diff's own + on every line is what makes it not JSON. Reading only the
    # last block would find the sample and drop back to the prose reading with
    # the question sitting right there above it.
    sample = '```questions\n+[{"question": "...", "options": [{"label": "..."}]}]\n+```\n'

    questions = questions_written_in_the_answer(_BLOCK + "\n" + sample)

    assert [question.question for question in questions] == ["Tabs or spaces?"]


def test_a_question_block_may_be_fenced_with_tildes():
    # A block written inside another fenced sample has to use ~~~ to keep the
    # sample from ending early, so a block fenced that way is read too.
    text = '~~~questions\n[{"question": "Tabs or spaces?"}]\n~~~\n'

    questions = questions_written_in_the_answer(text)

    assert [question.question for question in questions] == ["Tabs or spaces?"]


def test_a_single_question_may_be_written_as_one_object():
    # One question written out on its own is an object, not a one-item list.
    text = '```questions\n{"question": "What should it be called?"}\n```\n'

    questions = questions_written_in_the_answer(text)

    assert [question.question for question in questions] == ["What should it be called?"]
