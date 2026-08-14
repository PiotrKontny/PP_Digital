"""
Stage 38 — Ondrej's Radar and the linked-pawn system.

Two pawns become one movement unit without stopping being two pawns.  Most of
these tests are therefore about the SEAM: the pair moves together, in the right
order, exactly once, from either end, and stops being a pair on time.

The four stack examples in §6 of the brief are pinned twice over — once against
the pure reordering function, where the tower is just a list, and once against
a real board through a real activation.  The first is where a failure is
readable; the second is what proves the rule is actually wired up.
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
from pedzacy_piotrek.engine import victory
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import Status, StatusKind, Subject

from test_stage37_abilities import (          # noqa: E402  (shared fixtures)
    arm, chest_card, clear_start, give_character, hand_card, hunter_seat,
    install_mod, place, play, positions, use,
)

VARIANT_BOTH = "check_both"
VARIANT_ONE = "check_one"

#: The brief's tower, bottom to top, in this project's colours.
YELLOW, PINK, BLUE, RED = "żółty", "różowy", "niebieski", "czerwony"
BRIEF_TOWER = [YELLOW, PINK, BLUE, RED]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, **kwargs):
    kwargs.setdefault("double_frequency", 0.0)
    return create_game(
        SessionConfig(num_players=6, board_cells=40, seed=4321, **kwargs),
        library,
    )


@pytest.fixture
def game(library):
    return make(library)


# ── fixture helpers ──────────────────────────────────────────────────────────
def park_everyone_else(game, *keep: str, base: int = 24) -> None:
    """Put every pawn not named onto a field of its own, well out of the way."""
    spot = base
    for pawn in game.library.pawns:
        if pawn.id in keep:
            continue
        game.board.remove_pawn(pawn.id)
        place(game, pawn.id, spot)
        spot += 1
    game._sync_token_positions()


def build_tower(game, tile_position: int, order) -> int:
    """Stand a tower up bottom-to-top on one field and return its tile index."""
    for pawn_id in order:
        game.board.remove_pawn(pawn_id)
    for pawn_id in order:
        place(game, pawn_id, tile_position)
    tile = game.board.pawn_tile(order[0])
    assert list(tile.stack) == list(order)
    return tile.index


def tower_at(game, pawn_id):
    tile = game.board.pawn_tile(pawn_id)
    return list(tile.stack) if tile is not None else []


def link(game, first: str, second: str, seat: int = None) -> int:
    """Activate Radar for real, through the command path."""
    seat = arm(game, "Ondrej", seat)
    events = use(game, seat, pawns=f"{first},{second}")
    assert any(isinstance(e, ev.AbilityUsed) for e in events), events
    assert game.statuses.of_kind(StatusKind.LINKED)
    return seat


# ═════════════════════════════════════════════════════════════════════════════
# §2 — ordered selection
# ═════════════════════════════════════════════════════════════════════════════
def test_radar_asks_one_ordered_question_like_plagiat(game):
    """Not two prompts in a row: one multi-select, count 2, order significant."""
    seat = arm(game, "Ondrej")
    asked = next(e for e in use(game, seat) if isinstance(e, ev.ChoiceRequired))
    assert asked.key == "pawns", "the same key Plagiat! uses"
    assert asked.kind == "pawn"
    assert asked.count == 2 and asked.ordered


def test_the_selection_order_is_stored_and_not_sorted(game):
    """Both directions of the same pair are DIFFERENT answers."""
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, 6)
    place(game, PINK, 6)
    link(game, PINK, BLUE)
    assert effects.linked_group(game, BLUE) == [PINK, BLUE]

    other = make(game.library)
    park_everyone_else(other, BLUE, PINK)
    place(other, BLUE, 6)
    place(other, PINK, 6)
    link(other, BLUE, PINK)
    assert effects.linked_group(other, BLUE) == [BLUE, PINK]


def test_radar_cannot_be_activated_while_a_pawn_is_on_start(game):
    """The global rule, inherited rather than re-implemented."""
    seat = hunter_seat(game)
    give_character(game, seat, "Ondrej")
    clear_start(game)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()
    game.apply(cmd.SetActivePlayer(player_index=seat))

    events = use(game, seat, pawns=f"{BLUE},{PINK}")
    rejected = next(e for e in events if isinstance(e, ev.ActionRejected))
    assert "starcie" in rejected.reason.lower()
    assert not game.statuses.of_kind(StatusKind.LINKED)
    assert game.players[seat].character.uses_left == 1


# ═════════════════════════════════════════════════════════════════════════════
# §4 — different fields
# ═════════════════════════════════════════════════════════════════════════════
def test_the_pawn_further_behind_walks_onto_the_one_ahead(game):
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, 4)
    place(game, PINK, 9)
    link(game, BLUE, PINK)
    assert game.board.position_of_pawn(BLUE) == 9
    assert game.board.position_of_pawn(PINK) == 9


def test_it_does_not_matter_which_of_the_two_was_picked_first(game):
    """The pawn that moves is decided by the BOARD, not by the picking order."""
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, 4)
    place(game, PINK, 9)
    link(game, PINK, BLUE)          # the one in front picked first
    assert game.board.position_of_pawn(BLUE) == 9, "the rear pawn still moved"
    assert game.board.position_of_pawn(PINK) == 9


def test_arriving_is_seated_above_its_partner_not_on_the_roof(game):
    """A third pawn already standing there does NOT end up between the pair.

    This test used to assert the opposite — the arrival landing on top of the
    whole tower — which was the bug: it left the pair split by somebody else's
    pawn and the arrival riding a pawn it had nothing to do with.
    """
    park_everyone_else(game, BLUE, PINK, RED)
    place(game, BLUE, 4)
    place(game, PINK, 9)
    place(game, RED, 9)             # RED lands on PINK
    assert tower_at(game, PINK) == [PINK, RED]
    link(game, BLUE, PINK)
    assert tower_at(game, PINK) == [PINK, BLUE, RED]


def test_the_pair_is_linked_after_moving_together(game):
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, 4)
    place(game, PINK, 9)
    link(game, BLUE, PINK)
    assert game.statuses.linked_partners(BLUE) == [PINK]
    assert game.statuses.linked_partners(PINK) == [BLUE]


# ═════════════════════════════════════════════════════════════════════════════
# §5 / §6 — the same-field reordering, all four worked examples
# ═════════════════════════════════════════════════════════════════════════════
BRIEF_CASES = [
    pytest.param(BLUE, PINK, [YELLOW, BLUE, PINK, RED], id="A-blue-then-pink"),
    pytest.param(YELLOW, RED, [YELLOW, RED, PINK, BLUE], id="B-yellow-then-red"),
    pytest.param(RED, YELLOW, [PINK, BLUE, RED, YELLOW], id="C-red-then-yellow"),
    pytest.param(YELLOW, PINK, [YELLOW, PINK, BLUE, RED], id="D-already-in-order"),
]


@pytest.mark.parametrize("first,second,expected", BRIEF_CASES)
def test_the_reordering_rule_alone(first, second, expected):
    """The pure function, where a failure is readable as four names."""
    assert effects.restack_for_link(BRIEF_TOWER, first, second) == expected


@pytest.mark.parametrize("first,second,expected", BRIEF_CASES)
def test_the_reordering_rule_on_a_real_board(game, first, second, expected):
    """The same four cases through a real activation — the rule is wired up."""
    park_everyone_else(game, *BRIEF_TOWER)
    build_tower(game, 7, BRIEF_TOWER)
    link(game, first, second)
    assert tower_at(game, first) == expected
    assert all(game.board.position_of_pawn(p) == 7 for p in BRIEF_TOWER), (
        "a restack moves nobody between fields"
    )


def test_a_pair_already_in_order_leaves_the_tower_untouched(game):
    """Case D asserted as an ABSENCE of an event, not just an equal list."""
    park_everyone_else(game, *BRIEF_TOWER)
    build_tower(game, 7, BRIEF_TOWER)
    seat = arm(game, "Ondrej")
    events = use(game, seat, pawns=f"{YELLOW},{PINK}")
    assert not any(isinstance(e, ev.TileRestacked) for e in events)
    assert tower_at(game, YELLOW) == BRIEF_TOWER


def test_the_first_pick_always_ends_up_directly_under_the_second(game):
    """The invariant the four examples are instances of."""
    for first, second, _ in [(c.values[0], c.values[1], None)
                             for c in BRIEF_CASES]:
        state = make(game.library)
        park_everyone_else(state, *BRIEF_TOWER)
        build_tower(state, 7, BRIEF_TOWER)
        link(state, first, second)
        tower = tower_at(state, first)
        assert tower.index(second) == tower.index(first) + 1, (first, second)


def test_the_other_pawns_keep_their_relative_order(game):
    park_everyone_else(game, *BRIEF_TOWER)
    build_tower(game, 7, BRIEF_TOWER)
    link(game, RED, YELLOW)
    tower = tower_at(game, RED)
    others = [p for p in tower if p not in (RED, YELLOW)]
    assert others == [PINK, BLUE], "untouched pawns keep their order"


# ═════════════════════════════════════════════════════════════════════════════
# §7 / §8 / §9 — movement as one unit
# ═════════════════════════════════════════════════════════════════════════════
def linked_pair_on(game, position: int = 6):
    """BLUE under PINK, linked, alone on their field."""
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, position)
    place(game, PINK, position)
    seat = link(game, BLUE, PINK)
    assert tower_at(game, BLUE) == [BLUE, PINK]
    return seat


def test_moving_the_lower_pawn_brings_the_upper_one(game):
    seat = linked_pair_on(game)
    play(game, seat, hand_card(game, f"Zerówka - {BLUE}", seat))
    assert game.board.position_of_pawn(BLUE) == 7
    assert game.board.position_of_pawn(PINK) == 7


def test_moving_the_upper_pawn_brings_the_lower_one(game):
    """The case the tower rule alone would get wrong: the partner is BELOW."""
    seat = linked_pair_on(game)
    play(game, seat, hand_card(game, f"Zerówka - {PINK}", seat))
    assert game.board.position_of_pawn(PINK) == 7
    assert game.board.position_of_pawn(BLUE) == 7


@pytest.mark.parametrize("moved", [BLUE, PINK])
def test_the_pairs_internal_order_survives_a_move_from_either_end(game, moved):
    seat = linked_pair_on(game)
    play(game, seat, hand_card(game, f"Zerówka - {moved}", seat))
    assert tower_at(game, BLUE) == [BLUE, PINK], (
        "the pair is put down in the order it was picked up"
    )


def test_a_linked_pair_moves_once_not_twice(game):
    """Balbinka moves everybody; the pair must go the card's distance, once."""
    seat = arm(game, "Ondrej")
    for index, pawn in enumerate(game.library.pawns):
        game.board.remove_pawn(pawn.id)
        place(game, pawn.id, 5 + index * 2)
    use(game, seat, pawns=f"{BLUE},{PINK}")
    before = positions(game)

    play(game, seat, chest_card(game, "Balbinka", seat), direction="forward")
    after = positions(game)
    control = next(p.id for p in game.library.pawns if p.id not in (BLUE, PINK))
    step = after[control] - before[control]
    assert step != 0, "the card really moved somebody"
    assert after[BLUE] - before[BLUE] == step, "not double"
    assert after[PINK] - before[PINK] == step, "not double"


def test_the_pair_lands_on_top_of_a_pawn_already_there(game):
    seat = linked_pair_on(game, 6)
    game.board.remove_pawn(RED)
    place(game, RED, 7)
    play(game, seat, hand_card(game, f"Zerówka - {BLUE}", seat))
    assert tower_at(game, RED) == [RED, BLUE, PINK], (
        "stacked normally, pair intact and the right way up"
    )


def test_a_pawn_stacked_above_the_pair_does_not_unlink_it(game):
    seat = linked_pair_on(game, 6)
    game.board.remove_pawn(RED)
    place(game, RED, 6)                      # RED lands on top of the pair
    assert tower_at(game, BLUE) == [BLUE, PINK, RED]
    assert game.statuses.linked_partners(BLUE) == [PINK]
    play(game, seat, hand_card(game, f"Zerówka - {BLUE}", seat))
    assert tower_at(game, BLUE) == [BLUE, PINK, RED], "the tower came along"
    assert game.statuses.linked_partners(BLUE) == [PINK], "still linked"


def test_an_unlinked_pawn_still_moves_by_itself(game):
    """The regression that matters: linking two pawns changes nobody else."""
    seat = linked_pair_on(game)
    game.board.remove_pawn(RED)
    place(game, RED, 12)
    play(game, seat, hand_card(game, f"Zerówka - {RED}", seat))
    assert game.board.position_of_pawn(RED) == 13
    assert game.board.position_of_pawn(BLUE) == 6, "the pair did not react"
    assert game.board.position_of_pawn(PINK) == 6


# ═════════════════════════════════════════════════════════════════════════════
# §10 / §11 / §12 — checking, and the two variants
# ═════════════════════════════════════════════════════════════════════════════
def link_two_innocents(game):
    """Link two pawns that are definitely not Piotrek, and return them."""
    hidden = victory.hidden_pawn(game)
    innocent = [p.id for p in game.library.pawns if p.id != hidden]
    first, second = innocent[0], innocent[1]
    park_everyone_else(game, first, second)
    place(game, first, 6)
    place(game, second, 6)
    link(game, first, second)
    return first, second


def test_variant_1_checks_both_linked_pawns(library):
    game = make(library, card_variants={"Ondrej": VARIANT_BOTH})
    first, second = link_two_innocents(game)
    assert set(victory.checked_with(game, first)) == {first, second}
    assert set(victory.checked_with(game, second)) == {first, second}


def test_variant_2_checks_only_the_pawn_that_was_checked(library):
    game = make(library, card_variants={"Ondrej": VARIANT_ONE})
    first, second = link_two_innocents(game)
    assert victory.checked_with(game, first) == [first]
    assert victory.checked_with(game, second) == [second]


def test_variant_2_still_moves_the_pair_together(library):
    """The only difference is checking; movement is identical."""
    game = make(library, card_variants={"Ondrej": VARIANT_ONE})
    seat = linked_pair_on(game)
    play(game, seat, hand_card(game, f"Zerówka - {PINK}", seat))
    assert game.board.position_of_pawn(BLUE) == 7
    assert tower_at(game, BLUE) == [BLUE, PINK]


def test_a_failed_check_on_a_linked_pawn_crosses_both_off(library):
    """Variant 1, through the real check: one question, two colours ruled out."""
    game = make(library, card_variants={"Ondrej": VARIANT_BOTH})
    first, second = link_two_innocents(game)
    game.pending_pawn_check = (first, -1)

    follow_ups = victory.review(game)
    eliminated = [c.pawn_id for c in follow_ups
                  if isinstance(c, cmd.EliminatePawn)]
    assert set(eliminated) == {first, second}
    game.apply_many(follow_ups)
    assert first in game.eliminated_pawns and second in game.eliminated_pawns


def test_variant_2_crosses_off_only_the_checked_colour(library):
    game = make(library, card_variants={"Ondrej": VARIANT_ONE})
    first, second = link_two_innocents(game)
    game.pending_pawn_check = (first, -1)

    game.apply_many(victory.review(game))
    assert first in game.eliminated_pawns
    assert second not in game.eliminated_pawns


def test_checking_a_pawn_linked_to_piotrek_wins_under_variant_1(library):
    """The consequence of "both pawns are checked", stated out loud.

    If the partner IS Piotrek, checking the other one finds him — that is what
    the rule means, and declining to notice would be a different rule.
    """
    game = make(library, card_variants={"Ondrej": VARIANT_BOTH})
    hidden = victory.hidden_pawn(game)
    decoy = next(p.id for p in game.library.pawns if p.id != hidden)
    park_everyone_else(game, hidden, decoy)
    place(game, hidden, 6)
    place(game, decoy, 6)
    link(game, decoy, hidden)
    game.pending_pawn_check = (decoy, -1)

    follow_ups = victory.review(game)
    assert follow_ups and isinstance(follow_ups[0], cmd.DeclareVictory)
    assert follow_ups[0].outcome == victory.Outcome.HUNTERS.value


def test_variant_2_does_not_find_piotrek_through_the_link(library):
    game = make(library, card_variants={"Ondrej": VARIANT_ONE})
    hidden = victory.hidden_pawn(game)
    decoy = next(p.id for p in game.library.pawns if p.id != hidden)
    park_everyone_else(game, hidden, decoy)
    place(game, hidden, 6)
    place(game, decoy, 6)
    link(game, decoy, hidden)
    game.pending_pawn_check = (decoy, -1)

    follow_ups = victory.review(game)
    assert not any(isinstance(c, cmd.DeclareVictory) for c in follow_ups)
    assert [c.pawn_id for c in follow_ups
            if isinstance(c, cmd.EliminatePawn)] == [decoy]


def test_a_colour_already_ruled_out_is_not_checked_again(library):
    game = make(library, card_variants={"Ondrej": VARIANT_BOTH})
    first, second = link_two_innocents(game)
    game.eliminated_pawns.append(second)
    assert victory.checked_with(game, first) == [first]


def test_the_variant_is_recorded_on_the_link_not_looked_up_later(library):
    """A variant switched mid-match must not rewrite a running link."""
    game = make(library, card_variants={"Ondrej": VARIANT_BOTH})
    first, second = link_two_innocents(game)
    game.apply(cmd.SetCardVariant(deck_id=settings.DECK_CHARACTERS,
                                  title="Ondrej", variant=VARIANT_ONE))
    assert set(victory.checked_with(game, first)) == {first, second}, (
        "the running link keeps the rule it was made under"
    )


# ═════════════════════════════════════════════════════════════════════════════
# §13 — expiry
# ═════════════════════════════════════════════════════════════════════════════
def test_the_link_lasts_until_ondrejs_next_turn(game):
    seat = linked_pair_on(game)
    order = game.seat_order()
    seen = 0
    for _ in range(len(order) * 3):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.active_player_index == seat:
            break
        seen += 1
        assert game.statuses.of_kind(StatusKind.LINKED), (
            f"ended early after {seen} turns"
        )
    assert game.active_player_index == seat
    assert seen >= 2, "several turns really did pass"
    assert not game.statuses.of_kind(StatusKind.LINKED)


def test_one_intervening_turn_is_not_a_full_round(game):
    seat = linked_pair_on(game)
    other = next(p.index for p in game.players if p.index != seat)
    game.apply(cmd.SetActivePlayer(player_index=other))
    assert game.statuses.of_kind(StatusKind.LINKED)


def test_after_expiry_the_pawns_move_independently(game):
    seat = linked_pair_on(game)
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.active_player_index == seat and \
                not game.statuses.of_kind(StatusKind.LINKED):
            break
    assert not game.statuses.of_kind(StatusKind.LINKED)
    assert game.statuses.linked_partners(BLUE) == []

    where = game.board.position_of_pawn(PINK)
    play(game, seat, hand_card(game, f"Zerówka - {PINK}", seat))
    assert game.board.position_of_pawn(PINK) == where + 1
    assert game.board.position_of_pawn(BLUE) != game.board.position_of_pawn(PINK), (
        "BLUE stayed behind — they are two pawns again"
    )


def test_after_expiry_checking_is_individual_again(library):
    game = make(library, card_variants={"Ondrej": VARIANT_BOTH})
    first, second = link_two_innocents(game)
    seat = next(p.index for p in game.players
                if p.character is not None and p.character.title == "Ondrej")
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if not game.statuses.of_kind(StatusKind.LINKED):
            break
    assert victory.checked_with(game, first) == [first]


def test_no_stale_link_state_is_left_behind(game):
    seat = linked_pair_on(game)
    for _ in range(40):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if not game.statuses.of_kind(StatusKind.LINKED):
            break
    assert effects.linked_group(game, BLUE) == []
    assert effects.link_status(game, BLUE) is None
    assert game.statuses.linked_partners(BLUE) == []
    # Stand them apart so the TOWER rule is out of the picture: whatever
    # travels with BLUE now is not a leftover link.
    game.board.remove_pawn(PINK)
    place(game, PINK, 15)
    assert effects.travellers(game, BLUE) == ()
    assert not any(status["kind"] == "linked"
                   for status in game.snapshot()["statuses"])


# ═════════════════════════════════════════════════════════════════════════════
# §14 — Granny Costume stays authoritative
# ═════════════════════════════════════════════════════════════════════════════
def freeze_pawn(game, pawn_id: str, seat: int = None) -> int:
    seat = arm(game, "Big D Randy", seat)
    events = use(game, seat, pawn=pawn_id)
    assert any(isinstance(e, ev.AbilityUsed) for e in events), events
    assert game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    return seat


def test_radar_cannot_drag_a_frozen_pawn_into_the_link(game):
    """Making the link is itself a move, so the freeze refuses it."""
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, 5)
    place(game, PINK, 9)
    frozen_seat = freeze_pawn(game, BLUE)

    ondrej = next(p.index for p in game.players
                  if not p.is_piotrek and p.index != frozen_seat)
    give_character(game, ondrej, "Ondrej")
    game.apply(cmd.SetActivePlayer(player_index=ondrej))
    events = use(game, ondrej, pawns=f"{PINK},{BLUE}")

    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.board.position_of_pawn(BLUE) == 5, "the frozen pawn stayed"
    assert not game.statuses.of_kind(StatusKind.LINKED)
    assert game.players[ondrej].character.uses_left == 1, "a refusal costs nothing"


def test_a_frozen_anchor_is_fine_because_it_does_not_move(game):
    """Only the pawn that would WALK is stopped by a freeze."""
    park_everyone_else(game, BLUE, PINK)
    place(game, BLUE, 5)
    place(game, PINK, 9)
    frozen_seat = freeze_pawn(game, PINK)          # the one in front

    ondrej = next(p.index for p in game.players
                  if not p.is_piotrek and p.index != frozen_seat)
    give_character(game, ondrej, "Ondrej")
    game.apply(cmd.SetActivePlayer(player_index=ondrej))
    use(game, ondrej, pawns=f"{BLUE},{PINK}")
    assert game.board.position_of_pawn(BLUE) == 9, "the free pawn walked over"
    assert game.statuses.linked_partners(BLUE) == [PINK]


def test_a_freeze_landing_on_one_member_leaves_it_behind(game):
    """The link does not become a way to move a frozen pawn."""
    seat = linked_pair_on(game)
    game.statuses.add(Status.for_pawn(StatusKind.FROZEN, BLUE, source="test"))
    play(game, seat, hand_card(game, f"Zerówka - {PINK}", seat))
    assert game.board.position_of_pawn(BLUE) == 6, "frozen, so it stayed"
    assert game.board.position_of_pawn(PINK) == 7, "the free one still moved"


def test_a_card_aimed_at_the_frozen_member_moves_nobody(game):
    seat = linked_pair_on(game)
    game.statuses.add(Status.for_pawn(StatusKind.FROZEN, BLUE, source="test"))
    before = positions(game)
    play(game, seat, hand_card(game, f"Zerówka - {BLUE}", seat))
    assert positions(game) == before


# ═════════════════════════════════════════════════════════════════════════════
# §15 / §16 / §17 — manual movement, reuse, authority
# ═════════════════════════════════════════════════════════════════════════════
def test_dragging_a_linked_pawn_by_hand_follows_the_existing_semantics(game):
    """Manual movement breaks a temporary effect, as it does for the freeze.

    The link is a temporary effect on a pair, so dragging either member out of
    it ends it — the same rule Granny Costume already follows, applied through
    the same helper rather than a second manual-movement engine.
    """
    linked_pair_on(game)
    destination = game.board.positions[12].tiles[0]
    events = game.apply(cmd.MoveToken(
        pawn_id=PINK, tile_index=destination.index,
        x=destination.position[0], y=destination.position[1],
    ))
    assert any(isinstance(e, ev.StatusEnded) for e in events)
    assert not game.statuses.of_kind(StatusKind.LINKED)
    assert game.board.position_of_pawn(PINK) == 12
    assert game.board.position_of_pawn(BLUE) == 6, "left behind, no longer a pair"


def test_no_card_mentions_radar_anywhere(game):
    """§16: the rule is in the movement layer, not in a list of card names."""
    source = (Path(__file__).resolve().parent.parent
              / "pedzacy_piotrek" / "engine" / "effects.py").read_text("utf-8")
    for title in ("Zerówka", "Balbinka", "Plagiat", "Przepis", "Gejtos"):
        assert f'== "{title}' not in source
    # The reusable concept exists and is what movement asks.
    assert callable(effects.travellers) and callable(effects.linked_group)


def test_the_link_is_authoritative_state_that_survives_a_snapshot(game):
    linked_pair_on(game)
    linked = [s for s in game.snapshot()["statuses"] if s["kind"] == "linked"]
    assert linked, "the link travels to every client in the fingerprint"


def test_a_client_cannot_invent_a_link_by_playing_a_card(game):
    """Only the ability grants it, through the ordinary command path."""
    seat = arm(game, "Ondrej")
    play(game, seat, hand_card(game, f"Zerówka - {BLUE}", seat))
    assert not game.statuses.of_kind(StatusKind.LINKED)


def test_using_radar_spends_exactly_one_use(game):
    seat = linked_pair_on(game)
    assert game.players[seat].character.uses_left == 0
    events = use(game, seat, pawns=f"{RED},{YELLOW}")
    assert any(isinstance(e, ev.ActionRejected) for e in events)


# ═════════════════════════════════════════════════════════════════════════════
# Bugfix: inserting directly above the anchor instead of on the roof
# ═════════════════════════════════════════════════════════════════════════════
GREEN = "zielony"


@pytest.mark.parametrize("anchor,expected", [
    ("A", ["A", "X", "B", "C", "D", "E"]),
    ("B", ["A", "B", "X", "C", "D", "E"]),
    ("C", ["A", "B", "C", "X", "D", "E"]),
    ("E", ["A", "B", "C", "D", "E", "X"]),
])
def test_insert_above_puts_the_arrival_directly_over_the_anchor(anchor, expected):
    """Bottom, middle and top — the top needs no special case and has none."""
    assert effects.insert_above(["A", "B", "C", "D", "E"], anchor, ["X"]) == expected


def test_insert_above_keeps_everybody_else_in_relative_order():
    result = effects.insert_above(["A", "B", "C", "D", "E"], "B", ["X"])
    assert [p for p in result if p != "X"] == ["A", "B", "C", "D", "E"]


def test_a_pawn_joining_a_tower_lands_directly_above_its_partner(game):
    """The brief's case: pink under yellow, green arrives and goes BETWEEN."""
    park_everyone_else(game, GREEN, PINK, YELLOW)
    place(game, GREEN, 3)
    place(game, PINK, 5)
    place(game, YELLOW, 5)
    assert tower_at(game, PINK) == [PINK, YELLOW]

    link(game, PINK, GREEN)
    assert tower_at(game, PINK) == [PINK, GREEN, YELLOW]
    assert game.board.position_of_pawn(GREEN) == 5


@pytest.mark.parametrize("anchor_at", [0, 1, 2])
def test_the_arrival_is_seated_above_the_anchor_wherever_it_stands(game, anchor_at):
    """The anchor at the bottom, in the middle and on top of a three-tower."""
    tower = [YELLOW, PINK, RED]
    anchor = tower[anchor_at]
    park_everyone_else(game, GREEN, *tower)
    place(game, GREEN, 3)
    build_tower(game, 5, tower)

    link(game, anchor, GREEN)
    result = tower_at(game, anchor)
    assert result.index(GREEN) == result.index(anchor) + 1
    assert [p for p in result if p != GREEN] == tower, "others keep their order"


def test_the_radar_pair_survives_the_insertion(game):
    park_everyone_else(game, GREEN, PINK, YELLOW)
    place(game, GREEN, 3)
    place(game, PINK, 5)
    place(game, YELLOW, 5)
    link(game, PINK, GREEN)
    assert game.statuses.linked_partners(PINK) == [GREEN]
    assert game.statuses.linked_partners(GREEN) == [PINK]
    assert effects.linked_group(game, GREEN) == [PINK, GREEN]


def test_a_pawn_above_the_pair_is_not_part_of_it(game):
    """The gameplay bug the insertion order caused, asserted directly."""
    park_everyone_else(game, GREEN, PINK, YELLOW)
    place(game, GREEN, 3)
    place(game, PINK, 5)
    place(game, YELLOW, 5)
    seat = link(game, PINK, GREEN)
    assert tower_at(game, PINK) == [PINK, GREEN, YELLOW]

    # YELLOW is on top of the pair, so the tower rule carries nothing with it.
    assert game.statuses.linked_partners(YELLOW) == []
    play(game, seat, hand_card(game, f"Zerówka - {YELLOW}", seat))
    assert game.board.position_of_pawn(YELLOW) == 6
    assert game.board.position_of_pawn(GREEN) == 5, (
        "green was NOT dragged off by a pawn it merely stood under"
    )
    assert game.board.position_of_pawn(PINK) == 5
    assert game.statuses.linked_partners(PINK) == [GREEN], "still a pair"


@pytest.mark.parametrize("moved", [PINK, GREEN])
def test_either_member_still_moves_the_pair_after_an_insertion(game, moved):
    """The pair travels together and stays the right way up.

    YELLOW comes along too, and that is the ORDINARY TOWER RULE rather than
    anything to do with Radar: it is standing above the pair, and a pawn
    standing on a pawn that moves goes with it.  What matters here is that the
    pair is intact and adjacent, and that YELLOW is a passenger rather than a
    member — the previous test is the one that proves it is not a member.
    """
    park_everyone_else(game, GREEN, PINK, YELLOW)
    place(game, GREEN, 3)
    place(game, PINK, 5)
    place(game, YELLOW, 5)
    seat = link(game, PINK, GREEN)

    play(game, seat, hand_card(game, f"Zerówka - {moved}", seat))
    assert game.board.position_of_pawn(PINK) == 6
    assert game.board.position_of_pawn(GREEN) == 6
    tower = tower_at(game, PINK)
    assert tower.index(GREEN) == tower.index(PINK) + 1, "pair intact, in order"
    assert game.statuses.linked_partners(PINK) == [GREEN]


def test_the_insertion_is_one_restack_not_a_second_stacking_system(game):
    """It goes through the SAME operation the same-field case already used."""
    park_everyone_else(game, GREEN, PINK, YELLOW)
    place(game, GREEN, 3)
    place(game, PINK, 5)
    place(game, YELLOW, 5)
    seat = arm(game, "Ondrej")
    events = use(game, seat, pawns=f"{PINK},{GREEN}")
    restacks = [e for e in events if isinstance(e, ev.TileRestacked)]
    assert len(restacks) == 1
    assert restacks[0].order == [PINK, GREEN, YELLOW]
