"""Generate BlindPilot's application icon with no third-party dependency.

Renders a 1024x1024 rounded-rectangle gradient tile with a white prompt
chevron, downsamples it, and writes:

* ``packaging/BlindPilot.icns``  — macOS bundle icon (PNG-based chunks)
* ``packaging/BlindPilot.ico``   — Windows executable icon (PNG entries)
* ``packaging/BlindPilot-1024.png`` — source render, handy for docs/releases

ICNS and ICO are container formats that can carry PNG, so the only encoders
needed are the PNG ones below — pure :mod:`zlib` and :mod:`struct`.

Run from the repository root:

    python3 tools/make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "packaging"

ICNS_SIZES = {
    "icp4": 16,
    "icp5": 32,
    "ic06": 48,  # legacy 48px entry, some launchers still look for it
    "icp6": 64,
    "ic07": 128,
    "ic08": 256,
    "ic09": 512,
    "ic10": 1024,
    "ic11": 64,  # 32@2x
    "ic12": 32,  # 16@2x
    "ic13": 256,  # 128@2x
    "ic14": 512,  # 256@2x
}
ICO_SIZES = (16, 32, 48, 64, 128, 256)

TRUE_SIZE = 1024
AA = 2  # supersampling factor per axis


# ----- rasterisation -------------------------------------------------------


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from (px, py) to the segment [A, B]."""
    abx, aby = bx - ax, by - ay
    length_sq = abx * abx + aby * aby
    if length_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * abx), py - (ay + t * aby))


def _rounded_rect_distance(px, py, size, radius):
    """Signed distance from (px, py) to a rounded rect; negative inside.

    The standard rounded-box SDF: ``length(max(q, 0)) +
    min(max(q.x, q.y), 0) - r`` with ``q = abs(p) - half + r``.
    """
    half = size / 2.0
    qx = abs(px - half) - (half - radius)
    qy = abs(py - half) - (half - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - radius


def render(size: int) -> tuple[list[tuple[int, int, int, int]], int]:
    """Return a list of (r, g, b, a) pixels for *size* (any power of two).

    The master render runs at TRUE_SIZE with AA*AA samples per pixel; smaller
    sizes are box-filtered down from it, so every output is the same design.
    """
    master, _ = _render_at(TRUE_SIZE)
    pixels, _ = _downsample(master, TRUE_SIZE, size)
    return pixels, size


def _render_at(size: int) -> tuple[list[tuple[int, int, int, int]], int]:
    radius = size * 0.2237  # macOS-style squircle-ish corner
    top = (67, 56, 202)
    bottom = (14, 165, 233)
    # The chevron: two thick segments meeting at a right-pointing apex.
    w = 0.088 * size
    half_w = w / 2.0
    apex = (0.72 * size, 0.50 * size)
    start_top = (0.34 * size, 0.30 * size)
    start_bottom = (0.34 * size, 0.70 * size)
    aa = 1.0 / AA

    pixels: list[tuple[int, int, int, int]] = []
    for py in range(size):
        for px in range(size):
            acc_r = acc_g = acc_b = acc_a = 0.0
            for sy in range(AA):
                y = py + (sy + 0.5) * aa
                for sx in range(AA):
                    x = px + (sx + 0.5) * aa
                    # Coverage of the rounded tile. 1.5px of soft edge.
                    dist = _rounded_rect_distance(x, y, size, radius)
                    coverage = 0.5 - dist / (1.5 * (size / TRUE_SIZE))
                    coverage = max(0.0, min(1.0, coverage))
                    if coverage <= 0.0:
                        continue
                    # Gradient by vertical position.
                    t = y / size
                    r = top[0] + (bottom[0] - top[0]) * t
                    g = top[1] + (bottom[1] - top[1]) * t
                    b = top[2] + (bottom[2] - top[2]) * t
                    # Chevron: union of two stroked segments, white.
                    d1 = _segment_distance(x, y, *start_top, *apex)
                    d2 = _segment_distance(x, y, *start_bottom, *apex)
                    glyph = min(d1, d2) - half_w
                    glyph_cover = 0.5 - glyph / (1.5 * (size / TRUE_SIZE))
                    glyph_cover = max(0.0, min(1.0, glyph_cover))
                    if glyph_cover > 0.0:
                        r += (255.0 - r) * glyph_cover
                        g += (255.0 - g) * glyph_cover
                        b += (255.0 - b) * glyph_cover
                    acc_r += r * coverage
                    acc_g += g * coverage
                    acc_b += b * coverage
                    acc_a += coverage * 255.0
            total = AA * AA
            pixels.append(
                (
                    int(round(acc_r / total)),
                    int(round(acc_g / total)),
                    int(round(acc_b / total)),
                    int(round(acc_a / total)),
                )
            )
    return pixels, size


def _downsample(
    source: list[tuple[int, int, int, int]], source_size: int, target_size: int
) -> tuple[list[tuple[int, int, int, int]], int]:
    """Box-average *source* (source_size square) into *target_size* square.

    Averages premultiplied colour so a translucent edge against the gradient
    keeps its hue instead of fringing black.
    """
    if target_size == source_size:
        return list(source), target_size
    stride = source_size // target_size
    block = stride * stride
    pixels: list[tuple[int, int, int, int]] = []
    for ty in range(target_size):
        for tx in range(target_size):
            sr = sg = sb = sa = 0.0
            for dy in range(stride):
                row = (ty * stride + dy) * source_size
                for dx in range(stride):
                    r, g, b, a = source[row + tx * stride + dx]
                    sr += r * a
                    sg += g * a
                    sb += b * a
                    sa += a
            if sa <= 0.0:
                pixels.append((0, 0, 0, 0))
            else:
                pixels.append(
                    (
                        int(round(sr / sa)),
                        int(round(sg / sa)),
                        int(round(sb / sa)),
                        int(round(sa / block)),
                    )
                )
    return pixels, target_size


# ----- PNG encoder ---------------------------------------------------------


def png_bytes(pixels: list[tuple[int, int, int, int]], size: int) -> bytes:
    """Encode an RGBA *pixels* scanline list as PNG bytes."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type: none
        row = pixels[y * size : (y + 1) * size]
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


# ----- containers ----------------------------------------------------------


def icns_bytes(icons: dict[str, bytes]) -> bytes:
    """Pack PNG chunks into an .icns container."""
    chunks = b""
    for kind, data in icons.items():
        chunks += struct.pack(">4sI", kind.encode("ascii"), 8 + len(data)) + data
    return b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks


def ico_bytes(icons: dict[int, bytes]) -> bytes:
    """Pack PNG entries into an .ico container (Vista+ reads PNG entries)."""
    entries = []
    offset = 6 + 16 * len(icons)
    for size, data in icons.items():
        entries.append(
            struct.pack(
                "<BBBBHHII",
                size if size < 256 else 0,
                size if size < 256 else 0,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        offset += len(data)
    return (
        struct.pack("<HHH", 0, 1, len(icons))
        + b"".join(entries)
        + b"".join(icons[size] for size in icons)
    )


# ----- main ----------------------------------------------------------------


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    master, size = _render_at(TRUE_SIZE)
    (OUT_DIR / "BlindPilot-1024.png").write_bytes(png_bytes(master, size))

    icns_icons: dict[str, bytes] = {}
    for kind, target in ICNS_SIZES.items():
        pixels, _ = _downsample(master, TRUE_SIZE, target)
        icns_icons[kind] = png_bytes(pixels, target)
    (OUT_DIR / "BlindPilot.icns").write_bytes(icns_bytes(icns_icons))

    ico_icons: dict[int, bytes] = {}
    for target in ICO_SIZES:
        pixels, _ = _downsample(master, TRUE_SIZE, target)
        ico_icons[target] = png_bytes(pixels, target)
    (OUT_DIR / "BlindPilot.ico").write_bytes(ico_bytes(ico_icons))

    for path in ("BlindPilot.icns", "BlindPilot.ico", "BlindPilot-1024.png"):
        print(f"wrote {OUT_DIR / path} ({(OUT_DIR / path).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
