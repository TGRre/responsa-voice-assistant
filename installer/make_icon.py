# -*- coding: utf-8 -*-
"""
make_icon.py — יוצר קובץ app_icon.ico רב-רזולוציה (16, 32, 48, 256 px)
ללא תלויות חיצוניות (Pure Python PNG-in-ICO).
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


def _encode_png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0
        raw.extend(rgba[y * stride : (y + 1) * stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + chunk(b"IEND", b"")
    )


def _render_icon(size: int) -> bytes:
    """מצייר אייקון כהה-אלגנטי עם מיקרופון בצבע תכלת/זהב."""
    buf = bytearray(size * size * 4)
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    radius = size * 0.46
    corner_r = size * 0.22

    for y in range(size):
        for x in range(size):
            # קואורדינטות מנורמלות [-1, 1]
            nx = (x - cx) / (size * 0.5)
            ny = (y - cy) / (size * 0.5)

            # ריבוע עם פינות עגולות
            ax = abs(x - cx) - (radius - corner_r)
            ay = abs(y - cy) - (radius - corner_r)
            dist = math.hypot(max(ax, 0.0), max(ay, 0.0)) + min(max(ax, ay), 0.0) - corner_r

            if dist > 1.0:
                continue

            alpha = int(max(0.0, min(1.0, 1.0 - dist)) * 255)

            # רקע כחול-לילה עם גרדיאנט עדין (Catppuccin Mocha)
            t = (ny + 1.0) * 0.5
            r = int(26 + 12 * (1.0 - t))
            g = int(27 + 14 * (1.0 - t))
            b = int(46 + 22 * (1.0 - t))

            # מסגרת עדינה
            if -size * 0.045 <= dist <= 0.0:
                r, g, b = 137, 180, 250  # #89b4fa

            # גוף המיקרופון (קפסולה במרכז העליון)
            mic_top, mic_bot = -0.52, 0.08
            mic_hw = 0.20
            in_cap = (
                abs(nx) <= mic_hw
                and mic_top <= ny <= mic_bot
                and (
                    (mic_top + mic_hw <= ny <= mic_bot - mic_hw)
                    or math.hypot(nx, ny - (mic_top + mic_hw)) <= mic_hw
                    or math.hypot(nx, ny - (mic_bot - mic_hw)) <= mic_hw
                )
            )

            # קשת מסביב למיקרופון
            arc_d = math.hypot(nx, ny - (-0.02))
            in_arc = (ny >= -0.04) and (0.31 <= arc_d <= 0.41)

            # רגל המיקרופון + בסיס
            in_stem = abs(nx) <= 0.055 and 0.36 <= ny <= 0.60
            in_base = abs(nx) <= 0.28 and 0.55 <= ny <= 0.65

            if in_cap:
                r, g, b = 249, 226, 175  # זהב בהיר #f9e2af
            elif in_arc or in_stem or in_base:
                r, g, b = 137, 180, 250  # תכלת #89b4fa

            idx = (y * size + x) * 4
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b
            buf[idx + 3] = alpha

    return _encode_png(size, size, bytes(buf))


def build_ico(output: Path) -> None:
    sizes = [16, 32, 48, 256]
    images = [(s, _render_icon(s)) for s in sizes]

    header = struct.pack("<HHH", 0, 1, len(images))
    entries = bytearray()
    offset = 6 + 16 * len(images)

    for s, png in images:
        dim = 0 if s == 256 else s
        entries.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset))
        offset += len(png)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + bytes(entries) + b"".join(png for _, png in images))
    print(f"[ICON] Created {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    out_path = Path(__file__).resolve().parent / "app_icon.ico"
    build_ico(out_path)
