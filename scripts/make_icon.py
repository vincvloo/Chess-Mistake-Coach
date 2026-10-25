"""Draw the app icon (a white pawn on a blue rounded square) and write
chess_mistake_coach/web/static/icon.png and icon.ico, which ship with the
package: the desktop shortcut and the browser tab use them. No image
library needed: shapes are filled with 4x4 supersampling and written as PNG
with zlib.

    python scripts/make_icon.py
"""

from __future__ import annotations

import os
import struct
import zlib

BLUE = (63, 111, 214)
WHITE = (255, 255, 255)
SS = 4  # supersampling per axis


def _inside(x: float, y: float) -> tuple[bool, bool]:
    """(in the rounded background, in the pawn) for a point in 0..1 space."""
    r = 0.2
    cx = min(max(x, r), 1 - r)
    cy = min(max(y, r), 1 - r)
    bg = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    head = (x - 0.5) ** 2 + (y - 0.31) ** 2 <= 0.105 ** 2
    collar = 0.40 <= y <= 0.45 and abs(x - 0.5) <= 0.115
    # body: a trapezoid narrowing upward, 0.44 .. 0.70
    t = (y - 0.44) / (0.70 - 0.44)
    body = 0.0 <= t <= 1.0 and abs(x - 0.5) <= 0.075 + t * 0.10
    base = 0.69 <= y <= 0.78 and abs(x - 0.5) <= 0.215
    return bg, head or collar or body or base


def render(size: int) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            bg = pawn = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) / size
                    y = (py + (sy + 0.5) / SS) / size
                    b, p = _inside(x, y)
                    bg += b
                    pawn += b and p
            n = SS * SS
            a = bg / n
            if bg:
                w = pawn / bg
                col = [round(BLUE[i] * (1 - w) + WHITE[i] * w) for i in range(3)]
            else:
                col = [0, 0, 0]
            row += bytes(col + [round(a * 255)])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico(pngs: dict[int, bytes]) -> bytes:
    """An .ico whose entries are PNGs (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in sorted(pngs.items()):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> None:
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "chess_mistake_coach", "web", "static")
    os.makedirs(out, exist_ok=True)
    pngs = {s: render(s) for s in (16, 32, 48, 256)}
    with open(os.path.join(out, "icon.png"), "wb") as f:
        f.write(pngs[256])
    with open(os.path.join(out, "icon.ico"), "wb") as f:
        f.write(ico(pngs))
    print("wrote", os.path.join(out, "icon.png"), "and icon.ico")


if __name__ == "__main__":
    main()
