"""
The rendering engine.

Every drawing primitive the game uses lives here, so panels, cards and the
board all share one implementation of "rounded box with a drop shadow" or
"anti-aliased circle with a rim".  The prototype had these as free functions
sprinkled between game logic (``_aa_circle``, ``_wrap_lines``, ``_darken``);
gathering them makes the visual style adjustable in one place and gives the
future shader/quality settings somewhere to hook in.

Two performance notes that matter at 60 FPS:

* text is cached — ``font.render`` is expensive and the HUD re-renders the
  same dozen strings every frame;
* surfaces built per frame (glows, shadows) are cached by their parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import pygame
import pygame.gfxdraw

from ..config.theme import THEME, FontBook, Theme, darken, lighten, mix
from .highlight import BLOOM, Emphasis, bloom_for, emphasis

Color = Tuple[int, int, int]
Point = Tuple[float, float]


class Renderer:
    """Stateless-by-frame drawing helper bound to a target surface."""

    def __init__(self, theme: Optional[Theme] = None, fonts: Optional[FontBook] = None) -> None:
        self.theme = theme or THEME
        self.fonts = fonts or FontBook()
        self.surface: Optional[pygame.Surface] = None
        self._text_cache: Dict[Tuple[int, str, Color], pygame.Surface] = {}
        self._shadow_cache: Dict[Tuple[int, int, int, int], pygame.Surface] = {}
        #: Keyed by shape as well as size: rounded-rect blooms and rim blooms
        #: share the cache but never each other's entries.
        self._glow_cache: Dict[tuple, pygame.Surface] = {}
        self._panel_cache: Dict[tuple, pygame.Surface] = {}
        self._background_cache: Dict[Tuple[int, int], pygame.Surface] = {}
        #: Text measurement, so fitting a label to its button costs one lookup
        #: per frame rather than a walk down the font sizes.
        self._measure_cache: Dict[tuple, int] = {}
        self._fit_cache: Dict[tuple, int] = {}
        self.frame = 0

    def clear_caches(self) -> None:
        """Drop everything rasterised at the previous scale.

        Called when the window (and with it the type size) changes: keeping the
        old surfaces would put two different sizes of the same text on screen.
        """
        self._text_cache.clear()
        self._shadow_cache.clear()
        self._glow_cache.clear()
        self._panel_cache.clear()
        self._background_cache.clear()
        self._measure_cache.clear()
        self._fit_cache.clear()

    # ── frame lifecycle ──────────────────────────────────────────────────────
    def begin(self, surface: pygame.Surface) -> None:
        self.surface = surface
        self.frame += 1
        if len(self._text_cache) > 3000:
            self._text_cache.clear()

    def target(self, surface: Optional[pygame.Surface] = None) -> pygame.Surface:
        target = surface if surface is not None else self.surface
        if target is None:  # pragma: no cover - programming error
            raise RuntimeError("Renderer.begin() nie zostało wywołane")
        return target

    # ── text ─────────────────────────────────────────────────────────────────
    def text_surface(self, text: str, font: pygame.font.Font, color: Color) -> pygame.Surface:
        key = (id(font), text, color)
        cached = self._text_cache.get(key)
        if cached is None:
            cached = font.render(text, True, color)
            self._text_cache[key] = cached
        return cached

    def text(
        self,
        text: str,
        font: pygame.font.Font,
        color: Color,
        surface: Optional[pygame.Surface] = None,
        *,
        topleft: Optional[Point] = None,
        topright: Optional[Point] = None,
        bottomleft: Optional[Point] = None,
        bottomright: Optional[Point] = None,
        center: Optional[Point] = None,
        midtop: Optional[Point] = None,
        midbottom: Optional[Point] = None,
        midleft: Optional[Point] = None,
        midright: Optional[Point] = None,
        shadow: bool = False,
    ) -> pygame.Rect:
        target = self.target(surface)
        rendered = self.text_surface(text, font, color)
        rect = rendered.get_rect()
        # Whichever anchor the caller passed wins; they are mutually exclusive.
        for name, value in (
            ("topleft", topleft), ("topright", topright),
            ("bottomleft", bottomleft), ("bottomright", bottomright),
            ("center", center), ("midtop", midtop), ("midbottom", midbottom),
            ("midleft", midleft), ("midright", midright),
        ):
            if value is not None:
                setattr(rect, name, (int(value[0]), int(value[1])))
                break
        if shadow:
            dark = self.text_surface(text, font, darken(color, 0.25))
            target.blit(dark, (rect.x + 1, rect.y + 1))
        target.blit(rendered, rect)
        return rect

    def wrap_lines(self, text: str, font: pygame.font.Font, max_width: int) -> List[str]:
        """Word wrap, breaking mid-word only when a single word cannot fit."""
        words, lines, line = text.split(), [], []
        for word in words:
            if font.size(word)[0] > max_width:
                if line:
                    lines.append(" ".join(line))
                    line = []
                part = ""
                for char in word:
                    if part and font.size(part + char)[0] > max_width:
                        lines.append(part)
                        part = char
                    else:
                        part += char
                if part:
                    line = [part]
                continue
            candidate = " ".join(line + [word])
            if font.size(candidate)[0] <= max_width:
                line.append(word)
            else:
                if line:
                    lines.append(" ".join(line))
                line = [word]
        if line:
            lines.append(" ".join(line))
        return lines

    def draw_wrapped(
        self,
        text: str,
        font: pygame.font.Font,
        color: Color,
        rect: pygame.Rect,
        surface: Optional[pygame.Surface] = None,
        center: bool = False,
    ) -> int:
        target = self.target(surface)
        lines = self.wrap_lines(text, font, rect.width)
        row_h = font.get_height() + 2
        drawn = 0
        for i, line in enumerate(lines):
            top = rect.top + i * row_h
            if top + row_h > rect.bottom:
                break
            rendered = self.text_surface(line, font, color)
            if center:
                target.blit(rendered, rendered.get_rect(centerx=rect.centerx, top=top))
            else:
                target.blit(rendered, (rect.left, top))
            drawn += 1
        return drawn

    # ── primitives ───────────────────────────────────────────────────────────
    def aa_circle(
        self,
        center: Point,
        radius: int,
        fill: Color,
        border: Optional[Color] = None,
        border_width: int = 2,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        target = self.target(surface)
        x, y = int(round(center[0])), int(round(center[1]))
        radius = max(1, int(radius))
        if border is not None:
            pygame.gfxdraw.filled_circle(target, x, y, radius, border)
            pygame.gfxdraw.aacircle(target, x, y, radius, border)
            inner = max(0, radius - border_width)
            if inner > 0:
                pygame.gfxdraw.filled_circle(target, x, y, inner, fill)
                pygame.gfxdraw.aacircle(target, x, y, inner, fill)
        else:
            pygame.gfxdraw.filled_circle(target, x, y, radius, fill)
            pygame.gfxdraw.aacircle(target, x, y, radius, fill)

    def aa_ring(
        self,
        center: Point,
        radius: int,
        color: Color,
        width: int = 2,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        target = self.target(surface)
        x, y = int(round(center[0])), int(round(center[1]))
        for w in range(width):
            pygame.gfxdraw.aacircle(target, x, y, max(0, int(radius) - w), color)

    def aa_line(
        self,
        p0: Point,
        p1: Point,
        color: Color,
        width: int = 2,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        target = self.target(surface)
        if width <= 1:
            pygame.draw.aaline(target, color, p0, p1)
            return
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        half = width / 2
        quad = [
            (p0[0] + nx * half, p0[1] + ny * half),
            (p1[0] + nx * half, p1[1] + ny * half),
            (p1[0] - nx * half, p1[1] - ny * half),
            (p0[0] - nx * half, p0[1] - ny * half),
        ]
        self.aa_polygon(quad, color, surface=target)

    def aa_polygon(
        self, points: Sequence[Point], color: Color, surface: Optional[pygame.Surface] = None
    ) -> None:
        target = self.target(surface)
        pts = [(int(round(p[0])), int(round(p[1]))) for p in points]
        if len(pts) < 3:
            return
        pygame.gfxdraw.filled_polygon(target, pts, color)
        pygame.gfxdraw.aapolygon(target, pts, color)

    def aa_triangle(
        self, p1: Point, p2: Point, p3: Point, color: Color,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        target = self.target(surface)
        pts = [(int(round(p[0])), int(round(p[1]))) for p in (p1, p2, p3)]
        pygame.gfxdraw.filled_trigon(target, *pts[0], *pts[1], *pts[2], color)
        pygame.gfxdraw.aatrigon(target, *pts[0], *pts[1], *pts[2], color)

    def arrow(
        self,
        p0: Point,
        p1: Point,
        color: Color,
        width: int = 3,
        head: int = 10,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        target = self.target(surface)
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        base = (p1[0] - ux * head, p1[1] - uy * head)
        self.aa_line(p0, base, color, width, target)
        self.aa_triangle(
            (base[0] - uy * head * 0.7, base[1] + ux * head * 0.7),
            p1,
            (base[0] + uy * head * 0.7, base[1] - ux * head * 0.7),
            color,
            target,
        )

    def pie_circle(
        self,
        center: Point,
        radius: int,
        colors: Sequence[Color],
        surface: Optional[pygame.Surface] = None,
        rim: Color = (70, 60, 40),
    ) -> None:
        """The 'any pawn' rainbow wheel used on card badges."""
        target = self.target(surface)
        cx, cy = center
        n = max(1, len(colors))
        for i, col in enumerate(colors):
            a0 = math.radians(-90 + i * (360 / n))
            a1 = math.radians(-90 + (i + 1) * (360 / n))
            pts: List[Point] = [(cx, cy)]
            steps = 6
            for s in range(steps + 1):
                a = a0 + (a1 - a0) * s / steps
                pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
            pygame.draw.polygon(target, col, [(int(p[0]), int(p[1])) for p in pts])
        self.aa_ring(center, radius, rim, 1, target)

    # ── boxes ────────────────────────────────────────────────────────────────
    def panel(
        self,
        rect: pygame.Rect,
        fill: Color,
        border: Optional[Color] = None,
        radius: int = 10,
        border_width: int = 1,
        shadow: int = 0,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        target = self.target(surface)
        if shadow:
            self.drop_shadow(rect, radius=radius, spread=shadow, surface=target)
        pygame.draw.rect(target, fill, rect, border_radius=radius)
        if border is not None:
            pygame.draw.rect(target, border, rect, border_width, border_radius=radius)

    def drop_shadow(
        self,
        rect: pygame.Rect,
        radius: int = 10,
        spread: int = 8,
        alpha: int = 90,
        offset: Tuple[int, int] = (0, 4),
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        """Soft shadow under a rounded box, cached by size."""
        target = self.target(surface)
        key = (rect.width, rect.height, radius, spread)
        shadow = self._shadow_cache.get(key)
        if shadow is None:
            w = rect.width + spread * 2
            h = rect.height + spread * 2
            shadow = pygame.Surface((w, h), pygame.SRCALPHA)
            layers = max(1, spread)
            for i in range(layers, 0, -1):
                a = int(alpha * (1 - i / (layers + 1)) ** 1.6)
                inflate = i
                pygame.draw.rect(
                    shadow,
                    (0, 0, 0, a),
                    pygame.Rect(spread - inflate, spread - inflate,
                                rect.width + inflate * 2, rect.height + inflate * 2),
                    border_radius=radius + inflate,
                )
            self._shadow_cache[key] = shadow
        target.blit(shadow, (rect.x - spread + offset[0], rect.y - spread + offset[1]))

    # ── highlights: they follow the shape, always ────────────────────────────
    # There used to be one ``glow(centre, radius, ...)`` here and every caller
    # picked its own multiplier, so a highlight on a 300×70 player tile was a
    # 225-pixel glowing disc lying across the panel behind it.  The two methods
    # below take the COMPONENT'S OWN GEOMETRY instead of a multiplier, which is
    # why neither of them can produce that any more.  See render/highlight.py.

    def shape_glow(
        self,
        rect: pygame.Rect,
        color: Color,
        surface: Optional[pygame.Surface] = None,
        *,
        radius: int = 12,
        strength: float = 0.0,
        spread: Optional[int] = None,
        alpha: int = 96,
    ) -> None:
        """A soft bloom hugging the outline of a rounded rectangle.

        Drawn *under* the component, so only the few pixels that escape its
        edges are ever seen: the effect is a lit rim, not a halo.  Cached by
        shape, because a panel that glows every frame would otherwise rebuild
        the same surface sixty times a second.
        """
        if strength <= 0.01 or rect.width <= 0 or rect.height <= 0:
            return
        target = self.target(surface)
        spread = spread if spread is not None else bloom_for(rect.width, rect.height)
        spread = max(2, int(spread))
        peak = max(1, min(255, int(alpha * min(1.0, strength))))
        key = ("rect", rect.width, rect.height, radius, spread, color, peak)
        glow = self._glow_cache.get(key)
        if glow is None:
            glow = pygame.Surface((rect.width + spread * 2, rect.height + spread * 2),
                                  pygame.SRCALPHA)
            layers = max(3, spread // 2)
            for i in range(layers, 0, -1):
                grow = int(spread * i / layers)
                a = int(peak * (1 - i / (layers + 1)) ** 1.7)
                pygame.draw.rect(
                    glow, (*color, a),
                    pygame.Rect(spread - grow, spread - grow,
                                rect.width + grow * 2, rect.height + grow * 2),
                    border_radius=radius + grow,
                )
            if len(self._glow_cache) > 220:
                self._glow_cache.clear()
            self._glow_cache[key] = glow
        target.blit(glow, (rect.left - spread, rect.top - spread))

    def ring_glow(
        self,
        centre: Point,
        radius: int,
        color: Color,
        surface: Optional[pygame.Surface] = None,
        *,
        strength: float = 1.0,
        alpha: int = 110,
        bloom: float = BLOOM,
    ) -> None:
        """The circular counterpart: a lit rim around something genuinely round.

        ``radius`` is the COMPONENT's radius — a pawn's, a portrait's, a board
        field's — and the light reaches ``bloom`` of it further out.  Passing a
        radius already multiplied by 2.6 is the mistake this signature exists to
        prevent.
        """
        if strength <= 0.01:
            return
        target = self.target(surface)
        radius = max(2, int(radius))
        outer = radius + max(3, int(radius * bloom))
        peak = max(1, min(255, int(alpha * min(1.0, strength))))
        key = ("ring", radius, outer, color, peak)
        glow = self._glow_cache.get(key)
        if glow is None:
            size = outer * 2
            glow = pygame.Surface((size, size), pygame.SRCALPHA)
            steps = max(4, (outer - radius) + 2)
            for i in range(steps, 0, -1):
                r = int(radius + (outer - radius) * i / steps)
                a = int(peak * (1 - i / (steps + 1)) ** 1.5)
                pygame.draw.circle(glow, (*color, a), (outer, outer), r)
            # Hollow the middle: the component itself will cover it, and an
            # opaque core would wash out a pawn's own colour.
            pygame.draw.circle(glow, (*color, peak), (outer, outer), radius)
            if len(self._glow_cache) > 220:
                self._glow_cache.clear()
            self._glow_cache[key] = glow
        target.blit(glow, (int(centre[0]) - outer, int(centre[1]) - outer))

    def vertical_gradient(
        self,
        rect: pygame.Rect,
        top_color: Color,
        bottom_color: Color,
        surface: Optional[pygame.Surface] = None,
        steps: int = 0,
    ) -> None:
        target = self.target(surface)
        steps = steps or max(2, min(rect.height, 160))
        band = max(1, rect.height // steps)
        for i in range(steps):
            t = i / max(1, steps - 1)
            color = mix(top_color, bottom_color, t)
            y = rect.top + i * band
            pygame.draw.rect(target, color, pygame.Rect(rect.left, y, rect.width, band + 1))

    def soft_ellipse(
        self,
        center: Point,
        rx: float,
        ry: float,
        color: Color,
        alpha: int = 70,
        surface: Optional[pygame.Surface] = None,
    ) -> None:
        """Blurred blob — ground shadows and distant hills."""
        target = self.target(surface)
        rx, ry = max(1.0, rx), max(1.0, ry)
        size = (int(rx * 2) + 4, int(ry * 2) + 4)
        layer = pygame.Surface(size, pygame.SRCALPHA)
        rings = 5
        for i in range(rings, 0, -1):
            f = i / rings
            a = int(alpha * (1 - f) ** 1.2) + alpha // rings
            pygame.draw.ellipse(
                layer,
                (*color, min(255, a)),
                pygame.Rect(
                    (size[0] - rx * 2 * f) / 2,
                    (size[1] - ry * 2 * f) / 2,
                    rx * 2 * f,
                    ry * 2 * f,
                ),
            )
        target.blit(layer, (center[0] - size[0] / 2, center[1] - size[1] / 2))

    # ── colour helpers re-exported for convenience ───────────────────────────
    @staticmethod
    def darken(color: Color, factor: float = 0.62) -> Color:
        return darken(color, factor)

    @staticmethod
    def lighten(color: Color, amount: float = 0.35) -> Color:
        return lighten(color, amount)

    @staticmethod
    def mix(a: Color, b: Color, t: float) -> Color:
        return mix(a, b, t)

    # ── premium chrome ───────────────────────────────────────────────────────
    # Everything below draws the look defined by the concept art.  Panels are
    # built from layers rather than being a filled rectangle: an outer shadow, a
    # body with a vertical gradient, a hairline of light along the top inside
    # edge, a brass border, and small corner ornaments.  Five cheap steps, and
    # the difference between "a coloured box" and "a piece of furniture".

    def table_background(self, surface: Optional[pygame.Surface] = None) -> None:
        """The dark table everything sits on.

        A radial-ish wash (bright in the middle, deep at the corners) drawn once
        into a cached surface, because a per-frame gradient over a 4K window is
        pure waste.
        """
        target = self.target(surface)
        size = target.get_size()
        cached = self._background_cache.get(size)
        if cached is None:
            cached = pygame.Surface(size).convert()
            theme = self.theme
            width, height = size
            for y in range(height):
                t = abs((y / max(1, height - 1)) - 0.42) * 1.7
                cached.fill(
                    mix(theme.background_glow, theme.background_deep, min(1.0, t)),
                    pygame.Rect(0, y, width, 1),
                )
            # Corner vignette: four soft ellipses, no per-pixel maths.
            for corner_x, corner_y in ((0, 0), (width, 0), (0, height), (width, height)):
                self.soft_ellipse((corner_x, corner_y), width * 0.42, height * 0.55,
                                  theme.background_deep, alpha=120, surface=cached)
            if len(self._background_cache) > 4:
                self._background_cache.clear()
            self._background_cache[size] = cached
        target.blit(cached, (0, 0))

    def premium_panel(
        self,
        rect: pygame.Rect,
        surface: Optional[pygame.Surface] = None,
        *,
        radius: int = 12,
        fill: Optional[Color] = None,
        border: Optional[Color] = None,
        glow: Optional[Color] = None,
        glow_strength: float = 0.0,
        inset: bool = False,
        ornaments: bool = True,
        shadow: int = 14,
    ) -> None:
        """A panel in the game's visual language."""
        target = self.target(surface)
        theme = self.theme
        body = fill if fill is not None else (
            theme.panel_inset if inset else theme.panel_bg
        )
        edge = border if border is not None else theme.panel_line

        if glow and glow_strength > 0.01:
            # Under the shadow and under the body: what is left is a lit rim
            # tracing the panel.  This used to be a radial disc of 0.75 × the
            # panel's longest side, which on a wide tile covered everything
            # around it — the single worst offender of the old highlight system.
            self.shape_glow(rect, glow, target, radius=radius,
                            strength=glow_strength)
        if shadow:
            self.drop_shadow(rect, radius=radius, spread=shadow, alpha=150,
                             offset=(0, 5), surface=target)

        top = theme.panel_bg_light if not inset else theme.panel_bg
        self.rounded_gradient(target, rect, top, body, radius)

        # A hairline of light along the top inside edge reads as a bevel.
        highlight = pygame.Rect(rect.left + radius // 2, rect.top + 1,
                                rect.width - radius, 1)
        self._blend_line(target, highlight, theme.panel_highlight, 90)

        pygame.draw.rect(target, edge, rect, 1, border_radius=radius)
        if ornaments and rect.width > 60 and rect.height > 40:
            self._corner_ornaments(target, rect, edge, radius)

    def rounded_gradient(self, target: pygame.Surface, rect: pygame.Rect,
                         top: Color, bottom: Color, radius: int) -> None:
        """Fill a rounded rect with a vertical gradient, cached by shape."""
        key = (rect.width, rect.height, top, bottom, radius)
        cached = self._panel_cache.get(key)
        if cached is None:
            cached = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            for y in range(rect.height):
                t = y / max(1, rect.height - 1)
                pygame.draw.line(cached, mix(top, bottom, t ** 0.85),
                                 (0, y), (rect.width, y))
            mask = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255),
                             pygame.Rect(0, 0, rect.width, rect.height),
                             border_radius=radius)
            cached.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            if len(self._panel_cache) > 160:
                self._panel_cache.clear()
            self._panel_cache[key] = cached
        target.blit(cached, rect.topleft)

    @staticmethod
    def _blend_line(target: pygame.Surface, rect: pygame.Rect,
                    color: Color, alpha: int) -> None:
        if rect.width <= 0 or rect.height <= 0:
            return
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill((*color, alpha))
        target.blit(layer, rect.topleft)

    def _corner_ornaments(self, target: pygame.Surface, rect: pygame.Rect,
                          color: Color, radius: int) -> None:
        """Short brass brackets in each corner — the concept's frame detail."""
        length = max(8, min(20, rect.width // 12, rect.height // 6))
        inset = radius // 2 + 2
        for x_sign, y_sign, corner in (
            (1, 1, rect.topleft), (-1, 1, rect.topright),
            (1, -1, rect.bottomleft), (-1, -1, rect.bottomright),
        ):
            x = corner[0] + x_sign * inset
            y = corner[1] + y_sign * inset
            pygame.draw.line(target, color, (x, y), (x + x_sign * length, y), 1)
            pygame.draw.line(target, color, (x, y), (x, y + y_sign * length), 1)

    def spaced_text(
        self,
        text: str,
        font: pygame.font.Font,
        color: Color,
        surface: Optional[pygame.Surface] = None,
        *,
        center: Optional[Point] = None,
        midleft: Optional[Point] = None,
        midright: Optional[Point] = None,
        spacing: int = 2,
        shadow: bool = False,
    ) -> pygame.Rect:
        """Letter-spaced text, the way the concept sets its headings.

        pygame has no tracking, so the glyphs are placed one at a time.  Cached
        as a whole word, because doing that every frame for every heading would
        be a silly amount of blitting.
        """
        key = ("spaced", text, id(font), color, spacing)
        rendered = self._text_cache.get(key)
        if rendered is None:
            glyphs = [font.render(ch, True, color) for ch in text]
            width = sum(g.get_width() for g in glyphs) + spacing * max(0, len(glyphs) - 1)
            rendered = pygame.Surface((max(1, width), font.get_height()), pygame.SRCALPHA)
            x = 0
            for glyph in glyphs:
                rendered.blit(glyph, (x, 0))
                x += glyph.get_width() + spacing
            if len(self._text_cache) > 3000:
                self._text_cache.clear()
            self._text_cache[key] = rendered

        target = self.target(surface)
        rect = rendered.get_rect()
        if center is not None:
            rect.center = (int(center[0]), int(center[1]))
        elif midleft is not None:
            rect.midleft = (int(midleft[0]), int(midleft[1]))
        elif midright is not None:
            rect.midright = (int(midright[0]), int(midright[1]))
        if shadow:
            dark = rendered.copy()
            dark.fill((0, 0, 0, 160), special_flags=pygame.BLEND_RGBA_MULT)
            target.blit(dark, (rect.x + 1, rect.y + 1))
        target.blit(rendered, rect.topleft)
        return rect

    def section_heading(
        self,
        text: str,
        font: pygame.font.Font,
        centre_x: int,
        y: int,
        width: int,
        surface: Optional[pygame.Surface] = None,
        color: Optional[Color] = None,
    ) -> pygame.Rect:
        """An upper-case heading flanked by brass rules and diamonds.

        Used for every section title in the sidebars, which is what makes the
        two columns read as one design rather than two lists of words.
        """
        theme = self.theme
        colour = color or theme.text_heading
        label = self.spaced_text(text.upper(), font, colour, surface,
                                 center=(centre_x, y + font.get_height() // 2),
                                 spacing=max(1, font.get_height() // 9))
        target = self.target(surface)
        rule_colour = mix(theme.panel_line, theme.background, 0.15)
        gap = 10
        for direction in (-1, 1):
            start = label.centerx + direction * (label.width // 2 + gap)
            end = centre_x + direction * (width // 2)
            if abs(end - start) < 12:
                continue
            pygame.draw.line(target, rule_colour, (start, label.centery),
                             (end - direction * 8, label.centery), 1)
            self.diamond((end - direction * 4, label.centery), 3, rule_colour, target)
        return label

    def diamond(self, centre: Point, size: int, color: Color,
                surface: Optional[pygame.Surface] = None) -> None:
        target = self.target(surface)
        x, y = int(centre[0]), int(centre[1])
        pygame.draw.polygon(
            target, color,
            [(x, y - size), (x + size, y), (x, y + size), (x - size, y)],
        )

    def inset_well(
        self,
        rect: pygame.Rect,
        surface: Optional[pygame.Surface] = None,
        radius: int = 10,
        border: Optional[Color] = None,
    ) -> None:
        """A recessed area — empty card slots, notepads, board frames.

        Darker than the panel with a light hairline along the *bottom* edge,
        which is the opposite of the panel bevel and is what makes it read as a
        hollow rather than a lid.
        """
        target = self.target(surface)
        theme = self.theme
        pygame.draw.rect(target, theme.panel_inset, rect, border_radius=radius)
        self._blend_line(target, pygame.Rect(rect.left + radius // 2, rect.bottom - 2,
                                             rect.width - radius, 1),
                         theme.panel_highlight, 70)
        pygame.draw.rect(target, border or theme.panel_line, rect, 1,
                         border_radius=radius)

    # ── the one way to draw something the player can interact with ───────────
    def interactive_panel(
        self,
        rect: pygame.Rect,
        style: Emphasis,
        surface: Optional[pygame.Surface] = None,
        *,
        radius: int = 10,
        ornaments: bool = False,
    ) -> pygame.Rect:
        """Draw a control in the game's one interaction language.

        Takes an :class:`Emphasis` from ``render.highlight.emphasis`` — which
        already knows what hover, selection and pressed look like — so every
        button, tile, slot and row in the game reacts identically without any
        of them owning a copy of the rules.  Returns the rect the control was
        actually drawn at (hover lifts it, pressed drops it), so the caller can
        centre its label on the right pixels.

        USE THIS for anything that responds to the mouse.  ``premium_panel`` is
        for furniture that just sits there.
        """
        drawn = rect.move(0, style.offset)
        self.premium_panel(drawn, surface, radius=radius, fill=style.fill,
                           border=style.border, glow=style.glow,
                           glow_strength=style.glow_strength,
                           ornaments=ornaments, shadow=style.shadow)
        return drawn

    def emphasis(self, **kwargs) -> Emphasis:
        """``render.highlight.emphasis`` with this renderer's theme filled in."""
        return emphasis(self.theme, **kwargs)

    # ── text that fits the box it was given ──────────────────────────────────
    # Buttons are sized from their contents (``widgets.Button.fit``), but a
    # layout can still hand a control less room than its label wants — a long
    # Polish caption in the right-hand column, a stepper on a 1280-wide window.
    # These measure first and shrink the type rather than letting it run over
    # the border, so no label in the game can be clipped.

    def spaced_width(self, text: str, font: pygame.font.Font, spacing: int = 0) -> int:
        """Width ``spaced_text`` will need — glyph by glyph, as it draws it."""
        key = (id(font), text, spacing)
        cached = self._measure_cache.get(key)
        if cached is None:
            cached = (sum(font.size(ch)[0] for ch in text)
                      + spacing * max(0, len(text) - 1))
            if len(self._measure_cache) > 3000:
                self._measure_cache.clear()
            self._measure_cache[key] = cached
        return cached

    def fitted_font(
        self,
        text: str,
        max_width: int,
        base_size: int,
        *,
        bold: bool = True,
        spacing: int = 0,
        min_size: int = 9,
    ) -> pygame.font.Font:
        """The largest font no wider than ``max_width``, at most ``base_size``."""
        base_size = max(min_size, int(base_size))
        key = (text, int(max_width), base_size, bold, spacing, min_size,
               round(self.fonts.scale, 3))
        cached = self._fit_cache.get(key)
        if cached is None:
            size = base_size
            while size > min_size:
                candidate = self.fonts.get(size, bold=bold)
                if self.spaced_width(text, candidate, spacing) <= max_width:
                    break
                size -= 1
            cached = size
            if len(self._fit_cache) > 1500:
                self._fit_cache.clear()
            self._fit_cache[key] = cached
        return self.fonts.get(cached, bold=bold)

    def fit_spaced_text(
        self,
        text: str,
        rect: pygame.Rect,
        color: Color,
        surface: Optional[pygame.Surface] = None,
        *,
        base_size: Optional[int] = None,
        bold: bool = True,
        spacing: int = 2,
        padding: Optional[int] = None,
        min_size: int = 9,
        shadow: bool = False,
        height_ratio: float = 0.42,
    ) -> pygame.Rect:
        """Letter-spaced label centred in ``rect``, shrunk until it fits."""
        padding = padding if padding is not None else max(8, rect.width // 12)
        base = base_size if base_size is not None else max(
            min_size, int(rect.height * height_ratio / max(0.1, self.fonts.scale))
        )
        font = self.fitted_font(text, max(10, rect.width - 2 * padding), base,
                                bold=bold, spacing=spacing, min_size=min_size)
        return self.spaced_text(text, font, color, surface, center=rect.center,
                                spacing=spacing, shadow=shadow)

    def fit_text(
        self,
        text: str,
        rect: pygame.Rect,
        color: Color,
        surface: Optional[pygame.Surface] = None,
        *,
        base_size: Optional[int] = None,
        bold: bool = True,
        padding: Optional[int] = None,
        min_size: int = 9,
        shadow: bool = False,
        height_ratio: float = 0.44,
    ) -> pygame.Rect:
        """Plain label centred in ``rect``, shrunk until it fits."""
        padding = padding if padding is not None else max(8, rect.width // 14)
        base = base_size if base_size is not None else max(
            min_size, int(rect.height * height_ratio / max(0.1, self.fonts.scale))
        )
        font = self.fitted_font(text, max(10, rect.width - 2 * padding), base,
                                bold=bold, spacing=0, min_size=min_size)
        return self.text(text, font, color, surface, center=rect.center,
                         shadow=shadow)

    def circle_button(
        self,
        centre: Point,
        radius: int,
        surface: Optional[pygame.Surface] = None,
        *,
        fill: Optional[Color] = None,
        border: Optional[Color] = None,
        hover: float = 0.0,
        pressed: bool = False,
    ) -> None:
        """A round brass-rimmed button — zoom controls, the round counter."""
        from .highlight import HOVER_FILL, PRESSED_DARKEN

        target = self.target(surface)
        theme = self.theme
        body = fill or theme.btn_idle_bg
        if hover > 0.01:
            # A rim of light rather than a disc behind it: the round version of
            # the same hover every rectangle in the game uses.
            self.ring_glow(centre, radius, lighten(body, 0.5), target,
                           strength=0.55 * hover)
            body = lighten(body, HOVER_FILL * hover)
        if pressed:
            body = darken(body, PRESSED_DARKEN)
        offset = 1 if pressed else 0

        if not pressed:
            self.aa_circle((centre[0], centre[1] + 3), radius,
                           darken(theme.background_deep, 0.6), surface=target)
        self.aa_circle((centre[0], centre[1] + offset), radius, body,
                       border or theme.panel_edge, 2, target)
        # A crescent of light along the top: the same bevel as the panels.
        self.aa_ring((centre[0], centre[1] + offset - 1), radius - 2,
                     mix(body, theme.panel_highlight, 0.5), 1, target)






@dataclass

class HoverState:
    """Tracks what the mouse is over so effects can fade rather than snap."""

    key: Optional[str] = None
    strength: float = 0.0

    def update(self, key: Optional[str], dt: float, speed: float = 9.0) -> None:
        if key != self.key:
            self.key = key
            self.strength = 0.0
        target = 1.0 if key is not None else 0.0
        self.strength += (target - self.strength) * min(1.0, speed * dt)