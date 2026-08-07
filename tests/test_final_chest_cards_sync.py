"""
The stage 28 Chest cards across a real server.

Gejtos is the easy half: it is an ordinary ``PlayCard`` whose answers ride in
``choices``, and the only thing worth proving is that the towers it builds come
out identical everywhere.

ALTER EGO IS THE HARD HALF, and it is the most delicate thing in the project.
The hidden colour is the one fact the server has and the clients do not (N71,
N73), and this card makes it change hands mid-match.  Everything below is
really one question asked from several angles: does the secret stay secret, and
does every replica still agree about everything else?

The specific way it could go wrong: a handler that read the colour would build
a different plan on a machine that knows it than on one that does not, and the
table would split on the one card that must not split it.  So the card raises a
colourless flag, the AUTHORITY answers it, and the answer travels as an
ordinary logged command — the same road an elimination takes.
"""

from __future__ import annotations

import pytest

from netkit import Table, all_agree, take_a_turn
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def everywhere(room, services):
    return [room.state, *[s.state for s in services if s.state is not None]]


def straight(table, *names):
    """A started match on a board with no widened rows.

    Gejtos asks a question per widened neighbour; these tests are about what
    reaches the other machines, not about answering prompts.
    """
    host, clients = table.seated(*names)
    host.set_settings(mod_round_first=10_000, double_percent=0)
    table.pump()
    host.start_game(table.library)
    table.pump()
    table.choose_identity(host, clients)
    return host, clients


def pose(room, services, positions):
    for state in everywhere(room, services):
        for pawn in state.library.pawns:
            state.board.remove_pawn(pawn.id)
        for pawn_id, position in positions:
            tile = state.board.position(position).tiles[0]
            state.board.place_pawn(pawn_id, tile.index)
        state._sync_token_positions()


def deal_chest(room, services, seat: int, title: str) -> int:
    """The same physical card into the same hand on every machine."""
    uid = None
    for state in everywhere(room, services):
        deck = state.decks[settings.DECK_CHEST]
        card = next(c for c in deck.draw_pile if c.title == title)
        deck.draw_pile.remove(card)
        state._after_draw(state.player(seat), card)
        state.player(seat).add_card(card)
        uid = card.uid
    return uid


def towers(state):
    return {t.index: list(t.stack) for t in state.board.tiles if t.stack}


# ── Gejtos ───────────────────────────────────────────────────────────────────
def test_gejtos_builds_the_same_tower_everywhere(library):
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    anchor, behind, ahead = "zielony", "niebieski", "różowy"
    pose(room, parties, [(anchor, 5), (behind, 4), (ahead, 6)])

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Gejtos")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(
        player_index=seat, card_uid=uid,
        choices={"option": "gather", "pawn": anchor}))
    table.pump()

    expected = towers(room.state)
    assert len(expected) == 1, "everybody ended on one field"
    for service in parties:
        assert towers(service.state) == expected
    assert all_agree(host, *clients)
    table.close()


def test_gejtos_needs_no_command_of_its_own(library):
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    pose(room, parties, [("zielony", 5), ("różowy", 6)])

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Gejtos")
    before = len(room.command_log)
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(
        player_index=seat, card_uid=uid,
        choices={"option": "scatter", "pawn": "zielony"}))
    table.pump()

    assert [e["kind"] for e in room.command_log[before:]] == ["play_card"]
    assert all_agree(host, *clients)
    table.close()


# ── Alter Ego ────────────────────────────────────────────────────────────────
def piotrek_service(table, host, clients):
    """Whichever machine holds the Piotrek seat."""
    seat = host.state.piotrek_seat
    return seat, table.by_seat(host, clients)[seat]


def start_swap(table, host, clients):
    """Deal Alter Ego to Piotrek and play it.  Returns (seat, service, uid)."""
    room = table.room(host.room_code)
    parties = [host, *clients]
    seat, actor = piotrek_service(table, host, clients)
    uid = deal_chest(room, parties, seat, "Gamechanger")
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid))
    table.pump()
    return seat, actor, uid


def test_the_card_itself_carries_no_colour(library):
    """The heart of it: the played command names nobody.

    If the colour travelled in the command, every client would learn the secret
    from the log — and a reconnecting player would replay it out of the command
    history months later.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    room = table.room(host.room_code)
    colour = room.state.piotrek_pawn
    assert colour, "the server knows the colour"

    before = len(room.command_log)
    start_swap(table, host, clients)

    played = room.command_log[before]
    assert played["kind"] == "play_card"
    assert colour not in repr(played), "the colour is not in the command"
    table.close()


def test_the_reveal_reaches_every_machine_as_a_command(library):
    """The authority answers; the answer is logged and broadcast like any other."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    colour = room.state.piotrek_pawn

    before = len(room.command_log)
    start_swap(table, host, clients)

    kinds = [e["kind"] for e in room.command_log[before:]]
    assert kinds == ["play_card", "reveal_identity"]
    for state in everywhere(room, parties):
        assert state.eliminated_pawns == [colour], "everybody learned it"
        assert state.identity_swap == state.SWAP_CHOOSING
    assert all_agree(host, *clients)
    table.close()


def test_only_piotrek_is_asked_for_the_new_colour(library):
    """The same private message the opening uses — to one peer, not the table."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    colour = room.state.piotrek_pawn

    seat, actor, _ = start_swap(table, host, clients)

    asked = [s for s in parties if s.identity_request]
    assert asked == [actor], "exactly one machine was asked"
    offered = {p["id"] for p in actor.identity_request}
    assert colour not in offered, "and not the colour he just gave up"
    assert len(offered) == len(room.state.library.pawns) - 1
    table.close()


def test_the_new_colour_never_travels(library):
    """The second secret is as private as the first.

    The resume is a command so that everybody leaves the pause together, and it
    is empty for exactly this reason.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    seat, actor, _ = start_swap(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    before = len(room.command_log)
    actor.choose_identity(chosen)
    table.pump()

    assert room.state.piotrek_pawn == chosen, "the authority knows"
    kinds = [e["kind"] for e in room.command_log[before:]]
    assert kinds == ["finish_identity_swap"]
    assert chosen not in repr(room.command_log[before:]), "and nobody else does"

    blind = [s for s in parties if s is not actor]
    for service in blind:
        assert service.state.piotrek_pawn is None
    assert all_agree(host, *clients)
    table.close()


def test_the_table_resumes_together(library):
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    seat, actor, _ = start_swap(table, host, clients)
    for state in everywhere(room, parties):
        assert state.awaiting_identity, "everybody is paused"

    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    for state in everywhere(room, parties):
        assert not state.awaiting_identity, "and everybody resumed"
    # And play carries on through the ordinary path.
    take_a_turn(table, host, clients)
    assert all_agree(host, *clients)
    table.close()


def test_nobody_may_move_while_piotrek_chooses(library):
    """The pause holds against a client that simply does not draw the overlay."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    room = table.room(host.room_code)

    start_swap(table, host, clients)
    before = len(room.command_log)

    seat = room.state.active_player_index
    problem = room.state.authorise_remote(cmd.EndTurn(player_index=seat), seat)
    assert problem is not None
    assert len(room.command_log) == before
    table.close()


def test_the_secret_does_not_move_the_fingerprint(library):
    """The whole design rests on this, and a swap must not break it.

    The server knows two colours over the course of the match and the clients
    know none, yet every replica has to keep the same fingerprint — otherwise
    the swap would put the whole table into a resync loop.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn
    assert all_agree(host, *clients)

    seat, actor, _ = start_swap(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    assert room.state.snapshot() == host.state.snapshot()
    assert all_agree(host, *clients)
    table.close()


def test_a_reconnecting_player_replays_the_swap(library):
    """The reveal is in the log, so catching up produces the same notepad.

    And the two SECRETS are not in the log, so catching up teaches a latecomer
    nothing it should not know.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    seat, actor, _ = start_swap(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    log = [e["kind"] for e in room.command_log]
    assert "reveal_identity" in log and "finish_identity_swap" in log
    assert chosen not in repr(room.command_log)

    # A replica rebuilt from the log alone reaches the same public table.
    replica = [s for s in parties if s is not actor][0]
    assert replica.state.eliminated_pawns == [old]
    assert not replica.state.awaiting_identity
    assert replica.state.piotrek_pawn is None
    table.close()
