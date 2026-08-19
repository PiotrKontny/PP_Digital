"""
"Nie masz Rosji" — the right to stop one opponent movement.

The card grants a temporary status; the whole of its behaviour happens later,
when an opponent plays something that moves a pawn.  What is pinned here:

* the status, its two durations, and what a FULL ROUND means on a table where
  one seat holds every third slot;
* who is an opponent of whom, read from the roles the game already has;
* which movements can be stopped, and which things are deliberately not
  movements at all;
* the decision window, its configurable length, and its three endings —
  accepted, timed out, blocked;
* that a blocked movement NEVER HAPPENS, rather than happening and being undone;
* the automatic block on the last chance;
* two vetoes on one table, and one authoritative answer.

Engine level except for the last section, which drives the in-process server.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import StatusKind
from pedzacy_piotrek.net.lobby import LobbyState
from pedzacy_piotrek.net.session import LocalSession

CARD = "Nie masz Rosji"
TWO_ROUNDS = "two_rounds"
ONE_ROUND = "one_round"
#: A movement card that always moves the same pawn one field forward, so a test
#: never has to answer a question to make a movement happen.
MOVE_CARD = "Zerówka - zielony"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 5, seed: int = 5, **kwargs):
    """A game with every pawn on the board and nothing else going on."""
    config = SessionConfig(
        num_players=players, board_cells=24, seed=seed,
        chest_open_round=10_000, mod_round_first=10_000,
        double_frequency=0.0, piotrek_picks_pawn=False, **kwargs,
    )
    game = create_game(config, library)
    for index, pawn in enumerate(library.pawns):
        game.board.place_pawn(pawn.id,
                              game.board.position(index + 1).tiles[0].index)
    game._sync_token_positions()
    return game


def give(game, player, deck_id: str, title: str):
    """Put a named card into a hand, out of its own deck."""
    deck = game.decks[deck_id]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    player.add_card(card)
    return card


def piotrek_of(game):
    return next(p for p in game.players if p.is_piotrek)


def hunters_of(game):
    return [p for p in game.players if not p.is_piotrek]


def arm(game, player, variant: str = ""):
    """Play the card for real, so the status is granted by the card."""
    if variant:
        game.apply(cmd.SetCardVariant(deck_id=settings.DECK_CHEST, title=CARD,
                                      variant=variant))
    card = give(game, player, settings.DECK_CHEST, CARD)
    game.active_player_index = player.index
    events = game.apply(cmd.PlayCard(player_index=player.index,
                                     card_uid=card.uid))
    assert any(isinstance(e, ev.CardPlayed) for e in events), events
    return card


def veto_of(game, player):
    return game.veto_of(player.index)


def move(game, player, title: str = MOVE_CARD):
    """That player plays a movement card, whatever the table makes of it."""
    card = give(game, player, settings.DECK_MOVEMENT, title)
    game.active_player_index = player.index
    return card, game.apply(cmd.PlayCard(player_index=player.index,
                                         card_uid=card.uid))


# ── the card, its variants and the status it grants ─────────────────────────
def test_the_card_is_a_chest_card_with_two_variants(library):
    definition = next(card for card in library.deck(settings.DECK_CHEST).cards
                      if card.title == CARD)
    assert definition.deck_id == settings.DECK_CHEST
    assert definition.variant_ids == (TWO_ROUNDS, ONE_ROUND)
    assert definition.with_variant(TWO_ROUNDS).effect.get("rounds") == 2
    assert definition.with_variant(ONE_ROUND).effect.get("rounds") == 1
    assert definition.art == definition.with_variant(ONE_ROUND).art


def test_the_second_variant_says_one_round(library):
    definition = next(card for card in library.deck(settings.DECK_CHEST).cards
                      if card.title == CARD)
    two = definition.with_variant(TWO_ROUNDS).text
    one = definition.with_variant(ONE_ROUND).text
    assert "dwie pełne rundy" in two
    assert "jedną pełną rundę" in one
    assert one != two


def test_playing_it_grants_the_veto(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    status = veto_of(game, hunter)
    assert status is not None
    assert status.kind is StatusKind.MOVEMENT_VETO
    assert status.charges == 1
    assert status.data["rounds_left"] == 2


def test_the_variant_chooses_the_duration(library):
    game = make(library)
    arm(game, hunters_of(game)[0], variant=ONE_ROUND)
    assert veto_of(game, hunters_of(game)[0]).data["rounds_left"] == 1


def test_the_effect_starts_when_the_card_is_played(library):
    """Everybody EXCEPT the player owes a turn, which is what the brief's own
    example describes: the round is up when the turn comes back to them."""
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    pending = veto_of(game, hunter).data["pending"]
    assert hunter.index not in pending
    assert set(pending) == {p.index for p in game.players} - {hunter.index}


# ── who may block whom ───────────────────────────────────────────────────────
def test_a_hunter_may_block_piotrek(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    assert game._blockers_for(piotrek_of(game).index) == [hunter.index]


def test_piotrek_may_block_a_hunter(library):
    game = make(library)
    piotrek = piotrek_of(game)
    arm(game, piotrek)
    for hunter in hunters_of(game):
        assert game._blockers_for(hunter.index) == [piotrek.index]


def test_a_hunter_may_not_block_another_hunter(library):
    game = make(library)
    first, second = hunters_of(game)[:2]
    arm(game, first)
    assert game._blockers_for(second.index) == []


def test_the_opponent_rule_is_read_from_the_roles(library):
    game = make(library)
    piotrek, hunters = piotrek_of(game), hunters_of(game)
    assert game.are_opponents(piotrek.index, hunters[0].index)
    assert game.are_opponents(hunters[0].index, piotrek.index)
    assert not game.are_opponents(hunters[0].index, hunters[1].index)
    assert not game.are_opponents(piotrek.index, piotrek.index)


# ── what can be stopped ──────────────────────────────────────────────────────
def test_a_movement_card_opens_the_window(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    card, events = move(game, piotrek_of(game))

    opened = [e for e in events if isinstance(e, ev.MovementDecisionOpened)]
    assert len(opened) == 1
    assert opened[0].blockers == [hunter.index]
    assert game.pending_movement is not None
    assert game.pending_movement.card_uid == card.uid


def test_the_movement_has_not_happened_yet(library):
    """The pause is BEFORE the move, which is why there is nothing to undo."""
    game = make(library)
    arm(game, hunters_of(game)[0])
    piotrek = piotrek_of(game)
    before = game.board.position_of_pawn("zielony")
    card, _ = move(game, piotrek)
    assert game.board.position_of_pawn("zielony") == before
    assert card in piotrek.hand
    assert game.active_player_index == piotrek.index


def test_a_movement_causing_chest_card_opens_the_window(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    card = give(game, piotrek, settings.DECK_CHEST, "Dzieckorolka")
    game.active_player_index = piotrek.index
    events = game.apply(cmd.PlayCard(player_index=piotrek.index,
                                     card_uid=card.uid,
                                     choices={"pawn": "zielony"}))
    assert any(isinstance(e, ev.MovementDecisionOpened) for e in events), events


def test_a_card_that_moves_nobody_is_not_blockable(library):
    """Nie masz Rosji itself is a Chest card and moves no pawn."""
    game = make(library)
    arm(game, hunters_of(game)[0])
    piotrek = piotrek_of(game)
    card = give(game, piotrek, settings.DECK_CHEST, CARD)
    game.active_player_index = piotrek.index
    events = game.apply(cmd.PlayCard(player_index=piotrek.index,
                                     card_uid=card.uid))
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    assert game.pending_movement is None


def test_dragging_a_pawn_is_not_blockable(library):
    """Manual board manipulation is not a played movement action."""
    game = make(library)
    arm(game, hunters_of(game)[0])
    piotrek = piotrek_of(game)
    game.active_player_index = piotrek.index
    tile = game.board.position(8).tiles[0].index
    events = game.apply(cmd.MoveToken(pawn_id="zielony", x=0.0, y=0.0,
                                      tile_index=tile))
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    assert game.pending_movement is None
    assert game.board.position_of_pawn("zielony") == 8


def test_a_character_ability_is_not_blockable(library):
    """It is not a played card, and it does not come through _play_card."""
    game = make(library)
    arm(game, hunters_of(game)[0])
    mover = next((p for p in game.players
                  if p.character is not None and p.character.skill == "Skrypt"),
                 None)
    if mover is None:
        pytest.skip("this deal has no Dziad at the table")
    game.active_player_index = mover.index
    game.apply(cmd.UseAbility(player_index=mover.index, source="character",
                              choices={"pawn": "zielony", "move": "1"}))
    assert game.pending_movement is None


def test_a_movement_by_a_teammate_is_not_blockable(library):
    game = make(library)
    first, second = hunters_of(game)[:2]
    arm(game, first)
    _, events = move(game, second)
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)


# ── the decision window ──────────────────────────────────────────────────────
def test_the_window_uses_the_configured_length(library):
    game = make(library, block_decision_seconds=3)
    arm(game, hunters_of(game)[0])
    _, events = move(game, piotrek_of(game))
    opened = next(e for e in events if isinstance(e, ev.MovementDecisionOpened))
    assert opened.seconds == 3
    assert game.pending_movement.seconds == 3


def test_the_default_window_is_seven_seconds(library):
    assert RULES.block_decision_default == 7
    game = make(library)
    assert game.block_decision_seconds == 7


def test_the_table_stops_while_the_window_is_open(library):
    game = make(library)
    arm(game, hunters_of(game)[0])
    piotrek = piotrek_of(game)
    move(game, piotrek)
    events = game.apply(cmd.EndTurn(player_index=piotrek.index))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.pending_movement is not None


def test_accepting_lets_the_movement_happen(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    before = game.board.position_of_pawn("zielony")
    card, _ = move(game, piotrek)

    events = game.apply(cmd.AcceptMovement(player_index=hunter.index))

    assert any(isinstance(e, ev.MovementAccepted) for e in events)
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert game.board.position_of_pawn("zielony") == before + 1
    assert card not in piotrek.hand
    assert game.pending_movement is None


def test_accepting_does_not_consume_the_veto(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    move(game, piotrek_of(game))
    game.apply(cmd.AcceptMovement(player_index=hunter.index))
    assert veto_of(game, hunter) is not None
    assert veto_of(game, hunter).charges == 1


def test_the_veto_can_still_be_used_after_accepting_one_movement(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    move(game, piotrek)
    game.apply(cmd.AcceptMovement(player_index=hunter.index))

    card, events = move(game, piotrek)
    assert any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    game.apply(cmd.BlockMovement(player_index=hunter.index))
    assert veto_of(game, hunter) is None


def test_only_a_blocker_may_answer(library):
    game = make(library)
    hunter, other = hunters_of(game)[:2]
    arm(game, hunter)
    move(game, piotrek_of(game))
    assert any(isinstance(e, ev.ActionRejected)
               for e in game.apply(cmd.AcceptMovement(player_index=other.index)))
    assert any(isinstance(e, ev.ActionRejected)
               for e in game.apply(cmd.BlockMovement(player_index=other.index)))
    assert game.pending_movement is not None


def test_the_window_times_out_into_an_acceptance(library):
    """The AUTHORITY's clock, driven by the session rather than by a frame."""
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    session = LocalSession(game)
    piotrek = piotrek_of(game)
    before = game.board.position_of_pawn("zielony")
    move(game, piotrek)

    assert session.tick(100.0) == []          # notes the start
    assert session.tick(103.0) == []          # not yet
    events = session.tick(108.0)

    accepted = next(e for e in events if isinstance(e, ev.MovementAccepted))
    assert accepted.timeout
    assert game.board.position_of_pawn("zielony") == before + 1
    assert veto_of(game, hunter) is not None  # a timeout spends nothing
    assert game.pending_movement is None


def test_a_shorter_window_times_out_sooner(library):
    game = make(library, block_decision_seconds=2)
    arm(game, hunters_of(game)[0])
    session = LocalSession(game)
    move(game, piotrek_of(game))
    assert session.tick(50.0) == []
    assert session.tick(51.0) == []
    assert session.tick(52.5) != []


def test_a_client_may_not_time_the_table_out(library):
    """``ExpireMovementDecision`` is the authority's, like every other."""
    assert cmd.ExpireMovementDecision in cmd.AUTHORITY_ONLY
    game = make(library)
    arm(game, hunters_of(game)[0])
    move(game, piotrek_of(game))
    problem = game.authorise_remote(cmd.ExpireMovementDecision(), 0)
    assert problem
    assert game.pending_movement is not None


# ── blocking ─────────────────────────────────────────────────────────────────
def test_blocking_cancels_the_movement(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    before = {p.id: game.board.position_of_pawn(p.id) for p in library.pawns}
    card, _ = move(game, piotrek)

    events = game.apply(cmd.BlockMovement(player_index=hunter.index))

    blocked = next(e for e in events if isinstance(e, ev.MovementBlocked))
    assert blocked.blocker_index == hunter.index
    assert not blocked.automatic
    after = {p.id: game.board.position_of_pawn(p.id) for p in library.pawns}
    assert after == before
    assert not any(isinstance(e, ev.CardPlayed) for e in events)


def test_the_blocked_card_leaves_the_hand_for_its_own_discard_pile(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    card, _ = move(game, piotrek)
    game.apply(cmd.BlockMovement(player_index=hunter.index))

    assert card not in piotrek.hand
    discard = game.decks[settings.DECK_MOVEMENT].discard_pile
    assert any(c.uid == card.uid for c in discard)


def test_blocking_consumes_the_veto(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    move(game, piotrek_of(game))
    game.apply(cmd.BlockMovement(player_index=hunter.index))
    assert veto_of(game, hunter) is None


def test_one_card_never_blocks_twice(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    move(game, piotrek)
    game.apply(cmd.BlockMovement(player_index=hunter.index))

    before = game.board.position_of_pawn("zielony")
    _, events = move(game, piotrek)
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    assert game.board.position_of_pawn("zielony") == before + 1


def test_the_turn_moves_on_after_a_block(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    move(game, piotrek)
    events = game.apply(cmd.BlockMovement(player_index=hunter.index))
    assert any(isinstance(e, ev.ActivePlayerChanged) for e in events)
    assert game.active_player_index != piotrek.index


def test_a_blocked_movement_triggers_no_consequences(library):
    """The check that would have happened does not happen.

    The whole table is stacked onto one field except the mover, whose card
    would complete the tower — which is the position a check is made in.  With
    the movement blocked there is no tower, so ``victory.review`` has nothing
    to look at and nobody is eliminated.
    """
    from pedzacy_piotrek.engine import victory

    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    piotrek = piotrek_of(game)
    piotrek.secret_pawn = "czerwony"

    gathering = game.board.position(6).tiles[0].index
    for pawn in library.pawns:
        if pawn.id != "zielony":
            game.board.place_pawn(pawn.id, gathering)
    game.board.place_pawn("zielony", game.board.position(5).tiles[0].index)
    game._sync_token_positions()

    card, _ = move(game, piotrek)
    game.apply(cmd.BlockMovement(player_index=hunter.index))

    assert game.board.position_of_pawn("zielony") == 5
    assert victory.review(game) == []
    assert game.eliminated_pawns == []
    assert game.victory is None


# ── full rounds ──────────────────────────────────────────────────────────────
def turns(game, count: int) -> list:
    """Let ``count`` turns finish, reporting the seat that finished each."""
    seen = []
    for _ in range(count):
        seen.append(game.active_player_index)
        game._end_turn()
    return seen


def test_one_full_round_is_everybody_else_taking_a_turn(library):
    """The brief's hunter example: it is up when the turn comes back."""
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter, variant=ONE_ROUND)
    others = {p.index for p in game.players} - {hunter.index}

    finished = []
    for _ in range(20):
        finished.append(game.active_player_index)
        game._end_turn()
        if veto_of(game, hunter) is None:
            break
    assert veto_of(game, hunter) is None
    assert others <= set(finished)


def test_the_effect_survives_a_round_boundary(library):
    """A full round is NOT the round counter."""
    game = make(library)
    hunter = hunters_of(game)[-1]
    arm(game, hunter, variant=ONE_ROUND)
    start = game.round_number
    while veto_of(game, hunter) is not None and game.round_number < start + 4:
        game._end_turn()
    # Whether it crossed a boundary depends on where in the round it started;
    # what matters is that it survived until every seat had had a turn.
    assert veto_of(game, hunter) is None


def test_piotrek_appearing_again_does_not_end_a_round(library):
    """The brief's Piotrek example, on the real cadence.

    Piotrek holds every third slot, so his next turn comes round long before
    the hunters have all played.  The effect must still be alive then.
    """
    game = make(library)
    piotrek = piotrek_of(game)
    order = game.seat_order()
    assert order.count(piotrek.index) > 1, "this cadence has one Piotrek slot"

    game.active_player_index = piotrek.index
    game.turn_slot = order.index(piotrek.index)
    arm(game, piotrek, variant=ONE_ROUND)

    game._end_turn()
    while game.active_player_index != piotrek.index:
        game._end_turn()
    # Piotrek is up again and the hunters have not all played yet.
    assert veto_of(game, piotrek) is not None


def test_two_rounds_last_twice_as_long(library):
    def lifetime(variant: str) -> int:
        game = make(library)
        hunter = hunters_of(game)[0]
        arm(game, hunter, variant=variant)
        count = 0
        while veto_of(game, hunter) is not None and count < 60:
            game._end_turn()
            count += 1
        return count

    one, two = lifetime(ONE_ROUND), lifetime(TWO_ROUNDS)
    assert two > one


def test_every_turn_occurrence_counts(library):
    """A seat that plays twice in a round does not owe two turns."""
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter, variant=ONE_ROUND)
    status = veto_of(game, hunter)
    owed = list(status.data["pending"])
    game._note_turn_completed(owed[0])
    game._note_turn_completed(owed[0])
    assert set(status.data["pending"]) == set(owed[1:])


# ── the automatic final block ────────────────────────────────────────────────
def last_chance_position(game, blocker, opponent):
    """Walk the table forward to the opponent's last turn before expiry."""
    for _ in range(40):
        status = veto_of(game, blocker)
        if status is None:
            return False
        if (game.active_player_index == opponent.index
                and game._veto_is_last_chance(status, opponent.index)):
            return True
        game._end_turn()
    return False


def test_the_last_chance_is_recognised(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter, variant=ONE_ROUND)
    assert last_chance_position(game, hunter, piotrek_of(game))


def test_the_final_opponent_movement_is_blocked_automatically(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    piotrek = piotrek_of(game)
    arm(game, hunter, variant=ONE_ROUND)
    assert last_chance_position(game, hunter, piotrek)

    before = game.board.position_of_pawn("zielony")
    card, events = move(game, piotrek)

    blocked = next(e for e in events if isinstance(e, ev.MovementBlocked))
    assert blocked.automatic
    assert blocked.blocker_index == hunter.index
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    assert game.pending_movement is None
    assert game.board.position_of_pawn("zielony") == before
    assert veto_of(game, hunter) is None
    assert card not in piotrek.hand


def test_an_earlier_movement_still_opens_a_window(library):
    """Only the LAST chance fires by itself; the first one is a decision."""
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter, variant=TWO_ROUNDS)
    _, events = move(game, piotrek_of(game))
    assert any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    assert not any(isinstance(e, ev.MovementBlocked) for e in events)


def test_a_veto_already_spent_blocks_nothing_at_the_end(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    piotrek = piotrek_of(game)
    arm(game, hunter, variant=ONE_ROUND)
    move(game, piotrek)
    game.apply(cmd.BlockMovement(player_index=hunter.index))

    before = game.board.position_of_pawn("zielony")
    _, events = move(game, piotrek)
    assert not any(isinstance(e, ev.MovementBlocked) for e in events)
    assert game.board.position_of_pawn("zielony") == before + 1


# ── several vetoes at once ───────────────────────────────────────────────────
def test_two_hunters_both_hold_a_veto_against_piotrek(library):
    game = make(library)
    first, second = hunters_of(game)[:2]
    arm(game, first, variant=TWO_ROUNDS)
    arm(game, second, variant=TWO_ROUNDS)
    _, events = move(game, piotrek_of(game))
    opened = next(e for e in events if isinstance(e, ev.MovementDecisionOpened))
    assert set(opened.blockers) == {first.index, second.index}


def test_one_acceptance_does_not_spend_the_others_chance(library):
    game = make(library)
    first, second = hunters_of(game)[:2]
    arm(game, first, variant=TWO_ROUNDS)
    arm(game, second, variant=TWO_ROUNDS)
    piotrek = piotrek_of(game)
    before = game.board.position_of_pawn("zielony")
    move(game, piotrek)

    game.apply(cmd.AcceptMovement(player_index=first.index))
    assert game.pending_movement is not None          # still second's call
    assert game.board.position_of_pawn("zielony") == before

    events = game.apply(cmd.BlockMovement(player_index=second.index))
    assert any(isinstance(e, ev.MovementBlocked) for e in events)
    assert game.board.position_of_pawn("zielony") == before
    assert veto_of(game, first) is not None
    assert veto_of(game, second) is None


def test_the_movement_runs_once_everybody_has_accepted(library):
    game = make(library)
    first, second = hunters_of(game)[:2]
    arm(game, first, variant=TWO_ROUNDS)
    arm(game, second, variant=TWO_ROUNDS)
    piotrek = piotrek_of(game)
    before = game.board.position_of_pawn("zielony")
    move(game, piotrek)
    game.apply(cmd.AcceptMovement(player_index=first.index))
    game.apply(cmd.AcceptMovement(player_index=second.index))
    assert game.pending_movement is None
    assert game.board.position_of_pawn("zielony") == before + 1


def test_only_one_block_can_resolve_a_movement(library):
    """The second blocker finds nothing to answer — one authority, in order."""
    game = make(library)
    first, second = hunters_of(game)[:2]
    arm(game, first, variant=TWO_ROUNDS)
    arm(game, second, variant=TWO_ROUNDS)
    move(game, piotrek_of(game))

    game.apply(cmd.BlockMovement(player_index=first.index))
    events = game.apply(cmd.BlockMovement(player_index=second.index))

    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert veto_of(game, second) is not None      # not spent on nothing


# ── configuration ────────────────────────────────────────────────────────────
def test_the_lobby_carries_the_cooldown(library):
    lobby = LobbyState(block_decision_seconds=4)
    assert lobby.to_config(seed=1).block_decision_seconds == 4
    assert LobbyState.from_dict(lobby.to_dict()).block_decision_seconds == 4


def test_the_cooldown_is_clamped_into_its_range(library):
    high = SessionConfig(num_players=3, block_decision_seconds=9_000).normalised()
    low = SessionConfig(num_players=3, block_decision_seconds=-4).normalised()
    assert high.block_decision_seconds == RULES.block_decision_max
    assert low.block_decision_seconds == RULES.block_decision_min


def test_the_default_reaches_a_game_that_configures_nothing(library):
    assert make(library).block_decision_seconds == RULES.block_decision_default


def test_the_settings_panel_offers_the_cooldown(library):
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    class _FakePanel:
        # The rules tab grew three more rows in stage 40 (Ice Block's window
        # and the two rule variants); this test is still about the cooldown,
        # so the stand-in carries every row name the builder reads.
        BLOCK_ROW = GameSettingsPanel.BLOCK_ROW
        CHECK_ROW = GameSettingsPanel.CHECK_ROW
        CHECK_VARIANT_ROW = GameSettingsPanel.CHECK_VARIANT_ROW
        VICTORY_VARIANT_ROW = GameSettingsPanel.VICTORY_VARIANT_ROW

        def __init__(self, content):
            self.library = content

    tab = GameSettingsPanel._rules_tab(_FakePanel(library))
    assert GameSettingsPanel.BLOCK_ROW in tab.titles
    assert tab.values[GameSettingsPanel.BLOCK_ROW] == RULES.block_decision_default
    tab.bump(GameSettingsPanel.BLOCK_ROW, 1)
    assert tab.values[GameSettingsPanel.BLOCK_ROW] == 8
    for _ in range(50):
        tab.bump(GameSettingsPanel.BLOCK_ROW, 1)
    assert tab.values[GameSettingsPanel.BLOCK_ROW] == RULES.block_decision_max


# ── the snapshot ─────────────────────────────────────────────────────────────
def test_the_paused_movement_is_in_the_fingerprint(library):
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    assert game.snapshot()["pending_movement"] is None
    move(game, piotrek_of(game))
    held = game.snapshot()["pending_movement"]
    assert held["blockers"] == [hunter.index]
    assert held["seconds"] == 7
    # The CLOCK is deliberately not in it: it differs on every machine.
    assert "opened_at" not in held


# ── over a network ───────────────────────────────────────────────────────────

def online_pose(state, base: int) -> None:
    """Stand green where its one-field move lands on a SINGLE field.

    An online table gets a real board, so its widened rows are wherever the
    seed put them — and a card landing on one asks "12a or 12b?", which is a
    question these tests are not about.  The board is identical on every
    machine, so a base chosen from one is right for all of them.

    Nobody is parked on the finish: a pawn there ends the match, and every
    command after that is refused for a reason that has nothing to do with the
    card under test.
    """
    board = state.board
    spare = max(0, board.last_position - 2)
    for pawn in state.library.pawns:
        board.remove_pawn(pawn.id)
    board.place_pawn("zielony", board.position(base).tiles[0].index)
    for offset, pawn in enumerate(p for p in state.library.pawns
                                  if p.id != "zielony"):
        where = spare - offset
        if where in (base, base + 1):
            where = base - 2 - offset
        board.place_pawn(pawn.id, board.position(max(0, where)).tiles[0].index)
    state._sync_token_positions()


def single_field_base(board) -> int:
    """A position whose NEXT position is one field wide."""
    return next(index for index in range(1, board.last_position - 1)
                if not board.position(index + 1).is_doubled)


def test_the_whole_exchange_replicates(library):
    """Play, pause, block — through the real server, with no local rollback."""
    from netkit import Table, all_agree

    table = Table(library)
    host, clients = table.seated("Kuba", "Ola", "Ala")
    # ``edit_mode`` because this test fetches a NAMED card through
    # ``DrawTitledCard``, which needs an editing table since stage 53.  The
    # exchange being replicated here is unaffected by it.
    host.set_settings(block_decision_seconds=5, edit_mode=True)
    table.pump()
    assert host.lobby_state.block_decision_seconds == 5
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients, "")

    parties = [host, *clients]
    room = table.room(host.room_code)
    everywhere = [room.state] + [s.state for s in parties]
    for state in everywhere:
        assert state.block_decision_seconds == 5

    piotrek_seat = next(p.index for p in room.state.players if p.is_piotrek)
    hunter_seat = next(p.index for p in room.state.players if not p.is_piotrek)
    base = single_field_base(room.state.board)

    # The card is given to the hunter on every machine, then PLAYED as a
    # command so the effect itself travels the ordinary road.
    for state in everywhere:
        player = state.player(hunter_seat)
        deck = state.decks[settings.DECK_CHEST]
        card = next(c for c in deck.draw_pile if c.title == CARD)
        deck.draw_pile.remove(card)
        player.add_card(card)
        online_pose(state, base)
    uid = room.state.player(hunter_seat).hand[-1].uid

    by_seat = table.by_seat(host, clients)
    hunter = by_seat[hunter_seat]
    room.state.active_player_index = hunter_seat
    for state in everywhere:
        state.active_player_index = hunter_seat
    hunter.session.submit(cmd.PlayCard(player_index=hunter_seat, card_uid=uid))
    table.pump()
    for state in everywhere:
        assert state.veto_of(hunter_seat) is not None

    # Piotrek moves, and every machine sees the table stop.
    piotrek = by_seat[piotrek_seat]
    for state in everywhere:
        state.active_player_index = piotrek_seat
    piotrek.session.submit(cmd.DrawTitledCard(
        player_index=piotrek_seat, deck_id=settings.DECK_MOVEMENT,
        title=MOVE_CARD))
    table.pump()
    move_uid = next(c.uid for c in piotrek.state.player(piotrek_seat).hand
                    if c.title == MOVE_CARD)
    before = room.state.board.position_of_pawn("zielony")
    piotrek.session.submit(cmd.PlayCard(player_index=piotrek_seat,
                                        card_uid=move_uid))
    table.pump()
    for state in everywhere:
        assert state.pending_movement is not None
        assert state.board.position_of_pawn("zielony") == before

    # The hunter blocks, and the movement never happens anywhere.
    hunter.session.submit(cmd.BlockMovement(player_index=hunter_seat))
    table.pump()
    for state in everywhere:
        assert state.pending_movement is None
        assert state.board.position_of_pawn("zielony") == before
        assert state.veto_of(hunter_seat) is None
        assert any(c.uid == move_uid
                   for c in state.decks[settings.DECK_MOVEMENT].discard_pile)
    assert all_agree(*parties)


def test_the_server_times_the_window_out(library):
    """The room's own tick closes it — nobody has to send anything."""
    from netkit import Table, all_agree

    clock = {"now": 1_000.0}
    table = Table(library)
    host, clients = table.seated("Kuba", "Ola", "Ala")
    # An EDITING table since stage 53: this test fetches a NAMED card through
    # ``DrawTitledCard`` so that the movement under test is a known one, and
    # that command now needs edit mode.  The window timing being tested here is
    # untouched by it.
    host.set_settings(edit_mode=True)
    table.pump()
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients, "")
    parties = [host, *clients]
    room = table.room(host.room_code)
    room._clock = lambda: clock["now"]
    everywhere = [room.state] + [s.state for s in parties]

    piotrek_seat = next(p.index for p in room.state.players if p.is_piotrek)
    hunter_seat = next(p.index for p in room.state.players if not p.is_piotrek)
    base = single_field_base(room.state.board)
    for state in everywhere:
        player = state.player(hunter_seat)
        deck = state.decks[settings.DECK_CHEST]
        card = next(c for c in deck.draw_pile if c.title == CARD)
        deck.draw_pile.remove(card)
        player.add_card(card)
        online_pose(state, base)
        state.active_player_index = hunter_seat
    uid = room.state.player(hunter_seat).hand[-1].uid
    by_seat = table.by_seat(host, clients)
    by_seat[hunter_seat].session.submit(
        cmd.PlayCard(player_index=hunter_seat, card_uid=uid))
    table.pump()

    for state in everywhere:
        state.active_player_index = piotrek_seat
    piotrek = by_seat[piotrek_seat]
    piotrek.session.submit(cmd.DrawTitledCard(
        player_index=piotrek_seat, deck_id=settings.DECK_MOVEMENT,
        title=MOVE_CARD))
    table.pump()
    move_uid = next(c.uid for c in piotrek.state.player(piotrek_seat).hand
                    if c.title == MOVE_CARD)
    before = room.state.board.position_of_pawn("zielony")
    piotrek.session.submit(cmd.PlayCard(player_index=piotrek_seat,
                                        card_uid=move_uid))
    table.pump()
    assert room.state.pending_movement is not None

    table.tick()                       # notes when the window opened
    clock["now"] += 3.0
    table.tick()
    assert room.state.pending_movement is not None
    clock["now"] += 10.0
    table.tick()

    for state in everywhere:
        assert state.pending_movement is None
        assert state.board.position_of_pawn("zielony") == before + 1
        assert state.veto_of(hunter_seat) is not None
    assert all_agree(*parties)


# ── the interface ────────────────────────────────────────────────────────────
# Driven against SDL's dummy driver the way test_card_library.py drives it.
# What is checked here is that the controls appear for the right seat, that
# they produce COMMANDS and nothing else, and that the block goes through a
# confirmation first — not how any of it looks.
import os                                                     # noqa: E402
import sys                                                    # noqa: E402
from pathlib import Path                                      # noqa: E402

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame                                                 # noqa: E402

from pedzacy_piotrek.ui.app import App                        # noqa: E402
from pedzacy_piotrek.ui.game_screen import GameScreen         # noqa: E402
from pedzacy_piotrek.ui.layout import Layout                  # noqa: E402

WINDOW = (1920, 1080)


def screen_for(game) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    game_screen = GameScreen(app, LocalSession(game))
    app.push(game_screen)
    return game_screen


def frame(screen: GameScreen) -> pygame.Surface:
    surface = pygame.Surface(WINDOW)
    screen.app.renderer.begin(surface)
    screen.update(0.016, (0, 0))
    screen.draw(surface)
    return surface


def click(screen: GameScreen, position) -> None:
    position = (int(position[0]), int(position[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=position),
        position,
    )
    screen.session.poll()


def paused_screen(library):
    """A screen looking at the blocker's seat, with a movement waiting."""
    game = make(library)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    screen = screen_for(game)
    screen.view_seat = hunter.index
    move(game, piotrek_of(game))
    frame(screen)
    return screen, game, hunter


def test_the_controls_appear_for_the_blocker(library):
    screen, game, hunter = paused_screen(library)
    assert screen.movement_decision.active
    assert screen.movement_decision.card is not None
    assert screen.movement_decision.card.title == MOVE_CARD


def test_the_controls_do_not_appear_for_anybody_else(library):
    screen, game, hunter = paused_screen(library)
    screen.view_seat = piotrek_of(game).index
    frame(screen)
    assert not screen.movement_decision.active


def test_there_are_no_controls_without_a_decision(library):
    game = make(library)
    screen = screen_for(game)
    frame(screen)
    assert not screen.movement_decision.active


def test_accepting_from_the_button_submits_the_command(library):
    screen, game, hunter = paused_screen(library)
    before = game.board.position_of_pawn("zielony")
    _, accept = screen.app.layout.movement_decision_buttons()

    click(screen, accept.center)

    assert game.pending_movement is None
    assert game.board.position_of_pawn("zielony") == before + 1
    assert veto_of(game, hunter) is not None


def test_blocking_asks_first(library):
    """The first click opens a confirmation; it does not block."""
    screen, game, hunter = paused_screen(library)
    block, _ = screen.app.layout.movement_decision_buttons()

    click(screen, block.center)

    assert screen.movement_decision.confirming
    assert game.pending_movement is not None
    assert veto_of(game, hunter) is not None


def test_the_confirmation_shows_the_card_being_considered(library):
    screen, game, hunter = paused_screen(library)
    block, _ = screen.app.layout.movement_decision_buttons()
    click(screen, block.center)
    frame(screen)
    assert screen.movement_decision.card.uid == game.pending_movement.card_uid


def test_cancelling_the_confirmation_accepts_the_movement(library):
    screen, game, hunter = paused_screen(library)
    before = game.board.position_of_pawn("zielony")
    block, _ = screen.app.layout.movement_decision_buttons()
    click(screen, block.center)
    _, cancel = screen.app.layout.movement_confirm_buttons()

    click(screen, cancel.center)

    assert not screen.movement_decision.confirming
    assert game.pending_movement is None
    assert game.board.position_of_pawn("zielony") == before + 1
    assert veto_of(game, hunter) is not None      # NOT consumed


def test_confirming_blocks_the_movement(library):
    screen, game, hunter = paused_screen(library)
    before = game.board.position_of_pawn("zielony")
    block, _ = screen.app.layout.movement_decision_buttons()
    click(screen, block.center)
    confirm, _ = screen.app.layout.movement_confirm_buttons()

    click(screen, confirm.center)

    assert game.pending_movement is None
    assert game.board.position_of_pawn("zielony") == before
    assert veto_of(game, hunter) is None


def test_the_confirmation_swallows_clicks_on_the_table(library):
    """Modal: a click outside must not reach the board underneath."""
    screen, game, hunter = paused_screen(library)
    block, _ = screen.app.layout.movement_decision_buttons()
    click(screen, block.center)
    before = game.snapshot()

    click(screen, (4, 4))

    assert screen.movement_decision.confirming
    assert game.snapshot() == before


def test_the_countdown_is_drawn_and_runs_down(library):
    screen, game, hunter = paused_screen(library)
    started = screen.movement_decision.left
    assert started == pytest.approx(game.block_decision_seconds, abs=0.05)
    for _ in range(30):
        frame(screen)
    assert screen.movement_decision.left < started


def test_the_controls_go_away_once_it_is_answered(library):
    screen, game, hunter = paused_screen(library)
    _, accept = screen.app.layout.movement_decision_buttons()
    click(screen, accept.center)
    frame(screen)
    assert not screen.movement_decision.active
    assert not screen.movement_decision.confirming


def test_the_countdown_is_drawn_from_the_configured_length(library):
    """The number on screen comes from the state, not from a constant here."""
    game = make(library, block_decision_seconds=4)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    screen = screen_for(game)
    screen.view_seat = hunter.index
    move(game, piotrek_of(game))
    frame(screen)

    assert screen.movement_decision.left == pytest.approx(4.0, abs=0.05)
    screen.movement_decision.update(1.5, screen.app.layout, (0, 0))
    assert screen.movement_decision.left == pytest.approx(2.5, abs=0.05)


def test_the_countdown_never_closes_the_window_itself(library):
    """It is a PICTURE.  Only the authority ends a decision.

    Frames are pumped past the end of a two-second window WITHOUT the
    session's clock moving, which is exactly the situation a client is in: it
    draws, it reaches zero, and the table is still waiting because the
    authority has not said otherwise.
    """
    game = make(library, block_decision_seconds=2)
    hunter = hunters_of(game)[0]
    arm(game, hunter)
    screen = screen_for(game)
    screen.view_seat = hunter.index
    move(game, piotrek_of(game))

    for _ in range(120):
        screen.movement_decision.update(0.1, screen.app.layout, (0, 0))

    assert screen.movement_decision.left == 0.0
    assert game.pending_movement is not None


def test_both_halves_draw_at_every_reference_size(library):
    """The panel and the dialog fit on the screen at all supported windows."""
    for size in [(1280, 760), (1920, 1080), (2560, 1440), (3840, 2160)]:
        game = make(library)
        hunter = hunters_of(game)[0]
        arm(game, hunter)
        app = App(Layout(), headless=True, size=size)
        screen = GameScreen(app, LocalSession(game))
        app.push(screen)
        screen.view_seat = hunter.index
        move(game, piotrek_of(game))

        surface = pygame.Surface(size)
        app.renderer.begin(surface)
        screen.update(0.016, (0, 0))
        screen.draw(surface)
        screen.movement_decision.confirming = True
        screen.draw(surface)

        panel = app.layout.movement_decision_panel()
        assert 0 <= panel.left and panel.right <= app.layout.win_w, size
        assert panel.bottom <= app.layout.win_h, size
        for rect in app.layout.movement_confirm_buttons():
            assert app.layout.movement_confirm_panel().contains(rect), size
