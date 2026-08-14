"""
The Mody Patusa that actually change the rules, and the deck they come from.

Stage 24 gave Speedrun, Masa solna, Halloween and Sesja na PG their rules and
made the deck more than one copy of each.  Stage 25 added Paczka, Squid Game
and Shady, which live in test_mod_effects.py because they are large enough to
deserve their own file.  AKO is the last placeholder and there is still a test
here that says so, because "it does nothing yet" is a fact worth pinning: it
stops the next person assuming a missing effect is a bug they introduced.

Engine level throughout, so it runs headless.  The panel that sets the counts
is tested in test_ui.py.
"""

from __future__ import annotations

import collections

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig, clamp_mod_counts
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 4, seed: int = 99, **kwargs):
    """A game that never interrupts itself, so a test can pose a position."""
    kwargs.setdefault("double_frequency", 0.0)
    kwargs.setdefault("mod_round_first", 10_000)
    config = SessionConfig(
        num_players=players, board_cells=24, seed=seed,
        chest_open_round=10_000, piotrek_picks_pawn=False, **kwargs,
    )
    return create_game(config, library)


def install(game, title: str, slot: int = 0):
    """Put a named mod into the rack, the way a selection would."""
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[slot] = card
    # Through the same arrival path a real selection uses, so a mod that acts
    # the moment it lands (Paczka, Shady) does so here too.
    game._sync_mod_states()
    return card


def movement_card(game, title: str):
    pool = list(game.decks[settings.DECK_MOVEMENT].draw_pile)
    for player in game.players:
        pool.extend(player.hand)
    return next(c for c in pool if c.title == title)


def place(game, pawn_id: str, position: int) -> None:
    game.board.place_pawn(pawn_id, game.board.position(position).tiles[0].index)


def spread(game, *pairs) -> None:
    """Put the named pawns where they are wanted and the rest far away.

    Everything unmentioned is parked in a block at the far end, so a test that
    means "this pawn is alone" gets a pawn that is alone rather than one whose
    neighbour happens to be a pawn the test never thought about.
    """
    named = dict(pairs)
    for pawn_id, position in named.items():
        place(game, pawn_id, position)
    spare = game.board.last_position
    for pawn in game.library.pawns:
        if pawn.id not in named:
            place(game, pawn.id, spare)


# ── part 1: the deck is no longer one of each ────────────────────────────────
def test_the_deck_holds_the_printed_number_of_copies(library):
    """The composition from the brief, straight out of cards.json."""
    counts = collections.Counter(
        card.title
        for card in library.deck(settings.DECK_MODS).build_cards()
    )
    assert counts == {
        "Speedrun": 2, "Masa solna": 2, "AKO": 1, "Halloween": 1,
        "Sesja na PG": 2, "Paczka": 2, "Squid Game": 1, "Obóz Harcerski": 2,
    }
    assert sum(counts.values()) == 13


def test_every_mod_title_still_appears_exactly_once_in_the_data(library):
    """Copies come from ``count``, never from repeating the entry."""
    titles = [card.title for card in library.deck(settings.DECK_MODS).cards]
    assert len(titles) == len(set(titles)) == 8


# ── part 2: the lobby sets the counts ────────────────────────────────────────
def test_the_lobby_can_resize_the_deck(library):
    game = make(library, mod_counts={"Speedrun": 4, "Obóz Harcerski": 0})
    counts = collections.Counter(c.title
                                 for c in game.decks[settings.DECK_MODS].draw_pile)
    assert counts["Speedrun"] == 4
    assert "Obóz Harcerski" not in counts
    # Everything the lobby did not mention keeps the printed count.
    assert counts["Masa solna"] == 2


def test_an_empty_mapping_means_the_printed_deck(library):
    """The default path.  A test, or an older client, sends nothing."""
    assert (make(library).decks[settings.DECK_MODS].draw_count
            == make(library, mod_counts={}).decks[settings.DECK_MODS].draw_count
            == 13)


def test_counts_are_clamped_and_junk_is_dropped():
    assert clamp_mod_counts({"A": 99}) == {"A": RULES.mod_count_max}
    assert clamp_mod_counts({"A": -4}) == {"A": RULES.mod_count_min}
    assert clamp_mod_counts({"A": "nonsense"}) == {}
    assert clamp_mod_counts(None) == {}


def test_the_mapping_is_sorted_so_two_machines_compare_equal():
    """It travels in the lobby snapshot every client checks against the host."""
    assert list(clamp_mod_counts({"Obóz Harcerski": 1, "AKO": 2})) == ["AKO", "Obóz Harcerski"]


def test_a_resized_deck_is_identical_on_two_machines(library):
    """Same seed, same settings, same pile — including the order."""
    counts = {"Speedrun": 3, "Halloween": 2, "Paczka": 0}
    one = make(library, seed=4242, mod_counts=counts)
    two = make(library, seed=4242, mod_counts=counts)
    assert ([c.title for c in one.decks[settings.DECK_MODS].draw_pile]
            == [c.title for c in two.decks[settings.DECK_MODS].draw_pile])
    assert ([c.uid for c in one.decks[settings.DECK_MODS].draw_pile]
            == [c.uid for c in two.decks[settings.DECK_MODS].draw_pile])


def test_an_emptied_mods_deck_does_not_stop_the_round(library):
    """Zero of everything is legal, and must not hang the table.

    ``_open_mod_selection`` already declines to pause on an exhausted deck;
    this is the setting that can produce one from the lobby.
    """
    game = make(library, mod_round_first=1,
                mod_counts={title: 0 for title in
                            [c.title for c in
                             library.deck(settings.DECK_MODS).cards]})
    assert game.decks[settings.DECK_MODS].draw_count == 0
    game._begin_round(1)
    assert game.pending_mod_selection is None


# ── the rules are declared in the data, not matched by title ─────────────────
def test_the_rules_come_from_the_json(library):
    """No part of the engine may ask a mod what it is called."""
    by_title = {c.title: c for c in library.deck(settings.DECK_MODS).cards}
    assert by_title["Masa solna"].passive == {"movement_cap": 1}
    assert by_title["Speedrun"].passive == {"reverse_backward": True}
    assert by_title["Halloween"].passive == {"require_neighbour": True}
    assert by_title["Sesja na PG"].passive == {"abilities_locked": True}


def test_an_empty_rack_changes_nothing(library):
    game = make(library)
    assert game.movement_cap is None
    assert not game.abilities_locked
    assert not game.requires_neighbour
    assert not game.reverses_backward_moves


def test_a_mod_stops_applying_when_it_leaves_the_rack(library):
    game = make(library)
    install(game, "Masa solna")
    assert game.movement_cap == 1
    game.mod_slots[0] = None
    assert game.movement_cap is None


def test_a_rule_applies_from_either_slot(library):
    game = make(library)
    install(game, "Halloween", slot=1)
    assert game.requires_neighbour


# ── part 4: Masa solna ───────────────────────────────────────────────────────
def line_up(game, start: int = 4) -> None:
    """Every pawn in a row from ``start``, so any of them can be targeted.

    The fixed-colour cards name a specific pawn, so a test about distance must
    not leave that pawn parked on the finish where it cannot move at all.
    """
    for offset, pawn in enumerate(game.library.pawns):
        place(game, pawn.id, start + offset)


@pytest.mark.parametrize("title", [
    "Obniżenie progu", "Astral 2019", "Astral 2022",
    "Fillerski przedmiot - czerwony", "Fillerski przedmiot - zielony",
    "Fillerski przedmiot - żółty", "Fillerski przedmiot - niebieski",
    "Fillerski przedmiot - różowy", "Fillerski przedmiot - pomarańczowy",
])
def test_masa_solna_shortens_every_two_field_card(library, title):
    """The nine cards that move exactly two — the brief's list, in full."""
    game = make(library)
    line_up(game)
    install(game, "Masa solna")
    card = movement_card(game, title)
    plan = effects.resolve(game, card, 0, {"pawn": "czerwony"})
    assert isinstance(plan, effects.Plan)
    route = plan.operations[0].route
    assert len(route) == 1, f"{title} still moved {len(route)} fields"


def test_masa_solna_leaves_one_field_cards_alone(library):
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Masa solna")
    plan = effects.resolve(game, movement_card(game, "Przepis"), 0)
    assert len(plan.operations[0].route) == 1


def test_masa_solna_keeps_the_direction(library):
    """Astral 2022 moves two BACK, so under the cap it moves one back."""
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Masa solna")
    plan = effects.resolve(game, movement_card(game, "Astral 2022"), 0,
                           {"pawn": "czerwony"})
    assert plan.operations[0].route == (5,)


def test_masa_solna_does_not_touch_abilities(library):
    """Dziad moves a pawn, and an ability is not a movement card."""
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Masa solna")
    pool = list(game.decks[settings.DECK_CHARACTERS].draw_pile)
    pool += [p.character for p in game.players if p.character]
    dziad = next(c for c in pool if c.title == "Dziad")
    plan = effects.resolve_ability(game, dziad, 0,
                                   {"pawn": "czerwony", "move": "2"})
    assert isinstance(plan, effects.Plan)
    assert len(plan.operations[0].route) == 2


def test_masa_solna_shortens_the_multi_pawn_card(library):
    """Plagiat! reaches movement through a different handler.

    It moves one field already, so this pins the plumbing rather than a change
    in distance: the cap has to be applied on that path too, or the next
    two-field multi-pawn card would quietly escape it.
    """
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Masa solna")
    plan = effects.resolve(game, movement_card(game, "Plagiat!"), 0,
                           {"pawns": "czerwony,zielony"})
    assert isinstance(plan, effects.Plan)
    assert all(abs(op.steps) == 1 for op in plan.operations)


# ── part 3: Speedrun ─────────────────────────────────────────────────────────
def test_speedrun_asks_before_a_backward_card(library):
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Speedrun")
    result = effects.resolve(game, movement_card(game, "Wejściówka - czerwony"), 0)
    assert isinstance(result, effects.Choice)
    assert result.key == "speedrun"
    assert result.kind == "option"
    assert [o.id for o in result.options] == ["backward", "forward"]


def test_speedrun_never_asks_about_a_forward_card(library):
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Speedrun")
    assert isinstance(effects.resolve(game, movement_card(game, "Przepis"), 0),
                      effects.Plan)


def test_speedrun_does_not_ask_when_it_is_not_in_the_rack(library):
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    plan = effects.resolve(game, movement_card(game, "Wejściówka - czerwony"), 0)
    assert isinstance(plan, effects.Plan)
    assert plan.operations[0].route == (5,)


@pytest.mark.parametrize("answer,expected", [("backward", (5,)), ("forward", (7,))])
def test_speedrun_turns_the_card_around(library, answer, expected):
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Speedrun")
    plan = effects.resolve(game, movement_card(game, "Wejściówka - czerwony"),
                           0, {"speedrun": answer})
    assert plan.operations[0].route == expected


def test_the_direction_is_asked_before_the_pawn(library):
    """The order in the brief: direction, then pawn, then which half.

    Astral 2022 needs both questions, so it is the card that can get them the
    wrong way round.
    """
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Speedrun")
    card = movement_card(game, "Astral 2022")
    assert effects.resolve(game, card, 0).key == "speedrun"
    assert effects.resolve(game, card, 0, {"speedrun": "forward"}).key == "pawn"


def test_the_double_field_question_still_comes_last(library):
    """Direction, pawn, then the widened row — all three, in order."""
    game = make(library, double_frequency=1.0)
    doubled = next(i for i in range(game.board.last_position + 1)
                   if game.board.position(i).is_doubled)
    spread(game, ("czerwony", doubled + 1), ("zielony", doubled + 2))
    install(game, "Speedrun")
    card = movement_card(game, "Wejściówka - czerwony")
    assert effects.resolve(game, card, 0).key == "speedrun"
    third = effects.resolve(game, card, 0, {"speedrun": "backward"})
    assert isinstance(third, effects.Choice) and third.key == "tile"


def test_speedrun_reverses_the_multi_pawn_card(library):
    """Plagiat! moves backwards, so Speedrun turns it around as well."""
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Speedrun")
    card = movement_card(game, "Plagiat!")
    assert effects.resolve(game, card, 0).key == "speedrun"
    plan = effects.resolve(game, card, 0,
                           {"speedrun": "forward", "pawns": "czerwony,zielony"})
    assert all(op.steps == 1 for op in plan.operations)


def test_a_card_nobody_can_be_asked_about_keeps_its_printed_direction(library):
    """Seks z pedałami and Troll pick from ``resolves_without_asking``.

    That is a property of the PRINTED card and cannot know a mod has just given
    it a question.  Speedrun only ever offers a reversal, so declining is legal
    and the card must still work instead of fizzling.
    """
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 7))
    install(game, "Speedrun")
    card = movement_card(game, "Wejściówka - czerwony")
    plan = effects.resolve(game, card, 0, can_ask=False)
    assert isinstance(plan, effects.Plan)
    assert plan.operations[0].route == (5,)


# ── part 5: Halloween ────────────────────────────────────────────────────────
def test_a_pawn_with_a_neighbour_in_front_moves(library):
    game = make(library)
    spread(game, ("czerwony", 5), ("zielony", 6))
    install(game, "Halloween")
    plan = effects.resolve(game, movement_card(game, "Kolos z paki"), 0,
                           {"pawn": "czerwony"})
    assert isinstance(plan.operations[0], effects.MovePawn)


def test_a_pawn_with_a_neighbour_behind_moves(library):
    game = make(library)
    spread(game, ("czerwony", 6), ("zielony", 5))
    install(game, "Halloween")
    plan = effects.resolve(game, movement_card(game, "Kolos z paki"), 0,
                           {"pawn": "czerwony"})
    assert isinstance(plan.operations[0], effects.MovePawn)


def test_a_lone_pawn_does_not_move_and_the_card_is_not_refused(library):
    """The card resolves and is discarded; only the movement does nothing."""
    game = make(library)
    spread(game, ("czerwony", 3), ("zielony", 12))
    install(game, "Halloween")
    plan = effects.resolve(game, movement_card(game, "Kolos z paki"), 0,
                           {"pawn": "czerwony"})
    assert isinstance(plan, effects.Plan)
    assert plan.ok, "an unmovable pawn must not read as a refusal"
    assert [type(o) for o in plan.operations] == [effects.Fizzle]


def test_sharing_a_field_is_not_being_a_neighbour(library):
    """A tower is one field; the rule is about in front and behind."""
    game = make(library)
    spread(game, ("czerwony", 8), ("zielony", 8), ("niebieski", 8))
    install(game, "Halloween")
    plan = effects.resolve(game, movement_card(game, "Kolos z paki"), 0,
                           {"pawn": "czerwony"})
    assert [type(o) for o in plan.operations] == [effects.Fizzle]


def test_pawns_waiting_in_the_camp_are_neighbours(library):
    """A deliberate reading, and the one that stops the game deadlocking.

    Every pawn starts in the camp.  Taken literally nobody there has a
    neighbour, so Halloween reaching the rack on round 1 — which the lobby
    allows — would freeze the board for good: nothing could move, nobody could
    reach the finish and no tower could be built, so neither side could win.
    """
    game = make(library)
    install(game, "Halloween")
    assert all(effects.pawn_index(game, p.id) == effects.CAMP_INDEX
               for p in game.library.pawns)
    plan = effects.resolve(game, movement_card(game, "Przepis"), 0)
    assert [type(o) for o in plan.operations] == [effects.MovePawn]


def test_a_played_card_is_discarded_even_when_nothing_moves(library):
    """End to end: the hand, the pile and the turn all move on."""
    game = make(library)
    spread(game, ("czerwony", 3), ("zielony", 12))
    install(game, "Halloween")
    seat = game.active_player_index
    player = game.players[seat]
    # Put the card in the hand rather than hoping the deal produced one.
    card = movement_card(game, "Kolos z paki")
    if card not in player.hand:
        game.decks[settings.DECK_MOVEMENT].draw_pile.remove(card)
        player.hand.append(card)
    before = game.decks[settings.DECK_MOVEMENT].discard_count
    events = game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid,
                                     choices={"pawn": "czerwony"}))
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert any(isinstance(e, ev.MoveFizzled) for e in events)
    assert not any(isinstance(e, ev.ActionRejected) for e in events)
    assert card not in player.hand
    assert game.decks[settings.DECK_MOVEMENT].discard_count > before
    assert effects.pawn_index(game, "czerwony") == 3


def test_halloween_does_not_pin_an_ability(library):
    """Only movement cards are affected."""
    game = make(library)
    spread(game, ("czerwony", 3), ("zielony", 12))
    install(game, "Halloween")
    pool = list(game.decks[settings.DECK_CHARACTERS].draw_pile)
    pool += [p.character for p in game.players if p.character]
    dziad = next(c for c in pool if c.title == "Dziad")
    plan = effects.resolve_ability(game, dziad, 0,
                                   {"pawn": "czerwony", "move": "1"})
    assert [type(o) for o in plan.operations] == [effects.MovePawn]


def test_a_multi_pawn_card_moves_the_ones_that_can(library):
    """One pinned pawn does not refuse the card for the other."""
    game = make(library)
    spread(game, ("czerwony", 5), ("zielony", 6), ("niebieski", 14))
    install(game, "Halloween")
    plan = effects.resolve(game, movement_card(game, "Plagiat!"), 0,
                           {"pawns": "czerwony,niebieski"})
    assert isinstance(plan, effects.Plan)
    moved = [o for o in plan.operations if isinstance(o, effects.MoveBySteps)]
    assert [o.pawn_id for o in moved] == ["czerwony"]
    assert any(isinstance(o, effects.Fizzle) for o in plan.operations)


# ── part 6: Sesja na PG ──────────────────────────────────────────────────────
def _ability_seat(game):
    return next(p.index for p in game.players
                if p.character is not None and p.character.has_ability)


def test_sesja_na_pg_refuses_an_ability(library):
    game = make(library)
    install(game, "Sesja na PG")
    seat = _ability_seat(game)
    game.active_player_index = seat
    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not any(isinstance(e, ev.AbilityUsed) for e in events)


def test_the_charges_survive_the_mod(library):
    """The whole point: a locked ability is not a spent one."""
    game = make(library)
    install(game, "Sesja na PG")
    seat = _ability_seat(game)
    game.active_player_index = seat
    card = game.players[seat].character
    before = card.uses_left
    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert card.uses_left == before
    game.mod_slots[0] = None
    assert not game.abilities_locked
    assert card.ability_available


def test_an_already_spent_ability_stays_spent_afterwards(library):
    """Regaining access is not regaining charges."""
    game = make(library)
    seat = _ability_seat(game)
    card = game.players[seat].character
    card.uses_left = 0
    install(game, "Sesja na PG")
    game.mod_slots[0] = None
    assert not card.ability_available


# ── part 7: there are no placeholders left ───────────────────────────────────
def test_no_mod_is_a_placeholder_any_more(library):
    """Every Mod Patusa now declares a rule.

    AKO was the last one that did nothing; stage 35 gave it its two variants.
    This replaces the test that used to assert AKO was inert — that fact was
    worth pinning while it was true, and asserting it now would be pinning the
    opposite of the card's behaviour.
    """
    for card in library.deck(settings.DECK_MODS).cards:
        declared = dict(card.passive or {})
        for variant in card.variants:
            declared.update(variant.passive or {})
        # A mod's rule may be a PASSIVE it applies while racked or an ABILITY
        # it fires as it arrives — Herold is the first of the second kind, and
        # "declares nothing" has to mean neither rather than no passive.
        assert declared or card.ability is not None, (
            f"{card.title} declares no rule at all"
        )


def test_ako_changes_only_its_own_rule(library):
    """It is a rule about which pawns move, and about nothing else."""
    game = make(library)
    install(game, "AKO")
    assert game.carries_neighbour
    assert game.movement_cap is None
    assert not game.abilities_locked
    assert not game.requires_neighbour
    assert not game.reverses_backward_moves
    assert not game.chest_cards_revealed
    assert not game.lead_check_only
    assert not game.hides_leader


@pytest.mark.parametrize("title", ["AKO", "Paczka", "Squid Game", "Obóz Harcerski"])
def test_every_mod_can_be_chosen(library, title):
    """Every mod has to reach the rack, effect or no effect."""
    game = make(library)
    card = install(game, title)
    assert game.mod_slots[0] is card
    assert game.find_card(card.uid) is card
