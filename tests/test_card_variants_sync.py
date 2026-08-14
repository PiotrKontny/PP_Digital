"""
Card variants over a network.

A variant is CONFIGURATION, so it has to travel the two roads configuration
travels and no third one: the lobby's settings message before the match, and
the ordinary command log during it.  Three things can go wrong here and nowhere
else, and each has a test below:

1. a table that agrees on the deck and disagrees on what one of its cards DOES
   — every count matches, every uid matches, and two machines are playing
   different rules;
2. a variant chosen in the lobby that never reaches the clients, so the host's
   match and everybody else's are built from different definitions;
3. a variant changed mid-match on one machine only, which is the same failure
   arriving later.

The fingerprint is what catches all three, which is why the chosen variants are
in the snapshot.
"""

from __future__ import annotations

import pytest

from netkit import Table, all_agree, take_a_turn

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.engine import commands as cmd

SESJA = "Sesja na PG"
VARIANT_1 = "lock"
VARIANT_2 = "lock_and_cancel"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library) -> Table:
    return Table(library)


def variants_of(service) -> str:
    return service.state.card_variant(settings.DECK_MODS, SESJA)


def copies(service):
    return [card for card in service.state.cards_of_deck(settings.DECK_MODS)
            if card.title == SESJA]


# ── the lobby's choice ───────────────────────────────────────────────────────
def test_the_lobby_broadcasts_the_chosen_variant(table, library):
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(card_variants={SESJA: VARIANT_2})
    table.pump()
    for service in (host, *clients):
        assert service.lobby_state.card_variants == {SESJA: VARIANT_2}


def test_every_machine_builds_the_match_on_that_variant(table, library):
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(card_variants={SESJA: VARIANT_2})
    table.pump()
    host.start_game(library)
    table.pump()
    table.choose_identity(host, clients, "")

    for service in (host, *clients):
        assert variants_of(service) == VARIANT_2
        assert copies(service)
        assert all(card.passive.get("cancel_ability_effects")
                   for card in copies(service))
    assert all_agree(host, *clients)


def test_a_lobby_that_says_nothing_gets_the_printed_card(table, library):
    """The default path, and the one every older client takes."""
    host, clients = table.playing("Kuba", "Ola", "Ala")
    for service in (host, *clients):
        assert variants_of(service) == VARIANT_1
        assert all(not card.passive.get("cancel_ability_effects")
                   for card in copies(service))


def test_only_the_host_sets_the_table_variant(table, library):
    """The existing host-only rule, applied to the new setting for free."""
    host, clients = table.seated("Kuba", "Ola", "Ala")
    clients[0].set_settings(card_variants={SESJA: VARIANT_2})
    table.pump()
    assert host.lobby_state.card_variants == {}
    host.set_settings(card_variants={SESJA: VARIANT_2})
    table.pump()
    assert host.lobby_state.card_variants == {SESJA: VARIANT_2}


def test_the_variant_setting_does_not_disturb_the_other_settings(table, library):
    """It is merged, not substituted — the whole reason the server merges."""
    host, clients = table.seated("Kuba", "Ola", "Ala")
    host.set_settings(mod_counts={SESJA: 3})
    table.pump()
    host.set_settings(card_variants={SESJA: VARIANT_2})
    table.pump()
    assert host.lobby_state.mod_counts[SESJA] == 3
    assert host.lobby_state.card_variants == {SESJA: VARIANT_2}


# ── changing it during the match ─────────────────────────────────────────────
def test_a_variant_change_reaches_every_machine(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    assert all(variants_of(s) == VARIANT_1 for s in (host, *clients))

    clients[0].session.submit(cmd.SetCardVariant(
        deck_id=settings.DECK_MODS, title=SESJA, variant=VARIANT_2))
    table.pump()

    for service in (host, *clients):
        assert variants_of(service) == VARIANT_2
        assert all(card.variant == VARIANT_2 for card in copies(service))
    assert all_agree(host, *clients)


def test_the_change_survives_the_turns_that_follow(table, library):
    host, clients = table.playing("Kuba", "Ola", "Ala")
    host.session.submit(cmd.SetCardVariant(
        deck_id=settings.DECK_MODS, title=SESJA, variant=VARIANT_2))
    table.pump()
    for _ in range(4):
        take_a_turn(table, host, clients)
    assert all(variants_of(s) == VARIANT_2 for s in (host, *clients))
    assert all_agree(host, *clients)


def test_any_seat_may_change_it_not_only_the_active_one(table, library):
    """Table bookkeeping, exactly as the library's other commands are."""
    host, clients = table.playing("Kuba", "Ola", "Ala")
    # Whoever is NOT holding the turn, so the command is issued by a machine
    # that may not play a card at this moment and must still be obeyed.
    active = host.state.active_player_index
    actor = next(s for s in (host, *clients) if s.state.local_seat != active)
    actor.session.submit(cmd.SetCardVariant(
        deck_id=settings.DECK_MODS, title=SESJA, variant=VARIANT_2))
    table.pump()
    assert all(variants_of(s) == VARIANT_2 for s in (host, *clients))


def test_a_rejoining_client_is_told_the_variant(table, library):
    """Requirement 15: a reconnecting client must not see the other card."""
    host, clients = table.playing("Kuba", "Ola", "Ala")
    host.session.submit(cmd.SetCardVariant(
        deck_id=settings.DECK_MODS, title=SESJA, variant=VARIANT_2))
    table.pump()

    room = table.room(host.room_code)
    assert (settings.DECK_MODS, SESJA, VARIANT_2) in \
        room.state.snapshot()["card_variants"]
    assert room.state.snapshot() == host.state.snapshot()
