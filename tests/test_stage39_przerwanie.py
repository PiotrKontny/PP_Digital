"""
Stage 39 — Dziubdziuch's "Przerwanie Systemowe".

The same interception mechanism as Nie masz Rosji with four different sets of
numbers on it, so most of what is worth testing is the SCOPE rule that tells
the two apart: who may be interrupted, with what, and for how long.

Deliberately written against real played cards and the real decision window
rather than against the status data, because "a veto exists with these fields"
is not the claim — "Piotrek's Chest card was held and his Movement card was
not" is.  The status is inspected only where the point is that no clock is
running.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import StatusKind

CHARACTER = "Dziubdziuch"
V1_FOREVER_ANY = "forever_any"
V2_FOREVER_MOVEMENT = "forever_movement"
V3_ROUND_ANY = "round_any"
V4_ROUND_MOVEMENT = "round_movement"

#: Always moves the same pawn one field forward, so no question is asked.
MOVE_CARD = "Zerówka - zielony"
#: A Chest card that moves pawns without asking anything beyond a direction.
CHEST_MOVE_CARD = "Balbinka"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, variant: str = "", players: int = 6, **kwargs):
    """Six seats — Piotrek holds every third slot — everybody on the board."""
    if variant:
        kwargs.setdefault("card_variants", {CHARACTER: variant})
    config = SessionConfig(
        num_players=players, board_cells=40, seed=4321,
        chest_open_round=10_000, mod_round_first=10_000,
        double_frequency=0.0, piotrek_picks_pawn=False, **kwargs,
    )
    game = create_game(config, library)
    for index, pawn in enumerate(library.pawns):
        game.board.place_pawn(pawn.id,
                              game.board.position(index + 1).tiles[0].index)
    game._sync_token_positions()
    return game


@pytest.fixture
def game(library):
    return make(library)


def piotrek_seat(game) -> int:
    return next(p.index for p in game.players if p.is_piotrek)


def hunter_seats(game):
    return [p.index for p in game.players if not p.is_piotrek]


def give_character(game, seat: int, title: str):
    deck = game.deck(settings.DECK_CHARACTERS)
    card = deck.take_titled(title, include_discard=True)
    if card is None:
        for player in game.players:
            if player.character is not None and player.character.title == title:
                card, player.character = player.character, None
                break
    assert card is not None, title
    player = game.players[seat]
    if player.character is not None:
        deck.return_card(player.character)
    player.character = card
    return card


def give(game, seat: int, deck_id: str, title: str):
    deck = game.decks[deck_id]
    card = next((c for c in deck.draw_pile if c.title == title), None)
    if card is not None:
        deck.draw_pile.remove(card)
    else:
        for player in game.players:
            card = next((c for c in player.hand if c.title == title), None)
            if card is not None:
                player.remove_card(card)
                break
    assert card is not None, title
    game.players[seat].add_card(card)
    return card


def arm(game, seat: int = None) -> int:
    """Seat Dziubdziuch and activate the ability for real."""
    seat = hunter_seats(game)[0] if seat is None else seat
    give_character(game, seat, CHARACTER)
    game.apply(cmd.SetActivePlayer(player_index=seat))
    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.AbilityUsed) for e in events), events
    assert game.veto_of(seat) is not None
    return seat


def play_by(game, seat: int, deck_id: str, title: str, **choices):
    """Let a seat play a named card on its own turn."""
    game.apply(cmd.SetActivePlayer(player_index=seat))
    card = give(game, seat, deck_id, title)
    events = game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid,
                                     choices=choices))
    return card, events


def piotrek_plays_movement(game, **choices):
    return play_by(game, piotrek_seat(game), settings.DECK_MOVEMENT, MOVE_CARD,
                   **choices)


def piotrek_plays_chest(game):
    return play_by(game, piotrek_seat(game), settings.DECK_CHEST,
                   CHEST_MOVE_CARD, direction="forward")


def held(game) -> bool:
    """Whether a movement is currently paused waiting on a blocker."""
    return game.pending_movement is not None


# ═════════════════════════════════════════════════════════════════════════════
# Activation
# ═════════════════════════════════════════════════════════════════════════════
def test_activation_grants_the_same_veto_nie_masz_rosji_grants(game):
    """One mechanism, not two: the same status kind and the same tracker."""
    seat = arm(game)
    status = game.veto_of(seat)
    assert status is not None
    assert status.kind is StatusKind.MOVEMENT_VETO
    assert status.charges == 1, "one block, exactly as the Chest card"


def test_activation_spends_an_ability_use(game):
    seat = arm(game)
    assert game.players[seat].character.uses_left == 0


def test_the_ability_respects_the_global_start_rule(game):
    seat = hunter_seats(game)[0]
    give_character(game, seat, CHARACTER)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()
    game.apply(cmd.SetActivePlayer(player_index=seat))

    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.veto_of(seat) is None
    assert game.players[seat].character.uses_left == 1


# ═════════════════════════════════════════════════════════════════════════════
# §9 — Piotrek only
# ═════════════════════════════════════════════════════════════════════════════
def test_piotreks_movement_is_intercepted(game):
    seat = arm(game)
    _, events = piotrek_plays_movement(game)
    opened = next(e for e in events if isinstance(e, ev.MovementDecisionOpened))
    assert opened.blockers == [seat]
    assert held(game)


def test_a_hunters_movement_is_not_intercepted(game):
    """Narrower than "opponent": a hunter moving is nobody's business."""
    seat = arm(game)
    other = next(s for s in hunter_seats(game) if s != seat)
    _, events = play_by(game, other, settings.DECK_MOVEMENT, MOVE_CARD)
    assert not held(game)
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)


def test_dziubdziuch_does_not_interrupt_himself(game):
    seat = arm(game)
    _, events = play_by(game, seat, settings.DECK_MOVEMENT, MOVE_CARD)
    assert not held(game)
    assert any(isinstance(e, ev.CardPlayed) for e in events)


def test_the_scope_rule_reads_roles_not_titles(game):
    """``veto_covers`` is the single place the question is answered."""
    seat = arm(game)
    status = game.veto_of(seat)
    assert game.veto_covers(status, piotrek_seat(game),
                            settings.DECK_MOVEMENT)
    for other in hunter_seats(game):
        assert not game.veto_covers(status, other, settings.DECK_MOVEMENT)


# ═════════════════════════════════════════════════════════════════════════════
# §3 / §4 / §10 — variants 1 and 2: what may be blocked
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("variant", [V1_FOREVER_ANY, V2_FOREVER_MOVEMENT])
def test_every_variant_blocks_piotreks_movement_cards(library, variant):
    game = make(library, variant)
    arm(game)
    piotrek_plays_movement(game)
    assert held(game), "a Movement Card is blockable under every variant"


def test_variant_1_also_holds_a_movement_causing_chest_card(library):
    game = make(library, V1_FOREVER_ANY)
    arm(game)
    piotrek_plays_chest(game)
    assert held(game)


def test_variant_2_lets_a_chest_card_through(library):
    """Not "blocked and then allowed": no window opens at all."""
    game = make(library, V2_FOREVER_MOVEMENT)
    seat = arm(game)
    _, events = piotrek_plays_chest(game)
    assert not held(game)
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert game.veto_of(seat) is not None, "and the veto is not spent by it"


def test_the_deck_is_the_category_not_the_title(library):
    game = make(library, V2_FOREVER_MOVEMENT)
    seat = arm(game)
    status = game.veto_of(seat)
    piotrek = piotrek_seat(game)
    assert game.veto_covers(status, piotrek, settings.DECK_MOVEMENT)
    assert not game.veto_covers(status, piotrek, settings.DECK_CHEST)


def test_a_chest_card_that_moves_nobody_is_never_held(library):
    """Category alone is not enough — the plan has to move a pawn."""
    game = make(library, V1_FOREVER_ANY)
    arm(game)
    play_by(game, piotrek_seat(game), settings.DECK_CHEST, "Shady")
    assert not held(game)


# ═════════════════════════════════════════════════════════════════════════════
# §3 / §7 — variant 1 lasts until the uses are gone
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("variant", [V1_FOREVER_ANY, V2_FOREVER_MOVEMENT])
def test_the_forever_variants_carry_no_clock(library, variant):
    game = make(library, variant)
    seat = arm(game)
    assert game.veto_of(seat).data["rounds"] == effects.UNLIMITED_ROUNDS


@pytest.mark.parametrize("variant", [V1_FOREVER_ANY, V2_FOREVER_MOVEMENT])
def test_the_forever_variants_survive_many_full_rounds(library, variant):
    game = make(library, variant)
    seat = arm(game)
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        assert game.veto_of(seat) is not None, "turns passing do nothing to it"


def test_it_can_still_block_long_after_activation(library):
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    for _ in range(25):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
    piotrek_plays_movement(game)
    assert held(game), "still armed a couple of rounds later"


def test_blocking_spends_the_veto_and_leaves_no_second_block(library):
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    card, _ = piotrek_plays_movement(game)
    where = game.board.position_of_pawn("zielony")

    events = game.apply(cmd.BlockMovement(player_index=seat))
    assert any(isinstance(e, ev.MovementBlocked) for e in events)
    assert game.board.position_of_pawn("zielony") == where, "it never happened"
    assert game.veto_of(seat) is None, "the charge is spent"

    piotrek_plays_movement(game)
    assert not held(game), "no further window opens"


def test_with_no_uses_left_the_ability_cannot_be_activated_again(library):
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    piotrek_plays_movement(game)
    game.apply(cmd.BlockMovement(player_index=seat))
    assert game.veto_of(seat) is None, "spent"
    assert game.players[seat].character.uses_left == 0

    game.apply(cmd.SetActivePlayer(player_index=seat))
    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.veto_of(seat) is None


def test_a_second_use_arms_it_again(library):
    """The ability-use system IS the counter — two uses means two blocks."""
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    game.players[seat].character.uses_left = 1      # the Card Library may do this
    piotrek_plays_movement(game)
    game.apply(cmd.BlockMovement(player_index=seat))
    assert game.veto_of(seat) is None

    game.apply(cmd.SetActivePlayer(player_index=seat))
    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert game.veto_of(seat) is not None
    piotrek_plays_movement(game)
    assert held(game)


def test_a_stale_block_command_after_exhaustion_does_nothing(library):
    """§7: a leftover button must not manufacture a block."""
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    piotrek_plays_movement(game)
    game.apply(cmd.BlockMovement(player_index=seat))     # the only block
    assert game.veto_of(seat) is None

    before = game.board.position_of_pawn("zielony")
    _, events = piotrek_plays_movement(game)
    assert not held(game), "no window: there is nothing left to block with"
    assert game.board.position_of_pawn("zielony") == before + 1

    # And the command arriving late — a client whose button never went away.
    after = game.board.position_of_pawn("zielony")
    late = game.apply(cmd.BlockMovement(player_index=seat))
    assert not any(isinstance(e, ev.MovementBlocked) for e in late)
    assert game.board.position_of_pawn("zielony") == after, "nothing rewound"


# ═════════════════════════════════════════════════════════════════════════════
# §5 / §6 — variants 3 and 4 expire after one full round
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("variant", [V3_ROUND_ANY, V4_ROUND_MOVEMENT])
def test_the_round_variants_expire_when_the_turn_comes_back(library, variant):
    game = make(library, variant)
    seat = arm(game)
    seen = 0
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.veto_of(seat) is None:
            break
        seen += 1
    assert game.veto_of(seat) is None, "it did expire"
    assert seen >= 2, "and several turns passed first"


@pytest.mark.parametrize("variant", [V3_ROUND_ANY, V4_ROUND_MOVEMENT])
def test_the_round_variants_do_not_expire_after_a_single_turn(library, variant):
    game = make(library, variant)
    seat = arm(game)
    game.apply(cmd.EndTurn(player_index=game.active_player_index))
    assert game.veto_of(seat) is not None


def test_after_expiry_no_window_opens(library):
    game = make(library, V3_ROUND_ANY)
    seat = arm(game)
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.veto_of(seat) is None:
            break
    _, events = piotrek_plays_movement(game)
    assert not held(game)
    assert not any(isinstance(e, ev.MovementDecisionOpened) for e in events)


def test_variant_3_holds_a_chest_card_inside_its_round(library):
    game = make(library, V3_ROUND_ANY)
    arm(game)
    piotrek_plays_chest(game)
    assert held(game)


def test_variant_4_lets_a_chest_card_through_inside_its_round(library):
    """Variant 4 is variant 3's clock with variant 2's targets."""
    game = make(library, V4_ROUND_MOVEMENT)
    seat = arm(game)
    piotrek_plays_chest(game)
    assert not held(game)
    assert game.veto_of(seat) is not None, "still armed, just not for that"

    piotrek_plays_movement(game)
    assert held(game), "and a Movement Card still is"


def test_variant_4_expires_and_then_blocks_nothing(library):
    game = make(library, V4_ROUND_MOVEMENT)
    seat = arm(game)
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.veto_of(seat) is None:
            break
    assert game.veto_of(seat) is None
    piotrek_plays_movement(game)
    assert not held(game)


# ═════════════════════════════════════════════════════════════════════════════
# §8 — the shared decision flow
# ═════════════════════════════════════════════════════════════════════════════
def test_the_window_uses_the_existing_configurable_cooldown(library):
    game = make(library, V1_FOREVER_ANY, block_decision_seconds=5)
    arm(game)
    _, events = piotrek_plays_movement(game)
    opened = next(e for e in events if isinstance(e, ev.MovementDecisionOpened))
    assert opened.seconds == game.block_decision_seconds == 5.0


def test_accepting_lets_the_movement_happen(library):
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    piotrek_plays_movement(game)
    where = game.board.position_of_pawn("zielony")

    game.apply(cmd.AcceptMovement(player_index=seat))
    assert not held(game)
    assert game.board.position_of_pawn("zielony") == where + 1
    assert game.veto_of(seat) is not None, "accepting does not spend it"


def test_the_window_times_out_into_the_movement(library):
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    piotrek_plays_movement(game)
    where = game.board.position_of_pawn("zielony")

    game.apply(cmd.ExpireMovementDecision())
    assert not held(game)
    assert game.board.position_of_pawn("zielony") == where + 1
    assert game.veto_of(seat) is not None


def test_a_blocked_card_goes_to_its_own_discard_pile(library):
    """The blocked card is spent — "spala użytą kartę" — via the shared path."""
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    card, _ = piotrek_plays_movement(game)
    piotrek = piotrek_seat(game)

    game.apply(cmd.BlockMovement(player_index=seat))
    assert game.players[piotrek].card_by_uid(card.uid) is None
    assert game.decks[card.deck_id].find_discarded(card.uid) is card


def test_an_unlimited_veto_never_fires_automatically(library):
    """The forced final block belongs to a veto that is running out of time.

    One that never expires always has another chance coming, so it must open a
    window and let its owner decide rather than spending itself unasked.
    """
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    _, events = piotrek_plays_movement(game)
    assert not any(isinstance(e, ev.MovementBlocked) for e in events)
    assert held(game)


# ═════════════════════════════════════════════════════════════════════════════
# Multiplayer authority
# ═════════════════════════════════════════════════════════════════════════════
def test_the_veto_travels_in_the_snapshot(library):
    game = make(library, V1_FOREVER_ANY)
    arm(game)
    kinds = [s["kind"] for s in game.snapshot()["statuses"]]
    assert StatusKind.MOVEMENT_VETO.value in kinds


def test_a_seat_without_the_veto_cannot_block(library):
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    other = next(s for s in hunter_seats(game) if s != seat)
    piotrek_plays_movement(game)

    events = game.apply(cmd.BlockMovement(player_index=other))
    assert not any(isinstance(e, ev.MovementBlocked) for e in events)
    assert held(game), "the real blocker is still being waited on"


# ═════════════════════════════════════════════════════════════════════════════
# Nie masz Rosji is not disturbed
# ═════════════════════════════════════════════════════════════════════════════
def test_nie_masz_rosji_still_blocks_any_opponent_movement(library):
    """The Chest card keeps the WIDER rule: any opponent, any movement deck."""
    game = make(library)
    piotrek = piotrek_seat(game)
    card = give(game, piotrek, settings.DECK_CHEST, "Nie masz Rosji")
    game.apply(cmd.SetActivePlayer(player_index=piotrek))
    game.apply(cmd.PlayCard(player_index=piotrek, card_uid=card.uid))
    status = game.veto_of(piotrek)
    assert status is not None
    assert status.data["targets"] == effects.VETO_OPPONENTS
    assert status.data["rounds"] == 2, "still two full rounds"

    hunter = hunter_seats(game)[0]
    play_by(game, hunter, settings.DECK_MOVEMENT, MOVE_CARD)
    assert held(game), "a hunter's movement is still blockable by Piotrek"


def test_nie_masz_rosji_still_covers_chest_movement(library):
    game = make(library)
    piotrek = piotrek_seat(game)
    card = give(game, piotrek, settings.DECK_CHEST, "Nie masz Rosji")
    game.apply(cmd.SetActivePlayer(player_index=piotrek))
    game.apply(cmd.PlayCard(player_index=piotrek, card_uid=card.uid))

    hunter = hunter_seats(game)[0]
    play_by(game, hunter, settings.DECK_CHEST, CHEST_MOVE_CARD,
            direction="forward")
    assert held(game)


def test_both_vetoes_can_be_live_at_once(library):
    """Piotrek holding Nie masz Rosji and Dziubdziuch holding his ability."""
    game = make(library, V1_FOREVER_ANY)
    seat = arm(game)
    piotrek = piotrek_seat(game)
    card = give(game, piotrek, settings.DECK_CHEST, "Nie masz Rosji")
    game.apply(cmd.SetActivePlayer(player_index=piotrek))
    game.apply(cmd.PlayCard(player_index=piotrek, card_uid=card.uid))
    assert game.veto_of(piotrek) is not None and game.veto_of(seat) is not None

    # A THIRD hunter moves: only Piotrek's veto covers that.
    other = next(s for s in hunter_seats(game) if s != seat)
    _, events = play_by(game, other, settings.DECK_MOVEMENT, MOVE_CARD)
    opened = next(e for e in events if isinstance(e, ev.MovementDecisionOpened))
    assert opened.blockers == [piotrek], "Dziubdziuch has no say in that one"
