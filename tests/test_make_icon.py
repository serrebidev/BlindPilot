"""The icon generator's container tables say what Apple's format says."""

from __future__ import annotations

from tools import make_icon


def test_retina_icns_entries_carry_their_documented_sizes():
    """ic11 is 16x16 at 2x, ic12 is 32x32 at 2x. Swapped, Finder scales the
    wrong image at both retina sizes."""
    assert make_icon.ICNS_SIZES["ic11"] == 32
    assert make_icon.ICNS_SIZES["ic12"] == 64
    assert make_icon.ICNS_SIZES["ic13"] == 256
    assert make_icon.ICNS_SIZES["ic14"] == 512
