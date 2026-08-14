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
from ..engine.effects import COPY_ABILITY
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
    #: title -> the named choices the number is an INDEX into, for a tab whose
    #: numbers are not quantities.  Empty for the four counting tabs, which is
    #: what keeps every one of them drawing and behaving exactly as before.
    #: Each entry is ``(id, label, description)``.
    options: Dict[str, List[Tuple[str, str, str]]] = field(default_factory=dict)
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
        """Step one row, clamped to what THAT row can hold.

        A CHOICE ROW IS BOUNDED BY ITS OWN LIST, not by the tab's numbers.  The
        rules tab mixes two kinds of row — a decision timer that runs 1..30 and
        a variant that runs 0..1 — and clamping the variant to the timer's
        bounds is what made "Wariant 2, then -1" sit still: index 1 minus one
        is 0, and 0 was below the tab's ``low`` of 1, so it clamped straight
        back to 1.  It also let +1 walk the stored index up to 30 while the
        display stopped at the last variant, so a row could look unchanged and
        be far out of range.

        The counting tabs are untouched: with no options for the row, this is
        the tab's bounds exactly as before.
        """
        low, high = self.bounds_for(title)
        current = self.values.get(title, low)
        self.values[title] = max(low, min(high, current + delta))

    def bounds_for(self, title: str) -> Tuple[int, int]:
        """``(low, high)`` for one row — its own list, or the tab's numbers."""
        choices = self.choices_for(title)
        if choices:
            return 0, len(choices) - 1
        return self.low, self.high

    # ── tabs whose numbers name a choice rather than count something ────────
    @property
    def is_choice(self) -> bool:
        return bool(self.options)

    def choices_for(self, title: str) -> List[Tuple[str, str, str]]:
        return self.options.get(title, [])

    def chosen(self, title: str) -> Optional[Tuple[str, str, str]]:
        """The ``(id, label, description)`` this row is currently sitting on."""
        choices = self.choices_for(title)
        if not choices:
            return None
        index = max(0, min(len(choices) - 1, int(self.values.get(title, 0))))
        return choices[index]

    def value_text(self, title: str) -> str:
        """What goes in the stepper's well.

        A number for a quantity, and the choice's own NAME for a choice: "3"
        answers "how many copies", and nothing answers "which variant" except
        saying which one.
        """
        chosen = self.chosen(title)
        if chosen is None:
            return str(self.values.get(title, 0))
        return chosen[1] or chosen[0]

    def ids(self) -> Dict[str, str]:
        """title -> chosen id, which is what a config actually wants."""
        out: Dict[str, str] = {}
        for title in self.titles:
            chosen = self.chosen(title)
            if chosen is not None:
                out[title] = chosen[0]
        return out

    def merge_ids(self, incoming: Optional[Dict[str, str]]) -> None:
        """Take chosen ids from outside and sit each row on that choice.

        The counterpart of :meth:`merge` for a choice tab.  An id this build
        does not know is ignored rather than stored, so a saved configuration
        survives a variant being renamed — the row simply stays on its default.
        """
        for title, wanted in (incoming or {}).items():
            choices = self.choices_for(title)
            for index, (identifier, _label, _text) in enumerate(choices):
                if identifier == str(wanted):
                    self.values[title] = index
                    break

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

    def _variant_tab(self) -> SettingsTab:
        """The cards that can be played more than one way.

        ONLY cards that actually declare two or more variants appear, which is
        the whole reason this is a tab of its own rather than a control beside
        every deck row: twenty-nine of the thirty-one cards have nothing to
        choose, and a selector offering them one choice would be a control that
        cannot do anything.

        The rows are gathered from EVERY deck, so ``AKO`` and ``Nie masz
        Rosji`` — a Mod and a Chest card — will appear here the moment their
        variants are written into cards.json, with no code change.

        The number under the stepper is an INDEX into the card's variant list,
        which is what lets a tab of named choices reuse the counting tab's
        machinery whole: bump, clamp, reset and merge all still work on an int.
        """
        options: Dict[str, List[Tuple[str, str, str]]] = {}
        defaults: Dict[str, int] = {}
        titles: List[str] = []
        for deck_id in self.library.deck_order:
            for card in self.library.deck(deck_id).cards:
                if not card.has_variants or card.title in options:
                    continue
                titles.append(card.title)
                options[card.title] = [
                    (variant.id, variant.label or variant.id,
                     variant.text if variant.text is not None else card.text)
                    for variant in card.variants
                ]
                defaults[card.title] = 0

        def summary(tab: SettingsTab) -> str:
            changed = sum(1 for title in tab.titles
                          if tab.values.get(title, 0) != 0)
            if not tab.titles:
                return "Żadna karta nie ma wariantów"
            return (f"Kart z wariantami: {len(tab.titles)}"
                    + (f"  ·  zmienionych: {changed}" if changed else ""))

        # No ``problem``: every combination of variants is a legal table.  The
        # high bound is the longest variant list, and a row shorter than that
        # is clamped by ``chosen`` rather than by the stepper, so two cards with
        # different numbers of variants can share one tab.
        high = max([len(items) - 1 for items in options.values()] + [0])
        return SettingsTab(
            id="variants", label="Warianty", heading="WARIANTY KART",
            values=dict(defaults), defaults=dict(defaults), titles=titles,
            low=0, high=high, options=options, summary=summary,
        )

    #: The row the rules tab holds.  A CONSTANT rather than a literal in two
    #: places, because the panel reads its value back out by this exact name.
    BLOCK_ROW = "Czas na decyzję o bloku (s)"
    #: Ice Block's window, and the two rule VARIANTS that are a table choice
    #: rather than a card's.  Constants for the same reason BLOCK_ROW is: the
    #: panel reads each value back out by this exact name.
    CHECK_ROW = "Czas na decyzję o checku (s)"
    CHECK_VARIANT_ROW = "Nieudany check"
    VICTORY_VARIANT_ROW = "Zwycięstwo Piotrka"

    def _rules_tab(self) -> SettingsTab:
        """Table rules that are one number each, rather than one per card.

        Nie masz Rosji's decision window is the first: it is a number the table
        wants to try at 3 and at 12, which is exactly why it is a setting and
        not a constant in the gameplay code.  It lives in the panel rather than
        as another row on the two setup screens because both of those lay
        themselves out by measuring rows and shrinking the gaps, and the
        changelog already records a fifth row pushing the Start button off a
        1280x760 window.
        """
        low, high = RULES.block_decision_min, RULES.block_decision_max
        defaults = {
            self.BLOCK_ROW: RULES.block_decision_default,
            self.CHECK_ROW: RULES.check_decision_default,
            # Index 0 is the game as it shipped, on both rows.  A table that
            # never opens this tab plays exactly what it played before.
            self.CHECK_VARIANT_ROW: 0,
            self.VICTORY_VARIANT_ROW: 0,
        }
        # THE SAME NAMED-OPTION MACHINERY THE VARIANTS TAB USES.  A rule
        # variant is a choice between labelled answers, not a quantity, so it
        # reuses ``options`` rather than growing a second kind of row: bump,
        # clamp, reset and merge all still work on an int.
        options = {
            self.CHECK_VARIANT_ROW: [
                (variant, name, description)
                for variant, (name, description)
                in settings.CHECK_VARIANTS.items()
            ],
            self.VICTORY_VARIANT_ROW: [
                (variant, name, description)
                for variant, (name, description)
                in settings.VICTORY_VARIANTS.items()
            ],
        }

        def summary(tab: SettingsTab) -> str:
            changed = sum(1 for title in (self.CHECK_VARIANT_ROW,
                                          self.VICTORY_VARIANT_ROW)
                          if tab.values.get(title, 0) != 0)
            base = "Czasy decyzji i warianty zasad"
            return base + (f"  ·  zmienionych wariantów: {changed}"
                           if changed else "")

        return SettingsTab(
            id="rules", label="Zasady", heading="ZASADY STOŁU",
            values=dict(defaults), defaults=dict(defaults),
            titles=[self.BLOCK_ROW, self.CHECK_ROW, self.CHECK_VARIANT_ROW,
                    self.VICTORY_VARIANT_ROW],
            low=low, high=high, options=options, summary=summary,
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
            self._variant_tab(),
            self._rules_tab(),
            self._copy_tab(),
        ]

    #: The two answers every borrowable ability gets on the Herold tab.
    KEEPS_USE = ("keeps", "Zachowuje", "właściciel nie traci użycia")
    SPENDS_USE = ("spends", "Traci", "właściciel traci jedno użycie")

    def _copy_tab(self) -> SettingsTab:
        """Which abilities cost their OWNER a use when Herold copies them.

        One row per borrowable ability, each a two-answer choice — the same
        named-option machinery the variants and rules tabs use, so bump, clamp,
        reset and merge all keep working on an int and this is not a third kind
        of row.

        THE LIST IS BUILT FROM THE CARDS, not typed out: a tenth character
        appears here the day it is added, and anything that is itself a copier
        is left out because it is not borrowable.  The default marks Glockboy,
        matching ``SessionConfig.copy_consumes_use`` — and nothing here assumes
        Glockboy is special beyond being the default, which is the whole point
        of the setting existing.
        """
        titles: List[str] = []
        options: Dict[str, List[Tuple[str, str, str]]] = {}
        defaults: Dict[str, int] = {}
        printed = set(settings.SessionConfig().copy_consumes_use or ())

        for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
            deck = self.library.decks.get(deck_id)
            if deck is None:
                continue
            for card in deck.cards:
                ability = card.ability
                if ability is None or str(ability.type) == COPY_ABILITY:
                    continue
                row = card.skill or card.title
                if row in options:
                    continue
                titles.append(row)
                options[row] = [self.KEEPS_USE, self.SPENDS_USE]
                defaults[row] = 1 if row in printed else 0

        def summary(tab: SettingsTab) -> str:
            spending = [title for title in tab.titles
                        if tab.values.get(title, 0) == 1]
            if not tab.titles:
                return "Brak umiejętności do skopiowania"
            return (f"Umiejętności: {len(tab.titles)}"
                    + (f"  ·  zabierają użycie: {len(spending)}" if spending
                       else "  ·  żadna nie zabiera użycia"))

        return SettingsTab(
            id="copy", label="Herold", heading="HEROLD — KOPIOWANE UŻYCIA",
            values=dict(defaults), defaults=dict(defaults), titles=titles,
            low=0, high=1, options=options, summary=summary,
        )

    @property
    def copy_consumes_use(self) -> Tuple[str, ...]:
        """The skills whose owner also pays, as a config wants them: by NAME.

        Translated at the boundary for the same reason every other choice row
        is — the panel counts in indices, a config names things, and a number
        would break the moment somebody reordered the tab.
        """
        tab = self.tabs[self._index_of("copy")]
        return tuple(title for title in tab.titles
                     if (tab.chosen(title) or self.KEEPS_USE)[0] == "spends")

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

    @property
    def block_decision_seconds(self) -> int:
        """The Nie masz Rosji window, in seconds, as the config wants it."""
        tab = self.tabs[self._index_of("rules")]
        return int(tab.values.get(self.BLOCK_ROW,
                                  RULES.block_decision_default))

    @property
    def check_decision_seconds(self) -> int:
        """Ice Block's window, in seconds, as the config wants it."""
        tab = self.tabs[self._index_of("rules")]
        return int(tab.values.get(self.CHECK_ROW,
                                  RULES.check_decision_default))

    @property
    def check_variant(self) -> str:
        return self._rule_choice(self.CHECK_VARIANT_ROW, "continue")

    @property
    def victory_variant(self) -> str:
        return self._rule_choice(self.VICTORY_VARIANT_ROW, "own_pawn")

    def _rule_choice(self, row: str, fallback: str) -> str:
        """The chosen id of a named rule row, translated at the boundary.

        The panel counts in indices because a stepper counts; a config names
        ids because a number would break the moment somebody reordered the
        table.  Same translation the variants tab does, in the same place.
        """
        chosen = self.tabs[self._index_of("rules")].chosen(row)
        return chosen[0] if chosen is not None else fallback

    @property
    def card_variants(self) -> Dict[str, str]:
        """Title -> chosen variant id, which is what a SessionConfig holds.

        The panel counts in indices because a stepper counts; a config names
        ids because a card names its variants and a number would break the
        moment somebody reordered them in the JSON.  The translation happens
        here, once, at the boundary.
        """
        return self.tabs[self._index_of("variants")].ids()

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
             ability_uses: Optional[Dict[str, int]] = None,
             card_variants: Optional[Dict[str, str]] = None,
             block_decision_seconds: Optional[int] = None) -> None:
        """Show the panel, optionally seeded from settings held elsewhere.

        ``counts`` is the Mody Patusa mapping and keeps its old name and
        position: it is what every existing caller passes, and renaming it
        would break them for no gain.
        """
        self.tabs[self._index_of(settings.DECK_MODS)].merge(counts)
        self.tabs[self._index_of(settings.DECK_MOVEMENT)].merge(movement_counts)
        self.tabs[self._index_of(settings.DECK_CHEST)].merge(chest_counts)
        self.tabs[self._index_of("abilities")].merge(ability_uses)
        self.tabs[self._index_of("variants")].merge_ids(card_variants)
        if block_decision_seconds is not None:
            self.tabs[self._index_of("rules")].merge(
                {self.BLOCK_ROW: block_decision_seconds})
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
            if tab.is_choice:
                # Two lines, because a variant is a SENTENCE and the whole
                # point of choosing one is reading the difference: the title
                # says which card, the line under it says what this reading of
                # it does.  Rows are otherwise identical to a counting tab's.
                chosen = tab.chosen(title)
                r.text(title, r.fonts.get(int(14 * scale), bold=True),
                       theme.text_light, surface,
                       midleft=(left, centre_y - int(9 * scale)))
                if chosen is not None:
                    description = r.fitted_font(chosen[2], max(40, room),
                                                int(11 * scale), bold=False,
                                                spacing=0)
                    r.text(chosen[2], description, theme.text_dim, surface,
                           midleft=(left, centre_y + int(9 * scale)))
            else:
                font = r.fitted_font(title, max(40, room), int(15 * scale),
                                     bold=False, spacing=0)
                r.text(title, font, theme.text_light, surface,
                       midleft=(left, centre_y))
            stepper.draw(r, tab.value_text(title), mouse, surface)

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
