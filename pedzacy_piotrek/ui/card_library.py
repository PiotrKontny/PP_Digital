"""
The Card Library — every card in the game, laid out as cards (stage 32).

Opened from the BOOK in the bottom-right corner of the board, over a live
match.  Four categories, the first three of which are the table decks and are
editable, and a fourth that is not a deck at all:

    KARTY RUCHU / MODY PATUSA / KARTY SKRZYNI   [-] copies in the match [+]
    UMIEJĘTNOŚCI                                the owner, the card, a restore
                                                action and [-] uses left [+]

WHAT THIS FILE DOES NOT CONTAIN, and deliberately:

* A CARD RENDERER.  Every card here goes through
  :meth:`~pedzacy_piotrek.render.card_renderer.CardRenderer.draw_in`, exactly
  as the hand fan and every panel does, so a Signature card is a Signature card
  and a standard card is the parchment face — with no branch anywhere in this
  file on whether a card has artwork.  Stage 30 put that branch in ``face()``
  precisely so that a new screen would not have to know.
* CARD DEFINITIONS.  The four tabs are built from the loaded
  :class:`~pedzacy_piotrek.cards.loader.ContentLibrary`, so a card added to
  cards.json appears here with no code change.
* QUANTITIES OF ITS OWN.  Every number drawn under a card is read from the live
  :class:`~pedzacy_piotrek.engine.game_state.GameState` on the frame it is
  drawn, and every change leaves as a Command.  There is no library copy of
  anything to fall out of step with the game.
* ANYTHING ON A CARD FACE.  Stage 31's rule holds here: the count, the steppers
  and the owner's name are OUTSIDE the card, never over it.  A card face is the
  one surface in this game whose content the project does not control.

THE DISPLAY CARDS.  A library entry needs a ``Card`` to draw and most of them
are not in anybody's hand — a title with three copies in the deck is still one
picture.  So each entry holds ONE throwaway ``Card`` built from the definition,
used for drawing and for nothing else: it is never submitted in a command,
never enters a deck and never leaves this file.  Commands address cards by
TITLE, which is what the lobby's deck composition uses and what survives a card
being added or removed mid-match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pygame

from ..cards.base_card import Card, CardDef
from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.theme import darken
from ..engine import commands as cmd
from ..engine.animation import approach
from ..render.card_renderer import CardRenderer
from ..render.renderer import Renderer
from .layout import Layout
from .widgets import Button, Stepper

#: How long a refusal from the engine stays on the library's footer.
NOTICE_SECONDS = 4.0

#: Wheel step, as a fraction of one grid row.  A whole row per notch jumps past
#: the cards between; a third of one reads as scrolling.
WHEEL_FRACTION = 0.34


@dataclass
class LibraryEntry:
    """One cell of the grid."""

    #: Drawn, and only drawn.  See the module docstring.
    card: Card
    title: str
    #: Whose ability this is — the character's own name, or Piotrek's for one
    #: of his skills.  Empty for the three deck tabs.
    owner: str = ""
    #: The ability's printed name ("Jazdy"), when it differs from the card's.
    ability: str = ""
    #: Which variant the display card is currently showing, so the entry can
    #: notice the match has moved on and rebuild.  Empty for the great majority
    #: of cards, which have no variants at all.
    variant: str = ""


@dataclass
class LibraryTab:
    """One category.  A RECORD, not a subclass — the same bargain
    :class:`~pedzacy_piotrek.ui.settings_panel.SettingsTab` makes.

    The three deck tabs differ from each other only in ``deck_id``, and the
    ability tab differs from all three only in ``abilities``.  A fifth category
    would be one more entry in :meth:`CardLibrary._build_tabs`.
    """

    id: str
    label: str
    heading: str
    entries: List[LibraryEntry] = field(default_factory=list)
    #: The deck whose composition this tab edits, or "" for the abilities.
    deck_id: str = ""
    abilities: bool = False

    @property
    def has_variants(self) -> bool:
        """Whether ANY card here has variants, which is a question about the
        GRID: the row height is shared by every cell in the tab."""
        return any(entry.card.has_variants for entry in self.entries)


class CardLibrary:
    """The library overlay: state, input and drawing.

    Modal in the same sense :class:`~pedzacy_piotrek.ui.settings_panel.GameSettingsPanel`
    is: while it is open it consumes EVERY event, including clicks outside its
    own panel and the wheel, so nothing reaches the table underneath.
    """

    def __init__(self, library: ContentLibrary, state, submit: Callable,
                 renderer: Optional[Renderer] = None,
                 seat: Optional[Callable[[], int]] = None) -> None:
        self.library = library
        self.state = state
        self.submit = submit
        #: Which seat 'Dobierz kartę' draws FOR.  A callable, not a number,
        #: because the seat on screen changes during a hot-seat game and the
        #: hand fan reads it the same way for the same reason.
        self.seat: Callable[[], int] = seat or (lambda: 0)
        self.r = renderer
        self.active = False
        self.tabs: List[LibraryTab] = self._build_tabs()
        self.tab_index = 0
        #: Vertical scroll of the grid, in PIXELS.  Pixels rather than rows
        #: because a row here is a card and a card is most of the viewport: a
        #: row-quantised scroll would move the grid by more than a screen.
        self.scroll = 0
        self.scroll_by_tab: Dict[str, int] = {}
        #: title → 0..1 hover, so a Signature card's reveal glides the way it
        #: does in the hand rather than snapping.  Keyed by tab and title
        #: because "Shady" is a Chest card AND was once a Mod.
        self.hover: Dict[Tuple[str, str], float] = {}
        self.appear = 0.0
        self.notice = ""
        self.notice_left = 0.0
        #: False paints the notice red (a refusal), True green (it worked).
        self.notice_ok = False
        self.tab_rects: List[pygame.Rect] = []
        self.close_button = Button(pygame.Rect(0, 0, 160, 40), "Zamknij",
                                   radius=9, primary=True)

    # ── what is in it ────────────────────────────────────────────────────────
    def _display_card(self, definition: CardDef,
                      as_ability: bool = False) -> Card:
        """One throwaway card to draw, read the way THIS MATCH reads it.

        A card with variants is shown under the variant the match is playing —
        the description on its face is the one the rules are actually using —
        and that comes from the game state, exactly as every number in this
        file does.  The library still invents nothing: it asks.

        ``as_ability`` swaps the card onto its :attr:`CardDef.ability_face`,
        which is how the ability tab shows "Granny Costume" rather than "Big D
        Randy".  ORDER MATTERS: the variant is resolved FIRST, under the
        character's own title, because that is the title the match's variant
        settings are keyed by — taking the ability face first would ask the
        state about a card called "Granny Costume", which no deck contains.
        """
        variant = ""
        if definition.has_variants:
            chosen = self.state.variant_definition(definition.deck_id,
                                                   definition.title)
            if chosen is not None:
                definition = chosen
                variant = chosen.selected_variant
        if as_ability:
            definition = definition.ability_face
        card = Card(definition)
        return card

    def _entry_for(self, definition: CardDef, as_ability: bool = False,
                   **kwargs) -> LibraryEntry:
        """One cell.  ``title`` is the CARD's, never the ability face's.

        The entry's ``title`` is the identity every COMMAND uses —
        ``AdjustAbilityUses``, ``RestoreAbilityUses`` and
        ``GameState.ability_card`` all find a character card by its own title —
        so it stays "Big D Randy" even when the face drawn above it says
        "Granny Costume".  That split is the whole of this fix: what the
        player reads is the ability, what the engine is told is the card.
        """
        card = self._display_card(definition, as_ability=as_ability)
        return LibraryEntry(card=card, title=definition.title,
                            variant=card.variant, **kwargs)

    def _deck_tab(self, deck_id: str, label: str, heading: str) -> LibraryTab:
        """One table deck, in the data file's own order.

        The ORDER is the definition's, which is the order the lobby's deck
        composition lists and the order the cards were printed in.  Nothing
        here sorts or shuffles: a library that reordered itself between two
        openings would be unusable as a reference.
        """
        entries = [self._entry_for(definition)
                   for definition in self.library.deck(deck_id).cards]
        return LibraryTab(id=deck_id, label=label, heading=heading,
                          entries=entries, deck_id=deck_id)

    def _piotrek_name(self) -> str:
        """What this project calls Piotrek, read from the character deck.

        Not a literal: the relationship between a skill deck and the character
        who draws from it is in the data (``"role": "piotrek"``), and a name
        typed here would be a second copy of it to keep in step.
        """
        for definition in self.library.deck(settings.DECK_CHARACTERS).cards:
            if definition.is_piotrek:
                return definition.title
        return self.library.deck_name(settings.DECK_SKILLS)

    def _ability_tab(self) -> LibraryTab:
        """Both ability decks in one list, each with the character who owns it.

        The same population as the settings panel's fourth tab — a card with an
        ability and a number of uses — for the same reason: a charge counter on
        a card that can never spend one is a control that does nothing.

        A CHARACTER CARD *IS* ITS ABILITY CARD, but it is not called the same
        thing.  ``Lubin`` is the character and ``Jazdy`` is the ability printed
        on it, and this tab is the ABILITIES tab — so the card is drawn under
        its :attr:`CardDef.ability_face` and the character's name goes above it
        as the owner.  Until stage 49 the face carried the CHARACTER's name,
        which meant the tab titled every ability after its owner and looked its
        artwork up under the owner's name too, so an ability whose picture
        existed showed a parchment card.  One of Piotrek's skills is its own
        card with no separate skill name, so its ability face is itself.
        """
        entries: List[LibraryEntry] = []
        piotrek = self._piotrek_name()
        for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
            for definition in self.library.deck(deck_id).cards:
                if definition.ability is None or definition.uses is None:
                    continue
                owner = (definition.title if deck_id == settings.DECK_CHARACTERS
                         else piotrek)
                entries.append(self._entry_for(
                    definition, as_ability=True, owner=owner,
                    ability=definition.ability_face.title,
                ))
        return LibraryTab(id="abilities", label="Umiejętności",
                          heading="UMIEJĘTNOŚCI", entries=entries,
                          abilities=True)

    def _build_tabs(self) -> List[LibraryTab]:
        return [
            self._deck_tab(settings.DECK_MOVEMENT, "Karty ruchu",
                           "KARTY RUCHU"),
            self._deck_tab(settings.DECK_MODS, "Mody Patusa", "MODY PATUSA"),
            self._deck_tab(settings.DECK_CHEST, "Karty skrzyni",
                           "KARTY SKRZYNI"),
            self._ability_tab(),
        ]

    @property
    def tab(self) -> LibraryTab:
        return self.tabs[self.tab_index]

    @property
    def entries(self) -> List[LibraryEntry]:
        return self.tab.entries

    def index_of(self, tab_id: str) -> int:
        for index, tab in enumerate(self.tabs):
            if tab.id == tab_id:
                return index
        return 0

    def select_tab(self, tab_id: str) -> None:
        self.scroll_by_tab[self.tab.id] = self.scroll
        self.tab_index = self.index_of(tab_id)
        self.scroll = self.scroll_by_tab.get(self.tab.id, 0)

    # ── the numbers, read from the game every frame ──────────────────────────
    def count_of(self, entry: LibraryEntry) -> int:
        """How many copies of this card the match holds."""
        return self.state.deck_card_count(self.tab.deck_id, entry.title)

    def uses_of(self, entry: LibraryEntry) -> Tuple[int, int]:
        """``(remaining, default)`` for an ability.

        Two separate numbers on purpose, and the whole point of the tab: the
        steppers move the first one and the restore action copies the second
        onto it.  Neither ever writes the default.
        """
        card = self.state.ability_card(entry.title)
        if card is None:
            return (0, 0)
        return (int(card.uses_left or 0), int(card.uses_total or 0))

    # ── opening and closing ──────────────────────────────────────────────────
    def open(self) -> None:
        self.active = True
        self.appear = 0.0
        self.notice = ""
        self.notice_ok = False
        self.notice_left = 0.0

    def close(self) -> None:
        self.active = False
        self.hover.clear()

    def toggle(self) -> None:
        self.close() if self.active else self.open()

    def notify(self, text: str, ok: bool = False) -> None:
        """Say something WHERE THE PLAYER IS LOOKING.

        The status bar says these normally, and the status bar is behind this
        overlay: a ``-`` that refuses because the last copies are in people's
        hands has to explain itself, or it reads as a dead button.  Since stage
        33 it also carries the confirmations, because 'Dobierz kartę' puts a
        card in a hand the library is sitting on top of — the one piece of
        feedback the player would otherwise have to close the window to see.
        """
        self.notice = text
        self.notice_ok = ok
        self.notice_left = NOTICE_SECONDS

    def notice_colour(self, theme):
        """Red for a refusal, green for a confirmation.

        A method rather than two literals inside the painter so the rule is one
        thing that can be asked a question — a test that checks the message is
        actually RED should not have to read pixels to find out.
        """
        return theme.valid if self.notice_ok else theme.warning

    # ── geometry ─────────────────────────────────────────────────────────────
    def _scale(self, layout: Layout) -> float:
        return layout.type_scale

    def rows(self, layout: Layout) -> int:
        columns = layout.card_library_columns
        return (len(self.entries) + columns - 1) // max(1, columns)

    @property
    def tab_has_variants(self) -> bool:
        """Whether the OPEN TAB needs a variant row under every card.

        Asked of the tab rather than of each card so the grid stays a grid;
        see ``Layout.card_library_cell_size``.
        """
        return self.tab.has_variants and not self.tab.abilities

    def max_scroll(self, layout: Layout) -> int:
        content = layout.card_library_content
        _, cell_h = layout.card_library_cell_size(self.tab.abilities,
                                                  self.tab_has_variants)
        return max(0, self.rows(layout) * cell_h - content.height)

    def _clamp_scroll(self, layout: Layout) -> None:
        self.scroll = max(0, min(self.max_scroll(layout), int(self.scroll)))

    def cell_rect(self, index: int, layout: Layout) -> pygame.Rect:
        return layout.card_library_cell_rect(index, self.tab.abilities,
                                             self.scroll,
                                             self.tab_has_variants)

    def card_rect(self, index: int, layout: Layout) -> pygame.Rect:
        """The card itself inside its cell: centred, under the owner band."""
        cell = self.cell_rect(index, layout)
        card_w, card_h = layout.card_library_card_size
        top = cell.top + layout.card_library_gap // 2
        if self.tab.abilities:
            top += layout.card_library_owner_h
        return pygame.Rect(cell.centerx - card_w // 2, top, card_w, card_h)

    def _rows_under_card(self, index: int, layout: Layout) -> Dict[str, pygame.Rect]:
        """Everything drawn BELOW a card, as named boxes.

        One function for the painter and for the hit test, so a stepper that
        looks clicked is the stepper that was clicked at any scroll position.
        """
        card = self.card_rect(index, layout)
        cell = self.cell_rect(index, layout)
        label_h = layout.card_library_label_h
        stepper_h = layout.card_library_stepper_h
        button_h = layout.card_library_button_h
        boxes: Dict[str, pygame.Rect] = {}
        y = card.bottom
        if self.tab.abilities:
            boxes["owner"] = pygame.Rect(cell.left, card.top -
                                         layout.card_library_owner_h,
                                         cell.width,
                                         layout.card_library_owner_h)
            width = min(cell.width - layout.card_library_gap, card.width)
            boxes["restore"] = pygame.Rect(cell.centerx - width // 2,
                                           y + max(2, label_h // 4),
                                           width, button_h)
            y = boxes["restore"].bottom
        boxes["label"] = pygame.Rect(cell.left, y, cell.width, label_h)
        boxes["stepper"] = pygame.Rect(cell.left, boxes["label"].bottom,
                                       cell.width, stepper_h)
        if not self.tab.abilities:
            # 'Dobierz kartę' (stage 33).  Under the stepper, the same width as
            # the card above it, so it reads as belonging to that card and to
            # no other — at four columns the neighbours are close.
            width = min(cell.width - layout.card_library_gap, card.width)
            boxes["draw"] = pygame.Rect(cell.centerx - width // 2,
                                        boxes["stepper"].bottom,
                                        width, layout.card_library_button_h)
            if self.tab_has_variants:
                # UNDER everything, and never over the card: the variant is a
                # setting about the card, and stage 31's rule is that nothing
                # the project controls goes on a card face.  The box is
                # reserved for every cell in the tab so the columns line up;
                # only a card that HAS variants draws in it.
                boxes["variant"] = pygame.Rect(cell.centerx - width // 2,
                                               boxes["draw"].bottom,
                                               width,
                                               layout.card_library_button_h)
        return boxes

    def _stepper(self, box: pygame.Rect, layout: Layout) -> Optional[Stepper]:
        """A ``[-] n [+]`` row centred in ``box``.

        Built on demand rather than kept in a list: the grid scrolls in pixels,
        so the rows a stepper would be zipped against change on every wheel
        notch.  The settings panel keeps a stepper per VISIBLE ROW and indexes
        by position for exactly the same reason — this is that rule with a
        finer-grained scroll.
        """
        if self.r is None:
            return None
        scale = self._scale(layout)
        stepper = Stepper(box.centerx, box.top,
                          button_w=int(30 * scale), value_w=int(44 * scale),
                          gap=max(4, int(5 * scale)), r=self.r)
        # Centre the measured row inside the band the layout reserved for it.
        offset = (box.height - stepper.height) // 2
        if offset:
            for rect in stepper.rects.values():
                rect.y += offset
            stepper.y += offset
        return stepper

    def _visible_indices(self, layout: Layout) -> List[int]:
        """Only the cells the viewport can actually show.

        The movement tab is thirty cards; building steppers for all of them
        every frame is work nobody sees, and hit-testing them would let a click
        below the viewport reach a card scrolled out of sight.
        """
        content = layout.card_library_content
        out: List[int] = []
        for index in range(len(self.entries)):
            if self.cell_rect(index, layout).colliderect(content):
                out.append(index)
        return out

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int],
                     layout: Layout) -> bool:
        """Returns True for everything while open — the overlay IS modal.

        Consuming events it has no use for is the point: the table underneath
        is a live game, and a click that fell through would play a card the
        player cannot see.
        """
        if not self.active:
            return False

        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
                self.close()
            elif event.key == pygame.K_TAB:
                self.select_tab(self.tabs[(self.tab_index + 1)
                                          % len(self.tabs)].id)
            elif event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
                self.scroll += self._wheel_step(layout)
            elif event.key in (pygame.K_UP, pygame.K_PAGEUP):
                self.scroll -= self._wheel_step(layout)
            elif event.key == pygame.K_HOME:
                self.scroll = 0
            elif event.key == pygame.K_END:
                self.scroll = self.max_scroll(layout)
            self._clamp_scroll(layout)
            return True

        if event.type == pygame.MOUSEWHEEL:
            self.scroll -= event.y * self._wheel_step(layout)
            self._clamp_scroll(layout)
            return True

        if event.type != pygame.MOUSEBUTTONDOWN:
            return True
        if event.button in (4, 5):
            self.scroll += (-1 if event.button == 4 else 1) * self._wheel_step(layout)
            self._clamp_scroll(layout)
            return True
        if event.button != 1:
            return True

        for index, rect in enumerate(self.tab_rects):
            if rect.collidepoint(mouse):
                self.select_tab(self.tabs[index].id)
                self._clamp_scroll(layout)
                return True

        if layout.card_library_content.collidepoint(mouse):
            self._click_grid(mouse, layout)
            return True

        if self.close_button.hit(mouse) or not layout.card_library_panel.collidepoint(mouse):
            self.close()
        return True

    def _wheel_step(self, layout: Layout) -> int:
        _, cell_h = layout.card_library_cell_size(self.tab.abilities,
                                                  self.tab_has_variants)
        return max(24, int(cell_h * WHEEL_FRACTION))

    def _click_grid(self, mouse: Tuple[int, int], layout: Layout) -> None:
        """Turn a click in the grid into a Command, or into nothing."""
        for index in self._visible_indices(layout):
            boxes = self._rows_under_card(index, layout)
            entry = self.entries[index]
            restore = boxes.get("restore")
            if restore is not None and restore.collidepoint(mouse):
                self.submit(cmd.RestoreAbilityUses(title=entry.title))
                return
            variant_box = boxes.get("variant")
            if (variant_box is not None and entry.card.has_variants
                    and variant_box.collidepoint(mouse)):
                self._cycle_variant(entry)
                return
            fetch = boxes.get("draw")
            if fetch is not None and fetch.collidepoint(mouse):
                # The seat SHOWN, which is the hand that is about to change and
                # the same seat the hand fan plays from.  In a network match
                # that is this machine's own seat and nothing else is
                # authorised anyway.
                self.submit(cmd.DrawTitledCard(player_index=self.seat(),
                                               deck_id=self.tab.deck_id,
                                               title=entry.title))
                return
            stepper = self._stepper(boxes["stepper"], layout)
            delta = stepper.hit(mouse) if stepper is not None else None
            if delta:
                self._bump(entry, 1 if delta > 0 else -1)
                return

    def _next_variant(self, entry: LibraryEntry) -> str:
        """The variant after the one showing, wrapping round.

        The CONTROL cycles, but the COMMAND it sends names the variant it
        wants: two players clicking at the same moment must not end up
        somewhere neither of them chose, and an absolute id is what makes the
        second click a no-op instead of a second step.
        """
        ids = list(entry.card.definition.printed.variant_ids)
        if not ids:
            return ""
        current = self.state.card_variant(entry.card.deck_id, entry.title)
        index = ids.index(current) if current in ids else 0
        return ids[(index + 1) % len(ids)]

    def _cycle_variant(self, entry: LibraryEntry) -> None:
        """Ask for the next variant.  Changes nothing here — see :meth:`_bump`.

        The setting belongs to the MATCH, so it leaves as a Command and comes
        back as a change to the game state, exactly as a deck count does.  The
        base card definition in cards.json is not involved and is not edited.
        """
        wanted = self._next_variant(entry)
        if wanted:
            self.submit(cmd.SetCardVariant(deck_id=entry.card.deck_id,
                                           title=entry.title,
                                           variant=wanted))

    def _bump(self, entry: LibraryEntry, delta: int) -> None:
        """The ONLY place this file changes anything, and it changes nothing.

        It builds a command and hands it to the session.  The engine decides
        whether it is legal, the server decides whether it is allowed, and the
        number under the card moves when the change comes back — the same
        no-optimistic-prediction rule the rest of the game plays by.
        """
        if self.tab.abilities:
            self.submit(cmd.AdjustAbilityUses(title=entry.title, delta=delta))
        else:
            self.submit(cmd.AdjustDeckCount(deck_id=self.tab.deck_id,
                                            title=entry.title, delta=delta))

    # ── frame ────────────────────────────────────────────────────────────────
    def _refresh_variants(self) -> None:
        """Rebuild any display card the match no longer reads the way it does.

        The library holds no settings of its own; the display cards are the one
        thing it caches, and only because a card needs an object to be drawn
        from.  So this is the same rule as every number in this file — read the
        live state, and follow it — applied to the one cached thing.  A card
        whose variant has not moved is left alone, so this costs nothing on the
        frames where nothing happened.
        """
        for tab in self.tabs:
            for index, entry in enumerate(tab.entries):
                if not entry.card.has_variants:
                    continue
                current = self.state.card_variant(entry.card.deck_id,
                                                  entry.title)
                if current and current != entry.variant:
                    # ``printed`` leads back to the CHARACTER definition even
                    # when the face on screen is the ability's, because
                    # ``ability_face`` renames without disturbing ``base`` —
                    # so the rebuild starts from the same place the first build
                    # did, and has to ask for the ability face again.
                    definition = entry.card.definition.printed
                    tab.entries[index] = self._entry_for(
                        definition, as_ability=tab.abilities,
                        owner=entry.owner, ability=entry.ability)

    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self._refresh_variants()
        self.appear = approach(self.appear, 1.0, 12.0, dt)
        self.close_button.update(mouse, dt)
        if self.notice_left > 0.0:
            self.notice_left = max(0.0, self.notice_left - dt)
        content = layout.card_library_content
        inside = content.collidepoint(mouse)
        for index in self._visible_indices(layout):
            entry = self.entries[index]
            key = (self.tab.id, entry.title)
            # The whole CELL, not just the card.  Stage 32 used the card, which
            # was fine when nothing sat under it; with 'Dobierz kartę' there,
            # reaching for the button dropped the reveal on the way down and
            # the description flickered shut just as the player went to act on
            # it.  A cell is one entry, so this reads as "this entry is the one
            # you are working with" and holds still while you work with it.
            rect = self.cell_rect(index, layout)
            wanted = 1.0 if (inside and rect.collidepoint(mouse)) else 0.0
            self.hover[key] = approach(self.hover.get(key, 0.0), wanted,
                                       16.0, dt)
        self._clamp_scroll(layout)

    def hover_of(self, entry: LibraryEntry) -> float:
        return self.hover.get((self.tab.id, entry.title), 0.0)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.r = r
        theme = r.theme
        scale = self._scale(layout)
        panel = layout.card_library_panel

        # The table stays visible underneath, dimmed — the convention every
        # other overlay in this game follows.
        shade = pygame.Surface((layout.win_w, layout.win_h), pygame.SRCALPHA)
        shade.fill((6, 10, 8, int(170 * min(1.0, self.appear))))
        surface.blit(shade, (0, 0))

        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.35, shadow=22)
        r.spaced_text(self.tab.heading, r.fonts.get(int(19 * scale), bold=True),
                      theme.text_heading, surface,
                      center=(panel.centerx, panel.top + int(26 * scale)),
                      spacing=3, shadow=True)
        self._draw_tabs(r, layout, surface, mouse, scale)
        self._draw_grid(r, cards, layout, surface, mouse, scale)
        self._draw_scrollbar(r, layout, surface)
        self._draw_footer(r, layout, surface, mouse, scale)

    def _draw_tabs(self, r: Renderer, layout: Layout, surface: pygame.Surface,
                   mouse: Tuple[int, int], scale: float) -> None:
        """The tab strip, measured from its captions.

        Divided evenly it clips "Umiejętności" at 1280x760 — the settings panel
        learned this and the fix is the same one.
        """
        theme = r.theme
        panel = layout.card_library_panel
        font = r.fonts.get(int(14 * scale), bold=True)
        pad = int(16 * scale)
        margin = layout.card_library_pad
        widths = [font.size(tab.label)[0] + 2 * pad for tab in self.tabs]
        room = panel.width - 2 * margin
        if sum(widths) > room:
            shrink = room / max(1, sum(widths))
            widths = [max(int(44 * scale), int(w * shrink)) for w in widths]
        x = panel.centerx - sum(widths) // 2
        top = layout.card_library_content.top - int(38 * scale)
        height = int(30 * scale)
        self.tab_rects = []
        for index, (tab, width) in enumerate(zip(self.tabs, widths)):
            rect = pygame.Rect(x, top, width, height)
            self.tab_rects.append(rect)
            x += width
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

    def _draw_grid(self, r: Renderer, cards: CardRenderer, layout: Layout,
                   surface: pygame.Surface, mouse: Tuple[int, int],
                   scale: float) -> None:
        """The cards themselves, clipped to the viewport.

        CLIPPED, not merely skipped: a card is most of a row, so the row being
        scrolled into view is always half outside the viewport and has to be
        cut off rather than left to paint over the tab strip.  This is the same
        ``set_clip`` the board view uses for its particles.
        """
        theme = r.theme
        content = layout.card_library_content
        old_clip = surface.get_clip()
        surface.set_clip(content)
        for index in self._visible_indices(layout):
            entry = self.entries[index]
            hover = self.hover_of(entry)
            rect = self.card_rect(index, layout)
            boxes = self._rows_under_card(index, layout)
            if self.tab.abilities:
                self._draw_owner(r, surface, boxes["owner"], entry, scale)
            self._draw_card(r, cards, surface, rect, entry, hover)
            if self.tab.abilities:
                self._draw_ability_controls(r, layout, surface, boxes, entry,
                                            mouse, scale)
            else:
                self._draw_deck_controls(r, layout, surface, boxes, entry,
                                         mouse, scale)
        surface.set_clip(old_clip)

    def _draw_card(self, r: Renderer, cards: CardRenderer,
                   surface: pygame.Surface, rect: pygame.Rect,
                   entry: LibraryEntry, hover: float) -> None:
        """One card, through the ordinary renderer and nothing else.

        ``reveal`` is the hover level as a FLOAT, which is what the hand fan
        passes: a Signature card therefore darkens its artwork and slides its
        description up on the same curve here as it does in the hand, and a
        card with no artwork ignores it and takes its emphasis from
        ``highlighted`` and the halo — exactly as it does everywhere else.
        Nothing is drawn on top of the result.
        """
        theme = r.theme
        border = theme.deck_colors.get(entry.card.deck_id)
        lifted = rect.move(0, -int(rect.height * 0.02 * hover))
        if hover > 0.02:
            r.shape_glow(lifted, border or theme.brass_light, surface,
                         radius=10, strength=0.35 + 0.35 * hover)
        cards.draw_in(entry.card, lifted, surface, highlighted=hover > 0.5,
                      border_color=border, reveal=hover)

    def _draw_owner(self, r: Renderer, surface: pygame.Surface,
                    box: pygame.Rect, entry: LibraryEntry,
                    scale: float) -> None:
        """WHO owns this ability.

        Just the character, because since stage 49 the CARD carries the
        ability's own name on its face.  This used to print the ability
        underneath the owner as well, and it had to: the face said "Big D
        Randy", so "Granny Costume" appeared nowhere in the interface unless
        this line drew it.  Now that the face is right, repeating it here would
        be the same word twice in two sizes.

        The split it stands for has not changed and is the point of the tab —
        above the card is WHO, on the card is WHAT.
        """
        theme = r.theme
        r.text(entry.owner, r.fonts.get(int(16 * scale), bold=True),
               theme.brass_bright, surface,
               center=(box.centerx, box.centery))

    def _draw_deck_controls(self, r: Renderer, layout: Layout,
                            surface: pygame.Surface,
                            boxes: Dict[str, pygame.Rect], entry: LibraryEntry,
                            mouse: Tuple[int, int], scale: float) -> None:
        count = self.count_of(entry)
        r.text(f"{count} w talii", r.fonts.get(int(14 * scale)),
               r.theme.text_light, surface,
               center=(boxes["label"].centerx, boxes["label"].centery))
        stepper = self._stepper(boxes["stepper"], layout)
        if stepper is not None:
            stepper.draw(r, str(count), mouse, surface)
        self._draw_action(r, surface, boxes["draw"], "DOBIERZ KARTĘ", mouse)
        box = boxes.get("variant")
        if box is not None and entry.card.has_variants:
            # ONLY for a card that has variants.  The room is reserved for
            # every cell in the tab, but an ordinary card draws nothing in it:
            # a selector offering one option is a control that cannot do
            # anything, and twenty-nine of the thirty-one cards have none.
            self._draw_action(r, surface, box, self._variant_caption(entry),
                              mouse, selected=True)

    def _variant_caption(self, entry: LibraryEntry) -> str:
        """What the variant button says: the reading in force, and how many.

        "WARIANT 2/2" rather than a bare name, because the player needs to know
        both which one is on and that there is another — the description itself
        is on the card face above, which is where a card's text belongs.
        """
        printed = entry.card.definition.printed
        ids = list(printed.variant_ids)
        current = self.state.card_variant(entry.card.deck_id, entry.title)
        index = (ids.index(current) if current in ids else 0) + 1
        chosen = printed.variant_def(current)
        label = (chosen.label if chosen and chosen.label
                 else f"WARIANT {index}")
        return f"{label.upper()}  ({index}/{len(ids)})"

    def _draw_action(self, r: Renderer, surface: pygame.Surface,
                     rect: pygame.Rect, caption: str,
                     mouse: Tuple[int, int], selected: bool = False) -> None:
        """An action button under a card.

        Stateless, taking its hover straight from the cursor: the grid scrolls
        in pixels, so a Button object kept per entry would need its rectangle
        rewritten every frame anyway, and the glow it exists to animate would
        be animating on a control that had moved underneath it.
        """
        theme = r.theme
        style = r.emphasis(fill=theme.btn_idle_bg, border=theme.btn_idle_border,
                           text=theme.btn_text,
                           hover=1.0 if rect.collidepoint(mouse) else 0.0,
                           selected=selected, enabled=True,
                           accent=theme.brass_light)
        drawn = r.interactive_panel(rect, style, surface, radius=8)
        r.fit_spaced_text(caption, drawn, style.text, surface, spacing=1,
                          padding=8, min_size=8, height_ratio=0.44)

    def _draw_ability_controls(self, r: Renderer, layout: Layout,
                               surface: pygame.Surface,
                               boxes: Dict[str, pygame.Rect],
                               entry: LibraryEntry, mouse: Tuple[int, int],
                               scale: float) -> None:
        theme = r.theme
        left, default = self.uses_of(entry)
        # "Odśwież użycia" said what it does; "Przywróć użycia (n)" says what
        # it will leave behind, which is the number the player is deciding
        # about — and it borrows "użyć" from the ability button in the HUD
        # rather than introducing a third word for the same thing.
        self._draw_action(r, surface, boxes["restore"],
                          f"PRZYWRÓĆ UŻYCIA ({default})", mouse)
        r.text("Ilość użyć", r.fonts.get(int(14 * scale)), theme.text_dim,
               surface, center=(boxes["label"].centerx,
                                boxes["label"].centery))
        stepper = self._stepper(boxes["stepper"], layout)
        if stepper is not None:
            stepper.draw(r, str(left), mouse, surface)

    def _draw_scrollbar(self, r: Renderer, layout: Layout,
                        surface: pygame.Surface) -> None:
        """Track and thumb, drawn only when there is something to scroll.

        No hit-testing, for the reason the settings panel gives: the wheel and
        the keys do the scrolling, and a draggable thumb is a widget to
        maintain for a gesture nobody reaches for here.
        """
        theme = r.theme
        maximum = self.max_scroll(layout)
        if maximum <= 0:
            return
        track = layout.card_library_scrollbar
        content = layout.card_library_content
        pygame.draw.rect(surface, theme.panel_inset, track,
                         border_radius=track.width)
        share = content.height / max(1, content.height + maximum)
        thumb_h = max(int(24), int(track.height * share))
        travel = track.height - thumb_h
        offset = int(travel * (self.scroll / maximum))
        pygame.draw.rect(surface, theme.brass,
                         pygame.Rect(track.left, track.top + offset,
                                     track.width, thumb_h),
                         border_radius=track.width)

    def _draw_footer(self, r: Renderer, layout: Layout,
                     surface: pygame.Surface, mouse: Tuple[int, int],
                     scale: float) -> None:
        theme = r.theme
        panel = layout.card_library_panel
        self.close_button.rect = layout.card_library_close_rect()
        self.close_button.draw(r, surface)
        # Anchored to the CONTENT's bottom edge, not to the button's top: the
        # button sizes itself to its caption, so hanging a line off it puts
        # that line wherever the caption happens to leave it — which was over
        # the last row of cards.
        line_y = layout.card_library_content.bottom + int(5 * scale)
        if self.notice and self.notice_left > 0.0:
            r.text(self.notice, r.fonts.get(int(13 * scale)),
                   self.notice_colour(theme),
                   surface, midtop=(panel.centerx, line_y))
            return
        count = len(self.entries)
        hint = (f"{count} kart  ·  kółko myszy przewija  ·  Esc zamyka"
                if not self.tab.abilities
                else f"{count} umiejętności  ·  kółko myszy przewija"
                     "  ·  Esc zamyka")
        r.text(hint, r.fonts.get(int(12 * scale)), theme.text_dim, surface,
               midtop=(panel.centerx, line_y))


def draw_library_button(r: Renderer, layout: Layout, surface: pygame.Surface,
                        mouse: Tuple[int, int], open_now: bool = False) -> pygame.Rect:
    """The book in the bottom-right corner.

    Drawn rather than blitted from an asset, for the reason every other control
    in this interface is: the game ships without art files and must look
    finished anyway.  It reads as a BOOK — a spine, two covers and three page
    edges — so it is not mistaken for one more square gameplay button, and it
    takes its hover from the same :meth:`Renderer.emphasis` every button uses.
    """
    theme = r.theme
    rect = layout.card_library_button
    hovered = rect.collidepoint(mouse)
    style = r.emphasis(fill=theme.panel_bg_light, border=theme.brass_light,
                       text=theme.brass_bright,
                       hover=1.0 if hovered else 0.0,
                       selected=open_now, enabled=True,
                       accent=theme.brass_light)
    drawn = r.interactive_panel(rect, style, surface, radius=8)

    inner = drawn.inflate(-drawn.width // 4, -drawn.height // 4)
    spine = max(2, inner.width // 12)
    # A closed book seen from the front: two solid leaves either side of a
    # spine, with darker rules across them for pages.  Everything is drawn in
    # the button's OWN text colour and a darkened copy of it — never in
    # ``panel_bg``, which is the colour that means "nothing has been painted
    # here" to ``test_the_world_fills_the_viewport_even_when_zoomed_right_out``.
    pages = darken(style.text, 0.45)
    for direction in (-1, 1):
        leaf = pygame.Rect(0, 0, max(3, inner.width // 2 - spine), inner.height)
        if direction < 0:
            leaf.midright = (inner.centerx - spine // 2, inner.centery)
        else:
            leaf.midleft = (inner.centerx + spine // 2, inner.centery)
        pygame.draw.rect(surface, style.text, leaf, border_radius=max(2, spine))
        for line in range(3):
            y = leaf.top + leaf.height * (line + 1) // 4
            pygame.draw.line(surface, pages,
                             (leaf.left + spine, y), (leaf.right - spine, y),
                             max(1, spine // 2))
    pygame.draw.rect(surface, pages,
                     pygame.Rect(inner.centerx - spine // 2, inner.top,
                                 max(2, spine), inner.height))
    return drawn
