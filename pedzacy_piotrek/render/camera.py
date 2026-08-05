"""
Camera.

The prototype mixed three jobs into ``Board``: what the board *is*, how it is
scrolled, and how it is drawn.  This is the middle one — a window onto world
space with zoom, panning, clamping, and smooth interpolation.

Screen and world coordinates are converted only here, so tokens, hover tests,
particles and drag-and-drop all agree by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pygame

from ..config import settings
from ..engine.animation import approach

Point = Tuple[float, float]


@dataclass
class Camera:
    viewport: pygame.Rect
    world_width: float
    world_height: float
    center: Point = (0.0, 0.0)
    zoom: float = settings.ZOOM_DEFAULT
    target_center: Point = (0.0, 0.0)
    target_zoom: float = settings.ZOOM_DEFAULT
    smoothing: float = settings.CAMERA_SMOOTHING

    def __post_init__(self) -> None:
        if self.center == (0.0, 0.0):
            self.center = (self.world_width / 2, self.world_height * 0.88)
            self.target_center = self.center
        self.target_zoom = self.zoom
        self.clamp()

    def set_viewport(self, rect: pygame.Rect) -> None:
        """Follow the window: the board area changes size when the user resizes.

        The zoom limits are derived from the viewport, so this also re-clamps —
        without it, shrinking the window would leave the camera looking at
        somewhere outside the board.
        """
        self.viewport = pygame.Rect(rect)
        self.clamp()

    # ── limits ───────────────────────────────────────────────────────────────
    @property
    def min_zoom(self) -> float:
        """Never zoom out so far that the board is a postage stamp, but do
        allow fitting the whole map in the viewport."""
        fit = min(
            self.viewport.width / max(1.0, self.world_width),
            self.viewport.height / max(1.0, self.world_height),
        )
        return max(settings.ZOOM_MIN, min(fit, settings.ZOOM_DEFAULT))

    @property
    def max_zoom(self) -> float:
        return settings.ZOOM_MAX

    @property
    def visible_size(self) -> Point:
        return (self.viewport.width / self.zoom, self.viewport.height / self.zoom)

    def clamp(self) -> None:
        self.target_zoom = max(self.min_zoom, min(self.max_zoom, self.target_zoom))
        self.zoom = max(self.min_zoom, min(self.max_zoom, self.zoom))
        vw, vh = self.viewport.width / self.target_zoom, self.viewport.height / self.target_zoom
        cx, cy = self.target_center
        if vw >= self.world_width:
            cx = self.world_width / 2
        else:
            cx = max(vw / 2, min(self.world_width - vw / 2, cx))
        if vh >= self.world_height:
            cy = self.world_height / 2
        else:
            cy = max(vh / 2, min(self.world_height - vh / 2, cy))
        self.target_center = (cx, cy)

    # ── movement ─────────────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        self.zoom = approach(self.zoom, self.target_zoom, self.smoothing, dt)
        self.center = (
            approach(self.center[0], self.target_center[0], self.smoothing, dt),
            approach(self.center[1], self.target_center[1], self.smoothing, dt),
        )

    def snap(self) -> None:
        """Finish any in-flight camera move immediately."""
        self.zoom = self.target_zoom
        self.center = self.target_center

    def pan_screen(self, dx: float, dy: float, immediate: bool = False) -> None:
        """Pan by a screen-space delta (mouse drag, scroll wheel).

        ``immediate`` skips the smoothing. Dragging the map wants it: with
        smoothing the board lags a few pixels behind the cursor and the grab
        feels loose, whereas a wheel scroll wants exactly the opposite.
        """
        self.target_center = (
            self.target_center[0] - dx / self.zoom,
            self.target_center[1] - dy / self.zoom,
        )
        self.clamp()
        if immediate:
            self.center = self.target_center

    def move_to(self, world_point: Point, immediate: bool = False) -> None:
        self.target_center = world_point
        self.clamp()
        if immediate:
            self.snap()

    def set_zoom(self, zoom: float, anchor: Optional[Point] = None) -> None:
        """Set zoom, optionally keeping ``anchor`` (a screen point) fixed."""
        old_zoom = self.target_zoom
        new_zoom = max(self.min_zoom, min(self.max_zoom, zoom))
        if anchor is not None and old_zoom > 0:
            world_anchor = self.screen_to_world(anchor)
            self.target_zoom = new_zoom
            self.clamp()
            new_world_anchor = self.screen_to_world(anchor, use_target=True)
            self.target_center = (
                self.target_center[0] + (world_anchor[0] - new_world_anchor[0]),
                self.target_center[1] + (world_anchor[1] - new_world_anchor[1]),
            )
        else:
            self.target_zoom = new_zoom
        self.clamp()

    def zoom_by(self, factor: float, anchor: Optional[Point] = None) -> None:
        self.set_zoom(self.target_zoom * factor, anchor)

    @property
    def zoom_fraction(self) -> float:
        span = self.max_zoom - self.min_zoom
        return 0.0 if span <= 0 else (self.target_zoom - self.min_zoom) / span

    def set_zoom_fraction(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        self.set_zoom(self.min_zoom + fraction * (self.max_zoom - self.min_zoom))

    # ── transforms ───────────────────────────────────────────────────────────
    def world_to_screen(self, point: Point) -> Tuple[int, int]:
        cx, cy = self.center
        return (
            int(round(self.viewport.centerx + (point[0] - cx) * self.zoom)),
            int(round(self.viewport.centery + (point[1] - cy) * self.zoom)),
        )

    def screen_to_world(self, point: Point, use_target: bool = False) -> Point:
        zoom = self.target_zoom if use_target else self.zoom
        cx, cy = self.target_center if use_target else self.center
        return (
            cx + (point[0] - self.viewport.centerx) / zoom,
            cy + (point[1] - self.viewport.centery) / zoom,
        )

    def scale(self, value: float) -> float:
        return value * self.zoom

    def visible_world_rect(self) -> pygame.Rect:
        vw, vh = self.visible_size
        return pygame.Rect(
            int(self.center[0] - vw / 2),
            int(self.center[1] - vh / 2),
            int(vw) + 1,
            int(vh) + 1,
        )

    def contains_screen(self, point: Point) -> bool:
        return self.viewport.collidepoint(point)

    # ── scrollbar helpers ────────────────────────────────────────────────────
    def scroll_fraction(self) -> Point:
        vw, vh = self.visible_size
        span_x = max(0.0, self.world_width - vw)
        span_y = max(0.0, self.world_height - vh)
        fx = 0.0 if span_x <= 0 else (self.center[0] - vw / 2) / span_x
        fy = 0.0 if span_y <= 0 else (self.center[1] - vh / 2) / span_y
        return (max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy)))

    def set_scroll_fraction(self, fx: Optional[float] = None, fy: Optional[float] = None) -> None:
        vw, vh = self.visible_size
        cx, cy = self.target_center
        if fx is not None:
            span_x = max(0.0, self.world_width - vw)
            cx = vw / 2 + max(0.0, min(1.0, fx)) * span_x
        if fy is not None:
            span_y = max(0.0, self.world_height - vh)
            cy = vh / 2 + max(0.0, min(1.0, fy)) * span_y
        self.target_center = (cx, cy)
        self.clamp()

    def needs_h_scroll(self) -> bool:
        return self.visible_size[0] < self.world_width - 1

    def needs_v_scroll(self) -> bool:
        return self.visible_size[1] < self.world_height - 1

    def view_ratio(self) -> Point:
        vw, vh = self.visible_size
        return (
            max(0.05, min(1.0, vw / max(1.0, self.world_width))),
            max(0.05, min(1.0, vh / max(1.0, self.world_height))),
        )
