"""
ROOM LIFECYCLE.

``max_rooms`` is 1, so a room that outlives the people in it does not merely
waste memory — it is the whole server.  Every attempt to open a new one is
answered "Serwer jest zajęty" until ``room_idle_timeout`` expires, which is
FIFTEEN MINUTES.  Nobody waits fifteen minutes; they conclude the deployment is
broken and restart it, which is exactly the report this stage came from.

The pieces were nearly all there — ``Room.close``, ``RoomRegistry.close``,
``prune``, ``is_stale``, a reconnect grace period — and one path checked
whether the room had emptied while two did not:

    deliberate exit (BYE / LEAVE_LOBBY)   ->  checked
    lobby socket dropping                 ->  NOT checked
    grace period running out on the tick  ->  NOT checked

So the ways a room actually ends up empty in practice — people closing the
window, laptops sleeping, a match nobody comes back to — were the ways that
left it standing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.net.protocol import Message, MessageType
from pedzacy_piotrek.server.hub import ServerHub


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


class Table:
    """A hub with a clock a test can move."""

    def __init__(self, library):
        self.now = 0.0
        self.hub = ServerHub(library=library, clock=lambda: self.now)
        self.sent = []

    def hello(self, cid, nickname=""):
        self.hub.connect(cid)
        self.collect(self.hub.receive(cid, Message.hello(
            nickname=nickname or cid.upper())))

    def send(self, cid, kind, payload=None):
        self.collect(self.hub.receive(cid, Message(kind, payload or {})))

    def collect(self, outbound):
        self.sent.extend(outbound)
        return outbound

    def drain(self):
        sent, self.sent = self.sent, []
        return sent

    def messages_to(self, cid, kind=None):
        return [m for target, m in self.sent
                if target == cid and (kind is None or m.type is kind)]

    def open_room(self, cid, nickname=""):
        self.hello(cid, nickname)
        self.send(cid, MessageType.CREATE_LOBBY, {"nickname": nickname or cid})
        return next(iter(self.hub.rooms.rooms))

    def join(self, cid, code, nickname=""):
        self.hello(cid, nickname)
        self.send(cid, MessageType.JOIN_LOBBY,
                  {"code": code, "nickname": nickname or cid})

    def advance(self, seconds):
        self.now += seconds
        self.collect(self.hub.tick())


@pytest.fixture
def table(library):
    return Table(library)


def a_started_match(table):
    """Three players and a real match, through the real start path.

    A stubbed ``room.state`` was not enough: the same tick that expires
    absentees also expires decision windows, and that asks the state real
    questions.  Starting properly costs three lines and tests the thing.
    """
    code = table.open_room("host", "Kuba")
    for cid, name in (("guest", "Ola"), ("third", "Ala")):
        table.join(cid, code, name)
        table.send(cid, MessageType.PLAYER_READY, {"ready": True})
    table.send("host", MessageType.START_GAME, {})
    room = table.hub.rooms.get(code)
    assert room.started, room.lobby.validate()
    return code


def a_lobby_of_two(table):
    code = table.open_room("host", "Kuba")
    table.join("guest", code, "Ola")
    assert len(table.hub.rooms.get(code).members) == 2
    return code


# ── the bug: an empty room that keeps the server to itself ───────────────────
def test_a_lobby_everybody_disconnects_from_closes_at_once(table):
    """THE BUG.  Sockets dropping is how a lobby actually empties.

    ``mark_absent`` frees a lobby seat immediately — nobody has anything
    invested before the match starts — so the last socket dropping leaves a
    room with no members at all.  Nothing was checking, and with one room
    allowed the server was then unusable for fifteen minutes.
    """
    code = a_lobby_of_two(table)

    table.hub.disconnect("host")
    table.hub.disconnect("guest")

    assert table.hub.rooms.get(code) is None
    assert len(table.hub.rooms) == 0


def test_a_new_room_opens_immediately_afterwards(table):
    """The symptom, stated as the user meets it."""
    code = a_lobby_of_two(table)
    table.hub.disconnect("host")
    table.hub.disconnect("guest")

    table.drain()
    new_code = table.open_room("later", "Norbert")

    assert new_code != code
    errors = [m for _, m in table.sent if m.type is MessageType.ERROR]
    assert not errors, [m.payload for m in errors]


def test_a_match_nobody_returns_to_closes_when_the_grace_runs_out(table):
    """The other unchecked path: ``expire_absentees`` on the tick.

    A STARTED match holds the seats — that is the reconnect model and it is
    not being changed here — so the room correctly survives the disconnection
    itself.  What was missing is that when the grace period finally takes the
    last seat, somebody has to notice.
    """
    code = a_started_match(table)

    table.hub.disconnect("host")
    table.hub.disconnect("guest")
    table.hub.disconnect("third")
    assert table.hub.rooms.get(code) is not None, "seats are held during grace"

    table.advance(1.0)
    assert table.hub.rooms.get(code) is not None, "still inside the grace period"

    table.advance(table.hub.config.reconnect.grace_period + 1.0)
    assert table.hub.rooms.get(code) is None


def test_a_reconnect_inside_the_grace_period_keeps_the_room(table):
    """The reconnect model is unchanged, and this is what would break first.

    A started match holds its seats for the grace period, so the room must
    survive every disconnection in it — reaping on "nobody is connected" rather
    than "nobody is a member" would end matches out from under people whose
    train went into a tunnel.
    """
    code = a_started_match(table)
    table.hub.disconnect("host")
    table.hub.disconnect("guest")
    table.hub.disconnect("third")

    table.advance(table.hub.config.reconnect.grace_period - 1.0)
    room = table.hub.rooms.get(code)
    assert room is not None, "the room went before the grace period was up"
    assert len(room.members) == 3, "the seats are still being held"
    assert not room.present, "and nobody is connected to them"


def test_an_idle_room_with_a_member_still_waits_for_the_idle_timeout(table):
    """Unchanged semantics: ``is_stale`` still owns the slow path.

    ``abandoned`` is about MEMBERSHIP.  A room whose seats are still held is
    not abandoned however quiet it is, and the fifteen-minute timeout is what
    eventually collects it.  Reaping on silence instead would end matches
    people are still in.
    """
    code = a_started_match(table)
    room = table.hub.rooms.get(code)
    table.hub.disconnect("host")

    assert not room.abandoned
    table.advance(table.hub.config.reconnect.grace_period - 1.0)
    assert table.hub.rooms.get(code) is not None


# ── the explicit close ───────────────────────────────────────────────────────
def test_the_host_can_close_the_room(table):
    code = a_lobby_of_two(table)
    table.drain()

    table.send("host", MessageType.CLOSE_ROOM, {})

    assert table.hub.rooms.get(code) is None
    assert table.messages_to("guest", MessageType.MATCH_ENDED)
    assert table.messages_to("host", MessageType.MATCH_ENDED)


def test_a_guest_cannot_close_the_room(table):
    code = a_lobby_of_two(table)
    table.drain()

    table.send("guest", MessageType.CLOSE_ROOM, {})

    assert table.hub.rooms.get(code) is not None
    errors = table.messages_to("guest", MessageType.ERROR)
    assert errors and "host" in errors[0].payload["reason"].lower()
    assert not table.messages_to("host", MessageType.MATCH_ENDED)


def test_closing_leaves_nobody_believing_they_are_in_a_room(table):
    """Stale references are the point, not the message."""
    code = a_lobby_of_two(table)
    table.send("host", MessageType.CLOSE_ROOM, {})

    assert code not in table.hub.rooms.rooms
    assert all(identity.room_code == ""
               for identity in table.hub.identities.values())


def test_gameplay_cannot_be_submitted_to_a_closed_room(table):
    code = a_lobby_of_two(table)
    table.send("host", MessageType.CLOSE_ROOM, {})
    table.drain()

    table.send("guest", MessageType.COMMAND, {"kind": "end_turn",
                                              "player_index": 0})

    errors = table.messages_to("guest", MessageType.ERROR)
    assert errors, "a command to a closed room was accepted"
    assert "pokoju" in errors[0].payload["reason"].lower()


def test_a_room_can_be_opened_again_after_an_explicit_close(table):
    code = a_lobby_of_two(table)
    table.send("host", MessageType.CLOSE_ROOM, {})
    table.drain()

    again = table.open_room("fresh", "Norbert")

    assert again and again != code
    assert len(table.hub.rooms) == 1


def test_the_server_itself_is_untouched_by_closing_a_room(table):
    """A room is not the process.  Closing one leaves the hub serving.

    Recorded as a test because the report that started this stage described
    restarting the whole deployment to get a new room, and the fix must not
    quietly agree with that model.
    """
    code = a_lobby_of_two(table)
    table.send("host", MessageType.CLOSE_ROOM, {})

    assert table.hub.connections, "the sockets are still open"
    assert table.hub.identities, "the server still knows who is connected"
    table.hello("newcomer", "Ala")
    assert table.open_room("newcomer2", "Zosia")


# ── the menu entry ───────────────────────────────────────────────────────────
def test_only_the_host_is_offered_the_close_entry(library):
    """The menu agrees with the server rather than guessing."""
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from netkit import Table as NetTable
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout

    net = NetTable(library)
    try:
        host, clients = net.playing("Kuba", "Ola", "Norbert")
        labels = {}
        for service in [host, *clients]:
            app = App(Layout(), headless=True, size=(1920, 1080))
            screen = GameScreen(app, service.session, service=service)
            app.push(screen)
            labels[service.is_host] = [l for _, l in screen._pause_entries()]
        assert "Zamknij pokój" in labels[True]
        assert "Zamknij pokój" not in labels[False]
    finally:
        net.close()
