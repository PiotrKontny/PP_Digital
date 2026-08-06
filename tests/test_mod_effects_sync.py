"""
The three stage-25 Mody Patusa across a real server.

None of them adds a command, and that is the claim these tests exist to check.
Paczka's window is built by each machine from its own replica, Shady's pawn is
hidden by the round beginning on every machine at once, and Squid Game's check
is ARMED everywhere and JUDGED only on the authority — so all three ought to
replay out of the commands the game already had, and every replica ought to end
up with the same fingerprint.

The last of those is the one worth being careful about: the server knows the
hidden colour and the clients do not, so a mod that made the server's copy
drift from theirs would put five machines into a resync loop.
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


def force_mod(room, title: str, slot: int = 0):
    """Put a named mod into the SERVER's rack and let the table catch up.

    Written against the authority's copy because that is where a real arrival
    happens: the clients then learn about it the way they learn about
    everything else, by replaying the command that follows.
    """
    state = room.state
    deck = state.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    state.mod_slots[slot] = card
    return card


def spread_pawns(state, start: int = 1) -> None:
    for offset, pawn in enumerate(state.library.pawns):
        tile = state.board.position(start + offset).tiles[0]
        state.board.place_pawn(pawn.id, tile.index)
    state._sync_token_positions()


def mirror(room, services) -> None:
    """Copy the board the test posed onto every replica.

    The tests below pose a position by hand rather than playing twenty turns to
    reach one; the position itself is not what is under test, so it is put on
    every machine directly and only what happens NEXT travels as commands.
    """
    placement = room.state.board.to_dict()
    for service in services:
        if service.state is not None:
            service.state.board.apply_placement(
                placement["pawn_tiles"],
                {int(k): v for k, v in placement["stacks"].items()},
            )
            service.state._sync_token_positions()


def racks_agree(room, services) -> bool:
    wanted = [c.uid if c else None for c in room.state.mod_slots]
    return all([c.uid if c else None for c in s.state.mod_slots] == wanted
               for s in services if s.state is not None)


# ── Paczka ───────────────────────────────────────────────────────────────────
def test_every_machine_builds_the_same_paczka_window(library):
    """The list is built locally on each machine and must come out identical."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]

    # Captured ONCE: the loop below empties each machine's pile, so reading
    # the title inside it would name a different card every iteration.
    title = host.state.decks[settings.DECK_CHEST].draw_pile[0].title
    for service in parties:
        state = service.state
        pile = state.decks[settings.DECK_CHEST].draw_pile
        card = next(c for c in pile if c.title == title)
        pile.remove(card)
        state.player(1).add_card(card)

    windows = []
    for service in parties:
        events = service.state._chest_reveal_event()
        windows.append([(h.player_index, tuple(h.titles))
                        for h in events.holdings])
    assert windows[0]
    assert all(window == windows[0] for window in windows)
    table.close()


def test_paczka_needs_no_command_of_its_own(library):
    """It is informational: no command, and nothing added to the fingerprint."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    room = table.room(host.room_code)

    before = len(room.command_log)
    force_mod(room, "Paczka")
    events = room.state._sync_mod_states()
    assert [e for e in events if isinstance(e, ev.ChestCardsRevealed)]
    assert len(room.command_log) == before, "no command was logged"
    table.close()


# ── Squid Game ───────────────────────────────────────────────────────────────
def test_the_automatic_check_reaches_every_machine(library):
    """Armed everywhere, judged once, and the result is an ordinary command."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    card = force_mod(room, "Squid Game")
    for service in parties:
        state = service.state
        twin = next(c for c in state.decks[settings.DECK_MODS].draw_pile
                    if c.uid == card.uid)
        state.decks[settings.DECK_MODS].draw_pile.remove(twin)
        state.mod_slots[0] = twin
        state._sync_mod_states()
        spread_pawns(state)
    spread_pawns(room.state)
    room.state._sync_mod_states()

    leader = room.state.leading_pawn()
    assert leader is not None
    for service in parties:
        assert service.state.leading_pawn() == leader, "the question is public"

    # The round begins on every machine, so every machine arms the same check.
    for state in [room.state, *[s.state for s in parties]]:
        state._begin_round(state.round_number + 1)
        assert state.pending_lead_check == leader

    # Only the authority answers it.  A replica that was never told the colour
    # runs the identical code and reaches no verdict at all, which is the
    # safety net under the whole design (N72).  Piotrek\'s own machine knows
    # his colour and would reach one — nothing ever asks it to, because a
    # NetworkSession never calls review.
    from pedzacy_piotrek.engine import victory
    blind = [s for s in parties
             if all(p.secret_pawn is None for p in s.state.players)]
    assert blind, "at least the non-Piotrek machines know nothing"
    for service in blind:
        assert victory.review(service.state) == [], "a client decides nothing"

    # Any accepted command triggers the authority's review, and the verdict
    # then travels as the ordinary logged, broadcast EliminatePawn — which is
    # the whole point of returning commands rather than mutating.
    take_a_turn(table, host, clients)
    assert room.state.eliminated_pawns == [leader]
    for service in parties:
        assert service.state.eliminated_pawns == [leader], "everybody was told"
    table.close()


def test_the_armed_round_is_in_the_fingerprint(library):
    """Disagreeing about WHEN a mod arrived is disagreeing about the game.

    A Squid Game that reached one machine in round 4 and another in round 5
    starts checking on different rounds, and the only symptom would be an
    elimination appearing on one screen and not the others.  Putting the armed
    round in the snapshot turns that into a detected desync instead.
    """
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    room = table.room(host.room_code)
    assert all_agree(host, *clients), "in step to begin with"

    force_mod(room, "Squid Game")
    room.state._sync_mod_states()
    armed = room.state.snapshot()["armed_mods"]
    assert [tuple(entry) for entry in armed] == [
        (room.state.mod_slots[0].uid, room.state.round_number)
    ]
    # The clients have not been told, so they now differ from the authority —
    # which is exactly the drift the fingerprint exists to catch.
    assert room.state.snapshot() != host.state.snapshot()
    table.close()


def test_the_server_knowing_the_colour_does_not_move_the_fingerprint(library):
    """The whole design rests on this: a secret must not cause a resync."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    room = table.room(host.room_code)

    before = room.state.snapshot()
    seat = room.state.piotrek_seat
    room.state.player(seat).secret_pawn = room.state.library.pawns[0].id
    room.state.pending_lead_check = room.state.library.pawns[0].id
    after = room.state.snapshot()
    del before["pending_lead_check"], after["pending_lead_check"]
    assert before == after, "the colour itself is not in the snapshot"
    table.close()


# ── Shady ────────────────────────────────────────────────────────────────────
def test_every_machine_hides_and_restores_the_same_pawn(library):
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    card = force_mod(room, "Shady")
    spread_pawns(room.state)
    hidden_events = room.state._sync_mod_states()
    hidden = room.state.hidden_pawn_ids
    assert len(hidden) == 1
    assert [e for e in hidden_events if isinstance(e, ev.PawnHidden)]

    for service in parties:
        state = service.state
        twin = next(c for c in state.decks[settings.DECK_MODS].draw_pile
                    if c.uid == card.uid)
        state.decks[settings.DECK_MODS].draw_pile.remove(twin)
        state.mod_slots[0] = twin
        spread_pawns(state)
        state._sync_mod_states()
        assert state.hidden_pawn_ids == hidden, "the same pawn, everywhere"

    for state in [room.state, *[s.state for s in parties]]:
        state._begin_round(state.round_number + 1)
    where = room.state.board.position_of_pawn(hidden[0])
    for service in parties:
        assert not service.state.hidden_pawn_ids
        assert service.state.board.position_of_pawn(hidden[0]) == where
    table.close()


def test_a_hidden_pawn_is_a_status_and_therefore_survives_a_resync(library):
    """N18 buys reconnection for nothing: the status is already in the sync."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    room = table.room(host.room_code)

    force_mod(room, "Shady")
    spread_pawns(room.state)
    room.state._sync_mod_states()

    statuses = room.state.snapshot()["statuses"]
    assert any(s["kind"] == "hidden" for s in statuses)
    table.close()


def test_the_table_still_agrees_after_a_dozen_turns(library):
    """The ordinary safety net: nothing above broke the replication."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    for _ in range(12):
        take_a_turn(table, host, clients)
    assert all_agree(host, *clients)
    table.close()
