"""
Turn progression: does the cursor actually walk the whole round?

These exist because of a bug that lived through every previous stage without a
single test noticing.  ``compute_round_turn_order`` was tested and was always
right; what nobody tested was the CURSOR that walks the order it produces.

``_end_turn`` used to recover its position with ``order.index(seat)``, which
returns the FIRST slot a seat occupies.  Piotrek occupies every third slot, so
the moment his turn came round the cursor rewound to zero and the game looped
over the first three slots for ever — seats further down the round never played
at all, rounds never ended, and every round-based mechanic (chest hand-outs,
status expiry, Troll and Stańczyk) silently stopped.

The lesson these tests encode: assert on the SEQUENCE OF TURNS ACTUALLY TAKEN,
not on the function that computes the order.  A test of the order alone passes
happily while the game plays three seats in a circle.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game

from netkit import Table, all_agree
from pedzacy_piotrek.cards.loader import ContentLibrary


def make(players: int = 6, seed: int = 5, **kwargs):
    """A game that never interrupts the turn loop, so the walk can get somewhere.

    Two things would otherwise stop it, and both are real behaviour with tests
    of their own:

    * the chest hand-out can raise the hand limit and park a keep-or-discard
      prompt in front of the player, and ``EndTurn`` is then correctly refused
      until it is answered;
    * a Mod Patusa round pauses the table outright until both factions have
      chosen, which refuses ``EndTurn`` for exactly the same reason.

    Pushing both out past any round these tests reach keeps them about the turn
    cadence, which is what they are for.
    """
    config = SessionConfig(
        num_players=players, board_cells=40, seed=seed, edit_mode=True,
        piotrek_picks_pawn=False, chest_open_round=10_000,
        mod_round_first=10_000, **kwargs
    )
    return create_game(config)


def walk(state, turns: int):
    """Take ``turns`` turns and return the seat that held each one."""
    seats = []
    for _ in range(turns):
        seats.append(state.active_player_index)
        events = state.apply(cmd.EndTurn(player_index=state.active_player_index))
        rejected = [e for e in events if isinstance(e, ev.ActionRejected)]
        assert not rejected, f"turn refused: {[r.reason for r in rejected]}"
    return seats


def rounds_walked(state, turns: int):
    """Turns grouped by the round they happened in."""
    grouped: dict[int, list[int]] = {}
    for _ in range(turns):
        grouped.setdefault(state.round_number, []).append(state.active_player_index)
        state.apply(cmd.EndTurn(player_index=state.active_player_index))
    return grouped


# ── the bug itself ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("players", [2, 3, 4, 5, 6, 8])
def test_every_seat_gets_a_turn(players):
    """The reported bug, stated directly: nobody may be stranded.

    Before the fix this failed for every count above three — the walk visited
    exactly three seats no matter how many were sitting at the table.
    """
    state = make(players)
    everyone = {p.index for p in state.players}
    seen = set(walk(state, len(everyone) * 8))
    assert seen == everyone, f"never played: {sorted(everyone - seen)}"


def test_the_round_does_not_restart_when_piotrek_plays():
    """The precise mechanism: Piotrek holds several slots, the cursor must not
    rewind to the first of them."""
    state = make(6)
    order = state.seat_order(1)
    piotrek = next(p.index for p in state.players if p.is_piotrek)
    assert order.count(piotrek) > 1, "this test is pointless unless he repeats"

    slots = []
    for _ in range(len(order)):
        slots.append(state.turn_slot)
        state.apply(cmd.EndTurn(player_index=state.active_player_index))
    # Strictly increasing across a round: no slot revisited, none skipped.
    assert slots == list(range(len(order)))


def test_the_documented_cadence_is_what_actually_happens():
    """Piotrek, two hunters, Piotrek, the next two hunters — as designed.

    This is the sequence from the bug report: the game is meant to give Piotrek
    every third slot while the hunters cycle continuously past him.
    """
    state = make(6)
    piotrek = next(p.index for p in state.players if p.is_piotrek)
    seats = walk(state, 8)
    is_piotrek = [s == piotrek for s in seats]
    assert is_piotrek == [True, False, False, True, False, False, True, False]
    hunters = [s for s in seats if s != piotrek]
    assert len(set(hunters)) == len(hunters), "a hunter played twice too early"


# ── rounds ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("players", [3, 4, 6, 8])
def test_a_round_ends_only_once_everybody_has_played(players):
    state = make(players)
    everyone = {p.index for p in state.players}
    grouped = rounds_walked(state, len(everyone) * 10)
    finished = sorted(grouped)[:-1]          # the last one is still in progress
    assert finished, "no round ever completed"
    for round_number in finished:
        assert set(grouped[round_number]) == everyone, (
            f"round {round_number} ended without {sorted(everyone - set(grouped[round_number]))}"
        )


def test_the_round_counter_only_moves_between_rounds():
    state = make(6)
    seen = []
    for _ in range(40):
        seen.append(state.round_number)
        state.apply(cmd.EndTurn(player_index=state.active_player_index))
    # Monotonic, one at a time, and it does actually advance.
    assert seen == sorted(seen)
    assert seen[-1] > seen[0]
    assert all(b - a in (0, 1) for a, b in zip(seen, seen[1:]))


def test_nobody_plays_twice_in_a_row_at_a_full_table():
    state = make(8)
    seats = walk(state, 40)
    doubled = [i for i in range(1, len(seats)) if seats[i] == seats[i - 1]]
    assert not doubled, f"consecutive turns at {doubled}"


# ── the counter, and what hangs off it ───────────────────────────────────────
def test_every_turn_counts_even_when_the_seat_repeats():
    """Status expiry is measured in turns, so a turn that does not count is a
    status that never expires.

    With one hunter the cadence really does hand them two slots in a row, and
    the old code only counted a turn when the SEAT changed.
    """
    state = make(2)
    before = state.turn_counter
    taken = 12
    walk(state, taken)
    assert state.turn_counter - before == taken


# ── the cursor is real state ─────────────────────────────────────────────────
def test_the_cursor_agrees_with_the_seat_at_all_times():
    state = make(6)
    for _ in range(30):
        order = state.seat_order()
        assert order[state.turn_slot] == state.active_player_index
        state.apply(cmd.EndTurn(player_index=state.active_player_index))


def test_the_cursor_is_in_the_snapshot():
    """Two machines on different slots agree on whose turn it is and disagree
    about the whole rest of the round.  The fingerprint has to be able to see
    that."""
    state = make(6)
    walk(state, 4)
    assert state.snapshot()["turn_slot"] == state.turn_slot


def test_setting_a_seat_by_hand_leaves_the_cursor_consistent():
    """Edit mode can jump to any seat; the round must carry on from there."""
    state = make(6)
    walk(state, 2)
    target = next(p.index for p in state.players
                  if p.index != state.active_player_index)
    state.apply(cmd.SetActivePlayer(player_index=target))
    order = state.seat_order()
    assert order[state.turn_slot] == state.active_player_index
    # And the walk still reaches everybody afterwards.
    everyone = {p.index for p in state.players}
    assert set(walk(state, len(everyone) * 6)) == everyone


def test_jumping_to_a_later_round_does_not_stall_the_walk():
    state = make(6)
    state.apply(cmd.SetRound(round_number=4))
    everyone = {p.index for p in state.players}
    assert set(walk(state, len(everyone) * 6)) == everyone


# ── online ───────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def library():
    return ContentLibrary.load()


@pytest.fixture
def table(library):
    t = Table(library)
    yield t
    t.close()


def test_every_machine_walks_the_same_turn_order(table):
    """Host and clients must agree on the CURSOR, not merely on the seat.

    Agreeing on ``active_player`` while standing on different slots is the
    quiet version of this bug: both machines name the same player and then
    disagree about the entire rest of the round.  ``turn_slot`` is in the
    snapshot so ``all_agree`` can actually see a drift like that.
    """
    # Enough players that Piotrek holds SEVERAL slots in a round.  With three
    # he holds exactly one, the cursor never has anything to rewind to, and the
    # test passes cheerfully against the bug it was written to catch.
    host, clients = table.playing("Kuba", "Ola", "Ala", "Ela", "Ula", "Iza")
    piotrek = next(p.index for p in host.state.players if p.is_piotrek)
    assert host.state.seat_order(1).count(piotrek) > 1, "table too small to test"
    seen = []
    for _ in range(24):
        seat = host.state.active_player_index
        seen.append(seat)
        table.by_seat(host, clients)[seat].session.submit(
            cmd.EndTurn(player_index=seat))
        table.pump()
        assert all_agree(host, *clients), "machines drifted apart"
        for machine in (host, *clients):
            assert machine.state.turn_slot == host.state.turn_slot
            assert machine.state.round_number == host.state.round_number

    everyone = {p.index for p in host.state.players}
    assert set(seen) == everyone, (
        f"never played online: {sorted(everyone - set(seen))}"
    )


def test_a_reconnecting_client_lands_on_the_same_slot(table):
    """The cursor is replayed from the command log like everything else."""
    host, clients = table.playing("Kuba", "Ola", "Ala", "Ela", "Ula", "Iza")
    for _ in range(7):
        seat = host.state.active_player_index
        table.by_seat(host, clients)[seat].session.submit(
            cmd.EndTurn(player_index=seat))
        table.pump()

    victim = clients[0]
    room = table.room(host.room_code)
    room.mark_absent(victim.peer_id)
    for outbound in room.catch_up(victim.peer_id):
        victim.client._handle(outbound[1])

    assert victim.state.turn_slot == host.state.turn_slot
    assert victim.state.active_player_index == host.state.active_player_index
    assert victim.state.round_number == host.state.round_number
