"""
Ability and effect-engine tests.

These exercise the layer this stage introduced: a card or ability turns into a
Plan of operations, the executor applies it, and persistent gameplay states
outlive the action that created them.  Nothing here touches pygame.
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
from pedzacy_piotrek.engine.setup import create_game, starting_hand_size
from pedzacy_piotrek.engine.statuses import StatusKind, Subject


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def game(library):
    return create_game(
        SessionConfig(num_players=4, board_cells=30, seed=4321,
                      double_frequency=0.0),
        library,
    )


def hunter_seat(game) -> int:
    """A seat that is not Piotrek — handing it a character card is safe."""
    return next(p.index for p in game.players if not p.is_piotrek)


def take_movement_card(game, title: str, player_index: int = 0):
    """Move a named movement card into a hand, wherever it currently is."""
    deck = game.deck(settings.DECK_MOVEMENT)
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
    game.players[player_index].add_card(card)
    return card


def place_all(game, positions: dict = None) -> None:
    """Put every pawn on the board, spread out unless told otherwise."""
    positions = positions or {}
    for index, pawn in enumerate(game.library.pawns):
        place(game, pawn.id, positions.get(pawn.id, index + 3))


def give_character(game, player_index: int, title: str):
    """Put a specific character card in front of a seat."""
    deck = game.deck(settings.DECK_CHARACTERS)
    card = deck.take_titled(title)
    if card is None:
        for player in game.players:
            if player.character is not None and player.character.title == title:
                card = player.character
                player.character = None
                break
    assert card is not None, title
    player = game.players[player_index]
    if player.character is not None:
        deck.return_card(player.character)
    player.character = card
    return card


def give_skill(game, title: str):
    piotrek = next(p for p in game.players if p.is_piotrek)
    deck = game.deck(settings.DECK_SKILLS)
    if piotrek.skill is not None and piotrek.skill.title == title:
        return piotrek.skill
    card = deck.take_titled(title)
    if card is None:
        card = next(c for c in deck.discard_pile if c.title == title)
        deck.discard_pile.remove(card)
    if piotrek.skill is not None:
        deck.return_card(piotrek.skill)
    piotrek.skill = card
    return card


def place(game, pawn_id: str, position: int):
    tile = game.board.positions[position].tiles[0]
    game.board.place_pawn(pawn_id, tile.index)
    game._sync_token_positions()


def use(game, player_index: int, source: str = "character", **choices):
    return game.apply(
        cmd.UseAbility(player_index=player_index, source=source, choices=choices)
    )


# ── the registry itself ──────────────────────────────────────────────────────
def test_every_ability_in_the_data_has_a_handler(library):
    """A JSON ability with no handler would fail silently at the table."""
    for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
        for definition in library.deck(deck_id).cards:
            if definition.ability is None:
                continue
            assert definition.ability.type in effects.HANDLERS, definition.title


def test_every_card_effect_in_the_data_has_a_handler(library):
    for deck in library.decks.values():
        for definition in deck.cards:
            if definition.effect is not None:
                assert definition.effect.type in effects.HANDLERS, definition.title


def test_abilities_declare_their_use_counts(library):
    """The '1x' / '5x' in the descriptions must exist as structured data too."""
    expected = {"Big D Randy": 1, "Dziad": 2, "Atencjusz": 1}
    for definition in library.deck(settings.DECK_CHARACTERS).cards:
        if definition.title in expected:
            assert definition.uses == expected[definition.title]
    skills = {c.title: c.uses for c in library.deck(settings.DECK_SKILLS).cards}
    assert skills == {"ChatGPT": 5, "Ice Block": 1, "Dług u Tomasza": 1}


def test_registering_a_new_effect_needs_no_other_change():
    """The point of the registry: one decorator and the engine knows it."""
    from pedzacy_piotrek.cards.base_card import EffectSpec

    @effects.effect("test_only_smoke")
    def _handler(spec, ctx):
        return effects.Plan((effects.Announce("ok"),), "test")

    try:
        spec = EffectSpec.from_dict({"type": "test_only_smoke"})
        result = effects.resolve_spec(None, spec)
        assert result.ok and result.description == "test"
    finally:
        del effects.HANDLERS["test_only_smoke"]


# ── uses ─────────────────────────────────────────────────────────────────────
def test_using_an_ability_spends_one_use(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Atencjusz")
    card = game.players[SEAT].character
    assert card.uses_left == 1

    events = use(game, SEAT)
    used = next(e for e in events if isinstance(e, ev.AbilityUsed))
    assert used.uses_left == 0
    assert card.uses_left == 0


def test_an_exhausted_ability_is_refused(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Atencjusz")
    use(game, SEAT)
    events = use(game, SEAT)
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not game.players[SEAT].character.ability_available


def test_a_two_use_ability_survives_the_first_use(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Dziad")
    place(game, "żółty", 4)
    use(game, SEAT, pawn="żółty", move="1")
    assert game.players[SEAT].character.uses_left == 1
    assert game.players[SEAT].character.ability_available


# ── individual abilities ─────────────────────────────────────────────────────
def test_big_d_randy_freezes_a_lone_pawn(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Big D Randy")
    place(game, "żółty", 5)
    events = use(game, SEAT, pawn="żółty")
    assert any(isinstance(e, ev.StatusGranted) for e in events)
    assert game.statuses.pawn_has(StatusKind.FROZEN, "żółty")


def test_big_d_randy_refuses_a_pawn_in_a_tower(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Big D Randy")
    place(game, "żółty", 5)
    place(game, "różowy", 5)
    events = use(game, SEAT, pawn="żółty")
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "żółty")
    assert game.players[SEAT].character.uses_left == 1, "a refused ability costs nothing"


def test_a_frozen_pawn_cannot_be_moved_by_a_card(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Big D Randy")
    place(game, "żółty", 5)
    use(game, SEAT, pawn="żółty")

    deck = game.deck(settings.DECK_MOVEMENT)
    card = next(c for c in deck.draw_pile if c.title == "Zerówka - żółty")
    deck.draw_pile.remove(card)
    game.players[SEAT].add_card(card)

    events = game.apply(cmd.PlayCard(player_index=SEAT, card_uid=card.uid))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.board.position_of_pawn("żółty") == 5


def test_lubin_freezes_piotrek(game):
    seat = hunter_seat(game)
    give_character(game, seat, "Lubin")
    piotrek = next(p for p in game.players if p.is_piotrek)
    use(game, seat)
    assert game.statuses.player_has(StatusKind.SKIP_TURN, piotrek.index)


def test_mitoman_moves_the_front_pawn_onto_the_back_one(game):
    seat = hunter_seat(game)
    give_character(game, seat, "Mitoman")
    place_all(game, {"żółty": 14, "czerwony": 2})
    events = use(game, seat)
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    assert walked.pawn_id == "żółty", "the front-most pawn is the one that moves"
    assert game.board.position_of_pawn("żółty") == 2, "onto the back-most one"
    tower = game.board.tower_of(game.board.pawn_tile("czerwony").index)
    assert tower[-1] == "żółty" and "czerwony" in tower


def test_norbur_restricts_movement_to_the_span_between_pawns(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Norbur")
    place(game, "czerwony", 3)
    place(game, "żółty", 9)
    events = use(game, SEAT)
    assert any(isinstance(e, ev.StatusGranted) for e in events)
    assert game.statuses.movement_range() == (3, 9)


def test_norbur_needs_a_wide_enough_gap(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Norbur")
    place(game, "czerwony", 3)
    place(game, "żółty", 4)
    events = use(game, SEAT)
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_restricted_movement_blocks_a_card_that_leaves_the_span(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Norbur")
    place(game, "czerwony", 3)
    place(game, "żółty", 9)
    use(game, SEAT)

    deck = game.deck(settings.DECK_MOVEMENT)
    card = next(c for c in deck.draw_pile if c.title == "Fillerski przedmiot - żółty")
    deck.draw_pile.remove(card)
    game.players[SEAT].add_card(card)
    events = game.apply(cmd.PlayCard(player_index=SEAT, card_uid=card.uid))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_dziad_asks_which_pawn_and_how_far(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Dziad")
    place(game, "żółty", 6)

    events = use(game, SEAT)
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert asked.kind == "pawn" and asked.key == "pawn"

    events = use(game, SEAT, pawn="żółty")
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert asked.key == "move"
    ids = {option[0] for option in asked.options}
    assert ids == {"1", "2", "-1", "-2"}, "one or two fields, either way"

    use(game, SEAT, pawn="żółty", move="-2")
    assert game.board.position_of_pawn("żółty") == 4


def test_ondrej_links_two_pawns_so_they_travel_together(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Ondrej")
    place(game, "żółty", 6)
    place(game, "zielony", 2)
    use(game, SEAT, pawn="żółty", pawn_b="zielony")
    assert game.statuses.linked_partners("żółty") == ["zielony"]
    assert game.statuses.linked_partners("zielony") == ["żółty"]

    deck = game.deck(settings.DECK_MOVEMENT)
    card = next(c for c in deck.draw_pile if c.title == "Zerówka - żółty")
    deck.draw_pile.remove(card)
    game.players[SEAT].add_card(card)
    game.apply(cmd.PlayCard(player_index=SEAT, card_uid=card.uid))

    assert game.board.position_of_pawn("żółty") == 7
    assert game.board.position_of_pawn("zielony") == 7, "the link drags it along"


def test_atencjusz_grants_an_extra_turn(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Atencjusz")
    use(game, SEAT)
    assert game.statuses.player_has(StatusKind.EXTRA_TURN, SEAT)


def test_abilities_that_need_checking_say_so_without_spending_a_use(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Glockboy")
    events = use(game, SEAT, pawn="żółty")
    unavailable = next(e for e in events if isinstance(e, ev.AbilityUnavailable))
    assert "sprawdzan" in unavailable.reason.lower()
    assert game.players[SEAT].character.uses_left == 1


# ── Piotrek's skills ─────────────────────────────────────────────────────────
def test_piotrek_starts_with_a_skill(library):
    for seed in range(6):
        state = create_game(
            SessionConfig(num_players=4, board_cells=24, seed=seed + 1), library
        )
        piotrek = next(p for p in state.players if p.is_piotrek)
        assert piotrek.skill is not None
        assert piotrek.skill.uses_left == piotrek.skill.uses_total


def test_chatgpt_costs_piotrek_two_movement_cards_and_a_chest_slot(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    give_skill(game, "ChatGPT")
    assert starting_hand_size(piotrek) == 3
    assert game.chest_limit(piotrek) == 1

    hunter = next(p for p in game.players if not p.is_piotrek)
    assert starting_hand_size(hunter) == 3
    assert game.chest_limit(hunter) == 1


def test_chatgpt_adds_one_field_to_the_next_movement_card(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    give_skill(game, "ChatGPT")
    place(game, "żółty", 4)

    use(game, piotrek.index, source="skill")
    assert game.statuses.player_has(StatusKind.MOVEMENT_BONUS, piotrek.index)

    deck = game.deck(settings.DECK_MOVEMENT)
    card = next(c for c in deck.draw_pile if c.title == "Zerówka - żółty")
    deck.draw_pile.remove(card)
    piotrek.add_card(card)
    game.apply(cmd.PlayCard(player_index=piotrek.index, card_uid=card.uid))

    assert game.board.position_of_pawn("żółty") == 6, "one field became two"
    assert not game.statuses.player_has(StatusKind.MOVEMENT_BONUS, piotrek.index)


def test_the_bonus_only_applies_once(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    give_skill(game, "ChatGPT")
    place(game, "żółty", 4)
    use(game, piotrek.index, source="skill")

    deck = game.deck(settings.DECK_MOVEMENT)
    for _ in range(2):
        card = next(c for c in deck.draw_pile if c.title == "Zerówka - żółty")
        deck.draw_pile.remove(card)
        piotrek.add_card(card)
        game.apply(cmd.PlayCard(player_index=piotrek.index, card_uid=card.uid))
    assert game.board.position_of_pawn("żółty") == 7  # 4 → 6 → 7


def test_chatgpt_may_be_used_five_times(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    skill = give_skill(game, "ChatGPT")
    for expected in range(4, -1, -1):
        use(game, piotrek.index, source="skill")
        assert skill.uses_left == expected
        # Spend the bonus so the next activation is allowed.
        game.statuses.remove(StatusKind.MOVEMENT_BONUS, Subject.PLAYER,
                             str(piotrek.index))
    events = use(game, piotrek.index, source="skill")
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_ice_block_reports_the_missing_mechanic(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    skill = give_skill(game, "Ice Block")
    events = use(game, piotrek.index, source="skill")
    assert any(isinstance(e, ev.AbilityUnavailable) for e in events)
    assert skill.uses_left == 1


def test_dlug_u_tomasza_records_its_state(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    give_skill(game, "Dług u Tomasza")
    use(game, piotrek.index, source="skill", pawn="żółty", pawn_b="zielony")
    forbidden = game.statuses.of_kind(StatusKind.FORBIDDEN_ADJACENCY)
    assert forbidden and sorted(forbidden[0].data["members"]) == ["zielony", "żółty"]


# ── statuses over time ───────────────────────────────────────────────────────
def test_a_status_lasting_one_turn_expires_when_the_seat_changes(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Big D Randy")
    place(game, "żółty", 5)
    use(game, SEAT, pawn="żółty")
    assert game.statuses.pawn_has(StatusKind.FROZEN, "żółty")

    game.apply(cmd.SetActivePlayer(player_index=1))
    assert game.statuses.pawn_has(StatusKind.FROZEN, "żółty"), "still this turn"

    events = game.apply(cmd.SetActivePlayer(player_index=2))
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "żółty")
    assert any(isinstance(e, ev.StatusEnded) for e in events)


def test_statuses_survive_a_snapshot_round_trip(game):
    SEAT = hunter_seat(game)
    give_character(game, SEAT, "Big D Randy")
    place(game, "żółty", 5)
    use(game, SEAT, pawn="żółty")
    snapshot = game.snapshot()
    assert snapshot["statuses"], "statuses belong in the snapshot"
    assert snapshot["statuses"][0]["kind"] == "frozen"


# ── Seks z pedałami ──────────────────────────────────────────────────────────
def test_seks_z_pedalami_reveals_and_plays_a_random_card(game):
    SEAT = hunter_seat(game)
    place_all(game)
    card = take_movement_card(game, "Seks z pedałami", SEAT)

    events = game.apply(cmd.PlayCard(player_index=SEAT, card_uid=card.uid))
    revealed = next(e for e in events if isinstance(e, ev.CardRevealed))
    assert revealed.title != "Seks z pedałami"
    assert revealed.announce_seconds == pytest.approx(2.0)
    # The revealed card acted, and both cards appear in the played log.
    assert any(isinstance(e, ev.TokenWalked) for e in events)
    played = [e.title for e in events if isinstance(e, ev.CardPlayed)]
    assert revealed.title in played and "Seks z pedałami" in played


def test_the_revealed_card_is_the_same_on_every_machine(library):
    """Deterministic lockstep: the reveal comes from the shared seed."""
    config = SessionConfig(num_players=4, board_cells=30, seed=99)
    titles = []
    for _ in range(2):
        game = create_game(config, library)
        for index, pawn in enumerate(game.library.pawns):
            tile = game.board.positions[index + 1].tiles[0]
            game.board.place_pawn(pawn.id, tile.index)
        game._sync_token_positions()
        card = take_movement_card(game, "Seks z pedałami")
        events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
        titles.append(next(e.title for e in events if isinstance(e, ev.CardRevealed)))
    assert titles[0] == titles[1]


# ── chest limit and Gamechanger ──────────────────────────────────────────────
def test_drawing_over_the_chest_limit_asks_which_to_keep(game):
    seat = hunter_seat(game)
    player = game.players[seat]
    chest = game.deck(settings.DECK_CHEST)
    for _ in range(2):
        game.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_CHEST))

    held = game.chest_cards(player)
    assert len(held) == 2 > game.chest_limit(player)
    assert game.pending_chest_choice is not None

    keep = held[1].uid
    game.apply(cmd.KeepChestCards(player_index=seat, keep_uids=(keep,)))
    remaining = game.chest_cards(player)
    assert [c.uid for c in remaining] == [keep]
    assert chest.discard_count >= 1
    assert game.pending_chest_choice is None


def test_piotrek_may_hold_more_chest_cards(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    give_skill(game, "Dług u Tomasza")     # not ChatGPT, so no chest penalty
    assert game.chest_limit(piotrek) == 2
    events = game.apply(cmd.DrawCard(player_index=piotrek.index,
                                     deck_id=settings.DECK_CHEST))
    assert not any(isinstance(e, ev.ChestLimitReached) for e in events)


def test_gamechanger_announces_itself_before_it_lands(game):
    chest = game.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)          # next card drawn

    hunter = next(p for p in game.players if not p.is_piotrek)
    events = game.apply(cmd.DrawCard(player_index=hunter.index,
                                     deck_id=settings.DECK_CHEST))
    transformed = next(e for e in events if isinstance(e, ev.CardTransformed))
    assert transformed.from_title == "Gamechanger"
    assert transformed.to_title == "Kingmaker"
    assert transformed.intro_text


def test_gamechanger_shows_piotrek_a_different_card(game):
    chest = game.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)

    piotrek = next(p for p in game.players if p.is_piotrek)
    events = game.apply(cmd.DrawCard(player_index=piotrek.index,
                                     deck_id=settings.DECK_CHEST))
    transformed = next(e for e in events if isinstance(e, ev.CardTransformed))
    assert transformed.to_title == "Alter Ego"


# ── stage 5: transformation, target selection, mods, edit mode ───────────────
def test_gamechanger_actually_becomes_the_card_in_hand(game):
    """The animation was right; the card underneath it was still Gamechanger."""
    hunter = next(p for p in game.players if not p.is_piotrek)
    chest = game.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)

    game.apply(cmd.DrawCard(player_index=hunter.index, deck_id=settings.DECK_CHEST))
    held = [c for c in hunter.hand if c.deck_id == settings.DECK_CHEST]
    assert [c.title for c in held] == ["Kingmaker"]
    assert held[0].uid == card.uid, "the same physical card, renamed"
    assert not any(c.title == "Gamechanger" for c in hunter.hand)


def test_piotrek_gets_alter_ego_instead(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    chest = game.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)

    game.apply(cmd.DrawCard(player_index=piotrek.index, deck_id=settings.DECK_CHEST))
    assert card.title == "Alter Ego"
    assert card.text


def test_a_transformed_card_goes_back_to_the_deck_as_itself(game):
    """The chest deck must still contain a Gamechanger, not a Kingmaker."""
    hunter = next(p for p in game.players if not p.is_piotrek)
    chest = game.deck(settings.DECK_CHEST)
    card = next(c for c in chest.draw_pile if c.title == "Gamechanger")
    chest.draw_pile.remove(card)
    chest.draw_pile.append(card)

    game.apply(cmd.DrawCard(player_index=hunter.index, deck_id=settings.DECK_CHEST))
    assert card.title == "Kingmaker"
    game.apply(cmd.DiscardCard(player_index=hunter.index, card_uid=card.uid))
    assert chest.top_discard.title == "Gamechanger"
    assert not card.is_transformed


# ── target selection ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "title,steps",
    [("Kolos z paki", 1), ("Astral 2019", 2), ("Astral 2022", -2)],
)
def test_select_a_pawn_cards_ask_then_move(game, title, steps):
    place_all(game)
    card = take_movement_card(game, title)
    start = game.board.position_of_pawn("zielony")

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert asked.kind == "pawn"
    assert set(asked.pawns) == {p.id for p in game.library.pawns}
    assert card in game.players[0].hand, "nothing happens until a pawn is picked"

    choices = {"pawn": "zielony"}
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid,
                                     choices=choices))
    pending = next((e for e in events if isinstance(e, ev.ChoiceRequired)), None)
    if pending is not None:                      # doubled destination
        choices[pending.key] = pending.options[0][0]
        events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid,
                                         choices=choices))
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert game.board.position_of_pawn("zielony") == start + steps


def test_janek_puts_the_chosen_pawn_on_the_pink_one(game):
    place_all(game, {"różowy": 12, "zielony": 3})
    card = take_movement_card(game, "Janek")

    asked = next(
        e for e in game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
        if isinstance(e, ev.ChoiceRequired)
    )
    assert asked.kind == "pawn"
    game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid,
                            choices={"pawn": "zielony"}))
    assert game.board.position_of_pawn("zielony") == 12
    tower = game.board.tower_of(game.board.pawn_tile("różowy").index)
    assert tower[-1] == "zielony"


def test_the_card_text_says_porusz(library):
    janek = next(c for c in library.deck(settings.DECK_MOVEMENT).cards
                 if c.title == "Janek")
    assert janek.text.startswith("Porusz")


# ── Thunderfuck ──────────────────────────────────────────────────────────────
def test_thunderfuck_does_nothing_with_an_empty_rack(game):
    """No active mods means nothing to replace: the card is simply spent.

    It must not seed the rack.  Mods enter play by being CHOSEN on a mod round,
    and a Thunderfuck played before the first selection would put one there
    that nobody picked.
    """
    card = take_movement_card(game, "Thunderfuck")
    assert game.mod_slots == [None, None]
    mods = game.deck(settings.DECK_MODS)
    before = mods.draw_count

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    assert not any(isinstance(e, ev.ModPlaced) for e in events)
    assert game.mod_slots == [None, None]
    assert mods.draw_count == before
    # Spent like any other card, so it cannot be played twice.
    assert card not in game.players[0].hand
    assert card in game.deck(settings.DECK_MOVEMENT).discard_pile


def test_thunderfuck_shifts_a_single_mod_right(game):
    """One mod in play: the new card takes LEFT and the old one slides RIGHT."""
    mods = game.deck(settings.DECK_MODS)
    first = mods.take_card()
    game.mod_slots[0] = first
    card = take_movement_card(game, "Thunderfuck")

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    assert next(e.slot for e in events if isinstance(e, ev.ModPlaced)) == 0
    assert game.mod_slots[1] is first
    assert game.mod_slots[0] is not None and game.mod_slots[0] is not first
    # Nothing fell off the right, so nothing was discarded.
    assert not any(isinstance(e, ev.ModDiscarded) for e in events)


def test_thunderfuck_pushes_the_right_mod_off_a_full_rack(game):
    """New → LEFT, old LEFT → RIGHT, old RIGHT → discard."""
    mods = game.deck(settings.DECK_MODS)
    first, second = mods.take_card(), mods.take_card()
    game.mod_slots[0], game.mod_slots[1] = first, second
    card = take_movement_card(game, "Thunderfuck")

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    assert any(isinstance(e, ev.ModDiscarded) for e in events)
    assert game.mod_slots[1] is first
    assert game.mod_slots[0] not in (first, second)
    assert second in mods.discard_pile


def test_thunderfuck_replaces_mods_on_anybody_s_turn(game):
    """'Regardless of whose turn it is' — a hunter's copy works the same way."""
    mods = game.deck(settings.DECK_MODS)
    first = mods.take_card()
    game.mod_slots[0] = first
    seat = hunter_seat(game)
    card = take_movement_card(game, "Thunderfuck", player_index=seat)

    events = game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid))
    assert next(e.slot for e in events if isinstance(e, ev.ModPlaced)) == 0
    assert game.mod_slots[1] is first


def test_there_are_three_thunderfucks_in_the_movement_deck(library):
    cards = library.deck(settings.DECK_MOVEMENT).build_cards()
    assert sum(1 for c in cards if c.title == "Thunderfuck") == 3


# ── edit mode ────────────────────────────────────────────────────────────────
def _game_with_edit_mode(library, enabled: bool):
    return create_game(
        SessionConfig(num_players=4, board_cells=24, seed=7, edit_mode=enabled),
        library,
    )


def test_edit_mode_lets_one_person_play_every_seat(library):
    game = _game_with_edit_mode(library, True)
    other = next(p.index for p in game.players
                 if p.index != game.active_player_index)
    assert game.may_control(other)
    assert any(isinstance(e, ev.ActivePlayerChanged)
               for e in game.apply(cmd.SetActivePlayer(player_index=other)))
    assert any(isinstance(e, ev.CardDrawn) for e in game.apply(
        cmd.DrawCard(player_index=2, deck_id=settings.DECK_MOVEMENT)))


def test_without_edit_mode_only_the_local_seat_may_act(library):
    game = _game_with_edit_mode(library, False)
    assert game.may_control(game.local_seat)
    assert not game.may_control(game.local_seat + 1)

    for command in (
        cmd.DrawCard(player_index=2, deck_id=settings.DECK_MOVEMENT),
        cmd.UseAbility(player_index=2),
        cmd.ToggleMark(player_index=2, pawn_id="żółty"),
        cmd.RenamePlayer(player_index=2, name="Ktoś"),
    ):
        events = game.apply(command)
        assert any(isinstance(e, ev.ActionRejected) for e in events), command.kind

    # The local seat may act once the cadence reaches it.
    game.active_player_index = game.local_seat
    assert any(isinstance(e, ev.CardDrawn) for e in game.apply(
        cmd.DrawCard(player_index=game.local_seat,
                     deck_id=settings.DECK_MOVEMENT)))


def test_only_the_player_whose_turn_it_is_may_act(library):
    """Everyone else is a spectator until the turn comes round."""
    game = _game_with_edit_mode(library, False)
    first = game.active_player_index
    other = next(p.index for p in game.players if p.index != first)
    assert game.may_act(first) and not game.may_act(other)

    game.active_player_index = other
    assert not game.may_act(first)

    # The first seat is now watching: its moves are refused.
    events = game.apply(cmd.DrawCard(player_index=first,
                                     deck_id=settings.DECK_MOVEMENT))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    # Private bookkeeping on your own seat still works off turn.
    game.active_player_index = other
    assert any(isinstance(e, ev.MarkToggled) for e in game.apply(
        cmd.ToggleMark(player_index=game.local_seat, pawn_id="zielony")))


def test_edit_mode_survives_the_trip_from_the_menu(library):
    """A setting that vanishes between the menu and the game has bitten twice."""
    from dataclasses import fields

    config = SessionConfig(num_players=4, board_cells=24, seed=7,
                           edit_mode=False, local_seat=2,
                           double_frequency=0.4).normalised()
    state = create_game(config, library)
    for spec in fields(SessionConfig):
        if spec.name == "seed":
            continue
        assert getattr(state.config, spec.name) == getattr(config, spec.name), spec.name
    assert state.local_seat == 2
    assert state.may_control(2) and not state.may_control(0)


def test_the_random_reveal_only_picks_cards_it_can_resolve_alone(library):
    """The executor cannot open a prompt mid-plan, so it draws from the rest.

    Its own game rather than the shared fixture: twelve plays walk the table
    into round 3, where a Mod Patusa selection correctly pauses everything and
    the thirteenth play is refused.  That pause has its own tests; here it would
    only cut the sample short.
    """
    game = create_game(
        SessionConfig(num_players=4, board_cells=30, seed=4321,
                      double_frequency=0.0, mod_round_first=10_000),
        library,
    )
    place_all(game)
    deck = game.deck(settings.DECK_MOVEMENT)
    card = take_movement_card(game, "Seks z pedałami")
    for _ in range(12):
        if card not in game.players[0].hand:
            if card in deck.discard_pile:
                deck.discard_pile.remove(card)
            game.players[0].add_card(card)
        events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
        revealed = next(e for e in events if isinstance(e, ev.CardRevealed))
        drawn = game.deck(settings.DECK_MOVEMENT).find_discarded(revealed.card_uid)
        assert drawn is not None and drawn.resolves_without_asking
        assert not any(isinstance(e, ev.ChoiceRequired) for e in events)
        # Put the revealed card back so the next round has the same pool.
        if drawn in deck.discard_pile:
            deck.discard_pile.remove(drawn)
        deck.draw_pile.append(drawn)


def test_a_card_that_asks_is_still_playable(library):
    """is_playable means 'the engine can act on it', not 'needs no input'."""
    deck = library.deck(settings.DECK_MOVEMENT)
    kolos = next(c for c in deck.build_cards() if c.title == "Kolos z paki")
    assert kolos.is_playable
    assert not kolos.resolves_without_asking
    zerowka = next(c for c in deck.build_cards() if c.title.startswith("Zerówka"))
    assert zerowka.is_playable and zerowka.resolves_without_asking
    troll = next(c for c in deck.build_cards() if c.title == "Troll")
    assert not troll.is_playable


# ── the automatic turn loop (stage 8) ────────────────────────────────────────
def _loop_game(library, **kwargs):
    config = SessionConfig(num_players=3, board_cells=24, seed=6,
                           chest_open_round=2, double_frequency=0.0, **kwargs)
    return create_game(config, library)


def take_turn(game) -> None:
    """Resolve the active player's movement card, however it goes."""
    player = game.active_player
    card = next(c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT)
    events = game.apply(cmd.PlayCard(player_index=player.index, card_uid=card.uid))
    blocked = any(isinstance(e, (ev.ActionRejected, ev.ChoiceRequired))
                  for e in events)
    if blocked:
        game.apply(cmd.DiscardCard(player_index=player.index, card_uid=card.uid))


def test_playing_a_card_refills_the_hand_and_passes_the_turn(library):
    game = _loop_game(library)
    player = game.active_player
    before = len([c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT])
    card = next(c for c in player.hand
                if c.deck_id == settings.DECK_MOVEMENT and c.resolves_without_asking)

    events = game.apply(cmd.PlayCard(player_index=player.index, card_uid=card.uid))
    kinds = [type(e).__name__ for e in events]
    assert "CardPlayed" in kinds
    assert "CardDrawn" in kinds, "a replacement is drawn automatically"
    assert "ActivePlayerChanged" in kinds, "and the turn moves on by itself"

    after = len([c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT])
    assert after == before, "the hand is back to its proper size"
    assert game.active_player.index != player.index


def test_the_hand_size_follows_the_rules_for_each_player(library):
    """Piotrek holds more; the refill knows that without being told."""
    from pedzacy_piotrek.engine.setup import starting_hand_size

    game = _loop_game(library)
    for _ in range(12):
        take_turn(game)
        for player in game.players:
            held = len([c for c in player.hand
                        if c.deck_id == settings.DECK_MOVEMENT])
            assert held == starting_hand_size(player), player.name


def test_the_turn_follows_the_designed_cadence(library):
    game = _loop_game(library)
    seen = [game.active_player_index]
    for _ in range(6):
        take_turn(game)
        seen.append(game.active_player_index)
    expected = game.seat_order(1)
    assert seen[:len(expected)] == expected


def test_the_round_advances_when_its_slots_run_out(library):
    game = _loop_game(library)
    assert game.round_number == 1
    for _ in range(12):
        take_turn(game)
    assert game.round_number > 1


def test_a_discard_also_passes_the_turn(library):
    """A hand where nothing is legal must not leave the table stuck."""
    game = _loop_game(library)
    player = game.active_player
    card = next(c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT)
    game.apply(cmd.DiscardCard(player_index=player.index, card_uid=card.uid))
    assert game.active_player.index != player.index
    assert len([c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT]) == \
        len([c for c in game.players[player.index].hand
             if c.deck_id == settings.DECK_MOVEMENT])


def test_a_refused_play_does_not_end_the_turn(library):
    game = _loop_game(library)
    place(game, "różowy", 0)
    player = game.active_player
    card = take_movement_card(game, "Wejściówka - czerwony", player.index)
    events = game.apply(cmd.PlayCard(player_index=player.index, card_uid=card.uid))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.active_player.index == player.index, "you still have a turn to take"


def test_a_chest_card_is_handed_out_when_the_round_opens(library):
    game = _loop_game(library)
    awarded = []
    for _ in range(20):
        player = game.active_player
        card = next(c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT)
        events = game.apply(cmd.PlayCard(player_index=player.index, card_uid=card.uid))
        if any(isinstance(e, (ev.ActionRejected, ev.ChoiceRequired)) for e in events):
            events = game.apply(cmd.DiscardCard(player_index=player.index,
                                                card_uid=card.uid))
        awarded.extend(e for e in events if isinstance(e, ev.ChestCardAwarded))
        if game.pending_chest_choice:
            seat, uids = game.pending_chest_choice
            game.apply(cmd.KeepChestCards(player_index=seat, keep_uids=(uids[-1],)))
        if len(awarded) >= 2:
            break

    assert awarded, "the chest promised a card and never handed one out"
    assert all(a.round_number >= game.chest_open_round for a in awarded)
    # It rotates between the hunters rather than always feeding the same one.
    assert len({a.player_index for a in awarded}) > 1
    for award in awarded:
        assert not game.players[award.player_index].is_piotrek


def test_an_over_full_chest_hand_asks_before_anything_else_happens(library):
    game = _loop_game(library)
    seat = next(p.index for p in game.players if not p.is_piotrek)
    chest = game.deck(settings.DECK_CHEST)
    game.players[seat].add_card(chest.take_card())

    game.round_number = game.chest_open_round - 1
    events = game._begin_round(game.chest_open_round)
    if not any(isinstance(e, ev.ChestLimitReached) for e in events):
        pytest.skip("this round's card went to the other hunter")
    assert game.pending_chest_choice is not None


def test_the_loop_can_be_switched_off(library):
    """The automatic flow is a rule switch, not a hard-coded assumption."""
    from pedzacy_piotrek.config.settings import RULES

    game = _loop_game(library)
    player = game.active_player
    card = next(c for c in player.hand
                if c.deck_id == settings.DECK_MOVEMENT and c.resolves_without_asking)
    original = RULES.auto_turn_flow
    object.__setattr__(RULES, "auto_turn_flow", False)
    try:
        events = game.apply(cmd.PlayCard(player_index=player.index, card_uid=card.uid))
        assert not any(isinstance(e, ev.ActivePlayerChanged) for e in events)
        assert game.active_player.index == player.index
    finally:
        object.__setattr__(RULES, "auto_turn_flow", original)


def test_ending_a_turn_by_hand_does_what_the_automatic_end_does(library):
    """The button and the automatic path must not drift apart."""
    from pedzacy_piotrek.engine.setup import starting_hand_size

    game = _loop_game(library)
    seat = game.active_player_index
    player = game.players[seat]
    player.remove_card(player.hand[0])

    events = game.apply(cmd.EndTurn(player_index=seat))
    assert any(isinstance(e, ev.CardDrawn) for e in events)
    assert any(isinstance(e, ev.ActivePlayerChanged) for e in events)
    assert len([c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT]) == \
        starting_hand_size(player)


def test_only_the_active_player_may_end_the_turn(library):
    game = _loop_game(library)
    other = next(p.index for p in game.players
                 if p.index != game.active_player_index)
    events = game.apply(cmd.EndTurn(player_index=other))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_ending_a_turn_waits_for_the_chest_choice(library):
    game = _loop_game(library)
    seat = game.active_player_index
    game.pending_chest_choice = (seat, [])
    events = game.apply(cmd.EndTurn(player_index=seat))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.active_player_index == seat


def test_the_round_counter_deals_the_chest_cards_it_skips(library):
    """Nudging the counter forward used to swallow a hunter's chest card."""
    game = _loop_game(library)
    hunters = [p.index for p in game.players if not p.is_piotrek]
    awarded = []
    for target in range(2, 2 + 2 * len(hunters)):
        events = game.apply(cmd.SetRound(round_number=target))
        awarded.extend(e for e in events if isinstance(e, ev.ChestCardAwarded))
        if game.pending_chest_choice:
            owner, uids = game.pending_chest_choice
            game.apply(cmd.KeepChestCards(player_index=owner, keep_uids=(uids[-1],)))

    assert {a.player_index for a in awarded} == set(hunters), \
        "every hunter takes their turn at the chest"


def test_jumping_several_rounds_still_pays_everybody(library):
    game = _loop_game(library)
    hunters = [p.index for p in game.players if not p.is_piotrek]
    events = game.apply(cmd.SetRound(round_number=2 + len(hunters)))
    awarded = [e for e in events if isinstance(e, ev.ChestCardAwarded)]
    assert {a.player_index for a in awarded} == set(hunters)
