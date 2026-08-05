"""
Shared heading geometry.

Three menus draw the same title block, and while each of them measured it
separately they disagreed — which is how the settings subtitle ended up on top
of the first row label at 1280×760.  Measuring the fonts rather than assuming a
height means a longer subtitle cannot push into whatever comes below it.
"""

from __future__ import annotations

import pygame

TITLE = "Pędzący Piotrek"

#: Heading geometry, in one place because three screens share it and the
#: overlaps this stage fixed came from each of them guessing separately.
TITLE_TOP_FRACTION = 0.05
TITLE_SIZE = 40
SUBTITLE_SIZE = 20
TITLE_GAP = 10
BLOCK_GAP = 26


def title_height(r) -> int:
    """How much vertical room the heading occupies, subtitle included."""
    return (r.fonts.get(TITLE_SIZE, bold=True).get_height()
            + TITLE_GAP + r.fonts.get(SUBTITLE_SIZE).get_height())


def title_top(layout) -> int:
    return int(layout.win_h * TITLE_TOP_FRACTION)


def content_top(r, layout) -> int:
    """First y a screen may put its own content on."""
    return title_top(layout) + title_height(r) + BLOCK_GAP


def draw_title(r, layout, surface, subtitle: str = "") -> int:
    """Shared heading.  Returns the y to carry on from.

    The returned value is measured from the fonts rather than assumed, so a
    longer subtitle or a different font size cannot push the heading into
    whatever the screen draws underneath it.
    """
    centre = layout.win_w // 2
    top = title_top(layout)
    r.spaced_text(TITLE.upper(), r.fonts.get(TITLE_SIZE, bold=True),
                  r.theme.brass_bright, surface,
                  center=(centre, top + r.fonts.get(TITLE_SIZE, bold=True).get_height() // 2),
                  spacing=6, shadow=True)
    if subtitle:
        r.spaced_text(subtitle.upper(), r.fonts.get(SUBTITLE_SIZE - 3, bold=True),
                      r.theme.text_heading, surface,
                      center=(centre,
                              top + r.fonts.get(TITLE_SIZE, bold=True).get_height()
                              + TITLE_GAP + r.fonts.get(SUBTITLE_SIZE).get_height() // 2),
                      spacing=3)
    return content_top(r, layout)


