"""
UNDO OVER THE WIRE.

``UndoMove`` was written as a player's own action — its docstring says so in as
many words — and was then listed in ``AUTHORITY_ONLY``, which is the tuple for
commands a client sending one is CHEATING by sending.  ``authorise_remote``
tests that tuple first, so every online undo came back
"Tę decyzję podejmuje serwer" while the button that sent it stayed lit, because
the interface asks ``can_undo`` and ``can_undo`` was perfectly happy.

Nothing else was wrong.  Ownership was already enforced twice over — by
``authorise_remote``'s seat map, from the host's own table rather than from the
message, and by ``can_undo`` inside the handler — so these tests are mostly
about proving that the existing enforcement really does hold once the command
is allowed through, and that a rewind replays.

THE INVARIANT worth more than the rest put together:

    after an accepted UndoMove, every replica has the same fingerprint,
    and replaying the command log reproduces it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from netkit import Table, playable_card, replay_fingerprint
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.victory import MatchPhase
from pedzacy_piotrek.net.protocol import fingerprint_of


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library):
    made = Table(library)
    yield made
    made.close()


def maybe_playable_card(service, seat: int):
    """``netkit.playable_card`` with "there isn't one" as an answer.

    It raises ``StopIteration`` on a hand holding nothing but Troll and
    Stańczyk, which is a legitimate deal rather than a broken test.
    """
    try:
        return playable_card(service, seat)
    except StopIteration:
        return None


class Window:
    """A three-player table whose active seat has just played a card.

    Built by opening tables until one deals a card that RESOLVES — the deal is
    seeded by the server, and a card that asks a question (a direction, a
    target) returns CHOICE_REQUIRED, which is not logged and opens no window.
    Retrying the deal is the idiom ``test_multiplayer.py`` already uses, and it
    exercises the real start path rather than reaching behind it.
    """

    def __init__(self, table, host, clients, room, seat, actor, card, before):
        self.table, self.host, self.clients = table, host, clients
        self.room, self.seat, self.actor = room, seat, actor
        self.card, self.before = card, before

    @property
    def everybody(self):
        return [self.host, *self.clients]

    def pump(self):
        self.table.pump()

    def fingerprint(self) -> str:
        return fingerprint_of(self.room.state.snapshot())


@pytest.fixture
def window(library):
    """A table with an open undo window, closed again afterwards."""
    for _ in range(40):
        table = Table(library)
        host, clients = table.playing("Kuba", "Ola", "Norbert")
        room = table.room(host.room_code)
        seat = room.state.active_player_index
        actor = table.by_seat(host, clients)[seat]
        card = maybe_playable_card(actor, seat)
        if card is None:
            table.close()
            continue
        before = fingerprint_of(room.state.snapshot())
        actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=card.uid))
        table.pump()
        if room.state.can_undo(seat):
            made = Window(table, host, clients, room, seat, actor, card, before)
            yield made
            table.close()
            return
        table.close()
    pytest.fail("nie trafił się rozdanie otwierające okno cofnięcia")


# ── 1. the command is allowed through at all ─────────────────────────────────
def test_undo_is_accepted_from_a_client(window):
    """THE BUG.  The server used to answer this with 'that is the server's call'."""
    room, seat, actor = window.room, window.seat, window.actor
    log_before = len(room.command_log)
    for service in window.everybody:
        service.drain_notices()

    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    assert room.command_log[-1]["kind"] == "undo_move", (
        "the undo did not reach the log: " + repr(
            [n for s in window.everybody for n in s.drain_notices()]))
    assert len(room.command_log) == log_before + 1
    assert not room.state.can_undo(seat), "the window is spent"


def test_the_undo_is_a_command_and_nothing_else(window):
    """No side channel: the rewind travels as a logged, replayable command.

    Stated as a test because "do not add a special undo message" is easy to
    honour today and easy to lose later.
    """
    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.pump()

    logged = [c for c in window.room.command_log if c["kind"] == "undo_move"]
    assert len(logged) == 1
    assert logged[0].get("player_index") == window.seat


# ── 2. every replica lands in the same place ─────────────────────────────────
def test_undo_rewinds_every_replica_identically(window):
    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.pump()

    authoritative = window.fingerprint()
    for service in window.everybody:
        assert fingerprint_of(service.state.snapshot()) == authoritative
        assert not [n for n in service.drain_notices()
                    if "różni się" in n or "Rozjazd" in n]


def test_undo_puts_the_table_back_where_it_was(window):
    """The rewind is a rewind, not merely a shared state."""
    assert window.fingerprint() != window.before, "the card did nothing"

    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.pump()

    assert window.fingerprint() == window.before
    assert window.actor.state.player(window.seat).card_by_uid(
        window.card.uid) is not None, "the card is back in the hand"


# ── 3/4. the refusals, which were already in the engine ──────────────────────
def test_undo_from_a_foreign_seat_is_refused(window):
    """Two independent guards, and this exercises both.

    ``authorise_remote`` compares the command's ``player_index`` with the seat
    the HOST has for that connection, so a client cannot claim to be somebody
    else; ``can_undo`` then compares the seat with the window's owner.  A
    forged command has to get past both, and gets past neither.
    """
    room, seat = window.room, window.seat
    by_seat = window.table.by_seat(window.host, window.clients)
    thief = next(s for s in by_seat if s != seat)
    thief_service = by_seat[thief]
    log_before = len(room.command_log)
    before = window.fingerprint()

    # Honestly, from the thief's own seat: refused because the window is not
    # theirs — ``can_undo``.
    thief_service.session.submit(cmd.UndoMove(player_index=thief))
    window.pump()
    assert len(room.command_log) == log_before
    assert window.fingerprint() == before

    # Forged, claiming the owner's seat: refused before the engine looks,
    # because the seat comes from the host's map — ``authorise_remote``.
    thief_service.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()
    assert len(room.command_log) == log_before
    assert window.fingerprint() == before
    assert room.state.can_undo(seat), "the owner's window is untouched"


def test_undo_after_window_closed_is_refused(window):
    """The next card played closes it — that is the whole rule."""
    room, seat, actor = window.room, window.seat, window.actor
    by_seat = window.table.by_seat(window.host, window.clients)

    actor.session.submit(cmd.EndTurn(player_index=seat))
    window.pump()
    for _ in range(20):
        other = room.state.active_player_index
        if other == seat:
            by_seat[other].session.submit(cmd.EndTurn(player_index=other))
            window.pump()
            continue
        card = maybe_playable_card(by_seat[other], other)
        if card is None:
            by_seat[other].session.submit(cmd.EndTurn(player_index=other))
            window.pump()
            continue
        by_seat[other].session.submit(cmd.PlayCard(player_index=other,
                                                   card_uid=card.uid))
        window.pump()
        break
    if room.state.can_undo(seat):
        pytest.skip("nikt inny nie zdążył zagrać karty")

    log_before = len(room.command_log)
    before = window.fingerprint()
    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    assert len(room.command_log) == log_before
    assert window.fingerprint() == before


def test_undo_cannot_be_played_twice(window):
    """One window, one rewind.  A client sending it again changes nothing."""
    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.pump()

    log_before = len(window.room.command_log)
    before = window.fingerprint()
    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.pump()

    assert len(window.room.command_log) == log_before
    assert window.fingerprint() == before


# ── 5. it replays ────────────────────────────────────────────────────────────
def test_undo_replays_from_log_for_a_reconnecting_client(window, library):
    """What a client rebuilds on STATE_SYNC has to be where the server is.

    The undo is in the log like any other command, so a rebuild applies it in
    order.  This is the test that would catch a server-side rollback that never
    made it into the log.
    """
    window.actor.session.submit(cmd.UndoMove(player_index=window.seat))
    window.pump()

    assert replay_fingerprint(window.room, library) == window.fingerprint()


def test_the_table_keeps_playing_after_a_rewind(window, library):
    """A rewind is not the end of the match: the corrective turn replays too."""
    room, seat, actor = window.room, window.seat, window.actor
    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    card = maybe_playable_card(actor, seat)
    if card is not None:
        actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=card.uid))
        window.pump()
    if room.state.active_player_index == seat:
        actor.session.submit(cmd.EndTurn(player_index=seat))
        window.pump()

    authoritative = window.fingerprint()
    for service in window.everybody:
        assert fingerprint_of(service.state.snapshot()) == authoritative
    assert replay_fingerprint(room, library) == authoritative


def test_the_same_card_comes_back_and_can_be_played_again(window):
    """``undo.restore`` puts the draw pile back in order, so the corrective
    turn draws the same card — the promise the module docstring makes."""
    room, seat, actor = window.room, window.seat, window.actor

    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    assert actor.state.player(seat).card_by_uid(window.card.uid) is not None
    order = [c.uid for c in room.state.decks["movement"].draw_pile]
    for service in window.everybody:
        assert [c.uid for c in service.state.decks["movement"].draw_pile] == order


def test_a_pawn_rewound_into_the_camp_goes_back_to_its_camp_slot(window):
    """FOUND WHILE ENABLING THIS, and fixed in ``undo.py`` rather than here.

    The checkpoint stored MEMBERSHIP for every pawn and a POSITION for none.
    That is enough for a pawn on a field — ``_sync_token_positions`` recomputes
    it from the tile and the tower — but a pawn back in the camp has no tile to
    be recomputed from, so it kept the position the undone card gave it: in the
    camp by the rules, and drawn on field 1.  Pre-existing and purely local;
    online undo would have spread it to every player at the table.
    """
    room, seat, actor = window.room, window.seat, window.actor
    stranded = [pawn_id for pawn_id in room.state.board.pawn_tiles]
    if not stranded:
        pytest.skip("ta karta nie wyprowadziła pionka z obozu")

    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    camp_slots = {room.state.board.camp_position(i)
                  for i in range(len(room.state.library.pawns))}
    for pawn_id in stranded:
        if pawn_id in room.state.board.pawn_tiles:
            continue                    # it was on the board before the card
        assert room.state.tokens[pawn_id].position in camp_slots, (
            f"{pawn_id} jest w obozie, a stoi na planszy")
        assert room.state.tokens[pawn_id].tile_index is None


# ── 6. the generator goes back too ───────────────────────────────────────────
def test_undo_restores_rng_deterministically(window):
    """``undo.capture`` stores ``rng.getstate()`` and ``restore`` puts it back.

    Checked through the ROOM rather than by reading the generator alone,
    because the property that matters is that the replicas keep agreeing after
    the rewind — a generator that came back to a different place would show up
    as the next seeded decision differing.
    """
    room, seat, actor = window.room, window.seat, window.actor
    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    states = [s.state.rng.getstate() for s in window.everybody]
    assert all(rng == states[0] for rng in states)
    assert room.state.rng.getstate() == states[0]


# ── 7. the victory boundary ──────────────────────────────────────────────────
def test_a_finished_match_cannot_be_rewound(window):
    """DECIDED BY THE EXISTING CODE, not by this stage.

    ``can_undo`` requires ``self.phase.playable``, which is ``PLAYING`` and
    nothing else, and ``_phase_refusal`` answers "Gra została zakończona" for
    ``ENDED``.  The window is therefore closed by a declared victory in the
    engine that was already there — before and after this change — so an undo
    can never cross a ``DeclareVictory`` in the log.  Pinned here because the
    reasoning is easy to lose and the consequence is not obvious.
    """
    room, seat, actor = window.room, window.seat, window.actor
    assert room.state.can_undo(seat)

    finish = [t for t in room.state.board.tiles if t.kind.value == "finish"][0]
    mover = window.table.by_seat(window.host, window.clients)[
        room.state.active_player_index]
    mover.session.submit(cmd.MoveToken(
        pawn_id=room.state.piotrek_pawn, x=finish.position[0],
        y=finish.position[1], tile_index=finish.index))
    window.pump()
    if room.state.phase is not MatchPhase.ENDED:
        pytest.skip("ten wariant zwycięstwa nie zakończył partii")
    assert not room.state.can_undo(seat)

    log_before = len(room.command_log)
    before = window.fingerprint()
    actor.session.submit(cmd.UndoMove(player_index=seat))
    window.pump()

    assert len(room.command_log) == log_before, "no undo past a verdict"
    assert window.fingerprint() == before
    assert room.state.victory is not None


def test_can_undo_still_refuses_while_a_colour_is_being_chosen(library):
    """The other half of the same gate, kept working after the reclassification.

    ``can_undo`` checks ``awaiting_identity`` ITSELF rather than leaning on
    ``_phase_refusal``, and it has to keep doing so: replay applies commands
    with ``local=False``, which runs no authorisation at all, so the handler's
    own check is the only one a replaying client ever sees.
    """
    from pedzacy_piotrek.config.settings import SessionConfig
    from pedzacy_piotrek.engine.setup import create_game

    game = create_game(SessionConfig(num_players=4, board_cells=30, seed=77,
                                     chest_open_round=10_000,
                                     mod_round_first=10_000), library)
    for index, pawn in enumerate(library.pawns):
        game.board.place_pawn(pawn.id,
                              game.board.positions[index + 1].tiles[0].index)
    game._sync_token_positions()
    seat = game.active_player_index
    card = next((c for c in game.player(seat).hand
                 if c.deck_id == "movement" and not c.locked), None)
    if card is None:
        pytest.skip("rozdanie bez karty ruchu")
    game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    if not game.can_undo(seat):
        pytest.skip("karta nie otworzyła okna")

    game.identity_swap = game.SWAP_REVEALING
    assert game.awaiting_identity
    assert not game.can_undo(seat), "no rewinding out of an identity swap"
    events = game.apply(cmd.UndoMove(player_index=seat), local=False)
    assert any(isinstance(e, ev.ActionRejected) for e in events)


# ── 8. the pending decisions the checkpoint carries ──────────────────────────
def test_the_checkpoint_carries_the_pending_decisions():
    """``pending_movement``/``_check``/``_breakup`` are in ``undo._SCALARS``.

    So a rewind puts them back rather than leaving a decision armed against a
    board that no longer exists.  Asserted against the list itself because the
    list IS the design: a field added to GameState is meant to be a deliberate
    decision about whether a turn may change it, and this is the reminder.
    """
    from pedzacy_piotrek.engine import undo

    for name in ("pending_movement", "pending_check", "pending_breakup",
                 "pending_lead_check", "pending_pawn_check",
                 "pending_mod_selection", "phase", "victory"):
        assert name in undo._SCALARS, f"{name} would survive a rewind"
