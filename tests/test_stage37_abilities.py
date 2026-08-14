"""
Stage 37 — Granny Costume, Jazdy, Where are you Marcus? and Plac.

Four abilities and one rule that sits above all of them.  The rule comes first
because it is the one every future ability inherits: while any pawn is still on
START, nothing may be activated.

The freeze tests are the bulk of this file, and they are deliberately written
against the CARDS rather than against the status: an assertion that
``pawn_has(FROZEN)`` is true proves the flag was set, not that Przepis pays any
attention to it.  Every interaction the brief names is exercised by playing the
actual card and looking at the board afterwards.
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
from pedzacy_piotrek.engine.statuses import StatusKind, Subject
from pedzacy_piotrek.engine.victory import MatchPhase, Outcome




@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def game(library):
    """Six seats, so the round order is the one the brief works through.

    ``compute_round_turn_order`` gives Piotrek every third slot, which at five
    hunters is exactly the brief's example: Piotrek appears three times in a
    round and Big D Randy once.  Several tests depend on that shape and would
    be testing nothing at four seats.
    """
    return create_game(
        SessionConfig(num_players=6, board_cells=40, seed=4321,
                      double_frequency=0.0),
        library,
    )


# ── fixture helpers ──────────────────────────────────────────────────────────
def place(game, pawn_id: str, position: int) -> None:
    tile = game.board.positions[position].tiles[0]
    game.board.place_pawn(pawn_id, tile.index)
    game._sync_token_positions()


def clear_start(game, base: int = 20) -> None:
    """Walk every pawn still in the camp onto a field of its own, out of the way.

    Parked high and descending so the low fields stay free for whatever the
    test is actually arranging, and never on the last position, which is the
    finish.
    """
    spot = min(base, game.board.last_position - 1)
    for pawn in game.library.pawns:
        if game.board.position_of_pawn(pawn.id) is None:
            while game.board.positions[spot].tiles[0].stack:
                spot -= 1
            place(game, pawn.id, spot)
            spot -= 1


def seat_of(game, title: str) -> int:
    """Give a non-Piotrek seat a named character and hand it the turn."""
    seat = next(p.index for p in game.players
                if not p.is_piotrek and p.character is not None
                and p.character.title == title)
    return seat


def give_character(game, player_index: int, title: str):
    deck = game.deck(settings.DECK_CHARACTERS)
    card = deck.take_titled(title, include_discard=True)
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


def hunter_seat(game) -> int:
    return next(p.index for p in game.players if not p.is_piotrek)


def piotrek_seat(game) -> int:
    return next(p.index for p in game.players if p.is_piotrek)


def arm(game, title: str, seat: int = None) -> int:
    """Seat a character, clear the camp and make that seat active."""
    seat = hunter_seat(game) if seat is None else seat
    give_character(game, seat, title)
    clear_start(game)
    game.apply(cmd.SetActivePlayer(player_index=seat))
    return seat


def use(game, seat: int, source: str = "character", **choices):
    return game.apply(
        cmd.UseAbility(player_index=seat, source=source, choices=choices)
    )


def hand_card(game, title: str, seat: int):
    """Put a named movement card into a seat's hand, wherever it is now."""
    deck = game.deck(settings.DECK_MOVEMENT)
    card = next((c for c in deck.draw_pile if c.title == title), None)
    if card is None:
        card = next((c for c in deck.discard_pile if c.title == title), None)
        if card is not None:
            deck.discard_pile.remove(card)
    else:
        deck.draw_pile.remove(card)
    if card is None:
        for player in game.players:
            card = next((c for c in player.hand if c.title == title), None)
            if card is not None:
                player.remove_card(card)
                break
    assert card is not None, title
    game.players[seat].add_card(card)
    return card


def play(game, seat: int, card, **choices):
    return game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid,
                                   choices=choices))


def chest_card(game, title: str, seat: int):
    deck = game.deck(settings.DECK_CHEST)
    card = next((c for c in deck.draw_pile if c.title == title), None)
    assert card is not None, title
    deck.draw_pile.remove(card)
    game.players[seat].add_card(card)
    return card


def install_mod(game, title: str, slot: int = 0):
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[slot] = card
    game._sync_mod_states()
    return card


def freeze(game, pawn_id: str, seat: int = None) -> int:
    """Freeze a pawn with Granny Costume, for real, through the command path."""
    seat = arm(game, "Big D Randy", seat)
    events = use(game, seat, pawn=pawn_id)
    assert any(isinstance(e, ev.AbilityUsed) for e in events), events
    assert game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    return seat


def positions(game) -> dict:
    return {pawn.id: game.board.position_of_pawn(pawn.id)
            for pawn in game.library.pawns}


# ═════════════════════════════════════════════════════════════════════════════
# §39 — the global rule, for all four abilities
# ═════════════════════════════════════════════════════════════════════════════
ALL_FOUR = [
    ("Big D Randy", {"pawn": "czerwony"}),
    ("Lubin", {}),
    ("Glockboy", {"pawn": "czerwony"}),
    ("Norbur", {}),
]


@pytest.mark.parametrize("title,choices", ALL_FOUR)
def test_no_ability_activates_while_a_pawn_is_on_start(game, title, choices):
    """One pawn still in the camp is enough to lock every ability in the game."""
    seat = hunter_seat(game)
    give_character(game, seat, title)
    clear_start(game)
    # Put exactly one pawn back in the camp.  The others are on the board, so
    # nothing but the START rule can be the reason this is refused.
    game.board.remove_pawn("zielony")
    game._sync_token_positions()
    game.apply(cmd.SetActivePlayer(player_index=seat))
    assert game.pawns_on_start() == ["zielony"]

    events = use(game, seat, **choices)
    rejected = next(e for e in events if isinstance(e, ev.ActionRejected))
    assert "starcie" in rejected.reason.lower()
    assert not any(isinstance(e, ev.AbilityUsed) for e in events)
    assert game.players[seat].character.uses_left == 1, "a refusal costs nothing"


@pytest.mark.parametrize("title,choices", ALL_FOUR)
def test_every_ability_becomes_available_once_start_is_empty(game, title, choices):
    """The same four activations, with the camp cleared, are allowed through."""
    seat = arm(game, title)
    if title == "Lubin":
        assert not game.players[seat].is_piotrek
    if title == "Glockboy":
        # Its own precondition, which the next section tests properly.
        game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    events = use(game, seat, **choices)
    assert not any("starcie" in e.reason.lower()
                   for e in events if isinstance(e, ev.ActionRejected))


def test_the_start_rule_is_one_gate_not_four_copies(game):
    """The rule is asked once, by the engine, for any ability at all.

    A fifth ability added tomorrow must inherit it without its author knowing
    it exists — which is what ``ability_refusal`` being the only question in
    ``_use_ability`` buys.
    """
    clear_start(game)
    assert game.ability_refusal() is None
    game.board.remove_pawn("czerwony")
    game._sync_token_positions()
    assert game.ability_refusal() is not None
    assert "starcie" in game.ability_refusal().lower()


def test_a_pawn_hidden_by_oboz_harcerski_is_not_on_start(game):
    """Off the map is not the same as never having left.

    Otherwise Obóz Harcerski would lock every ability in the game for a full
    round, which is a rule nobody wrote.
    """
    clear_start(game)
    install_mod(game, "Obóz Harcerski")
    assert game.hidden_pawn_ids, "the mod took a pawn off the map"
    assert game.pawns_on_start() == []
    assert game.ability_refusal() is None


# ═════════════════════════════════════════════════════════════════════════════
# §40 — Granny Costume: target filtering
# ═════════════════════════════════════════════════════════════════════════════
def test_a_pawn_alone_on_its_field_can_be_frozen(game):
    seat = arm(game, "Big D Randy")
    place(game, "czerwony", 5)
    events = use(game, seat, pawn="czerwony")
    assert any(isinstance(e, ev.AbilityUsed) for e in events)
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_a_pawn_on_start_is_never_offered(game):
    """Unreachable in practice — the gate stops it — so this pins the targeting."""
    clear_start(game)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()
    assert "zielony" not in effects.freezable_pawns(game)


def test_a_pawn_underneath_another_is_not_offered(game):
    seat = arm(game, "Big D Randy")
    place(game, "czerwony", 5)
    place(game, "zielony", 5)          # lands on top of czerwony
    tower = game.board.tower_of(game.board.pawn_tile("czerwony").index)
    assert tower.index("czerwony") < tower.index("zielony")

    events = use(game, seat, pawn="czerwony")
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    offered = {option[0] for option in asked.options}
    assert "czerwony" not in offered, "a pawn with somebody on top of it"
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_a_pawn_standing_on_another_is_not_offered(game):
    seat = arm(game, "Big D Randy")
    place(game, "czerwony", 5)
    place(game, "zielony", 5)
    events = use(game, seat, pawn="zielony")
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    offered = {option[0] for option in asked.options}
    assert "zielony" not in offered, "a pawn standing on somebody"
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "zielony")


def test_the_briefs_worked_example_offers_exactly_three_pawns(game):
    """§2 of the brief, set up field for field.

    green on START, blue and pink sharing field 1, and three pawns alone.
    """
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "niebieski", 1)        # blue
    place(game, "różowy", 1)           # pink, on top of blue
    place(game, "pomarańczowy", 2)     # orange
    place(game, "czerwony", 3)         # red
    place(game, "żółty", 4)            # yellow
    game._sync_token_positions()       # zielony (green) stays in the camp

    offered = set(effects.freezable_pawns(game))
    assert offered == {"pomarańczowy", "czerwony", "żółty"}
    assert "zielony" not in offered, "still on START"
    assert not offered & {"niebieski", "różowy"}, "not alone on their field"


def test_asking_the_question_costs_nothing(game):
    seat = arm(game, "Big D Randy")
    place(game, "czerwony", 5)
    use(game, seat)                     # no pawn named: opens the prompt
    assert game.players[seat].character.uses_left == 1


# ═════════════════════════════════════════════════════════════════════════════
# §40 — Granny Costume: duration
# ═════════════════════════════════════════════════════════════════════════════
def test_the_freeze_lasts_until_big_d_randys_next_turn(game):
    """A FULL ROUND, walked one real turn at a time.

    The brief's example: the freeze survives every other seat's turn, including
    Piotrek's several appearances, and ends when the turn reaches Big D Randy.
    """
    seat = freeze(game, "czerwony")
    order = game.seat_order()
    assert order.count(piotrek_seat(game)) >= 2, "Piotrek holds several slots"

    seen = 0
    for _ in range(len(order) * 3):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))
        if game.active_player_index == seat:
            break
        seen += 1
        assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony"), (
            f"ended early after {seen} intervening turns"
        )
    assert game.active_player_index == seat, "the turn came back round"
    assert seen >= 2, "several turns really did pass"
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_other_characters_playing_repeatedly_do_not_end_it_early(game):
    """Piotrek taking three turns in a round is not three rounds."""
    seat = freeze(game, "czerwony")
    piotrek = piotrek_seat(game)
    for _ in range(3):
        game.apply(cmd.SetActivePlayer(player_index=piotrek))
        assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")
        game.apply(cmd.SetActivePlayer(player_index=hunter_seat(game))
                   if hunter_seat(game) != seat else cmd.SetActivePlayer(
                       player_index=piotrek))
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_the_freeze_is_not_one_global_turn(game):
    """The reading the brief rules out by name."""
    seat = freeze(game, "czerwony")
    other = next(p.index for p in game.players if p.index != seat)
    game.apply(cmd.SetActivePlayer(player_index=other))
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony"), (
        "one turn passing must not be enough"
    )


# ═════════════════════════════════════════════════════════════════════════════
# §40 — Granny Costume: movement prevention, card by card
# ═════════════════════════════════════════════════════════════════════════════
BLOCKED_BY_COLOUR = [
    "Zerówka - czerwony",
    "Fillerski przedmiot - czerwony",
    "Wejściówka - czerwony",
]


@pytest.mark.parametrize("title", BLOCKED_BY_COLOUR)
def test_a_card_naming_the_frozen_colour_does_not_move_it(game, title):
    seat = freeze(game, "czerwony")
    where = game.board.position_of_pawn("czerwony")
    card = hand_card(game, title, seat)
    play(game, seat, card)
    assert game.board.position_of_pawn("czerwony") == where


@pytest.mark.parametrize("title", ["Kolos z paki", "Astral 2019", "Astral 2022"])
def test_choosing_the_frozen_pawn_moves_nothing(game, title):
    seat = freeze(game, "czerwony")
    where = game.board.position_of_pawn("czerwony")
    card = hand_card(game, title, seat)
    play(game, seat, card, pawn="czerwony")
    assert game.board.position_of_pawn("czerwony") == where


def test_plagiat_does_not_move_a_frozen_pawn(game):
    """Plagiat! takes an ORDERED multi-select under the key ``pawns``.

    Passing the wrong key leaves the card on an unanswered prompt, which moves
    nothing and would pass a careless version of this test — so the resolution
    is asserted explicitly.
    """
    seat = freeze(game, "czerwony")
    place(game, "żółty", 8)
    where = positions(game)
    card = hand_card(game, "Plagiat!", seat)
    events = play(game, seat, card, pawns="czerwony,żółty")
    assert not any(isinstance(e, ev.ChoiceRequired) for e in events), (
        "the card must actually resolve"
    )
    assert game.board.position_of_pawn("czerwony") == where["czerwony"]


def test_plagiat_moves_a_pair_that_contains_no_frozen_pawn(game):
    """The control: the same card, same key, does move when nothing is frozen."""
    seat = arm(game, "Big D Randy")
    place(game, "czerwony", 6)
    place(game, "żółty", 8)
    card = hand_card(game, "Plagiat!", seat)
    events = play(game, seat, card, pawns="czerwony,żółty")
    assert not any(isinstance(e, ev.ChoiceRequired) for e in events)
    assert {e.pawn_id for e in events if isinstance(e, ev.TokenWalked)} == \
        {"czerwony", "żółty"}, "both pawns really do move on this card"


def test_seks_z_pedalami_cannot_move_the_frozen_pawn(game):
    """The card it reveals is random, so the assertion is about the outcome.

    Whatever comes up, the frozen pawn is where it was.  Run over a spread of
    seeds so the revealed card really does vary.
    """
    library = ContentLibrary.load()
    for seed in range(12):
        state = create_game(
            SessionConfig(num_players=6, board_cells=40, seed=seed + 1,
                          double_frequency=0.0),
            library,
        )
        seat = freeze(state, "czerwony")
        where = state.board.position_of_pawn("czerwony")
        card = hand_card(state, "Seks z pedałami", seat)
        play(state, seat, card)
        assert state.board.position_of_pawn("czerwony") == where, f"seed {seed}"


def test_przepis_skips_the_frozen_pawn_and_acts_on_the_next_one(game):
    """§8's worked example: frozen red is hindmost, yellow is next."""
    seat = arm(game, "Big D Randy")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 2)          # furthest behind
    place(game, "żółty", 4)             # second furthest behind
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id not in ("czerwony", "żółty")):
        place(game, pawn.id, 10 + index)
    use(game, seat, pawn="czerwony")
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")

    card = hand_card(game, "Przepis", seat)
    play(game, seat, card)
    assert game.board.position_of_pawn("czerwony") == 2, "the frozen pawn stays"
    assert game.board.position_of_pawn("żółty") == 5, "the next one moves instead"


def test_obnizenie_progu_skips_the_frozen_pawn_too(game):
    seat = arm(game, "Big D Randy")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 2)
    place(game, "żółty", 4)
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id not in ("czerwony", "żółty")):
        place(game, pawn.id, 10 + index)
    use(game, seat, pawn="czerwony")

    card = hand_card(game, "Obniżenie progu", seat)
    play(game, seat, card)
    assert game.board.position_of_pawn("czerwony") == 2
    assert game.board.position_of_pawn("żółty") == 6, "two fields, next pawn"


def test_a_pawn_stacked_on_the_frozen_one_still_moves(game):
    """§8: only the other pawn moves; the frozen one is not dragged along."""
    seat = arm(game, "Big D Randy")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 2)
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id != "czerwony"):
        place(game, pawn.id, 10 + index)
    use(game, seat, pawn="czerwony")
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")
    place(game, "żółty", 2)             # now standing on the frozen pawn

    card = hand_card(game, "Przepis", seat)
    play(game, seat, card)
    assert game.board.position_of_pawn("czerwony") == 2, "untouched underneath"
    assert game.board.position_of_pawn("żółty") == 3, "the rider moved alone"


def test_ako_respects_the_freeze(game):
    """AKO drags a neighbour along; a frozen neighbour is not a candidate.

    The control matters here: with nothing frozen the same arrangement DOES
    hand czerwony to AKO, so the assertion below is about the freeze rather
    than about an arrangement in which AKO was never going to do anything.
    """
    def arrange(state, freeze_it: bool) -> int:
        seat = arm(state, "Big D Randy")
        for pawn in state.library.pawns:
            state.board.remove_pawn(pawn.id)
        place(state, "czerwony", 5)
        place(state, "żółty", 6)
        for index, pawn in enumerate(p for p in state.library.pawns
                                     if p.id not in ("czerwony", "żółty")):
            place(state, pawn.id, 12 + index)
        if freeze_it:
            use(state, seat, pawn="czerwony")
        install_mod(state, "AKO")
        return seat

    control = create_game(
        SessionConfig(num_players=6, board_cells=40, seed=4321,
                      double_frequency=0.0),
        game.library,
    )
    seat = arrange(control, freeze_it=False)
    card = hand_card(control, "Zerówka - żółty", seat)
    play(control, seat, card, ako="czerwony")
    assert control.board.position_of_pawn("czerwony") == 6, (
        "without the freeze, AKO carries it along"
    )

    seat = arrange(game, freeze_it=True)
    assert effects.neighbour_candidates(game, "żółty", 1) == [], (
        "a frozen pawn is not even a candidate"
    )
    card = hand_card(game, "Zerówka - żółty", seat)
    play(game, seat, card, ako="czerwony")
    assert game.board.position_of_pawn("czerwony") == 5, "AKO cannot drag it"


# ═════════════════════════════════════════════════════════════════════════════
# §40 — Granny Costume: the other effects
# ═════════════════════════════════════════════════════════════════════════════
def test_sesja_variant_1_leaves_a_running_freeze_alone(game):
    seat = freeze(game, "czerwony")
    install_mod(game, "Sesja na PG")
    assert game.abilities_locked
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony"), (
        "variant 1 blocks activation, it does not cancel"
    )


def test_sesja_variant_2_cancels_a_running_freeze(game, library):
    state = create_game(
        SessionConfig(num_players=6, board_cells=40, seed=4321,
                      double_frequency=0.0,
                      card_variants={"Sesja na PG": "lock_and_cancel"}),
        library,
    )
    freeze(state, "czerwony")
    install_mod(state, "Sesja na PG")
    assert state.cancels_ability_effects
    assert not state.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_oboz_harcerski_taking_the_pawn_cancels_the_freeze(game):
    """§11: the freeze cannot outlive the pawn being on the board."""
    seat = arm(game, "Big D Randy")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 20)         # out in front, so the mod takes it
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id != "czerwony"):
        place(game, pawn.id, 3 + index)
    use(game, seat, pawn="czerwony")
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")

    install_mod(game, "Obóz Harcerski")
    assert "czerwony" in game.hidden_pawn_ids, "the mod took the frozen pawn"
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_dzieckorolka_does_not_carry_a_frozen_pawn(game):
    """§12: it passes through the field and leaves the frozen pawn there."""
    seat = arm(game, "Big D Randy")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 3)
    place(game, "żółty", 1)
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id not in ("czerwony", "żółty")):
        place(game, pawn.id, 20 + index)
    use(game, seat, pawn="czerwony")

    card = chest_card(game, "Dzieckorolka", seat)
    events = play(game, seat, card, pawn="żółty")
    walked = [e for e in events if isinstance(e, ev.TokenWalked)]
    assert walked, "the card resolved and the mover moved"
    assert all(e.pawn_id != "czerwony" for e in walked), "never carried"
    mover = next(e for e in walked if e.pawn_id == "żółty")
    assert 3 in mover.route, "it really did pass through the frozen pawn's field"
    assert game.board.position_of_pawn("czerwony") == 3, "left where it stood"


def test_balbinka_moves_everybody_except_the_frozen_pawn(game):
    """§13: one frozen pawn does not cancel the card for the others."""
    seat = arm(game, "Big D Randy")
    for index, pawn in enumerate(game.library.pawns):
        game.board.remove_pawn(pawn.id)
        place(game, pawn.id, 5 + index * 2)
    use(game, seat, pawn="czerwony")
    before = positions(game)

    card = chest_card(game, "Balbinka", seat)
    play(game, seat, card, direction="forward")
    assert game.board.position_of_pawn("czerwony") == before["czerwony"]
    moved = [pid for pid in before
             if pid != "czerwony"
             and game.board.position_of_pawn(pid) != before[pid]]
    assert moved, "everybody else still moved"


@pytest.mark.parametrize("option", ["gather", "scatter"])
def test_gejtos_cannot_move_the_frozen_pawn(game, option):
    """Both halves, resolved for real, each on a fresh arrangement.

    ``gather`` is Mężczyzna and ``scatter`` is Kobieta; the anchor pawn is
    named separately.  Getting either key wrong leaves the card sitting on an
    unanswered prompt, which would move nothing and pass this test for entirely
    the wrong reason — hence the assertions that the card resolved and that
    somebody else DID move.
    """
    seat = arm(game, "Big D Randy")
    for index, pawn in enumerate(game.library.pawns):
        game.board.remove_pawn(pawn.id)
        place(game, pawn.id, 6 + index)
    use(game, seat, pawn="czerwony")
    frozen_at = game.board.position_of_pawn("czerwony")
    # The anchor is the pawn on the very next field, so the frozen one is a
    # NEIGHBOUR — the only arrangement in which Gejtos would try to move it.
    anchor = "zielony"
    assert abs(game.board.position_of_pawn(anchor) - frozen_at) == 1

    before = positions(game)
    card = chest_card(game, "Gejtos", seat)
    events = play(game, seat, card, pawn=anchor, option=option)
    assert not any(isinstance(e, ev.ChoiceRequired) for e in events), (
        "the card must actually resolve"
    )
    assert game.board.position_of_pawn("czerwony") == frozen_at
    moved = [pid for pid in before
             if game.board.position_of_pawn(pid) != before[pid]]
    assert moved, "other pawns were still affected"


def test_paa_skips_a_frozen_foremost_pawn(game):
    """§15: Mitoman's ability takes the foremost pawn it is ALLOWED to take."""
    seat = arm(game, "Big D Randy")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 20)         # foremost, about to be frozen
    place(game, "żółty", 15)            # the next one forward
    place(game, "zielony", 2)           # hindmost: the destination
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id not in ("czerwony", "żółty", "zielony")):
        place(game, pawn.id, 8 + index)
    use(game, seat, pawn="czerwony")

    mit = next(p.index for p in game.players
               if not p.is_piotrek and p.index != seat)
    give_character(game, mit, "Mitoman")
    game.apply(cmd.SetActivePlayer(player_index=mit))
    use(game, mit)

    assert game.board.position_of_pawn("czerwony") == 20, "the frozen pawn stays"
    assert game.board.position_of_pawn("żółty") == 2, "the next one moved instead"


def test_skrypt_cannot_move_a_frozen_pawn(game):
    """§16: preferably filtered out of the prompt; blocked either way."""
    seat = arm(game, "Big D Randy")
    place(game, "czerwony", 5)
    use(game, seat, pawn="czerwony")

    dziad = next(p.index for p in game.players
                 if not p.is_piotrek and p.index != seat)
    give_character(game, dziad, "Dziad")
    game.apply(cmd.SetActivePlayer(player_index=dziad))

    asked = next(e for e in use(game, dziad)
                 if isinstance(e, ev.ChoiceRequired))
    assert "czerwony" not in {option[0] for option in asked.options}, (
        "the frozen pawn is not offered in the first place"
    )

    # And naming it anyway — a stale or hand-made command — moves nothing.
    events = use(game, dziad, pawn="czerwony", move="1")
    assert not any(isinstance(e, ev.TokenWalked) for e in events)
    assert game.board.position_of_pawn("czerwony") == 5


# ═════════════════════════════════════════════════════════════════════════════
# §40 — manual movement and cleanup
# ═════════════════════════════════════════════════════════════════════════════
def test_dragging_a_frozen_pawn_by_hand_cancels_the_freeze(game):
    """§7: the testing tool is the one thing allowed to overrule the freeze."""
    freeze(game, "czerwony")
    destination = game.board.positions[9].tiles[0]
    events = game.apply(cmd.MoveToken(
        pawn_id="czerwony", tile_index=destination.index,
        x=destination.position[0], y=destination.position[1],
    ))
    assert any(isinstance(e, ev.StatusEnded) for e in events)
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")
    assert game.board.position_of_pawn("czerwony") == 9, "it really moved"


def test_after_a_manual_drag_cards_affect_the_pawn_normally_again(game):
    seat = freeze(game, "czerwony")
    destination = game.board.positions[9].tiles[0]
    game.apply(cmd.MoveToken(pawn_id="czerwony", tile_index=destination.index,
                             x=destination.position[0], y=destination.position[1]))
    card = hand_card(game, "Zerówka - czerwony", seat)
    play(game, seat, card)
    assert game.board.position_of_pawn("czerwony") == 10


def test_the_blue_highlight_follows_the_status(game):
    """The board derives the highlight; it never stores one.

    Which is why every way a freeze ends removes it without knowing the board
    is drawing anything.
    """
    from pedzacy_piotrek.ui.board_view import BoardView

    seat = freeze(game, "czerwony")
    tile = game.board.pawn_tile("czerwony")
    frozen = [status.subject_id
              for status in game.statuses.of_kind(StatusKind.FROZEN)]
    assert frozen == ["czerwony"]
    assert BoardView.frozen_tiles(_FakeView(game)) == [tile.index]

    game.statuses.remove(StatusKind.FROZEN, Subject.PAWN, "czerwony")
    assert BoardView.frozen_tiles(_FakeView(game)) == [], "no stale highlight"


class _FakeView:
    """Just enough of a BoardView for ``frozen_tiles`` — no pygame needed."""

    def __init__(self, state):
        self.state = state


def test_removing_the_frozen_pawn_from_the_board_ends_the_freeze(game):
    freeze(game, "czerwony")
    game.apply(cmd.MoveToken(pawn_id="czerwony", x=-9999.0, y=-9999.0))
    assert game.board.position_of_pawn("czerwony") is None
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_stacking_onto_a_frozen_pawn_does_not_move_the_freeze(game):
    """§20: the freeze belongs to the pawn it was put on."""
    freeze(game, "czerwony")
    where = game.board.position_of_pawn("czerwony")
    place(game, "żółty", where)
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "żółty")


# ═════════════════════════════════════════════════════════════════════════════
# §41 — Jazdy
# ═════════════════════════════════════════════════════════════════════════════
def test_jazdy_can_be_activated_on_a_hunter_turn(game):
    seat = arm(game, "Lubin")
    events = use(game, seat)
    assert any(isinstance(e, ev.AbilityUsed) for e in events)
    assert game.statuses.player_has(StatusKind.SKIP_TURN, piotrek_seat(game))


def test_jazdy_cannot_skip_the_turn_piotrek_is_taking(game):
    """§23: it takes a FUTURE turn, never the one in progress."""
    seat = arm(game, "Lubin")
    game.apply(cmd.SetActivePlayer(player_index=piotrek_seat(game)))
    events = use(game, seat)
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not game.statuses.player_has(StatusKind.SKIP_TURN, piotrek_seat(game))
    assert game.players[seat].character.uses_left == 1


def test_jazdy_skips_the_next_piotrek_turn_and_only_that_one(game):
    """§24: Piotrek holds three slots; exactly one of them is lost."""
    seat = arm(game, "Lubin")
    piotrek = piotrek_seat(game)
    order = game.seat_order()
    assert order.count(piotrek) >= 2, "the order really does repeat Piotrek"

    use(game, seat)
    skipped = []
    played = []
    for _ in range(len(order) * 2):
        events = game.apply(cmd.EndTurn(player_index=game.active_player_index))
        for event in events:
            if isinstance(event, ev.TurnSkipped) and event.player_index == piotrek:
                skipped.append(event)
        if game.active_player_index == piotrek:
            played.append(game.turn_counter)
        if len(played) >= 2:
            break

    assert len(skipped) == 1, "exactly one Piotrek turn was taken away"
    assert played, "and a later Piotrek turn happened normally"
    assert not game.statuses.player_has(StatusKind.SKIP_TURN, piotrek), (
        "the status is spent, not left lying around"
    )


def test_normal_turn_order_resumes_after_the_skip(game):
    """The skip takes a turn; it does not touch the order itself.

    ``seat_order()`` is per-round and legitimately differs from round to round
    — the hunters cycle continuously — so the thing to pin is the CADENCE:
    Piotrek still holds every third slot, and nobody has left the order.
    """
    seat = arm(game, "Lubin")
    piotrek = piotrek_seat(game)
    seats_before = sorted(set(game.seat_order()))
    use(game, seat)
    for _ in range(len(game.seat_order()) + 2):
        game.apply(cmd.EndTurn(player_index=game.active_player_index))

    order_after = game.seat_order()
    assert sorted(set(order_after)) == seats_before, "everybody still plays"
    assert piotrek in order_after, "Piotrek still has turns coming"
    assert not game.statuses.player_has(StatusKind.SKIP_TURN, piotrek), (
        "and no skip is left hanging over him"
    )
    assert not game.players[piotrek].eliminated, "Piotrek is still in the game"


def test_jazdy_does_not_remove_piotrek_from_the_order(game):
    seat = arm(game, "Lubin")
    piotrek = piotrek_seat(game)
    use(game, seat)
    assert piotrek in game.seat_order()


# ═════════════════════════════════════════════════════════════════════════════
# §42 — Where are you Marcus?
# ═════════════════════════════════════════════════════════════════════════════
def test_glockboy_cannot_act_with_fewer_than_three_checks(game):
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski"])
    events = use(game, seat, pawn="czerwony")
    rejected = next(e for e in events if isinstance(e, ev.ActionRejected))
    assert "3" in rejected.reason
    assert game.players[seat].character.uses_left == 1
    assert game.pending_pawn_check is None


def test_only_completed_checks_count(game):
    """Previews and unanswered prompts never reach ``eliminated_pawns``."""
    seat = arm(game, "Glockboy")
    assert effects.completed_checks(game) == []
    use(game, seat)                     # opens nothing: refused on the count
    assert effects.completed_checks(game) == [], "asking is not checking"


def test_glockboy_may_act_after_three_completed_checks(game):
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    events = use(game, seat, pawn="czerwony")
    assert any(isinstance(e, ev.AbilityUsed) for e in events)
    assert game.pending_pawn_check == ("czerwony", seat)


def test_an_already_checked_colour_is_not_offered_again(game):
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    events = use(game, seat)
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    offered = {option[0] for option in asked.options}
    assert not offered & {"zielony", "niebieski", "różowy"}


def test_a_correct_guess_wins_the_game_for_the_hunters(game):
    """§28: the ORDINARY hunter victory, through the existing machinery."""
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    hidden = victory.hidden_pawn(game)
    assert hidden and hidden not in game.eliminated_pawns
    use(game, seat, pawn=hidden)

    follow_ups = victory.review(game)
    assert follow_ups and isinstance(follow_ups[0], cmd.DeclareVictory)
    game.apply_many(follow_ups)

    assert game.phase is MatchPhase.ENDED
    assert game.victory is not None
    assert game.victory.outcome is Outcome.HUNTERS
    assert not game.players[seat].eliminated, "the winner is not knocked out"


def test_a_wrong_guess_eliminates_glockboy_and_crosses_the_colour_off(game):
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    hidden = victory.hidden_pawn(game)
    wrong = next(p.id for p in game.library.pawns
                 if p.id != hidden and p.id not in game.eliminated_pawns)
    use(game, seat, pawn=wrong)

    follow_ups = victory.review(game)
    kinds = [type(c) for c in follow_ups]
    assert cmd.EliminatePawn in kinds and cmd.EliminatePlayer in kinds
    game.apply_many(follow_ups)

    assert wrong in game.eliminated_pawns, "the colour is crossed off"
    assert game.players[seat].eliminated
    assert game.phase is not MatchPhase.ENDED, "the match carries on"
    assert game.pending_pawn_check is None


def test_a_wrong_guess_does_not_end_the_game_or_lose_it_for_the_hunters(game):
    """§33: one hunter dropping out is not a defeat."""
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    hidden = victory.hidden_pawn(game)
    wrong = next(p.id for p in game.library.pawns
                 if p.id != hidden and p.id not in game.eliminated_pawns)
    use(game, seat, pawn=wrong)
    game.apply_many(victory.review(game))

    assert game.victory is None
    assert victory.review(game) == [], "nothing further is pending"


def eliminate_glockboy(game) -> int:
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    hidden = victory.hidden_pawn(game)
    wrong = next(p.id for p in game.library.pawns
                 if p.id != hidden and p.id not in game.eliminated_pawns)
    use(game, seat, pawn=wrong)
    game.apply_many(victory.review(game))
    assert game.players[seat].eliminated
    return seat


def test_an_eliminated_player_cannot_use_abilities(game):
    seat = eliminate_glockboy(game)
    game.players[seat].character.uses_left = 1
    game.active_player_index = seat
    events = use(game, seat, pawn="czerwony")
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not any(isinstance(e, ev.AbilityUsed) for e in events)


def test_an_eliminated_player_cannot_move(game):
    seat = eliminate_glockboy(game)
    game.active_player_index = seat
    card = hand_card(game, "Zerówka - czerwony", seat)
    where = game.board.position_of_pawn("czerwony")
    play(game, seat, card)
    assert game.board.position_of_pawn("czerwony") == where


def test_every_future_turn_of_an_eliminated_player_is_skipped(game):
    """§31: the seat keeps its slots and loses all of them."""
    seat = eliminate_glockboy(game)
    assert seat in game.seat_order(), "still in the order"

    skips = 0
    for _ in range(len(game.seat_order()) * 2):
        events = game.apply(cmd.EndTurn(player_index=game.active_player_index))
        skips += sum(1 for e in events
                     if isinstance(e, ev.TurnSkipped) and e.player_index == seat)
        assert game.active_player_index != seat, "never left holding the turn"
    assert skips >= 1


def test_an_eliminated_player_stays_connected_as_an_observer(game):
    seat = eliminate_glockboy(game)
    assert game.player(seat) is not None, "still a seat at the table"
    assert len(game.players) == 6, "nobody was removed from the session"
    assert game.player(seat).owner_id == game.players[seat].owner_id


def test_elimination_is_public_state_the_interface_can_draw(game):
    """§30: the X is drawn from state, so a reconnecting client sees it too."""
    seat = eliminate_glockboy(game)
    assert game.players[seat].to_public_dict()["eliminated"] is True
    assert game.snapshot() is not None


def test_elimination_travels_in_the_snapshot(game):
    seat = arm(game, "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    use(game, seat, pawn="czerwony")
    assert game.snapshot()["pending_pawn_check"] == ["czerwony", seat]


def test_the_elimination_command_is_authority_only(game):
    assert isinstance(cmd.EliminatePlayer(player_index=0), cmd.AUTHORITY_ONLY)


# ═════════════════════════════════════════════════════════════════════════════
# §43 — Plac
# ═════════════════════════════════════════════════════════════════════════════
def test_plac_is_refused_while_a_pawn_is_on_start(game):
    seat = hunter_seat(game)
    give_character(game, seat, "Norbur")
    clear_start(game)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()
    events = use(game, seat)
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.players[seat].character.uses_left == 1


def test_plac_activates_once_start_is_empty_and_spends_its_use(game):
    seat = arm(game, "Norbur")
    events = use(game, seat)
    used = next(e for e in events if isinstance(e, ev.AbilityUsed))
    assert used.uses_left == 0
    assert game.players[seat].character.uses_left == 0


def test_plac_creates_no_movement_restriction(game):
    seat = arm(game, "Norbur")
    before = positions(game)
    order_before = game.seat_order()
    use(game, seat)

    assert game.statuses.movement_range() is None
    assert not game.statuses.of_kind(StatusKind.RESTRICTED_MOVEMENT)
    assert game.seat_order() == order_before, "no turn-order change"
    assert positions(game) == before, "no pawn moved"


def test_plac_creates_no_temporary_status_at_all(game):
    """A no-op that quietly left a status behind would not be a no-op."""
    seat = arm(game, "Norbur")
    before = {(s.kind, s.subject, s.subject_id) for s in game.statuses.all()}
    use(game, seat)
    after = {(s.kind, s.subject, s.subject_id) for s in game.statuses.all()}
    assert after == before


def test_plac_does_not_restrict_pawn_selection(game):
    seat = arm(game, "Norbur")
    use(game, seat)
    card = hand_card(game, "Astral 2019", seat)
    events = play(game, seat, card)
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    offered = {option[0] for option in asked.options}
    assert len(offered) == len(game.library.pawns), "every pawn still selectable"


def test_a_card_that_would_have_broken_the_old_span_resolves(game):
    seat = arm(game, "Norbur")
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    place(game, "czerwony", 3)
    place(game, "żółty", 9)
    for index, pawn in enumerate(p for p in game.library.pawns
                                 if p.id not in ("czerwony", "żółty")):
        place(game, pawn.id, 15 + index)
    use(game, seat)

    card = hand_card(game, "Fillerski przedmiot - żółty", seat)
    events = play(game, seat, card)
    assert not any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.board.position_of_pawn("żółty") == 11
