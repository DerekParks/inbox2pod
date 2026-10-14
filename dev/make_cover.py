#!/usr/bin/env python3
"""Generate the placeholder podcast cover (inbox2pod/cover.png).

Pure stdlib PNG writer: a diagonal gradient with an envelope-and-soundwave
motif. Rerun with --size to change dimensions. Replace the output with your own
art any time; the feed just points at whatever cover.png sits in the package.
"""

import argparse
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "inbox2pod" / "cover.png"

TOP = (0x14, 0x27, 0x3C)  # deep ink
BOTTOM = (0x2C, 0x5F, 0x8A)  # pod blue
ACCENT = (0xF2, 0xC1, 0x4B)  # warm paper
WHITE = (0xF7, 0xF9, 0xFB)


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def envelope(x: int, y: int, cx: int, cy: int, w: int, h: int) -> bool:
    """True when (x, y) lands on the envelope body."""
    return abs(x - cx) <= w / 2 and abs(y - cy) <= h / 2


def envelope_flap(x: int, y: int, cx: int, cy: int, w: int, h: int) -> bool:
    """True when (x, y) lands on the envelope's V flap."""
    top = cy - h / 2
    if y <= top or y > top + h * 0.45:
        return False
    dx = abs(x - cx)
    return dx <= w / 2 and abs((y - top) - (w / 2 - dx) * 0.62) < w * 0.035


def soundwave(x: int, y: int, cx: int, cy: int, w: int, bars: int) -> bool:
    """Vertical bars of varying height, like a waveform under the envelope."""
    slot = w / bars
    index = int((x - (cx - w / 2)) // slot)
    if not 0 <= index < bars:
        return False
    centre = cx - w / 2 + slot * (index + 0.5)
    if abs(x - centre) > slot * 0.28:
        return False
    heights = [0.18, 0.42, 0.28, 0.62, 0.36, 0.2, 0.5, 0.3, 0.55, 0.25, 0.44, 0.16]
    return abs(y - cy) <= heights[index % len(heights)] * w * 0.5


def render(size: int):
    cx = cy = size / 2
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")  # filter type 0
        for x in range(size):
            t = (x / size + y / size) / 2
            colour = lerp(TOP, BOTTOM, t)

            if envelope(x, y, cx, cy * 0.82, size * 0.5, size * 0.32):
                colour = WHITE
            if envelope_flap(x, y, cx, cy * 0.82, size * 0.5, size * 0.32):
                colour = lerp(WHITE, ACCENT, 0.85)
            if soundwave(x, y, cx, size * 0.74, size * 0.42, 12):
                colour = ACCENT
            row += bytes(colour)
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path: Path, size: int) -> None:
    raw = render(size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    print(f"wrote {path} ({size}x{size}, {len(png)} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1400, help="square pixel size (podcast art: 1400-3000)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    write_png(args.out, args.size)


if __name__ == "__main__":
    main()
