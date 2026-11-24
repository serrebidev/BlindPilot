"""Attachments in any encoding, and binary files that stay binary.

A text file is not always UTF-8: older Windows files are cp1252 and some
editors save UTF-16 with a byte order mark. Decoding either as UTF-8 with
replacement characters garbled them before the model ever saw them. A
file that is not text at all was garbled the same way on the Messages
path; it now travels as base64 when it is small, and is only named when
it is too large to include.
"""

from __future__ import annotations

import base64

from accessible_ai.models import MessageAttachment
from accessible_ai.providers.protocols import (
    attachment_content_part,
    attachment_source_block,
    attachment_text,
)


def _attachment(name, data, mime="application/octet-stream"):
    return MessageAttachment(filename=name, mime_type=mime, data=data)


def test_cp1252_text_decodes_without_mojibake():
    """Curly quotes and an accented letter, saved as cp1252: the Chat
    Completions part and the helper both return the real characters."""
    data = "“café” — £5".encode("cp1252")
    attachment = _attachment("note.txt", data, "text/plain")
    assert attachment_text(attachment) == "“café” — £5"
    part = attachment_content_part(attachment)
    assert part == {"type": "text", "text": "[Attached file: note.txt]\n“café” — £5"}
    block = attachment_source_block(attachment)
    assert block == {"type": "text", "text": "[Attached file: note.txt]\n“café” — £5"}


def test_utf16_bom_text_decodes():
    """UTF-16 with its byte order mark says what it is: the mark is
    honoured and does not survive into the text the model reads."""
    data = "Hello, wörld".encode("utf-16")
    attachment = _attachment("note.txt", data, "text/plain")
    assert attachment_text(attachment) == "Hello, wörld"
    assert attachment_source_block(attachment)["text"].endswith("Hello, wörld")


def test_utf8_bom_and_utf32_bom_text_decode():
    """The other byte order marks: UTF-8's is stripped by utf-8-sig, and
    UTF-32's is not mistaken for UTF-16's, which shares its first bytes."""
    assert attachment_text(_attachment("a.txt", "café".encode("utf-8-sig"))) == "café"
    assert attachment_text(_attachment("a.txt", "café".encode("utf-32"))) == "café"


def test_binary_bytes_are_not_text():
    """NUL bytes and a flood of control characters are not an encoding
    problem to paper over: the helper refuses them as text."""
    assert attachment_text(_attachment("a.bin", b"\x00\x01\x02\xff\xfe")) is None
    assert attachment_text(_attachment("a.bin", bytes(range(1, 32)) * 4)) is None


def test_small_binary_on_the_messages_path_travels_as_base64():
    """A small binary file on the Messages path: a text block naming the
    file, its type and size, followed by base64 the model can decode --
    never the bytes decoded as text."""
    data = bytes(range(256))
    block = attachment_source_block(_attachment("data.bin", data))
    assert block["type"] == "text"
    header, _, payload = block["text"].partition("\n")
    assert header == "[Attached file: data.bin, application/octet-stream, 256 bytes, base64]"
    assert base64.b64decode(payload) == data


def test_large_binary_on_the_messages_path_is_named_not_sent():
    """A binary file over 100 KB on the Messages path: the block names
    the file and says it is too large to include, and carries none of
    the bytes, as text or as base64."""
    data = b"\x00\xff" * (60 * 1024)
    block = attachment_source_block(_attachment("big.bin", data))
    assert block["type"] == "text"
    assert "big.bin" in block["text"]
    assert "too large to include" in block["text"]
    assert base64.b64encode(data).decode("ascii") not in block["text"]


def test_images_and_pdfs_on_the_messages_path_are_unchanged():
    """The binary rule does not touch what Messages already carries as
    itself: images and PDFs still go as their own base64 blocks."""
    image = _attachment("pic.png", b"\x89PNG\x00", "image/png")
    assert attachment_source_block(image)["type"] == "image"
    pdf = _attachment("doc.pdf", b"%PDF-\x00", "application/pdf")
    assert attachment_source_block(pdf)["type"] == "document"
