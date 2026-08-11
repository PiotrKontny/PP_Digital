"""
The Card Library over a network.

The library is open DURING a match, so everything it changes has to survive the
trip every other action makes: client → server → authorisation → the log →
every replica.  The three things that can go wrong here and nowhere else:

1. a card added on one machine and not on another (a desync that only shows up
   several draws later, as a card nobody can account for);
2. a card added at a DIFFERENT POSITION in the pile on two machines, which is
   the same failure wearing a disguise — the counts agree and the next draw
   does not;
3. the ability actions being refused for anybody but the character's owner,
   which is what would happen if they had been written as ordinary
   seat-owned commands.
"""

from __future__ import annotations

import collections

import pytest

from netkit import Table, all_agree, snapshots, take_a_turn

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.engine import commands as cmd


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library) -> Table:
    return Table(library)


def composition(service, deck_id: str) -> dict:
    return service.state.deck_composition(deck_id)


def a_title(service, deck_id: str) -> str:
    return service.state.decks[deck_id].draw_pile[-1].title


def ability_title(service) -> str:
    for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
        for card in service.state.cards_of_deck(deck_id):
            if card.ability is not None and card.uses_total:
                return card.title
    raise AssertionError("no ability with charges in this content")


def test_a_deck_change_reaches_every_machine(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = a_title(host, settings.DECK_MOVEMENT)
    before = composition(host, settings.DECK_MOVEMENT)[title]

    host.session.submit(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title=title, delta=1))
    table.pump()

    for service in (host, *clients):
        assert composition(service, settings.DECK_MOVEMENT)[title] == before + 1
    assert all_agree(host, *clients)


def test_the_added_card_lands_in_the_same_place_on_every_machine(table, library):
    """Counts agreeing is not enough: the pile is an ORDER, and the next draw
    comes off the top of it.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = a_title(host, settings.DECK_CHEST)
    host.session.submit(cmd.AdjustDeckCount(deck_id=settings.DECK_CHEST,
                                            title=title, delta=1))
    table.pump()

    piles = [[card.uid for card in service.state.decks[settings.DECK_CHEST].draw_pile]
             for service in (host, *clients)]
    assert all(pile == piles[0] for pile in piles)
    titles = [[card.title for card in service.state.decks[settings.DECK_CHEST].draw_pile]
              for service in (host, *clients)]
    assert all(pile == titles[0] for pile in titles)


def test_a_client_may_change_a_deck_too(table, library):
    """Not a host-only setting any more: this is a table doing bookkeeping
    mid-match, and the lobby is long over.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = a_title(host, settings.DECK_MODS)
    before = composition(host, settings.DECK_MODS)[title]

    clients[-1].session.submit(cmd.AdjustDeckCount(deck_id=settings.DECK_MODS,
                                                   title=title, delta=1))
    table.pump()

    assert composition(host, settings.DECK_MODS)[title] == before + 1
    assert all_agree(host, *clients)


def test_a_deck_change_is_not_bound_to_whose_turn_it_is(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    seats = table.by_seat(host, clients)
    waiting = next(service for seat, service in seats.items()
                   if seat != host.state.active_player_index)
    title = a_title(host, settings.DECK_CHEST)
    before = composition(host, settings.DECK_CHEST)[title]

    waiting.session.submit(cmd.AdjustDeckCount(deck_id=settings.DECK_CHEST,
                                               title=title, delta=1))
    table.pump()

    assert composition(host, settings.DECK_CHEST)[title] == before + 1


def test_any_player_may_restore_any_characters_ability(table, library):
    """The requirement in one test: NOT restricted to the ability's owner.

    Every seat at the table restores the same ability in turn, including the
    seats holding neither that character nor the turn.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = ability_title(host)
    default = host.state.ability_default_uses(title)

    for service in (host, *clients):
        service.session.submit(cmd.AdjustAbilityUses(title=title, delta=-1))
        table.pump()
        assert host.state.ability_card(title).uses_left == default - 1

        service.session.submit(cmd.RestoreAbilityUses(title=title))
        table.pump()
        assert host.state.ability_card(title).uses_left == default
        assert all_agree(host, *clients)


def test_ability_uses_replicate_in_both_directions(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = ability_title(host)
    default = host.state.ability_default_uses(title)

    for _ in range(3):
        clients[0].session.submit(cmd.AdjustAbilityUses(title=title, delta=1))
    table.pump()

    for service in (host, *clients):
        assert service.state.ability_card(title).uses_left == default + 3
        # ...and the DEFAULT is untouched on every machine.
        assert service.state.ability_default_uses(title) == default
    assert all_agree(host, *clients)


def test_a_restore_after_a_reconnection_still_finds_the_default(table, library):
    """A replica rebuilt from seed + log has to agree about both numbers.

    This is the case the snapshot's ``ability_charges`` exists for: a
    reconnecting client replays the commands and must land on the same
    remaining uses AND the same default as everybody else.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = ability_title(host)
    default = host.state.ability_default_uses(title)
    for _ in range(2):
        host.session.submit(cmd.AdjustAbilityUses(title=title, delta=1))
    table.pump()

    # Corrupt one replica the way a real desync looks, then let it notice and
    # rebuild itself from seed + log — which is the road a reconnection takes.
    rejoin = clients[0]
    rejoin.state.round_number += 7
    resyncs_before = rejoin.stats.resyncs
    take_a_turn(table, host, clients)
    table.pump(12)

    assert rejoin.stats.resyncs > resyncs_before, "it noticed"
    assert rejoin.state.ability_card(title).uses_left == default + 2
    assert rejoin.state.ability_default_uses(title) == default
    assert all_agree(host, *clients)


def test_the_table_stays_in_step_through_a_turn_after_an_edit(table, library):
    """The point of the whole exercise: the game carries on afterwards."""
    host, clients = table.playing("Kuba", "Ola", "Ala")
    title = a_title(host, settings.DECK_MOVEMENT)
    host.session.submit(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title=title, delta=1))
    table.pump()
    for _ in range(4):
        take_a_turn(table, host, clients)
    assert all_agree(host, *clients)


def test_a_deck_change_never_touches_a_hand(table, library):
    """Nobody loses a card they are holding to somebody else's tidying up."""
    host, clients = table.playing("Kuba", "Ola", "Ala")
    hands = {p.index: [c.uid for c in p.hand] for p in host.state.players}
    for deck_id in settings.TABLE_DECKS:
        for title in list(composition(host, deck_id))[:3]:
            for delta in (1, -1, -1, -1):
                host.session.submit(cmd.AdjustDeckCount(
                    deck_id=deck_id, title=title, delta=delta))
            table.pump()

    assert {p.index: [c.uid for c in p.hand]
            for p in host.state.players} == hands
    assert all_agree(host, *clients)


# ══ stage 33: 'Dobierz kartę' over a network ═════════════════════════════════
def a_deck_title(service, deck_id: str) -> str:
    return service.state.decks[deck_id].draw_pile[-1].title


def hand_titles(service, seat: int) -> list:
    return [card.title for card in service.state.player(seat).hand]


def test_the_card_reaches_the_hand_of_the_player_who_asked(table, library):
    """The seat in the command is the seat that gets the card, on EVERY
    machine — not just on the one that clicked.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    asker = clients[1]
    seat = asker.session.seat
    title = a_deck_title(host, settings.DECK_MODS)
    before = len(host.state.player(seat).hand)

    asker.session.submit(cmd.DrawTitledCard(player_index=seat,
                                            deck_id=settings.DECK_MODS,
                                            title=title))
    table.pump()

    for service in (host, *clients):
        assert title in hand_titles(service, seat)
        assert len(service.state.player(seat).hand) == before + 1
    assert all_agree(host, *clients)


def test_nobody_elses_hand_changes(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    asker = clients[0]
    seat = asker.session.seat
    others = {p.index: [c.uid for c in p.hand]
              for p in host.state.players if p.index != seat}
    title = a_deck_title(host, settings.DECK_CHEST)

    asker.session.submit(cmd.DrawTitledCard(player_index=seat,
                                            deck_id=settings.DECK_CHEST,
                                            title=title))
    table.pump()

    assert {p.index: [c.uid for c in p.hand]
            for p in host.state.players if p.index != seat} == others


def test_the_live_deck_shrinks_on_every_machine(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    seat = host.session.seat
    title = a_deck_title(host, settings.DECK_MOVEMENT)
    piles = {s: s.state.decks[settings.DECK_MOVEMENT].draw_count
             for s in (host, *clients)}

    host.session.submit(cmd.DrawTitledCard(player_index=seat,
                                           deck_id=settings.DECK_MOVEMENT,
                                           title=title))
    table.pump()

    for service in (host, *clients):
        # Fewer than before — a card that draws a replacement takes two, which
        # is the card doing its job, so the assertion is "it went down".
        assert service.state.decks[settings.DECK_MOVEMENT].draw_count < piles[service]
    assert all_agree(host, *clients)


def test_a_seat_cannot_fetch_a_card_into_somebody_elses_hand(table, library):
    """The existing ``_OWNED_BY_PLAYER`` check, doing its job unchanged.

    'Dobierz kartę' is a convenience, not a way to stuff another player's hand.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    thief = clients[0]
    victim_seat = next(s for s in table.by_seat(host, clients)
                       if s != thief.session.seat)
    victim_hand = [c.uid for c in host.state.player(victim_seat).hand]
    title = a_deck_title(host, settings.DECK_MODS)

    thief.session.submit(cmd.DrawTitledCard(player_index=victim_seat,
                                            deck_id=settings.DECK_MODS,
                                            title=title))
    table.pump()

    assert [c.uid for c in host.state.player(victim_seat).hand] == victim_hand
    assert all_agree(host, *clients)


def test_a_missing_card_desynchronises_nothing(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    seat = host.session.seat
    hands = {p.index: [c.uid for c in p.hand] for p in host.state.players}

    host.session.submit(cmd.DrawTitledCard(player_index=seat,
                                           deck_id=settings.DECK_CHEST,
                                           title="Nie ma takiej karty"))
    table.pump()

    assert {p.index: [c.uid for c in p.hand]
            for p in host.state.players} == hands
    assert all_agree(host, *clients)


def test_the_table_plays_on_after_a_fetched_card(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    seat = host.session.seat
    host.session.submit(cmd.DrawTitledCard(
        player_index=seat, deck_id=settings.DECK_MODS,
        title=a_deck_title(host, settings.DECK_MODS)))
    table.pump()
    for _ in range(4):
        take_a_turn(table, host, clients)
    assert all_agree(host, *clients)
