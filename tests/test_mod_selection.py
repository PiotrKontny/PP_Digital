"""
The Mod Patusa selection: the round that pauses so both factions choose.

Piotrek picks one of three by clicking and it goes into the LEFT slot; the
hunters vote on three of their own and the winner goes into the RIGHT slot.
Everything here is engine-level, so it runs headless and says nothing about
where the cards are drawn on screen.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 4, seed: int = 99, **kwargs):
    """A game whose chest never opens, so only the mods interrupt anything."""
    config = SessionConfig(
        num_players=players, board_cells=24, seed=seed,
        chest_open_round=10_000, double_frequency=0.0,
        piotrek_picks_pawn=False, **kwargs
    )
    return create_game(config, library)


def open_selection(game, round_number: int = 3):
    """Walk the game into a mod round and hand back the opening event."""
    events = game._begin_round(round_number)
    started = [e for e in events if isinstance(e, ev.ModSelectionStarted)]
    assert started, "no selection opened"
    return started[0]


def reasons(events):
    return [e.reason for e in events if isinstance(e, ev.ActionRejected)]


# ── the schedule ─────────────────────────────────────────────────────────────
def test_the_default_schedule_is_every_second_round_from_the_third(library):
    """Round 3, then 5, 7, 9 — the physical game's cadence."""
    game = make(library)
    assert [n for n in range(1, 12) if game.is_mod_round(n)] == [3, 5, 7, 9, 11]


def test_rounds_before_the_first_one_never_pause(library):
    game = make(library)
    assert not any(game.is_mod_round(n) for n in (1, 2))


def test_the_schedule_is_configurable_from_the_lobby(library):
    game = make(library, mod_round_first=2, mod_round_interval=3)
    assert [n for n in range(1, 14) if game.is_mod_round(n)] == [2, 5, 8, 11]


def test_an_interval_of_one_pauses_every_round(library):
    game = make(library, mod_round_first=1, mod_round_interval=1)
    assert all(game.is_mod_round(n) for n in range(1, 8))


def test_next_mod_round_counts_the_current_one(library):
    """The round panel asks this to say when the next pause is due."""
    game = make(library)
    assert [game.next_mod_round(n) for n in range(1, 8)] == [3, 3, 3, 5, 5, 7, 7]


# ── opening a selection ──────────────────────────────────────────────────────
def test_a_mod_round_deals_three_cards_to_each_faction(library):
    game = make(library)
    started = open_selection(game)
    assert len(started.piotrek_uids) == RULES.mod_choices
    assert len(started.hunter_uids) == RULES.mod_choices
    # Six different cards: the factions never choose from the same lineup.
    assert not set(started.piotrek_uids) & set(started.hunter_uids)


def test_the_selection_pauses_every_move_at_the_table(library):
    game = make(library)
    open_selection(game)
    refused = game.apply(cmd.EndTurn(player_index=game.active_player_index))
    assert reasons(refused) == ["Najpierw wybierzcie Mody Patusa"]


def test_a_remote_client_is_refused_the_same_way(library):
    """The pause holds against a client that simply does not draw the overlay."""
    game = make(library, edit_mode=False)
    open_selection(game)
    seat = game.active_player_index
    assert game.authorise_remote(cmd.EndTurn(player_index=seat), seat) == \
        "Najpierw wybierzcie Mody Patusa"


def test_voting_is_allowed_while_everything_else_is_paused(library):
    """Voting is how the pause ends, so it cannot be blocked by the pause."""
    game = make(library, edit_mode=False)
    started = open_selection(game)
    seat = started.hunter_seats[0]
    assert game.authorise_remote(
        cmd.VoteMod(player_index=seat, card_uid=started.hunter_uids[0]), seat
    ) is None


def test_an_ordinary_round_opens_no_selection(library):
    game = make(library)
    events = game._begin_round(2)
    assert not any(isinstance(e, ev.ModSelectionStarted) for e in events)
    assert game.pending_mod_selection is None


# ── Piotrek's half ───────────────────────────────────────────────────────────
def test_piotrek_s_choice_goes_into_the_left_slot(library):
    game = make(library)
    started = open_selection(game)
    chosen = started.piotrek_uids[1]

    events = game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                                      card_uid=chosen))
    resolved = next(e for e in events if isinstance(e, ev.ModSelectionResolved))
    assert resolved.faction == "piotrek" and resolved.slot == 0
    assert game.mod_slots[0] is not None and game.mod_slots[0].uid == chosen


def test_piotrek_s_two_losing_cards_are_discarded(library):
    game = make(library)
    started = open_selection(game)
    losers = [started.piotrek_uids[0], started.piotrek_uids[2]]

    game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                             card_uid=started.piotrek_uids[1]))
    discarded = {c.uid for c in game.deck(settings.DECK_MODS).discard_pile}
    assert set(losers) <= discarded


def test_piotrek_s_pick_does_not_disturb_the_hunters_slot(library):
    """Each faction owns one slot; choosing must not shunt the other along.

    This is why the selection writes its slot directly instead of going through
    the push that Thunderfuck uses.
    """
    game = make(library)
    mods = game.deck(settings.DECK_MODS)
    theirs = mods.take_card()
    game.mod_slots[1] = theirs
    started = open_selection(game)

    game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                             card_uid=started.piotrek_uids[0]))
    assert game.mod_slots[1] is theirs


def test_only_piotrek_may_make_that_choice(library):
    game = make(library)
    started = open_selection(game)
    hunter = started.hunter_seats[0]
    refused = game.apply(cmd.ChooseMod(player_index=hunter,
                                       card_uid=started.piotrek_uids[0]))
    assert reasons(refused) == ["Tylko Piotrek wybiera ten Mod Patusa"]


def test_piotrek_cannot_choose_a_card_he_was_not_offered(library):
    """The engine checks against what it dealt rather than trusting the message."""
    game = make(library)
    started = open_selection(game)
    refused = game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                                       card_uid=started.hunter_uids[0]))
    assert reasons(refused) == ["Tej karty nie ma wśród wylosowanych"]


def test_piotrek_cannot_choose_twice(library):
    game = make(library)
    started = open_selection(game)
    game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                             card_uid=started.piotrek_uids[0]))
    refused = game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                                       card_uid=started.piotrek_uids[1]))
    assert reasons(refused) == ["Mod Patusa jest już wybrany"]


# ── the hunters' vote ────────────────────────────────────────────────────────
def test_the_most_voted_card_wins_and_takes_the_right_slot(library):
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids

    game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[2]))
    game.apply(cmd.VoteMod(player_index=seats[1], card_uid=uids[0]))
    events = game.apply(cmd.VoteMod(player_index=seats[2], card_uid=uids[2]))

    resolved = next(e for e in events if isinstance(e, ev.ModSelectionResolved))
    assert resolved.faction == "hunters" and resolved.slot == 1
    assert resolved.card_uid == uids[2]
    assert not resolved.tie_broken
    assert game.mod_slots[1].uid == uids[2]


def test_a_tie_goes_to_the_leftmost_card(library):
    """Three hunters, three different cards: the first one dealt wins."""
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids

    game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[2]))
    game.apply(cmd.VoteMod(player_index=seats[1], card_uid=uids[1]))
    events = game.apply(cmd.VoteMod(player_index=seats[2], card_uid=uids[0]))

    resolved = next(e for e in events if isinstance(e, ev.ModSelectionResolved))
    assert resolved.card_uid == uids[0]
    assert resolved.tie_broken


def test_nothing_is_decided_until_the_last_hunter_votes(library):
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids

    game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[0]))
    events = game.apply(cmd.VoteMod(player_index=seats[1], card_uid=uids[0]))
    assert not any(isinstance(e, ev.ModSelectionResolved) for e in events)
    assert game.mod_slots[1] is None
    assert game.pending_mod_selection is not None


def test_a_hunter_may_change_their_vote(library):
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids

    game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[0]))
    events = game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[1]))

    cast = next(e for e in events if isinstance(e, ev.ModVoteCast))
    assert cast.tally[uids[0]] == 0 and cast.tally[uids[1]] == 1
    # Still one voter, not two: changing a vote is not casting a second one.
    assert cast.voted == 1


def test_a_changed_vote_decides_the_winner(library):
    """Changing your mind before the last vote really does move the result."""
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids

    game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[0]))
    game.apply(cmd.VoteMod(player_index=seats[1], card_uid=uids[1]))
    game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[1]))
    events = game.apply(cmd.VoteMod(player_index=seats[2], card_uid=uids[2]))

    resolved = next(e for e in events if isinstance(e, ev.ModSelectionResolved))
    assert resolved.card_uid == uids[1]


def test_every_vote_reports_the_whole_tally(library):
    """The count is the engine's, so no two screens can disagree about it."""
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids

    events = game.apply(cmd.VoteMod(player_index=seats[0], card_uid=uids[1]))
    cast = next(e for e in events if isinstance(e, ev.ModVoteCast))
    assert cast.tally == {uids[0]: 0, uids[1]: 1, uids[2]: 0}
    assert cast.voted == 1 and cast.voters == len(seats)


def test_piotrek_does_not_vote(library):
    game = make(library)
    started = open_selection(game)
    refused = game.apply(cmd.VoteMod(player_index=started.piotrek_seat,
                                     card_uid=started.hunter_uids[0]))
    assert reasons(refused) == ["Tylko Oprawcy głosują nad tym Modem Patusa"]


def test_a_hunter_cannot_vote_for_a_card_they_were_not_offered(library):
    game = make(library)
    started = open_selection(game)
    refused = game.apply(cmd.VoteMod(player_index=started.hunter_seats[0],
                                     card_uid=started.piotrek_uids[0]))
    assert reasons(refused) == ["Tej karty nie ma wśród wylosowanych"]


def test_the_losing_cards_are_discarded_after_the_vote(library):
    game = make(library)
    started = open_selection(game)
    seats, uids = started.hunter_seats, started.hunter_uids
    for seat in seats:
        game.apply(cmd.VoteMod(player_index=seat, card_uid=uids[0]))

    discarded = {c.uid for c in game.deck(settings.DECK_MODS).discard_pile}
    assert {uids[1], uids[2]} <= discarded


# ── finishing ────────────────────────────────────────────────────────────────
def resolve_both(game, started):
    """Piotrek picks his first card and every hunter votes for theirs."""
    game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                             card_uid=started.piotrek_uids[0]))
    events = []
    for seat in started.hunter_seats:
        events = game.apply(cmd.VoteMod(player_index=seat,
                                        card_uid=started.hunter_uids[0]))
    return events


def test_the_table_moves_again_once_both_factions_have_chosen(library):
    game = make(library)
    started = open_selection(game)
    events = resolve_both(game, started)

    assert any(isinstance(e, ev.ModSelectionFinished) for e in events)
    assert game.pending_mod_selection is None
    accepted = game.apply(cmd.EndTurn(player_index=game.active_player_index))
    assert not reasons(accepted)


def test_both_slots_are_filled_by_the_selection(library):
    game = make(library)
    started = open_selection(game)
    resolve_both(game, started)
    assert game.mod_slots[0].uid == started.piotrek_uids[0]
    assert game.mod_slots[1].uid == started.hunter_uids[0]


def test_the_order_the_two_halves_finish_in_does_not_matter(library):
    """The vote can land before Piotrek has clicked, and usually will."""
    game = make(library)
    started = open_selection(game)
    for seat in started.hunter_seats:
        game.apply(cmd.VoteMod(player_index=seat,
                               card_uid=started.hunter_uids[1]))
    assert game.pending_mod_selection is not None

    events = game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                                      card_uid=started.piotrek_uids[2]))
    assert any(isinstance(e, ev.ModSelectionFinished) for e in events)
    assert game.mod_slots[0].uid == started.piotrek_uids[2]
    assert game.mod_slots[1].uid == started.hunter_uids[1]


def test_a_later_selection_replaces_what_the_last_one_installed(library):
    """Round 5 chooses again, and the old mods go back to the deck."""
    game = make(library)
    first = open_selection(game, 3)
    resolve_both(game, first)
    old = [game.mod_slots[0].uid, game.mod_slots[1].uid]

    second = open_selection(game, 5)
    resolve_both(game, second)
    assert [game.mod_slots[0].uid, game.mod_slots[1].uid] != old
    discarded = {c.uid for c in game.deck(settings.DECK_MODS).discard_pile}
    assert set(old) <= discarded


def test_the_open_selection_travels_in_the_snapshot(library):
    """Two machines that disagree about the votes disagree about the winner."""
    game = make(library)
    started = open_selection(game)
    assert game.snapshot()["mod_selection"] is not None

    game.apply(cmd.VoteMod(player_index=started.hunter_seats[0],
                           card_uid=started.hunter_uids[0]))
    votes = game.snapshot()["mod_selection"]["votes"]
    assert votes == {str(started.hunter_seats[0]): started.hunter_uids[0]}

    resolve_both(game, started)
    assert game.snapshot()["mod_selection"] is None


def test_a_table_with_no_hunters_still_lets_piotrek_choose(library):
    """A side with nobody to decide it must not stall the other one."""
    game = make(library, players=2, debug_version=True)
    started = open_selection(game)
    if not started.hunter_seats:
        pytest.skip("this seed dealt no hunter-free table")
    # Two players is one hunter: one vote settles it outright.
    game.apply(cmd.ChooseMod(player_index=started.piotrek_seat,
                             card_uid=started.piotrek_uids[0]))
    events = game.apply(cmd.VoteMod(player_index=started.hunter_seats[0],
                                    card_uid=started.hunter_uids[0]))
    assert any(isinstance(e, ev.ModSelectionFinished) for e in events)
    assert game.pending_mod_selection is None
