"""
The stage 27 Karty Skrzyni across a real server.

The claim under test is the same one every stage makes and none of them gets to
assume: NO NEW COMMANDS.  All four cards are ordinary ``PlayCard``s whose
decisions ride in ``choices``, so every replica ought to reach an identical
table by replaying the log, and the fingerprint ought to agree afterwards.

Three of them have a specific way to get this wrong, and there is a test for
each:

* Dzieckorolka rearranges a TOWER, and a tower in the wrong order is a
  different game — it decides who is at the bottom when the hunters check;
* Balbinka rolls a die for every widened destination, and a die rolled in a
  handler rather than in the executor would give each machine its own answer;
* Rage Quit draws two cards off a shared pile, and two draws in a different
  order on two machines is a desync.
"""

from __future__ import annotations

import pytest

from netkit import Table, all_agree, take_a_turn
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.statuses import StatusKind


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def everywhere(room, services):
    """The authority's state and every client's, as one list."""
    return [room.state, *[s.state for s in services if s.state is not None]]


def straight(table, *names):
    """A started match on a board with NO widened rows.

    Dzieckorolka asks a question at every widened position it walks through, so
    a board full of them turns a test about tower ORDER into a test about
    answering prompts.  The prompts get their own test below; these want the
    route settled so the stack is the only variable.
    """
    host, clients = table.seated(*names)
    host.set_settings(mod_round_first=10_000, double_percent=0)
    table.pump()
    host.start_game(table.library)
    table.pump()
    table.choose_identity(host, clients)
    return host, clients


def pose(room, services, positions):
    """Stand the pawns where the test wants them, on every machine.

    The position is not what is under test; only what happens NEXT is allowed
    to travel as commands.  Written straight onto each replica rather than
    mirrored from the authority so the tower ORDER is identical to begin with —
    a mirrored ``apply_placement`` would be testing the mirror.
    """
    for state in everywhere(room, services):
        for pawn in state.library.pawns:
            state.board.remove_pawn(pawn.id)
        for pawn_id, position in positions:
            tile = state.board.position(position).tiles[0]
            state.board.place_pawn(pawn_id, tile.index)
        state._sync_token_positions()


def deal_chest(room, services, seat: int, title: str) -> int:
    """Put the same physical chest card into the same hand on every machine.

    Returns its uid, which is what the command will name.  Uids are assigned
    from deck position (not a process counter), so the card with this uid is
    the same card everywhere — that is what lets one command mean one thing.
    """
    uid = None
    for state in everywhere(room, services):
        deck = state.decks[settings.DECK_CHEST]
        card = next(c for c in deck.draw_pile if c.title == title)
        deck.draw_pile.remove(card)
        state.player(seat).add_card(card)
        uid = card.uid
    return uid


def force_mod(room, services, title: str, slot: int = 0) -> int:
    """Install the same mod in the same slot on every machine."""
    uid = None
    for state in everywhere(room, services):
        deck = state.decks[settings.DECK_MODS]
        card = next(c for c in deck.draw_pile if c.title == title)
        deck.draw_pile.remove(card)
        state.mod_slots[slot] = card
        state._sync_mod_states()
        uid = card.uid
    return uid


def towers(state):
    """Every occupied field as a tile index → tower, BOTTOM FIRST."""
    return {t.index: list(t.stack) for t in state.board.tiles if t.stack}


def racks(state):
    return [c.uid if c else None for c in state.mod_slots]


# ── Dzieckorolka ─────────────────────────────────────────────────────────────
def test_every_machine_builds_the_same_tower(library):
    """The order of the collected stack has to survive the wire intact.

    It is not decoration: the hunters win by checking the pawn at the BOTTOM of
    a tower, so two machines with the same pawns in a different order disagree
    about who wins.
    """
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    green, blue, pink, red, yellow = (
        "zielony", "niebieski", "różowy", "czerwony", "żółty")
    pose(room, parties, [
        (green, 1), (blue, 2), (pink, 2), (red, 3), (yellow, 4),
    ])

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Dzieckorolka")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid,
                                      choices={"pawn": green}))
    table.pump()

    expected = towers(room.state)
    destination = room.state.board.pawn_tiles[green]
    assert expected[destination] == [yellow, red, pink, green]
    for service in parties:
        assert towers(service.state) == expected
    table.close()


def test_dzieckorolka_needs_no_command_of_its_own(library):
    """Every decision it asks for rides in PlayCard.choices."""
    table = Table(library)
    host, clients = straight(table, "Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)
    pose(room, parties, [("zielony", 1), ("różowy", 2)])

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Dzieckorolka")
    before = len(room.command_log)

    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid,
                                      choices={"pawn": "zielony"}))
    table.pump()

    added = room.command_log[before:]
    assert added, "the play itself is logged"
    # The log holds encoded commands, and every one of these is an ordinary
    # play_card: no new wire vocabulary was needed for any of this stage.
    assert [entry["kind"] for entry in added] == ["play_card"]
    assert all_agree(host, *clients)
    table.close()


def test_an_unanswered_question_reaches_only_the_asker(library):
    """N40: a command that produced only a question changed nothing.

    Broadcasting it would put a modal on every screen asking something one
    player is supposed to answer.
    """
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Dzieckorolka")
    before = len(room.command_log)

    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid))
    table.pump()

    assert len(room.command_log) == before, "nothing happened, so nothing logged"
    assert all_agree(host, *clients)
    table.close()


def test_the_branch_questions_travel_as_choices(library):
    """The widened-row prompts ride in ``choices``, one resubmission each.

    This is the path the straight-board tests deliberately avoid, and it is the
    one that would break if a question were ever answered anywhere but in the
    command: the server asks the ASKER alone (N40), the asker answers, and only
    the finished play is broadcast.
    """
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    doubled = [i for i in range(room.state.board.position_count)
               if room.state.board.position(i).is_doubled]
    if not doubled:
        pytest.skip("this seed produced a board with no widened rows")

    mover = "zielony"
    pose(room, parties, [(mover, doubled[0])])
    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Dzieckorolka")
    actor = table.by_seat(host, clients)[seat]

    before = len(room.command_log)
    answers = {"pawn": mover}
    asked = 0
    for _ in range(6):
        actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid,
                                          choices=dict(answers)))
        table.pump()
        if len(room.command_log) > before:
            break
        # The engine on this machine raises the same question the server did,
        # which is how the interface knows what to draw.
        question = actor.session.state.apply(
            cmd.PlayCard(player_index=seat, card_uid=uid, choices=dict(answers))
        )
        pending = next(e for e in question if isinstance(e, ev.ChoiceRequired))
        assert pending.kind == "tile"
        answers[pending.key] = str(pending.tiles[0])
        asked += 1

    assert asked >= 1, "a board of widened rows must have asked at least once"
    assert [entry["kind"] for entry in room.command_log[before:]] == ["play_card"]
    assert room.state.board.position_of_pawn(mover) == doubled[0] + 3
    for service in parties:
        assert service.state.board.position_of_pawn(mover) == doubled[0] + 3
    assert all_agree(host, *clients)
    table.close()


# ── Balbinka ─────────────────────────────────────────────────────────────────
def test_the_random_branches_come_out_the_same_everywhere(library):
    """The die is the seeded game RNG, rolled in the executor (N78).

    Rolled in the handler it would be rolled again by every preview, and the
    machines would disagree the moment one of them drew a frame the others
    did not.
    """
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    pose(room, parties, [(pawn.id, 3 + index)
                         for index, pawn in enumerate(room.state.library.pawns)])

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Balbinka")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid,
                                      choices={"direction": "forward"}))
    table.pump()

    expected = towers(room.state)
    for service in parties:
        assert towers(service.state) == expected
    assert all_agree(host, *clients)
    table.close()


def test_balbinka_moves_the_whole_table_on_every_replica(library):
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    start = {pawn.id: 4 + index
             for index, pawn in enumerate(room.state.library.pawns)}
    pose(room, parties, list(start.items()))

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Balbinka")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid,
                                      choices={"direction": "backward"}))
    table.pump()

    for state in everywhere(room, parties):
        for pawn_id, position in start.items():
            assert state.board.position_of_pawn(pawn_id) == position - 2
    table.close()


# ── Rage Quit ────────────────────────────────────────────────────────────────
def test_both_racks_end_up_holding_the_same_two_mods(library):
    """Two draws off one pile, in one fixed order, on every machine."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    force_mod(room, parties, "Speedrun", 0)
    force_mod(room, parties, "Sesja na PG", 1)
    before = racks(room.state)

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Rage Quit")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid))
    table.pump()

    after = racks(room.state)
    assert after != before and None not in after
    for service in parties:
        assert racks(service.state) == after
    assert all_agree(host, *clients)
    table.close()


def test_rage_quit_giving_shady_its_pawn_back_replicates(library):
    """The departure half runs inside the same command, so it replays (N107)."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    pose(room, parties, [(pawn.id, 2 + index)
                         for index, pawn in enumerate(room.state.library.pawns)])
    force_mod(room, parties, "Shady", 0)
    force_mod(room, parties, "Paczka", 1)
    hidden = room.state.hidden_pawn_ids
    assert len(hidden) == 1
    for state in everywhere(room, parties):
        assert state.hidden_pawn_ids == hidden, "hidden on every machine first"

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Rage Quit")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid))
    table.pump()

    for state in everywhere(room, parties):
        # The mod that took THIS pawn has gone, so this pawn is back — even if
        # the card drawn to replace it happens to be another Shady, which then
        # takes a pawn of its own.  Matching on the mod rather than on "does
        # anything still hide?" is what makes those two cases different.
        assert hidden[0] not in state.hidden_pawn_ids
        assert state.board.pawn_tile(hidden[0]) is not None
    assert all_agree(host, *clients)
    table.close()


# ── Gambit Patusa ────────────────────────────────────────────────────────────
def test_the_reversal_replicates_and_shows_in_the_fingerprint(library):
    """It is a Status, so the snapshot and reconnection are both free."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Gambit Patusa")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid))
    table.pump()

    target = room.state.round_number + 1
    for state in everywhere(room, parties):
        assert state.statuses.movement_reversed_in(target)
        assert not state.movement_reversed, "not until the round arrives"
    assert all_agree(host, *clients)
    table.close()


def test_every_machine_reverses_the_same_round(library):
    """The promise is about a round number, which no replica can disagree on."""
    table = Table(library)
    host, clients = table.playing("Kuba", "Ola", "Antek")
    parties = [host, *clients]
    room = table.room(host.room_code)

    seat = room.state.active_player_index
    uid = deal_chest(room, parties, seat, "Gambit Patusa")
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=uid))
    table.pump()

    # Walk the table into the next round the ordinary way, one turn at a time.
    target = room.state.round_number + 1
    for _ in range(40):
        if room.state.round_number >= target:
            break
        take_a_turn(table, host, clients)

    assert room.state.round_number == target
    for state in everywhere(room, parties):
        assert state.round_number == target
        assert state.movement_reversed
    assert all_agree(host, *clients)
    table.close()
