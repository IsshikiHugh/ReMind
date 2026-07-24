"""Rendering: content -> 1-bit raster for the M02.

Public API is stable across the split into submodules — import from here:

    from remind.render import render_text, preview_layout, Raster
"""

from .fonts import find_font, load_font
from .preview import preview_geometry, preview_layout
from .raster import DPI, WIDTH_BYTES, WIDTH_PX, Raster, mm_to_px, usable_width_px
from .text import render_text, wrap_lines

__all__ = [
    "render_text",
    "preview_layout",
    "preview_geometry",
    "wrap_lines",
    "load_font",
    "find_font",
    "Raster",
    "WIDTH_PX",
    "WIDTH_BYTES",
    "DPI",
    "mm_to_px",
    "usable_width_px",
]
