#!/usr/bin/env python3
"""Converts the four DyslexicFont-*.otf faces to WOFF for the site's font switch.

DyslexicFont is the renamed, metric-corrected build of OpenDyslexic 0.92 that the
Vintage Story "Dyslexic Font" mod ships (SIL Open Font License 1.1; the name
"OpenDyslexic" is reserved for the unmodified font, so the modified faces keep the
name DyslexicFont). The license text stored in the font is written next to the
WOFF files, as the license requires.

Usage:  python -I tools/make_webfonts.py <folder with DyslexicFont-*.otf> [output folder]
Needs:  pip install fonttools
The output folder defaults to docs/fonts. Run once; the result is part of the site.
"""
import os, sys
from fontTools.ttLib import TTFont

FACES = ["Regular", "Bold", "Italic", "BoldItalic"]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    src_dir = sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "docs", "fonts")
    os.makedirs(out_dir, exist_ok=True)
    license_text = None
    for face in FACES:
        src = os.path.join(src_dir, f"DyslexicFont-{face}.otf")
        f = TTFont(src)
        if license_text is None:
            rec = f["name"].getName(13, 3, 1, 0x409)
            note = f["name"].getName(10, 3, 1, 0x409)
            license_text = (rec.toUnicode() if rec else "") + "\n\n---\n" + (note.toUnicode() if note else "") + "\n"
        f.flavor = "woff"          # zlib; needs no extra module
        dst = os.path.join(out_dir, f"DyslexicFont-{face}.woff")
        f.save(dst)
        print(f"{os.path.basename(dst)}  {os.path.getsize(dst) // 1024} KB")
    with open(os.path.join(out_dir, "OFL.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(license_text)
    print("OFL.txt written")


if __name__ == "__main__":
    main()
