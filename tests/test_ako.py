"""
AKO — the Mod Patusa that makes every movement card bring a neighbour along.

Two variants, and the whole difference between them is what happens to the
tower standing on the pawn that is brought:

    variant 1  "with_stack"   the ordinary tower rule applies to it
    variant 2  "alone"        it is lifted out of the stack and travels alone

The brief's two worked examples are pinned literally below
(``test_the_briefs_forward_example`` / ``test_the_briefs_backward_example``),
because they are the specification and a paraphrase of them is not.

Engine level throughout, so it runs headless; the network section at the bottom
uses the in-process server the other sync tests use.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.base_card import EffectSpec
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import Status, StatusKind

AKO = "AKO"
WITH_STACK = "with_stack"
ALONE = "alone"

#: The six colours, by the ids the content actually uses.
BLUE, PINK, ORANGE = "niebieski", "różowy", "pomarańczowy"
GREEN, YELLOW, RED = "zielony", "żółty", "czerwony"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, variant: str = "", **kwargs):
    """A game that never interrupts itself, with AKO on a chosen variant."""
    if variant:
        kwargs.setdefault("card_variants", {AKO: variant})
    config = SessionConfig(
        num_players=4, board_cells=24, seed=99, chest_open_round=10_000,
        mod_round_first=10_000, double_frequency=0.0,
        piotrek_picks_pawn=False, **kwargs,
    )
    return create_game(config, library)


def install(game, title: str, slot: int = 0):
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[slot] = card
    game._sync_mod_states()
    return card


def field(game, position_index: int) -> int:
    """The first concrete field of a position — '2a' of the brief's '2'."""
    return game.board.position(position_index).tiles[0].index


def pose(game, **places: int) -> None:
    """Put the named pawns at the named POSITIONS, in the order given.

    Order matters: two pawns sent to the same position stack in the order they
    arrive, which is how the brief's ``blue / pink on blue / orange on pink``
    tower is built.  Anything unmentioned is parked well out of the way so a
    test that means "this pawn is alone" gets one.

    NOT ON THE LAST POSITION.  A pawn on the finish ends the match, and a
    posed position that quietly declares a winner makes every command after it
    fail with "Gra została zakończona" — which is correct behaviour and a very
    confusing test failure.
    """
    for pawn_id, position in places.items():
        game.board.place_pawn(pawn_id, field(game, position))
    spare = max(0, game.board.last_position - 2)
    for pawn in game.library.pawns:
        if pawn.id not in places:
            game.board.place_pawn(pawn.id, field(game, spare))


def stack_at(game, position_index: int):
    """Every pawn standing at a position, bottom first, both halves together."""
    out = []
    for tile in game.board.position(position_index).tiles:
        out.extend(tile.stack)
    return out


def move(spec_steps: int = 1, pawn: str = GREEN, direction: str = "forward"):
    return EffectSpec(type="move_pawn", params={
        "target": "fixed", "pawn": pawn,
        "steps": spec_steps, "direction": direction,
    })


def play(game, spec, choices=None, actor: int = 0):
    """Resolve a MOVEMENT CARD's effect and, if it is a plan, carry it out."""
    result = effects.resolve_spec(game, spec, actor, choices or {}, "test",
                                  deck_id=settings.DECK_MOVEMENT)
    if isinstance(result, effects.Plan):
        game._execute(result, actor)
    return result


# ── the card and its variants ────────────────────────────────────────────────
def test_ako_declares_its_rule_in_the_data(library):
    definition = next(card for card in library.deck(settings.DECK_MODS).cards
                      if card.title == AKO)
    assert definition.passive == {"carry_neighbour": True}
    assert definition.variant_ids == (WITH_STACK, ALONE)
    assert definition.with_variant(WITH_STACK).passive == {
        "carry_neighbour": True}
    assert definition.with_variant(ALONE).passive == {
        "carry_neighbour": True, "carry_neighbour_alone": True}


def test_the_two_variants_say_what_the_brief_says(library):
    definition = next(card for card in library.deck(settings.DECK_MODS).cards
                      if card.title == AKO)
    assert (definition.with_variant(WITH_STACK).text
            == "Wszystkie ruchy poruszają jednego sąsiadującego pionka")
    assert (definition.with_variant(ALONE).text
            == "Wszystkie ruchy poruszają TYLKO jednego sąsiadującego pionka")


def test_the_variants_share_one_identity(library):
    """It is one card with a setting, not two cards — stage 34's rule."""
    definition = next(card for card in library.deck(settings.DECK_MODS).cards
                      if card.title == AKO)
    one = definition.with_variant(WITH_STACK)
    two = definition.with_variant(ALONE)
    assert one.title == two.title == AKO
    assert one.art == two.art == definition.art
    assert one.count == two.count == definition.count


def test_the_rack_answers_the_rule(library):
    game = make(library, WITH_STACK)
    assert not game.carries_neighbour
    install(game, AKO)
    assert game.carries_neighbour
    assert not game.carries_neighbour_alone

    game = make(library, ALONE)
    install(game, AKO)
    assert game.carries_neighbour
    assert game.carries_neighbour_alone


# ── the brief's own examples ─────────────────────────────────────────────────
def test_the_briefs_forward_example_variant_1(library):
    """green 3→4 taking blue, whose tower comes with it.

        2a: blue / pink / orange     3: green     4: yellow
                              ↓
        3: blue / pink / orange      4: green + yellow
    """
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2, YELLOW: 3})

    play(game, move(), {"ako": BLUE})

    assert stack_at(game, 1) == []
    assert stack_at(game, 2) == [BLUE, PINK, ORANGE]
    assert stack_at(game, 3) == [YELLOW, GREEN]


def test_the_briefs_forward_example_variant_2(library):
    """The same move under "TYLKO jednego": pink and orange stay put.

        2a: blue / pink / orange     3: green
                              ↓
        2a: pink / orange     3: blue     4: green
    """
    game = make(library, ALONE)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2, YELLOW: 3})

    play(game, move(), {"ako": BLUE})

    assert stack_at(game, 1) == [PINK, ORANGE]
    assert stack_at(game, 2) == [BLUE]
    assert stack_at(game, 3) == [YELLOW, GREEN]


@pytest.mark.parametrize("variant", [WITH_STACK, ALONE])
def test_the_briefs_backward_example(library, variant):
    """green 3→2a backwards, and yellow follows it 4→3.

    The neighbour is the pawn on the far side of the direction of travel, so a
    backward move takes the one IN FRONT.  Both variants agree here: yellow is
    alone, so it has no tower to differ about.
    """
    game = make(library, variant)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2, YELLOW: 3})

    play(game, move(direction="backward"))

    assert stack_at(game, 1) == [BLUE, PINK, ORANGE, GREEN]
    assert stack_at(game, 2) == [YELLOW]
    assert stack_at(game, 3) == []


# ── variant 1: the ordinary stacking rules ───────────────────────────────────
def test_variant_1_uses_the_games_own_tower_rule(library):
    """Nothing here reimplements stacking: the riders come from ``travellers``.

    Pinning it by moving a pawn that carries a tower AND is linked to another
    pawn by Ondrej's Radar — a rule AKO knows nothing about, which still
    applies because the companion is an ordinary move.
    """
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2, RED: 5})
    game.statuses.add(Status.for_pawn(
        StatusKind.LINKED, BLUE, data={"members": [BLUE, RED]}))

    play(game, move(), {"ako": BLUE})

    assert stack_at(game, 2) == [BLUE, PINK, RED]


def test_variant_1_leaves_pawns_under_the_chosen_one_behind(library):
    """The tower rule is "everything ON it", not "everything with it"."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2})

    play(game, move(), {"ako": PINK})

    assert stack_at(game, 1) == [BLUE]
    assert stack_at(game, 2) == [PINK, ORANGE]


# ── variant 2: only the chosen pawn ──────────────────────────────────────────
def test_variant_2_moves_the_chosen_pawn_alone(library):
    game = make(library, ALONE)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2})

    play(game, move(), {"ako": BLUE})

    assert stack_at(game, 2) == [BLUE]
    assert stack_at(game, 1) == [PINK, ORANGE]


def test_variant_2_leaves_a_valid_board_behind(library):
    """Lifting a pawn out of the middle of a stack must not corrupt it."""
    game = make(library, ALONE)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2})

    play(game, move(), {"ako": PINK})

    assert stack_at(game, 1) == [BLUE, ORANGE]
    assert stack_at(game, 2) == [PINK]
    # Every pawn is on exactly one field, and every field agrees with the index.
    for pawn in game.library.pawns:
        tile = game.board.pawn_tile(pawn.id)
        assert tile is not None and pawn.id in tile.stack
        assert sum(pawn.id in t.stack for t in game.board.tiles) == 1
    assert game.board.carried_pawns(BLUE) == [ORANGE]


def test_variant_2_moves_nobody_else(library):
    game = make(library, ALONE)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2, YELLOW: 6, RED: 9})
    before = {p.id: game.board.position_of_pawn(p.id)
              for p in game.library.pawns}

    play(game, move(), {"ako": BLUE})

    after = {p.id: game.board.position_of_pawn(p.id)
             for p in game.library.pawns}
    moved = {pawn for pawn in after if after[pawn] != before[pawn]}
    assert moved == {GREEN, BLUE}


def test_the_two_variants_do_not_behave_alike(library):
    """The regression the whole card turns on."""
    results = {}
    for variant in (WITH_STACK, ALONE):
        game = make(library, variant)
        install(game, AKO)
        pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2})
        play(game, move(), {"ako": BLUE})
        results[variant] = (stack_at(game, 1), stack_at(game, 2))
    assert results[WITH_STACK] != results[ALONE]
    assert results[WITH_STACK] == ([], [BLUE, PINK, ORANGE])
    assert results[ALONE] == ([PINK, ORANGE], [BLUE])


# ── choosing the neighbour ───────────────────────────────────────────────────
def test_the_player_is_asked_when_several_pawns_qualify(library):
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2})

    result = play(game, move())

    assert isinstance(result, effects.Choice)
    assert result.key == "ako"
    assert result.kind == "pawn"
    assert set(result.pawns) == {BLUE, PINK, ORANGE}


def test_only_neighbours_are_offered(library):
    """Not an arbitrary pawn anywhere on the board."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2, YELLOW: 3, RED: 7})

    result = play(game, move())

    assert isinstance(result, effects.Choice)
    assert set(result.pawns) == {BLUE, PINK}
    for stranger in (YELLOW, RED, GREEN):
        assert stranger not in result.pawns


def test_an_invalid_pawn_is_not_accepted(library):
    """A hand-written answer naming a pawn that is nowhere near is re-asked."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2, RED: 7})

    result = play(game, move(), {"ako": RED})

    assert isinstance(result, effects.Choice)
    assert RED not in result.pawns
    assert game.board.position_of_pawn(RED) == 7


def test_a_single_candidate_is_taken_without_asking(library):
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, GREEN: 2})

    result = play(game, move())

    assert isinstance(result, effects.Plan)
    assert stack_at(game, 2) == [BLUE]


def test_no_neighbour_means_the_mover_simply_moves(library):
    """Not a refusal and not a fizzle: AKO adds nothing to this move."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{GREEN: 2, YELLOW: 8})

    result = play(game, move())

    assert isinstance(result, effects.Plan)
    assert stack_at(game, 3) == [GREEN]
    assert game.board.position_of_pawn(YELLOW) == 8


def test_a_frozen_neighbour_is_not_offered(library):
    """A freeze refuses a move everywhere else; it may not be dragged either."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2})
    game.statuses.add(Status.for_pawn(StatusKind.FROZEN, BLUE))

    result = play(game, move())

    assert isinstance(result, effects.Plan)          # PINK is the only one left
    assert stack_at(game, 2) == [PINK]
    assert game.board.position_of_pawn(BLUE) == 1


def test_the_side_is_read_from_the_direction_not_hard_coded(library):
    game = make(library)
    assert effects.neighbour_side(1) == -1
    assert effects.neighbour_side(3) == -1
    assert effects.neighbour_side(-1) == 1
    pose(game, **{BLUE: 1, GREEN: 2, YELLOW: 3})
    assert effects.neighbour_candidates(game, GREEN, 1) == [BLUE]
    assert effects.neighbour_candidates(game, GREEN, -1) == [YELLOW]


def test_a_card_played_by_another_card_still_brings_somebody(library):
    """``can_ask=False``: no prompt is possible, so palette order decides.

    Seks z pedałami and Troll's forced play choose from
    ``resolves_without_asking``, which cannot know a mod has just given the
    card a question — so AKO must not turn those cards into a fizzle.
    """
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2})

    result = effects.resolve_spec(game, move(), 0, {}, "test",
                                  deck_id=settings.DECK_MOVEMENT,
                                  can_ask=False)

    assert isinstance(result, effects.Plan)
    game._execute(result, 0)
    # Palette order: blue comes before pink and orange in characters.json.
    assert stack_at(game, 2) == [BLUE, PINK, ORANGE]


# ── how AKO sits in the movement pipeline ────────────────────────────────────
def test_ako_is_a_rule_about_movement_cards(library):
    """Like Masa solna and Halloween — not about abilities (N103).

    Dziad's Skrypt is an ordinary ``move_pawn``; a mod that redirected the
    movement deck must not quietly rewrite a character ability too.
    """
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, GREEN: 2})

    result = effects.resolve_spec(game, move(), 0, {}, "Skrypt",
                                  origin="ability",
                                  deck_id=settings.DECK_CHARACTERS)
    game._execute(result, 0)

    assert stack_at(game, 3) == [GREEN]          # the ability still moves it
    assert stack_at(game, 1) == [BLUE]           # ...and brings nobody along


def test_the_companion_travels_the_same_distance(library):
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, GREEN: 2})

    play(game, move(3))

    assert game.board.position_of_pawn(GREEN) == 5
    assert game.board.position_of_pawn(BLUE) == 4


def test_masa_solna_shortens_the_companion_too(library):
    """AKO joins the pipeline AFTER the distance is settled, so it inherits it."""
    game = make(library, WITH_STACK)
    install(game, AKO, slot=0)
    install(game, "Masa solna", slot=1)
    pose(game, **{BLUE: 1, GREEN: 2})

    play(game, move(3))

    assert game.board.position_of_pawn(GREEN) == 3
    assert game.board.position_of_pawn(BLUE) == 2


def test_a_movement_bonus_stretches_the_companion_too(library):
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, GREEN: 2})
    game.statuses.add(Status.for_player(StatusKind.MOVEMENT_BONUS, 0,
                                        data={"amount": 1}))

    play(game, move(1))

    assert game.board.position_of_pawn(GREEN) == 4
    assert game.board.position_of_pawn(BLUE) == 3


def test_a_pawn_pinned_by_halloween_brings_nobody(library):
    """The mover does not move, so there is no move to come along with."""
    game = make(library, WITH_STACK)
    install(game, AKO, slot=0)
    install(game, "Halloween", slot=1)
    pose(game, **{GREEN: 2, BLUE: 9})

    result = play(game, move())

    assert isinstance(result, effects.Plan)
    assert any(isinstance(op, effects.Fizzle) for op in result.operations)
    assert game.board.position_of_pawn(GREEN) == 2
    assert game.board.position_of_pawn(BLUE) == 9


def test_a_multi_pawn_card_brings_a_neighbour_for_each_move(library):
    """Plagiat! goes through the OTHER handler, which must not drift."""
    game = make(library, ALONE)
    install(game, AKO)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2, RED: 5, YELLOW: 9})
    spec = EffectSpec(type="move_pawns", params={
        "count": 2, "steps": 1, "direction": "forward", "asks": True})

    result = play(game, spec, {"pawns": f"{GREEN},{RED}",
                               "pawns_ako0": BLUE})

    assert isinstance(result, effects.Plan), getattr(result, "prompt", result)
    assert game.board.position_of_pawn(GREEN) == 3
    assert game.board.position_of_pawn(BLUE) == 2   # followed green
    assert game.board.position_of_pawn(PINK) == 1   # variant 2: left behind
    assert game.board.position_of_pawn(RED) == 6


def test_the_companion_is_an_ordinary_move_with_ordinary_events(library):
    """No parallel movement path: the executor emits what it always emits."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, GREEN: 2})
    result = effects.resolve_spec(game, move(), 0, {}, "test",
                                  deck_id=settings.DECK_MOVEMENT)
    kinds = [type(op).__name__ for op in result.operations]
    assert kinds == ["MovePawn", "MoveBySteps"]

    events = game._execute(result, 0)
    walked = [e for e in events if type(e).__name__ == "TokenWalked"]
    assert [e.pawn_id for e in walked] == [GREEN, BLUE]


def test_the_preview_highlights_the_companions_route(library):
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{BLUE: 1, GREEN: 2})
    result = effects.resolve_spec(game, move(), 0, {}, "test",
                                  deck_id=settings.DECK_MOVEMENT)
    tiles = effects.preview_tiles(game, result)
    assert field(game, 3) in tiles          # where green is going
    assert field(game, 2) in tiles          # where blue is going


# ── several copies, and the card leaving ─────────────────────────────────────
def test_two_copies_of_ako_bring_one_neighbour(library):
    """The rack's existing rule: a mod is a rule, not an accumulator.

    Read on the ALONE variant so the answer is unambiguous — under variant 1
    pink would ride along on top of blue by the ordinary tower rule, which is
    a different rule giving the same picture.
    """
    game = make(library, ALONE, mod_counts={AKO: 2})
    install(game, AKO, slot=0)
    install(game, AKO, slot=1)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2})

    play(game, move(), {"ako": BLUE})

    assert stack_at(game, 2) == [BLUE]
    assert stack_at(game, 1) == [PINK]


def test_two_copies_are_one_logical_card(library):
    game = make(library, ALONE, mod_counts={AKO: 2})
    first = install(game, AKO, slot=0)
    second = install(game, AKO, slot=1)
    assert first is not second
    assert first.definition is second.definition
    assert first.variant == second.variant == ALONE


def test_the_rule_leaves_with_the_card(library):
    game = make(library, WITH_STACK)
    install(game, AKO)
    game.mod_slots[0] = None
    game._sync_mod_states()
    assert not game.carries_neighbour
    pose(game, **{BLUE: 1, GREEN: 2})
    play(game, move())
    assert game.board.position_of_pawn(BLUE) == 1


def test_the_variant_can_be_changed_mid_match(library):
    """Stage 34's command, reused unchanged."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    assert not game.carries_neighbour_alone
    game.apply(cmd.SetCardVariant(deck_id=settings.DECK_MODS, title=AKO,
                                  variant=ALONE))
    assert game.carries_neighbour_alone
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2})
    play(game, move(), {"ako": BLUE})
    assert stack_at(game, 1) == [PINK]


# ── regression: nothing changes while AKO is not in the rack ─────────────────
@pytest.mark.parametrize("direction", ["forward", "backward"])
def test_normal_movement_is_untouched_without_ako(library, direction):
    game = make(library)
    pose(game, **{BLUE: 1, PINK: 1, ORANGE: 1, GREEN: 2, YELLOW: 3})
    before = {p.id: game.board.position_of_pawn(p.id)
              for p in game.library.pawns}

    result = play(game, move(direction=direction))

    assert isinstance(result, effects.Plan)
    after = {p.id: game.board.position_of_pawn(p.id)
             for p in game.library.pawns}
    assert {p for p in after if after[p] != before[p]} == {GREEN}


def test_no_question_is_asked_without_ako(library):
    game = make(library)
    pose(game, **{BLUE: 1, PINK: 1, GREEN: 2})
    assert isinstance(play(game, move()), effects.Plan)


def test_an_ordinary_movement_card_still_works(library):
    """A real card off the deck, not a hand-built spec."""
    game = make(library, WITH_STACK)
    install(game, AKO)
    pose(game, **{GREEN: 2, YELLOW: 8})
    card = next(c for c in game.decks[settings.DECK_MOVEMENT].draw_pile
                if c.effect is not None and c.effect.type == "move_pawn"
                and c.effect.target == "fixed")
    result = effects.resolve(game, card, 0)
    assert isinstance(result, (effects.Plan, effects.Choice, effects.Refusal))


# ── over a network ───────────────────────────────────────────────────────────
def test_every_machine_moves_the_same_two_pawns(library):
    """The companion travels inside the ordinary command, not beside it.

    The rack and the position are written onto EVERY replica including the
    authority's — the room's own state is a machine like any other, and a
    board posed on the clients alone would simply be resynced away.  Only what
    happens NEXT travels as a command.

    A NAMED card is fetched into the actor's hand through ``DrawTitledCard``,
    which is itself a command, so the move under test is a known one rather
    than whatever the deal happened to give.  That command needs an EDITING
    table since stage 53 — it is the one library action that hands a player a
    private advantage — so the lobby says so here.  Nothing else about the test
    changes: AKO's companion rule is what is under test.
    """
    from netkit import Table, all_agree

    table = Table(library)
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(card_variants={AKO: ALONE}, edit_mode=True)
    table.pump()
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients, "")

    parties = [host, *clients]
    room = table.room(host.room_code)
    everywhere = [room.state] + [s.state for s in parties]

    # An online table gets a real board, so its widened rows are wherever the
    # seed put them.  Landing on one asks "12a or 12b?" — a question this test
    # is not about — so the position is chosen where the destination is a
    # single field.  The board is identical on every machine, so asking the
    # authority is asking all of them.
    board = room.state.board
    base = next(index for index in range(1, board.last_position - 1)
                if not board.position(index + 1).is_doubled)

    for state in everywhere:
        assert state.card_variant(settings.DECK_MODS, AKO) == ALONE
        deck = state.decks[settings.DECK_MODS]
        card = next(c for c in deck.draw_pile if c.title == AKO)
        deck.draw_pile.remove(card)
        state.mod_slots[0] = card
        state._sync_mod_states()
        assert state.carries_neighbour_alone
        pose(state, **{BLUE: base - 1, PINK: base - 1, ORANGE: base - 1,
                       GREEN: base})
        state._sync_token_positions()

    seat = host.state.active_player_index
    actor = table.by_seat(host, clients)[seat]
    actor.session.submit(cmd.DrawTitledCard(
        player_index=seat, deck_id=settings.DECK_MOVEMENT,
        title="Zerówka - zielony"))
    table.pump()
    card = next(c for c in actor.state.player(seat).hand
                if c.title == "Zerówka - zielony")

    # The answer travels WITH the action, so every replica applies the pick.
    actor.session.submit(cmd.PlayCard(player_index=seat, card_uid=card.uid,
                                      choices={"ako": BLUE}))
    table.pump()

    for state in everywhere:
        assert stack_at(state, base + 1) == [GREEN]
        assert stack_at(state, base) == [BLUE]
        assert stack_at(state, base - 1) == [PINK, ORANGE]
    assert all_agree(*parties)
