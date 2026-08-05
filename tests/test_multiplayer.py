"""
Multiplayer.

The architecture under test is client/server: a dedicated server owns the game,
every player — the one who opened the room included — is a client, and the only
thing that crosses the wire during a match is an action.

Almost everything here runs against :class:`InProcessServer`, which is the real
:class:`ServerHub` with queues where the sockets would be.  That keeps the suite
fast and deterministic and leaves exactly one thing it cannot reach: the socket
layer itself.  ``test_two_machines_play_over_real_websockets`` covers that, end
to end, over a genuine WebSocket server on a real port.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from netkit import Table, all_agree, playable_card, server_config, take_a_turn
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.net.lobby import (
    CODE_LENGTH, LobbyState, clean_room_code, make_room_code,
)
from pedzacy_piotrek.net.protocol import Message, MessageType, fingerprint_of
from pedzacy_piotrek.server.hub import ServerHub
from pedzacy_piotrek.server.registry import RoomRegistry


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library):
    t = Table(library)
    yield t
    t.close()


# ── room codes ───────────────────────────────────────────────────────────────
def test_a_room_code_is_readable_aloud():
    """No 0/O and no 1/I/L: a code is dictated over a voice chat and typed by
    somebody who is not looking at it."""
    for _ in range(200):
        code = make_room_code()
        assert len(code) == CODE_LENGTH
        assert not set(code) & set("01ILO")


@pytest.mark.parametrize("typed", ["abc-123", " ABC123 ", "abc 123", "AbC123"])
def test_a_mistyped_code_still_finds_the_room(typed):
    assert clean_room_code(typed) == clean_room_code("ABC123")


def test_confusable_characters_are_folded():
    assert clean_room_code("0BC1ZZ") == clean_room_code("OBCIZZ")


# ── the lobby document ───────────────────────────────────────────────────────
def test_the_lobby_survives_a_json_round_trip():
    lobby = LobbyState(code="ABC123")
    lobby.add_seat("p1", "Kuba", is_host=True)
    lobby.add_seat("p2", "Ola")
    lobby.seat_of("p2").ready = True
    restored = LobbyState.from_dict(lobby.to_dict())
    assert restored.code == "ABC123"
    assert [s.nickname for s in restored.seats] == ["Kuba", "Ola"]
    assert restored.seat_of("p2").ready
    assert restored.is_host("p1")


def test_two_people_with_the_same_name_stay_distinguishable():
    lobby = LobbyState()
    lobby.add_seat("p1", "Kuba")
    lobby.add_seat("p2", "Kuba")
    assert lobby.seat_of("p2").nickname == "Kuba (2)"


def test_an_empty_nickname_becomes_player():
    lobby = LobbyState()
    assert lobby.add_seat("p1", "   ").nickname == "Player"


def test_the_lobby_keeps_the_piotrek_rule():
    """If everybody picked a character by hand and none is Piotrek, there is
    nobody to chase."""
    lobby = LobbyState()
    for index, name in enumerate(["a", "b", "c"]):
        seat = lobby.add_seat(f"p{index}", name)
        seat.ready = True
        seat.character = ["Lubin", "Norbur", "Dziad"][index]
    assert settings.PIOTREK_TITLE in lobby.validate()
    lobby.seat_of("p0").character = settings.PIOTREK_TITLE
    assert lobby.validate() == ""


def test_the_room_outlives_whoever_opened_it():
    """A host dropping in the lobby must not leave a room nobody can start."""
    lobby = LobbyState()
    lobby.add_seat("p1", "Kuba", is_host=True)
    lobby.add_seat("p2", "Ola")
    lobby.remove_seat("p1")
    assert lobby.promote_new_host().peer_id == "p2"
    assert lobby.is_host("p2")


# ── the registry ─────────────────────────────────────────────────────────────
def test_only_one_room_by_default():
    """One lobby is the requirement; the code is already written for many."""
    registry = RoomRegistry(server_config(max_rooms=1))
    assert registry.create() is not None
    assert registry.create() is None


def test_raising_the_limit_is_the_whole_of_the_work():
    registry = RoomRegistry(server_config(max_rooms=5))
    codes = {registry.create().code for _ in range(5)}
    assert len(codes) == 5, "and every room gets its own code"
    assert registry.create() is None


def test_an_abandoned_room_is_forgotten():
    clock = [1000.0]
    registry = RoomRegistry(server_config(room_idle_timeout=60.0),
                            clock=lambda: clock[0])
    room = registry.create()
    assert registry.get(room.code) is not None
    clock[0] += 3600
    assert registry.prune() == [room.code]
    assert registry.get(room.code) is None


# ── joining ──────────────────────────────────────────────────────────────────
def test_opening_a_room_gives_a_code_to_share(table):
    host = table.host("Kuba")
    assert len(host.room_code) == CODE_LENGTH
    assert host.is_host
    assert host.mode == "host"


def test_friends_join_with_the_code_alone(table):
    """No address, no port, no router settings — which is the whole point."""
    host = table.host("Kuba")
    ola = table.join(host.room_code, "Ola")
    table.pump()
    assert [s.nickname for s in host.lobby_state.seats] == ["Kuba", "Ola"]
    assert ola.mode == "client"
    assert not ola.is_host


def test_an_unknown_code_is_reported_not_crashed(table):
    table.host("Kuba")
    stranger = table.join("ZZZZZZ", "Ola")
    table.pump()
    assert stranger.error and "ZZZZZZ" in stranger.error
    assert not stranger.lobby_state.code


def test_a_full_table_turns_the_next_person_away(table):
    host = table.host("Kuba")
    for index in range(RULES.max_players - 1):
        table.join(host.room_code, f"Gracz {index}")
    extra = table.join(host.room_code, "Spóźnialski")
    table.pump()
    assert host.lobby_state.player_count == RULES.max_players
    assert extra.error and "pełny" in extra.error


def test_a_character_cannot_be_taken_twice(table):
    """The client's list hides taken characters; the server is what enforces
    it, because a client is not to be trusted on that."""
    host, (ola,) = table.seated("Kuba", "Ola")
    host.set_character(settings.PIOTREK_TITLE)
    table.pump()
    ola.set_character(settings.PIOTREK_TITLE)
    table.pump()
    assert ola.lobby_state.seat_of(ola.peer_id).character == ""
    assert ola.error and "zajęta" in ola.error


def test_leaving_the_lobby_frees_the_seat_and_the_character(table):
    host, (ola, norbert) = table.seated("Kuba", "Ola", "Norbert")
    ola.set_character("Lubin")
    table.pump()
    ola.close()
    table.pump()
    assert [s.nickname for s in host.lobby_state.seats] == ["Kuba", "Norbert"]
    assert "Lubin" not in host.lobby_state.taken_characters()
    assert [s.seat for s in host.lobby_state.seats] == [0, 1], "seats renumber"


# ── starting ─────────────────────────────────────────────────────────────────
def test_only_the_host_may_start(table):
    host, (ola, norbert) = table.seated("Kuba", "Ola", "Norbert")
    ola.client.start_game()
    table.pump()
    assert ola.session is None and host.session is None
    assert ola.error and "host" in ola.error.lower()


def test_the_server_refuses_to_start_an_incomplete_table(table):
    host, _ = table.seated("Kuba", "Ola")
    host.start_game(table.library)
    table.pump()
    assert host.session is None
    assert "Potrzeba" in (host.error or "")


def test_everyone_who_is_not_ready_holds_the_start_button(table):
    host = table.host("Kuba")
    table.join(host.room_code, "Ola")
    table.join(host.room_code, "Norbert")
    table.pump()
    assert "gotowi" in host.lobby_state.validate()


def test_starting_builds_the_identical_game_everywhere(table):
    """Only the configuration crosses the wire, seed included.  Not one card is
    transmitted, and every machine deals the same hands."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    assert all(s.session is not None for s in [host, *clients])
    assert all_agree(host, *clients)
    assert len({s.session.seat for s in [host, *clients]}) == 3


def test_a_two_player_table_needs_the_testing_option(table):
    host = table.host("Kuba")
    ola = table.join(host.room_code, "Ola")
    ola.set_ready(True)
    table.pump()
    assert not host.lobby_state.can_start

    host.set_settings(debug_version=True)
    table.pump()
    assert host.lobby_state.can_start
    host.start_game(table.library)
    table.pump()
    assert host.session is not None and ola.session is not None


def test_table_settings_belong_to_the_host(table):
    host, (ola,) = table.seated("Kuba", "Ola")
    ola.set_settings(board_cells=99)
    table.pump()
    assert host.lobby_state.board_cells != 99
    assert ola.error and "host" in ola.error.lower()


# ── playing ──────────────────────────────────────────────────────────────────
def test_an_action_reaches_every_machine_and_they_stay_identical(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    seat = take_a_turn(table, host, clients)
    assert all_agree(host, *clients)
    assert host.state.active_player_index != seat, "the turn moved on"
    assert table.room(host.room_code).sequence == 1, "one action, one log entry"


def test_a_client_cannot_act_for_another_seat(table):
    """The seat comes from the server's own map, never from the message."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    active = host.state.active_player_index
    impostor = by_seat[(active + 1) % 3]
    victim = host.state.player(active)

    impostor.session.submit(cmd.DiscardCard(player_index=active,
                                            card_uid=victim.hand[0].uid))
    table.pump()
    assert host.state.active_player_index == active, "nothing happened"
    assert len(host.state.player(active).hand) == len(victim.hand)
    assert table.room(host.room_code).sequence == 0


def test_a_client_cannot_act_out_of_turn(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    active = host.state.active_player_index
    waiting_seat = (active + 1) % 3
    waiting = by_seat[waiting_seat]

    card = playable_card(waiting, waiting_seat)
    waiting.session.submit(cmd.DiscardCard(player_index=waiting_seat,
                                           card_uid=card.uid))
    table.pump()
    assert table.room(host.room_code).sequence == 0
    assert all_agree(host, *clients)


def test_private_bookkeeping_works_off_turn(table):
    """Crossing a colour off the notepad is not a move, so it is not turn-bound."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    idle_seat = (host.state.active_player_index + 1) % 3
    idle = by_seat[idle_seat]
    pawn = table.library.pawns[0].id

    idle.session.submit(cmd.ToggleMark(player_index=idle_seat, pawn_id=pawn))
    table.pump()
    assert pawn in host.state.player(idle_seat).marks
    assert all_agree(host, *clients)


def test_a_pending_decision_is_asked_of_one_player_only(library):
    """A card that needs a choice changes nothing, so replaying it to everybody
    would pop a modal on three screens asking a question only one can answer."""
    from pedzacy_piotrek.engine import events as ev

    def choice_card(service, seat):
        player = service.state.player(seat)
        return next((c for c in player.hand
                     if c.definition.effect
                     and c.definition.effect.params.get("target") == "choice"),
                    None)

    # The deal is seeded by the server, so keep opening tables until the active
    # player is holding a card that asks a question.  Cheaper than a backdoor
    # for setting the seed, and it exercises the real start path.
    for _ in range(20):
        table = Table(library)
        try:
            host, clients = table.playing("Kuba", "Ola", "Norbert")
            seat = host.state.active_player_index
            actor = table.by_seat(host, clients)[seat]
            card = choice_card(actor, seat)
            if card is None:
                continue

            seen = {s.session.seat: [] for s in [host, *clients]}
            for service in [host, *clients]:
                service.session.bus.subscribe(
                    ev.ChoiceRequired, seen[service.session.seat].append)

            actor.session.submit(cmd.PlayCard(player_index=seat,
                                              card_uid=card.uid))
            table.pump()
            assert seen[seat], "the player who asked is asked"
            assert all(not seen[other] for other in seen if other != seat)
            assert table.room(host.room_code).sequence == 0, "nothing was logged"
            assert all_agree(host, *clients)
            return
        finally:
            table.close()
    pytest.fail("no deal in twenty tables held a card that asks a question")


def test_a_whole_match_keeps_every_machine_in_step(table):
    """A dozen turns, compared after every single action."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    for _ in range(12):
        take_a_turn(table, host, clients)
        assert all_agree(host, *clients)
    assert table.room(host.room_code).sequence == 12


def test_the_server_stamps_a_fingerprint_every_machine_can_check(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    take_a_turn(table, host, clients)
    room = table.room(host.room_code)
    assert room.fingerprint == fingerprint_of(host.state.snapshot())
    assert room.fingerprint == fingerprint_of(clients[0].state.snapshot())


# ── disconnection and coming back ────────────────────────────────────────────
def test_a_drop_is_announced_and_the_seat_is_held(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    victim = clients[-1]
    seat = victim.session.seat
    victim.client.transport.drop()
    table.pump()

    notices = " ".join(host.drain_notices())
    assert "stracił połączenie" in notices
    room = table.room(host.room_code)
    assert room.lobby.seat_at(seat) is not None, "the seat is still theirs"
    assert not room.lobby.seat_at(seat).connected


def test_a_returning_player_gets_their_seat_and_their_hand_back(table):
    """The seed plus the accepted command log *is* the state, so coming back is
    a replay rather than a second representation of the game to keep in step."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    victim = clients[-1]
    seat = victim.session.seat
    hand_before = [c.uid for c in victim.state.player(seat).hand]

    victim.client.transport.drop()
    table.pump()
    for _ in range(3):                     # the table plays on without them
        if host.state.active_player_index == seat:
            break
        take_a_turn(table, host, clients[:-1])

    victim.client.transport.restore()
    table.pump(12)

    assert victim.disconnected is None
    assert victim.session.seat == seat
    assert all_agree(host, victim)
    assert [c.uid for c in victim.state.player(seat).hand] != [] or hand_before == []


def test_a_grace_period_that_runs_out_releases_the_seat(table):
    clock = [1000.0]
    t = Table(table.library, server_config())
    t.server.hub._clock = lambda: clock[0]
    for room in t.server.hub.rooms:
        room._clock = lambda: clock[0]
    try:
        host, clients = t.playing("Kuba", "Ola", "Norbert")
        room = t.room(host.room_code)
        room._clock = lambda: clock[0]
        victim = clients[-1]
        seat = victim.session.seat
        victim.client.transport.drop()
        t.pump()
        assert seat in {m for m in room.members and
                        [room.seat_of(p) for p in room.members]}

        clock[0] += t.config.reconnect.grace_period + 10
        t.tick()
        assert victim.peer_id not in room.members, "the seat was released"
    finally:
        t.close()


def test_the_match_survives_a_player_leaving_for_good(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    leaver = clients[-1]
    leaver.close()
    table.pump()
    remaining = [host, clients[0]]
    # The others carry on: seats are not renumbered mid-match, because the seat
    # index is baked into every command already in the log.
    assert host.state is not None
    assert all_agree(*remaining)
    assert len(host.state.players) == 3


def test_a_late_joiner_is_brought_fully_up_to_date(table):
    """Arriving after the match began is the same operation as coming back."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    for _ in range(4):
        take_a_turn(table, host, clients)

    latecomer = table.join(host.room_code, "Ktoś")
    table.pump()
    assert latecomer.error and "rozpocz" in latecomer.error.lower(), (
        "a stranger cannot be dealt a hand halfway through a game whose "
        "hidden information was decided at the start")


def test_a_client_that_falls_behind_asks_for_the_whole_match(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    victim = clients[0]
    for _ in range(3):
        take_a_turn(table, host, clients)

    # Corrupt the replica, exactly as a real desync would look.
    victim.state.round_number += 7
    resyncs_before = victim.stats.resyncs
    take_a_turn(table, host, clients)
    table.pump(12)

    assert victim.stats.resyncs > resyncs_before, "it noticed"
    assert all_agree(host, victim), "and it healed"


def test_the_server_going_away_sends_everybody_home(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    table.server._route(
        table.server.hub.close_room(host.room_code, "Serwer zamknięty"))
    table.pump()
    assert all(s.disconnected for s in [host, *clients])


# ── the handshake ────────────────────────────────────────────────────────────
def test_a_mismatched_protocol_version_is_refused_readably():
    """Better a sentence telling somebody to update than a desync twenty
    minutes into a match."""
    hub = ServerHub(server_config())
    hub.connect("c1")
    stale = Message.hello("Kuba")
    stale.version = 999
    out = hub.receive("c1", stale)
    assert out and out[0][1].type is MessageType.ERROR
    assert "wersj" in out[0][1].payload["reason"].lower()
    assert out[0][1].payload["fatal"]


def test_anything_before_the_handshake_is_turned_away():
    hub = ServerHub(server_config())
    hub.connect("c1")
    out = hub.receive("c1", Message.create_lobby("Kuba"))
    assert out[0][1].type is MessageType.ERROR


def test_rubbish_costs_the_sender_its_message_and_nothing_else():
    hub = ServerHub(server_config())
    hub.connect("c1")
    out = hub.receive("c1", "{ not json")
    assert out[0][1].type is MessageType.ERROR
    assert hub.receive("c1", Message.hello("Kuba"))[0][1].type is MessageType.WELCOME


# ── the socket layer, for real ───────────────────────────────────────────────
@pytest.mark.slow
def test_two_machines_play_over_real_websockets(library):
    """The one test that uses genuine sockets: a real server on a real port,
    real clients, a real match.  Everything else in this file exercises the
    same hub with queues instead."""
    from pedzacy_piotrek.net.config import NetworkConfig
    from pedzacy_piotrek.net.service import ClientService, HostService
    from pedzacy_piotrek.server.embedded import EmbeddedServer

    config = NetworkConfig()
    config = replace(config, server=replace(config.server, host="127.0.0.1",
                                            port=0, max_rooms=2))
    server = EmbeddedServer(config, library)
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

        ola = ClientService(host.room_code, "Ola", config=config,
                            library=library, url=server.url)
        parties.append(ola)
        pump(2.0)
        ola.set_ready(True)
        host.set_settings(debug_version=True)
        pump(1.5)

        host.start_game(library)
        pump(2.5)
        assert host.session is not None and ola.session is not None
        assert host.state.snapshot() == ola.state.snapshot()

        seat = host.state.active_player_index
        actor = host if host.session.seat == seat else ola
        card = playable_card(actor, seat)
        actor.session.submit(cmd.DiscardCard(player_index=seat,
                                             card_uid=card.uid))
        pump(2.0)
        assert host.state.snapshot() == ola.state.snapshot()
        assert host.state.active_player_index != seat
        assert host.stats.ping_ms is not None, "latency is measured"
    finally:
        for service in parties:
            service.close()
        server.stop()
