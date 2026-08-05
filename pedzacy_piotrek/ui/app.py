"""
Application shell.

Owns the window, the main loop and a stack of screens (menu → game, and later
menu → lobby → game).  Screens are pushed and popped rather than being nested
``while`` loops, which is what let the prototype's menu and game share nothing.

This stage changed how the window works.  The previous version drew onto a
fixed 2273×969 canvas and scaled that image to the window, which kept the
proportions but made the game a blurry enlargement on a big monitor and gave
the board no more room at 2560×1440 than at 1280×720.  Now the game draws at
the window's real resolution and the :class:`Layout` recomputes on every
resize, so a bigger screen means a bigger board rather than bigger pixels.

Screens can implement ``on_resize`` to drop anything they cached at the old
size — cached card faces, mostly.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pygame

from ..config import settings
from ..config.theme import THEME, FontBook
from ..render.renderer import Renderer
from .layout import Layout


class Screen:
    """Base class for a full-screen state."""

    def __init__(self, app: "App") -> None:
        self.app = app

    # Lifecycle hooks — all optional.
    def on_enter(self) -> None:
        pass

    def on_exit(self) -> None:
        pass

    def on_resize(self) -> None:
        pass

    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        pass

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        pass

    def draw(self, surface: pygame.Surface) -> None:
        pass


class App:
    def __init__(
        self,
        layout: Optional[Layout] = None,
        *,
        headless: bool = False,
        size: Optional[Tuple[int, int]] = None,
        fullscreen: bool = False,
    ) -> None:
        self.headless = headless
        pygame.init()
        pygame.display.set_caption(settings.APP_TITLE)

        window_size = size or self._preferred_size()
        flags = pygame.RESIZABLE
        if fullscreen and not headless:
            flags |= pygame.FULLSCREEN
        self.display = pygame.display.set_mode(window_size, flags)
        #: Everything draws straight onto the window — no intermediate canvas.
        self.canvas = self.display

        self.layout = layout or Layout()
        self.layout.resize(*self.display.get_size())

        self.clock = pygame.time.Clock()
        self.fonts = FontBook()
        self.renderer = Renderer(THEME, self.fonts)
        self._apply_font_scale()
        self.screens: List[Screen] = []
        self.running = True

    # ── window ───────────────────────────────────────────────────────────────
    def _preferred_size(self) -> Tuple[int, int]:
        """Open as large as the desktop comfortably allows."""
        if self.headless:
            return settings.PREFERRED_WINDOW
        try:
            info = pygame.display.Info()
            available = (info.current_w, info.current_h - 70)
        except pygame.error:  # pragma: no cover - no display
            return settings.PREFERRED_WINDOW
        return (
            max(settings.MIN_WINDOW[0], min(available[0], settings.PREFERRED_WINDOW[0] * 2)),
            max(settings.MIN_WINDOW[1], min(available[1], settings.PREFERRED_WINDOW[1] * 2)),
        )

    def resize(self, size: Tuple[int, int]) -> None:
        width = max(settings.MIN_WINDOW[0], size[0])
        height = max(settings.MIN_WINDOW[1], size[1])
        self.display = pygame.display.set_mode((width, height), pygame.RESIZABLE)
        self.canvas = self.display
        self.layout.resize(width, height)
        self._apply_font_scale()
        for screen in self.screens:
            screen.on_resize()

    def _apply_font_scale(self) -> None:
        """Type grows with the display instead of staying 1080p-sized.

        Text is then *rendered* at the larger size, so a 1440p screen gets
        sharper text rather than the same bitmap stretched.
        """
        self.fonts.set_scale(self.layout.ui_scale)
        self.renderer.clear_caches()

    def mouse(self) -> Tuple[int, int]:
        return pygame.mouse.get_pos()

    def to_design(self, position: Tuple[int, int]) -> Tuple[int, int]:
        """Kept for callers written against the old scaled canvas.

        Window coordinates *are* layout coordinates now, so this is the
        identity — but keeping the name means screens and tests did not all
        have to change on the same day.
        """
        return position

    # ── screen stack ─────────────────────────────────────────────────────────
    @property
    def screen(self) -> Optional[Screen]:
        return self.screens[-1] if self.screens else None

    def push(self, screen: Screen) -> None:
        self.screens.append(screen)
        screen.on_enter()

    def pop(self) -> None:
        if self.screens:
            self.screens.pop().on_exit()
        if not self.screens:
            self.running = False

    def replace(self, screen: Screen) -> None:
        while self.screens:
            self.screens.pop().on_exit()
        self.push(screen)

    def quit(self) -> None:
        self.running = False

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self, max_frames: Optional[int] = None) -> None:
        frames = 0
        while self.running and self.screens:
            dt = min(self.clock.tick(settings.FPS) / 1000.0, 0.1)
            mouse = self.mouse()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break
                if event.type == pygame.VIDEORESIZE:
                    self.resize((event.w, event.h))
                    continue
                screen = self.screen
                if screen is not None:
                    screen.handle_event(event, mouse)

            screen = self.screen
            if screen is None or not self.running:
                break

            screen.update(dt, mouse)

            self.renderer.begin(self.canvas)
            self.renderer.table_background(self.canvas)
            screen.draw(self.canvas)
            pygame.display.flip()

            frames += 1
            if max_frames is not None and frames >= max_frames:
                break

        pygame.quit()
