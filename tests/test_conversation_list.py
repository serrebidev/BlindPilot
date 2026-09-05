"""The wrapping Responses list and what it tells a screen reader."""

from __future__ import annotations

from markdown_rows import Row

import conversation_list as cl


def test_every_row_kind_has_a_style():
    for kind in (
        "header",
        "prose",
        "heading",
        "list",
        "quote",
        "code",
        "you",
        "thinking",
        "tool",
        "result",
        "error",
    ):
        style = cl.style_for(kind)
        assert isinstance(style, cl.RowStyle)


def test_headings_and_the_persons_own_messages_are_bold():
    assert cl.style_for("you").bold
    assert cl.style_for("header").bold
    assert cl.style_for("heading").bold
    assert not cl.style_for("prose").bold


def test_reasoning_is_muted_code_is_mono_tools_are_indented_errors_are_marked():
    assert cl.style_for("thinking").muted
    assert cl.style_for("code").mono
    assert cl.style_for("tool").indented and cl.style_for("result").indented
    assert cl.style_for("error").error


def test_an_unknown_kind_draws_as_prose():
    assert cl.style_for("whatever") == cl.style_for("prose")


def test_strings_become_prose_rows_and_rows_pass_through():
    row = Row(kind="code", label="Code, Python, 2 lines", payload="x=1", response_number=1)
    out = cl.as_rows(["first", row])
    assert out[0].kind == "prose" and out[0].label == "first" and out[0].payload == "first"
    assert out[1] is row
