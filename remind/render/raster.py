"""Raster primitives: the 1-bit bitmap format the M02 consumes.

Shared by every renderer (text, and later layouts / images). 384 px wide
(48 bytes/row), 1 bit per pixel, MSB first, bit=1 means a black dot.
"""

from __future__ import annotations

from dataclasses import dataclass

WIDTH_PX = 384  # M02: 48 bytes * 8
WIDTH_BYTES = WIDTH_PX // 8
DPI = 203  # M02 print head resolution


def mm_to_px(mm: float) -> int:
    """Millimetres of paper -> dots. 48mm ≈ the full 384px head width."""
    return round(mm / 25.4 * DPI)


MIN_CONTENT_PX = 8  # always leave room for at least a sliver of text


def usable_width_px(paper_width_mm: float) -> int:
    """Printable width for a paper belt, never wider than the head."""
    return max(MIN_CONTENT_PX, min(WIDTH_PX, mm_to_px(paper_width_mm)))


def content_geometry(paper_width_mm: float, margin_x: int) -> tuple[int, int, int]:
    """(paper_px, margin_px, content_px) for a config, margins clamped to fit.

    Shared by the renderer and the preview so both agree on where text goes —
    including when someone sets a margin wider than the paper.
    """
    paper_px = usable_width_px(paper_width_mm)
    margin_px = max(0, min(margin_x, (paper_px - MIN_CONTENT_PX) // 2))
    return paper_px, margin_px, paper_px - 2 * margin_px


@dataclass
class Raster:
    data: bytes
    width_bytes: int
    height_lines: int
