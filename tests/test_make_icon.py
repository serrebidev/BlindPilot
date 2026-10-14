"""The icon generator's container tables say what Apple's format says."""

from __future__ import annotations

from pathlib import Path

from tools import make_icon


def test_retina_icns_entries_carry_their_documented_sizes():
    """ic11 is 16x16 at 2x, ic12 is 32x32 at 2x. Swapped, Finder scales the
    wrong image at both retina sizes."""
    assert make_icon.ICNS_SIZES["ic11"] == 32
    assert make_icon.ICNS_SIZES["ic12"] == 64
    assert make_icon.ICNS_SIZES["ic13"] == 256
    assert make_icon.ICNS_SIZES["ic14"] == 512


def test_packaged_icns_entries_match_the_generator_sizes():
    icon = (Path(__file__).resolve().parent.parent / "packaging" / "BlindPilot.icns").read_bytes()
    assert icon[:4] == b"icns"
    assert int.from_bytes(icon[4:8], "big") == len(icon)
    offset = 8
    while offset < len(icon):
        kind = icon[offset : offset + 4].decode("ascii")
        length = int.from_bytes(icon[offset + 4 : offset + 8], "big")
        assert length >= 8
        png = icon[offset + 8 : offset + length]
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        size = make_icon.ICNS_SIZES[kind]
        assert int.from_bytes(png[16:20], "big") == size
        assert int.from_bytes(png[20:24], "big") == size
        offset += length
    assert offset == len(icon)
