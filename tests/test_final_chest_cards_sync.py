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


# ── Kingmaker ────────────────────────────────────────────────────────────────
#
# The role swap is the OTHER half of Gamechanger, and across a server it asks a
# different question from Alter Ego's.  Alter Ego's question was "does the
# secret stay secret"; this one is "does every machine agree who Piotrek IS".
#
# The two are not the same fact and they are protected differently.  WHO
# Piotrek is has always been public — the turn order announces him every round
# — so the role moving is ordinary shared state and travels in the command
# every replica replays.  The COLOUR is not, so it goes on riding the same
# private road it always did, and the fact that the role moved underneath it
# must not shake it loose.
def hunter_seat_on_turn(table, host, clients):
    """Walk the table on until an actual hunter is holding the turn.

    Kingmaker is played by a hunter, and the round opens on Piotrek — so a
    test that dealt the card to whoever was active would be dealing it to the
    wrong man on the first turn of the match.
    """
    while host.state.active_player_index == host.state.piotrek_seat:
        take_a_turn(table, host, clients)
    return host.state.active_player_index


def start_kingmaker(table, host, clients):
    """Deal Gamechanger to the hunter on turn and play it.

    Returns ``(old_seat, new_seat, actor)`` — the seat that was Piotrek, the
    seat that played the card, and the service that owns the latter.
    """
    room = table.room(host.room_code)
    parties = [host, *clients]
    old_seat = room.state.piotrek_seat
    new_seat = hunter_seat_on_turn(table, host, clients)
    uid = deal_chest(room, parties, new_seat, "Gamechanger")
    actor = table.by_seat(host, clients)[new_seat]
    actor.session.submit(cmd.PlayCard(player_index=new_seat, card_uid=uid))
    table.pump()
    return old_seat, new_seat, actor


def test_a_hunters_gamechanger_is_kingmaker_on_every_machine(library):
    """The transformation is data, so it happens identically everywhere."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    seat = hunter_seat_on_turn(table, host, clients)
    uid = deal_chest(room, parties, seat, "Gamechanger")
    for state in everywhere(room, parties):
        card = state.player(seat).card_by_uid(uid)
        assert card is not None and card.title == "Kingmaker"
    table.close()


def test_the_role_moves_on_every_machine(library):
    """The heart of it: one command, and five replicas agree who Piotrek is."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    old_seat, new_seat, _ = start_kingmaker(table, host, clients)

    assert old_seat != new_seat
    for state in everywhere(room, parties):
        assert state.piotrek_seat == new_seat
        assert state.player(new_seat).is_piotrek
        assert not state.player(old_seat).is_piotrek
    assert all_agree(host, *clients)
    table.close()


def test_the_role_swap_needs_no_command_of_its_own(library):
    """It replays from the ``play_card`` that caused it, like every mechanic.

    The reveal that follows is the authority's, exactly as under Alter Ego —
    two commands, both already in the game before this card had a rule.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    room = table.room(host.room_code)

    hunter_seat_on_turn(table, host, clients)
    before = len(room.command_log)
    start_kingmaker(table, host, clients)

    kinds = [e["kind"] for e in room.command_log[before:]]
    assert kinds == ["play_card", "reveal_identity"]
    table.close()


def test_the_played_card_carries_no_colour(library):
    """N72 again, and it matters more here: the handler moves the secret.

    It moves it WITHOUT READING IT, which is the whole design — on a replica
    the swap moves ``None`` onto ``None``.  If the colour had travelled in the
    command instead, every client would learn it from the log, and so would
    anybody who reconnected months later.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    room = table.room(host.room_code)
    colour = room.state.piotrek_pawn
    assert colour, "the server knows the colour"

    hunter_seat_on_turn(table, host, clients)
    before = len(room.command_log)
    start_kingmaker(table, host, clients)

    played = room.command_log[before]
    assert played["kind"] == "play_card"
    assert colour not in repr(played), "the colour is not in the command"
    table.close()


def test_only_the_new_piotrek_is_asked_for_a_colour(library):
    """The private message goes to the man who now holds the role.

    Asking the OUTGOING Piotrek is the mistake this guards against, and it is
    the easy one to make: he is the one the question used to belong to.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    colour = room.state.piotrek_pawn

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)

    asked = [s for s in parties if s.identity_request]
    assert asked == [actor], "exactly one machine was asked"
    assert actor.session.seat == new_seat, "and it is the NEW Piotrek's"
    offered = {p["id"] for p in actor.identity_request}
    assert colour not in offered, "not the colour just given up"
    assert len(offered) == len(room.state.library.pawns) - 1
    table.close()


def test_the_old_colour_is_not_inherited_across_the_wire(library):
    """Between the exchange and the answer NOBODY holds an identity."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)

    for state in everywhere(room, parties):
        assert state.piotrek_pawn is None
        assert state.player(old_seat).secret_pawn is None
        assert state.player(new_seat).secret_pawn is None
    table.close()


def test_the_new_piotreks_colour_never_travels(library):
    """The second secret is as private as the first, and belongs to a new seat."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    before = len(room.command_log)
    actor.choose_identity(chosen)
    table.pump()

    assert room.state.piotrek_pawn == chosen, "the authority knows"
    assert actor.state.piotrek_pawn == chosen, "and so does its owner"
    kinds = [e["kind"] for e in room.command_log[before:]]
    assert kinds == ["finish_identity_swap"]
    assert chosen not in repr(room.command_log[before:])

    for service in [s for s in parties if s is not actor]:
        assert service.state.piotrek_pawn is None, "and nobody else does"
    assert all_agree(host, *clients)
    table.close()


def test_the_outgoing_piotrek_is_told_nothing_extra(library):
    """He knew a colour a moment ago.  He must not still be holding it.

    The seat he now occupies is an ordinary hunter's, and his replica has to
    look exactly like the other hunters' — otherwise the man who was Piotrek
    would spend the rest of the match knowing something they do not.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)
    outgoing = table.by_seat(host, clients)[old_seat]
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    assert outgoing is not actor
    assert outgoing.state.piotrek_pawn is None
    assert all(p.secret_pawn is None for p in outgoing.state.players)
    table.close()


def test_the_table_is_stopped_for_the_whole_exchange(library):
    """The pause holds against a client that simply does not draw the overlay."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)
    for state in everywhere(room, parties):
        assert state.awaiting_identity, "everybody is paused"

    before = len(room.command_log)
    seat = room.state.active_player_index
    assert room.state.authorise_remote(cmd.EndTurn(player_index=seat), seat)
    assert len(room.command_log) == before, "and nothing got through"

    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()
    for state in everywhere(room, parties):
        assert not state.awaiting_identity, "and everybody resumed together"

    take_a_turn(table, host, clients)
    assert all_agree(host, *clients)
    table.close()


def test_the_fingerprint_follows_the_role(library):
    """Two machines that disagreed about the role used to agree about the hash.

    ``piotrek_name`` is a character TITLE and the titles in play are the same
    after an exchange as before it, so before stage 45 nothing in the snapshot
    would have noticed the role sitting on a different seat.  It notices now,
    and — this is the other half — the secret still does not move it.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn
    assert all_agree(host, *clients)

    before = room.state.snapshot()["piotrek_seat"]
    old_seat, new_seat, actor = start_kingmaker(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    assert before == old_seat
    assert room.state.snapshot()["piotrek_seat"] == new_seat
    assert room.state.snapshot() == host.state.snapshot(), \
        "the server knows two colours and the host none, and they still match"
    assert all_agree(host, *clients)
    table.close()


def test_a_reconnecting_player_replays_the_exchange(library):
    """The log alone has to reach the same table — and teach nothing extra."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    log = [e["kind"] for e in room.command_log]
    assert "play_card" in log and "reveal_identity" in log \
        and "finish_identity_swap" in log
    assert chosen not in repr(room.command_log)

    replica = [s for s in parties if s is not actor][0]
    assert replica.state.piotrek_seat == new_seat, "the role moved for him too"
    assert replica.state.eliminated_pawns == [old]
    assert replica.state.piotrek_pawn is None
    assert not replica.state.awaiting_identity
    table.close()


def test_turn_permissions_follow_the_role(library):
    """Point 10 over the wire: the cadence is recomputed from the new owner."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    old = room.state.piotrek_pawn

    old_seat, new_seat, actor = start_kingmaker(table, host, clients)
    chosen = next(p["id"] for p in actor.identity_request if p["id"] != old)
    actor.choose_identity(chosen)
    table.pump()

    # Everybody computes the same round from the same new ownership.
    orders = [state.seat_order(state.round_number)
              for state in everywhere(room, parties)]
    assert all(order == orders[0] for order in orders)

    # And play carries on through the ordinary path for several turns.
    for _ in range(6):
        take_a_turn(table, host, clients)
    assert all_agree(host, *clients)
    table.close()
