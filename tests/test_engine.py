"""
Engine tests.

These are the safety net for the refactor: they assert that the rules behave
the way the prototype's rules behaved.  None of them needs a display, because
nothing in ``engine/`` imports pygame.

    python -m pytest tests -q
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.turn_order import (
    chest_recipient_for_round,
    compute_round_turn_order,
)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def game(library):
    return create_game(
        SessionConfig(num_players=4, board_cells=24, chest_open_round=3, seed=1234),
        library,
    )


# ── content ──────────────────────────────────────────────────────────────────
def test_deck_sizes_match_the_data_file(library):
    """Deck sizes come from the count column in cards.json.

    Hard-coded here on purpose: a balance edit that changes a count should make
    somebody confirm the new number, not slip through unnoticed.
    """
    assert len(library.deck(settings.DECK_MOVEMENT).build_cards()) == 70
    assert len(library.deck(settings.DECK_MODS).build_cards()) == 8
    # One blank placeholder card was dropped from the data file.
    assert len(library.deck(settings.DECK_CHEST).build_cards()) == 8
    assert len(library.deck(settings.DECK_CHARACTERS).build_cards()) == 10
    assert len(library.deck(settings.DECK_SKILLS).build_cards()) == 3
    assert len(library.pawns) == 6


def test_exactly_one_piotrek_in_the_character_deck(library):
    characters = library.deck(settings.DECK_CHARACTERS).cards
    assert sum(1 for c in characters if c.is_piotrek) == 1


# ── setup ────────────────────────────────────────────────────────────────────
def test_someone_always_gets_piotrek(library):
    for seed in range(20):
        state = create_game(SessionConfig(num_players=3, seed=seed + 1), library)
        assert sum(1 for p in state.players if p.is_piotrek) == 1


def test_explicit_character_choice_is_honoured(library):
    config = SessionConfig(
        num_players=3, character_choices=["Lubin", None, None], seed=99
    )
    state = create_game(config, library)
    assert state.players[0].character is not None
    assert state.players[0].character.title == "Lubin"
    assert any(p.is_piotrek for p in state.players)


def test_starting_hand_sizes(game):
    for player in game.players:
        expected = (
            RULES.start_hand_piotrek if player.is_piotrek else RULES.start_hand_default
        )
        assert len(player.hand) == expected


def test_same_seed_produces_the_same_game(library):
    a = create_game(SessionConfig(num_players=4, seed=555), library)
    b = create_game(SessionConfig(num_players=4, seed=555), library)
    assert [p.character.title for p in a.players] == [p.character.title for p in b.players]
    assert [c.title for c in a.players[0].hand] == [c.title for c in b.players[0].hand]
    assert [t.position for t in a.board.tiles] == [t.position for t in b.board.tiles]


# ── turn order ───────────────────────────────────────────────────────────────
def test_turn_order_matches_the_design_document():
    """Piotrek -> G1 -> G2 -> Piotrek -> G3 -> G1 -> Piotrek (4 players)."""
    order = compute_round_turn_order(1, "Piotrek", ["G1", "G2", "G3"])
    assert [slot.name for slot in order] == [
        "Piotrek", "G1", "G2", "Piotrek", "G3",
    ]
    assert [slot.is_piotrek for slot in order] == [True, False, False, True, False]


def test_round_ends_when_every_hunter_has_acted():
    for hunters in (["A"], ["A", "B"], ["A", "B", "C", "D", "E"]):
        order = compute_round_turn_order(1, "P", hunters)
        names = [s.name for s in order if not s.is_piotrek]
        assert set(names) == set(hunters)


def test_turn_order_without_piotrek_is_a_plain_round_robin():
    order = compute_round_turn_order(1, None, ["A", "B", "C"])
    assert [s.name for s in order] == ["A", "B", "C"]


def test_chest_rotates_one_hunter_per_round():
    hunters = ["A", "B", "C"]
    assert chest_recipient_for_round(1, 3, hunters) == "A"  # previewed before opening
    assert chest_recipient_for_round(3, 3, hunters) == "A"
    assert chest_recipient_for_round(4, 3, hunters) == "B"
    assert chest_recipient_for_round(5, 3, hunters) == "C"
    assert chest_recipient_for_round(6, 3, hunters) == "A"


# ── commands ─────────────────────────────────────────────────────────────────
def test_draw_and_discard_round_trip(game):
    player = game.players[0]
    before_hand = len(player.hand)
    deck = game.deck(settings.DECK_MOVEMENT)
    before_draw = deck.draw_count

    events = game.apply(cmd.DrawCard(player_index=0, deck_id=settings.DECK_MOVEMENT))
    assert any(isinstance(e, ev.CardDrawn) for e in events)
    assert len(player.hand) == before_hand + 1
    assert deck.draw_count == before_draw - 1

    card = player.hand[-1]
    game.apply(cmd.DiscardCard(player_index=0, card_uid=card.uid))
    assert len(player.hand) == before_hand
    assert deck.discard_count == 1


def test_hand_limit_is_enforced(game):
    player = game.players[0]
    for _ in range(30):
        game.apply(cmd.DrawCard(player_index=0, deck_id=settings.DECK_MOVEMENT))
    assert len(player.hand) == RULES.max_hand
    events = game.apply(cmd.DrawCard(player_index=0, deck_id=settings.DECK_MOVEMENT))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_empty_deck_reshuffles_its_discard_pile(game):
    deck = game.deck(settings.DECK_CHEST)
    # Move the whole deck into the discard pile.
    while deck.draw_pile:
        deck.return_card(deck.draw_pile.pop())
    assert deck.draw_count == 0
    events = game.apply(cmd.DrawCard(player_index=1, deck_id=settings.DECK_CHEST))
    assert any(isinstance(e, ev.DeckReshuffled) for e in events)
    assert any(isinstance(e, ev.CardDrawn) for e in events)


def test_mod_rack_pushes_left_and_discards_the_overflow(game):
    """Slot 0 takes the new card, the old slot 0 shifts right, slot 1 is binned."""
    player = game.players[0]
    uids = []
    for _ in range(3):
        game.apply(cmd.DrawCard(player_index=0, deck_id=settings.DECK_MODS))
        uids.append(player.hand[-1].uid)

    game.apply(cmd.PlaceMod(player_index=0, card_uid=uids[0]))
    assert game.mod_slots[0].uid == uids[0]
    assert game.mod_slots[1] is None

    game.apply(cmd.PlaceMod(player_index=0, card_uid=uids[1]))
    assert game.mod_slots[0].uid == uids[1]
    assert game.mod_slots[1].uid == uids[0]

    events = game.apply(cmd.PlaceMod(player_index=0, card_uid=uids[2]))
    assert game.mod_slots[0].uid == uids[2]
    assert game.mod_slots[1].uid == uids[1]
    assert any(isinstance(e, ev.ModDiscarded) and e.card_uid == uids[0] for e in events)
    assert game.deck(settings.DECK_MODS).discard_count == 1


def test_discarding_a_mod_returns_it_to_its_deck(game):
    player = game.players[0]
    game.apply(cmd.DrawCard(player_index=0, deck_id=settings.DECK_MODS))
    game.apply(cmd.PlaceMod(player_index=0, card_uid=player.hand[-1].uid))
    game.apply(cmd.DiscardMod(slot=0))
    assert game.mod_slots[0] is None
    assert game.deck(settings.DECK_MODS).discard_count == 1


def test_only_piotrek_can_draw_a_skill(game):
    hunter = next(p for p in game.players if not p.is_piotrek)
    events = game.apply(cmd.DrawSkill(player_index=hunter.index))
    assert any(isinstance(e, ev.ActionRejected) for e in events)

    piotrek = next(p for p in game.players if p.is_piotrek)
    game.apply(cmd.DrawSkill(player_index=piotrek.index))
    assert piotrek.skill is not None


def test_discard_top_character_card_removes_skill_before_character(game):
    piotrek = next(p for p in game.players if p.is_piotrek)
    game.apply(cmd.DrawSkill(player_index=piotrek.index))
    game.apply(cmd.DiscardTopCharacterCard(player_index=piotrek.index))
    assert piotrek.skill is None
    assert piotrek.character is not None
    game.apply(cmd.DiscardTopCharacterCard(player_index=piotrek.index))
    assert piotrek.character is None


def test_drawing_a_character_discards_the_previous_one(game):
    player = game.players[0]
    old = player.character
    game.apply(cmd.DrawCharacter(player_index=0))
    assert player.character is not old
    assert old in game.deck(settings.DECK_CHARACTERS).discard_pile


def test_marks_toggle(game):
    game.apply(cmd.ToggleMark(player_index=0, pawn_id="czerwony"))
    assert "czerwony" in game.players[0].marks
    game.apply(cmd.ToggleMark(player_index=0, pawn_id="czerwony"))
    assert "czerwony" not in game.players[0].marks


def test_round_counter_never_goes_below_one(game):
    game.apply(cmd.SetRound(round_number=1))
    game.apply(cmd.SetRound(round_number=0))
    assert game.round_number == 1


def test_rename_rejects_blank_names(game):
    events = game.apply(cmd.RenamePlayer(player_index=0, name="   "))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    game.apply(cmd.RenamePlayer(player_index=0, name="Kuba"))
    assert game.players[0].name == "Kuba"


def test_commands_survive_a_json_round_trip():
    import json

    original = cmd.MoveToken(pawn_id="zielony", x=12.5, y=8.25, tile_index=4)
    restored = cmd.Command.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original


# ── card effects (added in stage 2) ──────────────────────────────────────────
def _hand_card(game, title: str):
    """Put a specific movement card into player 0's hand.

    Looks in the draw pile first and then in the other players' hands, because
    a one-copy card may well have been dealt out during setup.
    """
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
    assert card is not None, f"nie znaleziono karty {title!r}"
    if card not in game.players[0].hand:
        game.players[0].add_card(card)
    return card


def test_cards_are_split_into_the_ones_that_ask_and_the_ones_that_do_not(library):
    """Both kinds are playable now; the difference is whether the engine asks."""
    deck = library.deck(settings.DECK_MOVEMENT)
    direct = [c for c in deck.cards if c.effect and not c.effect.needs_choice]
    asking = [c for c in deck.cards if c.effect and c.effect.needs_choice]
    # 20 movement cards, plus Seks z pedałami and Thunderfuck, act at once.
    assert len(direct) == 22
    # Janek, Kolos z paki, Astral 2019 and Astral 2022 ask which pawn.
    assert {c.title for c in asking} == {
        "Janek", "Kolos z paki", "Astral 2019", "Astral 2022",
    }
    # The four that remain need mechanics that do not exist yet.
    inert = {c.title for c in deck.cards if c.effect is None}
    assert inert == {"Troll", "Stańczyk", "Spy", "Plagiat!"}
    # Every card that names a pawn must name a real one.
    for definition in direct + asking:
        spec = definition.effect
        for key in ("pawn", "source", "destination"):
            value = spec.get(key)
            if value and value not in ("choice", "hindmost", "foremost", "piotrek"):
                assert library.pawn(value) is not None, definition.title


def test_playing_a_card_moves_the_pawn_and_discards_the_card(game):
    card = _hand_card(game, "Zerówka - żółty")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))

    walked = [e for e in events if isinstance(e, ev.TokenWalked)]
    played = [e for e in events if isinstance(e, ev.CardPlayed)]
    assert walked and played
    assert walked[0].route == [0]
    assert game.board.pawn_tiles["żółty"] == 0
    assert card not in game.players[0].hand
    assert game.deck(settings.DECK_MOVEMENT).find_discarded(card.uid) is card


def test_a_two_step_card_reports_both_fields(game):
    """Two steps means two visited positions, whatever the board looks like."""
    game.board.place_pawn("żółty", 0)
    game._sync_token_positions()
    card = _hand_card(game, "Fillerski przedmiot - żółty")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))

    choice = [e for e in events if isinstance(e, ev.ChoiceRequired)]
    if choice:
        # The destination is a widened stretch; the play needs the a/b answer.
        events = game.apply(
            cmd.PlayCard(player_index=0, card_uid=card.uid,
                         target_tile=choice[0].options[0])
        )
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    assert walked.route == [1, 2]
    assert len(walked.waypoints) == 2
    assert game.board.position_of_pawn("żółty") == 2


def test_backward_cards_move_backward(game):
    game.board.place_pawn("różowy", 5)
    game._sync_token_positions()
    card = _hand_card(game, "Wejściówka - różowy")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    assert walked.backward is True
    assert game.board.pawn_tiles["różowy"] == 4


def test_a_card_that_cannot_move_anything_is_refused(game):
    """Backwards from the camp is not a legal move, and nothing is discarded."""
    card = _hand_card(game, "Wejściówka - różowy")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert card in game.players[0].hand
    assert game.deck(settings.DECK_MOVEMENT).discard_count == 0


def test_movement_clamps_at_the_finish(game):
    last = len(game.board.tiles) - 1
    game.board.place_pawn("żółty", last)
    game._sync_token_positions()
    card = _hand_card(game, "Zerówka - żółty")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.board.pawn_tiles["żółty"] == last


def test_a_played_card_carries_the_pawns_riding_on_it(game):
    for pawn in ("czerwony", "zielony", "niebieski"):
        game.board.place_pawn(pawn, 3)
    game._sync_token_positions()
    card = _hand_card(game, "Zerówka - czerwony")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    assert walked.carried == ["zielony", "niebieski"]
    assert game.board.tower_of(4) == ["czerwony", "zielony", "niebieski"]


def test_hindmost_cards_target_the_pawn_furthest_back(game):
    for index, pawn in enumerate(("czerwony", "zielony", "niebieski")):
        game.board.place_pawn(pawn, 4 + index)
    game._sync_token_positions()
    # The three untouched pawns are still in camp, so one of those is hindmost.
    card = _hand_card(game, "Przepis")
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    assert walked.from_index == effects.CAMP_INDEX
    assert walked.pawn_id in ("żółty", "różowy", "pomarańczowy")


def test_hindmost_is_resolved_identically_everywhere(library):
    """Two independent copies of the same game must pick the same pawn.

    Deterministic lockstep depends on this: the host and every client resolve
    'the pawn furthest back' themselves.
    """
    config = SessionConfig(num_players=3, board_cells=20, seed=31)
    a, b = create_game(config, library), create_game(config, library)
    for state in (a, b):
        state.board.place_pawn("czerwony", 2)
        state.board.place_pawn("zielony", 2)
        state._sync_token_positions()
    assert effects.hindmost_pawn(a) == effects.hindmost_pawn(b)


def test_preview_does_not_change_anything(game):
    card = _hand_card(game, "Zerówka - żółty")
    before = game.snapshot()
    plan = effects.preview(game, card)
    assert plan.ok
    assert game.snapshot() == before


def test_a_card_needing_a_choice_asks_for_a_pawn(game):
    """Stage 5 made these playable: the engine asks instead of refusing."""
    card = _hand_card(game, "Kolos z paki")
    result = effects.resolve(game, card)
    assert isinstance(result, effects.Choice)
    assert result.kind == "pawn"
    assert set(result.pawns) == {p.id for p in game.library.pawns}

    chosen = "żółty"
    plan = effects.resolve(game, card, choices={"pawn": chosen})
    while isinstance(plan, effects.Choice):
        plan = effects.resolve(
            game, card, choices={"pawn": chosen, plan.key: plan.options[0].id}
        )
    assert plan.ok
    assert plan.routes[0].pawn_id == chosen


# ── doubled positions (12a / 12b) ────────────────────────────────────────────
def _doubled_game(library):
    """A game whose every second position is a widened stretch."""
    config = SessionConfig(num_players=3, board_cells=30, seed=11,
                           double_frequency=1.0)
    return create_game(config, library)


def test_landing_on_a_widened_stretch_asks_which_half(library):
    game = _doubled_game(library)
    card = _hand_card(game, "Zerówka - żółty")
    game.board.place_pawn("żółty", game.board.positions[0].tiles[0].index)
    game._sync_token_positions()

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    choice = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert choice.kind == "tile"
    assert choice.key == "tile"
    assert [label for _, label in choice.options] == ["2a", "2b"]
    # Nothing happened: the card is still in hand and the pawn has not moved.
    assert card in game.players[0].hand
    assert game.board.position_of_pawn("żółty") == 0


def test_supplying_the_choice_completes_the_play(library):
    game = _doubled_game(library)
    card = _hand_card(game, "Zerówka - żółty")
    game.board.place_pawn("żółty", game.board.positions[0].tiles[0].index)
    game._sync_token_positions()

    choice = next(
        e for e in game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
        if isinstance(e, ev.ChoiceRequired)
    )
    events = game.apply(
        cmd.PlayCard(player_index=0, card_uid=card.uid,
                     choices={"tile": str(choice.tiles[1])})
    )
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert game.board.pawn_tile("żółty").index == choice.tiles[1]
    assert game.board.pawn_tile("żółty").label == "2b"


def test_a_bad_choice_is_ignored_rather_than_trusted(library):
    """The engine validates the choice; a client cannot land somewhere else."""
    game = _doubled_game(library)
    card = _hand_card(game, "Zerówka - żółty")
    game.board.place_pawn("żółty", game.board.positions[0].tiles[0].index)
    game._sync_token_positions()

    far_away = game.board.positions[6].tiles[0].index
    events = game.apply(
        cmd.PlayCard(player_index=0, card_uid=card.uid,
                     choices={"tile": str(far_away)})
    )
    assert any(isinstance(e, ev.ChoiceRequired) for e in events)
    assert game.board.position_of_pawn("żółty") == 0


def _play_answering_choices(game, card, option: int = 0):
    """Play a card, answering a destination choice if one is asked for."""
    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    choice = next((e for e in events if isinstance(e, ev.ChoiceRequired)), None)
    if choice is not None:
        events = game.apply(
            cmd.PlayCard(player_index=0, card_uid=card.uid,
                         choices={choice.key: choice.options[option][0]})
        )
    return events


def test_passing_through_a_widened_stretch_needs_no_choice(library):
    """Only the destination is a decision; intermediate halves are automatic.

    Every position on this board is doubled, so a two-step move crosses one
    widened stretch on the way. The engine must ask exactly once — about where
    the pawn stops, not about where it passes.
    """
    game = _doubled_game(library)
    card = _hand_card(game, "Fillerski przedmiot - żółty")
    game.board.place_pawn("żółty", game.board.positions[0].tiles[0].index)
    game._sync_token_positions()

    first = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    asked = [e for e in first if isinstance(e, ev.ChoiceRequired)]
    assert len(asked) == 1
    assert [label for _, label in asked[0].options] == ["3a", "3b"]

    events = game.apply(
        cmd.PlayCard(player_index=0, card_uid=card.uid,
                     choices={"tile": str(asked[0].tiles[0])})
    )
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    assert walked.route == [1, 2]
    assert len(walked.tiles) == 2
    # It walked through one half of position 2 without being asked.
    assert game.board.tile(walked.tiles[0]).number == 2


def test_a_pawn_walks_through_the_nearer_half_of_a_pair(library):
    game = _doubled_game(library)
    card = _hand_card(game, "Fillerski przedmiot - zielony")
    start_tile = game.board.positions[0].tiles[0]
    game.board.place_pawn("zielony", start_tile.index)
    game._sync_token_positions()

    events = _play_answering_choices(game, card)
    walked = next(e for e in events if isinstance(e, ev.TokenWalked))
    passed = game.board.tile(walked.tiles[0])
    pair = game.board.tiles_at_position(passed.slot)
    assert min(pair, key=lambda t: math.dist(start_tile.position, t.position)) is passed


def test_positions_and_fields_are_different_counts(library):
    game = _doubled_game(library)
    assert len(game.board.tiles) == 30
    assert game.board.position_count < len(game.board.tiles)


def test_create_game_keeps_every_session_setting(library):
    """A regression: create_game used to rebuild the config field by field and
    silently drop anything added later."""
    from dataclasses import fields

    config = SessionConfig(
        num_players=4, board_cells=26, chest_open_round=5,
        character_choices=[None] * 4, double_frequency=0.25, seed=1234,
    )
    state = create_game(config, library)
    for spec in fields(SessionConfig):
        if spec.name == "seed":
            continue
        assert getattr(state.config, spec.name) == getattr(config, spec.name), spec.name
    assert state.config.seed == 1234
