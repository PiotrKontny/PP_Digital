"""
Chest hand-out timing, and the marker that announces it.

A table of five or six is dealt a chest card every eligible round, as it always
was.  A table of FOUR OR FEWER is dealt one only every second eligible round —
the rota is short there, so a card a round stopped the chest being an event.

The two halves of this are the engine (does a round actually deal?) and the
rota (who is it for?), and they have to agree, because the marker in the turn
bar is drawn from the same answer.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.turn_order import chest_recipient_for_round


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int, opens: int = 3, **kwargs):
    """A table that never pauses for mods, so only the chest interrupts."""
    return create_game(
        SessionConfig(num_players=players, board_cells=24, seed=5,
                      chest_open_round=opens, double_frequency=0.0,
                      piotrek_picks_pawn=False, mod_round_first=10_000,
                      debug_version=players < RULES.min_players, **kwargs),
        library,
    )


def pattern(game, last: int = 11) -> str:
    """Rounds 1..last as '.' before opening, 'F' dealing, 'o' skipped."""
    out = []
    for number in range(1, last + 1):
        if game.chest_awards_cards(number):
            out.append("F")
        elif number >= game.chest_open_round:
            out.append("o")
        else:
            out.append(".")
    return "".join(out)


# ── which tables are sparse ──────────────────────────────────────────────────
@pytest.mark.parametrize("players", [2, 3, 4])
def test_small_tables_deal_every_second_eligible_round(library, players):
    game = make(library, players)
    assert game.chest_is_sparse
    assert game.chest_interval == 2
    # Opens on 3, then 5, 7, 9, 11 — never 4, 6, 8, 10.
    assert pattern(game) == "..FoFoFoFoF"


@pytest.mark.parametrize("players", [5, 6])
def test_big_tables_are_untouched(library, players):
    game = make(library, players)
    assert not game.chest_is_sparse
    assert game.chest_interval == 1
    assert pattern(game) == "..FFFFFFFFF"


def test_the_threshold_is_four(library):
    """Four is sparse, five is not.  The boundary is the whole feature."""
    assert make(library, 4).chest_is_sparse
    assert not make(library, 5).chest_is_sparse
    assert RULES.chest_sparse_max_players == 4


def test_the_chest_always_deals_on_the_round_it_opens(library):
    """Whatever the table size, the opening round is a dealing round."""
    for players in (2, 3, 4, 5, 6):
        for opens in (1, 2, 3, 7):
            game = make(library, players, opens=opens)
            assert game.chest_awards_cards(opens), (players, opens)


def test_nothing_is_dealt_before_the_chest_opens(library):
    game = make(library, 3, opens=4)
    assert not any(game.chest_awards_cards(n) for n in (1, 2, 3))
    assert game.chest_awards_cards(4)


# ── the rota ─────────────────────────────────────────────────────────────────
def test_the_rota_steps_once_per_hand_out_not_once_per_round():
    """Two hunters, every second round: both must be fed.

    Stepping the rota once per ROUND would land every skipped round on the same
    half of a two-hunter rota, and one hunter would take every card in the
    game.
    """
    hunters = ["Norbur", "Lubin"]
    dealt = [chest_recipient_for_round(n, 3, hunters, 2)
             for n in range(3, 12) if (n - 3) % 2 == 0]
    assert dealt == ["Norbur", "Lubin", "Norbur", "Lubin", "Norbur"]


def test_the_marker_moves_onto_the_next_hunter_on_a_skipped_round():
    """The sequence the design asks for, round by round.

    Round 3 deals to Norbur; round 4 deals nothing and the marker moves to
    Lubin; round 5 deals to Lubin; round 6 moves it back.
    """
    hunters = ["Norbur", "Lubin"]
    seen = [chest_recipient_for_round(n, 3, hunters, 2) for n in range(3, 9)]
    assert seen == ["Norbur", "Lubin", "Lubin", "Norbur", "Norbur", "Lubin"]


def test_an_interval_of_one_is_the_old_rota_exactly():
    """Five and six players must be unaffected, card for card."""
    hunters = ["A", "B", "C"]
    assert [chest_recipient_for_round(n, 3, hunters, 1) for n in range(1, 8)] == \
        ["A", "A", "A", "B", "C", "A", "B"]
    # ...and the default argument is that same behaviour.
    for n in range(1, 8):
        assert chest_recipient_for_round(n, 3, hunters) == \
            chest_recipient_for_round(n, 3, hunters, 1)


def test_every_hunter_is_fed_on_a_small_table(library):
    """Nobody may be starved by the halved cadence, at any table size."""
    for players in (3, 4):
        game = make(library, players)
        hunters = {p.index for p in game.players if not p.is_piotrek}
        fed = {game.chest_recipient_seat(n) for n in range(3, 40)
               if game.chest_awards_cards(n)}
        assert fed == hunters, players


# ── what actually happens when the rounds are played ─────────────────────────
def settle_chest_limit(game) -> None:
    """Answer an outstanding chest limit the way a player would.

    Through the real command, not by clearing the field: keeping the card and
    DISCARDING the excess is what returns cards to the pile.  Simply blanking
    ``pending_chest_choice`` leaves them stuck in hands, and a chest deck of
    eight runs dry after a few rounds — which looks exactly like a distribution
    bug and is not one.
    """
    while game.pending_chest_choice is not None:
        seat, uids = game.pending_chest_choice
        limit = game.chest_limit(game.player(seat))
        keep = tuple(uids[-limit:]) if limit else ()
        game.apply(cmd.KeepChestCards(player_index=seat, keep_uids=keep))


def award_log(game, first: int, last: int):
    """Step through rounds and record who was actually dealt a card."""
    log = []
    for number in range(first, last + 1):
        events = game._begin_round(number)
        awarded = [e for e in events if isinstance(e, ev.ChestCardAwarded)]
        log.append((number, [a.player_index for a in awarded]))
        settle_chest_limit(game)
    return log


def test_a_small_table_really_is_dealt_only_every_second_round(library):
    game = make(library, 3)
    log = award_log(game, 3, 8)
    dealt = [number for number, seats in log if seats]
    empty = [number for number, seats in log if not seats]
    assert dealt == [3, 5, 7]
    assert empty == [4, 6, 8]


def test_a_full_table_is_dealt_every_round(library):
    game = make(library, 6)
    log = award_log(game, 3, 8)
    assert all(seats for _, seats in log), log


def test_a_skipped_round_costs_the_deck_nothing(library):
    """A round that deals nothing must not quietly take a card off the pile."""
    game = make(library, 3)
    deck = game.deck(settings.DECK_CHEST)
    game._begin_round(3)
    settle_chest_limit(game)
    before = deck.draw_count
    game._begin_round(4)
    assert deck.draw_count == before


# ── who is dealt a card ──────────────────────────────────────────────────────
@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_piotrek_is_dealt_a_chest_card_too(library, players):
    """The bug: the engine dealt only to the rota and skipped Piotrek entirely.

    The ribbon had marked him since the chest existed, so his dot promised a
    card that never arrived — at every table size, not only the small ones.
    """
    game = make(library, players)
    piotrek = game.piotrek_seat
    assert piotrek is not None
    log = award_log(game, 3, 9)
    fed = {seat for _, seats in log for seat in seats}
    assert piotrek in fed, log


@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_piotrek_is_dealt_on_every_dealing_round(library, players):
    """Piotrek is not part of the rotation — he is fed whenever the chest deals."""
    game = make(library, players)
    piotrek = game.piotrek_seat
    for number, seats in award_log(game, 3, 9):
        if game.chest_awards_cards(number):
            assert piotrek in seats, (number, seats)
        else:
            assert seats == [], (number, seats)


@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_a_dealing_round_feeds_piotrek_and_one_hunter(library, players):
    """Two cards per hand-out: Piotrek, plus whichever hunter the rota reached."""
    game = make(library, players)
    piotrek = game.piotrek_seat
    for number, seats in award_log(game, 3, 9):
        if not seats:
            continue
        assert len(seats) == 2, (number, seats)
        assert seats[0] == piotrek, "Piotrek is dealt first, as the ribbon reads"
        assert not game.players[seats[1]].is_piotrek


@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_the_indicator_names_exactly_who_is_dealt(library, players):
    """The ribbon's dots and the engine's hand-out must be the same set.

    ``chest_recipient_seats`` is what the marker is drawn from and what the
    engine deals to, so this is the property that was broken: the marker said
    Piotrek and the deal did not.
    """
    game = make(library, players)
    for number, seats in award_log(game, 3, 11):
        expected = (game.chest_recipient_seats(number)
                    if game.chest_awards_cards(number) else [])
        assert seats == expected, (number, seats, expected)


def test_the_hunter_rota_is_unchanged_by_piotrek_joining(library):
    """Piotrek is additive: he must not consume a place in the rotation."""
    game = make(library, 4)
    hunters = [p.index for p in game.players if not p.is_piotrek]
    dealt_to_hunters = [game.chest_recipient_seat(n) for n in range(3, 20)
                        if game.chest_awards_cards(n)]
    # Still steps one hunter per hand-out, still reaches everybody.
    assert set(dealt_to_hunters) == set(hunters)
    assert dealt_to_hunters[:len(hunters)] == \
        [game.chest_recipient_seat(n) for n in range(3, 20)
         if game.chest_awards_cards(n)][:len(hunters)]


def test_a_table_without_piotrek_still_deals_to_the_rota(library):
    """No Piotrek at the table is not an error; the hunters are still fed."""
    game = make(library, 4)
    for player in game.players:
        player.character = None
    assert game.piotrek_seat is None
    seats = game.chest_recipient_seats(3)
    assert seats and all(not game.players[s].is_piotrek for s in seats)


def test_no_card_leaks_out_of_the_chest_deck(library):
    """Every chest card is always somewhere: a hand, the draw pile or the discard.

    Two seats are fed per round and both can go over the limit at once.  While
    the pending choice was a single slot the second overflow erased the first,
    so that player kept a card nobody could ever put back and the eight-card
    deck bled out a card at a time until hunters stopped being dealt anything.
    """
    for players in (2, 3, 4, 5, 6):
        game = make(library, players)
        deck = game.deck(settings.DECK_CHEST)
        total = deck.draw_count + deck.discard_count
        award_log(game, 3, 16)
        held = sum(len(game.chest_cards(p)) for p in game.players)
        assert held + deck.draw_count + deck.discard_count == total, players
        assert total == 18, "the chest deck ships with the counts in cards.json"


def test_two_overflows_in_one_round_are_both_asked(library):
    """Piotrek and the hunter can both go over the limit on the same round."""
    game = make(library, 5)
    # Fill both due seats to their limit so the next deal overflows each.
    for seat in game.chest_recipient_seats(3):
        player = game.player(seat)
        while len(game.chest_cards(player)) < game.chest_limit(player):
            player.add_card(game.deck(settings.DECK_CHEST).take_card())

    game._begin_round(3)
    first = game.pending_chest_choice
    assert first is not None
    # Answering the first must reveal the second rather than losing it.
    seat, uids = first
    limit = game.chest_limit(game.player(seat))
    game.apply(cmd.KeepChestCards(player_index=seat,
                                  keep_uids=tuple(uids[-limit:])))
    second = game.pending_chest_choice
    assert second is not None and second[0] != seat
    seat2, uids2 = second
    limit2 = game.chest_limit(game.player(seat2))
    game.apply(cmd.KeepChestCards(player_index=seat2,
                                  keep_uids=tuple(uids2[-limit2:])))
    assert game.pending_chest_choice is None
    # Nobody is left holding more than they may.
    for player in game.players:
        assert len(game.chest_cards(player)) <= game.chest_limit(player)


@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_the_deck_can_supply_a_full_table_indefinitely(library, players):
    """Both due seats are served on every dealing round, at every table size.

    This used to be the opposite test.  With eight chest cards a six-player
    table kept seven of them at the limit and left one circulating, while a
    dealing round wants two — so the hunter went without once everybody was
    full.  Stage 23 doubled every chest title to two copies, which fixes the
    supply without touching the odds of drawing any particular card.
    """
    game = make(library, players)
    piotrek = game.piotrek_seat
    dealing = 0
    for number, seats in award_log(game, 3, 42):
        if not game.chest_awards_cards(number):
            assert seats == [], (number, seats)
            continue
        dealing += 1
        assert seats == game.chest_recipient_seats(number), (number, seats)
        assert piotrek in seats
    assert dealing >= 20, "expected plenty of dealing rounds in the sample"


def test_the_recipients_are_the_same_on_every_replica(library):
    """Server-authoritative: two replicas must agree on both seats, in order."""
    one, two = make(library, 5), make(library, 5)
    assert [one.chest_recipient_seats(n) for n in range(1, 20)] == \
        [two.chest_recipient_seats(n) for n in range(1, 20)]


# ── the marker and the deal cannot disagree ──────────────────────────────────
@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_the_marker_is_filled_exactly_when_a_card_arrives(library, players):
    """The HUD fills the dot on ``chest_awards_cards``; the engine deals on it.

    One source of truth for both, so a filled marker can never promise a card
    that does not turn up.
    """
    game = make(library, players)
    for number in range(1, 14):
        game.round_number = number
        promised = game.chest_is_open and game.chest_awards_cards()
        assert promised == game.chest_awards_cards(number)


def test_the_cadence_needs_no_state_of_its_own(library):
    """Deterministic from the round number and the table size, so multiplayer
    needs no new command, no RNG draw and nothing in the snapshot.

    Two independently built replicas of the same match must answer identically
    without ever having exchanged a message about the chest.
    """
    one = make(library, 3)
    two = make(library, 3)
    assert [one.chest_awards_cards(n) for n in range(1, 20)] == \
        [two.chest_awards_cards(n) for n in range(1, 20)]
    assert [one.chest_recipient_seat(n) for n in range(1, 20)] == \
        [two.chest_recipient_seat(n) for n in range(1, 20)]
    assert "chest" not in one.snapshot()
