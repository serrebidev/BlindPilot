"""Question types and parsing for BlindPilot.

Every backend BlindPilot drives can stop mid-turn to ask the person a
multiple-choice question, and each one describes that question differently.
This module owns the shared question shape, the parsing of questions a turn
writes into its answer, and the transcript summary of what was asked and
answered, so backend adapters and the window import them from one place.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class QuestionOption:
    """One answer a backend offers for a question.

    `preview` is the longer text some providers attach to an option to show
    what choosing it would mean (Claude Code's AskUserQuestion sends one, a
    mock-up or a plan sketch); most options from most providers have none.
    """

    label: str
    description: str = ""
    preview: str = ""


@dataclass(frozen=True)
class Question:
    """One question a backend has paused its turn to ask.

    ``id`` is whatever the backend needs to match the answer back to the
    question — Codex's question id, or an empty string where the provider keys
    answers by position or by the question's own text instead.
    """

    question: str
    header: str = ""
    options: tuple[QuestionOption, ...] = ()
    multi_select: bool = False
    id: str = ""
    # Whether the provider accepts an answer that is not one of its options.
    # Every backend does today, and all four say so in their tool descriptions
    # ("the client will add a free-form Other option automatically"), so this
    # is the default rather than the exception.
    allow_custom: bool = True
    # Codex alone marks a question whose answer should not be echoed back.
    secret: bool = False


# Given the questions, return one list of chosen answers per question — the
# option labels the person picked, or the text they typed instead — or None if
# they closed the dialog without answering. Called on the worker thread, and
# blocks it: the backend's turn is waiting on this answer.
AskQuestions = Callable[[Sequence[Question]], Optional[list[list[str]]]]


# How much a turn may say after its last question mark before it counts as
# having moved on. A question the answer then answers itself -- "Is the extra
# lock worth it? No, because..." -- is not asked of anybody, and opening a
# dialog on it would interrupt for nothing. A question followed by the
# recommendation the asker would give is still a question, and that is what
# fits inside this.
_QUESTION_TAIL_CHARS = 200

# Markdown that would otherwise be read out as part of the question.
_QUESTION_TRIM = "*_`~ 	"
_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _without_code(text: str) -> str:
    """The text with fenced code taken out.

    A question mark in a code sample is punctuation, not a question, and a
    fence is the one place it turns up often enough to matter.
    """
    kept: list[str] = []
    fence = ""
    for line in text.splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        kept.append(line)
    return "\n".join(kept)


def trailing_question(text: str) -> str:
    """The question a turn ended by asking, or "" if it did not end on one.

    Every backend opens the question dialog from a structured event of its own
    -- AskUserQuestion, request_user_input, clarify, userInput. A model that
    writes its question into its answer sends none of them, so the turn ends
    with nothing said and nothing shown, and the person is left waiting on a
    turn that is itself waiting on them. An interview skill asks that way by
    design, and Command Code has no question tool in a headless run at all, so
    on that backend it is the only way a question ever arrives.

    The judgement is deliberately narrow, because a dialog nobody asked for is
    worse than none: the last question mark has to be near the end, and the
    question is read no further back than the line it sits on. Both are what
    keep a turn that asked itself something in passing from being mistaken for
    one that is waiting.
    """
    body = _without_code(text)
    end = body.rfind("?")
    if end < 0:
        return ""
    if len(body[end + 1 :].strip()) > _QUESTION_TAIL_CHARS:
        return ""
    start = body.rfind("\n", 0, end) + 1
    for mark in ".!?":
        found = body.rfind(mark, start, end)
        if found >= 0:
            start = max(start, found + 1)
    question = " ".join(body[start : end + 1].split())
    question = _BULLET.sub("", question.lstrip("#").strip())
    question = question.strip(_QUESTION_TRIM)
    if not any(character.isalpha() for character in question):
        # A stray question mark on its own is not a question worth stopping for.
        return ""
    return question if question.endswith("?") else f"{question}?"


# The shapes a turn signs off with once its work is already done. "Want me to
# run the tests as well?", "Should I commit this?", "Anything else?" -- none of
# them is in the way of anything. The turn ended, the work it asks about is the
# next thing to ask for, and the reply is the next message whenever it comes,
# so a modal interrupts the answer being read for a question that was never
# blocking. What the dialog is for is the other kind: the turn that stopped
# because only the person can say which name, which of the two, what it should
# be called. That one still opens it.
_OFFER_TO_CARRY_ON = re.compile(
    r"""^(?:(?:and|then|so|also|now|ok|okay|alright),?\s+)*
    (?:
        (?:do\s+you\s+want|would\s+you\s+like|want|like)\s+(?:me|us)\s+to\b
      | (?:should|shall|can|may)\s+(?:i|we)\b
      | (?:ready|happy|ok(?:ay)?)\s+for\s+(?:me|us)\s+to\b
      | (?:is\s+)?(?:that|this|it)\s+(?:ok(?:ay)?|good|fine|right|all\s+right)\b
      | (?:does|do)\s+(?:that|this|they|these)\s+(?:work|help|sound|look|make)\b
      | (?:sound|sounds|look|looks)\s+(?:good|right|ok(?:ay)?)\b
      | (?:make|makes)\s+sense\b
      | any(?:thing)?\s+else\b
      | any\s+(?:other\s+)?questions?\b
      | all\s+good\b
      | good\s+to\s+go\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# A fork named inside the offer. "Should I use tabs or spaces?" is an offer by
# its grammar and a decision by its content, and nobody but the person can
# settle which way the work goes from there.
_A_FORK = re.compile(r"\bor\b", re.IGNORECASE)


def is_an_offer_to_carry_on(question: str) -> bool:
    """Whether a question written into an answer signs off rather than waits.

    A turn that delivered its work and closed by offering the next step, or by
    checking that what it did reads right, is not waiting on anybody: nothing
    is held open, and the reply is simply the next turn. A turn that stopped
    short of the work because it needs to be told something is waiting, and
    that is the one worth a dialog.

    The reading is of the question alone, and deliberately literal: an offer is
    recognised by the way it is put -- "want me to", "should I", "anything
    else" -- because the alternative is guessing at intent, and a dialog that
    opens over a finished turn is exactly the interruption this is meant to
    spare. A yes-or-no offer to do more is a sign-off; an offer that names a
    fork is a decision, and stays a question.
    """
    text = question.strip().strip(_QUESTION_TRIM)
    if not _OFFER_TO_CARRY_ON.match(text):
        return False
    return not _A_FORK.search(text)


# A question block a turn can write into its reply: a fenced code block whose
# info string is `questions`, carrying the same JSON a backend's question tool
# sends. Command Code has no way to be asked through a tool at all -- its
# ask_user_question is withheld from a headless run, because a headless run
# answers its own prompts by taking the first option, and switching it on would
# settle the question with nobody hearing it. Everywhere else the tool is what
# carries the options, so a backend that cannot be asked through one has no way
# to offer any. A written block does, and it is the same shape all four
# providers already send, so the dialog reads it with the very code that reads
# theirs: the same questions, the same options, the same answers back.
#
# Either fence character opens one, and the closer has to be the one that
# opened it. A turn that writes about the block -- quoting one, showing it in a
# diff -- has to reach for `~~~` to keep the sample it is writing from ending
# early, so a block fenced that way is read just the same.
_QUESTION_BLOCK = re.compile(
    r"(?P<fence>```|~~~)[ \t]*questions[ \t]*\r?\n(?P<body>.*?)(?P=fence)",
    re.DOTALL | re.IGNORECASE,
)


def questions_written_in_the_answer(text: str) -> tuple[Question, ...]:
    """The questions a turn wrote into its reply as a structured block.

    Empty when there is no block, when no block is the JSON it claims to be, or
    when nothing in any of them is a question. In every one of those cases the
    prose reading is what is left to try, so a malformed block degrades to
    exactly what the turn would have done without one rather than losing the
    question it was trying to ask.

    The last usable block wins, not simply the last block. A turn that asked
    its question and then went on to write about the block itself -- which is
    what happened the first time this was watched, the question followed by
    four samples of one shown in a diff -- would otherwise lose the question it
    had already asked to the prose that came after it, and be dropped back to
    the prose reading for no reason.

    A single question written as one object rather than a one-item list is
    taken as well, since that is what one question looks like when it is
    written out.
    """
    for block in reversed(list(_QUESTION_BLOCK.finditer(text))):
        try:
            raw = json.loads(block.group("body"))
        except (ValueError, TypeError):
            continue
        if isinstance(raw, dict):
            raw = [raw]
        questions = _questions(raw, multi_key="multiSelect", id_key="id")
        if questions:
            return questions
    return ()


def question_summary(questions: Sequence[Question], answers: Optional[list[list[str]]]) -> str:
    """One row for the transcript saying what was asked and what was said.

    Both halves are kept: read back later, a bare "Spaces" says nothing, and
    the answer is the reason the rest of the turn went the way it did.
    """
    if answers is None:
        asked = " ".join(question.question for question in questions)
        return f"Question left unanswered: {asked}"
    parts = []
    for index, question in enumerate(questions):
        chosen = answers[index] if index < len(answers) else []
        if question.secret:
            # Codex marks a question whose answer is a secret. The transcript
            # is read back, copied, and saved, so it gets the fact of an answer
            # and not the answer itself.
            parts.append(
                f'You answered "{question.question}".'
                if chosen
                else f'You left "{question.question}" unanswered.'
            )
            continue
        said = ", ".join(chosen) if chosen else "nothing"
        parts.append(f'You answered "{question.question}" with "{said}".')
    return " ".join(parts)


def _question_options(raw: object) -> tuple[QuestionOption, ...]:
    """The labelled options of a question, as Codex and opencode both send them."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        QuestionOption(
            str(option["label"]),
            str(option.get("description") or ""),
            str(option.get("preview") or ""),
        )
        for option in raw
        if isinstance(option, dict) and option.get("label")
    )


def _questions(
    raw: object,
    *,
    multi_key: str = "",
    custom_key: str = "",
    secret_key: str = "",
    id_key: str = "",
) -> tuple[Question, ...]:
    """Read a provider's list of question dicts into BlindPilot's own shape.

    All four providers send the same thing — a list of dicts carrying a
    question, a header and labelled options — and differ only in what they
    call the flags beside them, and in which of those they send at all. Each
    key is therefore named by its caller, and a key left empty means the
    provider has nothing to say on that point and the `Question` default
    stands: only Codex numbers its questions or marks an answer secret, only
    opencode and Claude Code ask for more than one answer, and every one of
    them lets an answer be typed instead of picked.
    """
    if not isinstance(raw, list):
        return ()
    questions: list[Question] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("question") or "").strip()
        if not text:
            continue
        options = _question_options(entry.get("options"))
        questions.append(
            Question(
                question=text,
                header=str(entry.get("header") or ""),
                options=options,
                multi_select=bool(entry.get(multi_key)) if multi_key else False,
                # A question with no options is a free-text one whatever the
                # flag says, so the two together decide whether typing is
                # offered.
                allow_custom=(bool(entry.get(custom_key)) or not options) if custom_key else True,
                secret=bool(entry.get(secret_key)) if secret_key else False,
                id=str(entry.get(id_key) or "") if id_key else "",
            )
        )
    return tuple(questions)
