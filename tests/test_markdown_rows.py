"""Unit tests for the keystone Markdown-to-rows parser.

Run from the project root with the venv active:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_markdown_rows.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from markdown_rows import Row, parse_response, reassemble  # noqa: E402


def kinds(rows):
    return [r.kind for r in rows]


def test_header_is_first_and_carries_full_text():
    text = "Hello there."
    rows = parse_response(text, 1)
    assert rows[0].kind == "header"
    assert rows[0].label == "Response 1"
    assert rows[0].payload == "Hello there."
    assert rows[0].response_number == 1


def test_single_paragraph():
    rows = parse_response("Just one paragraph of prose.", 3)
    assert kinds(rows) == ["header", "prose"]
    assert rows[1].payload == "Just one paragraph of prose."
    assert rows[1].label == "Just one paragraph of prose."
    assert rows[1].response_number == 3


def test_two_paragraphs_become_two_prose_rows():
    text = "First paragraph.\n\nSecond paragraph."
    rows = parse_response(text, 1)
    assert kinds(rows) == ["header", "prose", "prose"]
    assert rows[1].payload == "First paragraph."
    assert rows[2].payload == "Second paragraph."


def test_fenced_code_is_pristine_with_language_and_line_count():
    text = "Here is code:\n\n```python\nx = 1\ny = 2\n```"
    rows = parse_response(text, 2)
    assert kinds(rows) == ["header", "prose", "code"]
    code = rows[2]
    assert code.payload == "x = 1\ny = 2"  # no backticks, no language line
    assert "`" not in code.payload
    assert code.language == "Python"
    assert code.label == "Code, Python, 2 lines"


def test_single_line_code_says_line_singular():
    rows = parse_response("```js\nconsole.log(1)\n```", 1)
    code = [r for r in rows if r.kind == "code"][0]
    assert code.label == "Code, JavaScript, 1 line"


def test_code_without_language():
    rows = parse_response("```\nplain\n```", 1)
    code = [r for r in rows if r.kind == "code"][0]
    assert code.language is None
    assert code.label == "Code, 1 line"


def test_multiple_code_blocks_each_own_row():
    text = "Intro.\n\n```python\na = 1\n```\n\nMiddle prose.\n\n```sql\nSELECT 1;\n```"
    rows = parse_response(text, 1)
    assert kinds(rows) == ["header", "prose", "code", "prose", "code"]
    codes = [r for r in rows if r.kind == "code"]
    assert codes[0].payload == "a = 1"
    assert codes[0].language == "Python"
    assert codes[1].payload == "SELECT 1;"
    assert codes[1].language == "SQL"


def test_heading_becomes_labelled_prose_row():
    rows = parse_response("## Setup steps\n\nDo the thing.", 1)
    assert kinds(rows) == ["header", "heading", "prose"]
    heading = rows[1]
    assert heading.label == "Setup steps"  # plain text: no '##', no "Heading:" prefix
    assert heading.payload == "Setup steps"


def test_bullet_list_is_one_row_by_default():
    text = "Steps:\n\n- First item\n- Second item\n- Third item"
    rows = parse_response(text, 1)
    assert kinds(rows) == ["header", "prose", "list"]
    lst = rows[2]
    assert lst.kind == "list"
    assert "First item" in lst.payload
    assert "Second item" in lst.payload
    assert "Third item" in lst.payload
    assert lst.label.startswith("List: ")


def test_blockquote_row():
    rows = parse_response("> quoted wisdom", 1)
    assert kinds(rows) == ["header", "quote"]
    assert rows[1].label.startswith("Quote: ")
    assert "quoted wisdom" in rows[1].payload


def test_code_nested_in_list_is_rescued_as_its_own_code_row():
    text = "- do this:\n\n  ```python\n  z = 9\n  ```\n"
    rows = parse_response(text, 1)
    code = [r for r in rows if r.kind == "code"]
    assert len(code) == 1
    assert code[0].payload.strip() == "z = 9"
    assert "`" not in code[0].payload


def test_empty_response_is_header_only():
    rows = parse_response("", 5)
    assert kinds(rows) == ["header"]
    assert rows[0].payload == ""


def test_whitespace_only_response_is_header_only():
    rows = parse_response("   \n\n  ", 1)
    assert kinds(rows) == ["header"]


def test_long_prose_label_is_full_text_not_truncated():
    long_text = ("word " * 100).strip()
    rows = parse_response(long_text, 1)
    prose = rows[1]
    # The label is the full paragraph (the screen reader reads it) — no
    # ellipsis, no truncation.
    assert "…" not in prose.label
    assert prose.label == long_text
    assert prose.payload == long_text


def test_response_number_propagates_to_all_rows():
    text = "Para.\n\n```py\nx=1\n```"
    rows = parse_response(text, 7)
    assert all(r.response_number == 7 for r in rows)


def test_reassemble_returns_full_response():
    text = "Intro.\n\n```python\na = 1\n```"
    rows = parse_response(text, 1)
    assert reassemble(rows, 1) == text


def test_reassemble_fences_code_rows_that_have_no_language():
    rows = [
        Row(kind="prose", label="a", payload="Alpha", response_number=2),
        Row(kind="code", label="b", payload="x=1", response_number=2),
    ]
    assert reassemble(rows, 2) == "Alpha\n\n```\nx=1\n```"


def test_mixed_document_order_is_preserved():
    text = "# Title\n\nOpening prose.\n\n```bash\nls -la\n```\n\n- one\n- two\n\nClosing remark."
    rows = parse_response(text, 1)
    assert kinds(rows) == ["header", "heading", "prose", "code", "list", "prose"]
    assert rows[1].label == "Title"  # no '#', no "Heading:" prefix
    assert rows[3].payload == "ls -la"
    assert rows[3].language == "Bash"
    assert rows[5].payload == "Closing remark."


def test_emojis_are_stripped_from_prose():
    rows = parse_response("Done ✅ and shipped \U0001f680 now \U0001f389", 1)
    prose = rows[1]
    assert prose.payload == "Done and shipped now"
    assert all(ord(c) < 0x2600 for c in prose.payload)


def test_emoji_only_response_has_no_empty_prose_row():
    rows = parse_response("\U0001f389\U0001f389", 1)
    # The emoji-only paragraph collapses to nothing, so no prose row is emitted.
    assert kinds(rows) == ["header"]


def test_markdown_emphasis_and_code_markers_stripped_from_prose():
    rows = parse_response("This is **bold** and `code` and *italic* text.", 1)
    prose = rows[1]
    assert prose.payload == "This is bold and code and italic text."
    assert "*" not in prose.payload
    assert "`" not in prose.payload


def test_link_reduced_to_its_text():
    rows = parse_response("See [the docs](https://example.com/x) for more.", 1)
    prose = rows[1]
    assert prose.payload == "See the docs for more."
    assert "http" not in prose.payload


def test_heading_hashes_stripped():
    rows = parse_response("### A Heading Here", 1)
    assert rows[1].kind == "heading"
    assert rows[1].payload == "A Heading Here"


def test_code_is_never_stripped_of_symbols():
    # Markdown-ish characters inside code must survive verbatim.
    text = "```python\nx = a ** 2  # power\nprint(f'`{x}`')\n```"
    rows = parse_response(text, 1)
    code = [r for r in rows if r.kind == "code"][0]
    assert code.payload == "x = a ** 2  # power\nprint(f'`{x}`')"


def test_emoji_in_code_is_removed_but_code_structure_kept():
    # Emoji are stripped everywhere, but code whitespace/indentation is intact.
    text = "```python\nif x:\n    do()  ✅\n```"
    rows = parse_response(text, 1)
    code = [r for r in rows if r.kind == "code"][0]
    assert code.payload == "if x:\n    do()  "  # indentation preserved, emoji gone


def test_html_block_text_is_kept_as_a_prose_row():
    # markdown-it parses a line starting with a block tag as an html_block
    # token. Its words are the answer too; only the tags go.
    text = "Intro.\n\n<details>\n<summary>More</summary>\nHidden text.\n</details>\n\nAfter."
    rows = parse_response(text, 1)
    assert kinds(rows) == ["header", "prose", "prose", "prose"]
    assert rows[2].payload == "More\nHidden text."
    assert rows[2].label == "More Hidden text."
    assert rows[3].payload == "After."


def test_a_table_is_read_one_row_at_a_time_in_plain_words():
    text = "Files:\n\n| Name | Lines |\n|---|---|\n| a.py | 12 |\n| b.py | 3 |\n\nDone."
    rows = parse_response(text, 1)
    assert kinds(rows) == ["header", "prose", "prose", "prose", "prose", "prose"]
    assert [row.label for row in rows[2:5]] == [
        "Row: Name, Lines",
        "Row: a.py, 12",
        "Row: b.py, 3",
    ]
    assert rows[2].payload == "Name, Lines"
    assert "|" not in "".join(row.payload for row in rows[1:])
    assert rows[5].payload == "Done."


def test_a_table_cell_holding_markup_reads_as_words():
    rows = parse_response("| **Bold** | `code` |\n|---|---|\n| [link](http://x) | 1 |", 1)
    assert [row.label for row in rows[1:]] == ["Row: Bold, code", "Row: link, 1"]


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL: {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
