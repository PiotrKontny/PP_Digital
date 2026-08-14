"""
Victory, defeat, and the secret in between (stage 17).

Three layers, tested where each of them actually lives:

* the rules, against a bare :class:`GameState` — no server, no screen;
* the synchronisation, against the real server through ``netkit.Table``;
* the secrecy, which is the one property that cannot be checked by looking at
  the winner: it has to be checked by looking at what every OTHER machine
  knows while the match is still running.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from netkit import Table, playable_card, take_a_turn        # noqa: E402

from pedzacy_piotrek.board.tiles import TileKind             # noqa: E402
from pedzacy_piotrek.cards.loader import ContentLibrary      # noqa: E402
from pedzacy_piotrek.config.settings import SessionConfig    # noqa: E402
from pedzacy_piotrek.engine import commands as cmd           # noqa: E402
from pedzacy_piotrek.engine import victory                   # noqa: E402
from pedzacy_piotrek.engine.setup import (                   # noqa: E402
    create_game, starting_hand_size,
)
from pedzacy_piotrek.engine.victory import MatchPhase, Outcome  # noqa: E402
from pedzacy_piotrek.net.session import LocalSession         # noqa: E402


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def state(library):
    """A hot-seat game: the colour is dealt, so this copy is the authority."""
    return create_game(SessionConfig(num_players=4, board_cells=12, seed=7),
                       library)


# ── helpers ──────────────────────────────────────────────────────────────────
def finish_tile(state):
    return next(t for t in state.board.tiles if t.kind is TileKind.FINISH)


def stack_everyone_on(state, tile_index: int, bottom: str) -> None:
    """Build the tower the hunters have to build, with a chosen pawn beneath.

    ICE BLOCK IS ANSWERED HERE.  Piotrek is dealt a skill at setup, and when
    that skill is Ice Block the engine now offers him a window before any check
    resolves — correct, and tested on its own in ``test_stage40_ice_block``.
    These tests are about what a check DECIDES, so the window is opened and
    allowed, which is the path that leaves the check exactly as it was.
    """
    order = [bottom] + [p.id for p in state.library.pawns if p.id != bottom]
    for pawn_id in order:
        state.board.place_pawn(pawn_id, tile_index)
    state._sync_token_positions()
    allow_any_check(state)


def allow_any_check(state) -> None:
    """Open and allow an Ice Block window if one is due.  Costs no use."""
    pending = victory.review(state)
    if pending and pending[0].kind == "open_check_decision":
        state.apply(pending[0], local=False)
        state.apply(cmd.AllowCheck(player_index=state.pending_check.seat),
                    local=False)


# ── the rules ────────────────────────────────────────────────────────────────
def test_piotrek_wins_when_his_pawn_reaches_the_finish(state):
    hidden = state.piotrek_pawn
    assert hidden, "a hot-seat game deals the colour"
    state.board.place_pawn(hidden, finish_tile(state).index)

    followed = victory.review(state)
    assert [c.kind for c in followed] == ["declare_victory"]
    assert followed[0].outcome == Outcome.PIOTREK.value
    assert followed[0].pawn_id == hidden


def test_another_pawn_reaching_the_finish_ends_nothing(state):
    other = next(p.id for p in state.library.pawns if p.id != state.piotrek_pawn)
    state.board.place_pawn(other, finish_tile(state).index)
    assert victory.review(state) == []


def test_hunters_win_when_the_tower_stands_on_piotrek(state):
    hidden = state.piotrek_pawn
    stack_everyone_on(state, 3, bottom=hidden)

    followed = victory.review(state)
    assert [c.kind for c in followed] == ["declare_victory"]
    assert followed[0].outcome == Outcome.HUNTERS.value
    assert followed[0].pawn_id == hidden


def test_a_wrong_tower_eliminates_that_colour_and_play_goes_on(state):
    wrong = next(p.id for p in state.library.pawns if p.id != state.piotrek_pawn)
    stack_everyone_on(state, 3, bottom=wrong)

    followed = victory.review(state)
    assert [c.kind for c in followed] == ["eliminate_pawn"]
    assert followed[0].pawn_id == wrong

    state.apply(followed[0], local=False)
    assert state.eliminated_pawns == [wrong]
    assert state.phase is MatchPhase.PLAYING, "the game continues"


def test_an_eliminated_colour_is_never_checked_twice(state):
    wrong = next(p.id for p in state.library.pawns if p.id != state.piotrek_pawn)
    stack_everyone_on(state, 3, bottom=wrong)
    state.apply(cmd.EliminatePawn(pawn_id=wrong), local=False)
    # The tower has not moved; nothing may happen a second time.
    assert victory.review(state) == []


def test_a_tower_short_of_one_pawn_is_not_a_check(state):
    hidden = state.piotrek_pawn
    stack_everyone_on(state, 3, bottom=hidden)
    stray = next(p.id for p in state.library.pawns if p.id != hidden)
    state.board.place_pawn(stray, 4)
    assert victory.review(state) == [], "all of them, or it is not a check"


def test_a_replica_that_does_not_know_the_secret_decides_nothing(state):
    """The safety net under the whole design.

    Every client runs the same code with the same board; what stops five
    machines declaring five winners is that only the authority holds the
    colour.
    """
    hidden = state.piotrek_pawn
    stack_everyone_on(state, 3, bottom=hidden)
    state.player(state.piotrek_seat).secret_pawn = None      # a client's copy
    assert victory.review(state) == []


def test_nothing_is_decided_before_the_match_begins(state):
    state.phase = MatchPhase.STARTING
    state.board.place_pawn(state.piotrek_pawn, finish_tile(state).index)
    assert victory.review(state) == []


def test_a_finished_match_is_not_judged_again(state):
    hidden = state.piotrek_pawn
    state.board.place_pawn(hidden, finish_tile(state).index)
    state.apply(victory.review(state)[0], local=False)
    assert state.finished
    assert victory.review(state) == [], "one ending per match"


# ── the state after a verdict ────────────────────────────────────────────────
def test_the_verdict_reveals_the_colour_and_stops_the_game(state):
    hidden = state.piotrek_pawn
    seat = state.piotrek_seat
    state.board.place_pawn(hidden, finish_tile(state).index)
    state.apply(victory.review(state)[0], local=False)

    assert state.victory is not None and state.victory.piotrek_won
    assert state.victory.pawn_id == hidden
    assert state.victory.piotrek_seat == seat
    assert state.phase is MatchPhase.ENDED

    refused = state.apply(cmd.EndTurn(player_index=state.active_player_index))
    assert refused and refused[0].name == "ActionRejected"


def test_the_ending_is_part_of_the_snapshot(state):
    """So the fingerprint catches a machine that missed it."""
    before = state.snapshot()
    state.board.place_pawn(state.piotrek_pawn, finish_tile(state).index)
    state.apply(victory.review(state)[0], local=False)
    assert state.snapshot() != before
    assert state.snapshot()["victory"]["outcome"] == Outcome.PIOTREK.value


def test_the_snapshot_cannot_tell_which_colour_is_hidden(state):
    """What lets ONE machine know more than the others without desyncing.

    Every pawn id is in a snapshot — they are all on the board.  The property
    that matters is finer: two states that differ ONLY in Piotrek's secret must
    fingerprint identically, or the server would drift from every client the
    moment it was told, and five people would resync for ever.
    """
    seat = state.piotrek_seat
    original = state.piotrek_pawn
    knowing = state.snapshot()

    state.player(seat).secret_pawn = None                   # a client's copy
    assert state.snapshot() == knowing, "not knowing looks the same"

    other = next(p.id for p in state.library.pawns if p.id != original)
    state.player(seat).secret_pawn = other                  # a different secret
    assert state.snapshot() == knowing, "and so does knowing something else"


# ── a hot-seat game runs itself ──────────────────────────────────────────────
def test_a_local_session_notices_its_own_winner(state):
    session = LocalSession(state)
    state.board.place_pawn(state.piotrek_pawn, finish_tile(state).index)
    seat = state.active_player_index
    session.submit(cmd.EndTurn(player_index=seat))
    assert state.finished, "one machine is its own authority"


# ── over the network ─────────────────────────────────────────────────────────
@pytest.fixture
def table(library):
    table = Table(library)
    yield table
    table.close()


def test_only_piotrek_is_asked_for_the_colour(table):
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    asked = [s for s in [host, *clients] if s.identity_request]
    assert len(asked) == 1, "one question, one player"

    seat = asked[0].session.seat
    assert host.state.player(seat).is_piotrek


def test_nobody_moves_until_the_colour_is_chosen(table):
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    assert all(s.state.phase is MatchPhase.STARTING for s in [host, *clients])

    seat = host.state.active_player_index
    actor = table.by_seat(host, clients)[seat]
    card = playable_card(actor, seat)
    actor.session.submit(cmd.DiscardCard(player_index=seat, card_uid=card.uid))
    table.pump()
    assert len(host.state.player(seat).hand) == len(card and actor.state.player(seat).hand)
    assert host.state.phase is MatchPhase.STARTING


def test_everyone_starts_at_the_same_moment(table):
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    table.choose_identity(host, clients)
    assert all(s.state.phase is MatchPhase.PLAYING for s in [host, *clients])


def test_the_colour_reaches_the_server_and_nobody_else(table):
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    chosen = table.choose_identity(host, clients)
    room = table.room(host.room_code)

    assert room.state.piotrek_pawn == chosen, "the server knows"
    piotrek = table.piotrek(host, clients) or host
    for service in [host, *clients]:
        known = service.state.piotrek_pawn
        if service is piotrek or service.identity_pawn:
            continue
        assert known is None, "no other machine holds the secret"


def test_the_secret_is_not_in_the_command_log(table):
    """Because the log is replayed to whoever asks for a sync."""
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    chosen = table.choose_identity(host, clients)
    room = table.room(host.room_code)
    assert chosen not in repr(room.command_log)


def test_a_client_cannot_declare_itself_the_winner(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    host.session.submit(cmd.DeclareVictory(outcome="piotrek", pawn_id="zielony",
                                           piotrek_seat=host.session.seat))
    table.pump()
    assert host.state.victory is None
    assert all(s.state.victory is None for s in clients)


def test_a_win_reaches_every_machine(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    hidden = room.state.piotrek_pawn
    room.state.board.place_pawn(hidden, finish_tile(room.state).index)
    room.state._sync_token_positions()

    take_a_turn(table, host, clients)       # any accepted command triggers the review
    for service in [host, *clients]:
        assert service.state.victory is not None, "everybody was told"
        assert service.state.victory.pawn_id == hidden, "and told what was hidden"
        assert service.state.finished


def test_a_failed_check_crosses_the_colour_off_everywhere(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    wrong = next(p.id for p in room.state.library.pawns
                 if p.id != room.state.piotrek_pawn)
    stack_everyone_on(room.state, 3, bottom=wrong)

    take_a_turn(table, host, clients)
    for service in [host, *clients]:
        assert service.state.eliminated_pawns == [wrong]
        assert service.state.victory is None, "play continues"


def test_the_machines_still_agree_after_a_verdict(table):
    """The fingerprint is the whole desync story, and a verdict is state."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)

    for service in clients:
        assert service.state.snapshot() == host.state.snapshot()


def test_a_player_who_reconnects_arrives_at_the_ending(table):
    """The verdict is in the log, so replaying the log replays the ending."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)

    latecomer = clients[-1]
    latecomer.client.request_sync()
    table.pump()
    assert latecomer.state.finished
    assert latecomer.state.victory.pawn_id == host.state.victory.pawn_id


def test_the_room_reopens_as_a_lobby_when_asked(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)

    host.return_to_lobby()
    table.pump()
    assert room.state is None and not room.lobby.started
    for service in [host, *clients]:
        assert service.session is None, "everybody leaves the table together"
    assert len(room.lobby.seats) == 3, "the same people are still sitting there"


def test_a_room_cannot_be_reset_mid_match(table):
    """Otherwise one player could end everybody else's game."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    clients[0].return_to_lobby()
    table.pump()
    assert table.room(host.room_code).state is not None
    assert host.session is not None


def test_the_next_match_gets_a_fresh_secret(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)

    host.return_to_lobby()
    table.pump()
    host.start_game(table.library)
    table.pump()
    assert room.state.piotrek_pawn is None, "nobody has chosen yet"
    assert not room.identity_settled
    assert table.piotrek(host, clients) is not None, "and somebody is being asked"


# ── stage 18: one flow for the identity, and a room that really resets ───────
def test_a_hot_seat_game_asks_too(library):
    """The bug behind stage 18: only online matches were asking.

    A single-machine game dealt the colour from the seed and started, so the
    picker existed and was never seen by anybody testing alone.
    """
    from pedzacy_piotrek.ui.menu import MenuScreen        # noqa: F401 (import guard)

    config = SessionConfig(num_players=4, board_cells=12, seed=3,
                           piotrek_picks_pawn=True)
    state = create_game(config, library)
    assert state.piotrek_pawn is None, "nothing was dealt behind the player's back"
    assert state.phase is MatchPhase.STARTING


def test_the_seed_no_longer_gives_the_colour_away(library):
    """Two clients building the same match must not be able to compute it.

    They build from one seed, so anything the setup deals is public knowledge
    dressed up as a secret.
    """
    config = SessionConfig(num_players=4, board_cells=12, seed=99,
                           piotrek_picks_pawn=True)
    first, second = create_game(config, library), create_game(config, library)
    assert first.piotrek_pawn is None and second.piotrek_pawn is None


def test_the_random_draw_survives_only_where_nobody_can_click(library):
    """--players and --selftest still need a playable table with no interface."""
    state = create_game(SessionConfig(num_players=4, board_cells=12, seed=3),
                        library)
    assert state.piotrek_pawn, "the debugging fallback still deals one"
    assert state.phase is MatchPhase.PLAYING


def test_the_colour_is_chosen_once_and_only_once(table):
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    piotrek = table.piotrek(host, clients)
    pawns = [p["id"] for p in piotrek.identity_request]

    piotrek.choose_identity(pawns[0])
    table.pump()
    piotrek.choose_identity(pawns[1])        # a second attempt, or a stray click
    table.pump()

    room = table.room(host.room_code)
    assert room.state.piotrek_pawn == pawns[0], "the first answer stands"
    assert piotrek.error, "and the second is refused out loud"


def test_a_hunter_cannot_answer_for_piotrek(table):
    host, clients = table.starting("Kuba", "Ola", "Norbert")
    piotrek = table.piotrek(host, clients)
    hunter = next(s for s in [host, *clients] if s is not piotrek)

    hunter.choose_identity("zielony")
    table.pump()
    room = table.room(host.room_code)
    assert room.state.piotrek_pawn is None
    assert room.state.phase is MatchPhase.STARTING, "nothing started"


def test_piotrek_is_told_his_colour_again_after_reconnecting(table):
    """His replica is rebuilt from the seed and the log, and it is in neither."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    piotrek = next(s for s in [host, *clients] if s.identity_pawn)
    colour = piotrek.identity_pawn

    room = table.room(host.room_code)
    room.mark_absent(piotrek.peer_id)
    for outbound in room.catch_up(piotrek.peer_id):
        piotrek.client._handle(outbound[1])

    assert piotrek.identity_pawn == colour
    assert piotrek.state.piotrek_pawn == colour, "the badge has something to draw"
    for other in [host, *clients]:
        if other is not piotrek:
            assert other.state.piotrek_pawn is None


def _fingerprint_of_everything(state):
    """Everything a second match must not inherit from the first."""
    return {
        "round": state.round_number,
        "turn": state.turn_counter,
        "slot": state.turn_slot,
        "active": state.active_player_index,
        "phase": state.phase.value,
        "victory": state.victory,
        "eliminated": list(state.eliminated_pawns),
        "secret": state.piotrek_pawn,
        "mods": [c.uid if c else None for c in state.mod_slots],
        "statuses": state.statuses.to_list(),
        "tiles": {pid: tile for pid, tile in state.board.pawn_tiles.items()},
        "decks": {d: (deck.draw_count, deck.discard_count)
                  for d, deck in state.decks.items()},
        "hands": [len(p.hand) for p in state.players],
        "uses": [[c.uses_left for c in (p.character, p.skill) if c]
                 for p in state.players],
    }


def test_the_next_match_starts_from_nothing(table):
    """A rematch has to be a fresh table, not a tidied-up one."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    fresh = _fingerprint_of_everything(room.state)

    # Make a mess: play a dozen turns, cross a colour off, then win.
    for _ in range(12):
        take_a_turn(table, host, clients)
    room.state.apply(cmd.EliminatePawn(pawn_id=room.state.library.pawns[0].id),
                     local=False)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)
    assert room.state.finished
    dirty = _fingerprint_of_everything(room.state)
    assert dirty != fresh, "the match really did leave marks"

    host.return_to_lobby()
    table.pump()
    host.start_game(table.library)
    table.pump()
    again = _fingerprint_of_everything(room.state)

    assert again["round"] == 1 and again["turn"] == 0 and again["slot"] == 0
    assert again["victory"] is None and again["eliminated"] == []
    assert again["secret"] is None, "and a new colour to choose"
    assert again["mods"] == [None, None]
    assert again["statuses"] == []
    assert again["phase"] == MatchPhase.STARTING.value
    assert not again["tiles"], "every pawn is back in the camp"
    for deck_id, (draw, discard) in again["decks"].items():
        assert discard == 0, f"{deck_id} discard pile is empty again"
    # NOT compared against the first match's numbers: a rematch gets a new seed,
    # so the characters are dealt again — Piotrek can land on another seat, and
    # his opening hand is five or three depending on which skill he draws
    # (ChatGPT trades two cards for its range).  What must hold is that every
    # hand is the OPENING hand the setup would deal, not whatever was left in
    # it when the last match ended.
    assert again["hands"] == [starting_hand_size(p) for p in room.state.players]
    assert all(uses and all(u is None or u > 0 for u in uses)
               for uses in again["uses"]), "ability counters back to full"


def test_a_rematch_keeps_the_players_and_the_connection(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)

    host.return_to_lobby()
    table.pump()
    assert all(s.disconnected is None for s in [host, *clients]), "still connected"
    assert [s.nickname for s in room.lobby.seats] == ["Kuba", "Ola", "Norbert"]

    host.start_game(table.library)
    table.pump()
    assert all(s.session is not None for s in [host, *clients]), "no reconnecting"
    assert table.piotrek(host, clients) is not None, "and somebody is asked again"


def test_a_second_match_does_not_inherit_the_first_colour(table):
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    first = next(s for s in [host, *clients] if s.identity_pawn).identity_pawn
    room = table.room(host.room_code)
    room.state.board.place_pawn(room.state.piotrek_pawn,
                                finish_tile(room.state).index)
    room.state._sync_token_positions()
    take_a_turn(table, host, clients)

    host.return_to_lobby()
    table.pump()
    host.start_game(table.library)
    table.pump()
    assert all(not s.identity_pawn for s in [host, *clients]), \
        f"nobody still believes they are {first}"
    assert all(s.state.piotrek_pawn is None for s in [host, *clients])
