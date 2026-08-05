"""
The bottom of the networking stack: configuration, protocol, transports,
sessions.

These are the pieces every other multiplayer test stands on.  If a message
cannot survive a round trip, or a configuration file cannot survive a typo,
nothing above it is worth testing.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.config import (
    DEFAULT_PORT, NetworkConfig, ReconnectPolicy, normalise_url,
)
from pedzacy_piotrek.net.protocol import (
    Message, MessageType, ProtocolError, fingerprint_of,
)
from pedzacy_piotrek.net.session import LocalSession, NetworkSession
from pedzacy_piotrek.net.transport import (
    ConnectionState, LoopbackTransport, NullTransport,
)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def config() -> SessionConfig:
    return SessionConfig(num_players=3, board_cells=14, seed=99).normalised()


# ── configuration ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("typed, expected", [
    ("example.com", f"ws://example.com:{DEFAULT_PORT}"),
    ("example.com:9000", "ws://example.com:9000"),
    ("ws://example.com:9000", "ws://example.com:9000"),
    ("wss://piotrek.example.com", "wss://piotrek.example.com"),
    ("https://piotrek.example.com", "wss://piotrek.example.com"),
    ("ws://example.com:9000/", "ws://example.com:9000"),
])
def test_whatever_the_player_types_becomes_a_usable_url(typed, expected):
    """People paste all of these and mean the same thing."""
    assert normalise_url(typed) == expected


def test_an_empty_address_stays_empty():
    assert normalise_url("") == ""


def test_a_broken_config_file_does_not_stop_the_game(tmp_path):
    """A configuration error must never be what keeps the game from opening."""
    broken = tmp_path / "network.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    config = NetworkConfig.load(broken, env={})
    assert config.server_url                    # fell back to the defaults
    assert config.reconnect.enabled


def test_unknown_settings_are_ignored_rather_than_fatal():
    config = NetworkConfig.from_dict({
        "server_url": "ws://a.example:1234",
        "something_from_a_newer_build": True,
        "reconnect": {"initial_delay": 0.25, "nonsense": 1},
    })
    assert config.server_url == "ws://a.example:1234"
    assert config.reconnect.initial_delay == 0.25


def test_the_hosting_platform_port_variable_is_honoured():
    """Every PaaS injects PORT; ignoring it binds the wrong one and answers
    nothing, which looks exactly like a broken deployment."""
    config = NetworkConfig().with_environment({"PORT": "8080"})
    assert config.server.port == 8080


def test_the_server_url_can_be_overridden_by_environment():
    config = NetworkConfig().with_environment(
        {"PIOTREK_SERVER_URL": "wss://gra.example.com"})
    assert config.server_url == "wss://gra.example.com"
    assert config.is_secure


def test_a_config_survives_a_round_trip_through_json():
    original = NetworkConfig(server_url="wss://x.example",
                             reconnect=ReconnectPolicy(initial_delay=2.0))
    assert NetworkConfig.from_dict(original.to_dict()).reconnect.initial_delay == 2.0


def test_reconnection_backs_off_but_stops_growing():
    policy = ReconnectPolicy(initial_delay=0.5, backoff=2.0, max_delay=4.0)
    delays = [policy.delay_for(n) for n in range(1, 8)]
    assert delays[0] == 0.5
    assert delays == sorted(delays), "backoff must never go down"
    assert max(delays) <= 4.0, "and must never exceed the ceiling"


# ── protocol ─────────────────────────────────────────────────────────────────
def test_a_message_survives_a_round_trip():
    original = Message.command({"kind": "draw_card", "player_index": 2}, 7)
    restored = Message.decode(original.encode())
    assert restored.type is MessageType.COMMAND
    assert restored.payload["command"]["player_index"] == 2
    assert restored.payload["seq"] == 7


def test_polish_text_survives_the_wire():
    restored = Message.decode(Message.error("Zażółć gęślą jaźń").encode())
    assert restored.payload["reason"] == "Zażółć gęślą jaźń"


@pytest.mark.parametrize("raw", ["not json at all", "[1,2,3]", '{"t":"nonsense"}'])
def test_rubbish_is_rejected_readably_not_fatally(raw):
    """A peer sending nonsense costs that peer its message, never the game."""
    with pytest.raises(ProtocolError):
        Message.decode(raw)


def test_the_room_and_sender_are_carried_when_set():
    message = Message.lobby_state({"code": "ABC123"}, room="ABC123")
    assert Message.decode(message.encode()).room == "ABC123"


def test_the_fingerprint_notices_a_difference(library, config):
    a = create_game(config, library)
    b = create_game(config, library)
    assert fingerprint_of(a.snapshot()) == fingerprint_of(b.snapshot())
    b.round_number += 1
    assert fingerprint_of(a.snapshot()) != fingerprint_of(b.snapshot())


# ── transports ───────────────────────────────────────────────────────────────
def test_a_loopback_pair_delivers_both_ways():
    left, right = LoopbackTransport.pair()
    left.send(Message.ping(1.0))
    assert [m.type for m in right.poll()] == [MessageType.PING]
    assert right.poll() == []


def test_a_dropped_transport_is_a_state_not_an_exception():
    """Failure has to be something the game can draw, not something it catches."""
    left, _ = LoopbackTransport.pair()
    left.drop("Połączenie zerwane")
    assert left.state is ConnectionState.CLOSED
    assert left.error == "Połączenie zerwane"
    left.send(Message.ping(0.0))          # must not raise
    assert left.poll() == []


def test_the_null_transport_swallows_everything():
    transport = NullTransport()
    transport.send(Message.ping(0.0))
    assert transport.poll() == []
    assert transport.connected


def test_reconnecting_still_counts_as_working():
    """The interface keeps the board up while the socket comes back."""
    assert ConnectionState.RECONNECTING.is_working
    assert not ConnectionState.RECONNECTING.is_live
    assert not ConnectionState.CLOSED.is_working


# ── sessions ─────────────────────────────────────────────────────────────────
def test_a_local_session_applies_and_publishes(library, config):
    state = create_game(config, library)
    session = LocalSession(state)
    seen = []
    session.bus.subscribe(ev.RoundChanged, seen.append)
    session.submit(cmd.SetRound(round_number=4))
    assert state.round_number == 4
    assert seen and seen[-1].round_number == 4
    assert [e.round_number for e in seen] == [2, 3, 4], (
        "every round crossed owes a hunter a chest card, so each is stepped")


def test_a_network_session_changes_nothing_until_the_server_says_so(library, config):
    """No optimistic prediction: a wrong guess would have to be rolled back, and
    rolling back a hand in this game means having shown what should stay
    hidden."""
    state = create_game(config, library)
    sent = []
    session = NetworkSession(state, sent.append)

    session.submit(cmd.SetRound(round_number=5))
    assert state.round_number == 1, "nothing may change locally"
    assert len(sent) == 1
    assert session.in_flight == 1

    session.apply_authoritative(cmd.SetRound(round_number=5))
    assert state.round_number == 5
    assert session.in_flight == 0


def test_an_authoritative_command_is_not_re_judged(library):
    """Re-checking somebody else's legal move against *this* machine's seat
    would reject everything they do and desync on the spot."""
    config = SessionConfig(num_players=3, board_cells=14, seed=5,
                           edit_mode=False, local_seat=0).normalised()
    state = create_game(config, library)
    session = NetworkSession(state, lambda command: None, seat=0)
    other = 1 if state.active_player_index != 1 else 2
    state.active_player_index = other

    player = state.player(other)
    events = session.apply_authoritative(
        cmd.RenamePlayer(player_index=other, name="Zdalny"))
    assert not any(isinstance(e, ev.ActionRejected) for e in events)
    assert player.name == "Zdalny"


def test_a_rejection_reaches_the_interface_as_an_event(library, config):
    state = create_game(config, library)
    session = NetworkSession(state, lambda command: None)
    seen = []
    session.bus.subscribe(ev.ActionRejected, seen.append)
    session.submit(cmd.SetRound(round_number=2))
    session.reject("To nie twoja tura", "set_round")
    assert seen and seen[0].reason == "To nie twoja tura"
    assert session.in_flight == 0
