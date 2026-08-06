"""
Pre-game settings screen.

Same four decisions as the prototype — player count, board size, the round the
chest opens, and each seat's character — but built from widgets, so the whole
screen is now about a third of the code it was and the dropdown behaviour
(exclusive picks, opening upward when it would fall off the bottom) lives in
the widget rather than in the event loop.

This screen is the direct ancestor of the multiplayer lobby: it already
produces a :class:`SessionConfig`, which is exactly what a host will send to
its clients.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import pygame

from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import RULES, SessionConfig
from .app import App, Screen
from .headings import content_top, draw_title
from .widgets import BUTTON_TEXT_SIZE, Button, Checkbox, Dropdown, Stepper

ROW_H = 38
ROW_STEP = 42
CHECKBOX_SIZE = 18
DROPDOWN_W = 220
DROPDOWN_H = 26


class MenuScreen(Screen):
    def __init__(
        self,
        app: App,
        library: ContentLibrary,
        on_start: Callable[[SessionConfig], None],
    ) -> None:
        super().__init__(app)
        self.library = library
        self.on_start = on_start
        self.titles = library.character_titles()

        self.num_players = RULES.max_players
        self.board_cells = RULES.board_cells_default
        self.chest_round = RULES.chest_open_default
        #: Percentage of rows widened into a doubled position (12a / 12b).
        self.double_percent = RULES.double_frequency_default
        #: Hot-seat editing.  On, the game behaves as the prototype did; off,
        #: it behaves the way it will once it is played over a network.
        self.edit_mode = True
        #: Development option: allow a two-player table.
        self.debug_version = False
        self.choices: List[Optional[str]] = [None] * self.num_players

        self._lay_out()

        layout = app.layout
        r = app.renderer
        centre = layout.win_w // 2
        self.players_stepper = Stepper(centre, self.players_row_y, r=r)
        self.cells_stepper = Stepper(centre, self.cells_row_y, big_steps=True, r=r)
        self.chest_stepper = Stepper(centre, self.chest_row_y, r=r)
        self.doubles_stepper = Stepper(centre, self.doubles_row_y, big_steps=True, r=r)
        self.edit_checkbox = Checkbox(
            pygame.Rect(centre - 150, self.edit_row_y, CHECKBOX_SIZE, CHECKBOX_SIZE),
            checked=True,
        )
        self.debug_checkbox = Checkbox(
            pygame.Rect(centre + 150, self.edit_row_y, CHECKBOX_SIZE, CHECKBOX_SIZE),
            checked=False,
        )

        self.checkboxes: List[Checkbox] = []
        self.dropdowns: List[Dropdown] = []
        self._build_rows()

        self.start_button = Button(
            pygame.Rect(0, 0, 180, 52), "Start", radius=10, primary=True,
        )
        self.start_button.fit(r, min_width=180,
                              min_height=int(52 * r.fonts.scale))
        self.start_button.rect.midtop = (centre, self._start_button_y())
        self.open_dropdown: Optional[int] = None

    # ── layout ───────────────────────────────────────────────────────────────
    def _lay_out(self) -> None:
        """Place the rows top-down from measured heights.

        Fractions of the window height were close enough at 1080p and wrong
        below it: at 1280×760 the subtitle and the first row label were drawn on
        top of each other, and the Start button fell off the bottom.  The gaps
        are tried from comfortable to tight until the whole screen fits.
        """
        r, layout = self.app.renderer, self.app.layout
        height = layout.win_h
        label_h = r.fonts.get(18).get_height()
        stepper_h = 40
        row_label_gap = 6
        margin = 14

        # Measured, not assumed: the Start button is sized to its caption and
        # the type grows with the window, so the footer has to ask rather than
        # remember 52.
        button_h = max(int(52 * r.fonts.scale),
                       r.fonts.get(BUTTON_TEXT_SIZE, bold=True).get_height() + 28)
        self.footer_h = 20 + button_h + 12 + r.fonts.get(16, bold=True).get_height()
        top = content_top(r, layout)
        preferred_step = ROW_STEP if height >= 900 else 34

        for gap in range(28, 3, -2):
            y = top
            rows = []
            for _ in range(4):
                rows.append(y + label_h + row_label_gap)
                y += label_h + row_label_gap + stepper_h + gap
            edit_row_y = y
            # Checkbox row, then the note under it, then the gap.
            y += CHECKBOX_SIZE + 6 + r.fonts.get(13).get_height() + max(gap, 10)
            char_header_y = y
            char_rows_top = char_header_y + label_h + 10

            available = height - margin - self.footer_h - char_rows_top
            step = min(preferred_step, available // max(1, RULES.max_players))
            if step >= 24 or gap <= 5:
                self.row_step = max(20, step)
                (self.players_row_y, self.cells_row_y,
                 self.chest_row_y, self.doubles_row_y) = rows
                self.edit_row_y = edit_row_y
                self.char_header_y = char_header_y
                self.char_rows_top = char_rows_top
                return

    def on_resize(self) -> None:
        self._lay_out()
        self._reposition()

    def _reposition(self) -> None:
        """Move the widgets onto the freshly computed rows."""
        r = self.app.renderer
        centre = self.app.layout.win_w // 2
        self.players_stepper = Stepper(centre, self.players_row_y, r=r)
        self.cells_stepper = Stepper(centre, self.cells_row_y, big_steps=True, r=r)
        self.chest_stepper = Stepper(centre, self.chest_row_y, r=r)
        self.doubles_stepper = Stepper(centre, self.doubles_row_y, big_steps=True, r=r)
        self.edit_checkbox.rect.topleft = (centre - 150, self.edit_row_y)
        self.debug_checkbox.rect.topleft = (centre + 150, self.edit_row_y)
        self._build_rows()
        self.start_button.fit(r, min_width=180,
                              min_height=int(52 * r.fonts.scale))
        self.start_button.rect.midtop = (centre, self._start_button_y())

    # ── row construction ─────────────────────────────────────────────────────
    def _row_left(self) -> int:
        return self.app.layout.win_w // 2 - 275

    def _build_rows(self) -> None:
        self.checkboxes.clear()
        self.dropdowns.clear()
        left = self._row_left()
        for i in range(RULES.max_players):
            row_y = self.char_rows_top + i * self.row_step
            self.checkboxes.append(
                Checkbox(
                    pygame.Rect(left + 154, row_y + (ROW_H - CHECKBOX_SIZE) // 2,
                                CHECKBOX_SIZE, CHECKBOX_SIZE),
                    checked=True,
                )
            )
            dropdown = Dropdown(
                pygame.Rect(left + 340, row_y + (ROW_H - DROPDOWN_H) // 2,
                            DROPDOWN_W, DROPDOWN_H),
                self.titles,
            )
            dropdown.max_bottom = self.app.layout.win_h - 10
            dropdown.enabled = False
            self.dropdowns.append(dropdown)

    @property
    def minimum_players(self) -> int:
        """Two with the development option on, the real minimum otherwise."""
        return (RULES.debug_min_players if self.debug_version
                else RULES.min_players)

    # ── validation ───────────────────────────────────────────────────────────
    def validation_error(self) -> Optional[str]:
        """Why the game cannot start yet, or ``None`` when it can.

        Exactly one rule for now, and it is the important one: somebody has to
        be Piotrek. If any seat is set to a random character the dealer
        guarantees it, so the check only bites when every seat was chosen by
        hand and none of them picked him.
        """
        seats = self.choices[: self.num_players]
        if any(choice is None for choice in seats):
            return None
        if settings.PIOTREK_TITLE not in seats:
            return (
                f"Nikt nie wybrał postaci „{settings.PIOTREK_TITLE}” — "
                "bez niego nie ma kogo ścigać"
            )
        return None

    @property
    def can_start(self) -> bool:
        return self.validation_error() is None

    def _start_button_y(self) -> int:
        return self.char_rows_top + self.num_players * self.row_step + 20

    def _sync_widgets(self) -> None:
        taken = {c for c in self.choices if c is not None}
        for i in range(RULES.max_players):
            active = i < self.num_players
            choice = self.choices[i] if active else None
            self.checkboxes[i].checked = choice is None
            self.checkboxes[i].enabled = active
            dropdown = self.dropdowns[i]
            dropdown.enabled = active and choice is not None
            dropdown.value = choice
            dropdown.open = self.open_dropdown == i
            dropdown.disabled_options = taken - ({choice} if choice else set())
        self.start_button.rect.y = self._start_button_y()
        self.start_button.enabled = self.can_start
        self.edit_checkbox.checked = self.edit_mode
        self.debug_checkbox.checked = self.debug_version

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.open_dropdown is not None:
                    self.open_dropdown = None
                else:
                    self.app.quit()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.open_dropdown is None:
                self._start()
                return

        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        # An open dropdown swallows the click.
        if self.open_dropdown is not None:
            dropdown = self.dropdowns[self.open_dropdown]
            option = dropdown.option_at(mouse)
            if option is not None:
                self.choices[self.open_dropdown] = option
            self.open_dropdown = None
            return

        delta = self.players_stepper.hit(mouse)
        if delta:
            self.num_players = max(
                self.minimum_players,
                min(RULES.max_players, self.num_players + delta),
            )
            while len(self.choices) < self.num_players:
                self.choices.append(None)
            self.choices = self.choices[: max(self.num_players, len(self.choices))]
            return

        delta = self.cells_stepper.hit(mouse)
        if delta:
            self.board_cells = max(RULES.board_cells_min, self.board_cells + delta)
            return

        delta = self.chest_stepper.hit(mouse)
        if delta:
            self.chest_round = max(RULES.chest_open_min, self.chest_round + delta)
            return

        if self.edit_checkbox.hit(mouse):
            self.edit_mode = not self.edit_mode
            return

        if self.debug_checkbox.hit(mouse):
            self.debug_version = not self.debug_version
            if not self.debug_version:
                self.num_players = max(RULES.min_players, self.num_players)
            return

        delta = self.doubles_stepper.hit(mouse)
        if delta:
            step = 10 if abs(delta) > 1 else 5
            self.double_percent = max(0, min(100, self.double_percent + step * (1 if delta > 0 else -1)))
            return

        if self.start_button.hit(mouse):
            self._start()
            return

        for i in range(self.num_players):
            if self.checkboxes[i].hit(mouse):
                if self.choices[i] is None:
                    taken = {c for c in self.choices if c is not None}
                    self.choices[i] = next(
                        (t for t in self.titles if t not in taken), self.titles[0]
                    )
                else:
                    self.choices[i] = None
                return
            if self.dropdowns[i].enabled and self.dropdowns[i].hit(mouse):
                self.open_dropdown = i
                return

    def _start(self) -> None:
        if not self.can_start:
            return
        config = SessionConfig(
            num_players=self.num_players,
            board_cells=self.board_cells,
            chest_open_round=self.chest_round,
            character_choices=list(self.choices[: self.num_players]),
            double_frequency=self.double_percent / 100.0,
            edit_mode=self.edit_mode,
            debug_version=self.debug_version,
            # One production flow for the identity, hot-seat included: whoever
            # drew Piotrek picks a colour before the first move.  Dealing it
            # from the seed survives only behind --players/--selftest, where
            # there is no one to click.
            piotrek_picks_pawn=True,
        )
        self.on_start(config.normalised())

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        self._sync_widgets()
        for widget in (*self.checkboxes, *self.dropdowns, self.edit_checkbox,
                       self.debug_checkbox, self.start_button):
            widget.update(mouse, dt)

    def draw(self, surface: pygame.Surface) -> None:
        r = self.app.renderer
        theme = r.theme
        layout = self.app.layout
        mouse = self.app.to_design(pygame.mouse.get_pos())
        centre = layout.win_w // 2

        draw_title(r, layout, surface, "Ustawienia gry")

        r.text("Liczba graczy", r.fonts.get(18), theme.text_light, surface,
               midbottom=(centre, self.players_row_y - 6))
        self.players_stepper.draw(r, str(self.num_players), mouse, surface)

        r.text(f"Liczba pól planszy (min. {RULES.board_cells_min})", r.fonts.get(18),
               theme.text_light, surface, midbottom=(centre, self.cells_row_y - 6))
        self.cells_stepper.draw(r, str(self.board_cells), mouse, surface)

        r.text(f"Skrzynia otwiera się w rundzie (min. {RULES.chest_open_min})",
               r.fonts.get(18), theme.text_light, surface,
               midbottom=(centre, self.chest_row_y - 6))
        self.chest_stepper.draw(r, str(self.chest_round), mouse, surface)

        r.text("Jak często pola podwójne (12a / 12b)", r.fonts.get(18),
               theme.text_light, surface, midbottom=(centre, self.doubles_row_y - 6))
        self.doubles_stepper.draw(r, f"{self.double_percent}%", mouse, surface)

        self.edit_checkbox.draw(r, surface)
        r.text("Tryb edycji", r.fonts.get(17), theme.text_light, surface,
               midleft=(centre - 150 + CHECKBOX_SIZE + 8,
                        self.edit_row_y + CHECKBOX_SIZE // 2))
        self.debug_checkbox.draw(r, surface)
        r.text("Wersja testowa (od 2 graczy)", r.fonts.get(17), theme.text_light,
               surface,
               midleft=(centre + 150 + CHECKBOX_SIZE + 8,
                        self.edit_row_y + CHECKBOX_SIZE // 2))
        r.text(
            "tryb edycji: grasz za wszystkich z jednego komputera  ·  "
            "wersja testowa: tylko do testów",
            r.fonts.get(13), theme.text_dim, surface,
            midtop=(centre, self.edit_row_y + CHECKBOX_SIZE + 6),
        )

        r.text("Postacie graczy", r.fonts.get(18), theme.text_light, surface,
               midtop=(centre, self.char_header_y))

        row_font = r.fonts.get(15)
        left = self._row_left()
        for i in range(self.num_players):
            row_y = self.char_rows_top + i * self.row_step
            r.text(f"Player {i + 1}", row_font, theme.text_light, surface,
                   midleft=(left, row_y + ROW_H // 2))
            self.checkboxes[i].draw(r, surface)
            r.text("Losowa postać", row_font,
                   theme.text_light if self.choices[i] is None else theme.text_dim,
                   surface,
                   midleft=(left + 154 + CHECKBOX_SIZE + 8, row_y + ROW_H // 2))
            self.dropdowns[i].draw(r, surface)

        self.start_button.draw(r, surface)

        error = self.validation_error()
        if error:
            r.text(error, r.fonts.get(16, bold=True), theme.invalid, surface,
                   midtop=(centre, self.start_button.rect.bottom + 12), shadow=True)
        else:
            r.text(
                f"Liczba graczy: {RULES.min_players}\u2013{RULES.max_players}  \u00b7  "
                "ustawienia są stałe do zamknięcia aplikacji",
                r.fonts.get(13), theme.text_dim, surface,
                midtop=(centre, self.start_button.rect.bottom + 12),
            )

        # The open list is painted last so it sits above everything.
        if self.open_dropdown is not None:
            self.dropdowns[self.open_dropdown].draw_overlay(r, mouse, surface)
