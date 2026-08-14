"""
Paczka, Squid Game and Shady — the three Mody Patusa that do more than sit
there changing a number.

Stage 25.  Unlike the stage 24 four, none of these is a pure passive: each one
acts at a MOMENT — Paczka when it arrives, Squid Game at the start of every
round after it arrives, Shady when it arrives and again a round later.  Most of
what is worth testing here is therefore about timing and about what happens on
the way out, not about the rule while it sits in the rack.

Engine level throughout, so it runs headless.  The window Paczka opens is
tested in test_ui.py.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine import victory
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import StatusKind


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 5, seed: int = 99, **kwargs):
    """A game that never interrupts itself, so a test can pose a position."""
    kwargs.setdefault("double_frequency", 0.0)
    kwargs.setdefault("mod_round_first", 10_000)
    config = SessionConfig(
        num_players=players, board_cells=24, seed=seed,
        chest_open_round=10_000, piotrek_picks_pawn=False, **kwargs,
    )
    return create_game(config, library)


def install(game, title: str, slot: int = 0):
    """Put a named mod into the rack through the real arrival path.

    Returns the EVENTS the arrival produced, because for these three mods that
    is the interesting half — the card reaching the slot is the boring part.
    """
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[slot] = card
    return game._sync_mod_states()


def pawns(game):
    return [pawn.id for pawn in game.library.pawns]


def place(game, **positions):
    """Put pawns on given board POSITIONS, then resync the tokens."""
    for pawn_id, index in positions.items():
        tile = game.board.position(index).tiles[0]
        game.board.place_pawn(pawn_id, tile.index)
    game._sync_token_positions()


def spread(game, start: int = 1):
    """One pawn per position, so there is an unambiguous leader."""
    for offset, pawn_id in enumerate(pawns(game)):
        place(game, **{pawn_id: start + offset})


def set_secret(game, pawn_id: str) -> None:
    piotrek = next(p for p in game.players if p.is_piotrek)
    piotrek.secret_pawn = pawn_id


# ── part 1: Paczka ───────────────────────────────────────────────────────────
def test_paczka_declares_its_rule_in_the_data(library):
    """The rule is a passive, not a title the engine knows (N98)."""
    game = make(library)
    assert not game.chest_cards_revealed
    install(game, "Paczka")
    assert game.chest_cards_revealed


def test_paczka_announces_every_holder_the_moment_it_arrives(library):
    game = make(library)
    chest = game.decks[settings.DECK_CHEST]
    holder = game.players[1]
    card = chest.take_card()
    holder.add_card(card)

    events = install(game, "Paczka")
    revealed = [e for e in events if isinstance(e, ev.ChestCardsRevealed)]
    assert len(revealed) == 1
    holdings = revealed[0].holdings
    assert [h.player_index for h in holdings] == [holder.index]
    assert holdings[0].titles == [card.title]
    assert holdings[0].player_name == holder.name


def test_paczka_leaves_out_players_holding_nothing(library):
    """A list of empty names is noise, so they are not in it at all."""
    game = make(library)
    chest = game.decks[settings.DECK_CHEST]
    game.players[0].add_card(chest.take_card())

    events = install(game, "Paczka")
    revealed = next(e for e in events if isinstance(e, ev.ChestCardsRevealed))
    assert len(revealed.holdings) == 1
    assert revealed.holdings[0].player_index == 0


def test_paczka_says_so_when_nobody_holds_a_chest_card(library):
    """An empty list is what the window turns into its Polish sentence."""
    game = make(library)
    events = install(game, "Paczka")
    revealed = next(e for e in events if isinstance(e, ev.ChestCardsRevealed))
    assert revealed.holdings == []


def test_paczka_lists_every_card_a_player_holds(library):
    """Piotrek may hold two, and both belong in the window."""
    game = make(library)
    chest = game.decks[settings.DECK_CHEST]
    piotrek = next(p for p in game.players if p.is_piotrek)
    first, second = chest.take_card(), chest.take_card()
    piotrek.add_card(first)
    piotrek.add_card(second)

    events = install(game, "Paczka")
    revealed = next(e for e in events if isinstance(e, ev.ChestCardsRevealed))
    holding = next(h for h in revealed.holdings if h.player_index == piotrek.index)
    assert sorted(holding.titles) == sorted([first.title, second.title])


def test_paczka_changes_nothing_about_the_game(library):
    """Informational only: the board, the hands and the turn are untouched."""
    game = make(library)
    before = game.snapshot()
    install(game, "Paczka")
    after = game.snapshot()
    for key in ("board", "tokens", "players", "turn", "turn_slot", "round",
                "active_player", "eliminated"):
        assert before[key] == after[key]


def test_paczka_announces_once_and_not_every_round(library):
    """It arrives once; a round passing is not a second arrival."""
    game = make(library)
    game.players[0].add_card(game.decks[settings.DECK_CHEST].take_card())
    install(game, "Paczka")
    events = game._begin_round(game.round_number + 1)
    assert not [e for e in events if isinstance(e, ev.ChestCardsRevealed)]


# ── part 2: Squid Game ───────────────────────────────────────────────────────
def test_squid_game_declares_its_rule_in_the_data(library):
    game = make(library)
    assert not game.lead_check_only
    install(game, "Squid Game")
    assert game.lead_check_only


def test_stacking_every_pawn_no_longer_checks_anything(library):
    """The normal checking mechanic is DISABLED, not supplemented."""
    game = make(library)
    set_secret(game, pawns(game)[0])
    install(game, "Squid Game")
    for pawn_id in pawns(game):
        place(game, **{pawn_id: 4})
    # The whole table on one field with Piotrek at the bottom would normally be
    # an instant hunters' win.
    assert victory.gathering_tile(game) is not None
    assert victory.review(game) == []


def test_the_ordinary_check_comes_back_when_the_mod_leaves(library):
    game = make(library)
    set_secret(game, pawns(game)[0])
    install(game, "Squid Game")
    for pawn_id in pawns(game):
        place(game, **{pawn_id: 4})
    assert victory.review(game) == []

    game.apply(cmd.DiscardMod(slot=0))
    assert not game.lead_check_only
    followed = victory.review(game)
    assert followed and isinstance(followed[0], cmd.DeclareVictory)


def test_no_automatic_check_in_the_round_the_mod_arrives(library):
    """The first check falls on the NEXT round, not on this one."""
    game = make(library)
    spread(game)
    install(game, "Squid Game")
    assert game._arm_lead_check() == []
    assert game.pending_lead_check is None
    assert victory.review(game) == []


def test_the_automatic_check_fires_at_the_start_of_the_next_round(library):
    game = make(library)
    set_secret(game, pawns(game)[0])
    spread(game)
    install(game, "Squid Game")

    events = game._begin_round(game.round_number + 1)
    announced = [e for e in events if isinstance(e, ev.LeadCheckAnnounced)]
    assert len(announced) == 1 and not announced[0].skipped
    assert game.pending_lead_check == game.leading_pawn()


def test_the_automatic_check_repeats_every_following_round(library):
    game = make(library)
    set_secret(game, pawns(game)[0])
    install(game, "Squid Game")
    for extra in range(1, 4):
        spread(game, start=extra)
        game._begin_round(game.round_number + 1)
        assert game.pending_lead_check is not None, f"round {game.round_number}"
        game.pending_lead_check = None


def test_a_wrong_colour_is_crossed_off_the_notepad(library):
    game = make(library)
    leader = pawns(game)[-1]
    set_secret(game, pawns(game)[0])
    spread(game)
    install(game, "Squid Game")
    game._begin_round(game.round_number + 1)

    followed = victory.review(game)
    assert [type(c) for c in followed] == [cmd.EliminatePawn]
    assert followed[0].pawn_id == leader
    game.apply(followed[0], local=False)
    assert game.eliminated_pawns == [leader]


def test_finding_piotrek_at_the_front_wins_it_for_the_hunters(library):
    game = make(library)
    leader = pawns(game)[-1]
    set_secret(game, leader)
    spread(game)
    install(game, "Squid Game")
    game._begin_round(game.round_number + 1)

    followed = victory.review(game)
    assert [type(c) for c in followed] == [cmd.DeclareVictory]
    assert followed[0].outcome == victory.Outcome.HUNTERS.value
    game.apply(followed[0], local=False)
    assert game.victory is not None and not game.victory.piotrek_won


def test_a_shared_lead_skips_the_round(library):
    """Exactly one pawn must be in front, or nothing is checked at all."""
    game = make(library)
    set_secret(game, pawns(game)[0])
    spread(game)
    first, second = pawns(game)[0], pawns(game)[1]
    place(game, **{first: 9, second: 9})

    install(game, "Squid Game")
    events = game._begin_round(game.round_number + 1)
    announced = next(e for e in events if isinstance(e, ev.LeadCheckAnnounced))
    assert announced.skipped
    assert game.pending_lead_check is None
    assert victory.review(game) == []


def test_a_shared_lead_is_shared_even_when_it_is_a_tower(library):
    """Two pawns on one field are two pawns at the front, not one."""
    game = make(library)
    spread(game)
    place(game, **{pawns(game)[0]: 9, pawns(game)[1]: 9})
    assert game.leading_pawn() is None


def test_a_colour_is_never_checked_twice(library):
    game = make(library)
    leader = pawns(game)[-1]
    set_secret(game, pawns(game)[0])
    spread(game)
    install(game, "Squid Game")
    game.eliminated_pawns.append(leader)

    events = game._begin_round(game.round_number + 1)
    announced = next(e for e in events if isinstance(e, ev.LeadCheckAnnounced))
    assert announced.skipped and announced.pawn_id == leader
    assert game.pending_lead_check is None


def test_pawns_still_in_the_camp_cannot_lead(library):
    """Nobody has started, so there is no pawn out in front to check."""
    game = make(library)
    install(game, "Squid Game")
    assert game.leading_pawn() is None
    game._begin_round(game.round_number + 1)
    assert game.pending_lead_check is None


def test_a_replica_that_knows_no_secret_decides_nothing(library):
    """The client runs the identical code and must reach no verdict (N72)."""
    game = make(library)
    spread(game)
    install(game, "Squid Game")
    game._begin_round(game.round_number + 1)
    assert game.pending_lead_check is not None
    for player in game.players:
        player.secret_pawn = None
    assert victory.review(game) == []


def test_the_pending_check_is_in_the_snapshot(library):
    """Two machines disagreeing about who is checked is a desync worth seeing."""
    game = make(library)
    set_secret(game, pawns(game)[0])
    spread(game)
    install(game, "Squid Game")
    game._begin_round(game.round_number + 1)

    snapshot = game.snapshot()
    assert snapshot["pending_lead_check"] == game.pending_lead_check
    assert snapshot["armed_mods"]


def test_settling_the_check_clears_it(library):
    """Cleared by the COMMAND, so every replica clears it at the same point."""
    game = make(library)
    set_secret(game, pawns(game)[0])
    spread(game)
    install(game, "Squid Game")
    game._begin_round(game.round_number + 1)

    game.apply(victory.review(game)[0], local=False)
    assert game.pending_lead_check is None


def test_piotrek_can_still_win_by_reaching_the_finish(library):
    """Squid Game replaces CHECKING; it does not touch the other ending."""
    game = make(library)
    runner = pawns(game)[0]
    set_secret(game, runner)
    install(game, "Squid Game")
    place(game, **{runner: game.board.last_position})

    followed = victory.review(game)
    assert followed and followed[0].outcome == victory.Outcome.PIOTREK.value


# ── part 3: Shady ────────────────────────────────────────────────────────────
def test_shady_declares_its_rule_in_the_data(library):
    game = make(library)
    assert not game.hides_leader
    install(game, "Obóz Harcerski")
    assert game.hides_leader


def test_shady_removes_the_leading_pawn_at_once(library):
    game = make(library)
    spread(game)
    leader = pawns(game)[-1]

    events = install(game, "Obóz Harcerski")
    hidden = [e for e in events if isinstance(e, ev.PawnHidden)]
    assert len(hidden) == 1 and hidden[0].pawn_id == leader
    assert game.hidden_pawn_ids == (leader,)
    assert game.board.pawn_tile(leader) is None


def test_shady_takes_the_bottom_pawn_of_a_shared_field(library):
    """The brief's own example: pink standing on green removes GREEN."""
    game = make(library)
    spread(game)
    bottom, rider = pawns(game)[0], pawns(game)[1]
    place(game, **{bottom: 12})
    place(game, **{rider: 12})
    tile = game.board.pawn_tile(bottom)
    assert tile.stack == [bottom, rider]

    install(game, "Obóz Harcerski")
    assert game.hidden_pawn_ids == (bottom,)
    # The rider stays exactly where it was and simply settles onto the field.
    assert game.board.pawn_tile(rider) is tile
    assert tile.stack == [rider]


def test_a_hidden_pawn_is_ignored_by_hindmost_and_foremost(library):
    game = make(library)
    spread(game)
    leader = pawns(game)[-1]
    install(game, "Obóz Harcerski")
    assert effects.foremost_pawn(game) != leader
    assert effects.hindmost_pawn(game) != leader


def test_a_hidden_pawn_is_never_offered_as_a_target(library):
    game = make(library)
    spread(game)
    leader = pawns(game)[-1]
    install(game, "Obóz Harcerski")
    offered = [option.id for option in effects.pawn_options(game)]
    assert leader not in offered
    assert len(offered) == len(pawns(game)) - 1


def test_a_movement_card_aimed_at_a_hidden_pawn_resolves_and_does_nothing(library):
    """Discarded like a blocked move — NOT refused, or it would stay in hand."""
    game = make(library)
    spread(game)
    leader = pawns(game)[-1]
    install(game, "Obóz Harcerski")

    spec = effects.EffectSpec("move_pawn", {"target": "fixed", "pawn": leader,
                                            "steps": 1, "direction": "forward"})
    result = effects.resolve_spec(game, spec, 0)
    assert isinstance(result, effects.Plan) and result.ok
    assert all(isinstance(op, effects.Fizzle) for op in result.operations)
    assert game.board.pawn_tile(leader) is None


def test_a_hidden_pawn_is_ignored_by_the_neighbour_test(library):
    """It is off the map, so it cannot be somebody's neighbour (Halloween)."""
    game = make(library)
    lonely, leader = pawns(game)[0], pawns(game)[1]
    place(game, **{lonely: 4, leader: 5})
    assert effects.has_neighbour(game, lonely)
    install(game, "Obóz Harcerski")
    assert game.hidden_pawn_ids == (leader,)
    assert not effects.has_neighbour(game, lonely)


def test_checking_needs_only_the_pawns_that_are_left(library):
    """Shady's exception: five on one field is enough while one is away."""
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]
    for pawn_id in pawns(game):
        if pawn_id != hidden:
            place(game, **{pawn_id: 4})

    tile = victory.gathering_tile(game)
    assert tile is not None
    assert len(tile.stack) == len(pawns(game)) - 1


def test_the_full_table_is_required_again_once_the_pawn_is_back(library):
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]
    for pawn_id in pawns(game):
        if pawn_id != hidden:
            place(game, **{pawn_id: 4})

    game._begin_round(game.round_number + 1)
    assert not game.hidden_pawn_ids
    # The returning pawn lands on the rear pawn, which is in that same tower,
    # so the tower is complete again rather than one short.
    tile = victory.gathering_tile(game)
    assert tile is not None and len(tile.stack) == len(pawns(game))


def test_the_hidden_pawn_comes_back_a_round_later(library):
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]

    events = game._begin_round(game.round_number + 1)
    restored = [e for e in events if isinstance(e, ev.PawnRestored)]
    assert len(restored) == 1 and restored[0].pawn_id == hidden
    assert not game.hidden_pawn_ids
    assert game.board.pawn_tile(hidden) is not None


def test_it_comes_back_on_top_of_the_rearmost_pawn(library):
    game = make(library)
    spread(game, start=3)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]
    rear = effects.hindmost_pawn(game)

    game._begin_round(game.round_number + 1)
    tile = game.board.pawn_tile(hidden)
    assert tile is game.board.pawn_tile(rear)
    assert tile.stack[-1] == hidden, "it goes ON TOP"


def test_it_does_not_come_back_where_it_left(library):
    game = make(library)
    spread(game, start=3)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]
    where_it_was = 3 + len(pawns(game)) - 1

    game._begin_round(game.round_number + 1)
    assert game.board.position_of_pawn(hidden) != where_it_was


def test_shady_is_a_one_time_effect(library):
    """It stays in the rack afterwards and never hides anybody again."""
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    game._begin_round(game.round_number + 1)
    assert not game.hidden_pawn_ids

    for _ in range(3):
        events = game._begin_round(game.round_number + 1)
        assert not [e for e in events if isinstance(e, ev.PawnHidden)]
        assert not game.hidden_pawn_ids
    assert game.mod_slots[0].title == "Obóz Harcerski", "it is still on display"


def test_a_second_shady_hides_a_pawn_again(library):
    """A new card is a new arrival; only the same one is spent."""
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    game._begin_round(game.round_number + 1)

    events = install(game, "Obóz Harcerski", slot=1)
    assert [e for e in events if isinstance(e, ev.PawnHidden)]
    assert len(game.hidden_pawn_ids) == 1


def test_removing_shady_puts_the_pawn_back_at_once(library):
    """Nothing on the table keeps a pawn off the map, so it cannot be stranded."""
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]

    events = game.apply(cmd.DiscardMod(slot=0))
    assert [e for e in events if isinstance(e, ev.PawnRestored)]
    assert not game.hidden_pawn_ids
    assert game.board.pawn_tile(hidden) is not None


def test_a_hidden_pawn_is_a_status_so_it_survives_a_snapshot(library):
    """N18: the mechanic is a StatusKind, so serialisation comes for free."""
    game = make(library)
    spread(game)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]

    statuses = game.snapshot()["statuses"]
    entry = next(s for s in statuses if s["kind"] == StatusKind.HIDDEN.value)
    assert entry["subject_id"] == hidden
    assert entry["data"]["round"] == game.round_number


def test_the_carried_stack_is_recorded(library):
    """The brief asks for it stored, even though the riders stay behind."""
    game = make(library)
    spread(game)
    bottom, rider = pawns(game)[0], pawns(game)[1]
    place(game, **{bottom: 12})
    place(game, **{rider: 12})

    events = install(game, "Obóz Harcerski")
    hidden_event = next(e for e in events if isinstance(e, ev.PawnHidden))
    assert hidden_event.riders == [rider]


def test_a_hidden_pawn_cannot_be_the_leader_squid_game_checks(library):
    """The two mods coexist: Shady's pawn is not on the board to be checked."""
    game = make(library)
    set_secret(game, pawns(game)[0])
    spread(game)
    install(game, "Obóz Harcerski")
    hidden = game.hidden_pawn_ids[0]
    install(game, "Squid Game", slot=1)

    game._begin_round(game.round_number + 1)
    assert game.pending_lead_check != hidden


# ── the deck, and what the three of them cost it ─────────────────────────────
def test_every_declared_mod_rule_has_a_reader(library):
    """A passive nothing reads is a card that looks implemented and is not (N80)."""
    readers = {
        "reverse_backward": "reverses_backward_moves",
        "movement_cap": "movement_cap",
        "require_neighbour": "requires_neighbour",
        "abilities_locked": "abilities_locked",
        "reveal_chest": "chest_cards_revealed",
        "lead_check_only": "lead_check_only",
        "hide_leader": "hides_leader",
        # Stage 34/35: the two cards that declare rules through a VARIANT.
        "cancel_ability_effects": "cancels_ability_effects",
        "carry_neighbour": "carries_neighbour",
        "carry_neighbour_alone": "carries_neighbour_alone",
    }
    game = make(library)
    deck = next(d for d in library.decks.values() if d.id == settings.DECK_MODS)
    for card in deck.cards:
        # A VARIANT's passive counts too (stage 34).  A rule that only exists
        # on the second reading of a card is exactly as unimplemented as one on
        # the first if nothing reads it, and it is easier to miss.
        bags = [card.passive or {}] + [variant.passive or {}
                                       for variant in card.variants]
        for bag in bags:
            for key in bag:
                assert key in readers, (
                    f"{card.title} declares {key!r}, nothing reads it")
                assert hasattr(game, readers[key])
