"""
Stage 41 — movement undo, Dług u Tomasza, and Liskowy Konkurs.

Three features around one idea: the WINDOW between a player finishing a turn
and the next player playing a card.  Undo and Liskowy Konkurs both live in it,
so most of the timing tests here are really tests that they share it.

The undo tests are deliberately written against the whole table — hand, both
piles, draw order, turn, statuses — rather than against the pawn, because "put
the pawn back" is the implementation that passes a careless test and loses a
card.
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


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, **kwargs):
    kwargs.setdefault("double_frequency", 0.0)
    game = create_game(
        SessionConfig(num_players=5, board_cells=30, seed=77,
                      chest_open_round=10_000, mod_round_first=10_000, **kwargs),
        library,
    )
    # Everybody on the board, one per field — the START rule gates abilities,
    # and a tidy line makes adjacency arithmetic readable.
    for index, pawn in enumerate(library.pawns):
        game.board.place_pawn(pawn.id, game.board.positions[index + 1].tiles[0].index)
    game._sync_token_positions()
    return game


@pytest.fixture
def game(library):
    return make(library)


# ── fixture helpers ──────────────────────────────────────────────────────────
def movement_deck(game):
    return game.decks[settings.DECK_MOVEMENT]


def give_move(game, seat: int, title: str):
    """Put a named movement card into a hand, from the draw pile."""
    deck = movement_deck(game)
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.players[seat].add_card(card)
    return card


def swap_in(game, seat: int, title: str):
    """Trade a hand card for a named one, so the hand SIZE does not change.

    Adding a card outright pushes the hand over its limit, and a player who is
    already at their limit never refills — which quietly turns "did the drawn
    card come back" into a test of nothing.
    """
    deck = movement_deck(game)
    victim = next(c for c in game.players[seat].hand
                  if c.deck_id == settings.DECK_MOVEMENT and c.title != title)
    game.players[seat].remove_card(victim)
    deck.draw_pile.insert(0, victim)
    return give_move(game, seat, title)


def give_character(game, seat: int, title: str):
    deck = game.deck(settings.DECK_CHARACTERS)
    card = deck.take_titled(title, include_discard=True)
    if card is None:
        for player in game.players:
            if player.character is not None and player.character.title == title:
                card, player.character = player.character, None
                break
    assert card is not None, title
    if game.players[seat].character is not None:
        deck.return_card(game.players[seat].character)
    game.players[seat].character = card
    return card


def give_skill(game, title: str):
    player = next(p for p in game.players if p.is_piotrek)
    deck = game.deck(settings.DECK_SKILLS)
    card = deck.take_titled(title, include_discard=True)
    assert card is not None, title
    if player.skill is not None:
        deck.return_card(player.skill)
    player.skill = card
    return card


def piotrek_seat(game) -> int:
    return next(p.index for p in game.players if p.is_piotrek)


def positions(game):
    return {pawn.id: game.board.position_of_pawn(pawn.id)
            for pawn in game.library.pawns}


def hand_uids(game, seat: int):
    return sorted(card.uid for card in game.players[seat].hand)


def use_skill(game, seat: int, **choices):
    return game.apply(cmd.UseAbility(player_index=seat, source="skill",
                                     choices=choices))


# ═════════════════════════════════════════════════════════════════════════════
# PART A — undo
# ═════════════════════════════════════════════════════════════════════════════
def play_a_turn(game, seat: int = None, title: str = "Zerówka - żółty"):
    """Play one simple card and return (card, what the hand looked like before)."""
    seat = game.active_player_index if seat is None else seat
    card = swap_in(game, seat, title)
    before = {
        "positions": positions(game),
        "hand": hand_uids(game, seat),
        "top": movement_deck(game).draw_pile[-1].uid,
        "active": game.active_player_index,
        "turn": game.turn_counter,
    }
    game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    return card, before


def test_the_window_belongs_to_the_player_who_just_played(game):
    seat = game.active_player_index
    play_a_turn(game, seat)
    assert game.can_undo(seat)
    for other in range(len(game.players)):
        if other != seat:
            assert not game.can_undo(other), other


def test_undo_restores_pawn_positions(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    assert positions(game) != before["positions"], "the card really moved somebody"

    game.apply(cmd.UndoMove(player_index=seat))
    assert positions(game) == before["positions"]


def test_undo_restores_stacking(game):
    """A tower is not just six positions — the ORDER has to come back too."""
    seat = game.active_player_index
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    tile = game.board.positions[4].tiles[0]
    order = [p.id for p in game.library.pawns]
    for pawn_id in order:
        game.board.place_pawn(pawn_id, tile.index)
    game._sync_token_positions()

    card = swap_in(game, seat, "Zerówka - żółty")
    game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    game.apply(cmd.UndoMove(player_index=seat))
    assert list(game.board.pawn_tile(order[0]).stack) == order


def test_undo_returns_the_played_card_to_the_hand(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    assert movement_deck(game).find_discarded(card.uid) is card, "it was discarded"

    game.apply(cmd.UndoMove(player_index=seat))
    assert game.players[seat].card_by_uid(card.uid) is card, "and the SAME object"
    assert movement_deck(game).find_discarded(card.uid) is None


def test_undo_restores_the_whole_hand(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    game.apply(cmd.UndoMove(player_index=seat))
    assert hand_uids(game, seat) == before["hand"]


def test_undo_puts_the_drawn_card_back_on_top_of_the_deck(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    drawn = [c.uid for c in game.players[seat].hand if c.uid not in before["hand"]]
    assert drawn == [before["top"]], "the refill took the top card"

    game.apply(cmd.UndoMove(player_index=seat))
    assert movement_deck(game).draw_pile[-1].uid == before["top"]
    assert game.players[seat].card_by_uid(before["top"]) is None


def test_the_corrective_turn_draws_the_same_card(game):
    """§6: undo must not let a player fish for a different replacement.

    Not implemented as a rule — it falls out of restoring the draw ORDER, which
    is the only reason it can be trusted.
    """
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    first_draw = [c.uid for c in game.players[seat].hand
                  if c.uid not in before["hand"]]
    game.apply(cmd.UndoMove(player_index=seat))

    other = swap_in(game, seat, "Zerówka - zielony")
    hand = hand_uids(game, seat)
    game.apply(cmd.PlayCard(player_index=seat, card_uid=other.uid))
    second_draw = [c.uid for c in game.players[seat].hand if c.uid not in hand]
    assert second_draw == first_draw == [before["top"]]


def test_undo_gives_the_turn_back(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    assert game.active_player_index != seat, "the turn had moved on"
    game.apply(cmd.UndoMove(player_index=seat))
    assert game.active_player_index == seat
    assert game.turn_counter == before["turn"]


def test_undo_cannot_be_done_twice(game):
    seat = game.active_player_index
    play_a_turn(game, seat)
    game.apply(cmd.UndoMove(player_index=seat))
    events = game.apply(cmd.UndoMove(player_index=seat))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_a_player_cannot_undo_somebody_elses_turn(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    thief = next(i for i in range(len(game.players)) if i != seat)

    events = game.apply(cmd.UndoMove(player_index=thief))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert positions(game) != before["positions"], "nothing was rewound"


def test_the_window_closes_when_the_next_player_plays(game):
    seat = game.active_player_index
    card, before = play_a_turn(game, seat)
    following = game.active_player_index
    assert game.can_undo(seat)

    second = swap_in(game, following, "Zerówka - zielony")
    game.apply(cmd.PlayCard(player_index=following, card_uid=second.uid))
    assert not game.can_undo(seat)

    events = game.apply(cmd.UndoMove(player_index=seat))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert positions(game) != before["positions"]


def test_undo_restores_a_status_the_card_granted(game):
    """Undo is about the whole table, not the board.

    Nie masz Rosji grants a veto and moves nobody, so it is the cleanest way to
    ask whether a rewind reaches temporary state at all: a "put the pawn back"
    undo passes every board test above and fails this one.
    """
    seat = game.active_player_index
    deck = game.decks[settings.DECK_CHEST]
    card = next(c for c in deck.draw_pile if c.title == "Nie masz Rosji")
    deck.draw_pile.remove(card)
    game.players[seat].add_card(card)

    game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    assert game.veto_of(seat) is not None, "the card granted it"
    assert game.can_undo(seat)

    game.apply(cmd.UndoMove(player_index=seat))
    assert game.veto_of(seat) is None, "and the rewind took it away again"
    assert game.players[seat].card_by_uid(card.uid) is card


def test_undo_restores_a_charge_the_turn_spent(game):
    """A rewind reaches ability uses too, not only cards and pawns."""
    seat = game.active_player_index
    skill = give_skill(game, "Dług u Tomasza")
    piotrek = piotrek_seat(game)
    game.apply(cmd.SetActivePlayer(player_index=piotrek))

    card = swap_in(game, piotrek, "Zerówka - żółty")
    game.apply(cmd.PlayCard(player_index=piotrek, card_uid=card.uid))
    assert game.can_undo(piotrek)
    skill.uses_left = 0                      # as though the turn had spent it

    game.apply(cmd.UndoMove(player_index=piotrek))
    assert skill.uses_left == 1, "the charge came back with everything else"


def test_undo_is_authoritative_state(game):
    seat = game.active_player_index
    play_a_turn(game, seat)
    assert game.turn_window is not None
    assert game.turn_window.seat == seat


# ═════════════════════════════════════════════════════════════════════════════
# PART B — Dług u Tomasza
# ═════════════════════════════════════════════════════════════════════════════
def arm_ban(game, first: str, second: str):
    give_skill(game, "Dług u Tomasza")
    seat = piotrek_seat(game)
    game.apply(cmd.SetActivePlayer(player_index=seat))
    return seat, use_skill(game, seat, pawn=first, pawn_b=second)


def test_two_different_pawns_are_required(game):
    give_skill(game, "Dług u Tomasza")
    seat = piotrek_seat(game)
    game.apply(cmd.SetActivePlayer(player_index=seat))
    events = use_skill(game, seat, pawn="czerwony", pawn_b="czerwony")
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert "czerwony" not in {option[0] for option in asked.options}


def test_the_ban_respects_the_global_start_rule(game):
    give_skill(game, "Dług u Tomasza")
    seat = piotrek_seat(game)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()
    game.apply(cmd.SetActivePlayer(player_index=seat))

    events = use_skill(game, seat, pawn="czerwony", pawn_b="żółty")
    rejected = next(e for e in events if isinstance(e, ev.ActionRejected))
    assert "starcie" in rejected.reason.lower()
    assert not effects.forbidden_pairs(game)


def test_a_pair_already_apart_is_left_where_it_stands(game):
    before = positions(game)
    arm_ban(game, "czerwony", "żółty")          # fields 1 and 4
    assert positions(game) == before
    assert effects.forbidden_pairs(game) == [("czerwony", "żółty")]


def test_adjacent_pawns_are_separated_by_moving_the_leader(game):
    """§11: the one further behind stays; the one ahead steps forward."""
    arm_ban(game, "zielony", "niebieski")       # fields 2 and 3
    assert game.board.position_of_pawn("zielony") == 2, "the rear pawn stayed"
    assert game.board.position_of_pawn("niebieski") == 4
    assert effects.separation_ok(game, "zielony", "niebieski")


def test_pawns_on_the_same_field_are_separated(game):
    game.board.remove_pawn("różowy")
    game.board.place_pawn("różowy", game.board.pawn_tile("zielony").index)
    game._sync_token_positions()
    assert game.board.position_of_pawn("różowy") == 2

    arm_ban(game, "zielony", "różowy")
    assert game.board.position_of_pawn("zielony") == 2
    assert game.board.position_of_pawn("różowy") == 4, "two fields clears the gap"
    assert effects.separation_ok(game, "zielony", "różowy")


def test_a_stacked_pawn_is_the_one_that_moves(game):
    """§12: the pawn ON TOP steps off — the same rule as the same field."""
    game.board.remove_pawn("różowy")
    game.board.place_pawn("różowy", game.board.pawn_tile("zielony").index)
    game._sync_token_positions()
    tower = list(game.board.pawn_tile("zielony").stack)
    assert tower == ["zielony", "różowy"], "różowy is on top"

    arm_ban(game, "zielony", "różowy")
    assert game.board.position_of_pawn("zielony") == 2, "the lower pawn stayed"
    assert game.board.position_of_pawn("różowy") == 4


def test_adjacency_is_counted_in_positions_not_tiles(library):
    """A doubled row is ONE place: 3a and 3b are not a gap from each other."""
    game = make(library, double_frequency=1.0)
    tiles = game.board.tiles_at_position(3)
    assert len(tiles) == 2
    game.board.remove_pawn("czerwony")
    game.board.remove_pawn("żółty")
    game.board.place_pawn("czerwony", tiles[0].index)
    game.board.place_pawn("żółty", tiles[1].index)
    game._sync_token_positions()
    assert not effects.separation_ok(game, "czerwony", "żółty")


def test_a_card_cannot_push_the_pair_together(game):
    arm_ban(game, "czerwony", "niebieski")      # fields 1 and 3
    seat = game.active_player_index
    card = give_move(game, seat, "Zerówka - czerwony")

    events = game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    assert any(isinstance(e, ev.MoveFizzled) for e in events)
    assert game.board.position_of_pawn("czerwony") == 1, "it never moved"


def test_an_unrelated_pawn_still_moves_normally(game):
    """§13: do not cancel unrelated effects on other pawns."""
    arm_ban(game, "czerwony", "niebieski")
    seat = game.active_player_index
    where = game.board.position_of_pawn("pomarańczowy")
    card = give_move(game, seat, "Zerówka - pomarańczowy")
    game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    assert game.board.position_of_pawn("pomarańczowy") == where + 1


def test_a_move_that_keeps_the_gap_is_allowed(game):
    arm_ban(game, "czerwony", "żółty")          # 1 and 4
    seat = game.active_player_index
    card = give_move(game, seat, "Zerówka - żółty")
    game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    assert game.board.position_of_pawn("żółty") == 5, "away is fine"


def test_dragging_a_banned_pawn_by_hand_cancels_the_effect(game):
    """§14: the testing tool is the one thing allowed to overrule it."""
    arm_ban(game, "czerwony", "żółty")
    assert effects.forbidden_pairs(game)

    destination = game.board.positions[12].tiles[0]
    events = game.apply(cmd.MoveToken(
        pawn_id="czerwony", tile_index=destination.index,
        x=destination.position[0], y=destination.position[1],
    ))
    assert any(isinstance(e, ev.StatusEnded) for e in events)
    assert effects.forbidden_pairs(game) == []


def test_the_ban_lasts_one_full_round(game):
    seat, _ = arm_ban(game, "czerwony", "żółty")
    seen = 0
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.active_player_index == seat:
            break
        seen += 1
        assert effects.forbidden_pairs(game), f"ended early after {seen}"
    assert seen >= 2
    assert effects.forbidden_pairs(game) == []


def test_after_the_ban_expires_the_pawns_may_touch_again(game):
    seat, _ = arm_ban(game, "czerwony", "niebieski")
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if not effects.forbidden_pairs(game):
            break
    assert effects.forbidden_pairs(game) == []

    mover = game.active_player_index
    card = give_move(game, mover, "Zerówka - czerwony")
    game.apply(cmd.PlayCard(player_index=mover, card_uid=card.uid))
    assert game.board.position_of_pawn("czerwony") == 2, "no longer restricted"


# ═════════════════════════════════════════════════════════════════════════════
# PART C — Liskowy Konkurs
# ═════════════════════════════════════════════════════════════════════════════
def seat_atencjusz(game, seat: int = None):
    seat = game.active_player_index if seat is None else seat
    return seat, give_character(game, seat, "Atencjusz")


def test_used_after_a_move_it_hands_the_turn_back(game):
    seat, character = seat_atencjusz(game)
    card, before = play_a_turn(game, seat)
    assert game.active_player_index != seat

    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ExtraTurnGranted) for e in events)
    assert game.active_player_index == seat
    assert character.uses_left == 0


def test_used_after_a_move_the_move_is_not_undone(game):
    """§17: this is a second turn, not a second chance."""
    seat, character = seat_atencjusz(game)
    card, before = play_a_turn(game, seat)
    after_play = positions(game)
    drawn = [c.uid for c in game.players[seat].hand if c.uid not in before["hand"]]

    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert positions(game) == after_play, "the pawn stayed where the card put it"
    assert movement_deck(game).find_discarded(card.uid) is card, "still discarded"
    assert game.players[seat].card_by_uid(card.uid) is None, "NOT back in hand"
    for uid in drawn:
        assert game.players[seat].card_by_uid(uid) is not None, "the draw is kept"


def test_taking_the_extra_turn_closes_the_undo_window(game):
    """§22: he chooses between undo and the extra turn, not both."""
    seat, character = seat_atencjusz(game)
    play_a_turn(game, seat)
    assert game.can_undo(seat), "both were on offer"

    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert not game.can_undo(seat)
    events = game.apply(cmd.UndoMove(player_index=seat))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_used_before_the_move_it_deals_an_extra_card(game):
    seat, character = seat_atencjusz(game)
    before = len(game.players[seat].hand)
    top = movement_deck(game).draw_pile[-1].uid

    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert len(game.players[seat].hand) == before + 1
    assert game.players[seat].card_by_uid(top) is not None
    assert game.extra_play_pending(seat)
    assert character.uses_left == 0


def test_used_before_the_move_the_turn_survives_the_first_card(game):
    seat, character = seat_atencjusz(game)
    first = give_move(game, seat, "Zerówka - żółty")
    second = give_move(game, seat, "Zerówka - zielony")
    game.apply(cmd.UseAbility(player_index=seat, source="character"))

    game.apply(cmd.PlayCard(player_index=seat, card_uid=first.uid))
    assert game.active_player_index == seat, "the turn did NOT pass"
    assert game.board.position_of_pawn("żółty") == 5

    game.apply(cmd.PlayCard(player_index=seat, card_uid=second.uid))
    assert game.active_player_index != seat, "and now it does"
    assert game.board.position_of_pawn("zielony") == 3


def test_the_two_cases_are_not_the_same_thing(game):
    """§19, stated as a difference rather than as two separate facts."""
    before = make(game.library)
    seat, _ = seat_atencjusz(before)
    before.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert before.extra_play_pending(seat)
    assert before.turn_window is None, "nothing has been played yet"

    after = make(game.library)
    seat, _ = seat_atencjusz(after)
    play_a_turn(after, seat)
    after.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert not after.extra_play_pending(seat), "a whole turn, not a second card"


def test_it_cannot_be_used_after_the_next_player_plays(game):
    seat, character = seat_atencjusz(game)
    play_a_turn(game, seat)
    following = game.active_player_index
    second = swap_in(game, following, "Zerówka - zielony")
    game.apply(cmd.PlayCard(player_index=following, card_uid=second.uid))

    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert character.uses_left == 1, "a refusal costs nothing"
    assert game.active_player_index != seat, "and he did not steal the turn"


def test_it_cannot_be_used_on_somebody_elses_window(game):
    """The window belongs to ONE seat, and Atencjusz is not it.

    He must also not be the seat the turn has just passed TO — that player
    holds the turn and may legitimately use the ability before moving, which is
    the other half of the ability rather than a hole in this one.
    """
    mover = game.active_player_index
    play_a_turn(game, mover)
    following = game.active_player_index
    other = next(i for i in range(len(game.players))
                 if i not in (mover, following))
    seat, character = seat_atencjusz(game, other)

    events = game.apply(cmd.UseAbility(player_index=other, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert character.uses_left == 1
    assert game.active_player_index == following, "the turn is untouched"


def test_one_activation_spends_one_use(game):
    seat, character = seat_atencjusz(game)
    assert character.uses_left == 1
    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert character.uses_left == 0
    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
