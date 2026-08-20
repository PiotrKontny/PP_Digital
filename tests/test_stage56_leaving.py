"""
LEAVING, AND THE PROCESS THAT WOULD NOT END.

Two defects that met in the same place.

**The process.**  ``WebSocketTransport`` read its outbox with a blocking
``queue.get`` handed to asyncio's default executor.  ``concurrent.futures``
joins every executor worker it has ever created at interpreter shutdown —
daemon flag included, through a ``threading._register_atexit`` hook that runs
before daemon threads are abandoned — and that worker only returned when the
transport's stop flag was set.  A game closed by its window button never set it.
The window went, the process stayed, and it could not be stopped from a
debugger because the main thread was inside a non-interruptible ``join()``.

**The room.**  Leaving queued a goodbye and closed the socket in the same
breath, and the close won: the message was dropped rather than drained.  With
nothing on the wire the server saw only a socket that stopped answering, which
is precisely what a train entering a tunnel looks like — so it did the kind
thing, held the seat, and started a three-minute grace period for a player who
was already looking at the main menu.  When that was the last player, the room
outlived everybody in it.

The distinction these tests exist to pin down:

    intentional departure   -> seat freed now, room closed if it was the last
    temporary disconnection -> seat held, grace period, room survives
    grace period expiring    -> seat freed by the tick, room closed if last

The first is a decision and travels as a message.  The other two are things
that happen to a player and are timed by the server.  Nothing here changes the
second or the third; the whole point is that the first stops being mistaken for
them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame
import pytest

from netkit import Table
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.net.protocol import MessageType
from pedzacy_piotrek.net.transport import ConnectionState
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.network_screens import LobbyScreen, MainMenuScreen

WINDOW = (1600, 900)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


# ── driving the pause menu the way a player does ─────────────────────────────
def make_app() -> App:
    return App(Layout(), headless=True, size=WINDOW)


def game_screen(app: App, service, library) -> GameScreen:
    screen = GameScreen(app, service.session, service=service, library=library)
    app.push(screen)
    return screen


def open_pause_menu(screen: GameScreen) -> None:
    """Esc, through the real key handler."""
    screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0), (0, 0))
    assert screen.pause_menu.active, "Esc did not open the pause menu"


def choose(app: App, screen: GameScreen, key: str) -> None:
    """Click a pause entry by its key, through the real event path.

    The rects are computed while drawing, so the menu is laid out first — the
    alternative is calling the handler directly, which would test the branch
    without testing that the entry is reachable.
    """
    open_pause_menu(screen)
    screen.pause_menu._lay_out(app.layout, app.renderer)
    entries = dict(zip([k for k, _ in screen.pause_menu.entries],
                       screen.pause_menu.rects))
    assert key in entries, f"no {key!r} entry: {screen.pause_menu.entries}"
    where = entries[key].center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=where, button=1), where)


def a_match(library, table: Table, *names: str):
    """A real started match, plus an application per player."""
    host, clients = table.playing(*names)
    parties = [host, *clients]
    apps = []
    for service in parties:
        app = make_app()
        game_screen(app, service, library)
        apps.append(app)
    return parties, apps


# ── 1-3: the three pause entries ─────────────────────────────────────────────
def test_returning_to_the_main_menu_leaves_the_room(library):
    """"Wróć do menu głównego" — the entry, the room, and the screen."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[1].room_code
        peer = parties[1].peer_id

        choose(apps[1], apps[1].screen, "leave")
        table.pump()

        room = table.room(code)
        assert room is not None, "the other two are still playing"
        assert peer not in room.members, "the room still holds the leaver"
        assert isinstance(apps[1].screen, MainMenuScreen)
    finally:
        table.close()


def test_quitting_the_game_leaves_the_room(library):
    """"Wyjdź z gry" leaves it too, and only then stops the application."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[2].room_code
        peer = parties[2].peer_id

        choose(apps[2], apps[2].screen, "quit")
        table.pump()

        assert peer not in table.room(code).members
        assert not apps[2].running, "the application is stopping"
    finally:
        table.close()


def test_returning_to_the_game_does_not_leave_the_room(library):
    """"Wróć do gry" is the entry somebody who opened the menu by accident
    is reaching for.  It must cost them nothing at all."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[1].room_code
        peer = parties[1].peer_id

        choose(apps[1], apps[1].screen, "resume")
        table.pump()

        room = table.room(code)
        assert peer in room.members, "resuming threw the player out"
        assert peer in room.present, "resuming dropped the connection"
        assert not parties[1].departed
        assert not apps[1].screen.pause_menu.active, "the menu closed"
        assert isinstance(apps[1].screen, GameScreen), "still at the table"
    finally:
        table.close()


# ── 4: the lifecycle boundary itself, not a flag on a screen ─────────────────
def test_the_server_stops_treating_a_departed_player_as_connected(library):
    """The boundary this stage is about, checked on the SERVER.

    Not "the client thinks it left": the hub's own idea of who is in which
    room, and the message that told it so.  A leave that only changed a screen
    is the defect, so a test that only reads a screen would not have caught it.
    """
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        leaver = parties[1]
        code, peer = leaver.room_code, leaver.peer_id
        transport = leaver.client.transport

        choose(apps[1], apps[1].screen, "leave")
        table.pump()

        assert MessageType.LEAVE_LOBBY.value in \
            table.server.received_from(transport), \
            "leaving never reached the server as a message"
        room = table.room(code)
        assert peer not in room.members
        assert peer not in room.present
        identity = table.server.hub.identities[peer]
        assert identity.room_code == "", "the hub still places them in a room"
        assert transport.state is ConnectionState.CLOSED
    finally:
        table.close()


def test_a_departure_is_not_a_disconnection_the_others_wait_out(library):
    """Nobody is asked to wait for somebody who chose to go.

    A dropped player is announced with a grace period the table sits through;
    a departing one is announced with none.  Same message, and the number in it
    is the whole difference between the two lifecycles.
    """
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        host = parties[0]
        host.drain_notices()

        choose(apps[1], apps[1].screen, "leave")
        table.pump()

        notices = " ".join(host.drain_notices())
        assert "opuścił grę" in notices, notices
        assert "czekamy" not in notices, "the table was told to wait"
    finally:
        table.close()


# ── 5-6: what the room does about it ─────────────────────────────────────────
def test_the_room_lives_on_while_other_players_remain(library):
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[1].room_code

        choose(apps[1], apps[1].screen, "leave")
        table.pump()

        room = table.room(code)
        assert room is not None and not room.closed
        assert len(room.members) == 2
        assert parties[0].session is not None, "the host is still playing"
        assert parties[2].session is not None
    finally:
        table.close()


def test_the_last_player_leaving_removes_the_room(library):
    """The report behind this: a Railway deployment serving one room, held by
    nobody, answering every new game with "Serwer jest zajęty"."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[0].room_code

        for app in apps:
            choose(app, app.screen, "leave")
            table.pump()

        assert table.room(code) is None, "the room outlived everybody in it"
        assert len(table.server.hub.rooms) == 0
        assert all(identity.room_code == ""
                   for identity in table.server.hub.identities.values())
    finally:
        table.close()


def test_another_room_opens_afterwards_without_restarting_the_server(library):
    """A room is not the process.  The original report said otherwise."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[0].room_code
        for app in apps:
            choose(app, app.screen, "leave")
            table.pump()

        fresh = table.host("Zosia")
        table.pump()

        assert fresh.room_code and fresh.room_code != code
        assert fresh.error is None, fresh.error
    finally:
        table.close()


def test_the_last_player_leaving_the_lobby_removes_the_room(library):
    """The same boundary before a match has started, from the lobby screen."""
    table = Table(library)
    try:
        host, clients = table.seated("Kuba", "Ola")
        code = host.room_code
        apps = []
        for service in (host, *clients):
            app = make_app()
            app.push(LobbyScreen(app, library, service))
            apps.append(app)

        for app in apps:
            app.screen._leave()
            table.pump()

        assert table.room(code) is None
        assert all(isinstance(app.screen, MainMenuScreen) for app in apps)
    finally:
        table.close()


# ── 7-8: the reconnect contract, which is NOT what leaving uses ──────────────
def test_a_temporary_disconnection_does_not_destroy_the_room(library):
    """A socket that stops answering is not a decision.

    The seat is held, the grace period runs, and the room stays — including
    when the drop takes the last connected player with it.  Reaping on "nobody
    is connected right now" would end matches out from under people whose train
    went into a tunnel.
    """
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        code = parties[0].room_code
        for service in parties:
            service.client.transport.drop()
        table.tick()

        room = table.room(code)
        assert room is not None, "a hiccup destroyed the room"
        assert len(room.members) == 3, "the seats are held through the grace"
        assert not room.present, "and nobody is connected to them"
    finally:
        table.close()


def test_a_player_reconnecting_in_the_grace_period_keeps_the_existing_contract(
        library):
    """Unchanged behaviour, guarded because it is what would break first."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        client = parties[1]
        code, peer = client.room_code, client.peer_id

        client.client.transport.drop()
        table.pump()
        assert peer in table.room(code).members, "the seat is held"

        client.client.transport.restore()
        table.pump(20)

        room = table.room(code)
        assert peer in room.present, "the returning player is back in the room"
        assert client.disconnected is None
        assert client.session is not None
        assert client.state.snapshot() == parties[0].state.snapshot(), \
            "the returning player was not caught up"
    finally:
        table.close()


def test_a_departed_player_is_not_offered_their_seat_back(library):
    """Leaving is final in a way a drop is not.

    The resume token still identifies the player — the hub has not forgotten
    them, and should not — but it no longer places them in a room, so a
    reconnection cannot walk back through a door they closed behind them.
    """
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        leaver = parties[1]
        code, peer = leaver.room_code, leaver.peer_id

        choose(apps[1], apps[1].screen, "leave")
        table.pump()

        identity = table.server.hub.identities[peer]
        assert identity.resume_token, "the player is still known"
        assert identity.room_code == ""
        room = table.room(code)
        accepted, reason = room.reconnect(peer)
        assert not accepted, "a departed player was re-seated"
        assert reason
    finally:
        table.close()


# ── 9: the application closing its own connection ────────────────────────────
def test_closing_the_application_closes_its_connection(library):
    """The window's close box pops no screens, so the APPLICATION owns this.

    Nothing in the screen stack can be relied on to release a connection that
    is handed from the join screen to the lobby to the game, which is why the
    application holds it and closes it on the way out of the main loop.
    """
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        app, service = apps[1], parties[1]
        code, peer = service.room_code, service.peer_id
        app.own(service)

        app.close_owned()
        table.pump()

        assert service.client.transport.state is ConnectionState.CLOSED
        assert peer not in table.room(code).members, \
            "the goodbye never reached the room"
    finally:
        table.close()


def test_closing_the_window_leaves_the_room(library):
    """THE PATH THAT HAD NO CLEANUP AT ALL, driven through the real main loop.

    ``QUIT`` ends the loop where it stands: no screen is popped, so no
    ``on_exit`` runs, and before this stage nothing anywhere closed the
    connection.  The player was gone from their own machine and still seated at
    the table, and the room — with ``max_rooms`` at 1, the whole server — was
    held by them until the grace period expired.

    The teardown is in the loop's ``finally``, which is why this test starts the
    real loop rather than calling the shutdown directly: the point is that the
    exit path reaches it.
    """
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        app, service = apps[1], parties[1]
        code, peer = service.room_code, service.peer_id
        app.own(service)

        pygame.event.post(pygame.event.Event(pygame.QUIT))
        app.run(max_frames=8)
        table.pump()

        assert not app.running
        assert service.client.transport.state is ConnectionState.CLOSED
        assert peer not in table.room(code).members, \
            "closing the window left the player seated"
    finally:
        table.close()


def test_the_lobby_hands_the_connection_to_the_application(library):
    """Ownership is taken where a connection ENTERS the game, once."""
    table = Table(library)
    try:
        from pedzacy_piotrek.ui.network_screens import JoinScreen

        app = make_app()
        app.push(MainMenuScreen(app, library))
        joining = JoinScreen(app, library)
        app.push(joining)
        service = table.host("Kuba")

        joining._enter_lobby(service)
        assert isinstance(app.screen, LobbyScreen)

        app.close_owned()
        assert service.client.transport.state is ConnectionState.CLOSED, \
            "the application never took the connection"
    finally:
        table.close()


def test_closing_twice_is_harmless(library):
    """Leaving already closed it; the shutdown that follows must not mind."""
    table = Table(library)
    try:
        parties, apps = a_match(library, table, "Kuba", "Ola", "Norbert")
        app, service = apps[0], parties[0]
        app.own(service)

        choose(app, app.screen, "leave")
        app.close_owned()
        service.close()
        table.pump()

        assert service.client.transport.state is ConnectionState.CLOSED
    finally:
        table.close()


# ── 10: the process ends ─────────────────────────────────────────────────────
#: A real socket to a real server, and then the interpreter is simply left to
#: exit.  Nothing is closed on purpose: that is the case that used to hang, and
#: a test that tidied up first would pass against the defect.
_LINGER = """
import sys, time
sys.path.insert(0, {root!r})
from dataclasses import replace
from pedzacy_piotrek.net.config import NetworkConfig
from pedzacy_piotrek.net.websocket import WebSocketTransport
from pedzacy_piotrek.server.embedded import EmbeddedServer

config = NetworkConfig()
config = replace(config, server=replace(config.server, host="127.0.0.1",
                                        port=0))
server = EmbeddedServer(config)
assert server.start(), server.error
transport = WebSocketTransport(server.url, config)
deadline = time.time() + 5
while time.time() < deadline and not transport.connected:
    time.sleep(0.02)
assert transport.connected, "never connected"
transport.send(__import__(
    "pedzacy_piotrek.net.protocol", fromlist=["Message"]).Message.hello("Kuba"))
time.sleep(0.5)
{closing}
print("main returned", flush=True)
"""


def _run_client_process(closing: str, timeout: float = 30.0):
    script = _LINGER.format(root=str(ROOT), closing=closing)
    started = time.monotonic()
    finished = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True, text=True, timeout=timeout,
    )
    return finished, time.monotonic() - started


@pytest.mark.slow
def test_the_client_process_ends_without_being_killed(library):
    """THE HANG, as the owner met it: the window is gone and python is not.

    Before the fix this process never exited — the interpreter blocked for ever
    in ``concurrent.futures``' atexit hook, joining an executor worker parked
    on a queue it would only leave if somebody had closed the transport.  The
    timeout below is the assertion; ``subprocess.run`` raises on expiry.
    """
    finished, elapsed = _run_client_process(closing="")

    assert "main returned" in finished.stdout, finished.stderr
    assert finished.returncode == 0, finished.stderr
    assert elapsed < 20, f"the process took {elapsed:.1f}s to end"


@pytest.mark.slow
def test_closing_the_transport_still_ends_the_process(library):
    """The tidy path, which must not have been broken by fixing the other."""
    finished, elapsed = _run_client_process(closing="transport.close()")

    assert "main returned" in finished.stdout, finished.stderr
    assert finished.returncode == 0, finished.stderr
    assert elapsed < 20, f"the process took {elapsed:.1f}s to end"


@pytest.mark.slow
def test_leaving_over_a_real_socket_reaches_the_server(library):
    """THE OTHER HALF OF THE DEFECT, and it needs a genuine socket to see.

    In-process transports hand a message straight to the hub, so the queues the
    rest of this file runs on cannot reproduce the failure at all: it was a race
    between the goodbye being queued and the socket being closed, and closing
    won.  Over a real connection the old code left the room believing this
    player had merely stopped answering — seat held, grace period running — and
    the room therefore outlived the last person in it.
    """
    from dataclasses import replace

    from pedzacy_piotrek.net.config import NetworkConfig
    from pedzacy_piotrek.net.service import ClientService, HostService
    from pedzacy_piotrek.server.embedded import EmbeddedServer

    config = NetworkConfig()
    config = replace(config, server=replace(config.server, host="127.0.0.1",
                                            port=0, max_rooms=2))
    server = EmbeddedServer(config)
    assert server.start(), server.error
    config = config.with_url(server.url)
    parties = []

    def pump(seconds: float = 1.5) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            for service in parties:
                service.poll(library)
            time.sleep(0.01)

    try:
        host = HostService("Kuba", config=config, library=library,
                           url=server.url)
        parties.append(host)
        pump(3.0)
        assert host.room_code, "the server answered with a room code"
        code = host.room_code

        ola = ClientService(code, "Ola", config=config, library=library,
                            url=server.url)
        parties.append(ola)
        pump(2.0)
        hub = server._server.hub
        room = hub.rooms.get(code)
        assert room is not None and len(room.members) == 2, "both are seated"
        peer = ola.peer_id

        ola.leave_room()
        pump(2.0)

        assert peer not in room.members, \
            "the goodbye was dropped by the close that followed it"
        assert not room.abandoned, "the host is still here"

        # And the last one out closes the room, which is the symptom the whole
        # stage came from.
        host.leave_room()
        pump(2.0)
        assert hub.rooms.get(code) is None, "the room outlived everybody"
    finally:
        for service in parties:
            service.close()
        server.stop()


def _networking_threads() -> list:
    return [t.name for t in threading.enumerate() if t.name == "piotrek-net"]


@pytest.mark.slow
def test_a_closed_transport_leaves_no_networking_thread_behind(library):
    """The transport gives back the thread it took.

    BY NAME, not by counting.  A count against a baseline passes alone and
    fails in a full run, because the rest of the suite has threads of its own
    and some of them outlive the test that made them; what this is actually
    about is whether *this* transport let go, and the thread it owns has a name.
    """
    from dataclasses import replace

    from pedzacy_piotrek.net.config import NetworkConfig
    from pedzacy_piotrek.net.websocket import WebSocketTransport
    from pedzacy_piotrek.server.embedded import EmbeddedServer

    config = NetworkConfig()
    config = replace(config, server=replace(config.server, host="127.0.0.1",
                                            port=0))
    server = EmbeddedServer(config)
    assert server.start(), server.error
    before = len(_networking_threads())
    try:
        transport = WebSocketTransport(server.url, config)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not transport.connected:
            time.sleep(0.02)
        assert transport.connected, transport.error
        assert len(_networking_threads()) == before + 1, \
            "the network thread never started"

        transport.close()

        deadline = time.monotonic() + 5
        while (time.monotonic() < deadline
               and len(_networking_threads()) > before):
            time.sleep(0.02)
        assert len(_networking_threads()) == before, \
            "the network thread outlived the transport"
    finally:
        server.stop()
