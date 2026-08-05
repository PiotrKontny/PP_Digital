"""
Rendering-quality tests.

"Not blurry" sounds unmeasurable, but it is not: a picture that was enlarged
after being rasterised has soft edges, and one that was drawn at its final size
has hard ones.  Measuring the contrast between neighbouring pixels tells the two
apart reliably, which is what these tests do.

They exist because the fix is easy to undo by accident — one ``rotozoom`` on a
cached surface and every enlarged card is mush again.
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

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.render.card_renderer import CardRenderer
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

SIZES = [(1920, 1080), (1920, 1200), (2560, 1440)]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def edge_energy(surface: pygame.Surface) -> float:
    """Average contrast between neighbouring pixels.

    High for crisp text, low for anything that has been stretched — the
    difference between drawing at a size and blowing a smaller drawing up.
    """
    width, height = surface.get_size()
    total = 0
    samples = 0
    for y in range(2, height - 2, 2):
        for x in range(2, width - 3, 2):
            here = surface.get_at((x, y))[:3]
            right = surface.get_at((x + 1, y))[:3]
            total += sum(abs(a - b) for a, b in zip(here, right))
            samples += 1
    return total / max(1, samples)


def make_app(size) -> App:
    return App(Layout(), headless=True, size=size)


# ── cards ────────────────────────────────────────────────────────────────────
def test_an_enlarged_card_is_redrawn_not_zoomed(library):
    """The whole point: a big card is laid out big, not blown up."""
    app = make_app((1920, 1080))
    cards = CardRenderer(app.renderer, library)
    card = library.deck(settings.DECK_MOVEMENT).build_cards()[0]

    small = (110, 157)
    big = cards.quantised(small, 2.2)
    assert big[1] > small[1] * 2, "the paint size follows the scale"

    redrawn = cards.face(card, big)
    zoomed = pygame.transform.rotozoom(cards.face(card, small), 0, 2.2)
    assert redrawn.get_height() == pytest.approx(zoomed.get_height(), abs=8)
    assert edge_energy(redrawn) > edge_energy(zoomed) * 1.25, \
        "a redrawn card must be visibly crisper than a zoomed one"


def test_the_paint_size_is_stepped_so_faces_are_reusable(library):
    app = make_app((1920, 1080))
    cards = CardRenderer(app.renderer, library)
    base = (110, 157)
    # A smoothly growing card must not repaint on every frame.
    sizes = {cards.quantised(base, 1.0 + step / 200) for step in range(20)}
    assert len(sizes) <= 6


def test_a_hovered_played_card_is_drawn_at_its_hovered_size(library):
    app = make_app((1920, 1080))
    state = create_game(SessionConfig(num_players=3, board_cells=24, seed=5,
                                      double_frequency=0.0), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)

    deck = state.deck(settings.DECK_MOVEMENT)
    seat = state.active_player_index
    card = next((c for c in state.players[seat].hand
                 if c.resolves_without_asking), None)
    if card is None:
        # Deal one that needs no decision, so the play definitely resolves.
        card = next(c for c in deck.draw_pile if c.resolves_without_asking)
        deck.draw_pile.remove(card)
        state.players[seat].add_card(card)
    screen.submit(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    assert screen.recently_played.entries, "the card should have been played"

    slot = app.layout.recent_slot_rect(0)
    for _ in range(50):
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, slot.center)
        screen.draw(app.canvas)

    entry = screen.recently_played.entries[0]
    assert entry.hover > 0.8
    painted = {key for key in screen.cards._faces if key[0] == "face"}
    heights = {key[4][1] for key in painted}
    small = app.layout.recent_card_size()[1]
    assert max(heights) > small * 1.5, \
        "the hovered card was painted at its enlarged size"


# ── type ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("size", SIZES)
def test_type_is_rendered_larger_on_larger_displays(size):
    """Not scaled up afterwards — rendered at the larger size."""
    reference = make_app((1920, 1080))
    baseline = reference.fonts.get(18).get_height()

    app = make_app(size)
    height = app.fonts.get(18).get_height()
    expected = app.layout.ui_scale / reference.layout.ui_scale
    assert height >= baseline * min(expected, 1.0)
    if size[1] > 1080:
        assert height > baseline, "bigger screen, bigger glyphs"


def test_resizing_rebuilds_the_type_rather_than_stretching_it(library):
    app = make_app((1920, 1080))
    before = app.fonts.get(18).get_height()
    app.resize((2560, 1440))
    after = app.fonts.get(18).get_height()
    assert after > before
    # The old rasterised text must be gone, or two sizes would share a screen.
    assert not app.renderer._text_cache


# ── space ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("size", SIZES)
def test_the_board_gets_the_lion_s_share_of_the_window(size):
    layout = Layout(*size)
    board = layout.board_viewport
    assert board.width > layout.left_w + layout.right_w, \
        "the board is the game; the columns are trim"
    assert board.width >= layout.win_w * 0.58


@pytest.mark.parametrize("size", SIZES)
def test_the_side_columns_are_not_mostly_empty(size):
    """Panel space that is not doing anything should have gone to the board."""
    layout = Layout(*size)
    card_w, card_h = layout.panel_card_size
    inner = layout.left_w - 2 * layout.left_inner
    used_w = 2 * card_w + layout.left_card_gap
    assert used_w >= inner * 0.86, "the column is wider than its cards need"

    sections = 1 + len(settings.TABLE_DECKS)
    used_h = sections * (card_h + layout.section_label_h)
    assert used_h >= layout.left_panel.height * 0.78
