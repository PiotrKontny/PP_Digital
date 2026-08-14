"""
The enlarged hover preview (stage 48).

An ability card, and any card sitting in a panel slot without artwork, is too
small to read.  Hovering it now leaves it EXACTLY as it is and draws a second,
larger copy of the same card beside it.

These tests come in the same matching pairs ``test_card_art.py`` uses, and for
the same reason: half of them prove the preview appears, and the other half
prove that the card it previews, the card-art hover it borrows its
presentation from, and the game state underneath were all left alone.

Measured against pixels wherever the claim is about pixels.  "The preview
contains the description" is a claim about what was painted, and a test that
only checked a rect would pass with the preview drawn blank.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame
import pytest

from pedzacy_piotrek.cards.base_card import Card
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.render.card_renderer import CARD_ASPECT
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.card_preview import CardPreview
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

#: The reference window, a laptop, and the two smallest the game supports.
#: Every geometry claim below is made at all four — the whole point of this
#: feature is the small ones, and a preview verified at one resolution is a
#: preview verified at no resolution (see the note in LLM_Instructions).
WINDOWS = [(2560, 1440), (1920, 1080), (1600, 900), settings.MIN_WINDOW]

#: A window well away from the corner cases, for the tests that are not about
#: geometry.
DEFAULT_WINDOW = (1920, 1080)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def build(library, size=DEFAULT_WINDOW) -> GameScreen:
    app = App(Layout(), headless=True, size=size)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3, seed=77),
        library,
    )
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    return screen


@pytest.fixture
def screen(library) -> GameScreen:
    return build(library)


def frame(screen: GameScreen, mouse=(0, 0), dt: float = 1 / 60) -> None:
    """One whole frame with the cursor at ``mouse``.

    The HUD reads the cursor in ``draw`` through ``App.mouse``, not through the
    position handed to ``update`` — a panel hover is answered on the frame it
    is painted.  Pointing ``App.mouse`` at a fixed position is therefore the
    only honest way to hold a cursor still over a slot, and it is what a real
    player's motionless hand does.
    """
    app = screen.app
    app.mouse = lambda position=tuple(mouse): position
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(dt, tuple(mouse))
    screen.draw(app.canvas)


def click(screen: GameScreen, position, button: int = 1) -> None:
    position = (int(position[0]), int(position[1]))
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        screen.handle_event(pygame.event.Event(kind, pos=position, button=button),
                            position)


def ability_slot(screen: GameScreen) -> pygame.Rect:
    player = screen.state.player(screen.view_seat)
    return screen.app.layout.character_panel(player.is_piotrek)["card"]


def ability_card(screen: GameScreen) -> Card:
    """The very card the character panel is showing, not a copy of it."""
    player = screen.state.player(screen.view_seat)
    if player.is_piotrek:
        return player.skill
    return screen.character_panel._ability_card(player)


def view_hunter(screen: GameScreen) -> None:
    """Look at a seat whose ability has no artwork.

    Piotrek's own panel shows a skill; a hunter's shows the character's fixed
    ability, and in this seeding none of those are illustrated.  Both paths
    are exercised, because the ability slot previews either way.
    """
    for index, player in enumerate(screen.state.players):
        if not player.is_piotrek and screen.may_view(index):
            screen.view_seat = index
            return
    raise AssertionError("no hunter seat to look at")


def fill_mod_slot(screen: GameScreen, slot: int = 0, art: bool = False) -> Card:
    """Put a real mod card into a rack slot, with or without artwork."""
    deck = screen.state.deck(settings.DECK_MODS)
    card = next(c for c in deck.draw_pile
                if screen.cards.has_art(c) is art)
    deck.draw_pile.remove(card)
    screen.state.mod_slots[slot] = card
    return card


def give_card(screen: GameScreen) -> Card:
    """Move one movement card out of the deck and into the hand on screen."""
    deck = screen.state.deck(settings.DECK_MOVEMENT)
    card = deck.draw_pile[0]
    deck.draw_pile.remove(card)
    screen.state.player(screen.view_seat).add_card(card)
    return card


def ink(surface: pygame.Surface, colour, tolerance: int = 42) -> int:
    """How many pixels close to ``colour`` — i.e. how much type was painted."""
    count = 0
    width, height = surface.get_size()
    for y in range(height):
        for x in range(width):
            pixel = surface.get_at((x, y))
            if all(abs(pixel[i] - colour[i]) <= tolerance for i in range(3)):
                count += 1
    return count


def region(screen: GameScreen, rect: pygame.Rect) -> pygame.Surface:
    return screen.app.canvas.subsurface(rect).copy()


def interior(rect: pygame.Rect) -> pygame.Rect:
    """``rect`` less its border and its rounded corners.

    A card face is a ROUNDED rectangle, so its four corners are transparent and
    show whatever is underneath — the slot's own well, and the well's hover
    glow, which is an affordance this stage did not touch and must not be
    accused of breaking.  Every "was this repainted?" comparison is therefore
    made on the part of the card that is actually the card.
    """
    inset = max(6, int(rect.height * 0.09))
    return rect.inflate(-2 * inset, -2 * inset)


def painted_rows(surface: pygame.Surface, colour, tolerance: int = 40) -> list:
    """Rows in which something close to ``colour`` was painted — i.e. type."""
    rows = []
    width, height = surface.get_size()
    for y in range(height):
        for x in range(3, width - 3):
            pixel = surface.get_at((x, y))
            if all(abs(pixel[i] - colour[i]) <= tolerance for i in range(3)):
                rows.append(y)
                break
    return rows


def identical(one: pygame.Surface, other: pygame.Surface) -> bool:
    """Same size, same colour in every pixel.

    Compared channel by channel rather than through ``image.tostring``: one
    side of these comparisons is a slice of the CANVAS, which has no per-pixel
    alpha, and the other is a card face, which does.  ``tostring`` reports two
    surfaces that agree in every visible channel as different because of it,
    which is a difference about surface flags and not about what was drawn.
    """
    if one.get_size() != other.get_size():
        return False
    width, height = one.get_size()
    for y in range(height):
        for x in range(width):
            if one.get_at((x, y))[:3] != other.get_at((x, y))[:3]:
                return False
    return True


# ── the ability keeps its own face ───────────────────────────────────────────
def test_hovering_an_illustrated_ability_does_not_disturb_the_artwork(screen):
    """The card under the cursor is the whole point; it must not change.

    Byte-for-byte across the whole face, which is what catches the old
    behaviour exactly: the reveal laid a veil over the artwork, deepened the
    scrim and lifted the title, and every one of those is a differing pixel
    here.  What is allowed to change is the card's RIM — that is the game's one
    hover language (N45) and it is how the player knows the cursor landed.
    """
    player = screen.state.player(screen.view_seat)
    assert player.is_piotrek and screen.cards.has_art(player.skill), \
        "this seeding is expected to show Piotrek an illustrated skill"

    slot = ability_slot(screen)
    frame(screen, mouse=(0, 0))
    resting = region(screen, interior(slot))
    frame(screen, mouse=slot.center)
    hovered = region(screen, interior(slot))

    assert identical(resting, hovered), \
        "hovering an ability must not repaint the ability card"


@pytest.mark.parametrize("piotrek", [True, False])
def test_hovering_an_ability_never_lifts_its_title(library, piotrek):
    """The complaint this stage answers, measured where it was made.

    The ability card used to take the Signature reveal from ``highlighted``:
    hover it and the title climbed to make room for the description, inside a
    card 142 px tall.  The title must now sit in exactly the same rows hovered
    as at rest, for an illustrated ability and a plain one alike.
    """
    screen = build(library)
    if not piotrek:
        view_hunter(screen)
    card = ability_card(screen)
    theme = screen.app.renderer.theme
    colour = (theme.card_art_title if screen.cards.has_art(card)
              else theme.card_title)

    slot = ability_slot(screen)
    frame(screen, mouse=(0, 0))
    resting = painted_rows(region(screen, slot), colour)
    frame(screen, mouse=slot.center)
    hovered = painted_rows(region(screen, slot), colour)

    assert resting, "the ability card should have a title on it at rest"
    assert resting == hovered, "the title moved when the cursor arrived"


def test_hovering_an_ability_creates_the_enlarged_preview(screen):
    slot = ability_slot(screen)
    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None

    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect is not None


def test_the_preview_is_beside_the_ability_and_never_on_top_of_it(screen):
    """Disjoint rects are what make the hover flicker-free.

    If the preview overlapped its own card the cursor would have somewhere to
    sit where the hover is simultaneously on (the preview is under it) and off
    (it has left the slot), which is the classic tooltip oscillation.
    """
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    preview = screen.card_preview.rect
    assert not preview.colliderect(slot)


def test_the_preview_disappears_when_the_cursor_leaves(screen):
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect is not None

    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None, \
        "the preview lives for exactly as long as the hover"


def test_the_preview_is_noticeably_larger_than_the_card_it_previews(screen):
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    preview = screen.card_preview.rect
    assert preview.height >= slot.height * 1.8
    assert preview.width >= slot.width * 1.8


def test_the_preview_keeps_the_proportions_of_a_card(screen):
    """It is the same card, larger — not a differently shaped panel."""
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    preview = screen.card_preview.rect
    assert abs(preview.height / preview.width - CARD_ASPECT) < 0.06


# ── what the preview actually shows ──────────────────────────────────────────
def test_the_preview_shows_the_title_and_the_description(screen):
    """The claim is about painted pixels, so it is checked against them.

    The preview is compared to the face the card renderer paints at the
    revealed state — the SAME presentation the card-art hover has used since
    stage 30 — and, separately, shown to differ from the resting face, which
    is the one that has no description on it.
    """
    view_hunter(screen)
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    preview = screen.card_preview.rect
    card = ability_card(screen)

    size = (preview.width, preview.height)
    colour = screen.app.renderer.theme.deck_colors[settings.DECK_CHARACTERS]
    revealed = screen.cards.face(card, size, highlighted=True,
                                 border_color=colour, reveal=1.0)
    inner = interior(preview)
    assert identical(
        region(screen, inner),
        revealed.subsurface(pygame.Rect(inner.x - preview.x, inner.y - preview.y,
                                        inner.width, inner.height)),
    ), "the preview is not the revealed face the card-art hover paints"

    # And it is a card with words on it, not an empty frame.
    assert card.title and card.text
    assert ink(revealed, screen.app.renderer.theme.card_text) > 200


def test_the_preview_type_is_far_bigger_than_the_slot_card_type(screen):
    """The readability claim, measured: more ink means larger letters.

    A card without artwork already prints its description in the slot — that
    was never the problem.  The problem is that at 1280x760 the slot is 99 px
    tall and the description is a grey smudge.  The preview has to put
    *materially* more type on screen or it has not helped anybody.
    """
    view_hunter(screen)
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    preview = screen.card_preview.rect
    text_colour = screen.app.renderer.theme.card_text

    assert ink(region(screen, preview), text_colour) > \
        ink(region(screen, slot), text_colour) * 2.0


def test_the_preview_is_the_same_card_and_not_a_copy_of_it(screen):
    """No second Card, no second uid, nothing new in the game.

    A preview that built its own card would drift the moment an ability spent
    a use or a variant was chosen, and would show yesterday's text forever.
    """
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    screen.card_preview.request(ability_card(screen), slot)
    assert screen.card_preview.pending.card is ability_card(screen)


# ── cards without artwork, in a slot ─────────────────────────────────────────
def test_hovering_a_slot_card_without_artwork_creates_the_preview(screen):
    card = fill_mod_slot(screen, 0, art=False)
    slot = screen.app.layout.mod_slot_rect(0)

    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None

    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect is not None
    assert screen.card_preview.pending is None, "the draw consumes the request"
    assert not screen.cards.has_art(card)


def test_a_slot_card_without_artwork_is_not_repainted_by_the_hover(screen):
    fill_mod_slot(screen, 0, art=False)
    slot = screen.app.layout.mod_slot_rect(0)

    frame(screen, mouse=(0, 0))
    resting = region(screen, slot)
    frame(screen, mouse=slot.center)

    # The slot's own well lights up, as it always has — that is the existing
    # affordance and is not this stage's business.  The CARD inside it is what
    # must not move, so the comparison is inset past the well, the glow, and
    # the rounded corners the glow shows through.
    inner = interior(slot)
    assert identical(region(screen, inner), resting.subsurface(
        pygame.Rect(inner.x - slot.x, inner.y - slot.y,
                    inner.width, inner.height)))


def test_the_preview_of_a_slot_card_is_beside_it_and_on_screen(screen):
    fill_mod_slot(screen, 0, art=False)
    slot = screen.app.layout.mod_slot_rect(0)
    frame(screen, mouse=slot.center)

    preview = screen.card_preview.rect
    assert not preview.colliderect(slot)
    assert screen.app.layout.hover_preview_bounds.contains(preview)


def test_a_slot_card_with_artwork_keeps_the_hover_it_already_had(screen):
    """Stage 30's card-art hover works; the brief said not to disturb it."""
    card = fill_mod_slot(screen, 0, art=True)
    assert screen.cards.has_art(card)
    slot = screen.app.layout.mod_slot_rect(0)

    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect is None


# ── it stays on screen, at every size ────────────────────────────────────────
@pytest.mark.parametrize("size", WINDOWS)
def test_the_preview_never_leaves_the_screen(library, size):
    screen = build(library, size)
    layout = screen.app.layout
    slots = [ability_slot(screen)]
    fill_mod_slot(screen, 0, art=False)
    fill_mod_slot(screen, 1, art=False)
    slots += [layout.mod_slot_rect(0), layout.mod_slot_rect(1)]

    for slot in slots:
        frame(screen, mouse=slot.center)
        preview = screen.card_preview.rect
        assert preview is not None, f"no preview for {slot} at {size}"
        assert layout.hover_preview_bounds.contains(preview), \
            f"{preview} escaped {layout.hover_preview_bounds} at {size}"
        assert not preview.colliderect(slot)


@pytest.mark.parametrize("size", WINDOWS)
def test_the_preview_never_reaches_down_over_the_hand(library, size):
    """The fan is the one region of the screen that is always in use."""
    screen = build(library, size)
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect.bottom <= screen.app.layout.hand_area.top


@pytest.mark.parametrize("size", WINDOWS)
def test_the_right_hand_column_falls_back_to_the_left(library, size):
    """There is nothing to the right of the character panel but the edge.

    The preferred side is the right; this is the fallback, and it is the case
    that actually ships, so it is tested rather than assumed.
    """
    screen = build(library, size)
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect.right <= slot.left


@pytest.mark.parametrize("size", WINDOWS)
def test_the_left_hand_column_previews_to_the_right(library, size):
    screen = build(library, size)
    fill_mod_slot(screen, 0, art=False)
    slot = screen.app.layout.mod_slot_rect(0)
    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect.left >= slot.right


@pytest.mark.parametrize("size", WINDOWS)
def test_the_preview_is_a_real_enlargement_at_every_size(library, size):
    screen = build(library, size)
    slot = ability_slot(screen)
    frame(screen, mouse=slot.center)
    assert screen.card_preview.rect.height >= slot.height * 1.8


def test_nothing_on_screen_moves_because_of_a_hover(screen):
    """A hover is not a layout event.  Nothing may reflow under the cursor."""
    slot = ability_slot(screen)
    before = {
        "ability": ability_slot(screen),
        "button": screen.app.layout.ability_button_rect(True),
        "mod": screen.app.layout.mod_slot_rect(0),
        "board": screen.app.layout.board_viewport,
        "hand": screen.app.layout.hand_area,
    }
    frame(screen, mouse=slot.center)
    after = {
        "ability": ability_slot(screen),
        "button": screen.app.layout.ability_button_rect(True),
        "mod": screen.app.layout.mod_slot_rect(0),
        "board": screen.app.layout.board_viewport,
        "hand": screen.app.layout.hand_area,
    }
    assert before == after


# ── the preview changes nothing ──────────────────────────────────────────────
def test_hovering_an_ability_spends_nothing_and_moves_nothing(screen):
    state = screen.state
    player = state.player(screen.view_seat)
    source = player.skill if player.is_piotrek else player.character
    before = (source.uses_left, source.uses_total, source.ability_available,
              [len(p.hand) for p in state.players],
              state.deck(settings.DECK_MODS).draw_count,
              list(state.mod_slots))

    slot = ability_slot(screen)
    for _ in range(30):
        frame(screen, mouse=slot.center)

    after = (source.uses_left, source.uses_total, source.ability_available,
             [len(p.hand) for p in state.players],
             state.deck(settings.DECK_MODS).draw_count,
             list(state.mod_slots))
    assert before == after


def test_the_preview_never_swallows_a_click(screen):
    """It is a picture.  The card underneath keeps every gesture it had.

    The preview is drawn over the board, so if it were hit-tested at all the
    board would stop taking clicks wherever it happened to be.  Nothing routes
    to it: it is not a ``Panel``, it has no ``handle_click``, and the screen
    never offers it one.
    """
    assert not hasattr(CardPreview, "handle_click")

    card = fill_mod_slot(screen, 0, art=False)
    slot = screen.app.layout.mod_slot_rect(0)
    frame(screen, mouse=slot.center)
    preview = screen.card_preview.rect
    assert preview.colliderect(screen.app.layout.board_viewport), \
        "this test is only worth anything if the preview is over the board"

    # Right-click is the gesture that discards a mod.  Aimed at the picture of
    # the card it does nothing, because the picture is not the card.
    click(screen, preview.center, button=3)
    assert screen.state.mod_slots[0] is card

    # And the real slot still answers the same gesture.
    click(screen, slot.center, button=3)
    assert screen.state.mod_slots[0] is None


def test_a_screen_without_a_preview_still_draws(screen):
    """``HudContext.preview`` is optional, so every other screen is unaffected.

    Several screens and a good many tests build a context of their own.  A
    panel that assumed a preview was always there would take all of them down.
    """
    ctx = screen._context((0, 0))
    ctx.preview = None
    ctx.preview_card(ability_card(screen), ability_slot(screen))  # no raise
    screen.character_panel.draw(ctx)
    screen.mod_panel.draw(ctx)


def test_only_one_preview_is_drawn_however_many_are_requested(screen):
    """Two slots cannot be under one cursor; one preview is the right answer."""
    preview = CardPreview()
    first, second = ability_card(screen), ability_card(screen)
    preview.request(first, pygame.Rect(0, 0, 10, 10))
    preview.request(second, pygame.Rect(50, 0, 10, 10))
    assert preview.pending.anchor.left == 50


def test_a_request_for_no_card_is_ignored(screen):
    preview = CardPreview()
    preview.request(None, pygame.Rect(0, 0, 10, 10))
    assert preview.pending is None


# ── the card-art hover it borrows from is untouched ──────────────────────────
def test_the_card_art_hover_still_lifts_the_title_and_reveals_the_text(screen):
    """Stage 30's reveal, asked the same question ``test_card_art`` asks.

    This stage reuses that presentation; it must not have edited it.
    """
    cards = screen.cards
    card = next((c for c in screen.state.deck(settings.DECK_MOVEMENT).draw_pile
                 if cards.has_art(c)), None)
    if card is None:                                # pragma: no cover
        pytest.skip("no illustrated card in the movement deck")

    size = (216, 309)
    resting = cards.face(card, size, reveal=0.0)
    revealed = cards.face(card, size, reveal=1.0)
    assert not identical(resting, revealed), "the reveal still does something"

    # And it still defaults from ``highlighted``, which is what gave every
    # panel in the game both states for free.
    assert identical(cards.face(card, size, highlighted=True),
                     cards.face(card, size, highlighted=True, reveal=1.0))


def test_the_hand_fan_still_reveals_in_place(library):
    """The fan draws cards big enough to read; it keeps the reveal it had.

    The preview is for PANEL SLOTS.  A hand card is 200-300 px tall and the
    stage-30 reveal is exactly right for it — and if the fan started asking for
    previews the player would get one beside every card in a hand of eight.

    So this asserts BOTH halves: the fan's own smooth reveal still rises under
    the cursor, and no preview is spawned beside it.
    """
    screen = build(library)
    card = give_card(screen)
    for _ in range(5):
        frame(screen)

    # The topmost card in the fan, chased frame by frame: the cards are still
    # sliding into their resting arc, and a position sampled once is a position
    # the cursor has already lost.
    uid = screen.hand.order[-1]
    for _ in range(60):
        position = screen.hand.slots[uid].position
        frame(screen, mouse=(int(position[0]), int(position[1])))

    assert screen.hand.hovered is not None, "the fan stopped noticing the cursor"
    assert screen.hand.slots[screen.hand.hovered].hover > 0.8, \
        "the fan's own hover reveal stopped working"
    assert screen.card_preview.rect is None, \
        "a hand card must not spawn a preview beside it"



UI_DIR = Path(__file__).resolve().parent.parent / "pedzacy_piotrek" / "ui"


def test_the_interface_asks_the_renderer_about_artwork():
    """One question, one place.  ``ui/`` never reaches into the art library.

    Stage 30's rule is that the artwork branch lives on ``face()``'s first
    line.  This stage needs a DIFFERENT question — which hover affordance a
    slot offers — and it goes through ``CardRenderer.has_art`` so there is
    still exactly one opinion about what counts as artwork.
    """
    for source in UI_DIR.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert ".art.surface(" not in text, f"{source.name} bypasses has_art"
        assert "CardArtLibrary" not in text, f"{source.name} loads artwork itself"
