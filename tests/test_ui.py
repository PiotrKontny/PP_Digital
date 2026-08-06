"""
UI tests.

These drive the real screen with synthetic pygame events against SDL's dummy
video driver — no window, no human.  They are what stops a layout change from
silently detaching a button from the action it triggers, which was the failure
mode the prototype was most exposed to: rects and behaviour lived far apart, so
moving one never complained about the other.

This stage added the fan, the card play gesture and a resizable window, so
there are tests for all three, including the one that matters most: that a
played card actually moves a pawn and lands in the discard pile.
"""

from __future__ import annotations

import math
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
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.menu import MenuScreen

WINDOW = (1920, 1080)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def screen(library) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3, seed=77),
        library,
    )
    game_screen = GameScreen(app, LocalSession(state))
    app.push(game_screen)
    return game_screen


def click(screen: GameScreen, pos, button: int = 1) -> None:
    pos = (int(pos[0]), int(pos[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button), pos
    )
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button), pos
    )


def frame(screen: GameScreen, dt: float = 1 / 60, mouse=(0, 0)) -> None:
    app = screen.app
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(dt, mouse)
    screen.draw(app.canvas)


def settle(screen: GameScreen, frames: int = 60, mouse=(0, 0)) -> None:
    for _ in range(frames):
        frame(screen, mouse=mouse)


def give_card(screen: GameScreen, predicate):
    """Move a matching card from the movement deck into the active hand."""
    deck = screen.state.deck(settings.DECK_MOVEMENT)
    card = next(c for c in deck.draw_pile if predicate(c))
    deck.draw_pile.remove(card)
    screen.state.active_player.add_card(card)
    return card


# ── it draws at all ──────────────────────────────────────────────────────────
def test_the_screen_renders_without_raising(screen):
    for _ in range(5):
        frame(screen)


def test_something_is_actually_painted(screen):
    """A pass that renders nothing would satisfy every other test here."""
    frame(screen)
    canvas = screen.app.canvas
    colours = {
        canvas.get_at((x, y))[:3]
        for x in range(0, canvas.get_width(), 37)
        for y in range(0, canvas.get_height(), 37)
    }
    assert len(colours) > 20, "the frame is suspiciously uniform"


# ── responsive layout ────────────────────────────────────────────────────────
@pytest.mark.parametrize("size", [(1280, 760), (1600, 900), (1920, 1080), (2560, 1440)])
def test_regions_never_overlap_or_leave_the_window(size):
    layout = Layout(*size)
    window = pygame.Rect(0, 0, layout.win_w, layout.win_h)
    regions = {
        "left": layout.left_panel,
        "turn": layout.turn_bar,
        "players": layout.player_strip,
        "board": layout.board_viewport,
        "right": layout.right_panel,
        "hand": layout.hand_area,
    }
    for name, rect in regions.items():
        assert window.contains(rect), f"{name} escapes the window"
    names = list(regions)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not regions[a].colliderect(regions[b]), f"{a} overlaps {b}"


def test_a_bigger_window_gives_a_bigger_board():
    small = Layout(1280, 760).board_viewport
    large = Layout(2560, 1440).board_viewport
    assert large.width > small.width * 1.5
    assert large.height > small.height * 1.5


def test_panel_contents_stay_inside_their_panel():
    for size in ((1280, 760), (1920, 1080), (2560, 1440)):
        layout = Layout(*size)
        for slot in range(layout.mod_slot_count):
            assert layout.left_panel.contains(layout.mod_slot_rect(slot))
        for column in range(len(settings.TABLE_DECKS)):
            assert layout.left_panel.contains(layout.deck_draw_rect(column))
            assert layout.left_panel.contains(layout.deck_discard_rect(column))
        for show_skill in (False, True):
            rects = layout.character_panel(show_skill)
            assert layout.right_panel.contains(rects["card"])
            assert layout.right_panel.contains(rects["char_disc"])
        grid = layout.pawn_grid_panel(layout.pawn_grid_top(False))
        assert layout.right_panel.contains(grid)


def test_resizing_the_window_moves_the_board_with_it(screen):
    frame(screen)
    screen.app.resize((1600, 900))
    frame(screen)
    assert screen.app.layout.win_w == 1600
    assert screen.board_view.camera.viewport == screen.app.layout.board_viewport


# ── deck interactions ────────────────────────────────────────────────────────
def test_clicking_every_draw_pile_draws_from_the_right_deck(screen):
    state = screen.state
    for column, deck_id in enumerate(settings.TABLE_DECKS):
        before = state.deck(deck_id).draw_count
        click(screen, screen.app.layout.deck_draw_rect(column).center)
        assert state.deck(deck_id).draw_count == before - 1


# ── the hand fan ─────────────────────────────────────────────────────────────
def test_the_fan_lays_cards_out_in_an_arc(screen):
    for _ in range(4):
        click(screen, screen.app.layout.deck_draw_rect(0).center)
    settle(screen)

    fan = screen.hand
    positions = [fan.slots[card.uid].position for card in fan.hand]
    xs = [p[0] for p in positions]
    assert xs == sorted(xs), "cards run left to right"
    # The middle of an arc sits higher than its ends.
    middle = positions[len(positions) // 2][1]
    assert middle < positions[0][1] and middle < positions[-1][1]
    angles = [fan.slots[card.uid].angle for card in fan.hand]
    assert angles[0] < 0 < angles[-1], "cards tilt away from the centre"


def test_hovering_lifts_and_straightens_a_card(screen):
    for _ in range(3):
        click(screen, screen.app.layout.deck_draw_rect(0).center)
    settle(screen)
    fan = screen.hand

    uid = fan.hand[0].uid
    resting = fan.slots[uid].position
    resting_angle = fan.slots[uid].angle

    settle(screen, 40, mouse=(int(resting[0]), int(resting[1])))
    assert fan.hovered == uid
    assert fan.slots[uid].position[1] < resting[1] - 10, "hovered card rises"
    assert abs(fan.slots[uid].angle) < abs(resting_angle)
    assert fan.slots[uid].scale > 1.0


def test_the_hit_area_follows_the_tilt(screen):
    for _ in range(5):
        click(screen, screen.app.layout.deck_draw_rect(0).center)
    settle(screen)
    fan = screen.hand
    for card in fan.hand:
        slot = fan.slots[card.uid]
        assert fan._card_at(slot.position) is not None
    far = (fan.layout.hand_area.left + 5, fan.layout.hand_area.top + 5)
    assert fan._card_at(far) is None


def test_a_new_card_flies_in_rather_than_appearing(screen):
    click(screen, screen.app.layout.deck_draw_rect(0).center)
    frame(screen)
    fan = screen.hand
    uid = fan.hand[-1].uid
    start = fan.slots[uid].position
    settle(screen, 45)
    assert math.dist(start, fan.slots[uid].position) > 20


# ── playing cards ────────────────────────────────────────────────────────────
def test_clicking_a_playable_card_plays_it(screen):
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)

    slot = screen.hand.slots[card.uid]
    click(screen, slot.position)

    assert card not in state.active_player.hand
    assert state.deck(settings.DECK_MOVEMENT).find_discarded(card.uid) is card
    assert state.board.pawn_tiles["żółty"] == 0


def test_clicking_a_card_the_engine_cannot_resolve_still_discards_it(screen):
    """A card with no effect keeps the prototype's behaviour: click discards.

    Every movement card now has an effect or an on-draw effect, so the card
    that demonstrates this is dealt from the chest deck — which is the honest
    version of the test anyway, since "no implementation yet" is where the
    chest cards are and no longer where the movement cards are.
    """
    state = screen.state
    deck = state.deck(settings.DECK_CHEST)
    card = next(c for c in deck.draw_pile
                if c.effect is None and not c.locked)
    deck.draw_pile.remove(card)
    state.active_player.add_card(card)
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    assert card not in state.active_player.hand
    assert deck.discard_count >= 1
    assert not state.board.pawn_tiles


def test_a_locked_card_cannot_be_clicked_away(screen):
    """Troll is not a card you play, and not one you throw away either.

    Clicking it used to discard it — which would have been a free way out of
    the forced turn it just booked.  The engine refuses both the play and the
    discard, so a client that does not draw the lock is refused too.
    """
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Troll")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    assert card in state.active_player.hand
    assert state.deck(settings.DECK_MOVEMENT).find_discarded(card.uid) is None


def test_dragging_a_card_onto_the_board_plays_it(screen):
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Zerówka - zielony")
    settle(screen)
    fan = screen.hand
    start = fan.slots[card.uid].position
    board_centre = screen.app.layout.board_viewport.center

    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    move = (start[0] + 40, start[1] - 40)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=move, rel=(40, -40), buttons=(1, 0, 0)),
        move,
    )
    assert fan.dragging == card.uid

    screen.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=board_centre, rel=(0, 0),
                           buttons=(1, 0, 0)),
        board_centre,
    )
    frame(screen)
    assert screen.board_view.preview_route, "the route should be highlighted"

    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=board_centre, button=1), board_centre
    )
    assert card not in state.active_player.hand
    assert state.board.pawn_tiles["zielony"] == 0


def test_dropping_a_card_outside_the_board_returns_it_to_the_hand(screen):
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Zerówka - czerwony")
    settle(screen)
    start = screen.hand.slots[card.uid].position

    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    away = (screen.app.layout.left_panel.centerx, screen.app.layout.left_panel.centery)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=away, rel=(0, 0), buttons=(1, 0, 0)),
        away,
    )
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=away, button=1), away
    )
    assert card in state.active_player.hand
    settle(screen, 70)
    resting, _ = screen.hand.resting_transform(
        state.active_player.hand.index(card), len(state.active_player.hand)
    )
    assert math.dist(screen.hand.slots[card.uid].position, resting) < 6


def test_an_illegal_play_is_refused_with_a_reason(screen):
    """A backward card on a pawn still in camp cannot be played."""
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Wejściówka - różowy")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    assert card in state.active_player.hand
    assert screen.status_bar.message is not None
    assert "różowy" in screen.status_bar.message


def test_a_played_card_appears_in_the_recently_played_strip(screen):
    card = give_card(screen, lambda c: c.title == "Zerówka - niebieski")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    frame(screen)
    assert screen.recently_played.entries
    assert screen.recently_played.entries[0].card is card


def test_the_recently_played_strip_forgets_after_a_while(screen):
    card = give_card(screen, lambda c: c.title == "Zerówka - niebieski")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    for _ in range(300):
        frame(screen, dt=0.05)
    assert not screen.recently_played.entries


# ── token movement ───────────────────────────────────────────────────────────
def answer_choice(screen: GameScreen, option: int = 0) -> bool:
    """If the screen is waiting for a decision, answer it via the prompt."""
    if screen.pending_choice is None:
        return False
    frame(screen)          # lay the prompt buttons out
    button = screen.choice_prompt.options[option].rect
    click(screen, button.center)
    return True


def test_a_played_card_walks_the_pawn_through_every_field(screen):
    state = screen.state
    state.board.place_pawn("żółty", 0)
    state._sync_token_positions()
    screen.board_view.visual["żółty"] = state.tokens["żółty"].position

    card = give_card(screen, lambda c: c.title == "Fillerski przedmiot - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    answer_choice(screen)

    walk = screen.board_view.walks.get("żółty")
    assert walk is not None, "the pawn should be walking, not teleporting"
    assert len(walk.points) >= 3, "two steps means two intermediate points"

    seen = set()
    for _ in range(120):
        frame(screen)
        tile = state.board.tile_near(screen.board_view.visual["żółty"], 40)
        if tile is not None:
            seen.add(tile.number)
        if not screen.board_view.walks:
            break
    assert {1, 2, 3} <= seen, f"pawn skipped a position: {sorted(seen)}"
    assert state.board.position_of_pawn("żółty") == 2


def test_a_carried_pawn_travels_with_the_one_below(screen):
    state = screen.state
    for pawn in ("żółty", "różowy"):
        state.board.place_pawn(pawn, 0)
    state._sync_token_positions()

    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    answer_choice(screen)
    assert state.board.position_of_pawn("różowy") == 1
    assert "różowy" in screen.board_view.walks


# ── the 12a / 12b decision ───────────────────────────────────────────────────
def doubled_screen(library, **kwargs) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(
        SessionConfig(num_players=4, board_cells=30, seed=11,
                      double_frequency=1.0, **kwargs),
        library,
    )
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    return screen


def test_landing_on_a_widened_stretch_stops_and_asks(library):
    screen = doubled_screen(library)
    state = screen.state
    state.board.place_pawn("żółty", state.board.positions[0].tiles[0].index)
    state._sync_token_positions()

    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)

    assert screen.pending_choice is not None
    assert [label for _, label in screen.pending_choice.options] == ["2a", "2b"]
    assert screen.board_view.choice_tiles == screen.pending_choice.tiles
    assert state.board.position_of_pawn("żółty") == 0, "nothing moves until asked"
    frame(screen)


def test_the_rest_of_the_interface_waits_for_the_answer(library):
    screen = doubled_screen(library)
    state = screen.state
    state.board.place_pawn("żółty", state.board.positions[0].tiles[0].index)
    state._sync_token_positions()
    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    assert screen.pending_choice is not None

    before = state.deck(settings.DECK_MOVEMENT).draw_count
    click(screen, screen.app.layout.deck_draw_rect(0).center)
    click(screen, screen.app.layout.round_counter_rects()["plus"].center)
    assert state.deck(settings.DECK_MOVEMENT).draw_count == before
    assert state.round_number == 1
    assert screen.pending_choice is not None


def test_clicking_a_half_finishes_the_move(library):
    screen = doubled_screen(library)
    state = screen.state
    state.board.place_pawn("żółty", state.board.positions[0].tiles[0].index)
    state._sync_token_positions()
    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)

    chosen = screen.pending_choice.tiles[1]
    tile = state.board.tile(chosen)
    click(screen, screen.board_view.camera.world_to_screen(tile.position))

    assert screen.pending_choice is None
    assert screen.board_view.choice_tiles == []
    assert state.board.pawn_tile("żółty").index == chosen
    assert state.board.pawn_tile("żółty").label == "2b"
    assert card not in state.active_player.hand


def test_escape_cancels_a_pending_choice_and_keeps_the_card(library):
    screen = doubled_screen(library)
    state = screen.state
    state.board.place_pawn("żółty", state.board.positions[0].tiles[0].index)
    state._sync_token_positions()
    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)

    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), (0, 0))
    assert screen.pending_choice is None
    assert screen.board_view.choice_tiles == []
    assert card in state.active_player.hand
    assert screen.app.running


# ── stacks ───────────────────────────────────────────────────────────────────
def test_hovering_a_tower_fans_it_out_so_each_pawn_is_clickable(screen):
    state = screen.state
    stack = ["czerwony", "zielony", "niebieski"]
    for pawn in stack:
        state.board.place_pawn(pawn, 4)
    state._sync_token_positions()
    for pawn in stack:
        screen.board_view.visual[pawn] = state.tokens[pawn].position

    view = screen.board_view
    tile = state.board.tile(4)
    mouse = view.camera.world_to_screen(tile.position)
    settle(screen, 60, mouse=mouse)

    assert view.expanded_tile == 4
    assert view.expansion > 0.9
    positions = [view.display_position(p) for p in stack]
    for i, a in enumerate(positions):
        for b in positions[i + 1:]:
            assert math.dist(a, b) > 20, "fanned pawns must be separable"

    picked = {view.token_at(view.camera.world_to_screen(p)) for p in positions}
    assert picked == set(stack), "every pawn in the tower can be picked"


def test_pawns_stay_easy_to_grab_when_zoomed_out(screen):
    state = screen.state
    view = screen.board_view
    frame(screen)
    view.camera.set_zoom(view.camera.min_zoom)
    view.camera.snap()
    frame(screen)

    pawn_id = state.library.pawns[0].id
    centre = view.camera.world_to_screen(view.display_position(pawn_id))
    near_miss = (centre[0] + 9, centre[1] + 9)
    assert view.token_at(near_miss) == pawn_id


def test_dragging_a_pawn_still_snaps_it_to_a_field(screen):
    state = screen.state
    view = screen.board_view
    frame(screen)

    pawn_id = state.library.pawns[0].id
    start = view.camera.world_to_screen(view.display_position(pawn_id))
    target_tile = state.board.tiles[2]

    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    assert view.dragging == pawn_id
    to = view.camera.world_to_screen(target_tile.position)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=to, rel=(0, 0), buttons=(1, 0, 0)), to
    )
    screen.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=to, button=1), to)

    assert view.dragging is None
    assert state.tokens[pawn_id].tile_index == target_tile.index


# ── mods, tiles, renaming ────────────────────────────────────────────────────
def test_right_click_then_slot_click_places_a_mod(screen):
    state = screen.state
    click(screen, screen.app.layout.deck_draw_rect(1).center)  # a Mod Patusa card
    settle(screen)
    card = next(c for c in state.active_player.hand if c.deck_id == settings.DECK_MODS)

    position = screen.hand.slots[card.uid].position
    click(screen, position, button=3)
    assert screen.pending_mod_uid == card.uid

    click(screen, screen.app.layout.mod_slot_rect(0).center)
    assert screen.pending_mod_uid is None
    assert state.mod_slots[0] is not None and state.mod_slots[0].uid == card.uid


def test_middle_click_discards_a_card(screen):
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position, button=2)
    assert card not in state.active_player.hand
    assert not state.board.pawn_tiles, "discarding must not run the effect"


def test_round_counter_buttons(screen):
    rects = screen.app.layout.round_counter_rects()
    click(screen, rects["plus"].center)
    assert screen.state.round_number == 2
    click(screen, rects["minus"].center)
    assert screen.state.round_number == 1
    click(screen, rects["minus"].center)
    assert screen.state.round_number == 1, "never below round 1"


def test_clicking_a_player_tile_makes_it_active(screen):
    count = len(screen.state.players)
    click(screen, screen.app.layout.player_tile_rect(2, count).center)
    assert screen.state.active_player_index == 2


def test_the_pencil_icon_renames_a_player(screen):
    layout = screen.app.layout
    tile = layout.player_tile_rect(0, len(screen.state.players))
    click(screen, layout.rename_rect(tile).center)
    assert screen.rename.active

    for char in "Ala":
        screen.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=char), (0, 0))
    screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, unicode="\r"), (0, 0)
    )
    assert not screen.rename.active
    assert screen.state.players[0].name == "Ala"


def test_renaming_swallows_other_input(screen):
    layout = screen.app.layout
    tile = layout.player_tile_rect(0, len(screen.state.players))
    click(screen, layout.rename_rect(tile).center)
    before = screen.state.deck(settings.DECK_MOVEMENT).draw_count
    click(screen, layout.deck_draw_rect(0).center)
    assert screen.state.deck(settings.DECK_MOVEMENT).draw_count == before


def test_the_notepad_is_filled_in_by_the_game_not_by_the_player(screen):
    """Stage 17 took the pencil away.

    A colour is crossed off when a checked tower turned out not to be Piotrek,
    which is something only the authority knows.  A hunter clicking a circle
    used to record a private hunch on a shared-looking panel; now the panel
    means one thing and means it on every machine.
    """
    state = screen.state
    hunter = next(p for p in state.players if not p.is_piotrek)
    click(screen, screen.app.layout.player_tile_rect(hunter.index,
                                                     len(state.players)).center)
    frame(screen)

    top = screen.app.layout.pawn_grid_top(show_skill=False)
    rects = screen.app.layout.pawn_grid_rects(top, len(state.library.pawns))
    pawn_id = state.library.pawns[0].id
    click(screen, rects[0].center)
    assert not hunter.marks, "clicking records nothing any more"
    assert pawn_id not in state.eliminated_pawns

    state.apply(cmd.EliminatePawn(pawn_id=pawn_id), local=False)
    frame(screen)
    assert pawn_id in state.eliminated_pawns, "the game crossed it off"


def test_escape_cancels_a_staged_mod_instead_of_quitting(screen):
    click(screen, screen.app.layout.deck_draw_rect(1).center)
    settle(screen)
    card = next(
        c for c in screen.state.active_player.hand if c.deck_id == settings.DECK_MODS
    )
    click(screen, screen.hand.slots[card.uid].position, button=3)
    assert screen.pending_mod_uid is not None

    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), (0, 0))
    assert screen.pending_mod_uid is None
    assert screen.app.running


# ── menu ─────────────────────────────────────────────────────────────────────
def test_the_menu_produces_a_playable_config(library):
    app = App(Layout(), headless=True, size=WINDOW)
    captured = []
    menu = MenuScreen(app, library, captured.append)
    app.push(menu)
    for _ in range(3):
        app.canvas.fill(app.renderer.theme.background)
        menu.update(1 / 60, (0, 0))
        menu.draw(app.canvas)

    minus = menu.players_stepper.rects["minus1"].center
    menu.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=minus, button=1), minus
    )
    assert menu.num_players == RULES.max_players - 1

    start = menu.start_button.rect.center
    menu.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    assert captured, "the start button must hand back a SessionConfig"
    state = create_game(captured[0], library)
    assert len(state.players) == RULES.max_players - 1


def test_the_menu_never_offers_one_character_to_two_seats(library):
    app = App(Layout(), headless=True, size=WINDOW)
    menu = MenuScreen(app, library, lambda cfg: None)
    app.push(menu)
    for index in (0, 1):
        centre = menu.checkboxes[index].rect.center
        menu.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=centre, button=1), centre
        )
    menu.update(1 / 60, (0, 0))
    assert menu.choices[0] != menu.choices[1]
    assert menu.choices[0] in menu.dropdowns[1].disabled_options


# ── menu validation (stage 3) ────────────────────────────────────────────────
def make_menu(library, size=WINDOW):
    app = App(Layout(), headless=True, size=size)
    captured = []
    menu = MenuScreen(app, library, captured.append)
    app.push(menu)
    return menu, captured


def menu_click(menu, pos) -> None:
    pos = (int(pos[0]), int(pos[1]))
    menu.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), pos
    )


def choose(menu, seat: int, title: str) -> None:
    """Turn off 'random' for a seat and set it to a specific character."""
    menu_click(menu, menu.checkboxes[seat].rect.center)
    menu.update(1 / 60, (0, 0))
    menu.choices[seat] = title
    menu.update(1 / 60, (0, 0))


def test_random_characters_need_no_validation(library):
    menu, _ = make_menu(library)
    assert menu.validation_error() is None
    assert menu.can_start


def test_hand_picked_seats_without_piotrek_cannot_start(library):
    menu, captured = make_menu(library)
    others = [t for t in menu.titles if t != settings.PIOTREK_TITLE]
    for seat in range(menu.num_players):
        choose(menu, seat, others[seat])

    error = menu.validation_error()
    assert error is not None
    assert settings.PIOTREK_TITLE in error
    assert not menu.can_start

    menu_click(menu, menu.start_button.rect.center)
    assert not captured, "the game must not start without Piotrek"


def test_choosing_piotrek_clears_the_error(library):
    menu, captured = make_menu(library)
    others = [t for t in menu.titles if t != settings.PIOTREK_TITLE]
    for seat in range(menu.num_players):
        choose(menu, seat, others[seat])
    assert not menu.can_start

    menu.choices[0] = settings.PIOTREK_TITLE
    menu.update(1 / 60, (0, 0))
    assert menu.validation_error() is None

    menu_click(menu, menu.start_button.rect.center)
    assert captured
    state = create_game(captured[0], library)
    assert sum(1 for p in state.players if p.is_piotrek) == 1


def test_one_random_seat_is_enough_to_guarantee_piotrek(library):
    menu, _ = make_menu(library)
    others = [t for t in menu.titles if t != settings.PIOTREK_TITLE]
    for seat in range(1, menu.num_players):
        choose(menu, seat, others[seat])
    assert menu.choices[0] is None
    assert menu.can_start


def test_the_error_message_is_drawn_in_red(library):
    menu, _ = make_menu(library)
    others = [t for t in menu.titles if t != settings.PIOTREK_TITLE]
    for seat in range(menu.num_players):
        choose(menu, seat, others[seat])

    app = menu.app
    app.renderer.begin(app.canvas)
    app.canvas.fill((0, 0, 0))
    menu.update(1 / 60, (0, 0))
    menu.draw(app.canvas)

    band = pygame.Rect(0, menu.start_button.rect.bottom + 6,
                       app.layout.win_w, 40).clip(app.canvas.get_rect())
    reds = 0
    for x in range(band.left, band.right, 2):
        for y in range(band.top, band.bottom, 2):
            red, green, blue = app.canvas.get_at((x, y))[:3]
            if red > 150 and red > green + 60 and red > blue + 60:
                reds += 1
    assert reds > 30, "the validation message should be clearly red"


def test_the_menu_can_configure_how_often_rows_double(library):
    menu, captured = make_menu(library)
    menu.double_percent = 0
    menu_click(menu, menu.start_button.rect.center)
    assert captured[0].double_frequency == 0.0
    state = create_game(captured[0], library)
    assert not any(p.is_doubled for p in state.board.positions)


# ── dragging the board (stage 3) ─────────────────────────────────────────────
def drag(screen: GameScreen, start, end, button: int = 1) -> None:
    start = (int(start[0]), int(start[1]))
    end = (int(end[0]), int(end[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=button), start
    )
    screen.handle_event(
        pygame.event.Event(
            pygame.MOUSEMOTION, pos=end, rel=(end[0] - start[0], end[1] - start[1]),
            buttons=(1, 0, 0),
        ),
        end,
    )
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=button), end
    )


def empty_board_point(screen: GameScreen):
    """A point inside the viewport with no pawn under it."""
    viewport = screen.app.layout.board_viewport
    for dx in range(0, viewport.width // 2, 40):
        for dy in range(0, viewport.height // 2, 40):
            point = (viewport.left + 30 + dx, viewport.top + 30 + dy)
            if screen.board_view.token_at(point) is None:
                return point
    raise AssertionError("no empty spot on the board")


def test_dragging_empty_board_pans_the_view(screen):
    view = screen.board_view
    frame(screen)
    view.camera.set_zoom(view.camera.max_zoom)
    view.camera.snap()
    frame(screen)

    before = view.camera.center
    start = empty_board_point(screen)
    drag(screen, start, (start[0] + 120, start[1] + 60))
    assert view.camera.center != before
    assert view.map_dragging is False


def test_dragging_the_board_follows_the_cursor_exactly(screen):
    """No smoothing while grabbing: the world must not lag behind the hand."""
    view = screen.board_view
    frame(screen)
    view.camera.set_zoom(view.camera.max_zoom)
    view.camera.snap()
    frame(screen)

    start = empty_board_point(screen)
    world_before = view.camera.screen_to_world(start)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    moved = (start[0] + 90, start[1])
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=moved, rel=(90, 0), buttons=(1, 0, 0)),
        moved,
    )
    world_after = view.camera.screen_to_world(moved)
    assert world_after[0] == pytest.approx(world_before[0], abs=1.5)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=moved, button=1), moved
    )


def test_dragging_a_pawn_wins_over_dragging_the_board(screen):
    state = screen.state
    view = screen.board_view
    frame(screen)

    pawn_id = state.library.pawns[0].id
    start = view.camera.world_to_screen(view.display_position(pawn_id))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    assert view.dragging == pawn_id
    assert not view.map_dragging
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=start, button=1), start
    )


def test_dragging_a_card_is_not_confused_with_dragging_the_board(screen):
    card = give_card(screen, lambda c: c.title == "Zerówka - żółty")
    settle(screen)
    start = screen.hand.slots[card.uid].position
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    over_board = screen.app.layout.board_viewport.center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=over_board, rel=(10, 10),
                           buttons=(1, 0, 0)),
        over_board,
    )
    assert screen.hand.dragging == card.uid
    assert not screen.board_view.map_dragging
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=over_board, button=1), over_board
    )


# ── recently played polish ───────────────────────────────────────────────────
def test_hovering_a_played_card_enlarges_it(screen):
    card = give_card(screen, lambda c: c.title == "Zerówka - niebieski")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    answer_choice(screen)
    settle(screen, 40)
    assert screen.recently_played.entries

    slot = screen.app.layout.recent_slot_rect(0)
    settle(screen, 40, mouse=slot.center)
    entry = screen.recently_played.entries[0]
    assert entry.hover > 0.8


def test_a_hovered_card_does_not_fade_away(screen):
    card = give_card(screen, lambda c: c.title == "Zerówka - niebieski")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    answer_choice(screen)

    slot = screen.app.layout.recent_slot_rect(0)
    for _ in range(400):
        frame(screen, dt=0.05, mouse=slot.center)
    assert screen.recently_played.entries, "a card under the cursor must stay"


def test_played_cards_stay_around_longer_than_before(screen):
    card = give_card(screen, lambda c: c.title == "Zerówka - niebieski")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    answer_choice(screen)
    for _ in range(160):          # eight seconds
        frame(screen, dt=0.05)
    assert screen.recently_played.entries


# ── board backdrop (stage 3) ─────────────────────────────────────────────────
def test_the_world_fills_the_viewport_even_when_zoomed_right_out(screen):
    """No bare panel colour around the map: the board sits inside a landscape."""
    view = screen.board_view
    frame(screen)
    view.camera.set_zoom(view.camera.min_zoom)
    view.camera.snap()
    settle(screen, 5)

    canvas = screen.app.canvas
    viewport = screen.app.layout.board_viewport
    background = screen.app.renderer.theme.background
    panel = screen.app.renderer.theme.panel_bg

    bare = 0
    samples = 0
    for x in range(viewport.left + 4, viewport.right - 4, 11):
        for y in range(viewport.top + 4, viewport.bottom - 4, 11):
            colour = canvas.get_at((x, y))[:3]
            samples += 1
            if colour in (background, panel):
                bare += 1
    assert samples > 100
    assert bare == 0, f"{bare}/{samples} pixels of empty background inside the board"


def test_the_backdrop_moves_with_the_camera(screen):
    """Parallax: the land around the board drifts instead of sticking.

    Zoomed in far enough that the camera has somewhere to go — at minimum zoom
    the whole board fits and panning is correctly clamped to nothing.
    """
    view = screen.board_view
    frame(screen)
    view.camera.set_zoom(view.camera.max_zoom)
    view.camera.snap()
    frame(screen)

    corner = (screen.app.layout.board_viewport.left + 6,
              screen.app.layout.board_viewport.top + 6)
    before = screen.app.canvas.get_at(corner)[:3]
    view.camera.pan_screen(-260, -140)
    view.camera.snap()
    frame(screen)
    after = screen.app.canvas.get_at(corner)[:3]
    assert before != after, "the surrounding land should scroll, not stick"


# ── rotated card shadows (stage 3) ───────────────────────────────────────────
def test_a_tilted_card_casts_a_tilted_shadow():
    """The shadow is the card's silhouette, so it must rotate with it."""
    app = App(Layout(), headless=True, size=WINDOW)
    library = ContentLibrary.load()
    from pedzacy_piotrek.render.card_renderer import CardRenderer

    cards = CardRenderer(app.renderer, library)
    card = library.deck(settings.DECK_MOVEMENT).build_cards()[0]
    size = (140, 200)

    def shadow_pixels(angle: float) -> set:
        canvas = pygame.Surface((420, 420))
        canvas.fill((255, 255, 255))
        app.renderer.begin(canvas)
        cards.draw_transformed(card, (210, 210), angle, canvas, size=size, shadow=10)
        return {
            (x, y)
            for x in range(0, 420, 3)
            for y in range(0, 420, 3)
            if sum(canvas.get_at((x, y))[:3]) < 690  # darker than the background
        }

    upright = shadow_pixels(0.0)
    tilted = shadow_pixels(30.0)
    assert upright and tilted
    # A rotated card covers a different set of pixels; if the shadow stayed
    # axis-aligned the difference would be much smaller than the card itself.
    only_tilted = tilted - upright
    assert len(only_tilted) > len(upright) * 0.2


# ── label spacing (stage 3 polish) ───────────────────────────────────────────
def _text_rect(app, text: str, font, **anchor) -> pygame.Rect:
    return app.renderer.text_surface(text, font, (255, 255, 255)).get_rect(**anchor)


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (1920, 1200),
                                  (2560, 1440)])
def test_deck_names_never_collide_with_their_counters(library, size):
    """Name on the left, counters on the right, both inside the column."""
    app = App(Layout(), headless=True, size=size)
    state = create_game(SessionConfig(num_players=5, board_cells=24, seed=4), library)
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    layout, r = app.layout, app.renderer
    line = layout.section_line_h

    for column, deck_id in enumerate(settings.TABLE_DECKS):
        deck = state.deck(deck_id)
        draw_rect = layout.deck_draw_rect(column)
        disc_rect = layout.deck_discard_rect(column)
        label_y = layout.deck_label_y(column)

        name = _text_rect(
            app, deck.name, r.fonts.get(int(15 * layout.ui_scale), bold=True),
            midleft=(layout.left_panel.left + layout.left_inner, label_y + line // 2),
        )
        counts = _text_rect(
            app, f"{deck.draw_count} / {deck.discard_count}",
            r.fonts.get(int(14 * layout.ui_scale), bold=True),
            midright=(disc_rect.right, label_y + line // 2),
        )

        assert not name.colliderect(counts)
        assert name.bottom <= draw_rect.top
        assert counts.bottom <= draw_rect.top
        assert layout.left_panel.contains(name)
        assert layout.left_panel.contains(counts)


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (1920, 1200),
                                  (2560, 1440)])
def test_the_right_column_uses_the_space_it_has(size):
    """Both cases fill their column: no hand's width of dead space, no overflow.

    The colour notepad only exists for hunters — Piotrek does not cross colours
    off a list of his own — so it is only part of the hunter's stack.
    """
    layout = Layout(*size)
    panel = layout.right_panel
    for show_skill in (False, True):
        rects = layout.character_panel(show_skill)
        for key in ("card", "char_draw", "char_disc"):
            assert panel.contains(rects[key]), (size, show_skill, key)
        if show_skill:
            assert panel.contains(rects["skill_disc"])
        else:
            grid = layout.pawn_grid_panel(layout.pawn_grid_top(show_skill))
            assert panel.contains(grid)
            leftover = panel.bottom - grid.bottom
            assert leftover < panel.height * 0.25, "too much unused column"


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (2560, 1440)])
def test_panel_cards_are_a_readable_size(size):
    """Side-panel cards should be worth reading, not thumbnails."""
    layout = Layout(*size)
    panel_card = layout.panel_card_size[1]
    hand_card = layout.hand_card_size[1]
    assert panel_card >= 100
    assert panel_card >= hand_card * 0.55
    for show_skill in (False, True):
        assert layout.right_cards(show_skill)[1] >= 120


# ── abilities, prompts and overlays (stage 4) ────────────────────────────────
def seat_with_character(screen: GameScreen, title: str) -> int:
    """Give a non-Piotrek seat a specific character and make it active."""
    state = screen.state
    seat = next(p.index for p in state.players if not p.is_piotrek)
    deck = state.deck(settings.DECK_CHARACTERS)
    card = deck.take_titled(title)
    if card is None:
        for player in state.players:
            if player.character is not None and player.character.title == title:
                card = player.character
                player.character = None
                break
    assert card is not None, title
    if state.players[seat].character is not None:
        deck.return_card(state.players[seat].character)
    state.players[seat].character = card
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    return seat


def place_pawn(screen: GameScreen, pawn_id: str, position: int) -> None:
    tile = screen.state.board.positions[position].tiles[0]
    screen.state.board.place_pawn(pawn_id, tile.index)
    screen.state._sync_token_positions()
    screen.board_view.visual[pawn_id] = screen.state.tokens[pawn_id].position


def test_the_ability_button_activates_the_character(screen):
    seat = seat_with_character(screen, "Atencjusz")
    card = screen.state.players[seat].character
    frame(screen)

    rect = screen.app.layout.ability_button_rect(False)
    click(screen, rect.center)

    assert card.uses_left == 0
    from pedzacy_piotrek.engine.statuses import StatusKind
    assert screen.state.statuses.player_has(StatusKind.EXTRA_TURN, seat)


def test_the_ability_button_reports_when_it_is_spent(screen):
    seat_with_character(screen, "Atencjusz")
    rect = screen.app.layout.ability_button_rect(False)
    click(screen, rect.center)
    frame(screen)
    click(screen, rect.center)
    assert screen.status_bar.message is not None
    assert "zużyt" in screen.status_bar.message.lower()


def test_an_ability_that_needs_a_pawn_opens_a_prompt(screen):
    seat = seat_with_character(screen, "Big D Randy")
    place_pawn(screen, "żółty", 5)
    frame(screen)

    click(screen, screen.app.layout.ability_button_rect(False).center)
    assert screen.pending_choice is not None
    assert screen.pending_choice.kind == "pawn"
    assert screen.choice_prompt.active
    frame(screen)
    assert screen.board_view.choice_pawns

    option = next(o for o in screen.choice_prompt.options if o.id == "żółty")
    click(screen, option.rect.center)

    from pedzacy_piotrek.engine.statuses import StatusKind
    assert screen.pending_choice is None
    assert not screen.choice_prompt.active
    assert screen.state.statuses.pawn_has(StatusKind.FROZEN, "żółty")


def test_a_pawn_choice_can_be_answered_on_the_board(screen):
    seat_with_character(screen, "Big D Randy")
    place_pawn(screen, "zielony", 6)
    frame(screen)
    click(screen, screen.app.layout.ability_button_rect(False).center)
    frame(screen)

    position = screen.board_view.display_position("zielony")
    click(screen, screen.board_view.camera.world_to_screen(position))

    from pedzacy_piotrek.engine.statuses import StatusKind
    assert screen.state.statuses.pawn_has(StatusKind.FROZEN, "zielony")


def test_a_two_step_ability_asks_twice(screen):
    seat = seat_with_character(screen, "Dziad")
    place_pawn(screen, "żółty", 6)
    frame(screen)

    click(screen, screen.app.layout.ability_button_rect(False).center)
    assert screen.pending_choice.key == "pawn"
    frame(screen)
    option = next(o for o in screen.choice_prompt.options if o.id == "żółty")
    click(screen, option.rect.center)

    assert screen.pending_choice is not None
    assert screen.pending_choice.key == "move"
    assert screen.pending_choice.answers == {"pawn": "żółty"}
    frame(screen)
    back_two = next(o for o in screen.choice_prompt.options if o.id == "-2")
    click(screen, back_two.rect.center)

    # The destination may itself be a doubled position, which is a third
    # question; the engine asks for as many decisions as the move needs.
    while screen.pending_choice is not None:
        assert answer_choice(screen)
    assert screen.state.board.position_of_pawn("żółty") == 4


def test_escape_abandons_a_prompt_without_spending_the_ability(screen):
    seat = seat_with_character(screen, "Big D Randy")
    place_pawn(screen, "żółty", 5)
    frame(screen)
    click(screen, screen.app.layout.ability_button_rect(False).center)
    assert screen.pending_choice is not None

    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), (0, 0))
    assert screen.pending_choice is None
    assert not screen.choice_prompt.active
    assert screen.state.players[seat].character.uses_left == 1


def test_the_prompt_blocks_the_rest_of_the_interface(screen):
    seat_with_character(screen, "Big D Randy")
    place_pawn(screen, "żółty", 5)
    frame(screen)
    click(screen, screen.app.layout.ability_button_rect(False).center)

    before = screen.state.deck(settings.DECK_MOVEMENT).draw_count
    click(screen, screen.app.layout.deck_draw_rect(0).center)
    assert screen.state.deck(settings.DECK_MOVEMENT).draw_count == before


def test_an_ability_waiting_on_checking_says_so(screen):
    """Glockboy needs the checking rules, which do not exist yet."""
    seat_with_character(screen, "Glockboy")
    place_pawn(screen, "żółty", 5)
    frame(screen)
    click(screen, screen.app.layout.ability_button_rect(False).center)
    while screen.pending_choice is not None:
        assert answer_choice(screen)

    assert screen.status_bar.message is not None
    assert "sprawdzan" in screen.status_bar.message.lower()
    assert screen.state.players[screen.state.active_player_index].character.uses_left == 1


# ── the chest limit ──────────────────────────────────────────────────────────
def test_going_over_the_chest_limit_opens_the_keep_overlay(screen):
    state = screen.state
    seat = next(p.index for p in state.players if not p.is_piotrek)
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    for _ in range(2):
        click(screen, screen.app.layout.deck_draw_rect(2).center)

    assert screen.chest_choice.active
    assert len(screen.chest_choice.cards) == 2
    assert screen.chest_choice.limit == 1
    frame(screen)

    # Everything else is blocked while the choice is open.
    before = state.deck(settings.DECK_MOVEMENT).draw_count
    click(screen, screen.app.layout.deck_draw_rect(0).center)
    assert state.deck(settings.DECK_MOVEMENT).draw_count == before


def test_keeping_a_chest_card_discards_the_others(screen):
    state = screen.state
    seat = next(p.index for p in state.players if not p.is_piotrek)
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    for _ in range(2):
        click(screen, screen.app.layout.deck_draw_rect(2).center)
    frame(screen)

    layout = screen.app.layout
    keep_rect = layout.chest_choice_card_rect(0, len(screen.chest_choice.cards))
    click(screen, keep_rect.center)
    kept = screen.chest_choice.cards[0].uid
    click(screen, layout.chest_confirm_rect(len(screen.chest_choice.cards)).center)

    assert not screen.chest_choice.active
    remaining = [c.uid for c in state.chest_cards(state.players[seat])]
    assert remaining == [kept]
    assert state.deck(settings.DECK_CHEST).discard_count >= 1


# ── reveals ──────────────────────────────────────────────────────────────────
def test_gamechanger_is_announced_before_it_reaches_the_hand(screen):
    state = screen.state
    seat = next(p.index for p in state.players if not p.is_piotrek)
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    chest = state.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)

    click(screen, screen.app.layout.deck_draw_rect(2).center)
    assert screen.reveal.active
    assert screen.reveal.phases[0].title == "Gamechanger"
    assert screen.reveal.phases[1].title == "Kingmaker"
    frame(screen)

    # It becomes the second card after about a second.
    for _ in range(40):
        frame(screen, dt=0.05)
    assert screen.reveal.index >= 1


def test_seks_z_pedalami_shows_the_card_it_finds(screen):
    state = screen.state
    for index, pawn in enumerate(state.library.pawns):
        place_pawn(screen, pawn.id, index + 3)
    card = give_card(screen, lambda c: c.title == "Seks z pedałami")
    settle(screen)

    click(screen, screen.hand.slots[card.uid].position)
    assert screen.reveal.active
    assert screen.reveal.phases[0].title == "Seks z pedałami"
    assert screen.reveal.phases[1].title != "Seks z pedałami"
    assert len(screen.recently_played.entries) >= 1
    frame(screen)


def test_clicking_past_a_reveal_still_does_what_was_clicked(screen):
    """The animation must never cost the player an action."""
    state = screen.state
    chest = state.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)
    seat = next(p.index for p in state.players if not p.is_piotrek)
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    click(screen, screen.app.layout.deck_draw_rect(2).center)
    assert screen.reveal.active

    before = state.deck(settings.DECK_MOVEMENT).draw_count
    click(screen, screen.app.layout.deck_draw_rect(0).center)
    assert not screen.reveal.active
    assert state.deck(settings.DECK_MOVEMENT).draw_count == before - 1


def test_a_reveal_can_be_clicked_away(screen):
    state = screen.state
    chest = state.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)
    seat = next(p.index for p in state.players if not p.is_piotrek)
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    click(screen, screen.app.layout.deck_draw_rect(2).center)
    assert screen.reveal.active

    click(screen, screen.reveal.card_rect(screen.app.layout).center)
    assert not screen.reveal.active


# ── status marks ─────────────────────────────────────────────────────────────
def test_frozen_and_linked_pawns_are_drawn_differently(screen):
    from pedzacy_piotrek.engine.statuses import Status, StatusKind

    place_pawn(screen, "żółty", 5)
    place_pawn(screen, "zielony", 8)
    frame(screen)
    plain = screen.app.canvas.copy()

    screen.state.statuses.add(Status.for_pawn(StatusKind.FROZEN, "żółty"))
    screen.state.statuses.add(Status(
        kind=StatusKind.LINKED, subject_id="a",
        data={"members": ["żółty", "zielony"]},
    ))
    frame(screen)

    viewport = screen.app.layout.board_viewport
    changed = sum(
        1
        for x in range(viewport.left, viewport.right, 5)
        for y in range(viewport.top, viewport.bottom, 5)
        if plain.get_at((x, y)) != screen.app.canvas.get_at((x, y))
    )
    assert changed > 30, "statuses must be visible on the board"


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080), (2560, 1440)])
def test_the_ability_button_never_covers_the_deck_below_it(size):
    """It sits under the ability card, so the column must budget for it."""
    layout = Layout(*size)
    for show_skill in (False, True):
        rects = layout.character_panel(show_skill)
        button = layout.ability_button_rect(show_skill)
        label_y = rects["skill_label_y"] if show_skill else rects["char_label_y"]
        assert button.bottom <= label_y
        assert layout.right_panel.contains(button)
        if not show_skill:
            grid = layout.pawn_grid_panel(layout.pawn_grid_top(show_skill))
            assert layout.right_panel.contains(grid)


# ── target token selection (stage 5) ─────────────────────────────────────────
def test_playing_a_select_a_token_card_shows_coloured_tokens(screen):
    """The prompt shows the pawns themselves, not a row of named buttons."""
    state = screen.state
    for index, pawn in enumerate(state.library.pawns):
        place_pawn(screen, pawn.id, index + 3)
    card = give_card(screen, lambda c: c.title == "Kolos z paki")
    settle(screen)

    click(screen, screen.hand.slots[card.uid].position)
    assert screen.pending_choice is not None
    assert screen.pending_choice.kind == "pawn"
    assert screen.choice_prompt.is_pawn_choice
    frame(screen)

    colours = {option.color for option in screen.choice_prompt.options}
    assert colours == {pawn.color for pawn in state.library.pawns}
    assert set(screen.board_view.choice_pawns) == {p.id for p in state.library.pawns}


def test_choosing_a_token_from_the_prompt_moves_it(screen):
    state = screen.state
    for index, pawn in enumerate(state.library.pawns):
        place_pawn(screen, pawn.id, index + 3)
    start = state.board.position_of_pawn("zielony")
    card = give_card(screen, lambda c: c.title == "Astral 2019")
    settle(screen)

    click(screen, screen.hand.slots[card.uid].position)
    frame(screen)
    option = next(o for o in screen.choice_prompt.options if o.id == "zielony")
    click(screen, option.rect.center)
    while screen.pending_choice is not None:
        assert answer_choice(screen)

    assert state.board.position_of_pawn("zielony") == start + 2
    assert card not in state.active_player.hand


def test_a_card_target_can_also_be_clicked_on_the_board(screen):
    state = screen.state
    for index, pawn in enumerate(state.library.pawns):
        place_pawn(screen, pawn.id, index + 3)
    card = give_card(screen, lambda c: c.title == "Kolos z paki")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    frame(screen)

    position = screen.board_view.display_position("różowy")
    click(screen, screen.board_view.camera.world_to_screen(position))
    while screen.pending_choice is not None:
        assert answer_choice(screen)
    assert card not in state.active_player.hand


def test_the_same_prompt_serves_cards_and_abilities(screen):
    """One selection system: the ability path and the card path are identical."""
    state = screen.state
    place_pawn(screen, "żółty", 5)
    seat_with_character(screen, "Big D Randy")
    frame(screen)
    click(screen, screen.app.layout.ability_button_rect(False).center)
    ability_kind = screen.pending_choice.kind
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), (0, 0))

    card = give_card(screen, lambda c: c.title == "Kolos z paki")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    assert screen.pending_choice.kind == ability_kind == "pawn"


# ── edit mode in the interface ───────────────────────────────────────────────
def locked_screen(library) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(
        SessionConfig(num_players=4, board_cells=24, seed=7,
                      edit_mode=False, local_seat=1),
        library,
    )
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    return screen


def test_without_edit_mode_the_game_starts_on_the_cadence_seat(library):
    """The cadence decides who begins; a client does not claim the turn."""
    screen = locked_screen(library)
    assert screen.state.active_player_index == screen.state.seat_order(1)[0]
    assert screen.state.local_seat == 1
    assert screen.view_seat == 1, "but it still watches its own hand"


def test_without_edit_mode_other_seats_cannot_be_taken_over(library):
    screen = locked_screen(library)
    frame(screen)
    before = screen.state.active_player_index
    layout = screen.app.layout
    click(screen, layout.player_tile_rect(3, len(screen.state.players)).center)
    assert screen.state.active_player_index == before
    assert screen.view_seat == screen.my_seat


def test_without_edit_mode_tab_does_nothing(library):
    screen = locked_screen(library)
    before = screen.state.active_player_index
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB), (0, 0))
    assert screen.state.active_player_index == before


def test_edit_mode_keeps_the_prototype_behaviour(screen):
    frame(screen)
    layout = screen.app.layout
    click(screen, layout.player_tile_rect(3, len(screen.state.players)).center)
    assert screen.state.active_player_index == 3
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB), (0, 0))
    assert screen.state.active_player_index == 4 % len(screen.state.players)


def test_the_menu_can_turn_edit_mode_off(library):
    menu, captured = make_menu(library)
    menu_click(menu, menu.edit_checkbox.rect.center)
    assert menu.edit_mode is False
    menu_click(menu, menu.start_button.rect.center)
    assert captured and captured[0].edit_mode is False
    state = create_game(captured[0], library)
    assert not state.may_control(2)


# ── Gamechanger in the interface ─────────────────────────────────────────────
def test_the_transformed_card_is_the_one_that_reaches_the_hand(screen):
    state = screen.state
    seat = next(p.index for p in state.players if not p.is_piotrek)
    screen.submit(cmd.SetActivePlayer(player_index=seat))
    chest = state.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)

    click(screen, screen.app.layout.deck_draw_rect(2).center)
    settle(screen, 40)
    assert card.title == "Kingmaker"
    assert any(c.title == "Kingmaker" for c in state.players[seat].hand)
    assert not any(c.title == "Gamechanger" for c in state.players[seat].hand)


# ── end turn (stage 9) ───────────────────────────────────────────────────────
def test_the_end_turn_button_finishes_the_turn(screen):
    state = screen.state
    seat = state.active_player_index
    player = state.players[seat]
    player.remove_card(player.hand[0])          # leave the hand short
    frame(screen)

    click(screen, screen.app.layout.end_turn_button.center)

    from pedzacy_piotrek.engine.setup import starting_hand_size
    assert state.active_player_index != seat, "the turn moved on"
    assert len([c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT]) == \
        starting_hand_size(player), "and the hand was topped up"


def test_the_end_turn_button_is_dead_when_it_is_not_your_turn(library):
    screen = locked_screen(library)          # edit mode off, local seat 1
    state = screen.state
    state.active_player_index = 0            # somebody else's turn
    frame(screen)
    assert not screen.can_end_turn

    before = state.active_player_index
    click(screen, screen.app.layout.end_turn_button.center)
    assert state.active_player_index == before
    assert screen.status_bar.message is not None


def test_the_end_turn_button_waits_for_a_pending_decision(screen):
    state = screen.state
    for index, pawn in enumerate(state.library.pawns):
        place_pawn(screen, pawn.id, index + 3)
    card = give_card(screen, lambda c: c.title == "Kolos z paki")
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    assert screen.pending_choice is not None

    seat = state.active_player_index
    assert not screen.can_end_turn
    frame(screen)
    click(screen, screen.app.layout.end_turn_button.center)
    assert state.active_player_index == seat, "answer the question first"


def test_the_end_turn_button_does_not_sit_on_the_board_controls(library):
    for size in ((1280, 760), (1920, 1080), (1920, 1200), (2560, 1440)):
        layout = Layout(*size)
        button = layout.end_turn_button
        assert layout.board_viewport.contains(button)
        assert not button.colliderect(layout.zoom_slider)
        assert not button.colliderect(layout.status_bar)


# ── the match beginning and ending (stage 17) ────────────────────────────────
def _finish(state):
    from pedzacy_piotrek.board.tiles import TileKind

    return next(t for t in state.board.tiles if t.kind is TileKind.FINISH)


def _win(screen, outcome: str = "piotrek"):
    """Declare a winner the way the authority does, and let the screen react."""
    state = screen.state
    pawn = state.piotrek_pawn or state.library.pawns[0].id
    state.apply(cmd.DeclareVictory(outcome=outcome, pawn_id=pawn,
                                  piotrek_seat=state.piotrek_seat or 0,
                                  piotrek_name="Kuba"), local=False)
    frame(screen)
    return pawn


def test_a_hot_seat_game_is_playable_at_once(screen):
    """Nobody to ask, so nothing to wait for: only an online match pauses."""
    assert screen.state.phase.playable
    frame(screen)
    assert not screen.match_start.active


def test_winning_puts_the_ending_on_screen(screen):
    pawn_id = _win(screen)
    assert screen.victory.active
    assert screen.victory.piotrek_won
    assert screen.victory.pawn_name == screen.state.library.pawn(pawn_id).name


def test_the_two_endings_do_not_look_alike(screen):
    """Different colour, different words — you should not have to read it."""
    _win(screen, "piotrek")
    escaped = screen.victory.accent
    screen.state.victory = None
    screen.victory.hide()
    _win(screen, "hunters")
    assert screen.victory.accent != escaped
    assert not screen.victory.piotrek_won


def test_the_ending_paints_something_over_the_table(screen):
    frame(screen)
    before = screen.app.canvas.copy()
    _win(screen)
    settle(screen, 20)
    canvas = screen.app.canvas
    changed = sum(
        canvas.get_at((x, y))[:3] != before.get_at((x, y))[:3]
        for x in range(0, canvas.get_width(), 29)
        for y in range(0, canvas.get_height(), 29)
    )
    assert changed > 100, "an overlay that changes nothing is not an overlay"


def test_gameplay_is_dead_once_somebody_has_won(screen):
    state = screen.state
    _win(screen)
    before = state.deck(settings.DECK_MOVEMENT).draw_count
    click(screen, screen.app.layout.deck_draw_rect(0).center)
    click(screen, screen.app.layout.board_viewport.center)
    assert state.deck(settings.DECK_MOVEMENT).draw_count == before


def test_the_ending_offers_a_way_out(screen):
    _win(screen)
    settle(screen, 10)
    keys = [key for key, _ in screen.victory.buttons]
    assert "quit" in keys, "there is always a way out of the application"
    # A hot-seat game has no lobby to return to, so it does not pretend to.
    assert "lobby" not in keys


class _FakeService:
    """Just enough of NetworkService for the screen to believe it is online.

    The identity question is something the SERVER asks, so a test that reaches
    into the overlay and shows it by hand proves nothing: the screen rebuilds
    the overlay from the service every frame, exactly so a late answer or a
    reconnection cannot leave a stale picture on screen.
    """

    def __init__(self, session, pawns=()):
        self.session = session
        self.identity_request = [dict(p) for p in pawns]
        self.identity_pawn = ""
        self.disconnected = None
        self.reconnecting = False
        self.chosen = None
        self.returned = False
        self.closed = False

    def poll(self, library=None):
        pass

    def drain_notices(self):
        return []

    def choose_identity(self, pawn_id):
        self.chosen = pawn_id
        self.identity_pawn = pawn_id
        self.identity_request = []

    def return_to_lobby(self):
        self.returned = True

    def close(self):
        self.closed = True


def _online_screen(library, pawns=()):
    from pedzacy_piotrek.engine.victory import MatchPhase

    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(SessionConfig(num_players=4, board_cells=18, seed=5,
                                      piotrek_picks_pawn=True), library)
    assert state.phase is MatchPhase.STARTING
    session = LocalSession(state)
    service = _FakeService(session, pawns)
    game = GameScreen(app, session, service=service)
    app.push(game)
    return game, service


def test_a_player_who_is_not_piotrek_only_waits(library):
    game, _service = _online_screen(library)
    frame(game)
    assert game.match_start.active and not game.match_start.choosing

    before = game.state.deck(settings.DECK_MOVEMENT).draw_count
    click(game, game.app.layout.deck_draw_rect(0).center)
    assert game.state.deck(settings.DECK_MOVEMENT).draw_count == before, \
        "nothing is playable yet"


def test_piotrek_picks_a_colour_and_the_choice_goes_to_the_server(library):
    game, service = _online_screen(
        library,
        [{"id": p.id, "name": p.name, "color": list(p.color)}
         for p in ContentLibrary.load().pawns],
    )
    settle(game, 5)
    assert game.match_start.choosing

    wanted = game.match_start.pawns[1]["id"]
    click(game, game.match_start.rects[1].center)
    assert service.chosen == wanted, "the colour was sent, once"

    settle(game, 5)
    assert game.match_start.active, "still waiting for the table to start"
    assert not game.match_start.choosing, "but not asked twice"

    game.state.apply(cmd.BeginMatch(), local=False)
    frame(game)
    assert not game.match_start.active, "everybody starts together"


def test_the_ending_of_an_online_match_offers_the_lobby(library):
    game, service = _online_screen(library)
    game.state.apply(cmd.BeginMatch(), local=False)
    _win(game)
    settle(game, 10)
    keys = [key for key, _ in game.victory.buttons]
    assert keys == ["lobby", "menu", "quit"], "three ways out, best one first"

    lobby_rect = dict(game.victory.buttons)["lobby"]
    click(game, lobby_rect.center)
    assert service.returned, "the server is asked to reopen the room"
    assert not service.closed, "and the connection is kept"


def test_the_main_menu_button_closes_the_connection(library):
    """The other two endings differ in exactly this.

    Returning to the poczekalnia keeps the room; going to the main menu leaves
    it, and leaving without closing would hold a seat open behind a grace
    period nobody is waiting through.
    """
    game, service = _online_screen(library)
    game.state.apply(cmd.BeginMatch(), local=False)
    _win(game)
    settle(game, 10)

    click(game, dict(game.victory.buttons)["menu"].center)
    assert service.closed and not service.returned
    from pedzacy_piotrek.ui.network_screens import MainMenuScreen
    assert isinstance(game.app.screen, MainMenuScreen)


# ── stage 18: the hot-seat picker, and Piotrek's own reminder ────────────────
def _hot_seat(library, **overrides):
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(SessionConfig(num_players=4, board_cells=18, seed=11,
                                      piotrek_picks_pawn=True, **overrides),
                        library)
    game = GameScreen(app, LocalSession(state))
    app.push(game)
    return game


def test_one_machine_is_asked_too(library):
    """The bug: only online matches paused to ask, so testing alone never saw it."""
    game = _hot_seat(library)
    assert game.state.piotrek_pawn is None, "nothing dealt behind anybody's back"
    frame(game)
    assert game.match_start.active and game.match_start.choosing
    assert len(game.match_start.pawns) == len(game.state.library.pawns)


def test_the_hot_seat_pick_starts_the_match(library):
    game = _hot_seat(library)
    settle(game, 5)
    wanted = game.match_start.pawns[2]["id"]
    click(game, game.match_start.rects[2].center)
    frame(game)

    assert game.state.piotrek_pawn == wanted
    assert game.state.phase.playable, "and the table is live"
    assert not game.match_start.active


def test_the_pick_cannot_be_taken_back(library):
    game = _hot_seat(library)
    settle(game, 5)
    first = game.match_start.pawns[0]["id"]
    click(game, game.match_start.rects[0].center)
    frame(game)
    assert game.state.piotrek_pawn == first
    assert not game.state.set_piotrek_pawn(game.state.library.pawns[3].id)
    assert game.state.piotrek_pawn == first


def _panel_shows_pawn(game, player) -> bool:
    """Is a filled colour circle drawn in the identity row of the right panel?"""
    game.view_seat = player.index
    frame(game)
    rect = game.app.layout.character_panel(player.is_piotrek)["identity"]
    canvas = game.app.canvas
    pawn = game.state.library.pawn(player.secret_pawn) if player.secret_pawn else None
    if pawn is None:
        return False
    wanted = pawn.color
    return any(
        max(abs(canvas.get_at((x, y))[i] - wanted[i]) for i in range(3)) < 30
        for x in range(rect.left, rect.right, 2)
        for y in range(rect.top, rect.bottom, 2)
    )


def test_piotrek_always_sees_which_pawn_is_his(library):
    game = _hot_seat(library)
    settle(game, 5)
    click(game, game.match_start.rects[1].center)
    settle(game, 5)

    piotrek = game.state.player(game.state.piotrek_seat)
    assert _panel_shows_pawn(game, piotrek), "the badge is drawn in his own panel"


def test_the_reminder_outlives_a_dozen_turns(library):
    game = _hot_seat(library)
    settle(game, 5)
    click(game, game.match_start.rects[1].center)
    settle(game, 5)
    piotrek = game.state.player(game.state.piotrek_seat)

    for _ in range(12):
        seat = game.state.active_player_index
        game.state.apply(cmd.EndTurn(player_index=seat), local=False)
    assert _panel_shows_pawn(game, piotrek), "still there at the far end"


def test_nobody_else_has_a_colour_to_show(library):
    """A hunter's panel has no identity row at all — and no colour to draw."""
    game = _hot_seat(library)
    settle(game, 5)
    click(game, game.match_start.rects[1].center)
    settle(game, 5)

    for player in game.state.players:
        if player.is_piotrek:
            continue
        assert player.secret_pawn is None
        assert not _panel_shows_pawn(game, player)


def test_a_hunters_client_cannot_draw_a_badge_it_does_not_have(library):
    """The real protection online: the colour is not in a hunter's state.

    The panel is only Piotrek's, but the reason it cannot leak is simpler than
    that — a hunter's replica has ``secret_pawn`` set to None for everybody.
    """
    game = _hot_seat(library)
    settle(game, 5)
    click(game, game.match_start.rects[1].center)
    settle(game, 5)

    piotrek = game.state.player(game.state.piotrek_seat)
    piotrek.secret_pawn = None                  # what a hunter's copy looks like
    frame(game)
    assert not _panel_shows_pawn(game, piotrek)


# ── the four cards, seen from the interface ──────────────────────────────────
def _place(screen, pawn_id, position):
    board = screen.state.board
    board.place_pawn(pawn_id, board.position(position).tiles[0].index)
    screen.state._sync_token_positions()


def _pawn_option_rect(screen, pawn_id):
    """Where the prompt drew a particular pawn button."""
    for option in screen.choice_prompt.options:
        if option.id == pawn_id:
            return option.rect
    raise AssertionError(f"{pawn_id} nie jest w pytaniu")


def test_plagiat_asks_for_two_pawns_and_numbers_them(screen):
    """The generic multi-select: pick, pick, confirm — and the picks are ordered."""
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Plagiat!")
    green, pink = state.library.pawns[1].id, state.library.pawns[4].id
    _place(screen, green, 6)
    _place(screen, pink, 9)
    settle(screen)

    click(screen, screen.hand.slots[card.uid].position)
    settle(screen, 10)

    prompt = screen.choice_prompt
    assert prompt.active and prompt.is_multi and prompt.count == 2
    assert not prompt.ready, "nothing is picked yet"

    click(screen, _pawn_option_rect(screen, green).center)
    settle(screen, 5)
    click(screen, _pawn_option_rect(screen, pink).center)
    settle(screen, 5)

    assert prompt.selected == [green, pink]
    assert prompt.order_of(green) == 1 and prompt.order_of(pink) == 2
    assert prompt.ready
    # The board shows the same numbers, so the decision reads where the pawns are.
    assert screen.board_view.choice_selected == [green, pink]

    click(screen, prompt.confirm_rect.center)
    settle(screen, 60)

    assert state.board.position_of_pawn(green) == 5
    assert state.board.position_of_pawn(pink) == 8


def test_unpicking_a_pawn_closes_the_numbers_up(screen):
    """1 green, 2 pink → unpick green → 1 pink.  The example from the brief."""
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Plagiat!")
    green, pink = state.library.pawns[1].id, state.library.pawns[4].id
    _place(screen, green, 6)
    _place(screen, pink, 9)
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    settle(screen, 10)

    click(screen, _pawn_option_rect(screen, green).center)
    click(screen, _pawn_option_rect(screen, pink).center)
    settle(screen, 5)
    assert screen.choice_prompt.order_of(pink) == 2

    click(screen, _pawn_option_rect(screen, green).center)
    settle(screen, 5)

    assert screen.choice_prompt.selected == [pink]
    assert screen.choice_prompt.order_of(pink) == 1
    assert screen.choice_prompt.order_of(green) is None
    assert not screen.choice_prompt.ready, "confirm goes dead again"


def test_confirm_does_nothing_until_exactly_two_are_picked(screen):
    state = screen.state
    card = give_card(screen, lambda c: c.title == "Plagiat!")
    green = state.library.pawns[1].id
    _place(screen, green, 6)
    settle(screen)
    click(screen, screen.hand.slots[card.uid].position)
    settle(screen, 10)

    click(screen, _pawn_option_rect(screen, green).center)
    settle(screen, 5)
    click(screen, screen.choice_prompt.confirm_rect.center)
    settle(screen, 10)

    assert screen.pending_choice is not None, "still waiting"
    assert state.board.position_of_pawn(green) == 6, "and nothing has moved"


def test_spy_opens_a_card_picker_showing_only_movement_cards(screen):
    state = screen.state
    piotrek = next(p for p in state.players if p.is_piotrek)
    hunter = next(p for p in state.players if not p.is_piotrek)
    state.active_player_index = hunter.index
    screen.view_seat = hunter.index
    chest = state.deck(settings.DECK_CHEST).take_card()
    piotrek.hand.append(chest)
    spy = _give(state, hunter, "Spy")
    settle(screen)

    click(screen, screen.hand.slots[spy.uid].position)
    settle(screen, 10)

    picker = screen.card_picker
    assert picker.active
    shown = {c.uid for c in picker.cards}
    assert chest.uid not in shown, "a Chest card is not a Movement card"
    assert shown == {c.uid for c in piotrek.hand
                     if c.deck_id == settings.DECK_MOVEMENT}

    target = picker.cards[0]
    rect = screen.app.layout.card_picker_card_rect(0, len(picker.cards))
    click(screen, rect.center)
    settle(screen, 20)

    assert not screen.card_picker.active
    assert hunter.card_by_uid(target.uid) is target
    assert piotrek.card_by_uid(target.uid) is None


def test_the_card_picker_can_be_backed_out_of(screen):
    state = screen.state
    hunter = next(p for p in state.players if not p.is_piotrek)
    state.active_player_index = hunter.index
    screen.view_seat = hunter.index
    spy = _give(state, hunter, "Spy")
    settle(screen)
    click(screen, screen.hand.slots[spy.uid].position)
    settle(screen, 10)
    assert screen.card_picker.active

    screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode=""), (0, 0)
    )
    settle(screen, 5)

    assert not screen.card_picker.active
    assert screen.pending_choice is None
    assert hunter.card_by_uid(spy.uid) is spy, "and the card is still in hand"


def test_a_spotlit_card_holds_the_board_back_before_it_moves(screen):
    """The forced card is shown, and only then does anything appear to move.

    The STATE has already changed by this point — that is deliberate and the
    engine must never wait for a frame (N36).  What is checked here is that the
    picture arrives in the order a player can follow.
    """
    from pedzacy_piotrek.engine import events as ev

    seat = screen.state.active_player_index
    screen.bus.emit(ev.CardSpotlighted(
        player_index=seat, deck_id=settings.DECK_MOVEMENT, card_uid=-1,
        title="Troll", text="…", seconds=2.5, caption="Troll wybrał tę kartę",
        forced=True,
    ))

    assert screen.reveal.active, "the card is held up"
    assert screen.reveal.phases[0].halo, "with a ring round it, not just shown"
    assert screen.board_view.walk_delay == pytest.approx(2.5)

    settle(screen, 30)          # half a second of frames
    assert screen.board_view.walk_delay < 2.5, "the hold counts down in real time"
    settle(screen, 200)
    assert screen.board_view.walk_delay == 0.0, "and then lets the board go"


def test_a_two_pawn_badge_is_wider_than_a_one_pawn_badge(screen):
    """Plagiat! shows two pawns, and the difference has to be visible.

    Rendered and measured rather than asked: the badge is drawn, so the test
    that proves it changed is one that looks at the pixels.
    """
    from pedzacy_piotrek.cards.base_card import Badge

    cards = screen.cards
    size = (200, 280)
    one = cards.face(_badge_card(screen, Badge("rainbow", "-", False, 1)), size)
    two = cards.face(_badge_card(screen, Badge("rainbow", "-", False, 2)), size)
    assert _badge_width(one) < _badge_width(two)


def _give(state, player, title):
    """Put the named card into a hand, wherever in the game it currently is.

    Several of these cards exist in exactly one copy, so the opening deal may
    already have handed it to somebody; a test that only looks in the draw pile
    fails depending on the seed.
    """
    from pedzacy_piotrek.config import settings as _settings

    for deck_id in (_settings.DECK_MOVEMENT, _settings.DECK_CHEST):
        deck = state.deck(deck_id)
        for pile in (deck.draw_pile, deck.discard_pile):
            for card in list(pile):
                if card.title == title:
                    pile.remove(card)
                    player.hand.append(card)
                    return card
    for other in state.players:
        for card in list(other.hand):
            if card.title == title:
                other.remove_card(card)
                player.hand.append(card)
                return card
    raise AssertionError(f"nie ma karty {title!r} w grze")


def _badge_card(screen, badge):
    """A throwaway card carrying nothing but the badge under test."""
    from dataclasses import replace as dataclass_replace

    from pedzacy_piotrek.cards.base_card import Card

    definition = screen.state.deck(settings.DECK_MOVEMENT).definition.cards[0]
    return Card(dataclass_replace(definition, badge=badge, image=None))


def _badge_width(surface):
    """How far across the bottom strip of a card face anything is painted.

    Two details this has to get right, both learned the hard way.  The card has
    a border and an inset brass rule, so the scan starts INSIDE them or every
    card measures the full width and every comparison is 199 < 199.  And the
    parchment is a vertical gradient, so "background" is sampled per row rather
    than once — a single sample calls the next row down a painted pixel.
    """
    width, height = surface.get_size()
    margin = int(width * 0.14)
    band = range(int(height * 0.88), int(height * 0.97))
    columns = []
    for y in band:
        background = surface.get_at((margin, y))[:3]
        for x in range(margin, width - margin):
            if surface.get_at((x, y))[:3] != background:
                columns.append(x)
    return (max(columns) - min(columns)) if columns else 0
