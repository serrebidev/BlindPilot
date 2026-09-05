"""Keystone parser: turn an assistant Markdown response into navigable rows.

Each assistant turn arrives as Markdown text. We run it through
``markdown-it-py`` to get a token stream, then flatten that into an ordered
list of :class:`Row` objects — one header row per response, one prose row per
paragraph / heading / list / quote block, and one *code* row per fenced code
block.

Built for a screen-reader user, so prose is delivered as clean **plain text**:
emojis and decorative symbols are stripped, and Markdown syntax (``**bold**``,
``## headings``, ``` `code` ```, ``[link](url)``) is reduced to the words a
person actually wants to hear. Code rows keep pristine code so it can be copied
exactly.

This module is deliberately GUI-agnostic and has no wx dependency, so it can be
unit-tested in isolation (see ``tests/test_markdown_rows.py``).

Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from markdown_it import MarkdownIt

# A response is segmented with CommonMark rules plus tables. Tables are routine
# in agent output; without the rule a table reads as one prose row of pipes.
# No other extension is enabled, so the fence handling stays predictable.
_MD = MarkdownIt("commonmark").enable("table")

# Anything between angle brackets in an html_block token. Only the words of a
# <details> or <div> block are wanted, never the tags.
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Container blocks we recurse INTO only when they hold a fenced code block, so
# that nested code still becomes its own pristine code row. Without a nested
# fence, the whole container is emitted as a single prose row.
_CONTAINER_OPENS = {
    "bullet_list_open",
    "ordered_list_open",
    "blockquote_open",
    "list_item_open",
}

_CODE_TYPES = {"fence", "code_block"}

# Codepoint ranges treated as "noise" for a screen reader: emoji, pictographs,
# dingbats, misc symbols, and the joiners / variation selectors that glue them
# together. Deliberately conservative — it avoids the Letterlike block (so ™ ®
# survive) and the Technical block (so ⌘ survives).
_NOISE_RANGES = (
    (0x200D, 0x200D),  # zero-width joiner
    (0x20D0, 0x20FF),  # combining marks incl. enclosing keycap
    (0x2600, 0x27BF),  # miscellaneous symbols + dingbats (⚠ ✅ ✨ ❌ ➡)
    (0x2B00, 0x2BFF),  # misc symbols & arrows (⭐ ⬆)
    (0xFE00, 0xFE0F),  # variation selectors (emoji presentation)
    (0x1F000, 0x1FAFF),  # emoji / pictograph blocks
)

# Friendly display names for common languages coding agents emit. Anything not
# listed falls back to a capitalised form of the info string.
_LANG_NAMES = {
    "py": "Python",
    "python": "Python",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "sh": "Shell",
    "bash": "Bash",
    "zsh": "Zsh",
    "json": "JSON",
    "yaml": "YAML",
    "yml": "YAML",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
    "c": "C",
    "cpp": "C++",
    "cs": "C#",
    "go": "Go",
    "rs": "Rust",
    "rust": "Rust",
    "java": "Java",
    "rb": "Ruby",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "kt": "Kotlin",
    "md": "Markdown",
    "markdown": "Markdown",
    "xml": "XML",
    "toml": "TOML",
    "diff": "Diff",
    "text": "Plain text",
    "txt": "Plain text",
}


@dataclass
class Row:
    """One navigable row in the conversation list.

    ``label`` is what the screen reader announces on focus. ``payload`` is the
    exact text copied by the quick-copy action — pristine code for code rows,
    clean plain text for prose rows, the whole response for the header row.
    """

    # "header" | "prose" | "heading" | "list" | "quote" | "code", plus the
    # live-conversation kinds the GUI adds: "you" (the user's own message),
    # "thinking" (the backend's reasoning), "tool" (a tool it is running), "result"
    # (that tool's output).
    kind: str
    label: str
    payload: str
    response_number: int
    language: Optional[str] = None  # display name, code rows only
    lang_token: Optional[str] = None  # raw fence info word, code rows only


def _is_noise(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _NOISE_RANGES)


def _strip_noise(text: str) -> str:
    """Remove emoji / decorative symbols. Does not touch whitespace, so it is
    safe to run over the whole response including code."""
    if not text:
        return text
    if not any(_is_noise(ord(ch)) for ch in text):
        return text
    return "".join(ch for ch in text if not _is_noise(ord(ch)))


# Public name for the same thing: `session_history` titles past conversations
# with their first message, which has to be cleaned the way row labels are.
strip_noise = _strip_noise


def _flatten(text: str) -> str:
    """Collapse whitespace/newlines so a block reads as one logical line.

    Row labels carry the *full* text (no truncation) — the screen reader reads
    the label, so cutting it off would cut off the spoken paragraph mid-word.
    """
    return " ".join(text.split())


def _tidy_prose(text: str) -> str:
    """Collapse the double spaces left where an emoji was removed, preserving
    line breaks and leading indentation."""
    lines = []
    for line in text.split("\n"):
        lead_len = len(line) - len(line.lstrip(" "))
        lead, rest = line[:lead_len], line[lead_len:]
        lines.append(lead + re.sub(r"[ \t]{2,}", " ", rest).rstrip())
    return "\n".join(lines).strip()


def _inline_plain_text(inline_tok) -> str:
    """Plain text of an inline token: words only, no Markdown markup.

    Markdown-it represents emphasis/links/etc. as a *flat* list of
    ``*_open`` / ``*_close`` children with the visible text as ``text`` tokens
    in between, so concatenating the text (and inline code) children yields the
    readable string with the syntax markers dropped.
    """
    parts: List[str] = []
    for child in inline_tok.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "image":
            if child.content:
                parts.append(child.content)  # alt text
    return "".join(parts)


def _block_plain_text(inner: list) -> str:
    """Readable plain text for a block, joining its inline runs by line."""
    runs = [_inline_plain_text(t) for t in inner if t.type == "inline"]
    return "\n".join(run for run in runs if run)


def _lang_display(info: str) -> str:
    """First token of a fence info string mapped to a friendly name."""
    token = (info or "").strip().split()[0] if (info or "").strip() else ""
    if not token:
        return ""
    return _LANG_NAMES.get(token.lower(), token[:1].upper() + token[1:])


def _line_count(code: str) -> int:
    if not code:
        return 0
    return code.count("\n") + 1


def _match_close(tokens: list, open_idx: int) -> int:
    """Index of the ``*_close`` token matching the open token at ``open_idx``.

    Uses the token ``nesting`` field (+1 open, -1 close, 0 self-closing) so it
    works regardless of how blocks of the same type are nested.
    """
    depth = 0
    for k in range(open_idx, len(tokens)):
        depth += tokens[k].nesting
        if depth == 0:
            return k
    return len(tokens) - 1


def _contains_code(tokens: list) -> bool:
    return any(t.type in _CODE_TYPES for t in tokens)


def _prose_label(kind: str, text: str) -> str:
    flat = _flatten(text)
    # Headings read as plain text (no "Heading:" prefix). Lists and quotes keep
    # a short cue so their structure is still obvious when navigating by ear.
    if kind == "list":
        return f"List: {flat}"
    if kind == "quote":
        return f"Quote: {flat}"
    return flat


def _kind_for(open_type: str) -> str:
    if open_type == "heading_open":
        return "heading"
    if open_type in ("bullet_list_open", "ordered_list_open"):
        return "list"
    if open_type == "blockquote_open":
        return "quote"
    return "prose"


def _lang_token(info: str) -> str:
    """First word of a fence info string, exactly as written."""
    stripped = (info or "").strip()
    return stripped.split()[0] if stripped else ""


def _code_row(tok, response_number: int) -> Row:
    code = tok.content.rstrip("\n")
    lang = _lang_display(tok.info)
    n = _line_count(code)
    lines_word = "line" if n == 1 else "lines"
    if lang:
        label = f"Code, {lang}, {n} {lines_word}"
    else:
        label = f"Code, {n} {lines_word}"
    return Row(
        kind="code",
        label=label,
        payload=code,
        response_number=response_number,
        language=lang or None,
        lang_token=_lang_token(tok.info) or None,
    )


def _prose_row(text: str, response_number: int, label: Optional[str] = None) -> Row:
    return Row(
        kind="prose",
        label=_flatten(text) if label is None else label,
        payload=text,
        response_number=response_number,
    )


def _emit_table(inner: list, rows: List[Row], response_number: int) -> None:
    """One prose row per table row, cells read out in plain words.

    The header row comes first, as written. A screen reader has no way to
    show a grid, so "Row: a, b, c" is what a listener can follow.
    """
    for k, tok in enumerate(inner):
        if tok.type != "tr_open":
            continue
        row_tokens = inner[k + 1 : _match_close(inner, k)]
        cells = [_flatten(_inline_plain_text(t)) for t in row_tokens if t.type == "inline"]
        text = ", ".join(cell for cell in cells if cell)
        if text:
            rows.append(_prose_row(text, response_number, label=f"Row: {text}"))


def _emit(tokens: list, rows: List[Row], response_number: int) -> None:
    """Walk a token sequence, appending prose and code rows in document order."""
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.type in _CODE_TYPES:
            rows.append(_code_row(tok, response_number))
            i += 1
            continue
        if tok.type == "html_block":
            # Self-closing, so the nesting walk below never saw it and the
            # words inside a <details> or <div> block were lost.
            text = _tidy_prose(_HTML_TAG_RE.sub("", tok.content))
            if text:
                rows.append(_prose_row(text, response_number))
            i += 1
            continue
        if tok.nesting == 1:
            close_idx = _match_close(tokens, i)
            inner = tokens[i + 1 : close_idx]
            if tok.type == "table_open":
                _emit_table(inner, rows, response_number)
                i = close_idx + 1
                continue
            # Recurse into a container only to rescue a nested code fence;
            # otherwise the whole container is one prose row.
            if tok.type in _CONTAINER_OPENS and _contains_code(inner):
                _emit(inner, rows, response_number)
            else:
                payload = _tidy_prose(_block_plain_text(inner))
                if payload:
                    kind = _kind_for(tok.type)
                    rows.append(
                        Row(
                            kind=kind,
                            label=_prose_label(kind, payload),
                            payload=payload,
                            response_number=response_number,
                        )
                    )
            i = close_idx + 1
            continue
        i += 1


def parse_response(text: str, response_number: int) -> List[Row]:
    """Segment one assistant response into an ordered list of rows.

    The first row is always the header (``"Response N"``), whose payload is the
    full response text (emoji-stripped) so quick-copy on the header yields the
    whole response.
    """
    full = _strip_noise((text or "").strip())
    header = Row(
        kind="header",
        label=f"Response {response_number}",
        payload=full,
        response_number=response_number,
    )
    rows: List[Row] = [header]
    if not full:
        return rows
    tokens = _MD.parse(full)
    _emit(tokens, rows, response_number)
    return rows


# Cues written in front of a row's text when it is copied as part of a
# transcript, so the copied text still says who or what produced each part.
# Prose, headings, lists and quotes are the assistant's answer and get none.
_TRANSCRIPT_CUES = {
    "you": "You:",
    "thinking": "Thinking:",
    "result": "Result:",
}


def _transcript_block(row: Row) -> str:
    """One row rendered for the clipboard, cue included, code in a fence."""
    if row.kind == "code":
        return f"```{row.lang_token or ''}\n{row.payload}\n```"
    cue = _TRANSCRIPT_CUES.get(row.kind)
    if cue:
        return f"{cue} {row.payload}" if row.payload else cue
    return row.payload


def reassemble(rows: List[Row], response_number: int) -> str:
    """Everything the list holds for one response, for 'copy whole response'.

    Walks that response's rows in list order — your own message, thinking, tool
    steps, tool results and the answer itself — so the clipboard gets the whole
    block from start to finish, exactly as it reads on screen. Code rows come
    back fenced; the rest are prefixed with the same cue the row announces.

    The header row is skipped: its payload is the answer text that the prose and
    code rows already carry. If a response somehow has nothing but a header (no
    segments arrived), its payload is used so the copy is never empty.
    """
    blocks: List[str] = []
    header_payload = ""
    for row in rows:
        if row.response_number != response_number:
            continue
        if row.kind == "header":
            header_payload = header_payload or row.payload
            continue
        block = _transcript_block(row)
        if block:
            blocks.append(block)
    if not blocks:
        return header_payload
    return "\n\n".join(blocks)


def reassemble_all(rows: List[Row]) -> str:
    """Every row in the list, start to finish, for 'copy whole conversation'.

    Same rendering as :func:`reassemble`, in one run over the whole list, with
    each response header kept as a ``Response N`` line so the responses stay
    told apart.
    """
    blocks: List[str] = []
    for row in rows:
        if row.kind == "header":
            blocks.append(f"Response {row.response_number}")
            continue
        block = _transcript_block(row)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


# Sentence-ending punctuation, or the end of a paragraph, either of which is a
# place a listener expects the reading to stop. Lives here rather than beside
# its first caller so both the streaming backends and the Hermes worker can
# share one definition without importing each other.
_SENTENCE_END_RE = re.compile(r"(?s)^.*(?:[.!?:;…][\"'”’)\]]*(?=\s|$)|\n)")


def complete_sentences(text: str) -> str:
    """The part of ``text`` that reads as finished, or nothing yet."""
    match = _SENTENCE_END_RE.search(text)
    return match.group(0).rstrip() if match else ""
