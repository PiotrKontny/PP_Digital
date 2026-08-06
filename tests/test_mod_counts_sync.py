"""
The Mod Patusa deck composition, over a network.

The counts are a lobby setting, so they have to survive the same trip every
other setting does: host → server → every client, and then into a deck that
every machine builds identically from the seed.  A table that disagreed about
how many Speedruns are in the pile would disagree about which mod is drawn
next, which is a desync three rounds later and nowhere near the cause.
"""

from __future__ import annotations

import collections

import pytest

from netkit import Table, snapshots

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library) -> Table:
    return Table(library)


def mods_of(service) -> collections.Counter:
    deck = service.state.decks[settings.DECK_MODS]
    return collections.Counter(
        c.title for c in list(deck.draw_pile) + list(deck.discard_pile)
    )


def test_the_host_settings_reach_every_client(table, library):
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(mod_counts={"Speedrun": 4, "Shady": 0})
    table.pump()

    for service in (host, *clients):
        assert service.lobby_state.mod_counts["Speedrun"] == 4
        assert service.lobby_state.mod_counts["Shady"] == 0


def test_only_the_host_may_change_them(table, library):
    host, clients = table.seated("Kuba", "Ola")
    host.set_settings(mod_counts={"Speedrun": 4})
    table.pump()
    clients[0].set_settings(mod_counts={"Speedrun": 1})
    table.pump()

    assert host.lobby_state.mod_counts["Speedrun"] == 4


def test_every_machine_builds_the_same_deck(table, library):
    """Same titles, same copies, same order, same uids — everywhere."""
    host, clients = table.playing("Kuba", "Ola", "Ala")
    piles = [[c.title for c in s.state.decks[settings.DECK_MODS].draw_pile]
             for s in (host, *clients)]
    assert piles[1:] == piles[:-1], "the mods pile differs between machines"
    uids = [[c.uid for c in s.state.decks[settings.DECK_MODS].draw_pile]
            for s in (host, *clients)]
    assert uids[1:] == uids[:-1]


def test_a_resized_deck_replicates(table, library):
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(mod_counts={"Speedrun": 4, "Shady": 0, "AKO": 3})
    table.pump()
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients, "")

    for service in (host, *clients):
        counts = mods_of(service)
        assert counts["Speedrun"] == 4
        assert counts["AKO"] == 3
        assert "Shady" not in counts


def test_the_settings_do_not_split_the_fingerprint(table, library):
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(mod_counts={"Halloween": 3})
    table.pump()
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients, "")

    prints = snapshots(host, *clients)
    assert prints[1:] == prints[:-1]


def test_a_host_that_sends_nothing_still_gets_the_printed_deck(table, library):
    """The default path, and the one an older client takes.

    An empty mapping has to mean "as printed", not "no mods at all" — the
    lobby only ever sends the titles its panel knows about.
    """
    host, clients = table.playing("Kuba", "Ola", "Ala")
    assert sum(mods_of(host).values()) == 13


def test_the_server_clamps_what_it_is_told(table, library):
    """The server settles the settings; a hand-written message does not."""
    host, _ = table.seated("Kuba", "Ola")
    host.set_settings(mod_counts={"Speedrun": 999, "Shady": -5})
    table.pump()

    assert host.lobby_state.mod_counts["Speedrun"] == RULES.mod_count_max
    assert host.lobby_state.mod_counts["Shady"] == RULES.mod_count_min


def test_changing_one_title_does_not_wipe_the_others(table, library):
    """Settings arrive as partial updates and are merged, not replaced."""
    host, _ = table.seated("Kuba", "Ola")
    host.set_settings(mod_counts={"Speedrun": 4})
    table.pump()
    host.set_settings(mod_counts={"AKO": 3})
    table.pump()

    assert host.lobby_state.mod_counts == {"AKO": 3, "Speedrun": 4}
