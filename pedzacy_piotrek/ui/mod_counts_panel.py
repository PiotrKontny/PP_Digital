"""
The Mody Patusa deck composition panel.

One overlay, opened by a button, used by BOTH settings screens: the single
machine menu and the network host screen.  Written once because the two screens
have to offer the same deck — a host who sets three Speedruns and a hot-seat
game that cannot say so would be two different games with one rule book.

WHY AN OVERLAY AND NOT EIGHT MORE ROWS.  Both screens lay themselves out by
measuring their rows and shrinking the gaps until everything fits; the
changelog records a FIFTH row pushing the Start button off the bottom of a
1280x760 window.  Eight more would not fit at any gap, on either screen.  An
overlay is drawn on top of a layout it does not disturb, so the row-fitting
pass, and the tests that pin it, keep working exactly as they did.

The titles come from the loaded mods deck rather than a list here, so a mod
added to cards.json appears in the panel with no code change — the same reason
the counts themselves live in the data file.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pygame

from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import RULES
from .widgets import Button, Stepper

#: Height of one card's row, and the space above the first one.
ROW_H = 46
HEADER_H = 96
FOOTER_H = 74
PANEL_W = 560


class ModCountsPanel:
    """`[-] count [+]` for every Mod Patusa, over whatever screen opened it.

    Holds no opinion about where the numbers go afterwards: the owning screen
    reads :attr:`counts` when the panel closes and does what it likes with
    them.  That is what lets the menu put them straight into a
    :class:`SessionConfig` and the host screen send them to the server.
    """

    def __init__(self, app, library: ContentLibrary) -> None:
        self.app = app
        self.library = library
        self.active = False
        #: Title → copies.  Seeded from the DATA FILE, so "the defaults" are
        #: whatever the deck is printed as and this class never restates them.
        self.counts: Dict[str, int] = {
            card.title: max(RULES.mod_count_min,
                            min(RULES.mod_count_max, card.count))
            for card in library.deck(settings.DECK_MODS).cards
        }
        self.steppers: List[Stepper] = []
        self.close_button = Button(pygame.Rect(0, 0, 160, 40), "Gotowe",
                                   radius=9, primary=True)
        self.reset_button = Button(pygame.Rect(0, 0, 160, 40), "Domyślne",
                                   radius=9)
        self.panel = pygame.Rect(0, 0, 0, 0)
        self._lay_out()

    # ── the deck this describes ──────────────────────────────────────────────
    @property
    def titles(self) -> List[str]:
        """Card titles in DATA FILE order — the order the deck is built in."""
        return [card.title
                for card in self.library.deck(settings.DECK_MODS).cards]

    @property
    def defaults(self) -> Dict[str, int]:
        return {card.title: card.count
                for card in self.library.deck(settings.DECK_MODS).cards}

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def is_default(self) -> bool:
        return self.counts == self.defaults

    @property
    def warning(self) -> str:
        """Why this deck will not work, or an empty string.

        A selection deals ``mod_choices`` cards to each of two factions at
        once, so a deck smaller than twice that cannot open one and the round
        simply carries on unpaused.  Said out loud in the panel rather than
        discovered at round three.
        """
        needed = RULES.mod_choices * 2
        if self.total == 0:
            return "Talia jest pusta — wyborów Modów Patusa nie będzie"
        if self.total < needed:
            return (f"Za mało kart ({self.total}) — do jednego wyboru "
                    f"potrzeba {needed}")
        return ""

    # ── layout ───────────────────────────────────────────────────────────────
    def _lay_out(self) -> None:
        r, layout = self.app.renderer, self.app.layout
        scale = getattr(r.fonts, "scale", 1.0)
        titles = self.titles
        row_h = max(34, int(ROW_H * scale))
        height = int(HEADER_H * scale) + row_h * len(titles) + int(FOOTER_H * scale)
        height = min(height, layout.win_h - 40)
        width = min(int(PANEL_W * scale), layout.win_w - 40)
        self.panel = pygame.Rect(0, 0, width, height)
        self.panel.center = (layout.win_w // 2, layout.win_h // 2)

        # The steppers sit against the right edge of the panel so the titles,
        # which vary a lot in length, have the whole left side to themselves.
        # The row is MEASURED rather than assumed: Stepper sizes its buttons to
        # its own labels at the current type scale, so a hard-coded inset was
        # wrong at every window size and hung the "+1" button over the panel
        # edge.  Build one throwaway row, ask how wide it came out, then place
        # the real ones by their right edge.
        self.steppers = []
        top = self.panel.top + int(HEADER_H * scale)
        margin = int(20 * scale)
        probe = Stepper(0, 0, button_w=30, value_w=44, gap=5, r=r)
        row_w = (max(rect.right for rect in probe.rects.values())
                 - min(rect.left for rect in probe.rects.values()))
        stepper_x = self.panel.right - margin - row_w // 2
        for index, _title in enumerate(titles):
            row_y = top + index * row_h
            self.steppers.append(
                Stepper(stepper_x, row_y, button_w=30, value_w=44, gap=5, r=r)
            )

        button_y = self.panel.bottom - int(56 * scale)
        for button in (self.reset_button, self.close_button):
            button.fit(r, min_width=int(140 * scale),
                       min_height=int(38 * scale))
        self.reset_button.rect.topright = (self.panel.centerx - 8, button_y)
        self.close_button.rect.topleft = (self.panel.centerx + 8, button_y)

    def on_resize(self) -> None:
        self._lay_out()

    # ── opening and closing ──────────────────────────────────────────────────
    def open(self, counts: Optional[Dict[str, int]] = None) -> None:
        if counts:
            for title, value in counts.items():
                if title in self.counts:
                    self.counts[title] = max(
                        RULES.mod_count_min,
                        min(RULES.mod_count_max, int(value)))
        self._lay_out()
        self.active = True

    def close(self) -> None:
        self.active = False

    def reset(self) -> None:
        self.counts = dict(self.defaults)

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event,
                     mouse: Tuple[int, int]) -> bool:
        """Returns True when the panel consumed the event.

        It consumes EVERY click while it is open, including clicks outside its
        own rectangle, so the screen underneath cannot be operated through it —
        the panel covers the steppers of the screen that opened it, and a click
        that fell through would change a setting the player cannot see.
        """
        if not self.active:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
                self.close()
            return True
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return True

        for title, stepper in zip(self.titles, self.steppers):
            delta = stepper.hit(mouse)
            if delta:
                self.counts[title] = max(
                    RULES.mod_count_min,
                    min(RULES.mod_count_max,
                        self.counts.get(title, 0) + (1 if delta > 0 else -1)),
                )
                return True
        if self.reset_button.hit(mouse):
            self.reset()
            return True
        if self.close_button.hit(mouse) or not self.panel.collidepoint(mouse):
            self.close()
            return True
        return True

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.close_button.update(mouse, dt)
        self.reset_button.update(mouse, dt)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.active:
            return
        r = self.app.renderer
        theme = r.theme
        layout = self.app.layout
        mouse = self.app.to_design(pygame.mouse.get_pos())
        scale = getattr(r.fonts, "scale", 1.0)

        shade = pygame.Surface((layout.win_w, layout.win_h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surface.blit(shade, (0, 0))

        r.premium_panel(self.panel, surface, radius=14,
                        border=theme.brass_light, glow=theme.brass,
                        glow_strength=0.35, shadow=20)
        r.spaced_text("MODY PATUSA — SKŁAD TALII",
                      r.fonts.get(int(18 * scale), bold=True),
                      theme.text_heading, surface,
                      center=(self.panel.centerx,
                              self.panel.top + int(28 * scale)))
        r.text(f"Kart w talii: {self.total}", r.fonts.get(int(14 * scale)),
               theme.text_dim, surface,
               center=(self.panel.centerx, self.panel.top + int(54 * scale)))

        label_font = r.fonts.get(int(16 * scale))
        for title, stepper in zip(self.titles, self.steppers):
            centre_y = stepper.y + stepper.height // 2
            r.text(title, label_font, theme.text_light, surface,
                   midleft=(self.panel.left + int(24 * scale), centre_y))
            stepper.draw(r, str(self.counts.get(title, 0)), mouse, surface)

        warning = self.warning
        if warning:
            r.text(warning, r.fonts.get(int(13 * scale)), theme.warning, surface,
                   center=(self.panel.centerx,
                           self.close_button.rect.top - int(14 * scale)))
        self.reset_button.draw(r, surface)
        self.close_button.draw(r, surface)
