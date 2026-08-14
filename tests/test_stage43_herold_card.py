"""
Stage 43 — Herold as a Chest card.

Herold WAS a character with a Messenger ability and a Mody Patusa card; it is
now one thing, a Chest card, and the effect it carries is the same one it
always carried.  So this file is mostly about two claims:

* Herold exists in exactly ONE deck, and nothing anywhere still treats it as a
  character or as a mod;
* the effect behaves exactly as it did, reached through the ordinary Chest card
  pipeline instead of through ``UseAbility``.
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
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import StatusKind

HEROLD = "Herold"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, **kwargs):
    kwargs.setdefault("double_frequency", 0.0)
    game = create_game(
        SessionConfig(num_players=6, board_cells=30, seed=4321,
                      chest_open_round=10_000, mod_round_first=10_000, **kwargs),
        library,
    )
    for index, pawn in enumerate(library.pawns):
        game.board.remove_pawn(pawn.id)
        game.board.place_pawn(pawn.id, game.board.positions[index + 1].tiles[0].index)
    game._sync_token_positions()
    return game


@pytest.fixture
def game(library):
    return make(library)


# ── fixture helpers ──────────────────────────────────────────────────────────
def hunters(game):
    return [p.index for p in game.players if not p.is_piotrek]


def piotrek_seat(game) -> int:
    return next(p.index for p in game.players if p.is_piotrek)


def give_character(game, seat: int, title: str):
    deck = game.deck(settings.DECK_CHARACTERS)
    card = deck.take_titled(title, include_discard=True)
    if card is None:
        for player in game.players:
            if player.character is not None and player.character.title == title:
                card, player.character = player.character, None
                break
    assert card is not None, title
    if game.players[seat].character is not None:
        deck.return_card(game.players[seat].character)
    game.players[seat].character = card
    return card


def deal_herold(game, seat: int):
    """Take the Herold Chest card out of the deck and into a hand."""
    deck = game.decks[settings.DECK_CHEST]
    card = next(c for c in deck.draw_pile if c.title == HEROLD)
    deck.draw_pile.remove(card)
    game.players[seat].add_card(card)
    return card


def arm(game, others=("Lubin", "Big D Randy")):
    """Give a hunter the card and some characters to borrow from."""
    seats = hunters(game)
    lent = [give_character(game, seats[i + 1], title)
            for i, title in enumerate(others)]
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    return seats[0], deal_herold(game, seats[0]), lent


def play(game, seat: int, card, **choices):
    return game.apply(cmd.PlayCard(player_index=seat, card_uid=card.uid,
                                   choices=choices))


# ═════════════════════════════════════════════════════════════════════════════
# Herold is a Chest card, and ONLY a Chest card
# ═════════════════════════════════════════════════════════════════════════════
def test_herold_is_in_the_chest_deck(library):
    card = next(c for c in library.deck(settings.DECK_CHEST).cards
                if c.title == HEROLD)
    assert card.count == 1
    assert card.effect is not None
    assert card.effect.type == effects.COPY_ABILITY


def test_herold_is_not_a_character_any_more(library):
    """The design decision, asserted rather than assumed."""
    titles = {c.title for c in library.deck(settings.DECK_CHARACTERS).cards}
    assert HEROLD not in titles
    skills = {c.skill for c in library.deck(settings.DECK_CHARACTERS).cards}
    assert "Messenger" not in skills


def test_herold_is_not_a_mod_any_more(library):
    titles = {c.title for c in library.deck(settings.DECK_MODS).cards}
    assert HEROLD not in titles


def test_herold_has_exactly_one_definition(library):
    """§6: no ambiguity about what Herold is."""
    homes = [deck_id for deck_id, deck in library.decks.items()
             if any(c.title == HEROLD for c in deck.cards)]
    assert homes == [settings.DECK_CHEST]


def test_no_card_anywhere_still_carries_a_copy_ABILITY(library):
    """The character carried it as an ``ability``; the card carries an ``effect``.

    A leftover ability-shaped Herold is exactly the "two competing
    implementations" the refactor was meant to prevent.
    """
    for deck in library.decks.values():
        for card in deck.cards:
            ability = getattr(card, "ability", None)
            assert ability is None or ability.type != effects.COPY_ABILITY, (
                f"{card.title} in {deck.id} still copies as an ability"
            )


def test_the_engine_has_no_herold_specific_command_left(library):
    """§2: no special Herold system — the Chest pipeline is the system."""
    assert not hasattr(cmd, "CopyAbility")
    assert not hasattr(ev, "AbilityCopyOpened")
    game = make(library)
    assert not hasattr(game, "pending_ability_copy")


# ═════════════════════════════════════════════════════════════════════════════
# The ordinary Chest card lifecycle
# ═════════════════════════════════════════════════════════════════════════════
def test_it_can_be_drawn_into_a_hand_and_looks_like_a_chest_card(game):
    seat, card, lent = arm(game)
    assert card.deck_id == settings.DECK_CHEST
    assert game.players[seat].card_by_uid(card.uid) is card


def test_playing_it_asks_which_ability_to_copy(game):
    seat, card, lent = arm(game)
    asked = next(e for e in play(game, seat, card)
                 if isinstance(e, ev.ChoiceRequired))
    assert asked.key == effects.COPY_CHOICE_KEY
    offered = {option[0] for option in asked.options}
    assert {"Lubin", "Big D Randy"} <= offered


def test_asking_does_not_spend_the_card(game):
    seat, card, lent = arm(game)
    play(game, seat, card)
    assert game.players[seat].card_by_uid(card.uid) is card, "still in hand"
    assert game.decks[settings.DECK_CHEST].find_discarded(card.uid) is None


def test_playing_it_runs_the_chosen_ability(game):
    seat, card, lent = arm(game)
    play(game, seat, card, ability="Lubin")
    assert game.statuses.player_has(StatusKind.SKIP_TURN, piotrek_seat(game))


def test_it_leaves_the_hand_and_reaches_the_discard_pile(game):
    """§4: the normal Chest card lifecycle, not a lifecycle of its own."""
    seat, card, lent = arm(game)
    events = play(game, seat, card, ability="Lubin")
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    assert game.players[seat].card_by_uid(card.uid) is None
    assert game.decks[settings.DECK_CHEST].find_discarded(card.uid) is card


def test_a_refused_copy_leaves_the_card_in_hand(game):
    """A refused card is not played, exactly as any other refused card."""
    seat, card, lent = arm(game)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()

    events = play(game, seat, card, ability="Lubin")
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.players[seat].card_by_uid(card.uid) is card
    assert not game.statuses.player_has(StatusKind.SKIP_TURN, piotrek_seat(game))


# ═════════════════════════════════════════════════════════════════════════════
# The effect itself — unchanged from the character version
# ═════════════════════════════════════════════════════════════════════════════
def test_the_players_own_character_is_not_offered(game):
    """PRESERVED behaviour, not a fresh decision — see ``copyable_abilities``."""
    seats = hunters(game)
    give_character(game, seats[0], "Mitoman")
    seat, card, lent = arm(game)
    asked = next(e for e in play(game, seat, card)
                 if isinstance(e, ev.ChoiceRequired))
    assert "Mitoman" not in {option[0] for option in asked.options}


def test_a_character_nobody_holds_is_not_offered(game):
    seat, card, lent = arm(game, others=("Lubin",))
    asked = next(e for e in play(game, seat, card)
                 if isinstance(e, ev.ChoiceRequired))
    held = {p.character.title for p in game.players
            if p.character is not None and p.index != seat}
    assert {option[0] for option in asked.options} <= held


def test_a_spent_ability_is_not_offered(game):
    seat, card, lent = arm(game)
    lent[0].uses_left = 0
    asked = next(e for e in play(game, seat, card)
                 if isinstance(e, ev.ChoiceRequired))
    assert lent[0].title not in {option[0] for option in asked.options}


def test_the_copy_keeps_the_originals_target_rule(game):
    """Jazdy names Piotrek because the SPEC does, not because Lubin held it."""
    seat, card, lent = arm(game)
    play(game, seat, card, ability="Lubin")
    assert game.statuses.player_has(StatusKind.SKIP_TURN, piotrek_seat(game))
    assert not game.statuses.player_has(StatusKind.SKIP_TURN, seat)


def test_the_original_keeps_its_use_by_default(game):
    seat, card, lent = arm(game)
    play(game, seat, card, ability="Lubin")
    assert lent[0].uses_left == 1


def test_a_configured_exception_spends_the_original(library):
    game = make(library)
    assert "Where are you Marcus?" in game.config.copy_consumes_use
    seats = hunters(game)
    glockboy = give_character(game, seats[1], "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])

    play(game, seats[0], card, ability="Glockboy", pawn="czerwony")
    assert game.pending_pawn_check is not None
    assert glockboy.uses_left == 0


def test_the_exception_list_is_configuration_not_a_rule(library):
    game = make(library, copy_consumes_use=("Jazdy",))
    seats = hunters(game)
    lubin = give_character(game, seats[1], "Lubin")
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])

    play(game, seats[0], card, ability="Lubin")
    assert lubin.uses_left == 0


def test_the_printed_ability_is_never_changed(library):
    game = make(library)
    seats = hunters(game)
    give_character(game, seats[1], "Glockboy")
    game.eliminated_pawns.extend(["zielony", "niebieski", "różowy"])
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])
    play(game, seats[0], card, ability="Glockboy", pawn="czerwony")

    printed = next(c for c in library.deck(settings.DECK_CHARACTERS).cards
                   if c.title == "Glockboy")
    assert printed.uses == 1


def test_the_global_start_rule_still_applies(game):
    """It used to come free from ``_use_ability``; a Chest card gets no such gift.

    What is borrowed IS a character ability, so the rule that governs abilities
    travels with the borrowing.
    """
    seat, card, lent = arm(game)
    game.board.remove_pawn("zielony")
    game._sync_token_positions()

    events = play(game, seat, card, ability="Lubin")
    rejected = next(e for e in events if isinstance(e, ev.ActionRejected))
    assert "starcie" in rejected.reason.lower()

    # The control: the SAME copy works once the camp is empty, so the refusal
    # above really was the START rule.
    game.board.place_pawn("zielony", game.board.positions[9].tiles[0].index)
    game._sync_token_positions()
    play(game, seat, card, ability="Lubin")
    assert game.statuses.player_has(StatusKind.SKIP_TURN, piotrek_seat(game))


def test_a_copied_abilitys_own_prerequisite_still_applies(game):
    seats = hunters(game)
    glockboy = give_character(game, seats[1], "Glockboy")
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])

    events = play(game, seats[0], card, ability="Glockboy", pawn="czerwony")
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert glockboy.uses_left == 1
    assert game.players[seats[0]].card_by_uid(card.uid) is card


def test_a_copied_ability_asks_its_own_question(game):
    seat, card, lent = arm(game)
    events = play(game, seat, card, ability="Big D Randy")
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert asked.key == "pawn", "the BORROWED ability's key"

    play(game, seat, card, ability="Big D Randy", pawn="czerwony")
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


def test_a_two_pawn_ability_keeps_both_steps(game):
    seat, card, lent = arm(game, others=("Ondrej",))
    events = play(game, seat, card, ability="Ondrej")
    asked = next(e for e in events if isinstance(e, ev.ChoiceRequired))
    assert asked.key == "pawns" and asked.count == 2 and asked.ordered

    play(game, seat, card, ability="Ondrej", pawns="czerwony,zielony")
    assert game.statuses.linked_partners("czerwony") == ["zielony"]


def test_the_copy_uses_the_variant_the_table_configured(library):
    game = make(library, card_variants={"Ondrej": "check_one"})
    seats = hunters(game)
    give_character(game, seats[1], "Ondrej")
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])

    play(game, seats[0], card, ability="Ondrej", pawns="czerwony,zielony")
    status = effects.link_status(game, "czerwony")
    assert status is not None
    assert status.data.get("check_together") is False


def test_the_copy_counts_as_an_ability_effect(library):
    """Sesja na PG variant 2 cancels effects whose ORIGIN is an ability.

    A borrowed freeze is a character's ability however it was reached, so it
    must be cancellable the same way — which is why the inner resolution keeps
    ``origin="ability"`` even though a card played it.
    """
    game = make(library, card_variants={"Sesja na PG": "lock_and_cancel"})
    seats = hunters(game)
    give_character(game, seats[1], "Big D Randy")
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])
    play(game, seats[0], card, ability="Big D Randy", pawn="czerwony")
    assert game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")

    deck = game.decks[settings.DECK_MODS]
    sesja = next(c for c in deck.draw_pile if c.title == "Sesja na PG")
    deck.draw_pile.remove(sesja)
    game.mod_slots[1] = sesja
    game._sync_mod_states()
    assert not game.statuses.pawn_has(StatusKind.FROZEN, "czerwony")


# ═════════════════════════════════════════════════════════════════════════════
# §4 / §18 — the LOBBY control, not just the config field
# ═════════════════════════════════════════════════════════════════════════════
def settings_panel(library):
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    panel = GameSettingsPanel(App(Layout(), headless=True, size=(1920, 1080)),
                              library)
    return panel, panel.tabs[panel._index_of("copy")]


def test_the_lobby_has_a_tab_for_the_copy_exceptions(library):
    """§4 is mandatory: the TABLE decides, so the table needs a control."""
    panel, tab = settings_panel(library)
    assert tab.label == HEROLD
    assert tab.titles, "one row per borrowable ability"


def test_the_rows_are_built_from_the_cards(library):
    """A tenth character appears here the day it is added, not when edited in."""
    panel, tab = settings_panel(library)
    printed = {(c.skill or c.title)
               for deck in (settings.DECK_CHARACTERS, settings.DECK_SKILLS)
               for c in library.deck(deck).cards
               if c.ability is not None
               and c.ability.type != effects.COPY_ABILITY}
    assert set(tab.titles) == printed


def test_no_copier_is_offered_as_a_row(library):
    """A copier is not borrowable, so there is nothing to decide about it.

    Nothing carries ``copy_ability`` as an ability any more — Herold is a card
    now — so this asserts the FILTER rather than a name, and stays true if a
    future character ever copies again.
    """
    panel, tab = settings_panel(library)
    for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
        for card in library.deck(deck_id).cards:
            ability = card.ability
            if ability is not None and ability.type == effects.COPY_ABILITY:
                assert (card.skill or card.title) not in tab.titles


def test_the_default_row_is_glockboy_and_only_glockboy(library):
    panel, tab = settings_panel(library)
    assert panel.copy_consumes_use == ("Where are you Marcus?",)
    assert tab.values["Where are you Marcus?"] == 1
    assert all(tab.values[title] == 0 for title in tab.titles
               if title != "Where are you Marcus?")


def test_the_default_matches_what_a_bare_config_would_do(library):
    """The lobby and the config must not disagree about the default."""
    panel, tab = settings_panel(library)
    assert panel.copy_consumes_use == SessionConfig().copy_consumes_use


def test_a_row_walks_both_ways_and_stops_at_both_ends(library):
    panel, tab = settings_panel(library)
    assert "Jazdy" not in panel.copy_consumes_use
    tab.bump("Jazdy", +1)
    assert "Jazdy" in panel.copy_consumes_use
    tab.bump("Jazdy", +1)
    assert "Jazdy" in panel.copy_consumes_use, "cannot go past the last answer"
    tab.bump("Jazdy", -1)
    assert "Jazdy" not in panel.copy_consumes_use
    tab.bump("Jazdy", -1)
    assert "Jazdy" not in panel.copy_consumes_use


def test_the_well_holds_the_short_answer_and_the_help_line_the_rest(library):
    """The stage-40a lesson: a sentence in the well spills over the buttons."""
    panel, tab = settings_panel(library)
    for index in (0, 1):
        tab.values["Jazdy"] = index
        assert tab.value_text("Jazdy") in ("Zachowuje", "Traci")
        description = tab.chosen("Jazdy")[2]
        assert description and description != tab.value_text("Jazdy")


def test_the_panels_choice_reaches_a_session_config(library):
    """End to end: click the row, start the match, play by that rule."""
    import pedzacy_piotrek.ui.menu as menu_module
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.layout import Layout

    app = App(Layout(), headless=True, size=(1920, 1080))
    started: list = []
    screen = menu_module.MenuScreen(app, library, started.append)
    app.push(screen)
    screen.update(1 / 60, (0, 0))
    panel = screen.settings_panel
    tab = panel.tabs[panel._index_of("copy")]
    tab.bump("Jazdy", +1)
    tab.bump("Where are you Marcus?", -1)

    captured = {}
    real = menu_module.SessionConfig

    class Spy(real):
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    menu_module.SessionConfig = Spy
    try:
        screen._start()
    finally:
        menu_module.SessionConfig = real
    assert started, "the match really was started"
    assert captured.get("copy_consumes_use") == ("Jazdy",)


def test_the_setting_survives_the_lobby_wire(library):
    """An online table must agree about it, so it travels in the lobby state."""
    from pedzacy_piotrek.net.lobby import LobbyState

    sent = LobbyState(copy_consumes_use=("Jazdy",))
    received = LobbyState.from_dict(sent.to_dict())
    assert received.copy_consumes_use == ("Jazdy",)
    assert received.to_config().copy_consumes_use == ("Jazdy",)


def test_an_unknown_skill_name_over_the_wire_is_harmless(library):
    """A name from another build matches nothing rather than crashing."""
    from pedzacy_piotrek.net.lobby import LobbyState

    received = LobbyState.from_dict({"copy_consumes_use": ["Nie ma takiej"]})
    game = make(library, copy_consumes_use=received.copy_consumes_use)
    seats = hunters(game)
    lubin = give_character(game, seats[1], "Lubin")
    game.apply(cmd.SetActivePlayer(player_index=seats[0]))
    card = deal_herold(game, seats[0])
    play(game, seats[0], card, ability="Lubin")
    assert lubin.uses_left == 1, "nothing matched, so nobody paid"
