"""
The last four movement cards, and the machinery they needed.

Troll, Stańczyk, Spy and Plagiat! were the four cards left without behaviour,
and between them they wanted four things the effect engine did not have:

* an effect that fires when a card ARRIVES in a hand rather than when it is
  played (``on_draw``);
* a card that may not be played or discarded by hand (``locked``), and one that
  may not be dealt at the start (``in_opening_hand``);
* a turn that something other than the player gets to spend
  (``StatusKind.TURN_INTERRUPT``, and ``SKIP_TURN`` finally being read);
* a decision with more than one answer, in order (``Choice.count`` /
  ``Choice.ordered``), and one whose answers are cards.

Everything here is engine-level and needs no display: the interface tests for
the same features are in ``test_ui.py``.
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
from pedzacy_piotrek.engine.statuses import Status, StatusKind


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make_game(library, seed: int = 7, players: int = 4):
    return create_game(
        SessionConfig(num_players=players, board_cells=30, seed=seed,
                      edit_mode=True, piotrek_picks_pawn=False),
        library,
    )


def take(state, deck_id: str, title: str):
    """Pull a named card out of wherever the shuffle happened to put it."""
    deck = state.decks[deck_id]
    for card in deck.draw_pile:
        if card.title == title:
            deck.draw_pile.remove(card)
            return card
    for player in state.players:
        for card in player.hand:
            if card.title == title:
                player.remove_card(card)
                return card
    raise AssertionError(f"{title} nie znaleziono")


def stack_on_top(state, card) -> None:
    """Make this the very next card off the pile."""
    state.decks[card.deck_id].draw_pile.append(card)


def play_a_turn(state):
    """Whatever the active player can legally do, so the turn passes on."""
    player = state.active_player
    playable = next(
        (c for c in player.hand
         if c.deck_id == settings.DECK_MOVEMENT and c.is_playable
         and getattr(effects.preview(state, c, player.index), "ok", False)),
        None,
    )
    if playable is not None:
        return state.apply(cmd.PlayCard(player_index=player.index,
                                        card_uid=playable.uid))
    return state.apply(cmd.EndTurn(player_index=player.index))


def turns_until(state, predicate, limit: int = 40):
    """Play on until something happens, collecting everything that did."""
    seen: list = []
    for _ in range(limit):
        seen.extend(play_a_turn(state))
        if predicate(seen):
            return seen
    return seen


def of_type(events, kind):
    return [e for e in events if isinstance(e, kind)]


# ── the data says what these cards are ───────────────────────────────────────
def test_the_four_cards_are_declared_rather_than_coded(library):
    """No handler is allowed to know a card by name; the JSON carries it all."""
    deck = library.deck(settings.DECK_MOVEMENT)
    cards = {c.title: c for c in deck.cards}

    assert cards["Troll"].on_draw is not None
    assert cards["Troll"].locked and not cards["Troll"].in_opening_hand
    assert cards["Stańczyk"].on_draw is not None
    assert cards["Stańczyk"].locked
    assert cards["Spy"].effect.type == "steal_card"
    assert cards["Plagiat!"].effect.type == "move_pawns"
    # Two pawns on the face, because it moves two.
    assert cards["Plagiat!"].badge.count == 2
    assert cards["Plagiat!"].badge.is_rainbow


def test_no_locked_card_can_be_dealt_at_the_start(library):
    """A locked card in an opening hand would be stuck there for the whole game.

    Locked cards are armed by ``on_draw``, and the opening deal does not run
    it — so one dealt at setup can never be played, never be discarded and
    never resolve; it just costs its owner a hand slot until the game ends.
    Stańczyk was declared ``locked`` without being withheld and did exactly
    that, so the rule is derived from ``locked`` rather than restated per card.
    """
    for deck in library.decks.values():
        for card in deck.cards:
            if card.locked:
                assert not card.opens_a_hand, card.title


def test_the_opening_deal_withholds_locked_cards(library):
    """And the dealer really honours it, at every seat, over many shuffles."""
    for seed in range(40):
        state = make_game(library, seed=1000 + seed)
        for player in state.players:
            assert all(c.opens_a_hand for c in player.hand), player.hand
        # Withheld cards go back into the draw pile, never onto the discard:
        # a game must not begin with cards already thrown away.
        assert not state.deck(settings.DECK_MOVEMENT).discard_pile


def test_every_declared_effect_has_a_handler(library):
    """The registry is the contract; a card naming a type nobody serves fails."""
    for deck in library.decks.values():
        for card in deck.cards:
            for spec in (card.effect, card.ability, card.on_draw):
                if spec is not None:
                    assert spec.type in effects.HANDLERS, (card.title, spec.type)


def test_an_interrupt_effect_also_has_a_handler(library):
    """The nested spec inside on_draw is resolved by the same registry."""
    deck = library.deck(settings.DECK_MOVEMENT)
    for card in deck.cards:
        if card.on_draw is None:
            continue
        interrupt = card.on_draw.get("interrupt")
        if interrupt:
            assert interrupt["type"] in effects.HANDLERS, card.title


# ── Troll ────────────────────────────────────────────────────────────────────
def test_nobody_ever_begins_the_game_holding_troll(library):
    """Sixty different shuffles, because this is a promise and not a tendency."""
    for seed in range(60):
        state = make_game(library, seed=seed)
        held = [c.title for p in state.players for c in p.hand]
        assert "Troll" not in held, f"seed {seed}"


def test_the_opening_deal_leaves_no_discard_pile_behind(library):
    """A withheld Troll goes back into the DRAW pile, not onto the discards.

    A game that started with cards already discarded would look exactly like a
    game that had been played before, which is what
    ``test_the_next_match_starts_from_nothing`` objects to.
    """
    for seed in range(20):
        state = make_game(library, seed=seed)
        assert state.decks[settings.DECK_MOVEMENT].discard_count == 0, seed


def test_troll_stays_in_hand_and_brings_a_replacement(library):
    state = make_game(library)
    troll = take(state, settings.DECK_MOVEMENT, "Troll")
    stack_on_top(state, troll)
    seat = state.active_player_index
    player = state.player(seat)
    before = len(player.hand)

    state.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_MOVEMENT))

    assert troll in player.hand, "Troll is not discarded on the way in"
    assert len(player.hand) == before + 2, "and it draws one card to replace itself"
    assert state.statuses.interrupts_for(seat), "the next turn is booked"


def test_troll_cannot_be_played_or_thrown_away(library):
    state = make_game(library)
    troll = take(state, settings.DECK_MOVEMENT, "Troll")
    seat = state.active_player_index
    state.player(seat).add_card(troll)

    played = state.apply(cmd.PlayCard(player_index=seat, card_uid=troll.uid))
    assert of_type(played, ev.ActionRejected)
    dropped = state.apply(cmd.DiscardCard(player_index=seat, card_uid=troll.uid))
    assert of_type(dropped, ev.ActionRejected)
    assert troll in state.player(seat).hand


def test_troll_forces_a_chest_card_when_the_player_has_one(library):
    state = make_game(library)
    troll = take(state, settings.DECK_MOVEMENT, "Troll")
    stack_on_top(state, troll)
    seat = state.active_player_index
    player = state.player(seat)
    state.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_MOVEMENT))

    chest = state.decks[settings.DECK_CHEST].take_card()
    player.hand.append(chest)

    seen = turns_until(state, lambda evs: of_type(evs, ev.CardSpotlighted))
    spotlight = of_type(seen, ev.CardSpotlighted)[0]

    assert spotlight.forced is True
    assert spotlight.card_uid == chest.uid, "a Chest card outranks a Movement card"
    assert spotlight.seconds >= 2.0, "and is held up long enough to be read"
    assert chest not in player.hand
    assert troll not in player.hand, "Troll discards itself once it has fired"
    assert not state.statuses.interrupts_for(seat)


def test_troll_falls_back_to_a_movement_card(library):
    """No Chest card in hand: it plays a Movement card instead."""
    state = make_game(library, seed=19)
    troll = take(state, settings.DECK_MOVEMENT, "Troll")
    stack_on_top(state, troll)
    seat = state.active_player_index
    player = state.player(seat)
    state.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_MOVEMENT))
    assert not [c for c in player.hand if c.deck_id == settings.DECK_CHEST]

    seen = turns_until(state, lambda evs: of_type(evs, ev.CardSpotlighted))
    spotlight = of_type(seen, ev.CardSpotlighted)[0]
    assert spotlight.deck_id == settings.DECK_MOVEMENT
    assert spotlight.card_uid != troll.uid, "and never Troll itself"


def test_a_forced_card_with_no_implementation_does_not_block_the_game(library):
    """A card whose effect does not exist yet is played, discarded, and done.

    Four Chest cards gained effects in stage 27, so the card is now CHOSEN for
    having none rather than taken off the top of the pile — the rule under test
    is about an unimplemented effect, and picking one at random would quietly
    stop testing it as the deck fills in.
    """
    state = make_game(library)
    troll = take(state, settings.DECK_MOVEMENT, "Troll")
    stack_on_top(state, troll)
    seat = state.active_player_index
    player = state.player(seat)
    state.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_MOVEMENT))
    deck = state.decks[settings.DECK_CHEST]
    chest = next(c for c in deck.draw_pile if c.effect is None)
    deck.draw_pile.remove(chest)
    player.hand.append(chest)

    seen = turns_until(state, lambda evs: of_type(evs, ev.CardSpotlighted))

    assert state.decks[settings.DECK_CHEST].find_discarded(chest.uid) is chest
    assert of_type(seen, ev.CardPlayed), "it counts as played"
    # And play carried on: somebody else is on turn and the table is not stuck.
    assert turns_until(state, lambda evs: of_type(evs, ev.ActivePlayerChanged), 4)


def test_two_trolls_queue_rather_than_replacing_each_other(library):
    state = make_game(library)
    seat = state.active_player_index
    for _ in range(2):
        troll = take(state, settings.DECK_MOVEMENT, "Troll")
        stack_on_top(state, troll)
        state.apply(cmd.DrawCard(player_index=seat,
                                 deck_id=settings.DECK_MOVEMENT))
    assert len(state.statuses.interrupts_for(seat)) == 2


# ── Stańczyk ─────────────────────────────────────────────────────────────────
def test_stanczyk_skips_the_next_turn_and_then_discards_itself(library):
    state = make_game(library, seed=11)
    card = take(state, settings.DECK_MOVEMENT, "Stańczyk")
    stack_on_top(state, card)
    seat = state.active_player_index
    player = state.player(seat)
    state.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_MOVEMENT))
    assert state.statuses.interrupts_for(seat)

    seen = turns_until(state, lambda evs: of_type(evs, ev.TurnSkipped))
    spotlight = of_type(seen, ev.CardSpotlighted)

    assert spotlight and spotlight[0].card_uid == card.uid
    assert spotlight[0].seconds == pytest.approx(2.0), "about two seconds"
    assert of_type(seen, ev.TurnSkipped)[0].player_index == seat
    assert card not in player.hand
    assert not state.statuses.interrupts_for(seat)
    assert len([c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT]) \
        == starting_hand_size(player), "the normal end-of-turn draw still happens"


def test_a_skip_turn_status_is_finally_enforced(library):
    """Lubin and Dziubdziuch granted this since stage 4 and nothing read it."""
    state = make_game(library, seed=3)
    victim = (state.active_player_index + 1) % len(state.players)
    state.statuses.add(Status.for_player(StatusKind.SKIP_TURN, victim,
                                         source="Lubin"))

    seen = turns_until(state, lambda evs: of_type(evs, ev.TurnSkipped), 12)

    skipped = of_type(seen, ev.TurnSkipped)
    assert skipped and skipped[0].player_index == victim
    assert skipped[0].source == "Lubin"
    assert not state.statuses.player_has(StatusKind.SKIP_TURN, victim), \
        "spent once, not for ever"


def test_a_turn_taken_over_still_ends_normally(library):
    """An interrupt IS a turn: it refills the hand and passes play on."""
    state = make_game(library, seed=11)
    card = take(state, settings.DECK_MOVEMENT, "Stańczyk")
    stack_on_top(state, card)
    seat = state.active_player_index
    state.apply(cmd.DrawCard(player_index=seat, deck_id=settings.DECK_MOVEMENT))
    turns_until(state, lambda evs: of_type(evs, ev.TurnSkipped))
    assert state.active_player_index != seat


# ── Spy ──────────────────────────────────────────────────────────────────────
def spy_into(state, seat: int):
    """Put Spy in a seat's hand IN PLACE of a card it already had.

    On top of the hand it would leave a card spare and the end-of-turn refill
    would have nothing to prove.
    """
    player = state.player(seat)
    spare = next(c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT)
    player.remove_card(spare)
    state.decks[settings.DECK_MOVEMENT].return_card(spare)
    spy = take(state, settings.DECK_MOVEMENT, "Spy")
    player.hand.append(spy)
    return spy


def test_spy_shows_a_hunter_only_piotreks_movement_cards(library):
    state = make_game(library, seed=21)
    piotrek = next(p for p in state.players if p.is_piotrek)
    hunter = next(p for p in state.players if not p.is_piotrek)
    state.active_player_index = hunter.index
    spy = spy_into(state, hunter.index)
    # Give Piotrek a Chest card, which must NOT appear in the list.
    chest = state.decks[settings.DECK_CHEST].take_card()
    piotrek.hand.append(chest)

    asked = of_type(
        state.apply(cmd.PlayCard(player_index=hunter.index, card_uid=spy.uid)),
        ev.ChoiceRequired,
    )[0]

    assert asked.kind == "card"
    assert asked.owner == piotrek.index, "a hunter always robs Piotrek"
    assert chest.uid not in asked.card_options, "Chest cards stay face down"
    assert set(asked.card_options) == {
        c.uid for c in piotrek.hand if c.deck_id == settings.DECK_MOVEMENT
    }


def test_spy_asks_piotrek_which_hunter_first(library):
    state = make_game(library, seed=21)
    piotrek = next(p for p in state.players if p.is_piotrek)
    state.active_player_index = piotrek.index
    spy = spy_into(state, piotrek.index)

    first = of_type(
        state.apply(cmd.PlayCard(player_index=piotrek.index, card_uid=spy.uid)),
        ev.ChoiceRequired,
    )[0]
    assert first.key == "victim" and first.kind == "option"

    victim_seat = int(first.options[0][0])
    second = of_type(
        state.apply(cmd.PlayCard(player_index=piotrek.index, card_uid=spy.uid,
                                 choices={"victim": str(victim_seat)})),
        ev.ChoiceRequired,
    )[0]
    assert second.kind == "card" and second.owner == victim_seat


def test_spy_moves_the_card_and_the_victim_redraws(library):
    state = make_game(library, seed=33)
    piotrek = next(p for p in state.players if p.is_piotrek)
    hunter = next(p for p in state.players if not p.is_piotrek)
    state.active_player_index = hunter.index
    spy = spy_into(state, hunter.index)
    wanted_thief = starting_hand_size(hunter)
    wanted_victim = starting_hand_size(piotrek)
    target = next(c for c in piotrek.hand
                  if c.deck_id == settings.DECK_MOVEMENT)

    events = state.apply(cmd.PlayCard(player_index=hunter.index,
                                      card_uid=spy.uid,
                                      choices={"stolen": str(target.uid)}))

    assert hunter.card_by_uid(target.uid) is target
    assert piotrek.card_by_uid(target.uid) is None
    # The thief gained a card, so the ordinary refill gives them nothing; the
    # victim lost one, so the effect hands one back.  Both end up correct
    # without either being a special case.
    assert len([c for c in hunter.hand
                if c.deck_id == settings.DECK_MOVEMENT]) == wanted_thief
    assert len([c for c in piotrek.hand
                if c.deck_id == settings.DECK_MOVEMENT]) == wanted_victim
    assert of_type(events, ev.CardStolen)


def test_spy_never_names_the_stolen_card_to_the_table(library):
    """CardStolen and CardPlayed are broadcast; the title is not theirs to give."""
    state = make_game(library, seed=33)
    piotrek = next(p for p in state.players if p.is_piotrek)
    hunter = next(p for p in state.players if not p.is_piotrek)
    state.active_player_index = hunter.index
    spy = spy_into(state, hunter.index)
    target = next(c for c in piotrek.hand
                  if c.deck_id == settings.DECK_MOVEMENT)

    events = state.apply(cmd.PlayCard(player_index=hunter.index,
                                      card_uid=spy.uid,
                                      choices={"stolen": str(target.uid)}))

    stolen = of_type(events, ev.CardStolen)[0]
    assert not hasattr(stolen, "title")
    for played in of_type(events, ev.CardPlayed):
        if played.card_uid == spy.uid:
            assert target.title not in played.description


def test_spy_refuses_when_there_is_nothing_to_take(library):
    state = make_game(library, seed=21)
    piotrek = next(p for p in state.players if p.is_piotrek)
    hunter = next(p for p in state.players if not p.is_piotrek)
    state.active_player_index = hunter.index
    spy = spy_into(state, hunter.index)
    for card in list(piotrek.hand):
        piotrek.remove_card(card)

    events = state.apply(cmd.PlayCard(player_index=hunter.index,
                                      card_uid=spy.uid))
    assert of_type(events, ev.ActionRejected)
    assert hunter.card_by_uid(spy.uid) is spy, "and the card is not spent"


# ── Plagiat! and the generic multi-pawn selection ────────────────────────────
def place(state, pawn_id: str, position: int) -> None:
    state.board.place_pawn(pawn_id,
                           state.board.position(position).tiles[0].index)
    state._sync_token_positions()


def plagiat_into(state, seat: int):
    card = take(state, settings.DECK_MOVEMENT, "Plagiat!")
    state.player(seat).hand.append(card)
    return card


def test_plagiat_asks_for_two_pawns_in_order(library):
    state = make_game(library, seed=5)
    seat = state.active_player_index
    card = plagiat_into(state, seat)

    asked = of_type(
        state.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid)),
        ev.ChoiceRequired,
    )[0]

    assert asked.kind == "pawn"
    assert asked.count == 2
    assert asked.ordered is True
    assert len(asked.pawns) == len(state.library.pawns)


def test_plagiat_moves_both_pawns_back_in_the_chosen_order(library):
    state = make_game(library, seed=5)
    seat = state.active_player_index
    card = plagiat_into(state, seat)
    green, pink = state.library.pawns[1].id, state.library.pawns[4].id
    place(state, green, 6)
    place(state, pink, 9)

    events = state.apply(cmd.PlayCard(
        player_index=seat, card_uid=card.uid,
        choices={"pawns": f"{green},{pink}"},
    ))

    walked = of_type(events, ev.TokenWalked)
    assert [w.pawn_id for w in walked] == [green, pink], "strictly in order"
    assert all(w.backward for w in walked)
    assert state.board.position_of_pawn(green) == 5
    assert state.board.position_of_pawn(pink) == 8


def test_the_second_pawn_leaves_from_where_the_first_one_put_it(library):
    """A rider carried back by the tower must not then move from its old field.

    This is the whole reason a multi-pawn move recomputes its route at
    execution time instead of planning both up front.
    """
    state = make_game(library, seed=5)
    seat = state.active_player_index
    card = plagiat_into(state, seat)
    bottom, top = state.library.pawns[0].id, state.library.pawns[1].id
    tile = state.board.position(7).tiles[0].index
    state.board.place_pawn(bottom, tile)
    state.board.place_pawn(top, tile)
    state._sync_token_positions()
    assert top in state.board.carried_pawns(bottom)

    state.apply(cmd.PlayCard(
        player_index=seat, card_uid=card.uid,
        choices={"pawns": f"{bottom},{top}"},
    ))

    # The tower carried the rider from 7 to 6, and then the rider moved on to 5.
    assert state.board.position_of_pawn(bottom) == 6
    assert state.board.position_of_pawn(top) == 5


def test_a_repeated_pawn_is_not_two_pawns(library):
    state = make_game(library, seed=5)
    seat = state.active_player_index
    card = plagiat_into(state, seat)
    green = state.library.pawns[1].id
    place(state, green, 6)

    events = state.apply(cmd.PlayCard(
        player_index=seat, card_uid=card.uid,
        choices={"pawns": f"{green},{green}"},
    ))
    assert of_type(events, ev.ChoiceRequired), "it asks again rather than obeying"
    assert state.board.position_of_pawn(green) == 6


def test_plagiat_refuses_a_pawn_that_cannot_go_back(library):
    state = make_game(library, seed=5)
    seat = state.active_player_index
    card = plagiat_into(state, seat)
    green, pink = state.library.pawns[1].id, state.library.pawns[4].id
    place(state, green, 0)          # already on the first field
    place(state, pink, 9)

    events = state.apply(cmd.PlayCard(
        player_index=seat, card_uid=card.uid,
        choices={"pawns": f"{green},{pink}"},
    ))
    assert of_type(events, ev.ActionRejected)
    assert state.board.position_of_pawn(pink) == 9, "and nothing moved at all"


def test_the_multi_pawn_system_is_not_written_for_plagiat(library):
    """Same handler, different numbers: three pawns, forwards, two fields."""
    state = make_game(library, seed=5)
    spec = effects.EffectSpec.from_dict({
        "type": "move_pawns", "count": 3, "steps": 2, "direction": "forward",
    })
    ids = [p.id for p in state.library.pawns[:3]]
    for offset, pawn_id in enumerate(ids):
        place(state, pawn_id, 4 + offset * 3)

    asked = effects.resolve_spec(state, spec, actor=0)
    assert isinstance(asked, effects.Choice) and asked.count == 3

    plan = effects.resolve_spec(state, spec, actor=0,
                                choices={"pawns": ",".join(ids)})
    assert isinstance(plan, effects.Plan)
    assert len(plan.operations) == 3
    assert all(op.steps == 2 for op in plan.operations)


# ── the answer format is a command field nobody had to add ───────────────────
def test_a_multi_answer_survives_a_trip_through_json(library):
    """It has to: it travels inside PlayCard.choices, which is Dict[str, str]."""
    green, pink = "zielony", "różowy"
    command = cmd.PlayCard(player_index=1, card_uid=42,
                           choices={"pawns": effects.join_ids([green, pink])})
    restored = cmd.Command.from_dict(command.to_dict())
    assert restored == command
    assert effects.split_ids(restored.choices["pawns"]) == [green, pink]
