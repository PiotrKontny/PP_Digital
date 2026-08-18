"""
Generate ``assets/portraits/placeholder.png``.

WHY A GENERATOR AND NOT A DRAWING ROUTINE
    The placeholder is an ASSET, exactly like every real portrait will be, and
    the game loads it through the same ``PortraitLibrary`` path a photograph
    would take.  Drawing it procedurally each frame would have made it the one
    portrait that is not a file — so replacing it would have meant editing
    Python, which is precisely what stage 49 set out to avoid.

    This script exists so the shipped PNG can be regenerated or re-tuned
    without hand-editing a binary.  Running it is NOT part of the build and not
    part of the tests: the committed PNG is the asset.  Anybody replacing the
    placeholder with real art should simply overwrite the file and ignore this
    script entirely.

USAGE
    python tools/make_portrait_placeholder.py [--size 640] [--out PATH]

WHAT IT DRAWS
    A bust silhouette in brass on the panel's own dark field, with the vignette
    and the faint corner diamonds the premium panels use.  Deliberately NOT a
    generic "no image" glyph: it has to look like a piece of this game sitting
    in a frame, so that a panel full of placeholders reads as art not yet
    drawn rather than as a row of broken images.

    No frame and no border — the well and the brass edge are drawn by
    ``CardRenderer.draw_portrait``, and a border baked in here would appear
    twice.  Same rule the card-art README states for card pictures.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from pedzacy_piotrek.config import settings  # noqa: E402
from pedzacy_piotrek.config.theme import Theme, darken, lighten, mix  # noqa: E402


def _radial_vignette(size: int, theme: Theme) -> pygame.Surface:
    """A soft dark falloff towards the corners, drawn as concentric rings."""
    layer = pygame.Surface((size, size), pygame.SRCALPHA)
    centre = size / 2
    steps = 48
    for index in range(steps, 0, -1):
        fraction = index / steps
        radius = int(centre * 1.42 * fraction)
        alpha = int(150 * (fraction ** 2.2))
        pygame.draw.circle(layer, (*darken(theme.background_deep, 0.7), alpha),
                           (int(centre), int(centre)), radius)
    return layer


def _bust(surface: pygame.Surface, size: int, theme: Theme) -> None:
    """Head and shoulders, centred, in the panel's brass.

    Proportioned so the crop ``CardRenderer._cover`` applies at any panel
    shape — the well is near square but not exactly — never cuts the head.
    """
    unit = size / 100.0
    centre_x = size / 2
    fill = mix(theme.brass, theme.panel_bg_light, 0.46)
    rim = mix(theme.brass_light, theme.panel_bg, 0.35)

    figure = pygame.Surface((size, size), pygame.SRCALPHA)

    # Shoulders run off BOTH the bottom and the sides, so this reads as a bust
    # cropped by its frame — the way a real portrait sits — rather than as a
    # free-floating avatar glyph on a field.
    shoulders = pygame.Rect(0, 0, int(96 * unit), int(80 * unit))
    shoulders.center = (int(centre_x), int(107 * unit))
    pygame.draw.ellipse(figure, fill, shoulders)

    # Neck, drawn before the head and overlapped by it so the silhouette is one
    # continuous shape with no seam where the two meet.
    neck = pygame.Rect(0, 0, int(19 * unit), int(26 * unit))
    neck.center = (int(centre_x), int(60 * unit))
    pygame.draw.rect(figure, fill, neck, border_radius=int(6 * unit))

    # A head slightly TALLER than wide.  A circle is what makes a silhouette
    # look like a browser icon; an egg looks like a person.
    head = pygame.Rect(0, 0, int(30 * unit), int(35 * unit))
    head.center = (int(centre_x), int(42 * unit))
    pygame.draw.ellipse(figure, fill, head)

    # Rim light from the upper left, the direction every bevel in this
    # interface is lit from.  Drawn as a clipped arc pair rather than an
    # outline, so the lit edge falls off instead of ringing the whole figure.
    light = pygame.Surface((size, size), pygame.SRCALPHA)
    width = max(2, int(1.6 * unit))
    pygame.draw.arc(light, rim, head, math.radians(55), math.radians(205), width)
    pygame.draw.arc(light, rim, shoulders, math.radians(100), math.radians(155),
                    width)
    figure.blit(light, (0, 0))
    surface.blit(figure, (0, 0))


def _corner_diamonds(surface: pygame.Surface, size: int, theme: Theme) -> None:
    """The brass lozenges the premium panels put in their corners."""
    unit = size / 100.0
    inset = int(9 * unit)
    half = max(2, int(2.2 * unit))
    colour = (*mix(theme.brass, theme.panel_bg, 0.35), 120)
    layer = pygame.Surface((size, size), pygame.SRCALPHA)
    for x in (inset, size - inset):
        for y in (inset, size - inset):
            pygame.draw.polygon(layer, colour, [
                (x, y - half), (x + half, y), (x, y + half), (x - half, y),
            ])
    surface.blit(layer, (0, 0))


def build(size: int = 640) -> pygame.Surface:
    theme = Theme()
    surface = pygame.Surface((size, size), pygame.SRCALPHA)

    # Base field: a vertical gradient between the two panel tones, so the
    # placeholder sits in the same light as the panel behind it.
    top = lighten(theme.panel_bg_light, 0.06)
    bottom = darken(theme.panel_bg, 0.55)
    for y in range(size):
        t = y / max(1, size - 1)
        colour = mix(top, bottom, t ** 1.15)
        pygame.draw.line(surface, colour, (0, y), (size, y))

    # A faint diagonal weave, so a flat fill does not read as a loading state.
    weave = pygame.Surface((size, size), pygame.SRCALPHA)
    step = max(4, int(size / 40))
    for offset in range(-size, size * 2, step):
        pygame.draw.line(weave, (*lighten(theme.panel_bg_light, 0.3), 14),
                         (offset, 0), (offset - size, size), 1)
    surface.blit(weave, (0, 0))

    _bust(surface, size, theme)
    surface.blit(_radial_vignette(size, theme), (0, 0))
    _corner_diamonds(surface, size, theme)
    return surface


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=640,
                        help="square edge in pixels (default 640)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output path (default assets/portraits/placeholder.png)")
    args = parser.parse_args()

    pygame.init()
    out = args.out or (settings.PORTRAIT_DIR / settings.PORTRAIT_PLACEHOLDER)
    out.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(build(args.size), str(out))
    print(f"wrote {out} ({args.size}x{args.size})")
    pygame.quit()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
