"""Pick a typeface that can actually draw the text — CJK included.

Pillow's built-in default font has no CJK glyphs, so Chinese comes out as empty
boxes. Look for a system font that covers both scripts and fall back to the
default only when nothing is installed.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import ImageFont

# (file, face index) in preference order; first one that loads wins.
# .ttc files hold several faces — the index picks the one we want, and a
# missing index just raises, moving on to the next candidate.
_CANDIDATES: list[tuple[str, int]] = [
    ("/System/Library/Fonts/PingFang.ttc", 3),  # macOS, SC face
    ("/System/Library/Fonts/PingFang.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),  # latin-only last resort
]


@lru_cache(maxsize=1)
def find_font() -> tuple[str, int] | None:
    """First installed candidate, or None if we're stuck with the default.

    Probes by opening the file, not by Path.exists() — macOS hides system
    fonts like PingFang from directory listings, so exists() lies about them.
    """
    for path, index in _CANDIDATES:
        try:
            ImageFont.truetype(path, 12, index=index)
        except (OSError, ValueError):
            continue
        return path, index
    return None


@lru_cache(maxsize=64)
def load_font(size: int) -> ImageFont.ImageFont:
    """The one place the typeface is chosen — preview and print share it."""
    found = find_font()
    if found is not None:
        try:
            return ImageFont.truetype(found[0], size, index=found[1])
        except (OSError, ValueError):
            pass
    return ImageFont.load_default(size=size)
