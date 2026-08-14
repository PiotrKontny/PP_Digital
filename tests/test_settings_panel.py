"""
Stage 26: the settings tabs, the new defaults, and the two things that were
too easy to miss.

The panel tests live here rather than in test_mod_counts_ui.py, which stays
about the Mody Patusa tab specifically — it is the one that existed before and
its assertions are what proves the rest of the panel did not disturb it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if "SDL_VIDEODRIVER" not in os.environ:
    os.environ["SDL_VIDEODRIVER"] = "dummy"
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pygame
import pytest

from netkit import Table, all_agree
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import (RULES, SessionConfig,
                                             clamp_ability_uses,
                                             clamp_card_counts)
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.menu import MenuScreen
from pedzacy_piotrek.ui.network_screens import HostSetupScreen

WINDOW = (1600, 900)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def menu(library, size=WINDOW):
    app = App(Layout(), headless=True, size=size)
    captured: list = []
    screen = MenuScreen(app, library, captured.append)
    app.push(screen)
    screen.update(1 / 60, (0, 0))
    return screen, captured


def click(screen, pos, button: int = 1) -> None:
    pos = (int(pos[0]), int(pos[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button), pos
    )
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button), pos
    )


# ── part 1: the tabs ─────────────────────────────────────────────────────────
def test_there_is_a_tab_for_every_category(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    assert [tab.id for tab in panel.tabs] == [
        settings.DECK_MOVEMENT, settings.DECK_MODS, settings.DECK_CHEST,
        "abilities", "variants", "rules", "copy",
    ]
    assert len(panel.tab_rects) == len(panel.tabs)


def test_the_deck_tabs_are_seeded_from_the_data_file(library):
    """Defaults are whatever the deck is printed as — never a list in code."""
    screen, _ = menu(library)
    panel = screen.settings_panel
    for deck_id in (settings.DECK_MOVEMENT, settings.DECK_MODS,
                    settings.DECK_CHEST):
        printed = {card.title: card.count
                   for card in library.deck(deck_id).cards}
        assert panel.values_of(deck_id) == printed


def test_the_chest_tab_shows_the_stage_27_counts(library):
    """Part of the stage 27 brief: the new counts are the LOBBY defaults too.

    They arrive here for free because the tab is seeded from ``card.count``,
    which is the whole reason the composition lives in cards.json — but "for
    free" is exactly the kind of claim that stops being true the first time
    somebody writes a list of titles into a screen.
    """
    screen, _ = menu(library)
    assert screen.settings_panel.values_of(settings.DECK_CHEST) == {
        "Dzieckorolka": 2,
        "Rage Quit": 2,
        "Balbinka": 2,
        "Nie masz Rosji": 2,
        "Gambit Patusa": 3,
        "Herold": 1,
        "Shady": 2,
        "Gejtos": 3,
        "Gamechanger": 1,
    }


def test_the_ability_tab_offers_charges_and_not_copies(library):
    screen, _ = menu(library)
    uses = screen.settings_panel.ability_uses
    printed = {}
    for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
        for card in library.deck(deck_id).cards:
            if card.ability and card.uses is not None:
                printed[card.title] = card.uses
    assert uses == printed
    assert uses["Dziad"] == 2 and uses["ChatGPT"] == 5


def test_a_card_without_an_ability_gets_no_row(library):
    """A charge counter on a card that cannot spend one does nothing."""
    screen, _ = menu(library)
    titles = set(screen.settings_panel.ability_uses)
    for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
        for card in library.deck(deck_id).cards:
            if not card.ability:
                assert card.title not in titles


def test_clicking_a_tab_changes_what_is_listed(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.open()
    movement = panel.tab_rects[0]
    click(screen, movement.center)
    assert panel.tab.id == settings.DECK_MOVEMENT
    assert len(panel.titles) == len(library.deck(settings.DECK_MOVEMENT).cards)


def test_the_steppers_change_the_visible_tab(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.open()
    panel.select_tab(settings.DECK_CHEST)
    screen.update(1 / 60, (0, 0))
    title = panel.visible_titles[0]
    before = panel.counts[title]
    click(screen, panel.steppers[0].rects["plus1"].center)
    assert panel.counts[title] == before + 1
    # ...and only that tab: a click on one deck must not touch another.
    printed = {c.title: c.count for c in library.deck(settings.DECK_MODS).cards}
    assert panel.values_of(settings.DECK_MODS) == printed


def test_a_value_cannot_leave_its_range(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.select_tab("abilities")
    title = panel.titles[0]
    for _ in range(40):
        panel.tab.bump(title, -1)
    assert panel.counts[title] == RULES.ability_uses_min
    for _ in range(40):
        panel.tab.bump(title, 1)
    assert panel.counts[title] == RULES.ability_uses_max


def test_the_long_tab_scrolls_instead_of_shrinking(library):
    """Thirty movement cards do not fit any window, so the list moves."""
    screen, _ = menu(library, size=(1280, 760))
    panel = screen.settings_panel
    panel.open()
    panel.select_tab(settings.DECK_MOVEMENT)
    assert len(panel.titles) > panel.visible_rows, "otherwise nothing is proved"
    first = panel.visible_titles[0]
    screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-3), (0, 0))
    assert panel.visible_titles[0] != first
    assert panel.scroll <= panel.max_scroll
    # ...and it cannot be scrolled off either end.
    for _ in range(60):
        screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-1), (0, 0))
    assert panel.scroll == panel.max_scroll
    for _ in range(60):
        screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1), (0, 0))
    assert panel.scroll == 0


def test_every_row_has_a_stepper_beside_it(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.open()
    for tab in panel.tabs:
        panel.select_tab(tab.id)
        assert len(panel.steppers) >= len(panel.visible_titles)


def test_reset_puts_back_only_the_visible_tab(library):
    """One tab's button must not undo five minutes spent on another."""
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.open()
    panel.select_tab(settings.DECK_MOVEMENT)
    panel.tab.bump(panel.titles[0], 1)
    panel.select_tab(settings.DECK_CHEST)
    chest_title = panel.titles[0]
    panel.tab.bump(chest_title, 1)

    panel.reset()
    assert panel.counts[chest_title] == panel.defaults[chest_title]
    panel.select_tab(settings.DECK_MOVEMENT)
    assert not panel.tab.is_default, "the other tab was left alone"


def test_a_tab_warns_when_its_deck_cannot_work(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.select_tab(settings.DECK_MOVEMENT)
    for title in panel.titles:
        panel.counts[title] = 0
    assert panel.warning, "an empty movement deck cannot start a game"


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1200), (2560, 1440)])
def test_the_panel_fits_at_every_resolution(library, size):
    screen, _ = menu(library, size=size)
    panel = screen.settings_panel
    panel.open()
    window = pygame.Rect(0, 0, *size)
    for tab in panel.tabs:
        panel.select_tab(tab.id)
        assert window.contains(panel.panel), f"panel off screen at {size}"
        for rect in panel.tab_rects:
            assert panel.panel.contains(rect), f"tab strip escapes at {size}"
        for stepper in panel.steppers:
            row = pygame.Rect(
                min(r.left for r in stepper.rects.values()),
                min(r.top for r in stepper.rects.values()),
                max(r.right for r in stepper.rects.values())
                - min(r.left for r in stepper.rects.values()),
                stepper.height,
            )
            assert row.right <= panel.panel.right, f"stepper hangs over at {size}"
        for button in (panel.close_button, panel.reset_button):
            assert panel.panel.contains(button.rect)


# ── the numbers reaching the game ────────────────────────────────────────────
def test_the_settings_reach_the_session_config(library):
    screen, captured = menu(library)
    panel = screen.settings_panel
    panel.select_tab(settings.DECK_MOVEMENT)
    panel.counts["Troll"] = 5
    panel.select_tab(settings.DECK_CHEST)
    panel.counts["Balbinka"] = 4
    panel.select_tab("abilities")
    panel.counts["Dziad"] = 7
    screen._start()
    assert captured, "the menu never started a game"
    config = captured[0]
    assert config.movement_counts["Troll"] == 5
    assert config.chest_counts["Balbinka"] == 4
    assert config.ability_uses["Dziad"] == 7


def test_a_resized_deck_is_actually_built(library):
    config = SessionConfig(num_players=4, board_cells=24, seed=3,
                           movement_counts={"Troll": 5},
                           chest_counts={"Balbinka": 0})
    game = create_game(config, library)
    movement = game.decks[settings.DECK_MOVEMENT]
    everything = list(movement.draw_pile) + list(movement.discard_pile)
    for player in game.players:
        everything.extend(player.hand)
    assert sum(1 for c in everything if c.title == "Troll") == 5
    chest = game.decks[settings.DECK_CHEST]
    assert not [c for c in chest.draw_pile if c.title == "Balbinka"]


def test_ability_charges_reach_the_card(library):
    config = SessionConfig(num_players=4, board_cells=24, seed=3,
                           ability_uses={"Dziad": 4, "ChatGPT": 1})
    game = create_game(config, library)
    deck = game.decks[settings.DECK_CHARACTERS]
    pool = list(deck.draw_pile) + [p.character for p in game.players
                                   if p.character is not None]
    dziad = next(c for c in pool if c.title == "Dziad")
    assert dziad.uses_total == 4 and dziad.uses_left == 4


def test_an_empty_mapping_still_means_the_printed_decks(library):
    """What every existing test and every older client sends."""
    plain = create_game(SessionConfig(num_players=4, board_cells=24, seed=3),
                        library)
    for deck_id in (settings.DECK_MOVEMENT, settings.DECK_CHEST):
        printed = sum(card.count for card in library.deck(deck_id).cards)
        deck = plain.decks[deck_id]
        # Only that deck's cards count as held: an opening hand is movement
        # cards, and counting the whole hand made the chest look overdealt.
        held = sum(1 for p in plain.players for c in p.hand
                   if c.deck_id == deck_id)
        assert deck.draw_count + deck.discard_count + held == printed


def test_the_clamps_reject_junk():
    assert clamp_card_counts({"A": -4, "B": 999, "C": "x"}) == {
        "A": RULES.card_count_min, "B": RULES.card_count_max}
    assert clamp_ability_uses({"A": -1}) == {"A": RULES.ability_uses_min}
    # Sorted, because this ends up in the lobby snapshot clients compare.
    assert list(clamp_card_counts({"Z": 1, "A": 1})) == ["A", "Z"]


# ── multiplayer ──────────────────────────────────────────────────────────────
def test_the_new_settings_reach_every_client(library):
    table = Table(library)
    host, clients = table.seated("Kuba", "Ola", "Antek")
    host.set_settings(movement_counts={"Troll": 4},
                      chest_counts={"Balbinka": 3},
                      ability_uses={"Dziad": 6})
    table.pump()
    for service in [host, *clients]:
        lobby = service.lobby_state
        assert lobby.movement_counts["Troll"] == 4
        assert lobby.chest_counts["Balbinka"] == 3
        assert lobby.ability_uses["Dziad"] == 6
    table.close()


def test_the_resized_decks_are_identical_on_every_machine(library):
    table = Table(library)
    host, clients = table.seated("Kuba", "Ola", "Antek")
    host.set_settings(movement_counts={"Troll": 4}, ability_uses={"Dziad": 6})
    table.pump()
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients)

    room = table.room(host.room_code)
    wanted = room.state.decks[settings.DECK_MOVEMENT].draw_count
    for service in [host, *clients]:
        assert service.state.decks[settings.DECK_MOVEMENT].draw_count == wanted
    assert all_agree(host, *clients)
    table.close()


def test_a_partial_update_merges_rather_than_replacing(library):
    """The panel sends what it knows; an older host sends less."""
    table = Table(library)
    host, _ = table.seated("Kuba", "Ola", "Antek")
    host.set_settings(movement_counts={"Troll": 4})
    table.pump()
    host.set_settings(chest_counts={"Balbinka": 3})
    table.pump()
    room = table.room(host.room_code)
    assert room.lobby.movement_counts["Troll"] == 4, "the first update survived"
    assert room.lobby.chest_counts["Balbinka"] == 3
    table.close()


def test_the_host_screen_offers_the_same_panel(library):
    """One deck needs one place to describe it."""
    app = App(Layout(), headless=True, size=WINDOW)
    screen = HostSetupScreen(app, library)
    app.push(screen)
    assert [tab.id for tab in screen.settings_panel.tabs] == [
        settings.DECK_MOVEMENT, settings.DECK_MODS, settings.DECK_CHEST,
        "abilities", "variants", "rules", "copy",
    ]


# ── part 2: the new defaults ─────────────────────────────────────────────────
def test_the_chest_opens_on_round_six():
    assert RULES.chest_open_default == 6
    assert SessionConfig().chest_open_round == 6


def test_the_first_mod_round_is_three():
    assert RULES.mod_round_first_default == 3
    assert SessionConfig().mod_round_first == 3


# ── part 3: scaling ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("bigger,smaller", [
    ((1920, 1200), (1920, 1080)),
    ((2560, 1440), (1920, 1200)),
    ((3840, 2160), (2560, 1440)),
])
def test_a_taller_display_gets_a_bigger_card(bigger, smaller):
    """The ceilings used to stop at 1224 tall and hand the rest to margin."""
    big, small = Layout(*bigger), Layout(*smaller)
    assert big.hand_card_size[1] > small.hand_card_size[1]
    assert big.panel_card_size[1] > small.panel_card_size[1]


def test_the_laptop_resolution_is_no_longer_the_odd_one_out():
    """1920x1200 was running at 1.11 while 2560x1440 was comfortable at 1.33."""
    laptop = Layout(1920, 1200)
    assert laptop.ui_scale >= 1.19
    assert laptop.hand_card_size[1] >= 250


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (1920, 1200),
                                  (2560, 1440), (3840, 2160)])
def test_the_board_still_gets_the_room(size):
    """Bigger cards must not be paid for out of the board."""
    layout = Layout(*size)
    board = layout.board_viewport
    assert board.width >= layout.win_w * 0.58
    assert board.width > layout.left_w + layout.right_w


@pytest.mark.parametrize("size", [(1920, 1200), (2560, 1440)])
def test_an_enlarged_preview_is_painted_at_its_full_size(library, size):
    """Never a zoomed-up texture — the whole point of stage 9's quantised()."""
    app = App(Layout(), headless=True, size=size)
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=5),
                        library)
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    base = app.layout.hand_card_size
    enlarged = screen.cards.quantised(base, 2.15)
    assert enlarged[1] > base[1] * 2, "the paint size grew with the preview"
    surface = screen.cards.face(state.active_player.hand[0], enlarged)
    assert surface.get_size() == enlarged, "painted at size, not scaled after"


# ── parts 4 and 5: the two things that were missed ───────────────────────────
def game_screen(library, size=(1920, 1200)):
    app = App(Layout(), headless=True, size=size)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=77), library)
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    screen.board_view.build()
    return screen


def test_a_failed_check_raises_a_card_beside_the_board(library):
    screen = game_screen(library)
    pawn = screen.state.library.pawns[2]
    screen.bus.emit(ev.PawnEliminated(pawn.id))
    notice = screen.elimination_notice
    assert notice.active
    assert notice.pawn_name == pawn.name
    assert notice.color == pawn.color


def test_the_notice_sits_next_to_the_board_and_not_over_the_hand(library):
    screen = game_screen(library)
    layout = screen.app.layout
    rect = layout.elimination_card_rect()
    board = layout.board_viewport
    assert board.contains(rect), "it belongs to the board area"
    assert rect.left < board.centerx, "immediately to the LEFT"
    assert not rect.colliderect(layout.hand_area)


def test_the_notice_fades_and_goes_away_on_its_own(library):
    """It must never block the game permanently."""
    screen = game_screen(library)
    screen.bus.emit(ev.PawnEliminated(screen.state.library.pawns[1].id))
    notice = screen.elimination_notice
    notice.update(notice.FADE_IN / 2)
    faded_in = notice.alpha
    assert 0.0 < faded_in < 1.0, "it fades in rather than appearing"
    notice.update(notice.FADE_IN + notice.HOLD)
    assert notice.alpha < 1.0, "and then fades out"
    notice.update(notice.FADE_OUT + 0.1)
    assert not notice.active


def test_the_notice_swallows_no_input(library):
    """Presentation only: the table underneath stays live."""
    screen = game_screen(library)
    screen.bus.emit(ev.PawnEliminated(screen.state.library.pawns[1].id))
    before = screen.state.active_player_index
    rect = screen.app.layout.elimination_card_rect()
    click(screen, rect.center)
    assert screen.state.active_player_index == before
    assert screen.elimination_notice.active, "a click does not dismiss it either"


def test_the_notice_is_actually_painted(library):
    screen = game_screen(library)
    screen.bus.emit(ev.PawnEliminated(screen.state.library.pawns[1].id))
    app = screen.app
    for _ in range(12):
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)
    rect = app.layout.elimination_card_rect()
    colours = {app.canvas.get_at((x, y))[:3]
               for x in range(rect.left + 4, rect.right - 4, 7)
               for y in range(rect.top + 4, rect.bottom - 4, 7)}
    assert len(colours) > 6, "something substantial was drawn there"


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1200), (2560, 1440)])
def test_the_elimination_mark_scales_with_the_window(size):
    """It was a flat 4-pixel line, which is a hairline on a 1440p panel."""
    surface = pygame.Surface((300, 300), pygame.SRCALPHA)
    layout = Layout(*size)
    app = App(layout, headless=True, size=size)
    mark_size = int(layout.pk_circle_d * 0.92)
    app.renderer.heavy_cross((150, 150), mark_size, (220, 60, 60), (20, 20, 20),
                             surface, scale=layout.ui_scale)
    # Measure the stroke by walking the row through the centre of the cross.
    lit = [x for x in range(300) if surface.get_at((x, 150))[3] > 0]
    assert lit, "nothing was drawn"
    thickness = sum(1 for x in range(150 - mark_size, 150 + mark_size)
                    if surface.get_at((x, 150 - mark_size // 2))[3] > 0)
    assert thickness >= 4, f"still a hairline at {size}"


def test_a_bigger_window_draws_a_bigger_mark():
    small = pygame.Surface((400, 400), pygame.SRCALPHA)
    big = pygame.Surface((400, 400), pygame.SRCALPHA)
    for surface, size in ((small, (1280, 760)), (big, (2560, 1440))):
        layout = Layout(*size)
        app = App(layout, headless=True, size=size)
        app.renderer.heavy_cross((200, 200), int(layout.pk_circle_d * 0.92),
                                 (220, 60, 60), (20, 20, 20), surface,
                                 scale=layout.ui_scale)
    def painted(surface):
        return sum(1 for x in range(0, 400, 2) for y in range(0, 400, 2)
                   if surface.get_at((x, y))[3] > 0)
    assert painted(big) > painted(small)


def test_the_notepad_still_draws_every_colour(library):
    """The redesign must not lose the colours it is a list of."""
    screen = game_screen(library)
    hunter = next(p for p in screen.state.players if not p.is_piotrek)
    screen.view_seat = hunter.index
    screen.state.eliminated_pawns.append(screen.state.library.pawns[1].id)
    app = screen.app
    for _ in range(6):
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)
    top = app.layout.pawn_grid_top(False)
    rects = app.layout.pawn_grid_rects(top, len(screen.state.library.pawns))
    assert len(rects) == len(screen.state.library.pawns)
    for rect in rects:
        assert app.canvas.get_rect().contains(rect)


# ── a heading that outgrew its own row ───────────────────────────────────────
@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (1920, 1200),
                                  (2560, 1440), (3840, 2160)])
def test_a_deck_heading_never_runs_into_its_own_counter(library, size):
    """Found by looking, not by the suite: "UMIEJĘTNOŚCI PIOTRKA2 / 0".

    The existing collision test walks the LEFT column only, so the longest
    deck name in the game — the one in Piotrek's own panel — was never
    measured, and it ran straight into its counter the moment stage 26 made
    the type bigger.  This walks the character panel's decks as well, by
    RENDERING them and reading back what was actually drawn.
    """
    app = App(Layout(), headless=True, size=size)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=4), library)
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    piotrek = next(p for p in state.players if p.is_piotrek)
    screen.view_seat = piotrek.index

    drawn: list = []
    original = app.renderer.text
    spaced = app.renderer.spaced_text

    def record_text(text, font, colour, surface=None, **kwargs):
        rect = original(text, font, colour, surface, **kwargs)
        drawn.append((str(text), rect))
        return rect

    def record_spaced(text, font, colour, surface=None, **kwargs):
        rect = spaced(text, font, colour, surface, **kwargs)
        drawn.append((str(text), rect))
        return rect

    app.renderer.text = record_text            # type: ignore[assignment]
    app.renderer.spaced_text = record_spaced   # type: ignore[assignment]
    try:
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)
    finally:
        app.renderer.text = original           # type: ignore[assignment]
        app.renderer.spaced_text = spaced      # type: ignore[assignment]

    headings = [(text, rect) for text, rect in drawn
                if text.isupper() and len(text) > 6]
    assert headings, "nothing was recorded — the harness missed the draw"
    for text, rect in headings:
        for other, other_rect in drawn:
            if other is text or other_rect is rect:
                continue
            # A counter is short and numeric; those are the ones a heading is
            # allowed to sit beside and must never sit on top of.
            if other.replace("/", "").replace(" ", "").isdigit():
                assert not rect.colliderect(other_rect), (
                    f"{text!r} collides with {other!r} at {size}")
