"""
Visual-style tests.

A skin is easy to break by halves — one panel left flat, one button still using
the old palette — and none of that shows up in a functional test.  These check
the things the concept art actually asks for, mechanically:

* the table is dark and the panels are darker than the cards on them;
* no screen still fills a plain rectangle where a panel belongs;
* colours come from the theme rather than being written into the modules.
"""

from __future__ import annotations

import os
import re
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
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

SOURCE = Path(__file__).resolve().parent.parent / "pedzacy_piotrek"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def rendered(library):
    app = App(Layout(), headless=True, size=(1920, 1080))
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=4, double_frequency=0.0),
        library,
    )
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    for _ in range(12):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)
    return app, screen


def luminance(colour) -> float:
    return 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]


def region_mean(surface: pygame.Surface, rect: pygame.Rect, step: int = 5):
    rect = rect.clip(surface.get_rect())
    totals = [0, 0, 0]
    count = 0
    for x in range(rect.left, rect.right, step):
        for y in range(rect.top, rect.bottom, step):
            pixel = surface.get_at((x, y))
            for i in range(3):
                totals[i] += pixel[i]
            count += 1
    return tuple(t / max(1, count) for t in totals)


# ── the table ────────────────────────────────────────────────────────────────
def test_the_background_is_dark_and_unobtrusive(rendered):
    app, _ = rendered
    corner = region_mean(app.canvas, pygame.Rect(0, 0, 120, 60))
    assert luminance(corner) < 40, "the table should be near-black, not bright"
    # Dark, but not literally black: the concept has a faint blue wash.
    assert max(corner) > 4
    assert corner[2] >= corner[1] >= corner[0] - 2, "cool, blue-leaning"


def test_the_background_is_a_wash_not_a_flat_fill(rendered):
    app, _ = rendered
    middle = region_mean(app.canvas, pygame.Rect(0, 480, 60, 120))
    corner = region_mean(app.canvas, pygame.Rect(0, 0, 60, 60))
    assert luminance(middle) > luminance(corner) + 2, "there should be a vignette"


def test_panels_sit_above_the_table_but_below_the_cards(rendered):
    app, screen = rendered
    layout = app.layout
    table = luminance(region_mean(app.canvas, pygame.Rect(0, 0, 120, 60)))
    panel = luminance(region_mean(
        app.canvas, pygame.Rect(layout.left_panel.left + 6, layout.left_panel.top + 6,
                                layout.left_panel.width - 12, 30)))
    card = luminance(region_mean(app.canvas, layout.hand_area.inflate(-400, -60)))
    assert table < panel < card, (table, panel, card)


def test_every_panel_has_a_brass_edge(rendered):
    """The border is what makes a panel a panel rather than a dark rectangle."""
    app, _ = rendered
    layout = app.layout
    for name, rect in (("left", layout.left_panel), ("right", layout.right_panel),
                       ("top bar", layout.turn_bar)):
        edge = region_mean(app.canvas, pygame.Rect(rect.left, rect.top + rect.height // 3,
                                                   2, rect.height // 3), step=2)
        body = region_mean(app.canvas, pygame.Rect(rect.left + 8,
                                                   rect.top + rect.height // 3,
                                                   6, rect.height // 3), step=2)
        assert edge[0] > body[0] + 6, f"{name} panel has no lit edge"
        assert edge[0] >= edge[2], f"{name} panel edge should be warm, not blue"


# ── cards ────────────────────────────────────────────────────────────────────
def test_cards_are_parchment_against_the_dark_table(rendered):
    app, screen = rendered
    face = screen.cards.face(screen.state.active_player.hand[0], (140, 200))
    middle = region_mean(face, pygame.Rect(30, 60, 80, 80))
    assert luminance(middle) > 170, "cards should read as lit parchment"
    assert middle[0] > middle[2], "and be warm rather than grey"


def test_a_card_is_shaded_rather_than_flat(rendered):
    app, screen = rendered
    face = screen.cards.face(screen.state.active_player.hand[0], (140, 200))
    # Sampled down the inside margin, where there is parchment and no text.
    top = region_mean(face, pygame.Rect(8, 14, 6, 24), step=2)
    bottom = region_mean(face, pygame.Rect(8, 164, 6, 24), step=2)
    assert luminance(top) > luminance(bottom) + 4, "lit from above"


def test_the_drawn_fallback_back_uses_the_themed_colours(rendered):
    """The back painted when a deck has NO picture (stage 47).

    Since stage 47 the back of a card is artwork from ``assets/card_backs`` and
    this is the fallback — what a deck gets when its file is missing, and what
    a clean checkout with no binary assets shows.  It is still held to the
    concept's palette, because a deck that loses its picture must still look
    like this game.  Passing no ``deck_id`` is what selects it; the pictures
    themselves are covered by ``tests/test_card_backs.py``.
    """
    app, screen = rendered
    for deck_id in settings.TABLE_DECKS:
        colour = THEME.deck_colors[deck_id]
        back = screen.cards.back((120, 171), colour)
        middle = region_mean(back, pygame.Rect(20, 20, 80, 130))
        assert luminance(middle) < 150, "a deck back is a bound cover, not paper"
        dominant = max(range(3), key=lambda i: middle[i])
        assert dominant == max(range(3), key=lambda i: colour[i])


def test_the_decks_on_screen_show_their_artwork_backs(rendered):
    """And the NORMAL path is the artwork, not the drawing above.

    A back that quietly stopped resolving its picture would leave every test
    here passing, because the fallback is themed to look right.  This is the
    one that notices.
    """
    app, screen = rendered
    for deck_id in settings.TABLE_DECKS:
        assert screen.cards.backs.has_back(deck_id), f"{deck_id} has no artwork"
        picture = screen.cards.back((120, 171), None, 0.0, deck_id)
        drawn = screen.cards.back((120, 171), THEME.deck_colors[deck_id])
        assert (pygame.image.tostring(picture, "RGBA")
                != pygame.image.tostring(drawn, "RGBA")), \
            f"{deck_id} is still painting the generated back"


# ── consistency ──────────────────────────────────────────────────────────────
def test_the_interface_does_not_hard_code_colours():
    """Colours belong to the theme so the next skin is one file, not a hunt."""
    offenders = []
    pattern = re.compile(r"\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)")
    for path in sorted((SOURCE / "ui").glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or "pawn.color" in stripped:
                continue
            if pattern.search(stripped):
                offenders.append(f"{path.name}:{number}: {stripped[:70]}")
    assert len(offenders) <= 12, "too many literal colours outside the theme:\n" + \
        "\n".join(offenders[:15])


def test_the_theme_carries_the_concept_palette():
    assert luminance(THEME.background) < 25, "dark table"
    assert THEME.brass[0] > THEME.brass[2], "brass is warm"
    assert luminance(THEME.card_bg) > 200, "parchment cards"
    assert THEME.panel_edge[0] > THEME.panel_edge[2], "warm panel edges"
    # One accent, used for the thing that matters most on screen.
    assert THEME.accent[1] > THEME.accent[0]


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (2560, 1440)])
def test_the_style_holds_at_every_resolution(library, size):
    app = App(Layout(), headless=True, size=size)
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=2), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    for _ in range(4):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)

    corner = region_mean(app.canvas, pygame.Rect(0, 0, 80, 40))
    assert luminance(corner) < 40
    panel = region_mean(app.canvas, pygame.Rect(app.layout.left_panel.left + 6,
                                                app.layout.left_panel.top + 6,
                                                40, 20))
    assert luminance(panel) > luminance(corner)


# ── the same language everywhere ─────────────────────────────────────────────
def test_the_menus_share_the_table_and_the_type(library):
    """A menu that kept the old green would give the whole thing away."""
    from pedzacy_piotrek.ui.network_screens import (
        HostSetupScreen, JoinScreen, MainMenuScreen,
    )

    for screen_class in (MainMenuScreen, HostSetupScreen, JoinScreen):
        app = App(Layout(), headless=True, size=(1600, 900))
        screen = screen_class(app, library)
        app.push(screen)
        for _ in range(3):
            app.renderer.begin(app.canvas)
            app.renderer.table_background(app.canvas)
            screen.update(1 / 60, (0, 0))
            screen.draw(app.canvas)
        corner = region_mean(app.canvas, pygame.Rect(0, 0, 60, 40))
        assert luminance(corner) < 30, f"{screen_class.__name__} is not on the table"
        pygame.display.quit()


def test_buttons_react_to_hover_and_press(library):
    """Depth, not a colour swap: the three states must all differ."""
    from pedzacy_piotrek.ui.widgets import Button

    app = App(Layout(), headless=True, size=(1600, 900))
    button = Button(pygame.Rect(40, 40, 240, 54), "Zakończ turę", primary=True)

    def snapshot() -> tuple:
        app.renderer.begin(app.canvas)
        app.canvas.fill((0, 0, 0))
        button.draw(app.renderer, app.canvas)
        return region_mean(app.canvas, button.rect.inflate(10, 10))

    idle = snapshot()
    button.update(button.rect.center, 1.0)          # settle the hover animation
    hovered = snapshot()
    button.pressed = True
    pressed = snapshot()

    assert luminance(hovered) > luminance(idle) + 2, "hover should lift it"
    assert pressed != hovered, "pressed should sit differently"


def test_a_disabled_button_reads_as_disabled(library):
    from pedzacy_piotrek.ui.widgets import Button

    app = App(Layout(), headless=True, size=(1600, 900))
    live = Button(pygame.Rect(40, 40, 240, 54), "Start", primary=True)
    dead = Button(pygame.Rect(40, 40, 240, 54), "Start", primary=True, enabled=False)

    def snapshot(button) -> tuple:
        app.renderer.begin(app.canvas)
        app.canvas.fill((0, 0, 0))
        button.draw(app.renderer, app.canvas)
        return region_mean(app.canvas, button.rect)

    assert luminance(snapshot(live)) > luminance(snapshot(dead)) + 4


# ── one highlight system (stage 12) ──────────────────────────────────────────
def test_nothing_draws_a_radial_highlight_any_more():
    """The giant glowing circles cannot come back by accident.

    ``Renderer.glow(centre, radius, ...)`` was a disc whose size every caller
    guessed, and the guesses ranged from 1.15 to 2.6 times the component.  It
    was replaced by ``shape_glow`` (rounded rect) and ``ring_glow`` (circle),
    both of which take the component's own geometry — so this test is really
    "nobody has reintroduced a highlight that can outgrow its widget".
    """
    offenders = []
    pattern = re.compile(r"\.glow\s*\(")
    for folder in ("ui", "render"):
        for path in sorted((SOURCE / folder).glob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("*"):
                    continue
                # ``shape_glow``/``ring_glow`` are the replacements, and
                # ``self.glow =`` is the Button's animated hover level.
                if "shape_glow" in stripped or "ring_glow" in stripped:
                    continue
                if pattern.search(stripped):
                    offenders.append(f"{path.name}:{number}: {stripped[:70]}")
    assert not offenders, "radial highlights are gone; put them back and:\n" + \
        "\n".join(offenders)


def test_a_highlight_never_reaches_far_past_its_component():
    """A bloom is a lit rim, not a lamp: measure how far the light travels."""
    from pedzacy_piotrek.render.highlight import BLOOM_MAX
    from pedzacy_piotrek.render.renderer import Renderer

    App(Layout(), headless=True, size=(1600, 900))
    r = Renderer()
    canvas = pygame.Surface((900, 400))
    canvas.fill((0, 0, 0))
    r.begin(canvas)

    # A wide, short panel: the shape the old radial glow handled worst.
    rect = pygame.Rect(300, 170, 300, 60)
    r.shape_glow(rect, THEME.accent, canvas, radius=9, strength=1.0)

    lit = 0
    for x in range(rect.right, canvas.get_width()):
        if luminance(canvas.get_at((x, rect.centery))) > 2:
            lit = x - rect.right
    assert lit <= BLOOM_MAX + 2, f"the glow reached {lit}px past a 300px panel"
    # The old version put a 225-pixel disc here; anything on that scale fails.
    assert lit < rect.width // 3


def test_the_ring_glow_hugs_the_thing_it_rings():
    from pedzacy_piotrek.render.highlight import BLOOM
    from pedzacy_piotrek.render.renderer import Renderer

    App(Layout(), headless=True, size=(1600, 900))
    r = Renderer()
    canvas = pygame.Surface((400, 400))
    canvas.fill((0, 0, 0))
    r.begin(canvas)

    radius = 20
    r.ring_glow((200, 200), radius, THEME.accent, canvas, strength=1.0)
    lit = 0
    for x in range(200 + radius, 400):
        if luminance(canvas.get_at((x, 200))) > 2:
            lit = x - 200
    assert lit <= radius * (1 + BLOOM) + 4, f"reached {lit}px for a {radius}px token"


# ── buttons size themselves (stage 12) ───────────────────────────────────────
@pytest.mark.parametrize("label", [
    "Wróć",
    "Gra lokalna (hot-seat)",
    "Rozpocznij grę",
    "UŻYJ UMIEJĘTNOŚCI (2/2)",
])
def test_a_button_is_wide_enough_for_its_own_label(library, label):
    """No manual widths: a button measures its caption and grows to hold it."""
    from pedzacy_piotrek.ui.widgets import BUTTON_SPACING, Button

    app = App(Layout(), headless=True, size=(1600, 900))
    r = app.renderer
    button = Button(pygame.Rect(0, 0, 10, 10), label)
    button.fit(r)

    font = r.fonts.get(button.text_size, bold=True)
    needed = r.spaced_width(label.upper(), font, BUTTON_SPACING)
    assert button.rect.width >= needed, f"'{label}' does not fit its button"
    assert button.rect.height >= font.get_height()


def test_a_label_shrinks_rather_than_escaping_a_box_it_was_given(library):
    """When a layout insists on a small box, the type gives way — not the box."""
    from pedzacy_piotrek.ui.widgets import Button

    app = App(Layout(), headless=True, size=(1600, 900))
    r = app.renderer
    app.renderer.begin(app.canvas)
    # Deliberately far too small for the caption.
    cramped = pygame.Rect(20, 20, 150, 26)
    button = Button(cramped.copy(), "UŻYJ UMIEJĘTNOŚCI (2/2)")
    drawn = r.fit_spaced_text(button.label, cramped, THEME.text_light,
                              app.canvas, spacing=1, padding=8)
    assert drawn.width <= cramped.width, "the label ran outside its button"


def test_every_menu_button_holds_its_caption(library):
    """The screens the player meets first, checked end to end."""
    from pedzacy_piotrek.ui.network_screens import (
        HostSetupScreen, JoinScreen, MainMenuScreen,
    )
    from pedzacy_piotrek.ui.widgets import BUTTON_SPACING, Button

    for size in ((1280, 760), (1920, 1080), (2560, 1440)):
        app = App(Layout(), headless=True, size=size)
        for screen_class in (MainMenuScreen, HostSetupScreen, JoinScreen):
            screen = screen_class(app, library)
            r = app.renderer
            buttons = [value for value in vars(screen).values()
                       if isinstance(value, Button)]
            buttons += [button for _, button in getattr(screen, "buttons", [])]
            assert buttons, f"{screen_class.__name__} has no buttons to check"
            for button in buttons:
                font = r.fonts.get(button.text_size, bold=True)
                needed = r.spaced_width(button.label.upper(), font, BUTTON_SPACING)
                assert button.rect.width >= needed, (
                    f"{screen_class.__name__} at {size}: "
                    f"'{button.label}' needs {needed}px, has {button.rect.width}px"
                )
        pygame.display.quit()


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (2560, 1440)])
def test_in_game_button_labels_stay_inside_their_buttons(library, size):
    """The captions the stage brief named, checked where they are actually drawn.

    "UŻYJ UMIEJĘTNOŚCI (1/1)" lives in the narrow right-hand column and
    "ZAKOŃCZ TURĘ" over the board; both used to be laid out to a fixed box and
    typeset at a size derived from its height, which is a recipe for a caption
    wider than its button.  Recording the rectangles the renderer actually
    draws is the only way to be sure they fit.
    """
    from pedzacy_piotrek.render.renderer import Renderer

    app = App(Layout(), headless=True, size=size)
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=7), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)

    drawn: list = []
    original = Renderer.spaced_text

    def record(self, text, font, color, surface=None, **kwargs):
        rect = original(self, text, font, color, surface, **kwargs)
        drawn.append((text, pygame.Rect(rect)))
        return rect

    Renderer.spaced_text = record
    try:
        for _ in range(4):
            app.renderer.begin(app.canvas)
            app.renderer.table_background(app.canvas)
            screen.update(1 / 60, (0, 0))
            screen.draw(app.canvas)
    finally:
        Renderer.spaced_text = original

    boxes = {
        "ZAKOŃCZ TURĘ": app.layout.end_turn_button,
        "UŻYJ UMIEJĘTNOŚCI": app.layout.ability_button_rect(
            state.player(screen.view_seat).is_piotrek),
    }
    checked = 0
    for text, rect in drawn:
        for caption, box in boxes.items():
            if text.startswith(caption):
                # The button lifts by a pixel on hover, so allow the box a
                # little slack vertically but none at all horizontally.
                assert rect.width <= box.width, (
                    f"{size}: '{text}' is {rect.width}px in a {box.width}px button")
                assert box.left <= rect.left and rect.right <= box.right, (
                    f"{size}: '{text}' escapes its button sideways")
                checked += 1
    assert checked >= 2, f"expected both captions on screen, found {checked}"


def test_hover_is_the_same_everywhere(library):
    """One language: hover lightens the surface and lifts it, for everything."""
    from pedzacy_piotrek.render.highlight import emphasis

    idle = emphasis(THEME)
    hovered = emphasis(THEME, hover=1.0)
    selected = emphasis(THEME, selected=True)
    pressed = emphasis(THEME, hover=1.0, pressed=True)

    assert luminance(hovered.fill) > luminance(idle.fill), "hover lightens"
    assert hovered.offset < 0, "hover lifts"
    assert hovered.shadow > idle.shadow, "hover deepens the shadow"
    assert pressed.offset > 0 and pressed.shadow == 0, "pressed drops and flattens"
    assert selected.glow_strength > hovered.glow_strength, "selection reads stronger"
    assert emphasis(THEME, enabled=False).glow_strength == 0.0
