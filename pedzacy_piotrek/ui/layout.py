"""
Screen layout.

Every rectangle in the game screen is computed here, from two numbers: the
current window width and height.  Nothing else in the interface contains a
hard-coded coordinate.

This replaces the previous fixed *design resolution*.  That kept proportions
honest but scaled the whole picture like an image, so on a 2560×1440 display
the game was a blurry enlargement of a 2273×969 canvas.  Now the window size
flows through the layout: panels keep their share of the screen, card sizes are
derived from the space actually available, and the board — which this stage
makes the centre of the game — simply gets bigger on a bigger monitor.

Region map::

    ┌──────────┬────────────────────────────────┬──────────────┐
    │  mods    │  turn order + round counter    │  character   │
    │  +       ├────────────────────────────────┤  + ability   │
    │  decks   │  player list                   │  + colours   │
    │          ├────────────────────────────────┤              │
    │          │  BOARD                         │              │
    ├──────────┴────────────────────────────────┴──────────────┤
    │                    hand fan (full width)                 │
    └──────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pygame

from ..config import settings
from ..config.settings import RULES

#: Cards keep this aspect ratio wherever they are drawn.
CARD_ASPECT = 200 / 140


def _clamp(value: float, low: float, high: float) -> int:
    return int(max(low, min(high, value)))


class Layout:
    """Every rectangle in the game screen, derived from the window size."""

    def __init__(self, width: Optional[int] = None, height: Optional[int] = None) -> None:
        self.mod_slot_count = RULES.mod_slots
        self.turn_circle_actual_d = 0
        self.turn_arrow_actual_w = 0
        self.resize(
            width or settings.PREFERRED_WINDOW[0],
            height or settings.PREFERRED_WINDOW[1],
        )

    # ── computation ──────────────────────────────────────────────────────────
    def resize(self, width: int, height: int) -> None:
        """Recompute everything.  Called at startup and on every window resize."""
        self.win_w = max(settings.MIN_WINDOW[0], int(width))
        self.win_h = max(settings.MIN_WINDOW[1], int(height))
        w, h = self.win_w, self.win_h

        #: Everything quoted in pixels is quoted against this factor, fonts
        #: included, so a bigger screen gets bigger *rendered* type rather than
        #: a stretched bitmap.
        #:
        #: The reference height is 1000, not 1080 (stage 26).  Keying it to
        #: 1080 meant a 1920x1200 laptop — a physically SMALL panel with a lot
        #: of pixels — ran at 1.11 and was reported as hard to read, while
        #: 2560x1440 on a desktop monitor was comfortable at 1.33.  Moving the
        #: reference lifts the middle of the range by about 8% and leaves both
        #: ends where they were: 1280x760 is still on the floor, and 4K still
        #: hits the ceiling.
        self.ui_scale = max(0.85, min(1.8, h / 1000.0))

        self.pad = _clamp(w * 0.008, 8, 18)
        pad = self.pad

        #: The hand is where the cards are READ, so its height is what decides
        #: how big a card is anywhere: the side columns cap themselves against
        #: it too.  The ceiling scales rather than being a flat 300, which had
        #: made a 4K screen show exactly the same 182x260 card as a 1920x1200
        #: laptop — every pixel past 1224 tall went into margin.
        self.hand_h = _clamp(h * 0.245, 180, 300 * self.ui_scale)
        self.content_h = h - self.hand_h - 2 * pad
        self.turn_bar_h = _clamp(h * 0.075, 58, 96)
        self.player_strip_h = _clamp(h * 0.095, 76, 120)

        # The board is the game, so the columns are measured rather than
        # guessed: the cards are sized from the height available, the column is
        # made exactly wide enough to hold them, and everything left over goes
        # to the board instead of sitting as margin.
        self.left_w = self._fit_left_column()
        self.right_w = self._fit_right_column()
        self.centre_x = pad + self.left_w + pad
        self.centre_w = w - self.left_w - self.right_w - 4 * pad

        self._compute_left()
        self._compute_centre()
        self._compute_right()
        self._compute_hand()

    # ── column widths follow their contents ──────────────────────────────────
    def _column_metrics(self, sections: int) -> Tuple[int, int, int]:
        """Card size and padding for a column of ``sections`` stacked rows."""
        inner = _clamp(10 * self.ui_scale, 6, 14)
        gap = _clamp(12 * self.ui_scale, 6, 14)
        # Same band, same scaling ceiling as ``_compute_left`` — the column
        # WIDTH is budgeted here and the height is spent there, so the two have
        # to agree about how tall a label line is.
        line_h = _clamp(self.content_h * 0.021 * self.ui_scale,
                        16, 26 * self.ui_scale)
        section_gap = _clamp(self.content_h * 0.012, 6, 16)

        available = (self.content_h - 2 * inner
                     - sections * (line_h + 3) - (sections + 1) * section_gap)
        card_h = max(90.0, available / sections)
        # A side-panel card never needs to be bigger than a card in hand: past
        # that it is not more readable, only more of the board's space.
        card_h = min(card_h, self.hand_h * 0.78)
        card_w = int(card_h / CARD_ASPECT)
        return card_w, inner, gap

    def _fit_left_column(self) -> int:
        """Mods rack plus the three table decks, two cards wide."""
        card_w, inner, gap = self._column_metrics(1 + len(settings.TABLE_DECKS))
        #: A floor so the deck names and counters still have room to breathe.
        floor = int(230 * self.ui_scale)
        return _clamp(max(floor, 2 * card_w + gap + 2 * inner), 230, self.win_w * 0.22)

    def _fit_right_column(self) -> int:
        """Character, ability and its decks, plus the colour notepad.

        Counted as four rows rather than three: the heading, the ability button
        and the notepad together eat about a row's worth of height, and sizing
        for three made the column wide enough to matter to the board.
        """
        card_w, inner, gap = self._column_metrics(4)
        floor = int(248 * self.ui_scale)
        return _clamp(max(floor, 2 * card_w + gap + 2 * inner), 248, self.win_w * 0.23)

    # ── left column: mods rack and the three table decks ─────────────────────
    def _compute_left(self) -> None:
        panel = self.left_panel
        self.left_inner = _clamp(self.left_w * 0.035, 6, 14)
        self.section_gap = _clamp(self.content_h * 0.012, 6, 16)
        #: One line for the deck name, with its counters on the same row to the
        #: right.  Two stacked lines per deck spent 88 pixels of column on
        #: labels — pixels the cards themselves needed far more.
        #:
        #: The CEILING SCALES.  It was a flat 26, which stopped growing while
        #: the type inside it did not, so at 1440p the deck name overflowed its
        #: own band and touched the card below — a 2-pixel collision that a
        #: test caught the moment stage 26 made the cards bigger.  A band that
        #: cannot hold its own line is not a band.
        self.section_line_h = _clamp(self.content_h * 0.021 * self.ui_scale,
                                     16, 26 * self.ui_scale)
        self.section_label_h = self.section_line_h + 3

        inner_w = panel.width - 2 * self.left_inner
        self.left_card_gap = _clamp(inner_w * 0.05, 6, 14)

        # Four stacked sections: the mod rack plus one row per table deck.
        sections = 1 + len(settings.TABLE_DECKS)
        available_h = (
            panel.height
            - 2 * self.left_inner
            - sections * self.section_label_h
            - (sections + 1) * self.section_gap
        )
        by_height = available_h / sections
        by_width = (inner_w - self.left_card_gap) / 2 * CARD_ASPECT
        card_h = max(80.0, min(by_height, by_width))
        self.panel_card_size = (int(card_h / CARD_ASPECT), int(card_h))

        card_w, card_h = self.panel_card_size
        row_h = card_h + self.section_label_h + self.section_gap
        pair_w = 2 * card_w + self.left_card_gap
        self.left_pair_x = panel.left + self.left_inner + (inner_w - pair_w) // 2

        top = panel.top + self.left_inner + self.section_gap // 2
        self._mod_label_y = top
        self._mod_row_y = top + self.section_label_h
        self._deck_label_y = [
            top + (i + 1) * row_h for i in range(len(settings.TABLE_DECKS))
        ]
        self._deck_row_y = [y + self.section_label_h for y in self._deck_label_y]

    @property
    def left_panel(self) -> pygame.Rect:
        return pygame.Rect(self.pad, self.pad, self.left_w, self.content_h)

    def mod_slot_rect(self, slot: int) -> pygame.Rect:
        card_w, card_h = self.panel_card_size
        x = self.left_pair_x + slot * (card_w + self.left_card_gap)
        return pygame.Rect(x, self._mod_row_y, card_w, card_h)

    @property
    def mod_label_y(self) -> int:
        return self._mod_label_y

    def deck_label_y(self, column: int) -> int:
        return self._deck_label_y[column]

    def deck_draw_rect(self, column: int) -> pygame.Rect:
        card_w, card_h = self.panel_card_size
        return pygame.Rect(self.left_pair_x, self._deck_row_y[column], card_w, card_h)

    def deck_discard_rect(self, column: int) -> pygame.Rect:
        card_w, card_h = self.panel_card_size
        x = self.left_pair_x + card_w + self.left_card_gap
        return pygame.Rect(x, self._deck_row_y[column], card_w, card_h)

    # ── centre column: turn bar, player list, board ──────────────────────────
    def _compute_centre(self) -> None:
        self.turn_circle_d = _clamp(self.turn_bar_h * 0.72, 40, 72)
        self.turn_arrow_w = _clamp(self.turn_circle_d * 0.42, 18, 40)
        self.counter_big_d = _clamp(self.turn_bar_h * 0.62, 38, 62)
        self.counter_small_d = _clamp(self.counter_big_d * 0.62, 24, 40)
        self.counter_gap = 8
        self.chest_box_w = _clamp(self.centre_w * 0.17, 130, 230)
        #: Gap between cards in a picker row.  Scales, like every other space.
        self.picker_gap = _clamp(20 * self.ui_scale, 12, 34)
        self.rename_mark = _clamp(self.player_strip_h * 0.2, 14, 22)
        self.tile_gap = _clamp(self.centre_w * 0.008, 6, 14)
        self.turn_circle_actual_d = self.turn_circle_d
        self.turn_arrow_actual_w = self.turn_arrow_w

    @property
    def turn_bar(self) -> pygame.Rect:
        return pygame.Rect(self.centre_x, self.pad, self.centre_w, self.turn_bar_h)

    @property
    def player_strip(self) -> pygame.Rect:
        return pygame.Rect(
            self.centre_x,
            self.turn_bar.bottom + self.pad,
            self.centre_w,
            self.player_strip_h,
        )

    @property
    def board_viewport(self) -> pygame.Rect:
        top = self.player_strip.bottom + self.pad
        bottom = self.pad + self.content_h
        return pygame.Rect(self.centre_x, top, self.centre_w, max(140, bottom - top))

    @property
    def chest_box(self) -> pygame.Rect:
        bar = self.turn_bar
        h = int(bar.height * 0.74)
        return pygame.Rect(bar.left + 10, bar.centery - h // 2, self.chest_box_w, h)

    def round_counter_rects(self) -> Dict[str, pygame.Rect]:
        bar = self.turn_bar
        cy = bar.centery
        plus = pygame.Rect(0, 0, self.counter_small_d, self.counter_small_d)
        plus.centery = cy
        plus.right = bar.right - 14
        big = pygame.Rect(0, 0, self.counter_big_d, self.counter_big_d)
        big.centery = cy
        big.right = plus.left - self.counter_gap
        minus = pygame.Rect(0, 0, self.counter_small_d, self.counter_small_d)
        minus.centery = cy
        minus.right = big.left - self.counter_gap
        return {"minus": minus, "big": big, "plus": plus}

    def turn_circle_rects(self, count: int) -> List[pygame.Rect]:
        """Turn-order circles, shrunk to fit however many slots the round has."""
        if count <= 0:
            return []
        left = self.chest_box.right + 16
        right = self.round_counter_rects()["minus"].left - 16
        span = max(80, right - left)
        diameter, arrow = self.turn_circle_d, self.turn_arrow_w
        total = count * diameter + (count - 1) * arrow
        if total > span:
            shrink = span / total
            diameter = max(24, int(diameter * shrink))
            arrow = max(6, int(arrow * shrink))
            total = count * diameter + (count - 1) * arrow
        self.turn_circle_actual_d = diameter
        self.turn_arrow_actual_w = arrow

        start_x = left + max(0, (span - total) // 2)
        cy = self.turn_bar.centery
        return [
            pygame.Rect(start_x + i * (diameter + arrow), cy - diameter // 2,
                        diameter, diameter)
            for i in range(count)
        ]

    def player_tile_rect(self, index: int, count: int) -> pygame.Rect:
        strip = self.player_strip
        count = max(1, count)
        gap = self.tile_gap
        width = (strip.width - (count + 1) * gap) // count
        x = strip.left + gap + index * (width + gap)
        return pygame.Rect(x, strip.top + 4, width, strip.height - 8)

    def player_tile_width(self, count: int) -> int:
        return self.player_tile_rect(0, count).width

    def rename_rect(self, tile: pygame.Rect) -> pygame.Rect:
        size = self.rename_mark
        return pygame.Rect(tile.right - size - 7, tile.top + 7, size, size)

    # ── recently played strip (floats over the board) ────────────────────────
    def recent_card_size(self) -> Tuple[int, int]:
        card_h = _clamp(self.board_viewport.height * 0.24, 86, 165)
        return (int(card_h / CARD_ASPECT), int(card_h))

    def recent_slot_rect(self, index: int) -> pygame.Rect:
        """Slots run right-to-left from the board's top-right corner."""
        card_w, card_h = self.recent_card_size()
        board = self.board_viewport
        gap = 8
        x = board.right - 14 - (index + 1) * card_w - index * gap
        return pygame.Rect(x, board.top + 30, card_w, card_h)

    @property
    def recent_label_pos(self) -> Tuple[int, int]:
        board = self.board_viewport
        return (board.right - 14, board.top + 10)

    # ── board controls ───────────────────────────────────────────────────────
    @property
    def zoom_slider(self) -> pygame.Rect:
        board = self.board_viewport
        width = _clamp(board.width * 0.22, 120, 260)
        return pygame.Rect(board.left + 46, board.bottom - 22, width, 7)

    @property
    def end_turn_button(self) -> pygame.Rect:
        """'End turn', bottom-right of the board.

        Near the board rather than the hand: it is the same kind of act as
        moving a pawn, and the hand area belongs to the cards.
        """
        board = self.board_viewport
        width = _clamp(board.width * 0.16, 150, 260)
        height = _clamp(34 * self.ui_scale, 30, 52)
        return pygame.Rect(board.right - width - 16, board.bottom - height - 16,
                           width, height)

    @property
    def return_seat_button(self) -> pygame.Rect:
        """'Back to my player', shown only while looking at somebody else's."""
        board = self.board_viewport
        width = _clamp(board.width * 0.22, 200, 300)
        return pygame.Rect(board.centerx - width // 2, board.top + 52, width, 30)

    @property
    def status_bar(self) -> pygame.Rect:
        # Stops short of the "end turn" button in the bottom-right corner.
        board = self.board_viewport
        width = board.width - 32 - self.end_turn_button.width
        return pygame.Rect(board.left + 8, board.bottom - 52, max(120, width), 22)

    # ── right column: character, ability, colours ────────────────────────────
    def _compute_right(self) -> None:
        panel = self.right_panel
        self.right_inner = _clamp(self.right_w * 0.04, 6, 14)
        inner_w = panel.width - 2 * self.right_inner

        self.r_title_h = int(20 * self.ui_scale)
        self.r_name_h = int(26 * self.ui_scale)
        #: Piotrek's own colour badge, above 'Twoja Postać'.  Reserved whenever
        #: the panel is Piotrek's, chosen colour or not, so the geometry depends
        #: on ONE thing (show_skill) that both the drawing and the click
        #: handling already have.  Making it depend on whether a colour is known
        #: yet would let the two drift apart by a few pixels and put the ability
        #: card just out of reach of its own rectangle.
        self.r_identity_h = int(24 * self.ui_scale)
        # Same single-line band as the left column: name plus counters.
        self.r_label_h = self.section_label_h
        #: Room under the ability card for the "use ability" button.
        self.r_button_h = _clamp(self.content_h * 0.035, 26, 42)
        self.r_gap = _clamp(self.content_h * 0.014, 6, 16)

        self.pk_circle_d = _clamp(inner_w * 0.19, 30, 56)
        self.pk_col_gap = _clamp(inner_w * 0.05, 8, 18)
        self.pk_row_gap = _clamp(inner_w * 0.045, 8, 16)
        self.pk_title_h = int(21 * self.ui_scale)
        self.pk_pad = int(10 * self.ui_scale)
        grid_h = self.pk_title_h + 2 * self.pk_circle_d + self.pk_row_gap + self.pk_pad

        # Vertical budget: title, name, ability card, the deck rows and the
        # colour notepad.  Sized per case rather than for the worst one —
        # Piotrek has a row more than a hunter, and making everybody pay for it
        # left a hunter's panel with a hand's width of unused space.
        self._right_sizes: Dict[bool, Tuple[int, int]] = {}
        self._right_pair_x: Dict[bool, int] = {}
        by_width = (inner_w - self.left_card_gap) / 2 * CARD_ASPECT
        for show_skill in (False, True):
            rows = 3.0 if show_skill else 2.0
            labels = 2 if show_skill else 1
            grid = 0 if show_skill else grid_h
            fixed = (
                2 * self.right_inner + self.r_title_h + self.r_name_h
                + labels * self.r_label_h + self.r_button_h + grid
                + (self.r_identity_h if show_skill else 0)
                + (6 if show_skill else 5) * self.r_gap
            )
            card_h = min(max(70.0, (panel.height - fixed) / rows), by_width)
            size = (int(card_h / CARD_ASPECT), int(card_h))
            self._right_sizes[show_skill] = size
            pair_w = 2 * size[0] + self.left_card_gap
            self._right_pair_x[show_skill] = panel.left + (panel.width - pair_w) // 2

        # Kept for callers that do not care which case they are in.
        self.right_card_size = self._right_sizes[True]
        self.right_pair_x = self._right_pair_x[True]

    def right_cards(self, show_skill: bool) -> Tuple[int, int]:
        return self._right_sizes[bool(show_skill)]

    def right_pair_left(self, show_skill: bool) -> int:
        return self._right_pair_x[bool(show_skill)]

    @property
    def right_panel(self) -> pygame.Rect:
        return pygame.Rect(
            self.win_w - self.pad - self.right_w, self.pad, self.right_w, self.content_h
        )

    def right_content_height(self, show_skill: bool) -> int:
        """Height of everything stacked in the right column.

        Used to centre it: whatever is left over after the cards have taken
        what they can is split evenly above and below.
        """
        card_h = self.right_cards(show_skill)[1]
        gap = self.r_gap
        grid_h = (
            self.pk_title_h + 2 * self.pk_circle_d + self.pk_row_gap + self.pk_pad
        )
        total = (self.r_title_h + self.r_name_h + gap // 2 + card_h
                 + self.r_button_h + gap)
        if show_skill:
            total += self.r_identity_h + self.r_label_h + card_h + gap
        total += self.r_label_h + card_h + gap
        if not show_skill:
            total += grid_h
        return total

    def right_content_offset(self, show_skill: bool) -> int:
        """Vertical nudge that centres the column's content in its panel."""
        panel = self.right_panel
        spare = panel.height - 2 * self.right_inner - self.right_content_height(show_skill)
        return max(0, spare // 2)

    def character_panel(self, show_skill: bool) -> Dict[str, object]:
        """Rects for the right-hand panel.

        When the active player is Piotrek an extra 'Umiejętności Piotrka' deck
        section is inserted between the ability card and 'Karty Postaci'.
        """
        panel = self.right_panel
        card_w, card_h = self.right_cards(show_skill)
        pair_x = self.right_pair_left(show_skill)
        gap = self.r_gap

        top = panel.top + self.right_inner + self.right_content_offset(show_skill)
        identity = pygame.Rect(panel.left + self.right_inner, top,
                               panel.width - 2 * self.right_inner,
                               self.r_identity_h)
        title_y = top + (self.r_identity_h if show_skill else 0)
        name_y = title_y + self.r_title_h
        card_y = name_y + self.r_name_h + gap // 2
        card_rect = pygame.Rect(panel.centerx - card_w // 2, card_y, card_w, card_h)

        rects: Dict[str, object] = {
            "identity": identity,
            "title_y": title_y, "name_y": name_y, "card": card_rect,
        }
        # The ability button lives directly under the card, so everything below
        # it has to make room — otherwise the next deck label sits on top of it.
        y = card_rect.bottom + self.r_button_h + gap

        if show_skill:
            rects["skill_label_y"] = y
            row_y = y + self.r_label_h
            rects["skill_draw"] = pygame.Rect(pair_x, row_y, card_w, card_h)
            rects["skill_disc"] = pygame.Rect(
                pair_x + card_w + self.left_card_gap, row_y, card_w, card_h
            )
            y = row_y + card_h + gap

        rects["char_label_y"] = y
        row_y = y + self.r_label_h
        rects["char_draw"] = pygame.Rect(pair_x, row_y, card_w, card_h)
        rects["char_disc"] = pygame.Rect(
            pair_x + card_w + self.left_card_gap, row_y, card_w, card_h
        )
        return rects

    def ability_button_rect(self, show_skill: bool) -> pygame.Rect:
        """The 'use ability' button, tucked under the ability card.

        Full inner width of the column: the caption is a long Polish phrase
        plus a use counter, and sizing the box to the card above it left the
        text with nowhere to go.  Every pixel here is already paid for by the
        panel, so there is nothing to save by making it narrower.
        """
        card = self.character_panel(show_skill)["card"]
        width = self.right_panel.width - 2 * self.right_inner
        width = max(width, min(int(card.width * 1.3), width))
        return pygame.Rect(
            self.right_panel.centerx - width // 2, card.bottom + 4,
            width, self.r_button_h - 6,
        )

    # ── overlays ─────────────────────────────────────────────────────────────
    @property
    def choice_prompt(self) -> pygame.Rect:
        """Strip along the bottom of the board where the engine asks a question."""
        board = self.board_viewport
        height = _clamp(board.height * 0.16, 92, 150)
        width = int(board.width * 0.82)
        return pygame.Rect(
            board.centerx - width // 2, board.bottom - height - 30, width, height
        )

    @property
    def reveal_card_size(self) -> Tuple[int, int]:
        """A card shown big in the middle of the screen (Gamechanger, reveals)."""
        height = _clamp(self.win_h * 0.42, 260, 560)
        return (int(height / CARD_ASPECT), int(height))

    @property
    def reveal_centre(self) -> Tuple[int, int]:
        board = self.board_viewport
        return (board.centerx, board.centery)

    # A row of cards laid out for the player to pick from.  Two things use it:
    # the chest hand limit and Spy looking into somebody else's hand.  The
    # geometry is written once because it is the same problem — "n cards side
    # by side, shrinking so they always fit" — and because the second use
    # arrived by copying the first, which is how two overlays end up drifting
    # apart at 4K.
    def card_picker_panel(self, count: int) -> pygame.Rect:
        card_w, card_h = self.card_picker_card_size(count)
        gap = self.picker_gap
        width = count * card_w + (count + 1) * gap
        height = card_h + 130
        return pygame.Rect(
            self.win_w // 2 - width // 2, self.win_h // 2 - height // 2, width, height
        )

    def card_picker_card_size(self, count: int) -> Tuple[int, int]:
        height = _clamp(self.win_h * 0.34, 200, 420)
        width = int(height / CARD_ASPECT)
        # Shrink if a wide spread would run off the screen.  A stolen hand can
        # hold eight cards where the chest limit never showed more than three,
        # so this branch is now the ordinary case rather than the safety net.
        available = self.win_w * 0.8 - (count + 1) * self.picker_gap
        if count * width > available:
            width = int(available / max(1, count))
            height = int(width * CARD_ASPECT)
        return (width, height)

    def card_picker_card_rect(self, index: int, count: int) -> pygame.Rect:
        panel = self.card_picker_panel(count)
        card_w, card_h = self.card_picker_card_size(count)
        x = panel.left + self.picker_gap + index * (card_w + self.picker_gap)
        return pygame.Rect(x, panel.top + 56, card_w, card_h)

    def card_picker_confirm_rect(self, count: int) -> pygame.Rect:
        panel = self.card_picker_panel(count)
        width = _clamp(self.win_w * 0.14, 200, 300)
        return pygame.Rect(panel.centerx - width // 2, panel.bottom - 56,
                           width, int(40 * self.ui_scale))

    # The chest limit keeps its own names: they are what the tests and the
    # overlay were written against, and "which chest cards do you keep" is not
    # the same question as "which card do you steal" even where the furniture
    # is.
    def chest_choice_panel(self, count: int) -> pygame.Rect:
        return self.card_picker_panel(count)

    def chest_choice_card_size(self, count: int) -> Tuple[int, int]:
        return self.card_picker_card_size(count)

    def chest_choice_card_rect(self, index: int, count: int) -> pygame.Rect:
        return self.card_picker_card_rect(index, count)

    def chest_confirm_rect(self, count: int) -> pygame.Rect:
        panel = self.card_picker_panel(count)
        return pygame.Rect(panel.centerx - 110, panel.bottom - 56, 220, 40)

    def elimination_card_rect(self) -> pygame.Rect:
        """Where the "not Piotrek" card appears — the board's left margin.

        A CARD, sized like one and placed where the eye already goes: hard
        against the inside of the board's left edge, vertically centred.  The
        old feedback was a line in the status bar in the bottom-left corner,
        which is the one part of the screen nobody watches while a tower is
        being lifted.

        Inside the board rather than in the left column because the column is
        full at every supported size — the mods rack and three decks use all of
        it — and a notice that only fits on a big monitor is a notice that is
        missed on the machine that reported the problem.  It fades, so it never
        holds the board for more than a few seconds.
        """
        board = self.board_viewport
        height = int(_clamp(board.height * 0.42, 200, 420))
        width = int(height / CARD_ASPECT)
        width = min(width, int(board.width * 0.32))
        height = int(width * CARD_ASPECT)
        left = board.left + int(_clamp(18 * self.ui_scale, 10, 30))
        return pygame.Rect(left, board.centery - height // 2, width, height)

    # Paczka's window is a LIST rather than a row of cards: six players holding
    # two Chest cards each is twelve faces, which no card lineup fits at
    # 1280×760.  It borrows the picker's furniture — centred panel, brass
    # heading, one button — and sizes itself to however many lines it has.
    def chest_reveal_panel(self, lines: int) -> pygame.Rect:
        width = int(_clamp(self.win_w * 0.34, 380, 620))
        height = int(self.chest_reveal_header
                     + max(1, lines) * self.chest_reveal_line
                     + self.chest_reveal_footer)
        height = min(height, int(self.win_h * 0.86))
        return pygame.Rect(self.win_w // 2 - width // 2,
                           self.win_h // 2 - height // 2, width, height)

    @property
    def chest_reveal_line(self) -> int:
        return int(24 * self.ui_scale)

    @property
    def chest_reveal_header(self) -> int:
        return int(76 * self.ui_scale)

    @property
    def chest_reveal_footer(self) -> int:
        return int(78 * self.ui_scale)

    def chest_reveal_ok_rect(self, lines: int) -> pygame.Rect:
        panel = self.chest_reveal_panel(lines)
        width = int(_clamp(self.win_w * 0.09, 130, 220))
        height = int(40 * self.ui_scale)
        return pygame.Rect(panel.centerx - width // 2,
                           panel.bottom - height - int(20 * self.ui_scale),
                           width, height)

    # ── choosing the Mods Patusa ─────────────────────────────────────────────
    # Same furniture as the card picker, one size down and with room under the
    # panel for the vote tally.  Reusing ``card_picker_card_size`` is what keeps
    # three mods looking like three chest cards instead of like a fourth kind of
    # card lineup.
    def mod_choice_panel(self, count: int) -> pygame.Rect:
        card_w, card_h = self.mod_choice_card_size(count)
        gap = self.picker_gap
        width = count * card_w + (count + 1) * gap
        height = card_h + 168
        return pygame.Rect(
            self.win_w // 2 - width // 2,
            self.win_h // 2 - height // 2,
            width, height,
        )

    def mod_choice_card_size(self, count: int) -> Tuple[int, int]:
        """A little shorter than the picker's, to leave room for the counters.

        The vote badge sits in the card's top-right corner and the tally under
        its bottom edge, so the panel needs vertical room the picker does not.
        """
        width, height = self.card_picker_card_size(count)
        height = int(height * 0.88)
        width = int(height / CARD_ASPECT)
        return (width, height)

    def mod_choice_card_rect(self, index: int, count: int) -> pygame.Rect:
        panel = self.mod_choice_panel(count)
        card_w, card_h = self.mod_choice_card_size(count)
        x = panel.left + self.picker_gap + index * (card_w + self.picker_gap)
        return pygame.Rect(x, panel.top + 72, card_w, card_h)

    def mod_vote_badge_rect(self, index: int, count: int) -> pygame.Rect:
        """The tick in a card's upper-right corner — one hunter, one card."""
        card = self.mod_choice_card_rect(index, count)
        size = int(_clamp(card.width * 0.22, 22, 40))
        return pygame.Rect(card.right - size - 6, card.top + 6, size, size)

    def choice_confirm_rect(self) -> pygame.Rect:
        """Confirm button for a multi-select question, inside the prompt strip.

        Only drawn when the engine asked for more than one thing; a single
        answer is confirmed by picking it, and a button there would be a click
        nobody needs.
        """
        panel = self.choice_prompt
        width = _clamp(panel.width * 0.20, 150, 260)
        height = _clamp(panel.height * 0.26, 30, 44)
        return pygame.Rect(panel.right - width - 16,
                           panel.bottom - height - 12, width, height)

    def pawn_grid_top(self, show_skill: bool) -> int:
        """Top edge of the 'Kolory Piotrka' notepad.

        Both the painter and the click handler need this number, so it lives
        here rather than being recomputed identically in two places.
        """
        rects = self.character_panel(show_skill)
        return int(rects["char_draw"].bottom + self.r_gap)  # type: ignore[union-attr]

    def pawn_grid_panel(self, top_y: int) -> pygame.Rect:
        grid_w = 3 * self.pk_circle_d + 2 * self.pk_col_gap
        grid_h = 2 * self.pk_circle_d + self.pk_row_gap
        w = grid_w + 2 * self.pk_pad
        h = self.pk_title_h + grid_h + self.pk_pad
        return pygame.Rect(self.right_panel.centerx - w // 2, top_y, w, h)

    def pawn_grid_rects(self, top_y: int, count: int) -> List[pygame.Rect]:
        panel = self.pawn_grid_panel(top_y)
        grid_w = 3 * self.pk_circle_d + 2 * self.pk_col_gap
        start_x = panel.centerx - grid_w // 2
        start_y = panel.top + self.pk_title_h
        rects: List[pygame.Rect] = []
        for i in range(count):
            row, col = divmod(i, 3)
            rect = pygame.Rect(0, 0, self.pk_circle_d, self.pk_circle_d)
            rect.center = (
                start_x + col * (self.pk_circle_d + self.pk_col_gap) + self.pk_circle_d // 2,
                start_y + row * (self.pk_circle_d + self.pk_row_gap) + self.pk_circle_d // 2,
            )
            rects.append(rect)
        return rects

    # ── bottom: the hand fan ─────────────────────────────────────────────────
    def _compute_hand(self) -> None:
        # The 260 ceiling was the second half of the flat hand-height cap: even
        # once the shelf grew, the card on it stopped at 182x260 and the rest
        # became padding.  It scales now, so a card in hand is bigger on a
        # bigger display — which is the whole of "card readability".
        card_h = _clamp(self.hand_h * 0.88, 150, 260 * self.ui_scale)
        self.hand_card_size = (int(card_h / CARD_ASPECT), int(card_h))
        #: Radius of the imaginary circle the fan is laid out on.  A large
        #: radius gives a shallow, readable arc instead of a wheel of cards.
        self.hand_arc_radius = float(card_h * 5.4)
        #: Pivot point, far below the screen edge.
        self.hand_pivot = (
            self.win_w // 2,
            int(self.win_h - self.hand_h + card_h * 0.58 + self.hand_arc_radius),
        )
        self.hand_max_spread = self.win_w - 2 * self.pad - self.hand_card_size[0]

    @property
    def hand_area(self) -> pygame.Rect:
        return pygame.Rect(0, self.win_h - self.hand_h, self.win_w, self.hand_h)


LAYOUT = Layout()
