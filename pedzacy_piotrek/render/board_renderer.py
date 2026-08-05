"""
Board rendering.

Strategy: the terrain never changes, so it is painted **once** into a large
world-sized surface — gradient, hills, rivers, the road ribbon, bridges, the
fields, and every tree, rock and hut.  Each frame only blits the slice the
camera can see, scaled to the viewport.  Cost per frame is therefore
proportional to the size of the *window*, not to the size of the board, which
is what keeps a 100-field map at 60 FPS.

Dynamic things — pawns, hover rings, snap previews, particles — are drawn on
top in screen space every frame.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence, Tuple

import pygame

from ..board.board import BoardModel
from ..board.tiles import Prop, PropKind, River, Tile, TileKind
from ..config import settings
from ..config.theme import Theme, darken, lighten, mix
from .camera import Camera
from .renderer import Renderer

Color = Tuple[int, int, int]
Point = Tuple[float, float]


class BoardRenderer:
    """Paints a :class:`BoardModel`."""

    def __init__(self, renderer: Renderer, board: BoardModel) -> None:
        self.r = renderer
        self.theme: Theme = renderer.theme
        self.board = board
        self.surface: Optional[pygame.Surface] = None
        self._number_font: Optional[pygame.font.Font] = None
        self._small_number_font: Optional[pygame.font.Font] = None
        self._label_font: Optional[pygame.font.Font] = None
        self._backdrop: Optional[pygame.Surface] = None
        self._pulse = 0.0

    # ── static terrain ───────────────────────────────────────────────────────
    def build(self) -> pygame.Surface:
        """Paint the whole world once.  Call after ``pygame.display.set_mode``."""
        board = self.board
        self._number_font = self.r.fonts.get(19, bold=True)
        # "12a" needs a little less room than "12".
        self._small_number_font = self.r.fonts.get(16, bold=True)
        self._label_font = self.r.fonts.get(22, bold=True)

        surface = pygame.Surface((board.width, board.height)).convert()
        self._paint_ground(surface)
        self._paint_hills(surface)
        for river in board.rivers:
            self._paint_river(surface, river)
        self._paint_road(surface)
        for river in board.rivers:
            self._paint_bridge(surface, river)
        self._paint_camp(surface)
        self._paint_tiles(surface)
        self._paint_props(surface)
        self.surface = surface
        return surface

    def _paint_ground(self, surface: pygame.Surface) -> None:
        """Vertical gradient: cooler and hazier towards the top (distance)."""
        rect = surface.get_rect()
        self.r.vertical_gradient(rect, self.theme.terrain_far, self.theme.terrain_near,
                                 surface=surface, steps=90)
        # Subtle mottling so large empty stretches are not flat colour.
        rng = random.Random(self.board.seed ^ 0xA11CE)
        for _ in range(int(rect.width * rect.height / 5200)):
            x = rng.uniform(0, rect.width)
            y = rng.uniform(0, rect.height)
            r = rng.uniform(24, 90)
            tint = mix(self.theme.terrain_near, self.theme.hill_light, rng.uniform(0.1, 0.5))
            self.r.soft_ellipse((x, y), r, r * 0.55, tint, alpha=26, surface=surface)

    def _paint_hills(self, surface: pygame.Surface) -> None:
        for prop in self.board.props:
            if prop.kind is not PropKind.HILL:
                continue
            base = self.theme.hill_dark if prop.variant == 0 else self.theme.hill_light
            color = mix(base, self.theme.terrain_far, 1.0 - prop.depth * 0.8)
            self.r.soft_ellipse(
                prop.position, 150 * prop.scale, 62 * prop.scale, color,
                alpha=48, surface=surface,
            )

    def _paint_river(self, surface: pygame.Surface, river: River) -> None:
        width = river.width
        # Banks first, then water, then a highlight ripple.
        self._thick_polyline(surface, river.points, width + 14, darken(self.theme.water, 0.55))
        self._thick_polyline(surface, river.points, width, self.theme.water)
        self._thick_polyline(
            surface,
            [(x, y - width * 0.18) for (x, y) in river.points],
            max(2.0, width * 0.16),
            self.theme.water_light,
        )

    def _paint_road(self, surface: pygame.Surface) -> None:
        path = self.board.path
        if path is None:
            return
        points = path.resample(9.0)
        width = self.board.layout.road_width
        # Shadow, verge, then the road surface itself.
        self._thick_polyline(surface, [(x, y + 5) for x, y in points], width + 10,
                             self.theme.road_shadow)
        self._thick_polyline(surface, points, width + 8, self.theme.road_edge)
        self._thick_polyline(surface, points, width, self.theme.road_fill)
        self._thick_polyline(surface, points, width * 0.62,
                             lighten(self.theme.road_fill, 0.12))

        # Wheel ruts and gravel — cheap texture that reads well when zoomed in.
        rng = random.Random(self.board.seed ^ 0xBEEF)
        for i in range(0, len(points) - 1, 3):
            px, py = points[i]
            nx, ny = path.normal_at(i * 9.0)
            for side in (-1, 1):
                offset = side * width * rng.uniform(0.18, 0.26)
                self.r.aa_circle(
                    (px + nx * offset, py + ny * offset),
                    max(1, int(rng.uniform(1.5, 3.0))),
                    darken(self.theme.road_fill, 0.86),
                    surface=surface,
                )
        for _ in range(int(path.length / 26)):
            d = rng.uniform(0, path.length)
            lateral = rng.uniform(-width * 0.46, width * 0.46)
            p = path.offset_point(d, lateral)
            shade = rng.choice((0.78, 0.9, 1.12))
            self.r.aa_circle(p, max(1, int(rng.uniform(1.0, 2.4))),
                             darken(self.theme.road_fill, shade), surface=surface)

    def _paint_bridge(self, surface: pygame.Surface, river: River) -> None:
        if river.bridge_point is None:
            return
        cx, cy = river.bridge_point
        angle = math.radians(-river.bridge_angle)
        length = river.width + 58
        width = self.board.layout.road_width + 18
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux

        def corner(along: float, across: float) -> Point:
            return (cx + ux * along + nx * across, cy + uy * along + ny * across)

        deck = [
            corner(-length / 2, -width / 2),
            corner(length / 2, -width / 2),
            corner(length / 2, width / 2),
            corner(-length / 2, width / 2),
        ]
        self.r.aa_polygon([(p[0], p[1] + 4) for p in deck], self.theme.road_shadow,
                          surface=surface)
        self.r.aa_polygon(deck, self.theme.bridge, surface=surface)

        # Planks across the deck, then rails along both sides.
        planks = max(4, int(length / 13))
        for i in range(planks + 1):
            t = -length / 2 + i * (length / planks)
            self.r.aa_line(corner(t, -width / 2), corner(t, width / 2),
                           self.theme.bridge_dark, 1, surface)
        for side in (-1, 1):
            a = corner(-length / 2, side * width / 2)
            b = corner(length / 2, side * width / 2)
            self.r.aa_line(a, b, self.theme.bridge_dark, 5, surface)
            self.r.aa_line((a[0], a[1] - 5), (b[0], b[1] - 5),
                           lighten(self.theme.bridge, 0.25), 3, surface)

    def _paint_camp(self, surface: pygame.Surface) -> None:
        slots = self.board.camp_slots
        if not slots:
            return
        xs = [s.position[0] for s in slots]
        ys = [s.position[1] for s in slots]
        pad = 46
        rect = pygame.Rect(
            int(min(xs) - pad), int(min(ys) - pad - 16),
            int(max(xs) - min(xs) + pad * 2), int(max(ys) - min(ys) + pad * 2),
        )
        self.r.panel(rect, self.theme.camp_bg, self.theme.camp_border, radius=26,
                     border_width=3, surface=surface)
        if self._label_font is not None:
            self.r.text("START", self._label_font, self.theme.camp_label, surface,
                        midtop=(rect.centerx, rect.top + 6), shadow=True)
        for slot in slots:
            self.r.aa_circle(slot.position, 22, darken(self.theme.camp_bg, 0.82),
                             self.theme.camp_border, 2, surface)

    def _paint_tiles(self, surface: pygame.Surface) -> None:
        radius = int(self.board.layout.tile_radius)
        for tile in self.board.tiles:
            base = self.theme.tile_light if tile.number % 2 else self.theme.tile_dark
            if tile.kind is TileKind.BRIDGE:
                base = mix(base, self.theme.bridge, 0.35)
            elif tile.kind is TileKind.CROSSROAD:
                base = mix(base, self.theme.road_fill, 0.25)

            self.r.aa_circle((tile.position[0], tile.position[1] + 3), radius,
                             self.theme.road_shadow, surface=surface)
            self.r.aa_circle(tile.position, radius, base, self.theme.tile_border, 3, surface)
            self.r.aa_ring((tile.position[0], tile.position[1] - 1), radius - 5,
                           lighten(base, 0.35), 2, surface)

            if tile.kind is TileKind.FINISH:
                self._paint_finish(surface, tile, radius)
            elif tile.kind is TileKind.START:
                self.r.aa_ring(tile.position, radius + 5, (120, 200, 120), 3, surface)

            if settings.SHOW_TILE_NUMBERS and self._number_font is not None:
                # Two fields of a widened stretch share a number and are told
                # apart by their letter: 12a / 12b.
                font = self._number_font if not tile.is_doubled else self._small_number_font
                self.r.text(tile.label, font or self._number_font, self.theme.tile_label,
                            surface, center=tile.position)

    def _paint_finish(self, surface: pygame.Surface, tile: Tile, radius: int) -> None:
        """A chequered rim plus a small banner so the meta reads at a glance."""
        segments = 16
        for i in range(segments):
            a0 = math.tau * i / segments
            a1 = math.tau * (i + 1) / segments
            color = self.theme.finish_a if i % 2 == 0 else self.theme.finish_b
            outer, inner = radius + 8, radius + 1
            pts = []
            steps = 4
            for s in range(steps + 1):
                a = a0 + (a1 - a0) * s / steps
                pts.append((tile.position[0] + math.cos(a) * outer,
                            tile.position[1] + math.sin(a) * outer))
            for s in range(steps, -1, -1):
                a = a0 + (a1 - a0) * s / steps
                pts.append((tile.position[0] + math.cos(a) * inner,
                            tile.position[1] + math.sin(a) * inner))
            self.r.aa_polygon(pts, color, surface=surface)
        if self._label_font is not None:
            banner = pygame.Rect(0, 0, 132, 34)
            banner.center = (int(tile.position[0]), int(tile.position[1] - radius - 40))
            self.r.panel(banner, (44, 44, 48), (235, 235, 230), radius=8, border_width=2,
                         surface=surface)
            self.r.text("META", self._label_font, (245, 245, 240), surface,
                        center=banner.center)

    def _paint_props(self, surface: pygame.Surface) -> None:
        for prop in self.board.props:
            if prop.kind is PropKind.HILL:
                continue
            painter = self._PROP_PAINTERS.get(prop.kind)
            if painter is not None:
                painter(self, surface, prop)

    # ── individual props ─────────────────────────────────────────────────────
    def _paint_tree(self, surface: pygame.Surface, prop: Prop) -> None:
        x, y = prop.position
        s = prop.scale
        haze = prop.depth  # 0 at the top of the map (far), 1 at the bottom (near)
        leaf = mix(self.theme.tree_leaf, self.theme.terrain_far, (1.0 - haze) * 0.45)
        leaf_hi = mix(self.theme.tree_leaf_light, leaf, 0.35)

        self.r.soft_ellipse((x + 4 * s, y + 4 * s), 16 * s, 7 * s, (18, 30, 22),
                            alpha=70, surface=surface)
        trunk_w = max(2, int(4 * s))
        pygame.draw.rect(surface, self.theme.tree_trunk,
                         pygame.Rect(int(x - trunk_w / 2), int(y - 10 * s),
                                     trunk_w, int(14 * s)))
        if prop.variant == 0:      # round broadleaf
            self.r.aa_circle((x, y - 22 * s), int(15 * s), leaf, surface=surface)
            self.r.aa_circle((x - 9 * s, y - 14 * s), int(11 * s), leaf, surface=surface)
            self.r.aa_circle((x + 9 * s, y - 15 * s), int(11 * s), leaf, surface=surface)
            self.r.aa_circle((x - 3 * s, y - 27 * s), int(8 * s), leaf_hi, surface=surface)
        elif prop.variant == 1:    # conifer
            for i, k in enumerate((1.0, 0.78, 0.54)):
                top = y - (16 + i * 11) * s
                half = 15 * s * k
                self.r.aa_triangle(
                    (x, top - 14 * s), (x - half, top + 6 * s), (x + half, top + 6 * s),
                    leaf if i else darken(leaf, 0.9), surface,
                )
        else:                      # bushy scrub
            for dx, dy, r in ((-7, -12, 9), (7, -13, 9), (0, -19, 11)):
                self.r.aa_circle((x + dx * s, y + dy * s), int(r * s), leaf, surface=surface)
            self.r.aa_circle((x + 3 * s, y - 22 * s), int(5 * s), leaf_hi, surface=surface)

    def _paint_rock(self, surface: pygame.Surface, prop: Prop) -> None:
        x, y = prop.position
        s = prop.scale
        self.r.soft_ellipse((x + 3 * s, y + 4 * s), 14 * s, 6 * s, (18, 30, 22),
                            alpha=60, surface=surface)
        facets = [
            [(-14, 4), (-8, -10), (2, -12), (8, -2), (4, 6)],
            [(-11, 5), (-4, -8), (6, -9), (11, 3)],
            [(-16, 5), (-9, -6), (0, -13), (9, -7), (14, 5)],
        ][prop.variant % 3]
        pts = [(x + dx * s, y + dy * s) for dx, dy in facets]
        self.r.aa_polygon(pts, self.theme.rock, surface=surface)
        top = [(x + dx * s, y + dy * s - 2 * s) for dx, dy in facets[:3]]
        self.r.aa_polygon(top + [pts[0]], lighten(self.theme.rock, 0.22), surface=surface)
        self.r.aa_line(pts[0], pts[-1], self.theme.rock_dark, 1, surface)

    def _paint_hut(self, surface: pygame.Surface, prop: Prop) -> None:
        x, y = prop.position
        s = prop.scale
        w, h = 34 * s, 24 * s
        self.r.soft_ellipse((x + 4 * s, y + 6 * s), w * 0.7, 8 * s, (18, 30, 22),
                            alpha=70, surface=surface)
        body = pygame.Rect(int(x - w / 2), int(y - h), int(w), int(h))
        pygame.draw.rect(surface, self.theme.village_wall, body)
        pygame.draw.rect(surface, darken(self.theme.village_wall, 0.7), body, max(1, int(s)))
        roof = [(x - w / 2 - 5 * s, y - h), (x, y - h - 20 * s), (x + w / 2 + 5 * s, y - h)]
        self.r.aa_polygon(roof, self.theme.village_roof, surface=surface)
        self.r.aa_polygon(
            [(roof[0][0], roof[0][1]), (roof[1][0], roof[1][1]), (roof[1][0], roof[1][1] + 4 * s)],
            darken(self.theme.village_roof, 0.8), surface=surface,
        )
        door = pygame.Rect(int(x - 4 * s), int(y - 12 * s), int(8 * s), int(12 * s))
        pygame.draw.rect(surface, darken(self.theme.village_wall, 0.45), door)
        if prop.variant == 2:  # a hut with a window
            win = pygame.Rect(int(x + 7 * s), int(y - 19 * s), int(7 * s), int(6 * s))
            pygame.draw.rect(surface, (110, 150, 170), win)

    def _paint_grass(self, surface: pygame.Surface, prop: Prop) -> None:
        x, y = prop.position
        s = prop.scale
        color = mix(self.theme.grass_tuft, self.theme.terrain_near, 0.25)
        for dx, dy in ((-4, -8), (0, -11), (4, -7)):
            self.r.aa_line((x, y), (x + dx * s, y + dy * s), color, max(1, int(2 * s)), surface)

    _PROP_PAINTERS = {
        PropKind.TREE: _paint_tree,
        PropKind.ROCK: _paint_rock,
        PropKind.HUT: _paint_hut,
        PropKind.GRASS: _paint_grass,
    }

    # ── per-frame drawing ────────────────────────────────────────────────────
    def _build_backdrop(self) -> pygame.Surface:
        """A tile of generic countryside for the land *around* the board.

        Without it, zooming out left the board floating on flat panel colour,
        which made it read as a picture of a map rather than a place. This is
        deliberately low-contrast: it must never compete with the board.
        """
        size = 256
        surface = pygame.Surface((size, size)).convert()
        surface.fill(self.theme.terrain_far)
        rng = random.Random((self.board.seed ^ 0x0DDBA11) & 0xFFFFFFFF)
        for _ in range(26):
            x, y = rng.uniform(0, size), rng.uniform(0, size)
            radius = rng.uniform(28, 96)
            tint = mix(self.theme.terrain_far, self.theme.hill_dark, rng.uniform(0.1, 0.55))
            # Draw each blob four times so the tile wraps seamlessly.
            for dx in (-size, 0, size):
                for dy in (-size, 0, size):
                    if abs(dx) + abs(dy) > size:
                        continue
                    self.r.soft_ellipse((x + dx, y + dy), radius, radius * 0.6, tint,
                                        alpha=30, surface=surface)
        for _ in range(90):
            x, y = rng.uniform(0, size), rng.uniform(0, size)
            self.r.aa_circle((x, y), rng.uniform(1.0, 2.4),
                             mix(self.theme.terrain_far, self.theme.hill_light, 0.5),
                             surface=surface)
        return surface

    def _draw_backdrop(self, target: pygame.Surface, camera: Camera) -> None:
        """Tile the surrounding countryside across the whole viewport.

        The tiling scrolls at a fraction of the camera speed, so the land
        outside the board drifts like distant scenery instead of sticking to
        the screen.
        """
        if self._backdrop is None:
            self._backdrop = self._build_backdrop()
        backdrop = self._backdrop
        viewport = camera.viewport
        tile_w, tile_h = backdrop.get_size()

        parallax = 0.45
        offset_x = int(-camera.center[0] * camera.zoom * parallax) % tile_w
        offset_y = int(-camera.center[1] * camera.zoom * parallax) % tile_h

        old_clip = target.get_clip()
        target.set_clip(viewport)
        start_x = viewport.left + offset_x - tile_w
        start_y = viewport.top + offset_y - tile_h
        for x in range(start_x, viewport.right + tile_w, tile_w):
            for y in range(start_y, viewport.bottom + tile_h, tile_h):
                target.blit(backdrop, (x, y))
        target.set_clip(old_clip)

    def draw(self, target: pygame.Surface, camera: Camera, dt: float = 0.0) -> None:
        """Blit the visible slice of the world, scaled to the camera's zoom."""
        if self.surface is None:
            self.build()
        assert self.surface is not None
        self._pulse = (self._pulse + dt * 2.2) % math.tau

        viewport = camera.viewport
        self._draw_backdrop(target, camera)

        world_rect = camera.visible_world_rect()
        clipped = world_rect.clip(self.surface.get_rect())
        if clipped.width <= 0 or clipped.height <= 0:
            return

        dest_w = max(1, int(round(clipped.width * camera.zoom)))
        dest_h = max(1, int(round(clipped.height * camera.zoom)))
        slice_surface = self.surface.subsurface(clipped)
        scaled = (
            pygame.transform.smoothscale(slice_surface, (dest_w, dest_h))
            if camera.zoom < 1.0
            else pygame.transform.scale(slice_surface, (dest_w, dest_h))
        )
        top_left = camera.world_to_screen((clipped.x, clipped.y))

        old_clip = target.get_clip()
        target.set_clip(viewport)
        # A soft edge where the board meets the surrounding land, so the map
        # sits *in* the world rather than on top of it.
        board_rect = pygame.Rect(top_left[0], top_left[1], dest_w, dest_h)
        if (board_rect.width < viewport.width - 2
                or board_rect.height < viewport.height - 2):
            self.r.drop_shadow(board_rect, radius=6, spread=18, alpha=120,
                               offset=(0, 4), surface=target)
        target.blit(scaled, top_left)
        target.set_clip(old_clip)

    def draw_tile_highlight(
        self, target: pygame.Surface, camera: Camera, tile: Tile, color: Color,
        strength: float = 1.0,
    ) -> None:
        centre = camera.world_to_screen(tile.position)
        if not camera.viewport.collidepoint(centre):
            return
        radius = int(self.board.layout.tile_radius * camera.zoom)
        pulse = 1.0 + 0.08 * math.sin(self._pulse)
        old_clip = target.get_clip()
        target.set_clip(camera.viewport)
        self.r.ring_glow(centre, int(radius * pulse), color, target,
                         strength=strength)
        self.r.aa_ring(centre, int(radius * pulse) + 3, color, 3, target)
        target.set_clip(old_clip)

    def draw_tokens(
        self,
        target: pygame.Surface,
        camera: Camera,
        tokens: Sequence[Tuple[str, Point, Color, bool]],
        highlight: Optional[str] = None,
    ) -> None:
        """Draw pawns.

        ``tokens`` is ``(pawn_id, world_position, colour, is_held)`` — the caller
        supplies *visual* positions, which may be mid-animation and differ from
        the authoritative ones in the game state.
        """
        old_clip = target.get_clip()
        target.set_clip(camera.viewport)
        radius = max(6, int(15 * camera.zoom))

        for pawn_id, world, color, held in tokens:
            sx, sy = camera.world_to_screen(world)
            if not camera.viewport.inflate(80, 80).collidepoint(sx, sy):
                continue
            lift = int(6 * camera.zoom) if held else 0

            # Ground shadow stays put while a held pawn lifts away from it.
            self.r.soft_ellipse((sx + 2, sy + radius * 0.55 + lift * 0.4),
                                radius * (1.05 + lift * 0.02), radius * 0.42,
                                (10, 18, 12), alpha=110, surface=target)
            if held or highlight == pawn_id:
                self.r.ring_glow((sx, sy - lift), radius, lighten(color, 0.4),
                                 target, strength=0.8)

            cy = sy - lift
            self.r.aa_circle((sx, cy), radius + 2, darken(color, 0.45), surface=target)
            self.r.aa_circle((sx, cy), radius, color, surface=target)
            # Glossy top edge — cheap fake lighting, reads as a rounded piece.
            self.r.aa_circle((sx - radius * 0.28, cy - radius * 0.34),
                             max(2, int(radius * 0.42)), lighten(color, 0.5), surface=target)
            ring = self.theme.token_ring_drag if held else self.theme.token_ring
            self.r.aa_ring((sx, cy), radius, ring, 2, target)

        target.set_clip(old_clip)

    def draw_stack_badges(
        self, target: pygame.Surface, camera: Camera, board: BoardModel,
    ) -> None:
        """Show ``xN`` next to a tower so a tall stack is readable when zoomed out."""
        font = self.r.fonts.get(max(11, int(13 * camera.zoom)), bold=True)
        old_clip = target.get_clip()
        target.set_clip(camera.viewport)
        for tile in board.tiles:
            if len(tile.stack) < 2:
                continue
            top = board.stack_position(tile, len(tile.stack) - 1)
            sx, sy = camera.world_to_screen((top[0] + 24, top[1] - 14))
            if not camera.viewport.collidepoint(sx, sy):
                continue
            label = f"x{len(tile.stack)}"
            size = font.size(label)
            rect = pygame.Rect(0, 0, size[0] + 10, size[1] + 4)
            rect.center = (sx, sy)
            self.r.panel(rect, (28, 34, 30), (200, 220, 180), radius=7, border_width=1,
                         surface=target)
            self.r.text(label, font, (225, 235, 200), target, center=rect.center)
        target.set_clip(old_clip)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _thick_polyline(
        self, surface: pygame.Surface, points: Sequence[Point], width: float, color: Color
    ) -> None:
        """A wide smooth ribbon: quads between samples, discs at the joints."""
        if len(points) < 2:
            return
        half = max(0.5, width / 2)
        for a, b in zip(points, points[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length < 0.01:
                continue
            nx, ny = -dy / length * half, dx / length * half
            pygame.draw.polygon(
                surface,
                color,
                [
                    (a[0] + nx, a[1] + ny),
                    (b[0] + nx, b[1] + ny),
                    (b[0] - nx, b[1] - ny),
                    (a[0] - nx, a[1] - ny),
                ],
            )
        step = max(1, len(points) // 400)
        for p in points[::step]:
            pygame.draw.circle(surface, color, (int(p[0]), int(p[1])), int(half))
        pygame.draw.circle(surface, color, (int(points[-1][0]), int(points[-1][1])), int(half))
