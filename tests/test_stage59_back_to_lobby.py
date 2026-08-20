"""
Stage 59 — a room outlives its matches.

    MATCH -> LOBBY -> MATCH, on the same room, the same code, the same
    connections and the same seats.

Before this the only way back to the poczekalnia was to win, so a table that
wanted to try a different deck had to close the room, make another one and set
it up again. The transition itself already existed and already did the right
teardown; what it refused was being asked before somebody had won, on the
grounds that one player must not be able to end everybody else's game. That
reasoning is kept and narrowed rather than dropped: mid-match, only the HOST may
ask — the same say-so that starts a match and closes the room. A non-host asking
mid-match is refused exactly as it always was, and there is still a test that
says so.

WHAT THE TRANSITION IS NOT. It is not a screen change: no client decides on its
own that the room is a lobby, and the connection is untouched. The server drops
the match and broadcasts the lobby, and every client leaves the table on that
one message — including the ones that pressed nothing.

WHAT THE NEXT MATCH IS. Built fresh in ``Room.start`` from the lobby's current
settings and a new seed, never wound back out of the old ``GameState``. That is
what these tests are really pinning: not "the fields were reset" but "the old
match cannot be found anywhere in the new one", hands, board, statuses, secret
and all.

READY FLAGS ARE DELIBERATELY KEPT across the transition and deliberately
CLEARED when the settings change afterwards. Ready is consent to a table: a
rematch on the same table is still consented to and stays one click for the
host, while a table somebody has just rebuilt is not. See
``Room._apply_settings``, which only does this once the room has actually played
something — before the first match, settings messages ARE the setup.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pygame
import pytest

from netkit import Table, playable_card, take_a_turn
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.network_screens import LobbyScreen

WINDOW = (1920, 1080)

#: Every window the project promises. The pause menu grew a row and the
#: changelog records exactly that pushing a screen off the bottom before.
REFERENCE_WINDOWS = [(1280, 760), (1600, 900), (1920, 1080), (2560, 1440),
                     (3840, 2160)]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library) -> Table:
    made = Table(library)
    yield made
    made.close()


# ── harness ──────────────────────────────────────────────────────────────────
def screens_for(services, size=WINDOW, library=None):
    made = []
    for service in services:
        app = App(Layout(), headless=True, size=size)
        app.push(GameScreen(app, service.session, service=service,
                            library=library))
        made.append(app)
    return made


def settle(apps, table=None):
    """Let every screen notice what the server said."""
    if table is not None:
        table.pump()
    for app in apps:
        app.screen.update(1 / 60.0, (0, 0))


def frame(screen) -> None:
    app = screen.app
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(1 / 60.0, (0, 0))
    screen.draw(app.canvas)


def pause_labels(screen) -> list:
    return [label for _, label in screen._pause_entries()]


def choose_pause(app, screen, key) -> None:
    """Click a pause entry by key, through the real event path."""
    screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0), (0, 0))
    screen.pause_menu._lay_out(app.layout, app.renderer)
    entries = dict(zip([k for k, _ in screen.pause_menu.entries],
                       screen.pause_menu.rects))
    assert key in entries, f"no {key!r} entry: {screen.pause_menu.entries}"
    where = entries[key].center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=where, button=1), where)


def a_match_in_progress(table, library, *names):
    host, clients = table.playing(*names)
    apps = screens_for([host, *clients], library=library)
    return host, clients, apps


# ── 1-6: the transition itself ───────────────────────────────────────────────
def test_the_host_can_return_an_active_match_to_the_lobby(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    code = host.room_code
    assert room.state is not None, "a match really is running"

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)

    assert room.state is None, "the match was not dropped"
    assert not room.lobby.started
    assert table.room(code) is not None, "the room was closed"
    assert room.code == code, "the code changed"


def test_every_client_lands_in_the_lobby_not_just_the_one_who_asked(table,
                                                                    library):
    """A ROOM-LEVEL transition. Nobody decides this locally."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)

    assert all(isinstance(app.screen, LobbyScreen) for app in apps), \
        [type(app.screen).__name__ for app in apps]
    assert all(service.session is None for service in [host, *clients])


def test_the_seats_and_the_connections_survive(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    before = [(s.peer_id, s.seat, s.nickname) for s in room.lobby.seats]

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)

    assert [(s.peer_id, s.seat, s.nickname)
            for s in room.lobby.seats] == before, "somebody lost their seat"
    for service in [host, *clients]:
        assert service.disconnected is None, "somebody was disconnected"
        assert service.client.transport.alive, "a connection was closed"
    assert len(room.members) == 3


def test_a_non_host_still_cannot_reset_a_running_match(table, library):
    """The rule the refusal always protected, kept and narrowed."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)

    clients[0].return_to_lobby()
    settle(apps, table)

    assert room.state is not None, "one player ended everybody's game"
    assert all(service.session is not None for service in [host, *clients])


def test_only_the_host_is_offered_the_entry(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")

    assert "Wróć do poczekalni" in pause_labels(apps[0].screen)
    assert "Wróć do poczekalni" not in pause_labels(apps[1].screen), \
        "a non-host was offered an action the server refuses"


def test_a_hot_seat_pause_menu_is_unchanged(library):
    """No service, no room, no lobby to go back to."""
    from pedzacy_piotrek.config.settings import SessionConfig
    from pedzacy_piotrek.engine.setup import create_game
    from pedzacy_piotrek.net.session import LocalSession

    state = create_game(SessionConfig(num_players=3, edit_mode=True, seed=7),
                        library)
    app = App(Layout(), headless=True, size=WINDOW)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)

    assert "Wróć do poczekalni" not in pause_labels(screen)


# ── 7-12: nothing of the old match survives ──────────────────────────────────
def test_the_old_match_leaves_nothing_behind(table, library):
    """Not "the fields were reset" — the old match cannot be FOUND.

    Compared against what the previous match actually contained: its board, its
    hands, its statuses and its secret. Anything that came back would come back
    here.
    """
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    take_a_turn(table, host, clients)
    old_secret = room.state.piotrek_pawn
    # The CARD OBJECTS, not their uids.  Uids are handed out when a deck is
    # built, so a fresh deck numbers its cards from the same place and the new
    # match legitimately holds the same numbers — comparing them would fail
    # against correct behaviour.  What must not come back is the old cards.
    old_cards = {id(card) for p in room.state.players for card in p.hand}
    old_sizes = {p.index: len(p.hand) for p in room.state.players}
    old_tiles = dict(room.state.board.pawn_tiles)
    old_log = len(room.command_log)
    assert old_secret and old_log, "the old match had something in it"

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)

    assert room.state is None
    assert room.session_config is None
    assert room.command_log == []
    assert room.fingerprint == ""
    assert not room.identity_settled
    assert room.identity_peer is None

    host.start_game(library)
    settle(apps, table)
    fresh = room.state
    assert fresh is not None, "the next match did not start"

    assert fresh.piotrek_pawn is None, "the old identity survived"
    assert not room.identity_settled, "the next match asks again"
    assert fresh.board.pawn_tiles != old_tiles or not old_tiles, \
        "the old board survived"
    from pedzacy_piotrek.engine.setup import starting_hand_size

    for player in fresh.players:
        assert not any(id(card) in old_cards for card in player.hand), \
            f"seat {player.index} is holding cards from the old match"
        assert len(player.hand) == starting_hand_size(player), \
            f"seat {player.index} was not dealt a fresh opening hand"
        assert not player.eliminated, "an old elimination survived"
        assert not player.marks, "old notepad marks survived"
        assert player.secret_pawn is None, "an old secret survived"
    assert old_sizes, "sanity: the old match had hands to lose"
    assert fresh.victory is None, "the old victory survived"
    assert fresh.round_number == 1, "the old round counter survived"


def test_the_secret_does_not_leak_through_the_lobby(table, library):
    """Hidden information does not get a free ride on the transition."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    secret = room.state.piotrek_pawn
    assert secret

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)

    published = str(room.lobby.to_dict())
    assert "secret" not in published
    assert f"'{secret}'" not in published
    for service in [host, *clients]:
        assert service.state is None, "a replica kept the old match"
        assert str(secret) not in str(service.lobby_state.to_dict())


def test_the_next_match_asks_for_a_fresh_identity(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    host.start_game(library)
    settle(apps, table)

    assert room.state.piotrek_pawn is None, "nobody has chosen yet"
    assert not room.identity_settled
    assert table.piotrek(host, clients) is not None, "somebody is being asked"


# ── 13-19: the settings ──────────────────────────────────────────────────────
def lobby_screen_for(app, service, library) -> LobbyScreen:
    screen = app.screen
    assert isinstance(screen, LobbyScreen), type(screen).__name__
    return screen


def test_the_host_can_reopen_the_settings_from_the_lobby(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    screen = lobby_screen_for(apps[0], host, library)

    assert screen.may_edit_settings
    screen._open_settings()

    assert screen.settings_panel.active, "the panel did not open"
    # Seeded from the ROOM, not from this screen's defaults.  The lobby stores
    # OVERRIDES, so it is the room's entries that must appear in the panel
    # rather than the two mappings being equal.
    room = table.room(host.room_code)
    for title, count in room.lobby.mod_counts.items():
        assert screen.settings_panel.mod_counts[title] == count


def test_a_non_host_is_not_offered_the_settings(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    screen = lobby_screen_for(apps[1], clients[0], library)

    assert not screen.may_edit_settings, "a non-host was offered the settings"


def test_a_non_host_asking_anyway_is_refused_by_the_server(table, library):
    """The button being absent is a courtesy; this is the actual rule."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    room = table.room(host.room_code)
    before = dict(room.lobby.movement_counts)
    screen = lobby_screen_for(apps[0], host, library)

    clients[0].set_settings(movement_counts={a_changed_title(screen): 9})
    settle(apps, table)

    assert dict(room.lobby.movement_counts) == before, \
        "a non-host changed the table"


def a_changed_title(screen):
    """A real movement card, taken from the panel's own titles.

    Not from ``lobby.movement_counts``: that mapping holds OVERRIDES and is
    empty on a fresh room, so indexing it picks nothing at all.
    """
    panel = screen.settings_panel
    return sorted(panel.tabs[panel._index_of(settings.DECK_MOVEMENT)].titles)[0]


def test_changed_settings_reach_the_server_and_every_client(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    room = table.room(host.room_code)
    screen = lobby_screen_for(apps[0], host, library)
    title = a_changed_title(screen)

    screen._open_settings()
    panel = screen.settings_panel
    panel.tabs[panel._index_of(settings.DECK_MOVEMENT)].values[title] = 7
    panel.close()
    screen.update(1 / 60.0, (0, 0))       # closing is applying
    settle(apps, table)

    assert room.lobby.movement_counts[title] == 7, "the server was not told"
    for service in [host, *clients]:
        assert service.lobby_state.movement_counts[title] == 7, \
            "a client's mirror is stale"


def test_the_next_match_is_built_from_the_new_settings(table, library):
    """The point of the whole stage: change the table, play the new one."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    room = table.room(host.room_code)
    screen = lobby_screen_for(apps[0], host, library)
    title = a_changed_title(screen)

    screen._open_settings()
    panel = screen.settings_panel
    panel.tabs[panel._index_of(settings.DECK_MOVEMENT)].values[title] = 7
    panel.close()
    screen.update(1 / 60.0, (0, 0))
    settle(apps, table)

    for service in [host, *clients]:
        seat = service.lobby_state.seat_of(service.peer_id)
        if seat is not None and not seat.is_host:
            service.set_ready(True)
    settle(apps, table)
    host.start_game(library)
    settle(apps, table)

    assert room.state is not None, room.lobby.validate()
    assert room.session_config.movement_counts[title] == 7, \
        "the new match ignored the new settings"
    # Counted across the WHOLE deck — the pile, the discards and every hand —
    # because the match has already dealt starting hands out of it.
    deck = room.state.decks[settings.DECK_MOVEMENT]
    dealt = sum(1 for card in [*deck.draw_pile, *deck.discard_pile,
                               *(c for p in room.state.players for c in p.hand)]
                if card.definition.title == title)
    assert dealt == 7, f"the deck was built with {dealt} rather than 7"


def test_no_session_config_field_is_lost_across_the_round_trip(table, library):
    """The failure this project has actually had, guarded generically.

    ``to_config`` is asked for every field it can produce, before and after,
    rather than a list written out here — a list is what silently stops
    covering a field somebody adds next year.
    """
    from dataclasses import fields

    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    before = room.session_config
    assert before is not None

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    host.start_game(library)
    settle(apps, table)
    after = room.session_config

    ignored = {"seed", "character_choices", "local_seat"}
    for field_def in fields(before):
        if field_def.name in ignored:
            continue
        assert getattr(after, field_def.name) == getattr(before,
                                                         field_def.name), \
            f"{field_def.name} was lost between matches"
    assert after.seed != before.seed, "the next match reused the old seed"


def test_changing_the_settings_withdraws_ready(table, library):
    """Ready is consent to a TABLE, and the table just changed.

    The decision recorded in the module docstring: kept across the transition,
    cleared by a settings change, and only once the room has played something —
    before the first match a settings message IS the setup.
    """
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    room = table.room(host.room_code)
    assert any(s.ready for s in room.lobby.seats if not s.is_host), \
        "the flags survived the transition, which is the other half of this"

    screen = lobby_screen_for(apps[0], host, library)
    screen._open_settings()
    panel = screen.settings_panel
    panel.tabs[panel._index_of(settings.DECK_MOVEMENT)].values[
        a_changed_title(screen)] = 5
    panel.close()
    screen.update(1 / 60.0, (0, 0))
    settle(apps, table)

    assert not any(s.ready for s in room.lobby.seats if not s.is_host), \
        "everybody is still ready for a table that no longer exists"
    assert room.lobby.validate(), "and the host cannot start without them"


def test_a_rematch_on_the_same_table_is_still_one_click(table, library):
    """The behaviour the narrow rule protects."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)
    room = table.room(host.room_code)

    assert room.lobby.validate() == "", room.lobby.validate()
    host.start_game(library)
    settle(apps, table)
    assert room.state is not None, "the rematch needed a second round of ready"


# ── 20-23: the neighbouring behaviours ───────────────────────────────────────
def test_the_post_match_return_still_works(table, library):
    """The existing path, unified with the new one rather than duplicated."""
    from pedzacy_piotrek.board.tiles import TileKind

    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    finish = next(t for t in room.state.board.tiles if t.kind is TileKind.FINISH)
    room.state.board.place_pawn(room.state.piotrek_pawn, finish.index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)
    assert room.state.finished, "the match really ended"

    # A NON-host, which is the case the finished-match rule still allows.
    clients[0].return_to_lobby()
    settle(apps, table)

    assert room.state is None and not room.lobby.started
    assert all(service.session is None for service in [host, *clients])
    assert len(room.lobby.seats) == 3


def test_resume_is_still_a_local_pause_action(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)

    choose_pause(apps[0], apps[0].screen, "resume")
    settle(apps, table)

    assert room.state is not None, "resuming ended the match"
    assert host.session is not None
    assert isinstance(apps[0].screen, GameScreen)
    assert not apps[0].screen.pause_menu.active


def test_returning_to_the_lobby_does_not_close_the_connection(table, library):
    """The distinction the pause menu now has to keep: match, not room."""
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)

    choose_pause(apps[0], apps[0].screen, "lobby")
    settle(apps, table)

    assert host.client.transport.alive, "the socket was closed"
    assert not host.departed, "the room was left"
    assert host.peer_id in room.members, "the server lost the player"
    assert room.lobby.seat_of(host.peer_id) is not None


def test_the_other_endings_still_leave_the_room(table, library):
    host, clients, apps = a_match_in_progress(table, library,
                                              "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)

    choose_pause(apps[1], apps[1].screen, "leave")
    settle(apps, table)
    assert clients[0].peer_id not in room.members, "menu did not leave"

    choose_pause(apps[2], apps[2].screen, "quit")
    settle(apps, table)
    assert clients[1].peer_id not in room.members, "quit did not leave"
    assert not apps[2].running


# ── layout ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_longer_pause_menu_still_fits(table, library, size):
    """A row was added to a menu that has overflowed before."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    app = App(Layout(), headless=True, size=size)
    screen = GameScreen(app, host.session, service=host, library=library)
    app.push(screen)

    screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0), (0, 0))
    screen.pause_menu._lay_out(app.layout, app.renderer)
    rects = screen.pause_menu.rects
    window = pygame.Rect(0, 0, *size)

    assert len(rects) == 5, "the host's five entries"
    for rect in rects:
        assert window.contains(rect), f"an entry left the window at {size}"
    for index, rect in enumerate(rects):
        for other in rects[index + 1:]:
            assert not rect.colliderect(other), f"entries overlap at {size}"


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_lobby_settings_button_fits_beside_the_code(table, library, size):
    """It shares the room-code row, which is why it must clear the copy button."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    app = App(Layout(), headless=True, size=size)
    app.push(GameScreen(app, host.session, service=host, library=library))
    host.return_to_lobby()
    table.pump()
    app.screen.update(1 / 60.0, (0, 0))
    screen = app.screen
    assert isinstance(screen, LobbyScreen)
    frame(screen)

    button = screen.settings_button.rect
    window = pygame.Rect(0, 0, *size)
    assert window.contains(button), f"the settings button left the window at {size}"
    assert not button.colliderect(screen.copy_code.rect), \
        f"it lands on the copy button at {size}"
    assert button.width > 8 and button.height > 8
