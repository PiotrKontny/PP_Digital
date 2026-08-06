"""
The deck-and-ability settings panel.

One overlay, opened by a button, used by BOTH settings screens: the single
machine menu and the network host screen.  Written once because the two screens
have to offer the same table — a host who sets three Speedruns and a hot-seat
game that cannot say so would be two different games with one rule book.

WHY AN OVERLAY AND NOT MORE ROWS.  Both screens lay themselves out by measuring
their rows and shrinking the gaps until everything fits; the changelog records a
FIFTH row pushing the Start button off the bottom of a 1280x760 window.  Thirty
movement cards would not fit at any gap, on either screen.  An overlay is drawn
on top of a layout it does not disturb, so the row-fitting pass, and the tests
that pin it, keep working exactly as they did.

WHY TABS (stage 26).  This began as the Mody Patusa deck alone.  Four
categories at once is four times the rows, and the movement deck by itself has
thirty titles — so the panel gained tabs and a scrolling list rather than a
fifth screen to keep in step with the other two.  Each tab is a
:class:`SettingsTab`, which is nothing but "where do these numbers come from,
what are they bounded by, and what is wrong with them" — so a fifth category is
one entry in :meth:`_build_tabs` and no new drawing code.

Every title and every default comes from the LOADED DATA, never from a list
here, so a card added to cards.json appears in the panel with no code change —
the same reason the counts themselves live in the data file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pygame

from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import RULES
from .widgets import Button, Stepper

#: Height of one row, and the space above the first one and below the last.
ROW_H = 44
HEADER_H = 118
FOOTER_H = 78
PANEL_W = 620
#: How tall the panel is allowed to get, as a share of the window.  It stops
#: short of the edges so it still reads as a panel ON something rather than as
#: a second screen.
MAX_HEIGHT = 0.88


@dataclass
class SettingsTab:
    """One category of numbers the player may change.

    Deliberately data, not a subclass: a tab is a title, a source of rows, a
    range and a warning, and every one of the four differs only in those.
    """

    id: str
    label: str
    heading: str
    #: Title → current value.  Seeded from the data file by :func:`_seed`.
    values: Dict[str, int] = field(default_factory=dict)
    defaults: Dict[str, int] = field(default_factory=dict)
    titles: List[str] = field(default_factory=list)
    low: int = 0
    high: int = 9
    #: What the number means, for the line under the heading ("kart w talii").
    summary: Callable[["SettingsTab"], str] = lambda tab: ""
    #: Why this configuration will not work, or "".
    problem: Callable[["SettingsTab"], str] = lambda tab: ""

    @property
    def total(self) -> int:
        return sum(self.values.values())

    @property
    def is_default(self) -> bool:
        return self.values == self.defaults

    def reset(self) -> None:
        self.values = dict(self.defaults)

    def bump(self, title: str, delta: int) -> None:
        current = self.values.get(title, self.low)
        self.values[title] = max(self.low, min(self.high, current + delta))

    def merge(self, incoming: Optional[Dict[str, int]]) -> None:
        """Take values from outside, ignoring titles this tab does not have.

        A title the data no longer contains is dropped rather than invented,
        which is what lets a saved configuration survive a card being renamed.
        """
        for title, value in (incoming or {}).items():
            if title in self.values:
                try:
                    self.values[title] = max(self.low, min(self.high, int(value)))
                except (TypeError, ValueError):
                    continue


class GameSettingsPanel:
    """Tabs of ``[-] value [+]`` rows, over whatever screen opened it.

    Holds no opinion about where the numbers go afterwards: the owning screen
    reads the four mappings when the panel closes and does what it likes with
    them.  That is what lets the menu put them straight into a
    :class:`SessionConfig` and the host screen send them to the server.
    """

    def __init__(self, app, library: ContentLibrary) -> None:
        self.app = app
        self.library = library
        self.active = False
        self.tabs: List[SettingsTab] = self._build_tabs()
        self.tab_index = self._index_of(settings.DECK_MODS)
        #: First visible row.  The movement deck is thirty titles long and no
        #: window is tall enough for that, so the list scrolls rather than the
        #: rows shrinking into illegibility.
        self.scroll = 0
        self.steppers: List[Stepper] = []
        self.tab_rects: List[pygame.Rect] = []
        self.close_button = Button(pygame.Rect(0, 0, 160, 40), "Gotowe",
                                   radius=9, primary=True)
        self.reset_button = Button(pygame.Rect(0, 0, 160, 40), "Domyślne",
                                   radius=9)
        self.panel = pygame.Rect(0, 0, 0, 0)
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self._lay_out()

    # ── what the tabs are ────────────────────────────────────────────────────
    def _deck_tab(self, deck_id: str, label: str, heading: str,
                  low: int, high: int, minimum: int = 0) -> SettingsTab:
        cards = self.library.deck(deck_id).cards
        defaults = {card.title: max(low, min(high, card.count)) for card in cards}

        def summary(tab: SettingsTab) -> str:
            return f"Kart w talii: {tab.total}"

        def problem(tab: SettingsTab) -> str:
            if tab.total == 0:
                return "Talia jest pusta"
            if minimum and tab.total < minimum:
                return (f"Za mało kart ({tab.total}) — potrzeba co najmniej "
                        f"{minimum}")
            return ""

        return SettingsTab(
            id=deck_id, label=label, heading=heading,
            values=dict(defaults), defaults=dict(defaults),
            titles=[card.title for card in cards],
            low=low, high=high, summary=summary, problem=problem,
        )

    def _ability_tab(self) -> SettingsTab:
        """Charges, not copies — and only for cards that HAVE an ability.

        Both ability decks are in one list: a Piotrek skill is an ability with
        a number of uses exactly as a character's is, and splitting them into
        two tabs would be a distinction the player does not have a word for.
        Cards without an ability are left out, because a charge counter on a
        card that can never spend one is a control that does nothing.
        """
        low, high = RULES.ability_uses_min, RULES.ability_uses_max
        defaults: Dict[str, int] = {}
        titles: List[str] = []
        for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
            for card in self.library.deck(deck_id).cards:
                if card.ability and card.uses is not None:
                    titles.append(card.title)
                    defaults[card.title] = max(low, min(high, int(card.uses)))

        def summary(tab: SettingsTab) -> str:
            return f"Ładunków łącznie: {tab.total}"

        def problem(tab: SettingsTab) -> str:
            spent = [title for title, value in tab.values.items() if value == 0]
            if len(spent) == len(tab.values) and tab.values:
                return "Żadna postać nie będzie mogła użyć umiejętności"
            return ""

        return SettingsTab(
            id="abilities", label="Umiejętności", heading="UMIEJĘTNOŚCI POSTACI",
            values=dict(defaults), defaults=dict(defaults), titles=titles,
            low=low, high=high, summary=summary, problem=problem,
        )

    def _build_tabs(self) -> List[SettingsTab]:
        cards_low, cards_high = RULES.card_count_min, RULES.card_count_max
        return [
            self._deck_tab(settings.DECK_MOVEMENT, "Karty ruchu",
                           "KARTY RUCHU — SKŁAD TALII", cards_low, cards_high,
                           # Everybody is dealt an opening hand and the deck
                           # refills from its own discard pile, so a deck
                           # smaller than one full table's hands cannot start.
                           minimum=RULES.max_players * RULES.start_hand_piotrek),
            self._deck_tab(settings.DECK_MODS, "Mody Patusa",
                           "MODY PATUSA — SKŁAD TALII",
                           RULES.mod_count_min, RULES.mod_count_max,
                           # A selection deals mod_choices cards to each of two
                           # factions at once; a smaller deck cannot open one.
                           minimum=RULES.mod_choices * 2),
            self._deck_tab(settings.DECK_CHEST, "Karty Skrzyni",
                           "KARTY SKRZYNI — SKŁAD TALII", cards_low, cards_high,
                           minimum=0),
            self._ability_tab(),
        ]

    def _index_of(self, tab_id: str) -> int:
        for index, tab in enumerate(self.tabs):
            if tab.id == tab_id:
                return index
        return 0

    # ── what the owning screen reads ─────────────────────────────────────────
    @property
    def tab(self) -> SettingsTab:
        return self.tabs[self.tab_index]

    def values_of(self, tab_id: str) -> Dict[str, int]:
        return dict(self.tabs[self._index_of(tab_id)].values)

    @property
    def movement_counts(self) -> Dict[str, int]:
        return self.values_of(settings.DECK_MOVEMENT)

    @property
    def mod_counts(self) -> Dict[str, int]:
        return self.values_of(settings.DECK_MODS)

    @property
    def chest_counts(self) -> Dict[str, int]:
        return self.values_of(settings.DECK_CHEST)

    @property
    def ability_uses(self) -> Dict[str, int]:
        return self.values_of("abilities")

    #: The visible tab's numbers, its titles and whether everything is untouched.
    #: Named for what a reader of the panel sees rather than for a category, so
    #: the input code below never has to know which tab is open.
    @property
    def counts(self) -> Dict[str, int]:
        return self.tab.values

    @property
    def titles(self) -> List[str]:
        return list(self.tab.titles)

    @property
    def defaults(self) -> Dict[str, int]:
        return dict(self.tab.defaults)

    @property
    def total(self) -> int:
        return self.tab.total

    @property
    def is_default(self) -> bool:
        return all(tab.is_default for tab in self.tabs)

    @property
    def warning(self) -> str:
        return self.tab.problem(self.tab)

    # ── layout ───────────────────────────────────────────────────────────────
    @property
    def scale(self) -> float:
        return getattr(self.app.renderer.fonts, "scale", 1.0)

    @property
    def row_h(self) -> int:
        return max(32, int(ROW_H * self.scale))

    @property
    def visible_rows(self) -> int:
        return max(1, self.list_rect.height // self.row_h)

    @property
    def max_scroll(self) -> int:
        return max(0, len(self.tab.titles) - self.visible_rows)

    def _lay_out(self) -> None:
        r, layout = self.app.renderer, self.app.layout
        scale = self.scale
        row_h = self.row_h
        header = int(HEADER_H * scale)
        footer = int(FOOTER_H * scale)

        # Tall enough for the longest tab, but never taller than the window
        # allows — past that the list scrolls instead.
        longest = max(len(tab.titles) for tab in self.tabs)
        wanted = header + row_h * longest + footer
        height = min(wanted, int(layout.win_h * MAX_HEIGHT))
        width = min(int(PANEL_W * scale), layout.win_w - int(40 * scale))
        self.panel = pygame.Rect(0, 0, width, height)
        self.panel.center = (layout.win_w // 2, layout.win_h // 2)
        self.list_rect = pygame.Rect(
            self.panel.left, self.panel.top + header,
            self.panel.width, max(row_h, height - header - footer),
        )

        # The tab strip: measured from the captions rather than divided evenly,
        # because "Mody Patusa" and "Umiejętności" are not the same width and
        # an even split clips the longer one at 1280x760.
        font = r.fonts.get(int(14 * scale), bold=True)
        pad = int(14 * scale)
        margin = int(12 * scale)
        widths = [font.size(tab.label)[0] + 2 * pad for tab in self.tabs]
        room = self.panel.width - 2 * margin
        if sum(widths) > room:
            shrink = room / max(1, sum(widths))
            widths = [max(int(40 * scale), int(w * shrink)) for w in widths]
        x = self.panel.left + margin
        tab_y = self.panel.top + int(56 * scale)
        tab_h = int(30 * scale)
        self.tab_rects = []
        for width_of in widths:
            self.tab_rects.append(pygame.Rect(x, tab_y, width_of, tab_h))
            x += width_of

        # The steppers sit against the right edge of the panel so the titles,
        # which vary a lot in length, have the whole left side to themselves.
        # The row is MEASURED rather than assumed: Stepper sizes its buttons to
        # its own labels at the current type scale, so a hard-coded inset was
        # wrong at every window size and hung the "+1" button over the panel
        # edge.  Build one throwaway row, ask how wide it came out, then place
        # the real ones by their right edge.
        probe = Stepper(0, 0, button_w=30, value_w=44, gap=5, r=r)
        row_w = (max(rect.right for rect in probe.rects.values())
                 - min(rect.left for rect in probe.rects.values()))
        stepper_x = self.panel.right - int(20 * scale) - row_w // 2
        self.steppers = [
            Stepper(stepper_x, self.list_rect.top + index * row_h,
                    button_w=30, value_w=44, gap=5, r=r)
            for index in range(self.visible_rows)
        ]

        # Placed by their BOTTOM edge against the panel's, not by a top edge a
        # fixed distance up from it: Button.fit sizes itself to its caption at
        # the current type scale, so at 1280x760 a 49px button hung 1px past
        # the bottom of a panel that had budgeted 47 for it.
        for button in (self.reset_button, self.close_button):
            button.fit(r, min_width=int(140 * scale), min_height=int(38 * scale))
        margin = int(16 * scale)
        self.reset_button.rect.bottomright = (self.panel.centerx - 8,
                                              self.panel.bottom - margin)
        self.close_button.rect.bottomleft = (self.panel.centerx + 8,
                                             self.panel.bottom - margin)
        self._clamp_scroll()

    def on_resize(self) -> None:
        self._lay_out()

    def _clamp_scroll(self) -> None:
        self.scroll = max(0, min(self.max_scroll, self.scroll))

    @property
    def visible_titles(self) -> List[str]:
        """The rows actually on screen, in order — what the steppers line up with."""
        return self.tab.titles[self.scroll:self.scroll + self.visible_rows]

    # ── opening and closing ──────────────────────────────────────────────────
    def select_tab(self, tab_id: str) -> None:
        self.tab_index = self._index_of(tab_id)
        self.scroll = 0
        self._clamp_scroll()

    def open(self, counts: Optional[Dict[str, int]] = None, *,
             movement_counts: Optional[Dict[str, int]] = None,
             chest_counts: Optional[Dict[str, int]] = None,
             ability_uses: Optional[Dict[str, int]] = None) -> None:
        """Show the panel, optionally seeded from settings held elsewhere.

        ``counts`` is the Mody Patusa mapping and keeps its old name and
        position: it is what every existing caller passes, and renaming it
        would break them for no gain.
        """
        self.tabs[self._index_of(settings.DECK_MODS)].merge(counts)
        self.tabs[self._index_of(settings.DECK_MOVEMENT)].merge(movement_counts)
        self.tabs[self._index_of(settings.DECK_CHEST)].merge(chest_counts)
        self.tabs[self._index_of("abilities")].merge(ability_uses)
        self._lay_out()
        self.active = True

    def close(self) -> None:
        self.active = False

    def reset(self) -> None:
        """Put the VISIBLE tab back to the data file's values.

        Only the visible one: a player who spent five minutes on the movement
        deck and then wanted the mods back as printed should not lose the lot
        to one button, and the button sits under the tab it undoes.
        """
        self.tab.reset()

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
            elif event.key == pygame.K_DOWN:
                self.scroll += 1
            elif event.key == pygame.K_UP:
                self.scroll -= 1
            elif event.key == pygame.K_TAB:
                self.tab_index = (self.tab_index + 1) % len(self.tabs)
                self.scroll = 0
            self._clamp_scroll()
            return True
        if event.type == pygame.MOUSEWHEEL:
            self.scroll -= event.y
            self._clamp_scroll()
            return True
        if event.type != pygame.MOUSEBUTTONDOWN:
            return True
        if event.button in (4, 5):
            self.scroll += -1 if event.button == 4 else 1
            self._clamp_scroll()
            return True
        if event.button != 1:
            return True

        for index, rect in enumerate(self.tab_rects):
            if rect.collidepoint(mouse):
                self.tab_index = index
                self.scroll = 0
                self._clamp_scroll()
                return True

        for title, stepper in zip(self.visible_titles, self.steppers):
            delta = stepper.hit(mouse)
            if delta:
                self.tab.bump(title, 1 if delta > 0 else -1)
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
        scale = self.scale
        tab = self.tab

        shade = pygame.Surface((layout.win_w, layout.win_h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surface.blit(shade, (0, 0))

        r.premium_panel(self.panel, surface, radius=14,
                        border=theme.brass_light, glow=theme.brass,
                        glow_strength=0.35, shadow=20)
        r.spaced_text(tab.heading, r.fonts.get(int(17 * scale), bold=True),
                      theme.text_heading, surface,
                      center=(self.panel.centerx,
                              self.panel.top + int(26 * scale)))
        self._draw_tabs(r, surface, mouse, scale)

        summary = tab.summary(tab)
        if summary:
            r.text(summary, r.fonts.get(int(13 * scale)), theme.text_dim,
                   surface, midtop=(self.panel.centerx,
                                    self.list_rect.top - int(18 * scale)))

        for title, stepper in zip(self.visible_titles, self.steppers):
            centre_y = stepper.y + stepper.height // 2
            # Left-aligned and shrunk to the room between the panel edge and
            # the stepper: card titles run from "Troll" to "Fillerski
            # przedmiot - pomarańczowy", and a centred label would wander.
            left = self.panel.left + int(22 * scale)
            room = stepper.rects["minus1"].left - left - int(12 * scale)
            font = r.fitted_font(title, max(40, room), int(15 * scale),
                                 bold=False, spacing=0)
            r.text(title, font, theme.text_light, surface,
                   midleft=(left, centre_y))
            stepper.draw(r, str(tab.values.get(title, 0)), mouse, surface)

        self._draw_scrollbar(r, surface, theme, scale)

        problem = tab.problem(tab)
        if problem:
            r.text(problem, r.fonts.get(int(13 * scale)), theme.warning, surface,
                   center=(self.panel.centerx,
                           self.close_button.rect.top - int(14 * scale)))
        self.reset_button.draw(r, surface)
        self.close_button.draw(r, surface)

    def _draw_tabs(self, r, surface, mouse, scale: float) -> None:
        theme = r.theme
        font = r.fonts.get(int(13 * scale), bold=True)
        for index, (tab, rect) in enumerate(zip(self.tabs, self.tab_rects)):
            selected = index == self.tab_index
            style = r.emphasis(
                fill=theme.panel_bg_light if selected else theme.panel_bg,
                border=theme.brass_light if selected else theme.panel_line,
                text=theme.text_heading if selected else theme.text_dim,
                hover=1.0 if rect.collidepoint(mouse) and not selected else 0.0,
                selected=selected, enabled=True, accent=theme.brass_light,
            )
            drawn = r.interactive_panel(rect, style, surface, radius=7)
            r.fit_text(tab.label, drawn, style.text, surface,
                       base_size=int(13 * scale), bold=True)

    def _draw_scrollbar(self, r, surface, theme, scale: float) -> None:
        """A plain track and thumb, drawn only when there is something to scroll.

        No hit-testing: the wheel and the arrow keys do the scrolling, and a
        draggable thumb on a list this short would be a widget to maintain for
        a gesture nobody reaches for.  It is here so the player can SEE that
        thirty movement cards do not all fit.
        """
        rows = len(self.tab.titles)
        if rows <= self.visible_rows:
            return
        width = max(3, int(4 * scale))
        track = pygame.Rect(self.panel.right - int(7 * scale),
                            self.list_rect.top, width, self.list_rect.height)
        pygame.draw.rect(surface, theme.panel_inset, track, border_radius=width)
        share = self.visible_rows / rows
        thumb_h = max(int(18 * scale), int(track.height * share))
        travel = track.height - thumb_h
        offset = int(travel * (self.scroll / max(1, self.max_scroll)))
        thumb = pygame.Rect(track.left, track.top + offset, width, thumb_h)
        pygame.draw.rect(surface, theme.brass, thumb, border_radius=width)
        r.text(f"{self.scroll + 1}–{min(rows, self.scroll + self.visible_rows)}"
               f" z {rows}", r.fonts.get(int(11 * scale)), theme.text_dim,
               surface, midtop=(self.panel.centerx,
                                self.list_rect.bottom + int(2 * scale)))
