"""core.comic.panel_size — panel canvas size from a page plan's shape_hint.

panel_compose used to render every panel at the fixed ``INKSTONE_PANEL_SIZE``
square. The planner's ``shape_hint`` vocabulary (`wide` horizontal band,
`tall` standing figure, `square`, `rect` landscape) never reached the size
decision, so "One long horizontal band: ... two small figures at the table"
came back as a full-bleed square portrait of one chest-up figure — the last
two pages of c0001 each spend a whole page on one square upscaled to page
width. The provider accepts a small set of canvas sizes; landscape and
portrait shapes get the matching canvas, everything else keeps the default.
"""

from __future__ import annotations

# Provider-accepted sizes for the shape vocabulary. 1536 keeps the long edge
# at the same resolution as the square default, so a wide band loses no
# detail against what a square panel would have carried.
_SHAPE_SIZES: dict[str, str] = {
    "wide": "1536x1024",
    "rect": "1536x1024",
    "tall": "1024x1536",
}


def panel_size_for_shape(shape_hint: str | None, default: str = "1024x1024") -> str:
    """Canvas size for one page-plan panel from its ``shape_hint``.

    Unknown / empty / ``inset`` hints keep *default* — an inset is framed
    inside another panel by the layout, not by its canvas aspect.
    """
    return _SHAPE_SIZES.get((shape_hint or "").strip().casefold(), default)
