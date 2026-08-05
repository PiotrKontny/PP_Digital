"""
Card rendering.

A card is data (``cards/base_card.py``); this paints it.  The visual result
matches the prototype — wrapped title, divider, body text, badge strip — but
this version paints a card *onto its own surface* and caches it by (definition,
size, state).

That change is what makes the rest of this stage possible:

* the hand fan needs every card rotated by a few degrees, and rotating a cached
  surface is one ``rotozoom`` instead of re-laying out text every frame;
* panels need small cards and the hand needs large ones, so size became a
  parameter instead of a constant — and because the face is *painted* at its
  final size rather than scaled up from 140×200, small cards stay crisp;
* a dragged card is the same surface, drawn wherever the cursor is.

The cache is keyed by content, so the 5 identical copies of "Zerówka" in a hand
share one surface.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import pygame

from ..cards.base_card import Badge, Card
from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.theme import darken, lighten, mix
from .renderer import Renderer

Color = Tuple[int, int, int]
Size = Tuple[int, int]

#: Reference size.  Everything else is expressed as a fraction of it, so a card
#: drawn at any size keeps the same proportions.
CARD_W = 140
CARD_H = 200
NATIVE_SIZE: Size = (CARD_W, CARD_H)
#: Proportions every card keeps, whatever size it is painted at.
CARD_ASPECT = CARD_H / CARD_W

_CACHE_LIMIT = 320


class CardRenderer:
    def __init__(self, renderer: Renderer, library: ContentLibrary) -> None:
        self.r = renderer
        self.library = library
        self.theme = renderer.theme
        self._images: Dict[str, Optional[pygame.Surface]] = {}
        self._faces: Dict[tuple, pygame.Surface] = {}
        self._rainbow = library.pawn_colors()

    def clear_cache(self) -> None:
        """Drop cached faces — called when the window resizes."""
        self._faces.clear()

    # ── artwork ──────────────────────────────────────────────────────────────
    def _image(self, name: Optional[str]) -> Optional[pygame.Surface]:
        """Load card art on demand; a missing file is not an error."""
        if not name:
            return None
        if name in self._images:
            return self._images[name]
        path = settings.IMAGE_DIR / name
        surface: Optional[pygame.Surface] = None
        if path.exists():
            try:
                surface = pygame.image.load(str(path)).convert_alpha()
            except pygame.error:  # pragma: no cover - broken asset
                surface = None
        self._images[name] = surface
        return surface

    # ── cache plumbing ───────────────────────────────────────────────────────
    def _store(self, key: tuple, surface: pygame.Surface) -> pygame.Surface:
        if len(self._faces) > _CACHE_LIMIT:
            self._faces.clear()
        self._faces[key] = surface
        return surface

    def _font(self, size: Size, fraction: float, bold: bool = False):
        return self.r.fonts.get(max(8, int(size[1] * fraction)), bold=bold)

    # ── faces ────────────────────────────────────────────────────────────────
    def face(
        self,
        card: Card,
        size: Size = NATIVE_SIZE,
        *,
        highlighted: bool = False,
        border_color: Optional[Color] = None,
        dim: bool = False,
    ) -> pygame.Surface:
        """The painted front of a card, cached."""
        key = (
            "face", card.definition.deck_id, card.title, card.text,
            size, highlighted, border_color, dim,
        )
        cached = self._faces.get(key)
        if cached is not None:
            return cached

        w, h = size
        surface = pygame.Surface(size, pygame.SRCALPHA)
        theme = self.theme
        radius = max(5, int(h * 0.05))

        # Parchment, lit from the top: a card in the concept is a physical
        # thing, and a flat fill is the fastest way to lose that.
        top = theme.card_bg_highlight if highlighted else theme.card_bg
        bottom = lighten(theme.card_bg_shade, 0.12) if highlighted else theme.card_bg_shade
        if dim:
            top, bottom = darken(top, 0.86), darken(bottom, 0.86)
        self.r.rounded_gradient(surface, pygame.Rect(0, 0, w, h), top, bottom, radius)

        if border_color is not None:
            border = lighten(border_color, 0.32) if highlighted else darken(border_color, 0.8)
        else:
            border = theme.card_border_hover if highlighted else theme.card_border

        # Outer border, then an inset brass rule — the double frame the concept
        # puts on every card.
        pygame.draw.rect(surface, border, pygame.Rect(0, 0, w, h),
                         max(2, int(h * 0.014)), border_radius=radius)
        inset = max(3, int(h * 0.028))
        pygame.draw.rect(surface, theme.card_frame,
                         pygame.Rect(inset, inset, w - 2 * inset, h - 2 * inset),
                         1, border_radius=max(3, radius - 2))

        pawn_color = self._pawn_color(card.badge)
        title_color = darken(pawn_color, 0.55) if pawn_color else theme.card_title
        title_font = self._font(size, 0.068, bold=True)
        body_font = self._font(size, 0.054)

        pad = inset + max(4, int(w * 0.05))
        title_lines = self.r.wrap_lines(card.title, title_font, w - 2 * pad)[:2]
        row_h = title_font.get_height() + 1
        title_top = inset + max(4, int(h * 0.030))
        for i, line in enumerate(title_lines):
            self.r.text(line, title_font, title_color, surface,
                        midtop=(w // 2, title_top + i * row_h))

        div_y = title_top + len(title_lines) * row_h + max(3, int(h * 0.016))
        # A hairline rule with a small diamond, matching the panel headings.
        pygame.draw.line(surface, theme.card_divider,
                         (pad, div_y), (w - pad, div_y), 1)
        if w > 70:
            self.r.diamond((w // 2, div_y), max(2, int(h * 0.012)),
                           theme.card_divider, surface)

        badge_h = int(h * 0.13) if card.badge is not None else int(h * 0.04)
        body_top = div_y + max(4, int(h * 0.028))

        art = self._image(card.definition.image)
        if art is not None:
            art_h = min(int(h * 0.36), h - div_y - badge_h - int(h * 0.2))
            if art_h > 16:
                scaled = pygame.transform.smoothscale(art, (w - 2 * pad, art_h))
                surface.blit(scaled, (pad, body_top))
                body_top += art_h + 4

        self.r.draw_wrapped(
            card.text, body_font, theme.card_text,
            pygame.Rect(pad, body_top, w - 2 * pad, h - body_top - badge_h - 4),
            surface,
        )

        if card.badge is not None:
            self._draw_badge(surface, size, card.badge)
        return self._store(key, surface)

    def back(
        self, size: Size = NATIVE_SIZE, color: Optional[Color] = None,
        brightness: float = 0.0,
    ) -> pygame.Surface:
        """The back of a card.

        ``brightness`` (0..1) lightens the whole back. Deck hovering uses it:
        lifting the card's own colour reads as "this is live" far better than
        the halo that used to be drawn around the pile, which mostly looked
        like a rendering artefact.
        """
        step = round(max(0.0, min(1.0, brightness)) * 10) / 10
        key = ("back", size, color, step)
        cached = self._faces.get(key)
        if cached is not None:
            return cached

        w, h = size
        surface = pygame.Surface(size, pygame.SRCALPHA)
        theme = self.theme
        radius = max(5, int(h * 0.05))
        base = color if color is not None else theme.card_back_bg
        if step:
            base = lighten(base, 0.22 * step)

        # A deck back in the concept is a bound cover: deep colour, brass frame,
        # an emblem in the middle.
        self.r.rounded_gradient(surface, pygame.Rect(0, 0, w, h),
                                lighten(base, 0.12), darken(base, 0.74), radius)
        pygame.draw.rect(surface, darken(base, 0.45), pygame.Rect(0, 0, w, h),
                         max(2, int(h * 0.014)), border_radius=radius)

        inset = max(4, int(h * 0.045))
        frame = pygame.Rect(inset, inset, w - 2 * inset, h - 2 * inset)
        pygame.draw.rect(surface, theme.card_back_deco, frame, 1,
                         border_radius=max(3, radius - 2))
        for corner in ((frame.left, frame.top), (frame.right, frame.top),
                       (frame.left, frame.bottom), (frame.right, frame.bottom)):
            self.r.diamond(corner, max(2, int(h * 0.014)), theme.card_back_deco, surface)

        cx, cy = w // 2, h // 2
        emblem = max(6, int(h * 0.13))
        self.r.aa_circle((cx, cy), emblem + 3, darken(base, 0.6),
                         theme.card_back_deco, 1, surface)
        self.r.diamond((cx, cy), emblem, mix(base, theme.card_back_deco, 0.55), surface)
        self.r.diamond((cx, cy), max(2, emblem // 2), darken(base, 0.5), surface)
        return self._store(key, surface)

    def empty(self, size: Size = NATIVE_SIZE, label: Optional[str] = None) -> pygame.Surface:
        key = ("empty", size, label)
        cached = self._faces.get(key)
        if cached is not None:
            return cached
        w, h = size
        surface = pygame.Surface(size, pygame.SRCALPHA)
        theme = self.theme
        radius = max(5, int(h * 0.05))
        # An empty slot is a hollow in the panel, not a card-shaped rectangle:
        # dashed brass on a recess, so it reads as "something goes here".
        pygame.draw.rect(surface, theme.card_empty_bg, pygame.Rect(0, 0, w, h),
                         border_radius=radius)
        self.r.rounded_gradient(surface, pygame.Rect(1, 1, w - 2, h - 2),
                                darken(theme.panel_bg, 0.9), theme.card_empty_bg,
                                radius)
        dash = max(4, h // 22)
        for x in range(radius, w - radius, dash * 2):
            pygame.draw.line(surface, theme.card_empty_line, (x, 1), (min(x + dash, w - radius), 1))
            pygame.draw.line(surface, theme.card_empty_line, (x, h - 2),
                             (min(x + dash, w - radius), h - 2))
        for y in range(radius, h - radius, dash * 2):
            pygame.draw.line(surface, theme.card_empty_line, (1, y), (1, min(y + dash, h - radius)))
            pygame.draw.line(surface, theme.card_empty_line, (w - 2, y),
                             (w - 2, min(y + dash, h - radius)))
        pygame.draw.rect(surface, theme.card_empty_line, pygame.Rect(0, 0, w, h), 1,
                         border_radius=radius)
        if label:
            self.r.spaced_text(label.upper(), self._font(size, 0.055, bold=True),
                               theme.text_dim, surface, center=(w // 2, h // 2),
                               spacing=1)
        return self._store(key, surface)

    # ── blitting ─────────────────────────────────────────────────────────────
    def draw(
        self,
        card: Card,
        x: int,
        y: int,
        surface: Optional[pygame.Surface] = None,
        *,
        size: Size = NATIVE_SIZE,
        highlighted: bool = False,
        border_color: Optional[Color] = None,
        lift: int = 0,
        dim: bool = False,
        shadow: bool = True,
    ) -> pygame.Rect:
        target = self.r.target(surface)
        y -= lift
        rect = pygame.Rect(x, y, size[0], size[1])
        if shadow:
            self.r.drop_shadow(rect, radius=max(4, int(size[1] * 0.036)),
                               spread=6 + lift, alpha=110, offset=(0, 3 + lift),
                               surface=target)
        target.blit(
            self.face(card, size, highlighted=highlighted,
                      border_color=border_color, dim=dim),
            rect.topleft,
        )
        return rect

    def draw_in(
        self, card: Card, rect: pygame.Rect,
        surface: Optional[pygame.Surface] = None, **kwargs,
    ) -> pygame.Rect:
        """Draw a card filling a rect — the form every panel uses."""
        return self.draw(card, rect.x, rect.y, surface, size=(rect.w, rect.h), **kwargs)

    def _silhouette(self, surface: pygame.Surface, key: tuple) -> pygame.Surface:
        """A black copy of a card surface, used as its shadow.

        Multiplying the RGB to zero keeps the alpha channel, so the shadow has
        exactly the card's outline — including its rotation. The previous
        version drew an upright rectangle behind every card, which looked wrong
        the moment the fan tilted them.
        """
        cached = self._faces.get(key)
        if cached is not None:
            return cached
        shadow = surface.copy()
        shadow.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
        # Two blurred-ish passes: scale down and back up to soften the edge.
        small = pygame.transform.smoothscale(
            shadow, (max(1, shadow.get_width() // 6), max(1, shadow.get_height() // 6))
        )
        soft = pygame.transform.smoothscale(small, shadow.get_size())
        soft.set_alpha(130)
        return self._store(key, soft)

    #: Card sizes are rounded to this many pixels of height before a face is
    #: painted.  Without it a smoothly growing card would paint a new face every
    #: frame; with it there are a handful of steps, each one crisp.
    SIZE_STEP = 6

    def quantised(self, size: Size, scale: float = 1.0) -> Size:
        """The size a face will actually be painted at.

        Scaling is applied *before* the face is drawn, not after: enlarging an
        already-rasterised card is what made hovered cards and previews blurry.
        """
        height = max(24, int(round(size[1] * scale / self.SIZE_STEP)) * self.SIZE_STEP)
        return (max(16, int(round(height / CARD_ASPECT))), height)

    def draw_transformed(
        self,
        card: Card,
        centre: Tuple[float, float],
        angle: float,
        surface: Optional[pygame.Surface] = None,
        *,
        size: Size = NATIVE_SIZE,
        scale: float = 1.0,
        highlighted: bool = False,
        border_color: Optional[Color] = None,
        shadow: int = 8,
    ) -> pygame.Rect:
        """Draw a card rotated about its centre — the hand fan and drag ghost.

        ``angle`` is in degrees, positive counter-clockwise (pygame's
        convention), so a fan tilts its right-hand cards with negative angles.
        The shadow is the rotated silhouette offset downwards, which is what a
        card lying above a table actually casts.

        A scaled card is **repainted at the larger size**, not zoomed: the text
        is laid out again at the size it will be seen, so a card that grows
        under the cursor gets sharper rather than softer.
        """
        target = self.r.target(surface)
        paint_size = self.quantised(size, scale) if abs(scale - 1.0) > 0.005 else size
        face = self.face(card, paint_size, highlighted=highlighted,
                         border_color=border_color)
        if abs(angle) < 0.05:
            rotated = face
        else:
            rotated = pygame.transform.rotozoom(face, angle, 1.0)
        rect = rotated.get_rect(center=(int(centre[0]), int(centre[1])))

        if shadow:
            # Quantise the transform so the cache is not defeated by animation.
            key = ("shadow", size, round(angle, 1), round(scale, 2))
            silhouette = self._silhouette(rotated, key)
            offset = (shadow // 3, max(3, shadow // 2))
            target.blit(silhouette, (rect.x + offset[0], rect.y + offset[1]))
        target.blit(rotated, rect.topleft)
        return rect

    def draw_back(
        self, x: int, y: int, color: Optional[Color] = None,
        surface: Optional[pygame.Surface] = None, size: Size = NATIVE_SIZE,
        brightness: float = 0.0,
    ) -> pygame.Rect:
        target = self.r.target(surface)
        rect = pygame.Rect(x, y, size[0], size[1])
        self.r.drop_shadow(rect, radius=max(4, int(size[1] * 0.036)),
                           spread=5 + int(4 * brightness), alpha=100,
                           offset=(0, 3), surface=target)
        target.blit(self.back(size, color, brightness), rect.topleft)
        return rect

    def draw_empty(
        self, x: int, y: int, surface: Optional[pygame.Surface] = None,
        label: Optional[str] = None, size: Size = NATIVE_SIZE,
    ) -> pygame.Rect:
        target = self.r.target(surface)
        target.blit(self.empty(size, label), (x, y))
        return pygame.Rect(x, y, size[0], size[1])

    def draw_pile(
        self,
        x: int,
        y: int,
        count: int,
        color: Optional[Color],
        surface: Optional[pygame.Surface] = None,
        empty_label: str = "pusto",
        size: Size = NATIVE_SIZE,
        brightness: float = 0.0,
    ) -> pygame.Rect:
        """A draw pile: up to three stacked backs so depth is visible.

        Only the top card takes the hover brightness — the ones underneath stay
        in its shadow, which is what stops the effect looking like a light bulb.
        """
        if count <= 0:
            return self.draw_empty(x, y, surface, empty_label, size)
        lift = int(round(brightness * 2))
        for depth in range(min(3, count) - 1, -1, -1):
            top = depth == 0
            self.draw_back(
                x + depth * 2, y + depth * 2 - (lift if top else 0), color, surface,
                size, brightness if top else 0.0,
            )
        return pygame.Rect(x, y, size[0], size[1])

    # ── badge ────────────────────────────────────────────────────────────────
    def _pawn_color(self, badge: Optional[Badge]) -> Optional[Color]:
        if badge is None or badge.is_rainbow:
            return None
        return self.library.pawn_color(badge.pawn)

    def _draw_badge(self, surface: pygame.Surface, size: Size, badge: Badge) -> None:
        w, h = size
        font = self._font(size, 0.062, bold=True)
        radius = max(4, int(h * 0.035))
        cy = h - int(h * 0.075)
        sign_surface = self.r.text_surface(badge.sign, font, (55, 45, 25))
        arrow_surface = (
            self.r.text_surface("\u2191", font, (55, 45, 25)) if badge.arrow else None
        )

        gap = 4
        total_w = (
            radius * 2 + gap + sign_surface.get_width()
            + (arrow_surface.get_width() + gap if arrow_surface else 0)
        )
        start_x = w // 2 - total_w // 2
        cx = start_x + radius

        if badge.is_rainbow:
            self.r.pie_circle((cx, cy), radius, self._rainbow, surface)
        else:
            color = self.library.pawn_color(badge.pawn) or (200, 200, 200)
            self.r.aa_circle((cx, cy), radius, color, (70, 60, 40), 1, surface)

        nx = start_x + radius * 2 + gap
        if arrow_surface is not None:
            surface.blit(arrow_surface, (nx, cy - arrow_surface.get_height() // 2))
            nx += arrow_surface.get_width() + gap
        surface.blit(sign_surface, (nx, cy - sign_surface.get_height() // 2))
