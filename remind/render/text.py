"""Render plain text to a 1-bit raster.

Pure rendering — no BLE, no protocol. This is the single "layout template"
for now: left-aligned, word-wrapped to the printable width. As layouts grow
(headings, checkboxes, dividers) add sibling modules alongside this one.

Every knob here is a PrintConfig field, so what the user configures per device
is exactly what comes out of the printer.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from .fonts import load_font
from .raster import WIDTH_BYTES, WIDTH_PX, Raster, content_geometry

__all__ = ["render_text", "wrap_lines", "load_font"]

# Chinese/Japanese/Korean text has no spaces, so it wraps between characters —
# but these must never be left stranded at the start of a line (禁则处理).
_NO_LINE_START = "、。，．：；！？）〕］｝〉》」』】〞’”…‥・ーヽヾゝゞ々"


def render_text(
    text: str,
    *,
    font_size: int = 48,
    margin_x: int = 12,
    margin_top: int = 12,
    margin_bottom: int = 12,
    line_spacing: int = 10,
    paper_width_mm: float = 48,
) -> Raster:
    """Render text (may contain newlines) to a Raster. Content in, raster out.

    Rows are always the full 48-byte head width — that is hardware. A narrower
    paper belt just narrows the *content* column inside it.
    """
    font = load_font(font_size)

    _paper_px, margin_x, text_width = content_geometry(paper_width_mm, margin_x)
    lines = wrap_lines(text, font, text_width)

    # Measure total height.
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + line_spacing
    height = max(1, margin_top + line_height * len(lines) + margin_bottom)

    img = Image.new("1", (WIDTH_PX, height), color=1)  # 1 = white in mode "1"
    draw = ImageDraw.Draw(img)
    y = margin_top
    for line in lines:
        draw.text((margin_x, y), line, font=font, fill=0)  # 0 = black
        y += line_height

    # Mode "1": bit 1 = white, bit 0 = black. Printer wants bit 1 = black → invert.
    raw = img.tobytes()
    inverted = bytes(b ^ 0xFF for b in raw)
    return Raster(data=inverted, width_bytes=WIDTH_BYTES, height_lines=height)


def wrap_lines(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """Wrap each explicit line to max_width pixels.

    Latin wraps on spaces, CJK wraps between characters, and a word too long
    for any line gets chopped rather than clipped. Public because the TUI
    preview wraps with the same font and width, so the line breaks on screen
    are the line breaks on paper.
    """
    out: list[str] = []
    for para in text.split("\n"):
        if not para:
            out.append("")
        else:
            out.extend(_wrap_paragraph(para, font, max_width))
    return out


def _wrap_paragraph(para: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for atom in _atoms(para):
        if atom == " " and not cur:
            continue  # a wrapped line never starts with a space
        if font.getlength(cur + atom) <= max_width:
            cur += atom
            continue
        if atom == " ":  # the break lands on the space: drop it
            lines.append(cur.rstrip())
            cur = ""
            continue
        if cur:
            head, atom = _keep_punctuation_attached(cur, atom)
            lines.append(head.rstrip())
            cur = ""
        # Still too wide on its own (long URL, unbroken run): chop it.
        while font.getlength(atom) > max_width:
            head = _fit_prefix(atom, font, max_width)
            lines.append(head)
            atom = atom[len(head):]
        cur = atom
    if cur or not lines:
        lines.append(cur.rstrip())
    return lines


def _atoms(para: str) -> list[str]:
    """Split a paragraph into wrap units: Latin runs, single CJK chars, spaces."""
    out: list[str] = []
    buf = ""
    for ch in para:
        if ch == " " or _is_wide(ch):
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _keep_punctuation_attached(cur: str, atom: str) -> tuple[str, str]:
    """Pull text down with a punctuation mark that must not start a line."""
    head = cur.rstrip()
    if atom[:1] not in _NO_LINE_START or len(head) <= 1:
        return cur, atom
    if _is_wide(head[-1]):
        return head[:-1], head[-1] + atom  # one CJK char is enough
    # Latin tail: take the whole trailing word, never split it mid-letter.
    space = head.rfind(" ")
    if space > 0:
        return head[:space], head[space + 1:] + atom
    return cur, atom


def _is_wide(ch: str) -> bool:
    """True for CJK-ish characters, which may break on any boundary."""
    code = ord(ch)
    return (
        0x1100 <= code <= 0x115F  # Hangul Jamo
        or 0x2E80 <= code <= 0xA4CF  # CJK radicals .. Yi (incl. kana, punctuation)
        or 0xAC00 <= code <= 0xD7A3  # Hangul syllables
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
        or 0xFE30 <= code <= 0xFE4F  # CJK compatibility forms
        or 0xFF00 <= code <= 0xFF60  # fullwidth forms
        or 0xFFE0 <= code <= 0xFFE6
        or 0x20000 <= code <= 0x3FFFD  # extensions B..
    )


def _fit_prefix(word: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """Longest prefix of word that fits in max_width (at least one char)."""
    n = 1
    while n < len(word) and font.getlength(word[: n + 1]) <= max_width:
        n += 1
    return word[:n]
