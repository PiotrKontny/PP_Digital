"""
Card backs: one picture per deck, configured in one table.

The feature is a BRANCH, exactly like Signature Cards — a deck with a picture
in ``assets/card_backs`` is drawn one way, and anything without one falls back
to the drawn cover ``CardRenderer.back`` painted before stage 47 — so these
tests come in matching pairs.  Half prove the pictures are used; half prove the
fallback still exists and that nothing about card FRONTS moved.

Rendered and measured rather than asked wherever it is a claim about pixels:
"the chest pile shows the chest back" is not something a return value can
prove, because a renderer that ignored its argument would return a surface too.
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
from pedzacy_piotrek.config.theme import THEME
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.render.card_back import CardBackLibrary
from pedzacy_piotrek.render.card_renderer import CardRenderer
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

PANEL_SIZE = (120, 171)

#: Every deck in the game.  Not ``TABLE_DECKS`` — Piotrek's two piles are drawn
#: by the same ``_deck_section`` and must be just as covered.
ALL_DECKS = (settings.DECK_MOVEMENT, settings.DECK_MODS, settings.DECK_CHEST,
             settings.DECK_SKILLS, settings.DECK_CHARACTERS)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def cards(library):
    """A CardRenderer pointed at the real, shipped card-back folder."""
    app = App(Layout(), headless=True, size=(2560, 1440))
    return CardRenderer(app.renderer, library)


def solid_back(directory: Path, name: str, colour, size=(64, 96)) -> Path:
    """A picture whose only job is to exist and be one recognisable colour."""
    surface = pygame.Surface(size)
    surface.fill(colour)
    path = directory / name
    pygame.image.save(surface, str(path))
    return path


def mean(surface: pygame.Surface, rect: pygame.Rect, step: int = 3):
    total = [0, 0, 0]
    count = 0
    for y in range(rect.top, rect.bottom, step):
        for x in range(rect.left, rect.right, step):
            pixel = surface.get_at((x, y))
            for i in range(3):
                total[i] += pixel[i]
            count += 1
    return tuple(v / max(1, count) for v in total)


def middle(surface: pygame.Surface) -> pygame.Rect:
    w, h = surface.get_size()
    return pygame.Rect(w // 6, h // 6, w - w // 3, h - h // 3)


def fingerprint(surface: pygame.Surface) -> bytes:
    return pygame.image.tostring(surface, "RGBA")


# ── the configuration is one table ───────────────────────────────────────────
def test_every_deck_has_a_card_back_configured():
    """All five, and no deck left drawing the placeholder in normal play."""
    for deck_id in ALL_DECKS:
        assert deck_id in settings.CARD_BACKS, f"{deck_id} has no card back"
    assert len(settings.CARD_BACKS) == 5


def test_the_shipped_folder_is_where_the_documentation_says():
    assert settings.CARD_BACK_DIR.name == "card_backs"
    assert settings.CARD_BACK_DIR.parent == settings.ASSETS_DIR
    # A card back is not card art and is not an inline illustration.
    assert settings.CARD_BACK_DIR != settings.CARD_ART_DIR
    assert settings.CARD_BACK_DIR != settings.IMAGE_DIR


def test_all_five_files_ship_and_load():
    backs = CardBackLibrary()
    for deck_id in ALL_DECKS:
        path = backs.path(deck_id)
        assert path is not None and path.is_file(), f"{deck_id}: {path} missing"
        assert backs.has_back(deck_id), f"{deck_id} does not load"


def test_no_two_decks_share_a_card_back_file():
    """The whole point of five pictures is that they are five pictures."""
    names = list(settings.CARD_BACKS.values())
    assert len(set(names)) == len(names), f"duplicate card back: {names}"


def test_the_five_backs_are_five_different_pictures(cards):
    """Measured, not compared by filename — two names can hold one image."""
    painted = {deck: fingerprint(cards.back(PANEL_SIZE, None, 0.0, deck))
               for deck in ALL_DECKS}
    assert len(set(painted.values())) == 5, "two decks render the same back"


def test_no_rendering_code_names_a_card_back_file():
    """Replacing a picture must never mean editing a renderer.

    The failure this guards against is somebody 'fixing' a one-off by reaching
    for a path in ``card_renderer.py``, which is exactly what the table exists
    to prevent.
    """
    source = Path(__file__).resolve().parent.parent / "pedzacy_piotrek"
    offenders = []
    for folder in ("render", "ui"):
        for path in sorted((source / folder).glob("*.py")):
            # ``card_back.py`` is the library the table feeds, and its module
            # docstring shows the mapping as documentation.  It resolves the
            # configuration; it does not RENDER, and it is not where a one-off
            # would be hacked in.
            if path.name == "card_back.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                for name in settings.CARD_BACKS.values():
                    if name in line:
                        offenders.append(f"{path.name}:{number}")
    assert not offenders, f"card-back filenames leaked into: {offenders}"


# ── swapping a picture is a change to the table, not to code ─────────────────
def test_repointing_one_deck_changes_only_that_deck(tmp_path, library):
    """The headline requirement, proved by doing it.

    One deck's entry is repointed at a different file; that deck's back
    changes, and the other four are byte-identical to what they were.
    """
    app = App(Layout(), headless=True, size=(2560, 1440))
    for deck_id in ALL_DECKS:
        solid_back(tmp_path, f"{deck_id}.png", (20, 20, 20))
    solid_back(tmp_path, "brand_new.png", (0, 255, 0))
    mapping = {deck: f"{deck}.png" for deck in ALL_DECKS}

    before_lib = CardBackLibrary(tmp_path, mapping)
    before = {deck: fingerprint(CardRenderer(app.renderer, library,
                                             backs=before_lib)
                                .back(PANEL_SIZE, None, 0.0, deck))
              for deck in ALL_DECKS}

    swapped = dict(mapping, **{settings.DECK_CHEST: "brand_new.png"})
    after_cards = CardRenderer(app.renderer, library,
                               backs=CardBackLibrary(tmp_path, swapped))
    after = {deck: fingerprint(after_cards.back(PANEL_SIZE, None, 0.0, deck))
             for deck in ALL_DECKS}

    assert after[settings.DECK_CHEST] != before[settings.DECK_CHEST]
    for deck_id in ALL_DECKS:
        if deck_id != settings.DECK_CHEST:
            assert after[deck_id] == before[deck_id], f"{deck_id} moved too"


def test_the_picture_in_the_file_is_the_picture_on_the_card(tmp_path, library):
    app = App(Layout(), headless=True, size=(2560, 1440))
    solid_back(tmp_path, "green.png", (0, 200, 0))
    cards = CardRenderer(app.renderer, library,
                         backs=CardBackLibrary(tmp_path,
                                               {settings.DECK_CHEST: "green.png"}))
    back = cards.back(PANEL_SIZE, None, 0.0, settings.DECK_CHEST)
    r, g, b = mean(back, middle(back))
    assert g > 150 and r < 60 and b < 60, "the file's colour is not on the card"


# ── the artwork is respected ─────────────────────────────────────────────────
def test_a_card_back_keeps_the_card_shape(cards):
    """Same size and same rounded silhouette as every other card."""
    for deck_id in ALL_DECKS:
        back = cards.back(PANEL_SIZE, None, 0.0, deck_id)
        assert back.get_size() == PANEL_SIZE
        assert back.get_at((0, 0))[3] == 0, f"{deck_id}: square corner"
        assert back.get_at((PANEL_SIZE[0] - 1, PANEL_SIZE[1] - 1))[3] == 0


@pytest.mark.parametrize("size", [(80, 114), (120, 171), (216, 309), (300, 429)])
def test_the_artwork_is_not_distorted(tmp_path, library, size):
    """Cover-scaled and cropped, never stretched.

    A wildly non-card-shaped source with a circle on it: if the back were
    stretched to the card the circle would come out an ellipse.  Measured as
    the painted width against the painted height at the centre lines.
    """
    app = App(Layout(), headless=True, size=(2560, 1440))
    art = pygame.Surface((400, 400))
    art.fill((0, 0, 0))
    pygame.draw.circle(art, (255, 255, 255), (200, 200), 150)
    pygame.image.save(art, str(tmp_path / "round.png"))

    cards = CardRenderer(app.renderer, library,
                         backs=CardBackLibrary(tmp_path,
                                               {settings.DECK_MODS: "round.png"}))
    back = cards.back(size, None, 0.0, settings.DECK_MODS)
    w, h = size
    lit_w = sum(1 for x in range(w) if back.get_at((x, h // 2))[0] > 128)
    lit_h = sum(1 for y in range(h) if back.get_at((w // 2, y))[0] > 128)
    # A square source cover-scaled onto a portrait card is cropped on the
    # width, so the visible chord can be narrower — but never WIDER than tall,
    # which is what a stretch would produce.
    assert lit_w <= lit_h + 2, f"{size}: the picture is stretched"


def test_hovering_a_pile_lights_its_back(cards):
    """The deck-panel hover still reads, on a picture instead of a colour."""
    for deck_id in ALL_DECKS:
        rest = cards.back(PANEL_SIZE, None, 0.0, deck_id)
        lit = cards.back(PANEL_SIZE, None, 1.0, deck_id)
        assert sum(mean(lit, middle(lit))) > sum(mean(rest, middle(rest))) + 10, \
            f"{deck_id} does not light up"


def test_the_same_back_is_painted_once_and_reused(cards):
    """A pile is three backs and a frame is sixty; none of that re-reads disk."""
    first = cards.back(PANEL_SIZE, None, 0.0, settings.DECK_MOVEMENT)
    second = cards.back(PANEL_SIZE, None, 0.0, settings.DECK_MOVEMENT)
    assert first is second, "the painted back is not cached"
    assert (cards.backs.surface(settings.DECK_MOVEMENT)
            is cards.backs.surface(settings.DECK_MOVEMENT)), "the file is re-read"


# ── robustness: this must never take the game down ───────────────────────────
def test_a_missing_file_falls_back_to_the_drawn_back(tmp_path, library):
    app = App(Layout(), headless=True, size=(2560, 1440))
    backs = CardBackLibrary(tmp_path, {settings.DECK_CHEST: "nothing_here.png"})
    assert not backs.has_back(settings.DECK_CHEST)
    cards = CardRenderer(app.renderer, library, backs=backs)
    colour = THEME.deck_colors[settings.DECK_CHEST]
    painted = cards.back(PANEL_SIZE, colour, 0.0, settings.DECK_CHEST)
    drawn = cards.back(PANEL_SIZE, colour, 0.0, None)
    assert fingerprint(painted) == fingerprint(drawn), "no fallback was painted"


def test_a_corrupt_image_falls_back_instead_of_raising(tmp_path, library):
    """Half a download, or a .png that is really something else."""
    (tmp_path / "chest.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    backs = CardBackLibrary(tmp_path, {settings.DECK_CHEST: "chest.png"})
    assert backs.path(settings.DECK_CHEST).is_file(), "the file is found"
    assert backs.surface(settings.DECK_CHEST) is None, "but it does not load"
    assert not backs.has_back(settings.DECK_CHEST)


def test_a_missing_folder_is_not_an_error(tmp_path):
    backs = CardBackLibrary(tmp_path / "does" / "not" / "exist")
    for deck_id in ALL_DECKS:
        assert backs.surface(deck_id) is None


def test_an_unconfigured_deck_is_not_an_error(tmp_path):
    backs = CardBackLibrary(tmp_path, {})
    assert backs.surface(settings.DECK_MOVEMENT) is None
    assert backs.surface(None) is None
    assert backs.filename("no_such_deck") is None


def test_the_game_starts_and_runs_with_an_empty_card_back_folder(tmp_path, library):
    """A clean checkout with no binary assets still plays.

    The project's own rule (``assets/README.md``): everything in assets is an
    override, and the game runs without any of it.
    """
    app = App(Layout(), headless=True, size=(1920, 1080))
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=7), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    screen.cards.backs = CardBackLibrary(tmp_path)
    screen.cards.clear_cache()
    app.push(screen)
    for _ in range(6):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)


# ── the decks on screen show their own backs ─────────────────────────────────
def _rendered(library, size=(2560, 1440), seat=0):
    app = App(Layout(), headless=True, size=size)
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=11),
                        library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    for _ in range(3):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)
    return app, screen


def test_each_table_deck_shows_its_own_back_on_screen(library):
    """The left column, measured off the real canvas.

    This is the test that would have caught a renderer ignoring ``deck_id``:
    three piles on screen, three different pictures, each matching the one the
    table configures for that deck.
    """
    app, screen = _rendered(library)
    layout = screen.app.layout
    for column, deck_id in enumerate(settings.TABLE_DECKS):
        rect = layout.deck_draw_rect(column)
        on_screen = mean(app.canvas, rect.inflate(-rect.w // 3, -rect.h // 3))
        expected_surface = screen.cards.back((rect.w, rect.h), None, 0.0, deck_id)
        expected = mean(expected_surface, middle(expected_surface))
        for channel in range(3):
            assert abs(on_screen[channel] - expected[channel]) < 42, (
                f"{deck_id} pile does not show its own back "
                f"(screen {on_screen}, expected {expected})"
            )


def test_the_table_decks_do_not_share_a_back_on_screen(library):
    app, screen = _rendered(library)
    layout = screen.app.layout
    seen = []
    for column in range(len(settings.TABLE_DECKS)):
        rect = layout.deck_draw_rect(column)
        seen.append(mean(app.canvas, rect.inflate(-rect.w // 3, -rect.h // 3)))
    for i in range(len(seen)):
        for j in range(i + 1, len(seen)):
            spread = sum(abs(seen[i][c] - seen[j][c]) for c in range(3))
            assert spread > 40, f"piles {i} and {j} look identical on screen"


# ── nothing about card FRONTS moved ──────────────────────────────────────────
def test_card_fronts_are_untouched_by_the_back_system(cards, library):
    """A front is a front.  The two libraries never resolve through each other."""
    deck = library.deck(settings.DECK_MOVEMENT)
    card_def = deck.cards[0]
    from pedzacy_piotrek.cards.base_card import Card

    card = Card(definition=card_def, uid=1)
    face = cards.face(card, PANEL_SIZE)
    for deck_id in ALL_DECKS:
        back = cards.back(PANEL_SIZE, None, 0.0, deck_id)
        assert fingerprint(face) != fingerprint(back), "a front rendered as a back"


def test_a_card_back_is_not_looked_for_in_the_card_art_folder(cards):
    """And card art is not looked for in the card-back folder."""
    assert cards.backs.directory == settings.CARD_BACK_DIR
    assert cards.art.directory == settings.CARD_ART_DIR
    assert cards.backs.directory != cards.art.directory


def test_signature_cards_still_render_as_signature_cards(cards, library):
    """The shipped Troll: the front system is exactly as stage 30 left it."""
    troll = next(c for c in library.deck(settings.DECK_MOVEMENT).cards
                 if c.title == "Troll")
    assert cards.art.has_art(troll), "Troll lost its artwork"
