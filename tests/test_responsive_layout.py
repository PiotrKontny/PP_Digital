"""
Responsive layout (stage 29).

The report these exist for: "the game looks great on my 2560x1440 desktop, but
on my 1920x1200 laptop the movement cards are extremely narrow and their text
is almost unreadable".  The cause was not the resolution.  It was that ONE
number scaled everything, so the laptop was simply the desktop at 83% — and
card type, which was sized from the card AND multiplied by the interface scale
again, moved with that number SQUARED.

Two families of test, and they pull against each other on purpose:

  * the reference display must not move.  The owner plays on 2560x1440 and a
    "fix" that redesigns his screen is not a fix;
  * the laptop must become readable, and specifically the movement cards must,
    because those are the ones being read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.config import settings
from pedzacy_piotrek.render.card_renderer import CARD_TYPE_ANCHOR
from pedzacy_piotrek.ui.layout import (
    BREAKPOINTS,
    MIN_HAND_CARD_W,
    REFERENCE_WINDOW,
    TYPE_ANCHOR,
    Layout,
)

DESKTOP = (2560, 1440)
LAPTOP = (1920, 1200)
SIZES = [(1280, 760), (1600, 900), (1920, 1080), (1920, 1200),
         (2560, 1440), (3840, 2160)]


# ── the reference display must not move ──────────────────────────────────────
def test_the_reference_display_is_untouched():
    """Every responsive rule collapses to stage 28's numbers at 2560x1440.

    This is the guarantee the whole stage is written under.  If one of these
    drifts, the owner's own monitor has been redesigned to fix somebody's
    laptop, which is not a trade he was offered.
    """
    layout = Layout(*DESKTOP)
    assert layout.compact == 0.0
    assert layout.type_boost == 1.0
    assert layout.type_scale == layout.ui_scale == pytest.approx(1.44)
    assert layout.panel_scale == layout.ui_scale
    assert layout.breakpoint == "wide"

    # The stage 28 values, written out rather than recomputed: a formula that
    # is wrong in the same way in the test and in the code agrees with itself.
    assert layout.pad == 18
    assert layout.hand_h == 352
    assert layout.hand_card_size == (216, 309)
    assert (layout.left_w, layout.right_w) == (331, 357)
    assert layout.board_viewport.size == (1800, 800)


def test_nothing_adapts_above_the_reference_display():
    """4K is bigger, not tighter.  The adaptation is for SMALL screens."""
    layout = Layout(3840, 2160)
    assert layout.compact == 0.0
    assert layout.type_boost == 1.0
    assert layout.type_scale == layout.ui_scale
    assert layout.breakpoint == "wide"


# ── breakpoints ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("size,expected", [
    ((3840, 2160), "wide"),
    ((2560, 1440), "wide"),
    ((1920, 1200), "medium"),
    ((1920, 1080), "medium"),
    ((1600, 900), "compact"),
    ((1280, 760), "compact"),
])
def test_the_window_lands_in_the_breakpoint_it_should(size, expected):
    assert Layout(*size).breakpoint == expected


def test_the_breakpoints_are_ordered_and_reachable():
    """A table whose floors are out of order silently swallows a tier."""
    floors = [floor for _, floor in BREAKPOINTS]
    assert floors == sorted(floors, reverse=True)
    assert floors[-1] == 0.0, "the last tier must catch everything"
    assert len({name for name, _ in BREAKPOINTS}) == len(BREAKPOINTS)


def test_the_layout_does_not_jump_at_a_breakpoint():
    """Named tiers decide RULES; the pixels interpolate continuously.

    A window being dragged across 0.66 room must not make the board leap.  One
    pixel of window may not move the hand by more than a couple of pixels.
    """
    for width in (1680, 1690, 1700, 1710):
        a = Layout(width, 950)
        b = Layout(width + 1, 950)
        assert abs(a.hand_h - b.hand_h) <= 2
        assert abs(a.left_w - b.left_w) <= 3
        assert abs(a.board_viewport.width - b.board_viewport.width) <= 6


@pytest.mark.parametrize("size", SIZES)
def test_compact_stays_in_range(size):
    layout = Layout(*size)
    assert 0.0 <= layout.compact <= 1.0
    assert 0.0 <= layout.vertical_room <= 1.0
    assert layout.room > 0.0


# ── the movement cards are the point ─────────────────────────────────────────
def test_the_laptop_card_is_nearly_the_size_of_the_desktop_card():
    """The headline result.  It used to be 180 wide against the desktop's 216."""
    desktop = Layout(*DESKTOP).hand_card_size
    laptop = Layout(*LAPTOP).hand_card_size
    assert laptop[0] >= desktop[0] * 0.95, \
        "the hand card must not shrink with the window the way it used to"


def test_card_type_is_the_same_absolute_size_on_both_displays():
    """A description is READ, so what matters is its size in pixels.

    The old arithmetic gave 23px of body type on the desktop and 15.6px on the
    laptop — a third smaller on the smaller panel, which is the opposite of
    what a physically smaller screen needs.
    """
    def body_px(size):
        layout = Layout(*size)
        card_h = layout.hand_card_size[1]
        return int(card_h * 0.054) * CARD_TYPE_ANCHOR

    desktop, laptop = body_px(DESKTOP), body_px(LAPTOP)
    assert laptop >= desktop * 0.95, (desktop, laptop)


@pytest.mark.parametrize("size", SIZES)
def test_card_type_keeps_its_share_of_the_card(size):
    """Type on a card is a fraction of the CARD, at every resolution.

    This is the invariant that replaces the double scaling.  Before it, the
    ratio ran from 0.060 at 1280x760 through 0.107 at 1440p to 0.138 at 4K:
    the same card design rendered three different ways.
    """
    layout = Layout(*size)
    card_w, card_h = layout.hand_card_size
    body = int(card_h * 0.054) * CARD_TYPE_ANCHOR
    assert 0.100 <= body / card_w <= 0.115, f"{size}: {body / card_w:.4f}"


@pytest.mark.parametrize("size", SIZES)
def test_a_hand_card_never_becomes_a_strip(size):
    layout = Layout(*size)
    assert layout.hand_card_size[0] >= MIN_HAND_CARD_W


@pytest.mark.parametrize("size", SIZES)
def test_the_hand_card_keeps_the_card_aspect_ratio(size):
    """"Preserve the current card aspect ratio" — the brief, verbatim."""
    from pedzacy_piotrek.ui.layout import CARD_ASPECT

    card_w, card_h = Layout(*size).hand_card_size
    assert card_h / card_w == pytest.approx(CARD_ASPECT, rel=0.02)


@pytest.mark.parametrize("size", SIZES)
def test_the_hand_fits_on_its_shelf(size):
    """A card taller than the shelf it sits on overhangs the board."""
    layout = Layout(*size)
    assert layout.hand_card_size[1] <= layout.hand_h


def test_the_hand_takes_a_bigger_share_of_a_smaller_window():
    """The priority list, made a number: the hand is what must not shrink."""
    desktop = Layout(*DESKTOP)
    laptop = Layout(*LAPTOP)
    assert (laptop.hand_h / laptop.win_h) > (desktop.hand_h / desktop.win_h)


# ── what gives way instead, and in what order ────────────────────────────────
def test_the_side_columns_give_ground_before_the_hand_does():
    """Priority 2 in the brief: the panels shrink before the cards do.

    Measured against what UNIFORM scaling gave, not against the desktop.  The
    columns cannot fall to the desktop's share of the window and should not be
    asked to: they stack a mod rack and three decks in whatever height is
    left, so their width follows that vertical budget, and squeezing further
    would leave them half empty — which stage 26 already has a test against.
    Stage 28 gave the laptop 30.2% of its width to the columns, MORE than the
    desktop's 26.9%, which is what "they consume more space than necessary"
    meant in the report.
    """
    desktop, laptop = Layout(*DESKTOP), Layout(*LAPTOP)
    laptop_share = (laptop.left_w + laptop.right_w) / laptop.win_w
    desktop_share = (desktop.left_w + desktop.right_w) / desktop.win_w

    assert laptop_share < 0.302, "stage 28's share; the columns must give ground"
    assert laptop_share < desktop_share * 1.06, "and end up close to the desktop's"
    assert laptop.panel_scale < laptop.ui_scale


def test_margins_shrink_before_anything_that_holds_content():
    """Priority 3: empty space is the cheapest thing on the screen."""
    desktop, laptop = Layout(*DESKTOP), Layout(*LAPTOP)
    assert (laptop.pad / laptop.win_w) < (desktop.pad / desktop.win_w)


def test_the_board_may_shrink_but_stays_comfortable():
    """The brief allows the board to give up room; it does not allow a stamp."""
    for size in SIZES:
        layout = Layout(*size)
        board = layout.board_viewport
        assert board.width >= layout.win_w * 0.58, size
        assert board.width > layout.left_w + layout.right_w, size
        assert board.height >= layout.win_h * 0.30, size


def test_the_board_keeps_its_area_on_the_laptop():
    """It loses height to the hand and wins width back from the columns.

    Which is the point of doing all four priorities rather than just the one:
    the board gives up the height the hand needs and is repaid out of the side
    panels, so the gameplay area is no worse off than before the change.
    """
    board = Layout(*LAPTOP).board_viewport
    assert board.width * board.height >= 1281 * 642 * 0.97


# ── typography scales on its own curve ───────────────────────────────────────
@pytest.mark.parametrize("size", SIZES)
def test_type_never_decays_faster_than_the_layout(size):
    """The whole meaning of "scale fonts independently"."""
    layout = Layout(*size)
    assert layout.type_scale >= layout.ui_scale - 1e-9
    assert layout.type_boost <= 1.0 + 1e-9 or layout.ui_scale < TYPE_ANCHOR


def test_type_holds_more_of_its_size_than_the_layout_does():
    desktop, laptop = Layout(*DESKTOP), Layout(*LAPTOP)
    geometry = laptop.ui_scale / desktop.ui_scale
    type_ratio = laptop.type_scale / desktop.type_scale
    assert type_ratio > geometry, "type must decay more slowly than the boxes"


@pytest.mark.parametrize("size", SIZES)
def test_a_text_band_really_holds_the_type_it_is_given(size):
    """Stage 26's rule, one step out: either the box scales or the text fits.

    Asserted against RENDERED type rather than against the formula that sizes
    the band, because a formula checked against itself agrees with itself.
    The deck heading is the case stage 26 got wrong at 1440p — a flat 26-pixel
    band with type in it that kept growing.
    """
    from pedzacy_piotrek.ui.app import App

    app = App(Layout(), headless=True, size=size)
    layout = app.layout

    heading = app.fonts.get(int(13 * layout.ui_scale), bold=True)
    assert heading.get_height() <= layout.section_line_h + 2, \
        f"{size}: deck heading {heading.get_height()} in a {layout.section_line_h} band"


@pytest.mark.parametrize("size", SIZES)
def test_the_character_name_does_not_run_into_what_follows_it(size):
    """KNOWN DEFECT above the laptop band — pinned here so it cannot grow.

    The name is drawn with ``fonts.get(int(21 * ui_scale))``, and FontBook
    multiplies by the scale again, so the type moves with ``ui_scale``
    SQUARED while the room under it moves with ``ui_scale``.  Above about
    1920x1200 the type wins and the name overhangs the ability card: seven
    pixels at 2560x1440, twenty-four at 4K.  It is the same double-scaling
    stage 29 removed from card faces, in the one place that was not part of
    the report — so it is DIAGNOSED here and left alone, because widening the
    band moves the reference display's right-hand column and stage 29 promised
    not to.  See CHANGELOG_LLM.md, stage 29, "what was found and not fixed".

    At and below 1920x1200 there is no overhang at all, and that much this
    stage does guarantee.
    """
    from pedzacy_piotrek.ui.app import App

    app = App(Layout(), headless=True, size=size)
    layout = app.layout
    name = app.fonts.get(int(21 * layout.ui_scale), bold=True)

    for show_skill in (False, True):
        rects = layout.character_panel(show_skill)
        # Against the ABILITY BUTTON since stage 50: the ability card that used
        # to sit under the name is no longer drawn in this column, and the
        # button is what the name can now collide with.
        clearance = layout.ability_button_rect(show_skill).top - int(rects["name_y"])
        overhang = name.get_height() - clearance
        if size[1] <= 1200:
            assert overhang <= 0, f"{size}: the name overhangs by {overhang}"
        assert overhang <= 24, \
            f"{size}: the known overhang got worse ({overhang} > 24)"


def test_the_text_bands_follow_the_type_curve_not_the_geometry():
    """If they followed ``ui_scale`` they would shrink out from under the type."""
    laptop = Layout(*LAPTOP)
    assert laptop.r_name_h == int(26 * laptop.type_scale)
    assert laptop.pk_title_h == int(21 * laptop.type_scale)
    assert laptop.r_title_h == int(20 * laptop.type_scale)
    assert laptop.r_name_h > int(26 * laptop.ui_scale)


def test_the_type_boost_is_spent_only_where_there_is_room_to_spend_it():
    """MIN_WINDOW has no slack: its menu rows are already at their floor.

    Lifting type there does not make anything more readable, it pushes
    captions out of boxes that cannot grow — so the boost tapers to nothing.
    """
    assert Layout(1280, 760).type_boost == pytest.approx(1.0)
    assert Layout(*LAPTOP).type_boost > 1.0


def test_the_card_anchor_matches_the_layout_anchor():
    """Two constants that must agree, in two packages that must not import
    each other.  If they drift, the reference display stops being exact."""
    assert CARD_TYPE_ANCHOR == pytest.approx(TYPE_ANCHOR)
    assert TYPE_ANCHOR == pytest.approx(REFERENCE_WINDOW[1] / 1000.0)


# ── nothing regressed on the way ─────────────────────────────────────────────
@pytest.mark.parametrize("size", SIZES)
def test_a_card_title_is_never_broken_mid_word(size):
    """"Avoid wrapping text into many very short lines" — the brief.

    Every title in the game, at the size it is dealt into a hand and at the
    size it is enlarged to on hover.  "Thunderfuck" used to come out as
    "Thunderfuc" over "k" on every display including the reference one.
    """
    from pedzacy_piotrek.cards.loader import ContentLibrary
    from pedzacy_piotrek.render.card_renderer import CardRenderer
    from pedzacy_piotrek.ui.app import App

    library = ContentLibrary.load()
    app = App(Layout(), headless=True, size=size)
    cards = CardRenderer(app.renderer, library)

    for card_size in (app.layout.hand_card_size,
                      cards.quantised(app.layout.hand_card_size, 2.15)):
        w, h = card_size
        inset = max(3, int(h * 0.028))
        pad = inset + max(4, int(w * 0.05))
        for deck in library.decks.values():
            for card in deck.build_cards():
                font = cards._title_font(card_size, card.title, w - 2 * pad)
                lines = app.renderer.wrap_lines(card.title, font, w - 2 * pad)
                rejoined = " ".join(lines).split()
                assert rejoined == card.title.split(), \
                    f"{size} {card_size}: {card.title!r} broke into {lines}"


@pytest.mark.parametrize("size", SIZES)
def test_the_columns_still_hold_their_contents(size):
    layout = Layout(*size)
    panel = layout.left_panel
    for column in range(len(settings.TABLE_DECKS)):
        assert panel.contains(layout.deck_draw_rect(column)), size
        assert panel.contains(layout.deck_discard_rect(column)), size
    for slot in range(layout.mod_slot_count):
        assert panel.contains(layout.mod_slot_rect(slot)), size

    right = layout.right_panel
    for show_skill in (False, True):
        rects = layout.character_panel(show_skill)
        for key in ("portrait", "char_draw", "char_disc"):
            assert right.contains(rects[key]), (size, show_skill, key)


@pytest.mark.parametrize("bigger,smaller", [
    ((1920, 1200), (1920, 1080)),
    ((2560, 1440), (1920, 1200)),
    ((3840, 2160), (2560, 1440)),
])
def test_a_taller_display_still_gets_a_bigger_card(bigger, smaller):
    """Stage 26's guarantee, re-checked: the hand may take a bigger SHARE of a
    small window without a small window ever showing a bigger card."""
    big, small = Layout(*bigger), Layout(*smaller)
    assert big.hand_card_size[1] > small.hand_card_size[1]
    assert big.panel_card_size[1] > small.panel_card_size[1]


def test_resizing_is_idempotent():
    """Two paths to the same window must give the same layout, or a resize
    leaves the interface depending on where it was dragged from."""
    direct = Layout(*LAPTOP)
    walked = Layout(*DESKTOP)
    walked.resize(1280, 760)
    walked.resize(*LAPTOP)
    for name in ("ui_scale", "type_scale", "panel_scale", "compact", "hand_h",
                 "left_w", "right_w", "hand_card_size", "pad"):
        assert getattr(direct, name) == getattr(walked, name), name
