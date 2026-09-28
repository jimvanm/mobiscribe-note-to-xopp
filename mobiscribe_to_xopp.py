#!/usr/bin/env python3
"""
mobiscribe_to_xopp.py

Convert Mobiscribe .note notebooks into Xournal++ .xopp notebooks.

Strokes stay real vector paths, typed text stays real text -- nothing is
rasterized. Reference for the .note layout: NOTE_FORMAT.md (reverse-engineered
from real device files). Reference for the .xopp layout: xournalpp's own
SaveHandler.cpp / XmlTags.h / XmlAttrs.h (read directly from source, not
guessed).

Usage:
    python3 mobiscribe_to_xopp.py NOTEBOOK.note [-o OUTPUT.xopp]
"""

import argparse
import gzip
import io
import struct
import sys
import tarfile
import xml.sax.saxutils as sx

# ---------------------------------------------------------------------------
# Low-level readers for the .note binary format (big-endian DataOutputStream
# conventions -- see NOTE_FORMAT.md section 2).
# ---------------------------------------------------------------------------

class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def bytes(self, n: int) -> bytes:
        b = self.data[self.pos:self.pos + n]
        if len(b) != n:
            raise ValueError(f"truncated read: wanted {n} bytes, got {len(b)} at offset {self.pos}")
        self.pos += n
        return b

    def u8(self) -> int:
        return self.bytes(1)[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.bytes(4))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.bytes(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.bytes(8))[0]

    def f32(self) -> float:
        return struct.unpack(">f", self.bytes(4))[0]

    def utf(self) -> str:
        (length,) = struct.unpack(">H", self.bytes(2))
        raw = self.bytes(length)
        # Java's writeUTF is "modified" UTF-8: differs from standard UTF-8
        # only for NUL and characters outside the BMP. Good enough for
        # ordinary note text; degrades gracefully (replacement char) if it
        # ever hits one of those edge cases.
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# .note parsing -- follows NOTE_FORMAT.md sections 3 and 4 exactly.
# ---------------------------------------------------------------------------

def parse_index(data: bytes) -> dict:
    r = Reader(data)
    version = r.i32()
    n = r.i32()
    page_uuids = [r.utf() for _ in range(n)]
    current_page = r.i32()
    title = r.utf()
    created = r.i64()
    modified = r.i64()
    notebook_uuid = r.utf()
    return {
        "version": version,
        "page_uuids": page_uuids,
        "current_page": current_page,
        "title": title,
        "created": created,
        "modified": modified,
        "notebook_uuid": notebook_uuid,
    }


def parse_page(data: bytes, page_uuid: str) -> dict:
    r = Reader(data)

    page_version = r.i32()
    if page_version != 15:
        print(f"  warning: page {page_uuid} has format version {page_version}, "
              f"expected 15 -- proceeding, but layout may not match", file=sys.stderr)

    uuid = r.utf()
    r.i32()          # unknown, always 1
    r.bytes(21)       # unknown, always zero
    aspect_ratio = r.f32()
    r.i32()           # unknown, always 0
    r.utf()           # "na" -- unknown, guessed paper/template id
    r.bytes(10)       # unknown, always zero
    page_ts = r.utf()  # decimal-text timestamp

    # --- text boxes ---
    textboxes = []
    tb_count = r.i32()
    for _ in range(tb_count):
        kind = r.i32()
        k = r.i32()  # number of floats, always 4
        floats = [r.f32() for _ in range(k)]
        left, right, top, bottom = floats[:4]
        text = r.utf()
        size = r.i32()
        colour = r.u32()
        bold = r.u8()
        italic = r.u8()
        underline = r.u8()
        r.u8()  # unknown, always 0
        textboxes.append({
            "left": left, "right": right, "top": top, "bottom": bottom,
            "text": text, "size": size, "colour": colour,
            "bold": bold, "italic": italic, "underline": underline,
        })

    # --- middle block (fixed, per NOTE_FORMAT.md 4.3) ---
    r.i32()
    r.u8()
    r.utf()          # "en_US"
    r.i32()
    r.bytes(2)
    r.bytes(27)

    # --- strokes ---
    strokes = []
    st_count = r.i32()
    for _ in range(st_count):
        kind = r.i32()
        colour = r.u32()
        pen_width = r.i32()
        flag = r.i32()
        p = r.i32()
        points = []
        for _ in range(p):
            x = r.f32()
            y = r.f32()
            pressure = r.f32()
            points.append((x, y, pressure))
        strokes.append({
            "colour": colour, "pen_width": pen_width, "flag": flag,
            "points": points,
        })

    trailer = r.bytes(4)
    if trailer != b"\x00\x00\x00\x00":
        print(f"  warning: page {page_uuid} trailer is {trailer!r}, expected zero -- "
              f"file may hold content this converter doesn't know about", file=sys.stderr)

    if r.remaining() != 0:
        print(f"  warning: page {page_uuid} has {r.remaining()} unparsed trailing bytes", file=sys.stderr)

    return {
        "uuid": uuid,
        "aspect_ratio": aspect_ratio,
        "timestamp": page_ts,
        "textboxes": textboxes,
        "strokes": strokes,
    }


# ---------------------------------------------------------------------------
# .xopp writing -- schema taken from xournalpp's own SaveHandler.cpp
# (visitPage / visitLayer / visitStroke), XmlTags.h and XmlAttrs.h.
# ---------------------------------------------------------------------------

# Target page height in points (1/72 in). Mobiscribe stores every coordinate,
# stroke width and text size as a fraction of the *device* page height
# (1440 device pixels -- see NOTE_FORMAT.md section 6), so one scale factor
# carries everything across cleanly. 792pt keeps the notebook's own recorded
# aspect ratio rather than forcing it onto a Letter/A4 rectangle.
PAGE_HEIGHT_PT = 792.0
DEVICE_HEIGHT_PX = 1440.0
SCALE = PAGE_HEIGHT_PT / DEVICE_HEIGHT_PX  # points per device pixel


def argb_to_xopp_color(argb: int) -> str:
    """Mobiscribe stores 0xAARRGGBB. Xournal++ wants '#RRGGBBAA'."""
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


def esc(s: str) -> str:
    return sx.escape(s, {'"': "&quot;"})


def stroke_to_xml(stroke: dict, page_height_units: float) -> str:
    color = argb_to_xopp_color(stroke["colour"])
    width_pt = max(stroke["pen_width"] * SCALE, 0.1)
    pts = stroke["points"]
    coord_pairs = " ".join(
        f"{x * page_height_units:.2f} {y * page_height_units:.2f}" for x, y, _ in pts
    )
    return (f'<stroke tool="pen" color="{color}" width="{width_pt:.2f}">'
            f'{coord_pairs}</stroke>')


def textbox_to_xml(tb: dict, page_height_units: float) -> str:
    color = argb_to_xopp_color(tb["colour"])
    x_pt = tb["left"] * page_height_units
    y_pt = tb["top"] * page_height_units
    # NOTE_FORMAT.md's own rendering recipe: ~3.6 device px per size unit
    # at 1440px device height.
    size_pt = tb["size"] * 3.6 * SCALE
    text = tb["text"]
    return (f'<text font="Sans" size="{size_pt:.2f}" x="{x_pt:.2f}" y="{y_pt:.2f}" '
            f'color="{color}">{esc(text)}</text>')


def page_to_xml(page: dict) -> str:
    # page height in xournal "units" (points): aspect_ratio*H is the width,
    # H is the height, coordinates are stored as fractions of H.
    height_pt = PAGE_HEIGHT_PT
    width_pt = page["aspect_ratio"] * PAGE_HEIGHT_PT

    parts = [f'<page width="{width_pt:.2f}" height="{height_pt:.2f}">']
    parts.append('<background type="solid" color="#ffffffff" style="plain"/>')
    parts.append('<layer>')
    for tb in page["textboxes"]:
        parts.append(textbox_to_xml(tb, height_pt))
    for st in page["strokes"]:
        parts.append(stroke_to_xml(st, height_pt))
    parts.append('</layer>')
    parts.append('</page>')
    return "\n".join(parts)


def build_xopp(title: str, pages: list) -> bytes:
    xml_parts = [
        '<?xml version="1.0" standalone="no"?>',
        '<xournal creator="mobiscribe_to_xopp.py" fileversion="4">',
        f'<title>Xournal++ document - converted from Mobiscribe notebook "{esc(title)}"</title>',
    ]
    for page in pages:
        xml_parts.append(page_to_xml(page))
    xml_parts.append('</xournal>')
    xml_text = "\n".join(xml_parts) + "\n"
    return gzip.compress(xml_text.encode("utf-8"))


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def convert(note_path: str, out_path: str):
    with tarfile.open(note_path, "r") as tf:
        members = {m.name: m for m in tf.getmembers()}

        index_member = next((m for m in members.values() if m.name.endswith("/index")), None)
        if index_member is None:
            raise ValueError("no index file found inside archive -- is this really a .note file?")

        index_data = tf.extractfile(index_member).read()
        index = parse_index(index_data)

        print(f"Notebook: {index['title']!r}  ({len(index['page_uuids'])} pages)")

        pages = []
        for uuid in index["page_uuids"]:
            page_member = next(
                (m for m in members.values() if m.name.endswith(f"/page_{uuid}.page")), None
            )
            if page_member is None:
                print(f"  warning: page {uuid} listed in index but its .page file is missing -- skipping", file=sys.stderr)
                continue
            page_data = tf.extractfile(page_member).read()
            page = parse_page(page_data, uuid)
            pages.append(page)
            print(f"  page {uuid}: {len(page['textboxes'])} text box(es), "
                  f"{len(page['strokes'])} stroke(s)")

        xopp_bytes = build_xopp(index["title"], pages)

    with open(out_path, "wb") as f:
        f.write(xopp_bytes)
    print(f"Wrote {out_path}")


def main():
    ap = argparse.ArgumentParser(description="Convert a Mobiscribe .note file to Xournal++ .xopp")
    ap.add_argument("note_file")
    ap.add_argument("-o", "--output", help="output .xopp path (default: same name, .xopp extension)")
    args = ap.parse_args()

    out_path = args.output or (args.note_file.rsplit(".", 1)[0] + ".xopp")
    convert(args.note_file, out_path)


if __name__ == "__main__":
    main()
