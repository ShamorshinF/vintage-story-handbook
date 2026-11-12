#!/usr/bin/env python3
"""Draws the home-screen icons (an open book) as PNG files, with no extra modules.

Usage:  python -I tools/make_icons.py [output folder]
The output folder defaults to docs/icons. Run once; the result is part of the site.
The drawing stays inside the middle 80 % so Android can crop it to any icon shape.
"""
import os, struct, sys, zlib

BG = (42, 33, 26)
COVER = (134, 84, 15)
PAGE = (240, 230, 210)
LINE = (169, 150, 120)
MARK = (231, 184, 102)
SPINE = (92, 74, 57)


def png(path, size, pixels):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw += pixels[y]
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        fh.write(chunk(b"IEND", b""))


def draw(size):
    rows = [bytearray(BG * size) for _ in range(size)]

    def rect(x0, y0, x1, y1, colour):
        xa, xb = round(size * x0 / 100), round(size * x1 / 100)
        ya, yb = round(size * y0 / 100), round(size * y1 / 100)
        strip = bytes(colour) * (xb - xa)
        for y in range(ya, yb):
            rows[y][xa * 3:xb * 3] = strip

    rect(18, 29, 82, 75, COVER)            # cover
    rect(21, 26, 49, 71, PAGE)             # left page
    rect(51, 26, 79, 71, PAGE)             # right page
    rect(49, 26, 51, 73, SPINE)            # spine
    for y in (33, 40, 47, 54, 61):
        rect(25, y, 45, y + 2.6, LINE)
        rect(55, y, 67 if y < 40 else 75, y + 2.6, LINE)
    rect(69, 26, 74, 39, MARK)             # bookmark
    return rows


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "docs", "icons")
    os.makedirs(out, exist_ok=True)
    for size in (192, 512):
        path = os.path.join(out, f"icon-{size}.png")
        png(path, size, draw(size))
        print(f"{os.path.basename(path)}  {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    main()
