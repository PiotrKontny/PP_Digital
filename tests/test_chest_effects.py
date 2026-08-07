"""
The first four Karty Skrzyni that do something: Dzieckorolka, Rage Quit,
Balbinka and Gambit Patusa.

Stage 27.  What these four have in common is that none of them is a movement
card, and three of them break a rule the movement deck relies on:

* Dzieckorolka cares which HALF of a widened row it walks through, where every
  other card in the game picks the nearer one and moves on;
* Balbinka moves every pawn its own distance, which means the tower rule has
  to be switched OFF or a rider travels twice;
* Gambit Patusa is a promise about a ROUND, and a round is a variable number of
  turns, so it cannot ride on the turn-based expiry every other status uses.

Engine level throughout, so it runs headless.  The same four across a real
server are in test_chest_effects_sync.py.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import Status, StatusKind, Subject


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 5, seed: int = 99, **kwargs):
    """A game that never interrupts itself, so a test can pose a position."""
    kwargs.setdefault("double_frequency", 0.0)
    kwargs.setdefault("mod_round_first", 10_000)
    kwargs.setdefault("chest_open_round", 10_000)
    config = SessionConfig(
        num_players=players, board_cells=24, seed=seed,
        piotrek_picks_pawn=False,
        debug_version=players < RULES.min_players, **kwargs,
    )
    return create_game(config, library)


def give(game, seat: int, title: str, deck_id: str = settings.DECK_CHEST):
    """Put a named card straight into a hand, bypassing the limit prompt.

    The hand limit is not what any of these tests is about, and a chest card
    dealt through the ordinary path would open a keep/discard overlay first.
    """
    deck = game.decks[deck_id]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.player(seat).add_card(card)
    return card


def play(game, seat: int, card, **choices):
    return game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid,
                                   choices={k: str(v) for k, v in choices.items()}))


def pawns(game):
    return [pawn.id for pawn in game.library.pawns]


def place(game, pawn_id: str, position: int, half: int = 0) -> int:
    """Stand a pawn on a board POSITION, returning the field it landed on."""
    tile = game.board.position(position).tiles[half]
    game.board.place_pawn(pawn_id, tile.index)
    game._sync_token_positions()
    return tile.index


def stack_at(game, position: int, half: int = 0):
    """The tower on a field, BOTTOM FIRST — the order the board stores."""
    return list(game.board.position(position).tiles[half].stack)


def choice_of(events):
    return next((e for e in events if isinstance(e, ev.ChoiceRequired)), None)


def clear_board(game):
    """Take every pawn off the road, so a test poses the whole position."""
    for pawn_id in pawns(game):
        game.board.remove_pawn(pawn_id)
    game._sync_token_positions()


# ── part 1: the deck composition ─────────────────────────────────────────────
def test_the_chest_deck_ships_the_counts_the_owner_asked_for(library):
    """The composition is CONTENT, so it is asserted against the data file."""
    counts = {card.title: card.count for card in library.deck(settings.DECK_CHEST).cards}
    assert counts == {
        "Dzieckorolka": 2,
        "Rage Quit": 2,
        "Balbinka": 2,
        "Nie masz Rosji": 2,
        "Gambit Patusa": 3,
        "Shady": 2,
        "Gejtos": 3,
        "Gamechanger": 1,
    }
    assert sum(counts.values()) == 17


def test_the_lobby_defaults_are_the_printed_counts(library):
    """The panel seeds itself from the data, so one change moves both.

    There is no second list of chest titles anywhere — that is the point of
    reading ``card.count`` — and this is what would fail if somebody added one.
    """
    from pedzacy_piotrek.config.settings import clamp_card_counts

    printed = {c.title: c.count for c in library.deck(settings.DECK_CHEST).cards}
    # An empty mapping still means "as printed", which is what an older client
    # and every test that never opened the panel send.
    built = library.deck(settings.DECK_CHEST).with_counts({}).build_cards()
    assert len(built) == sum(printed.values())
    # And the printed counts survive the clamp the lobby puts them through.
    assert clamp_card_counts(printed) == {t: printed[t] for t in sorted(printed)}


def test_the_new_counts_do_not_shrink_the_working_deck(library):
    """Stage 23 found 16 was the size a six-player table needs; 17 clears it.

    The sweep itself is
    ``test_chest_cadence.test_the_deck_can_supply_a_full_table_indefinitely``,
    which drives forty real rounds at every table size and now runs against
    these counts.  What is asserted HERE is the thing that sweep cannot see:
    the composition stopped being a uniform doubling, so the supply guarantee
    is no longer a side effect of every title having the same number of copies
    and has to be stated on its own.
    """
    total = len(library.deck(settings.DECK_CHEST).build_cards())
    assert total >= 16, "16 is the size stage 23 measured a full table needs"


def test_every_chest_title_can_still_be_dealt(library):
    """Gamechanger is down to ONE copy, which is the count worth checking.

    A title whose only copy sits in somebody's hand for the whole match is a
    balance decision; a title that cannot reach the table at all is a bug, and
    the two look identical from the outside until somebody counts.
    """
    game = make(library, 6, chest_open_round=1)
    deck = game.deck(settings.DECK_CHEST)
    seen: set[str] = set()
    for round_number in range(1, 41):
        events = game._begin_round(round_number)
        for event in events:
            if isinstance(event, ev.ChestCardAwarded):
                card = game.find_card(event.card_uid)
                if card is not None:
                    # Gamechanger becomes Alter Ego or Kingmaker on the way into
                    # the hand (D12), so the PRINTED title is the one that says
                    # which entry in cards.json was dealt.
                    printed_def = card.original_definition or card.definition
                    seen.add(printed_def.title)
        while game.pending_chest_choice is not None:
            seat, uids = game.pending_chest_choice
            limit = game.chest_limit(game.player(seat))
            game.apply(cmd.KeepChestCards(player_index=seat,
                                          keep_uids=tuple(uids[-limit:])))
        # Nothing may leak: every card is in a hand, the draw pile or the discard.
        held = sum(len(game.chest_cards(p)) for p in game.players)
        assert held + deck.draw_count + deck.discard_count == 17, round_number

    printed = {c.title for c in library.deck(settings.DECK_CHEST).cards}
    assert seen == printed, f"never dealt: {sorted(printed - seen)}"


# ── part 2: Dzieckorolka ─────────────────────────────────────────────────────
def test_dzieckorolka_asks_which_pawn_first(library):
    game = make(library)
    card = give(game, 0, "Dzieckorolka")
    question = choice_of(play(game, 0, card))
    assert question is not None
    assert question.kind == "pawn"
    # Nothing happened: an unanswered question changes no state (N40).
    assert game.player(0).card_by_uid(card.uid) is not None


def test_dzieckorolka_builds_the_tower_in_travelled_order(library):
    """The brief's own example, asserted from the bottom of the stack up.

    Green walks through a field holding Blue with Pink on top, then a field
    holding Red, and lands on Yellow.  Reading DOWNWARDS from Green the tower
    has to be Green, Pink, Red, Yellow — the journey in order — which is the
    same list bottom-first reversed.
    """
    game = make(library)
    clear_board(game)
    green, blue, pink, red, yellow = (
        "zielony", "niebieski", "różowy", "czerwony", "żółty")

    place(game, green, 1)
    place(game, blue, 2)
    place(game, pink, 2)      # lands on top of blue
    place(game, red, 3)
    place(game, yellow, 4)
    assert stack_at(game, 2) == [blue, pink]

    card = give(game, 0, "Dzieckorolka")
    play(game, 0, card, pawn=green)

    assert stack_at(game, 4) == [yellow, red, pink, green]
    # Blue was under Pink and is untouched: one pawn per field, the TOP one.
    assert stack_at(game, 2) == [blue]
    assert stack_at(game, 3) == []


def test_dzieckorolka_takes_one_pawn_per_field_and_only_the_top(library):
    """Three pawns on one field give up exactly one of them."""
    game = make(library)
    clear_board(game)
    mover = "zielony"
    a, b, c = "niebieski", "różowy", "czerwony"
    place(game, mover, 1)
    for pawn_id in (a, b, c):
        place(game, pawn_id, 2)
    assert stack_at(game, 2) == [a, b, c]

    card = give(game, 0, "Dzieckorolka")
    play(game, 0, card, pawn=mover)

    assert stack_at(game, 2) == [a, b]
    assert stack_at(game, 4) == [c, mover]


def test_dzieckorolka_leaves_the_destination_stack_alone(library):
    """The field it LANDS on is an ordinary landing, not a sweep."""
    game = make(library)
    clear_board(game)
    mover, low, high = "zielony", "niebieski", "różowy"
    place(game, mover, 1)
    place(game, low, 4)
    place(game, high, 4)

    card = give(game, 0, "Dzieckorolka")
    play(game, 0, card, pawn=mover)

    assert stack_at(game, 4) == [low, high, mover]


def test_dzieckorolka_walks_an_empty_road_without_collecting(library):
    game = make(library)
    clear_board(game)
    mover = "zielony"
    place(game, mover, 1)

    card = give(game, 0, "Dzieckorolka")
    events = play(game, 0, card, pawn=mover)

    assert stack_at(game, 4) == [mover]
    collected = next(e for e in events if isinstance(e, ev.PawnsCollected))
    assert collected.collected == []


def test_dzieckorolka_carries_its_own_riders_on_top(library):
    """A pawn moved from the bottom of a tower still takes the tower with it.

    The collected pawns go UNDERNEATH the mover; the riders stay above it.  Two
    different relationships to the same pawn, and getting them the same way
    round would put a passenger below the pawn carrying it.
    """
    game = make(library)
    clear_board(game)
    mover, rider, victim = "zielony", "żółty", "różowy"
    place(game, mover, 1)
    place(game, rider, 1)
    place(game, victim, 2)

    card = give(game, 0, "Dzieckorolka")
    play(game, 0, card, pawn=mover)

    assert stack_at(game, 4) == [victim, mover, rider]


def test_dzieckorolka_asks_for_every_widened_row_separately(library):
    """Which half it walks decides which pawn it takes, so each one is a question.

    Every other card in the game settles an intermediate widened row itself
    (D8a) because nothing depends on it.  Here everything depends on it.
    """
    game = make(library, seed=7, double_frequency=1.0)
    doubled = [i for i in range(game.board.position_count)
               if game.board.position(i).is_doubled]
    assert len(doubled) >= 4, "this board is meant to be full of widened rows"

    clear_board(game)
    mover = "zielony"
    start = doubled[0]
    place(game, mover, start)

    card = give(game, 0, "Dzieckorolka")
    answers: dict = {"pawn": mover}
    keys = []
    for _ in range(4):
        question = choice_of(play(game, 0, card, **answers))
        if question is None:
            break
        assert question.kind == "tile"
        keys.append(question.key)
        answers[question.key] = str(question.tiles[0])

    # Three steps forward over a board of widened rows is three questions, and
    # each one has its own key so no answer can overwrite another.
    assert keys == ["branch0", "branch1", "branch2"]
    assert len(set(keys)) == len(keys)


def test_dzieckorolka_collects_from_the_half_it_was_sent_down(library):
    """The pawn on the OTHER half of a widened row is not swept up."""
    game = make(library, seed=7, double_frequency=1.0)
    doubled = [i for i in range(game.board.position_count)
               if game.board.position(i).is_doubled]
    clear_board(game)
    mover, near, far = "zielony", "różowy", "czerwony"
    start = doubled[0]
    place(game, mover, start)
    place(game, near, start + 1, half=0)
    place(game, far, start + 1, half=1)

    card = give(game, 0, "Dzieckorolka")
    answers = {"pawn": mover}
    while True:
        question = choice_of(play(game, 0, card, **answers))
        if question is None:
            break
        position = game.board.position(start + 1 + len(answers) - 1)
        # Always take the FIRST half offered, which is where ``near`` stands.
        answers[question.key] = str(question.tiles[0])

    assert game.board.pawn_tile(far).index == game.board.position(
        start + 1).tiles[1].index
    assert far not in stack_at(game, start + 3, 0) + stack_at(game, start + 3, 1)
    assert near in (stack_at(game, start + 3, 0) + stack_at(game, start + 3, 1))


def test_dzieckorolka_will_not_dig_a_frozen_pawn_out_of_a_tower(library):
    """A frozen pawn may not move, and being swept up is moving.

    The field yields NOTHING rather than the sweep reaching past it: "always
    take the top pawn" is the rule, and digging underneath one would take a
    tower apart.
    """
    game = make(library)
    clear_board(game)
    mover, under, frozen = "zielony", "niebieski", "różowy"
    place(game, mover, 1)
    place(game, under, 2)
    place(game, frozen, 2)
    game.statuses.add(Status.for_pawn(StatusKind.FROZEN, frozen))

    card = give(game, 0, "Dzieckorolka")
    play(game, 0, card, pawn=mover)

    assert stack_at(game, 2) == [under, frozen]
    assert stack_at(game, 4) == [mover]


def test_dzieckorolka_is_untouched_by_the_movement_mods(library):
    """Masa solna and Halloween are rules about MOVEMENT cards (N103).

    A pawn alone on an empty road would be pinned by Halloween and a three-field
    card capped to one by Masa solna, if either applied to the Chest.
    """
    game = make(library)
    clear_board(game)
    mover = "zielony"
    place(game, mover, 1)
    for title, slot in (("Masa solna", 0), ("Halloween", 1)):
        deck = game.decks[settings.DECK_MODS]
        mod = next(c for c in deck.draw_pile if c.title == title)
        deck.draw_pile.remove(mod)
        game.mod_slots[slot] = mod
    game._sync_mod_states()
    assert game.movement_cap == 1 and game.requires_neighbour

    card = give(game, 0, "Dzieckorolka")
    play(game, 0, card, pawn=mover)

    assert game.board.position_of_pawn(mover) == 4


# ── part 3: Rage Quit ────────────────────────────────────────────────────────
def install(game, title: str, slot: int = 0):
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[slot] = card
    game._sync_mod_states()
    return card


def test_rage_quit_replaces_both_active_mods(library):
    game = make(library)
    first = install(game, "Speedrun", 0)
    second = install(game, "Paczka", 1)

    card = give(game, 0, "Rage Quit")
    play(game, 0, card)

    now = [c.uid for c in game.mod_slots]
    assert first.uid not in now and second.uid not in now
    assert all(slot is not None for slot in game.mod_slots)


def test_rage_quit_never_hands_back_what_it_just_threw_away(library):
    """The draws happen BEFORE the discards, or a reshuffle returns them.

    The mods deck is emptied down to nothing here, so the only cards a
    reshuffle could offer are the two being replaced.
    """
    game = make(library)
    first = install(game, "Speedrun", 0)
    second = install(game, "Paczka", 1)
    deck = game.decks[settings.DECK_MODS]
    deck.draw_pile.clear()
    deck.discard_pile.clear()

    card = give(game, 0, "Rage Quit")
    play(game, 0, card)

    # There was nothing to draw, so the rack is left exactly as it was rather
    # than being refilled with the outgoing pair.
    assert [c.uid for c in game.mod_slots] == [first.uid, second.uid]


def test_rage_quit_does_nothing_to_an_empty_rack(library):
    """Thunderfuck's rule: it replaces what is IN PLAY (N86, N99)."""
    game = make(library)
    assert not any(game.mod_slots)

    card = give(game, 0, "Rage Quit")
    events = play(game, 0, card)

    assert not any(game.mod_slots), "the rack must not be seeded ahead of schedule"
    assert any(isinstance(e, ev.MoveFizzled) for e in events)
    # The card still resolved, so it left the hand for the discard pile.
    assert game.player(0).card_by_uid(card.uid) is None


def test_rage_quit_leaves_an_empty_slot_empty(library):
    game = make(library)
    only = install(game, "Speedrun", 0)

    card = give(game, 0, "Rage Quit")
    play(game, 0, card)

    assert game.mod_slots[0] is not None and game.mod_slots[0].uid != only.uid
    assert game.mod_slots[1] is None


def test_rage_quit_runs_the_departure_half(library):
    """A mod leaving must leave nothing behind (N107).

    Shady is holding a pawn off the map; replacing it has to give the pawn back
    in the same command, or it is stranded for the rest of the match.
    """
    game = make(library)
    clear_board(game)
    for offset, pawn_id in enumerate(pawns(game)):
        place(game, pawn_id, 1 + offset)
    install(game, "Shady", 0)
    hidden = game.hidden_pawn_ids
    assert len(hidden) == 1

    card = give(game, 0, "Rage Quit")
    play(game, 0, card)

    assert game.hidden_pawn_ids == ()
    assert game.board.pawn_tile(hidden[0]) is not None


def test_rage_quit_arms_whatever_arrives(library):
    """The arrival half runs too: the new mods are armed in the same command."""
    game = make(library)
    install(game, "Speedrun", 0)
    install(game, "Sesja na PG", 1)

    card = give(game, 0, "Rage Quit")
    play(game, 0, card)

    for mod in game.active_mods:
        assert mod.uid in game.armed_mods


# ── part 4: Balbinka ─────────────────────────────────────────────────────────
def test_balbinka_asks_for_a_direction(library):
    game = make(library)
    card = give(game, 0, "Balbinka")
    question = choice_of(play(game, 0, card))
    assert question is not None
    assert question.key == "direction"
    assert {option for option, _ in question.options} == {"forward", "backward"}


@pytest.mark.parametrize("way, delta", [("forward", 2), ("backward", -2)])
def test_balbinka_moves_every_pawn_two(library, way, delta):
    game = make(library)
    clear_board(game)
    start = {pawn_id: 5 + offset for offset, pawn_id in enumerate(pawns(game))}
    for pawn_id, position in start.items():
        place(game, pawn_id, position)

    card = give(game, 0, "Balbinka")
    play(game, 0, card, direction=way)

    for pawn_id, position in start.items():
        assert game.board.position_of_pawn(pawn_id) == position + delta, pawn_id


def test_balbinka_moves_a_tower_two_and_keeps_its_order(library):
    """No pawn is CARRIED, or a rider would travel twice and land four on.

    The tower has to arrive intact all the same, which is what the ordering
    rule in the handler buys: bottom of a tower moves first, so it arrives
    first and the pawn that was above it lands back on top.
    """
    game = make(library)
    clear_board(game)
    bottom, middle, top = "zielony", "różowy", "czerwony"
    for pawn_id in (bottom, middle, top):
        place(game, pawn_id, 6)
    assert stack_at(game, 6) == [bottom, middle, top]

    card = give(game, 0, "Balbinka")
    play(game, 0, card, direction="forward")

    assert stack_at(game, 8) == [bottom, middle, top]
    assert stack_at(game, 6) == []


def test_balbinka_never_lands_on_a_pawn_that_has_not_moved_yet(library):
    """Going forward the furthest pawn goes first, or it walks out from under.

    Two pawns two fields apart: the one in front vacates its field before the
    one behind arrives on it, so neither ends up carrying the other.
    """
    game = make(library)
    clear_board(game)
    behind, ahead = "zielony", "różowy"
    place(game, behind, 5)
    place(game, ahead, 7)

    card = give(game, 0, "Balbinka")
    play(game, 0, card, direction="forward")

    assert stack_at(game, 7) == [behind]
    assert stack_at(game, 9) == [ahead]


def test_balbinka_going_backward_moves_the_rearmost_first(library):
    game = make(library)
    clear_board(game)
    behind, ahead = "zielony", "różowy"
    place(game, behind, 5)
    place(game, ahead, 7)

    card = give(game, 0, "Balbinka")
    play(game, 0, card, direction="backward")

    assert stack_at(game, 3) == [behind]
    assert stack_at(game, 5) == [ahead]


def test_balbinka_picks_a_widened_half_without_asking(library):
    """No player input, so the executor rolls for it — never the handler (N78)."""
    game = make(library, seed=7, double_frequency=1.0)
    clear_board(game)
    mover = "zielony"
    place(game, mover, 2)

    card = give(game, 0, "Balbinka")
    events = play(game, 0, card, direction="forward")

    assert choice_of(events) is None, "Balbinka must not open a second prompt"
    assert game.board.position_of_pawn(mover) == 4


def test_balbinka_is_the_same_on_two_machines_from_one_seed(library):
    """The random half comes from the seeded RNG, so replicas agree."""
    boards = []
    for _ in range(2):
        game = make(library, seed=7, double_frequency=1.0)
        clear_board(game)
        for offset, pawn_id in enumerate(pawns(game)):
            place(game, pawn_id, 1 + (offset % 3))
        card = give(game, 0, "Balbinka")
        play(game, 0, card, direction="forward")
        boards.append(game.board.to_dict()["stacks"])
    assert boards[0] == boards[1]


def test_balbinka_skips_a_frozen_pawn_and_moves_the_rest(library):
    game = make(library)
    clear_board(game)
    frozen, free = "zielony", "różowy"
    place(game, frozen, 5)
    place(game, free, 8)
    game.statuses.add(Status.for_pawn(StatusKind.FROZEN, frozen))

    card = give(game, 0, "Balbinka")
    play(game, 0, card, direction="forward")

    assert game.board.position_of_pawn(frozen) == 5
    assert game.board.position_of_pawn(free) == 10


# ── part 5: Gambit Patusa ────────────────────────────────────────────────────
def forward_card(game):
    """A movement card that moves a named pawn one field forward."""
    deck = game.decks[settings.DECK_MOVEMENT]
    return next(c for c in deck.draw_pile
                if c.effect is not None
                and c.effect.type == "move_pawn"
                and c.effect.target == "fixed"
                and c.effect.direction == "forward")


def hand_a_movement_card(game, seat: int, card):
    game.decks[settings.DECK_MOVEMENT].draw_pile.remove(card)
    game.player(seat).add_card(card)
    return card


def test_gambit_does_not_fire_in_the_round_it_is_played(library):
    """"w kolejnej rundzie" — the round it is played in is untouched."""
    game = make(library)
    game.round_number = 4

    card = give(game, 0, "Gambit Patusa")
    play(game, 0, card)

    assert not game.movement_reversed


def test_gambit_reverses_the_next_round(library):
    game = make(library)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))

    game._begin_round(5)
    assert game.movement_reversed


def test_gambit_lapses_by_itself_after_its_round(library):
    """One round, and nothing has to remember to switch it off."""
    game = make(library)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))

    game._begin_round(5)
    assert game.movement_reversed
    game._begin_round(6)
    assert not game.movement_reversed
    # And the spent status is gone rather than accumulating for the match.
    assert not game.statuses.of_kind(StatusKind.MOVEMENT_REVERSED)


def test_gambit_turns_a_forward_card_backward_at_the_same_distance(library):
    """Direction reverses; the distance on the card does not change."""
    game = make(library)
    clear_board(game)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))
    game._begin_round(5)

    card = hand_a_movement_card(game, 0, forward_card(game))
    pawn_id = card.effect.pawn
    place(game, pawn_id, 6)

    game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    assert game.board.position_of_pawn(pawn_id) == 5


def test_gambit_does_not_touch_a_chest_card(library):
    """It is a rule about MOVEMENT cards (N103), and Dzieckorolka is not one."""
    game = make(library)
    clear_board(game)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))
    game._begin_round(5)

    mover = "zielony"
    place(game, mover, 4)
    play(game, 0, give(game, 0, "Dzieckorolka"), pawn=mover)

    assert game.board.position_of_pawn(mover) == 7


def test_gambit_reaches_the_multi_pawn_handler_too(library):
    """Plagiat! moves through a SECOND handler, and N104 says both or neither."""
    game = make(library)
    clear_board(game)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))
    game._begin_round(5)

    first, second = "zielony", "różowy"
    place(game, first, 5)
    place(game, second, 8)
    card = next(c for c in game.decks[settings.DECK_MOVEMENT].draw_pile
                if c.title == "Plagiat!")
    hand_a_movement_card(game, 0, card)

    # Plagiat! is printed BACKWARD, so under a Gambit it goes forward.
    game.apply(cmd.PlayCard(
        player_index=0, card_uid=card.uid,
        choices={"pawns": effects.join_ids([first, second])},
    ))
    assert game.board.position_of_pawn(first) == 6
    assert game.board.position_of_pawn(second) == 9


def test_two_gambits_do_not_cancel_each_other(library):
    """The second is a promise about a DIFFERENT round, so it must stack.

    Replacing would let a Gambit played during a reversed round switch the
    reversal it was played under back off.
    """
    game = make(library)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))
    game._begin_round(5)
    assert game.movement_reversed

    play(game, 0, give(game, 0, "Gambit Patusa"))
    assert game.movement_reversed, "round 5 is still reversed"
    game._begin_round(6)
    assert game.movement_reversed, "and round 6 now is too"
    game._begin_round(7)
    assert not game.movement_reversed


def test_speedrun_asks_about_the_effective_direction(library):
    """A backward card under a Gambit is going forward, so Speedrun stays quiet.

    Offering to "turn it around" would be offering to undo the Gambit while
    describing it as undoing the card.
    """
    game = make(library)
    clear_board(game)
    install(game, "Speedrun", 0)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))
    game._begin_round(5)

    deck = game.decks[settings.DECK_MOVEMENT]
    card = next(c for c in deck.draw_pile
                if c.effect is not None and c.effect.type == "move_pawn"
                and c.effect.direction == "backward"
                and c.effect.target == "fixed")
    hand_a_movement_card(game, 0, card)
    place(game, card.effect.pawn, 6)

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    question = choice_of(events)
    assert question is None or question.key != "speedrun"
    assert game.board.position_of_pawn(card.effect.pawn) == 7


def test_speedrun_still_offers_to_turn_a_gambit_around(library):
    """A FORWARD card under a Gambit is now backward, so Speedrun speaks up."""
    game = make(library)
    clear_board(game)
    install(game, "Speedrun", 0)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))
    game._begin_round(5)

    card = hand_a_movement_card(game, 0, forward_card(game))
    place(game, card.effect.pawn, 6)

    events = game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid))
    question = choice_of(events)
    assert question is not None and question.key == "speedrun"

    game.apply(cmd.PlayCard(player_index=0, card_uid=card.uid,
                            choices={"speedrun": "forward"}))
    assert game.board.position_of_pawn(card.effect.pawn) == 7


def test_the_reversal_is_in_the_snapshot(library):
    """Two machines disagreeing about it disagree about a whole round."""
    game = make(library)
    game.round_number = 4
    play(game, 0, give(game, 0, "Gambit Patusa"))

    kinds = [s["kind"] for s in game.snapshot()["statuses"]]
    assert StatusKind.MOVEMENT_REVERSED.value in kinds
