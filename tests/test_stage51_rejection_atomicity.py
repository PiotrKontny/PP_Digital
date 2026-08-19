"""
A REFUSED COMMAND CHANGES NOTHING.

The authority does not log or broadcast a command it refuses (``Room.submit``),
so a handler that changes the table and only THEN refuses leaves the server in
a state its own command log cannot reproduce.  Every client then replays that
log, arrives somewhere else, and is told — permanently —

    "Uwaga: stan gry różni się od serwera — zgłoś to"

which is the message a real three-player match produced.  The tests here pin
the invariant that makes it impossible:

    ActionRejected  =>  the authoritative snapshot is byte-identical

They are written against the FINGERPRINT rather than against the particular
field a handler happened to touch, because the next handler will touch a
different one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from netkit import (Table, apply_to, playable_card, replay_fingerprint,
                    take_a_turn)
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.statuses import Status, StatusKind, Subject
from pedzacy_piotrek.net.protocol import fingerprint_of


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library):
    made = Table(library)
    yield made
    made.close()


def a_started_room(table):
    """A three-player match in progress, and the room that owns it."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    return host, clients, table.room(host.room_code)


def freeze(state, pawn_id: str) -> Status:
    """Granny Costume's status, attached directly.

    Direct rather than through Big D Randy because these tests are about ONE
    state object and what one command does to it; the three-player regression
    at the bottom of this file earns the status the long way, through a real
    ability, so that the log genuinely contains it.
    """
    return state.statuses.add(Status(
        kind=StatusKind.FROZEN, subject=Subject.PAWN, subject_id=pawn_id,
        source="Granny Costume"))


# ── A. the confirmed dirty rejection ─────────────────────────────────────────
def test_refusing_an_unknown_field_leaves_a_frozen_pawn_frozen(table):
    """THE BUG.  ``_thaw_dragged`` used to run before the field was checked.

    Dragging a pawn by hand deliberately ends its freeze — that is the testing
    tool overruling a rule, and it is correct when the drag SUCCEEDS.  It was
    also happening when the drag was refused, which is a change nobody could
    see and no log could carry.
    """
    _, _, room = a_started_room(table)
    state = room.state
    pawn = state.library.pawns[0].id
    freeze(state, pawn)

    outcome = apply_to(state, cmd.MoveToken(pawn_id=pawn, x=1.0, y=2.0,
                                            tile_index=99_999))

    assert outcome.refused
    assert outcome.reason == "Nieznane pole"
    assert not outcome.touched_state, (
        "a refused MoveToken moved the authoritative state: "
        f"{outcome.before} -> {outcome.after}")
    assert state.statuses.pawn_has(StatusKind.FROZEN, pawn)


def test_refusing_unusable_coordinates_leaves_the_pawn_on_the_board(table):
    """THE SECOND DIRTY PATH, found while fixing the first.

    Free placement removed the pawn from the board and only then converted the
    coordinates, so a message carrying nonsense where a number belongs took the
    pawn off the board and refused in the same breath.  ``pawn_index`` reads an
    absent pawn as ``CAMP_INDEX``, so the server quietly believed a pawn
    standing on field 5 had never set out.
    """
    _, _, room = a_started_room(table)
    state = room.state
    pawn = state.library.pawns[0].id
    tile = state.board.tiles[4]
    state.apply(cmd.MoveToken(pawn_id=pawn, x=tile.position[0],
                              y=tile.position[1], tile_index=tile.index),
                local=False)
    assert state.board.pawn_tiles.get(pawn) == tile.index

    outcome = apply_to(state, cmd.MoveToken(pawn_id=pawn, x="nonsens", y=0.0,
                                            tile_index=None))

    assert outcome.refused
    assert not outcome.touched_state
    assert state.board.pawn_tiles.get(pawn) == tile.index


def test_refusing_a_malformed_field_index_changes_nothing(table):
    """A ``tile_index`` that is not a number at all.

    ``board.tile()`` compared it with ``0 <=`` and raised, which ``apply``
    turns into a refusal — after the thaw had already run.  Same class, different
    door.
    """
    _, _, room = a_started_room(table)
    state = room.state
    pawn = state.library.pawns[0].id
    freeze(state, pawn)

    outcome = apply_to(state, cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0,
                                            tile_index="abc"))

    assert outcome.refused
    assert not outcome.touched_state
    assert state.statuses.pawn_has(StatusKind.FROZEN, pawn)


# ── the invariant, across the whole command surface ──────────────────────────
def refusable_commands(state):
    """One of every shape of refusal the engine can produce.

    Not exhaustive over inputs — exhaustive over HANDLERS that can say no, which
    is where the next instance of this bug would live.
    """
    seat = state.active_player_index
    pawn = state.library.pawns[0].id
    frozen = state.library.pawns[1].id
    return [
        ("MoveToken: unknown pawn",
         cmd.MoveToken(pawn_id="nie-ma", x=0.0, y=0.0, tile_index=0)),
        ("MoveToken: unknown field",
         cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0, tile_index=99_999)),
        ("MoveToken: negative field",
         cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0, tile_index=-5)),
        ("MoveToken: malformed field",
         cmd.MoveToken(pawn_id=frozen, x=0.0, y=0.0, tile_index="abc")),
        ("MoveToken: malformed coordinates",
         cmd.MoveToken(pawn_id=frozen, x=None, y=0.0, tile_index=None)),
        ("PickUpToken: unknown pawn", cmd.PickUpToken(pawn_id="nie-ma")),
        ("DiscardCard: no such card",
         cmd.DiscardCard(player_index=seat, card_uid=-1)),
        ("PlayCard: no such card",
         cmd.PlayCard(player_index=seat, card_uid=-1)),
        ("DrawTitledCard: no such title",
         cmd.DrawTitledCard(player_index=seat, deck_id="movement",
                            title="nie-ma")),
        ("AdjustDeckCount: no such deck",
         cmd.AdjustDeckCount(deck_id="nie-ma", title="x", delta=1)),
        ("AdjustAbilityUses: no such ability",
         cmd.AdjustAbilityUses(title="nie-ma", delta=-1)),
        ("RestoreAbilityUses: no such ability",
         cmd.RestoreAbilityUses(title="nie-ma")),
        ("ChooseMod: no selection open",
         cmd.ChooseMod(player_index=seat, card_uid=1)),
        ("VoteMod: no selection open",
         cmd.VoteMod(player_index=seat, card_uid=1)),
        ("DiscardMod: empty slot", cmd.DiscardMod(slot=0)),
        ("AcceptMovement: nothing paused",
         cmd.AcceptMovement(player_index=seat)),
        ("BlockMovement: nothing paused",
         cmd.BlockMovement(player_index=seat)),
        ("AllowCheck: nothing to answer", cmd.AllowCheck(player_index=seat)),
        ("RefuseCheck: nothing to answer", cmd.RefuseCheck(player_index=seat)),
        ("ChooseBreakupTile: nothing to choose",
         cmd.ChooseBreakupTile(player_index=seat, tile_index=0)),
        ("EliminatePawn: unknown colour", cmd.EliminatePawn(pawn_id="nie-ma")),
        ("DeclareVictory: unknown outcome",
         cmd.DeclareVictory(outcome="nonsens")),
        ("UndoMove: no window", cmd.UndoMove(player_index=seat)),
    ]


def test_no_refusal_anywhere_moves_the_authoritative_state(table):
    """THE INVARIANT ITSELF, over every handler that can refuse.

    A fresh room per command, so one dirty handler cannot mask another by
    leaving the state somewhere the next command is refused from anyway.
    """
    library = table.library
    dirty = []
    for label, command in refusable_commands(a_started_room(table)[2].state):
        single = Table(library)
        try:
            _, _, room = a_started_room(single)
            state = room.state
            # Something for the freeze cases to actually thaw.
            freeze(state, state.library.pawns[1].id)
            outcome = apply_to(state, command)
            if not outcome.refused:
                continue        # nothing to say about a command that succeeded
            if outcome.touched_state:
                dirty.append(f"{label} -> {outcome.reason!r}")
        finally:
            single.close()
    assert not dirty, "refused commands that changed the state: " + "; ".join(dirty)


# ── B. the server does not log or broadcast a refusal ────────────────────────
def test_a_refused_command_is_neither_logged_nor_stamped(table):
    host, clients, room = a_started_room(table)
    seat = room.state.active_player_index
    actor = table.by_seat(host, clients)[seat]
    pawn = room.state.library.pawns[0].id

    log_before = list(room.command_log)
    stamp_before = room.fingerprint
    state_before = fingerprint_of(room.state.snapshot())

    actor.session.submit(cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0,
                                       tile_index=99_999))
    table.pump()

    assert room.command_log == log_before
    assert room.fingerprint == stamp_before
    assert fingerprint_of(room.state.snapshot()) == state_before


# ── C. the server is always replayable from its own log ──────────────────────
def test_the_server_state_replays_from_its_own_command_log(table, library):
    """The property every client depends on when it asks for a resync.

    A realistic sequence WITH refusals in it, then the same thing a client does
    on ``STATE_SYNC``: build from the configuration and apply the log.
    """
    host, clients, room = a_started_room(table)
    pawn = room.state.library.pawns[0].id
    # NOTHING is attached by hand here: a replay can only reproduce what the
    # log carries, so a status injected directly would make this test fail for
    # a reason that has nothing to do with the server.  The freeze earned
    # through a real ability lives in the three-player test below.

    for round_number in range(4):
        seat = room.state.active_player_index
        actor = table.by_seat(host, clients)[seat]
        # a legal manual placement
        tile = room.state.board.tiles[round_number + 1]
        actor.session.submit(cmd.MoveToken(
            pawn_id=pawn, x=tile.position[0], y=tile.position[1],
            tile_index=tile.index))
        table.pump()
        # ...and three that must bounce.  The middle one is the point: before
        # stage 51 it took the pawn off the board and THEN refused, so the
        # server kept a pawn in the camp that its own log puts on a field.
        actor.session.submit(cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0,
                                           tile_index=99_999))
        actor.session.submit(cmd.MoveToken(pawn_id=pawn, x="nonsens", y=0.0,
                                           tile_index=None))
        actor.session.submit(cmd.DiscardCard(player_index=seat, card_uid=-1))
        table.pump()
        take_a_turn(table, host, clients)

    assert replay_fingerprint(room, library) == fingerprint_of(
        room.state.snapshot())


# ── D. the three-player match that produced the report ───────────────────────
def test_a_refused_drag_of_a_frozen_pawn_does_not_desync_three_players(table,
                                                                      library):
    """THE REPORTED SCENARIO, end to end, with the freeze earned honestly.

    Big D Randy's ability is the only path by which a FROZEN status enters the
    COMMAND LOG, and that is what makes this test meaningful: a status the
    replicas can rebuild is a status the server may not quietly drop.  The deal
    is seeded by the server, so the table is opened until Randy is at it —
    the same idiom ``test_multiplayer.py`` already uses for a seeded hand.
    """
    for _ in range(80):
        attempt = Table(library)
        try:
            host, clients = attempt.playing("Kuba", "Ola", "Norbert")
            room = attempt.room(host.room_code)
            randy = next((p.index for p in room.state.players
                          if p.character is not None
                          and p.character.title == "Big D Randy"), None)
            if randy is None:
                continue
            by_seat = attempt.by_seat(host, clients)
            while room.state.active_player_index != randy:
                seat = room.state.active_player_index
                by_seat[seat].session.submit(cmd.EndTurn(player_index=seat))
                attempt.pump()
            actor = by_seat[randy]

            # Randy freezes a pawn that stands alone, which is his rule.
            for offset, pawn in enumerate(room.state.library.pawns):
                tile = room.state.board.tiles[2 + offset * 2]
                actor.session.submit(cmd.MoveToken(
                    pawn_id=pawn.id, x=tile.position[0], y=tile.position[1],
                    tile_index=tile.index))
                attempt.pump()
            target = room.state.library.pawns[0].id
            actor.session.submit(cmd.UseAbility(
                player_index=randy, source="character",
                choices={"pawn": target}))
            attempt.pump()
            if not room.state.statuses.pawn_has(StatusKind.FROZEN, target):
                continue

            everybody = [host, *clients]
            for service in everybody:
                service.drain_notices()
            agreed = fingerprint_of(room.state.snapshot())
            assert all(fingerprint_of(s.state.snapshot()) == agreed
                       for s in everybody)

            # THE DRAG THAT USED TO POISON THE TABLE.
            actor.session.submit(cmd.MoveToken(pawn_id=target, x=1.0, y=2.0,
                                               tile_index=99_999))
            attempt.pump()
            assert fingerprint_of(room.state.snapshot()) == agreed
            assert room.state.statuses.pawn_has(StatusKind.FROZEN, target)

            # ...and then anything at all, which is where it used to show.
            seat = room.state.active_player_index
            by_seat[seat].session.submit(cmd.EndTurn(player_index=seat))
            for _ in range(4):
                attempt.pump()

            authoritative = fingerprint_of(room.state.snapshot())
            for service in everybody:
                assert fingerprint_of(service.state.snapshot()) == authoritative
                assert not [n for n in service.drain_notices()
                            if "różni się" in n or "Rozjazd" in n]
            assert replay_fingerprint(room, library) == authoritative
            return
        finally:
            attempt.close()
    pytest.fail("nie trafił się stół z Big D Randy w 80 próbach")


# ── E/F. legal manual movement is untouched ──────────────────────────────────
def test_a_manual_move_onto_a_real_field_still_works_everywhere(table):
    host, clients, room = a_started_room(table)
    seat = room.state.active_player_index
    actor = table.by_seat(host, clients)[seat]
    pawn = room.state.library.pawns[0].id
    tile = room.state.board.tiles[3]

    actor.session.submit(cmd.MoveToken(pawn_id=pawn, x=tile.position[0],
                                       y=tile.position[1],
                                       tile_index=tile.index))
    table.pump()

    assert room.state.board.pawn_tiles.get(pawn) == tile.index
    authoritative = fingerprint_of(room.state.snapshot())
    for service in [host, *clients]:
        assert fingerprint_of(service.state.snapshot()) == authoritative
        assert service.state.board.pawn_tiles.get(pawn) == tile.index
    assert room.command_log[-1]["kind"] == "move_token"


def test_a_manual_move_with_no_field_still_takes_the_pawn_off_the_board(table):
    """CURRENT INTENDED BEHAVIOUR, pinned rather than changed.

    Free placement is the prototype's drag-anywhere tool and it genuinely
    removes the pawn from ``pawn_tiles``, which ``effects.pawn_index`` reads as
    ``CAMP_INDEX``.  Whether that should stay is a question for a stage of its
    own; this test exists so that stage 51 can be shown NOT to have answered
    it.
    """
    host, clients, room = a_started_room(table)
    seat = room.state.active_player_index
    actor = table.by_seat(host, clients)[seat]
    pawn = room.state.library.pawns[0].id

    actor.session.submit(cmd.MoveToken(pawn_id=pawn, x=123.5, y=-45.25,
                                       tile_index=None))
    table.pump()

    assert pawn not in room.state.board.pawn_tiles
    assert room.state.tokens[pawn].position == (123.5, -45.25)
    authoritative = fingerprint_of(room.state.snapshot())
    for service in [host, *clients]:
        assert fingerprint_of(service.state.snapshot()) == authoritative
