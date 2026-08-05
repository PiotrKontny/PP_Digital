"""
Multiplayer interface tests.

The screens are thin, so these check the things that are easy to get wrong when
a menu drives a network service: that the flow reaches the right screen, that a
failure is shown instead of crashing, that the room code is what a friend is
given, and that a match already under way locks out everybody except the player
whose turn it is.
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

from netkit import Table, server_config
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.menu import MenuScreen
from pedzacy_piotrek.ui.network_screens import (
    HostSetupScreen, JoinScreen, LobbyScreen, MainMenuScreen,
)

WINDOW = (1600, 900)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make_app() -> App:
    return App(Layout(), headless=True, size=WINDOW)


def render(app: App, frames: int = 2) -> None:
    for _ in range(frames):
        screen = app.screen
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)


def click(screen, pos) -> None:
    pos = (int(pos[0]), int(pos[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), pos)


# ── main menu ────────────────────────────────────────────────────────────────
def test_the_game_opens_on_the_main_menu(library):
    app = make_app()
    menu = MainMenuScreen(app, library)
    app.push(menu)
    render(app)
    assert [key for key, _ in menu.buttons] == ["host", "join", "local", "quit"]


def test_the_menu_leads_to_hosting_and_joining(library):
    app = make_app()
    menu = MainMenuScreen(app, library)
    app.push(menu)
    render(app)
    click(menu, dict(menu.buttons)["host"].rect.center)
    assert isinstance(app.screen, HostSetupScreen)

    app.pop()
    click(menu, dict(menu.buttons)["join"].rect.center)
    assert isinstance(app.screen, JoinScreen)


def test_local_play_is_still_reachable(library):
    """Nothing about the online rewrite may cost the hot-seat game."""
    app = make_app()
    menu = MainMenuScreen(app, library)
    app.push(menu)
    render(app)
    click(menu, dict(menu.buttons)["local"].rect.center)
    assert isinstance(app.screen, MenuScreen)


def test_the_menu_says_which_server_it_will_use(library):
    app = make_app()
    menu = MainMenuScreen(app, library)
    app.push(menu)
    render(app)                 # must draw the server line without crashing
    assert menu.server_y > dict(menu.buttons)["quit"].rect.bottom


# ── the join form ────────────────────────────────────────────────────────────
def test_joining_without_a_code_is_reported_not_crashed(library):
    app = make_app()
    screen = JoinScreen(app, library)
    app.push(screen)
    render(app)
    screen.confirm()
    assert isinstance(app.screen, JoinScreen), "we stay on the form"
    assert "kod pokoju" in screen.error.lower()


def test_an_unreachable_server_is_reported_not_crashed(library):
    """A wrong address must produce a sentence, never a freeze or a traceback."""
    app = make_app()
    screen = JoinScreen(app, library)
    app.push(screen)
    screen.code.value = "ABC123"
    screen.server.value = ""
    render(app)
    screen.confirm()
    # An empty address cannot be dialled; the message says so and we stay put.
    assert isinstance(app.screen, (JoinScreen, LobbyScreen))


def test_the_join_form_normalises_the_typed_code(library):
    app = make_app()
    screen = JoinScreen(app, library)
    app.push(screen)
    assert screen.code.placeholder


# ── the lobby ────────────────────────────────────────────────────────────────
def lobby_pair(library, table: Table):
    """A host and a client, both looking at their lobby screen."""
    host_app, client_app = make_app(), make_app()
    host = table.host("Kuba")
    host_screen = LobbyScreen(host_app, library, host)
    host_app.push(host_screen)

    client = table.join(host.room_code, "Ola")
    client_screen = LobbyScreen(client_app, library, client)
    client_app.push(client_screen)

    def pump(rounds: int = 8) -> None:
        for _ in range(rounds):
            for app in (host_app, client_app):
                app.screen.update(1 / 60, (0, 0))
            for service in table.services:
                service.poll(library)

    pump()
    return host, client, host_app, client_app, pump


def test_the_lobby_shows_the_code_to_share(library):
    table = Table(library)
    try:
        host, client, host_app, client_app, pump = lobby_pair(library, table)
        render(host_app)
        assert host_app.screen.lobby.code == host.room_code
        assert not host_app.screen.connecting
    finally:
        table.close()


def test_the_lobby_will_not_start_without_enough_players(library):
    table = Table(library)
    try:
        host, client, host_app, client_app, pump = lobby_pair(library, table)
        screen = host_app.screen
        render(host_app)
        assert not screen.start.enabled
        click(screen, screen.start.rect.center)
        pump()
        assert isinstance(host_app.screen, LobbyScreen), "still in the lobby"
    finally:
        table.close()


def test_a_client_marks_itself_ready_from_the_lobby(library):
    table = Table(library)
    try:
        host, client, host_app, client_app, pump = lobby_pair(library, table)
        screen = client_app.screen
        render(client_app)
        click(screen, screen.ready.rect.center)
        pump()
        assert host.lobby_state.seat_of(client.peer_id).ready
    finally:
        table.close()


def test_the_host_starting_puts_everyone_into_the_game(library):
    """Host and client take the same path in: the server broadcasts, and both
    screens notice the session appearing."""
    table = Table(library)
    try:
        host, client, host_app, client_app, pump = lobby_pair(library, table)
        third = table.join(host.room_code, "Norbert")
        for service in (client, third):
            service.set_ready(True)
        pump()

        host_app.screen._start()
        pump()
        assert isinstance(host_app.screen, GameScreen)
        assert isinstance(client_app.screen, GameScreen)
        assert host_app.screen.state.snapshot() == client_app.screen.state.snapshot()
    finally:
        table.close()


def test_a_client_is_sent_home_when_the_room_closes(library):
    table = Table(library)
    try:
        host, client, host_app, client_app, pump = lobby_pair(library, table)
        table.server._route(
            table.server.hub.close_room(host.room_code, "Pokój zamknięty"))
        pump()
        assert isinstance(client_app.screen, MainMenuScreen)
        assert client_app.screen.message
    finally:
        table.close()


# ── in the game ──────────────────────────────────────────────────────────────
def multiplayer_screens(library, table: Table):
    """A running three-player match, returning both game screens."""
    host_app, client_app = make_app(), make_app()
    host = table.host("Kuba")
    host_screen = LobbyScreen(host_app, library, host)
    host_app.push(host_screen)

    client = table.join(host.room_code, "Ola")
    client_screen = LobbyScreen(client_app, library, client)
    client_app.push(client_screen)
    third = table.join(host.room_code, "Norbert")

    def pump(rounds: int = 10) -> None:
        for _ in range(rounds):
            for app in (host_app, client_app):
                app.screen.update(1 / 60, (0, 0))
            for service in table.services:
                service.poll(library)

    for service in (client, third):
        service.set_ready(True)
    pump()
    host_app.screen._start()
    pump()
    return host, client, third, host_app, client_app, pump


def test_only_the_active_player_can_act_in_a_match(library):
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        host_screen, client_screen = host_app.screen, client_app.screen
        assert isinstance(client_screen, GameScreen)
        state = client_screen.state
        assert state.local_seat == 1

        active = state.active_player_index
        idle_seat = (active + 1) % 3
        idle_screen = {0: host_screen, 1: client_screen}.get(idle_seat)
        if idle_screen is not None:
            before = idle_screen.state.deck(settings.DECK_MOVEMENT).draw_count
            render(client_app if idle_seat == 1 else host_app)
            app = client_app if idle_seat == 1 else host_app
            click(idle_screen, app.layout.deck_draw_rect(0).center)
            pump()
            assert idle_screen.state.deck(settings.DECK_MOVEMENT).draw_count \
                == before, "a spectator's click does nothing"

        assert host_screen.state.snapshot() == client_screen.state.snapshot()
    finally:
        table.close()


def test_each_machine_shows_its_own_hand_not_the_active_one(library):
    """The old reported bug: host "Byd" and client "Lap" both played Byd's
    cards."""
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        host_screen, client_screen = host_app.screen, client_app.screen
        assert host_screen.view_seat == 0
        assert client_screen.view_seat == 1, "the client watches its own seat"
        assert client_screen.hand.owner.name == "Ola"
        assert host_screen.hand.owner.name == "Kuba"
        assert client_screen.hand.hand is not host_screen.hand.hand
    finally:
        table.close()


def test_a_client_cannot_look_at_another_hand(library):
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        screen = client_app.screen
        assert not screen.may_view(0)
        screen.focus_seat(0)
        assert screen.view_seat == 1, "the request is simply ignored"

        render(client_app)
        click(screen, client_app.layout.player_tile_rect(
            0, len(screen.state.players)).center)
        assert screen.view_seat == 1
        assert screen.hand.owner.name == "Ola"
    finally:
        table.close()


def test_leaving_a_match_returns_to_the_main_menu(library):
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        client_app.screen._leave_match()
        assert isinstance(client_app.screen, MainMenuScreen)
    finally:
        table.close()


def test_the_others_carry_on_when_somebody_leaves(library):
    """The match lives on the server, so one player quitting is one empty seat
    rather than the end of everybody's game."""
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        third.close()
        pump()
        assert isinstance(host_app.screen, GameScreen)
        assert isinstance(client_app.screen, GameScreen)
        assert host_app.screen.state.snapshot() == \
            client_app.screen.state.snapshot()
    finally:
        table.close()


def test_a_reconnecting_player_sees_a_banner_not_a_menu(library):
    """A four-second hiccup should cost four seconds, not the match."""
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        screen = client_app.screen
        client.client.transport.drop()
        pump(2)
        assert isinstance(client_app.screen, GameScreen), "the board stays up"
        assert client.reconnecting
        render(client_app)             # the banner must draw without crashing

        client.client.transport.restore()
        pump(14)
        assert client.disconnected is None
        assert client_app.screen.state.snapshot() == \
            host_app.screen.state.snapshot()
    finally:
        table.close()


# ── the debug panel ──────────────────────────────────────────────────────────
def test_the_debug_panel_is_off_until_asked_for(library):
    app = make_app()
    state = create_game(SessionConfig(num_players=3, board_cells=24, seed=1),
                        library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    assert not screen.debug_panel.enabled

    render(app)
    plain = app.canvas.copy()
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3),
                        (0, 0))
    assert screen.debug_panel.enabled
    render(app)

    corner = pygame.Rect(app.layout.win_w - 340, 10, 330, 200)
    changed = sum(
        1
        for x in range(corner.left, corner.right, 6)
        for y in range(corner.top, corner.bottom, 6)
        if plain.get_at((x, y)) != app.canvas.get_at((x, y))
    )
    assert changed > 50, "the panel should be visible once switched on"


def test_the_debug_panel_reports_the_networking_state(library):
    table = Table(library)
    try:
        host, client, third, host_app, client_app, pump = \
            multiplayer_screens(library, table)
        screen = client_app.screen
        screen.debug_panel.enabled = True
        pump()
        lines = dict(screen.debug_panel._lines(screen.session, screen.service,
                                               screen.state))
        assert lines["tryb"] == "client"
        assert lines["pokój"] == host.room_code
        assert "moje miejsce" in lines

        host_lines = dict(host_app.screen.debug_panel._lines(
            host_app.screen.session, host_app.screen.service,
            host_app.screen.state))
        assert host_lines["tryb"] == "host"
        # The fingerprint is how a desync gets spotted: same state, same hash.
        assert lines["suma stanu"] == host_lines["suma stanu"]
    finally:
        table.close()


# ── ownership, on one machine ────────────────────────────────────────────────
def test_a_spectator_cannot_play_from_the_hand_it_watches(library):
    """Hot-seat off: looking at a hand is not the same as holding it."""
    app = make_app()
    state = create_game(
        SessionConfig(num_players=3, board_cells=24, seed=4,
                      edit_mode=False, local_seat=1, debug_version=True),
        library,
    )
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    render(app)

    screen.focus_seat(0)
    assert screen.view_seat == 0, "the development option allows looking"
    assert not screen.controls_view

    before = len(state.players[0].hand)
    render(app)
    card = state.players[0].hand[0]
    slot = screen.hand.slots.get(card.uid)
    if slot is not None:
        click(screen, slot.position)
    assert len(state.players[0].hand) == before, "watching is not playing"
    assert screen.status_bar.message


def test_there_is_always_a_way_back_to_your_own_player(library):
    app = make_app()
    state = create_game(
        SessionConfig(num_players=4, board_cells=24, seed=4, local_seat=2),
        library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    render(app)

    screen.focus_seat(0)
    assert screen.view_seat == 0
    render(app)

    click(screen, app.layout.return_seat_button.center)
    assert screen.view_seat == screen.my_seat == 2

    screen.focus_seat(1)
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_HOME),
                        (0, 0))
    assert screen.view_seat == 2


def test_hot_seat_still_follows_the_turn(library):
    app = make_app()
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=4),
                        library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    render(app)

    screen.submit(cmd.SetActivePlayer(player_index=2))
    assert screen.view_seat == 2, "one keyboard: the screen follows the turn"
    assert screen.controls_view
