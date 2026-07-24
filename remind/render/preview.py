"""Terminal preview of what will come out of the printer.

The terminal can't scale glyphs, so a bigger font can't draw bigger — instead
the whole print is scaled *down* to character cells at one consistent px→cell
ratio: a larger font means fewer cells across the paper, i.e. a narrower strip.

Line breaks are not approximated: they come from the real renderer's wrap with
the real font at the real width, so what wraps on screen wraps on paper.
"""

from __future__ import annotations

from rich.cells import cell_len

from .fonts import load_font
from .raster import content_geometry
from .text import wrap_lines

# Terminal cells are roughly twice as tall as they are wide.
_CELL_ASPECT = 2.0
# Sample used to measure the font's average advance (typical prose mix). One
# such advance is one cell — which also lines up with CJK, whose glyphs are
# about twice as wide and take exactly two cells.
_SAMPLE = "abcdefghijklmnopqrstuvwxyz "


def preview_geometry(
    *,
    font_size: int = 48,
    margin_x: int = 12,
    margin_top: int = 12,
    margin_bottom: int = 12,
    paper_width_mm: float = 48,
) -> tuple[int, int, int, int]:
    """Return (box_cols, pad_cols, top_rows, bottom_rows): the empty paper.

    The cell geometry a preview_layout would use before any text is placed —
    the full strip width and the four margins, in terminal cells/rows. The
    editor reserves these same margins (as padding) so what you type wraps at
    the content width and sits inside the same border the preview will.
    """
    font = load_font(font_size)
    char_w = max(1.0, font.getlength(_SAMPLE) / len(_SAMPLE))
    row_px = char_w * _CELL_ASPECT

    paper_px, margin_x, _content_px = content_geometry(paper_width_mm, margin_x)

    box = max(4, round(paper_px / char_w))
    pad_cols = round(margin_x / char_w)
    top = round(margin_top / row_px)
    bottom = round(margin_bottom / row_px)
    return box, pad_cols, top, bottom


def preview_layout(
    text: str,
    *,
    font_size: int = 48,
    margin_x: int = 12,
    margin_top: int = 12,
    margin_bottom: int = 12,
    paper_width_mm: float = 48,
) -> tuple[list[str], int]:
    """Return (rows, box_cols): the paper strip as fixed-width terminal rows.

    Widths are counted in terminal cells, so CJK (double-width) lines up.
    Margins are drawn as real blank columns/rows, pixel-rounded — so a margin
    smaller than one cell legitimately rounds away, same as the eye would see.
    """
    font = load_font(font_size)
    _paper_px, _margin_x, content_px = content_geometry(paper_width_mm, margin_x)
    box, pad_cols, top, bottom = preview_geometry(
        font_size=font_size,
        margin_x=margin_x,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        paper_width_mm=paper_width_mm,
    )

    wrapped = wrap_lines(text, font, content_px) if text.strip() else [""]

    # Never clip: proportional glyphs mean a wrapped line can still need more
    # cells than the scale predicts (narrow letters). Widen the strip instead.
    box = max(box, pad_cols * 2 + max(cell_len(w) for w in wrapped))

    pad = " " * pad_cols
    rows = [""] * top + [pad + w for w in wrapped] + [""] * bottom
    return [row + " " * (box - cell_len(row)) for row in rows], box
