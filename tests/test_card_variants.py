"""
Cards that can be played more than one way (stage 34).

The mechanism is deliberately small: a card declares ``variants`` in its JSON,
the match's configuration names one of them per title, and everything else —
the deck, the rack, the hand, the library and the wire — carries the card it
already carried.  These tests pin both halves of that: that a variant is
CONFIGURATION rather than a second card, and that ``Sesja na PG``'s two
readings actually behave differently.

Engine level throughout, so it runs headless.  The two interfaces that set a
variant (the lobby's settings panel and the Card Library) are at the bottom of
the file; the panel is exercised the way test_ui.py exercises it, without a
display.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.base_card import CardDef, CardVariant
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig, clean_card_variants
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.engine.statuses import Status, StatusKind, Subject
from pedzacy_piotrek.net.lobby import LobbyState

SESJA = "Sesja na PG"
VARIANT_1 = "lock"
VARIANT_2 = "lock_and_cancel"
GRANNY = "Granny Costume"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 4, seed: int = 7, **kwargs):
    """A game that never interrupts itself, so a test can pose a position."""
    kwargs.setdefault("double_frequency", 0.0)
    kwargs.setdefault("mod_round_first", 10_000)
    config = SessionConfig(
        num_players=players, board_cells=24, seed=seed,
        chest_open_round=10_000, piotrek_picks_pawn=False, **kwargs,
    )
    return create_game(config, library)


def install(game, title: str, slot: int = 0):
    """Put a named mod into the rack, the way a selection would."""
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[slot] = card
    # Through the same arrival path a real selection uses, so a mod that acts
    # the moment it lands does so here too.
    game._sync_mod_states()
    return card


def sesja_def(library) -> CardDef:
    return next(card for card in library.deck(settings.DECK_MODS).cards
                if card.title == SESJA)


def granny_seat(game) -> int:
    """The seat holding the character whose ability leaves a lasting effect."""
    return next(p.index for p in game.players
                if p.character is not None and p.character.skill == GRANNY)


def leave_start(game) -> None:
    """Walk every pawn out of the camp.

    No ability may be activated while a pawn is still on START, so a test that
    wants a REAL ability effect has to get the table moving first.
    """
    for index, pawn in enumerate(game.library.pawns):
        if game.board.position_of_pawn(pawn.id) is None:
            game.board.place_pawn(
                pawn.id, game.board.position(index + 4).tiles[0].index)


def freeze_a_pawn_with_granny(game) -> str:
    """Use Granny Costume for real, so the status is a genuine ability effect.

    Through ``apply`` rather than by attaching a status by hand: the whole
    question variant 2 asks is where an effect CAME FROM, and a test that
    stamped the answer itself would be testing its own fixture.
    """
    seat = granny_seat(game)
    game.active_player_index = seat
    pawn_id = game.library.pawns[0].id
    game.board.place_pawn(pawn_id, game.board.position(3).tiles[0].index)
    leave_start(game)
    events = game.apply(cmd.UseAbility(player_index=seat, source="character",
                                       choices={"pawn": pawn_id}))
    assert any(isinstance(e, ev.AbilityUsed) for e in events), events
    assert game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    return pawn_id


# ── part 1: the variant definitions ──────────────────────────────────────────
def test_sesja_na_pg_exposes_exactly_the_two_intended_variants(library):
    definition = sesja_def(library)
    assert definition.has_variants
    assert definition.variant_ids == (VARIANT_1, VARIANT_2)
    assert definition.default_variant == VARIANT_1


def test_both_variants_are_the_same_card(library):
    """The identity is the card's, not the variant's — the whole design."""
    printed = sesja_def(library)
    one = printed.with_variant(VARIANT_1)
    two = printed.with_variant(VARIANT_2)
    for reading in (one, two):
        assert reading.title == printed.title == SESJA
        assert reading.deck_id == printed.deck_id
        assert reading.count == printed.count == 2
        assert reading.art == printed.art
        assert reading.image == printed.image
        assert reading.variant_ids == printed.variant_ids


def test_the_two_variants_say_different_things(library):
    printed = sesja_def(library)
    one = printed.with_variant(VARIANT_1)
    two = printed.with_variant(VARIANT_2)
    assert one.text == "Umiejętności postaci nie mogą być używane"
    assert two.text == ("Umiejętności postaci nie mogą być używane, a wszystkie "
                        "obecnie działające efekty umiejętności są anulowane")
    assert one.text != two.text


def test_both_variants_keep_the_same_artwork(library):
    """Requirement 5: a variant changes the words, never the picture.

    Neither reading names artwork of its own, so both resolve to whatever the
    Signature Card system finds for the title — which is the same file.
    """
    printed = sesja_def(library)
    assert all(variant.__dict__.get("art") is None
               for variant in printed.variants)  # a variant has no art field
    assert (printed.with_variant(VARIANT_1).art
            == printed.with_variant(VARIANT_2).art
            == printed.art)


def test_a_variant_that_declares_nothing_inherits_the_card(library):
    """The smallest possible variant: an id and nothing else."""
    printed = CardDef(deck_id="mods", title="Próba", text="tekst",
                      passive={"a": 1},
                      variants=(CardVariant(id="x"), CardVariant(id="y")))
    assert printed.with_variant("y").text == "tekst"
    assert printed.with_variant("y").passive == {"a": 1}


def test_switching_back_restores_the_printed_wording(library):
    """Variants resolve against the PRINTED card, never against each other."""
    printed = sesja_def(library)
    round_trip = printed.with_variant(VARIANT_2).with_variant(VARIANT_1)
    assert round_trip.text == printed.with_variant(VARIANT_1).text
    assert round_trip.passive == printed.with_variant(VARIANT_1).passive


def test_an_unknown_variant_leaves_the_card_alone(library):
    printed = sesja_def(library)
    assert printed.with_variant("nie ma takiego") is printed


def test_an_ordinary_card_has_no_variants(library):
    """The control must not appear on the cards that have nothing to choose.

    Five cards declare variants now — Sesja na PG (stage 34), AKO (stage 35),
    the Chest card Nie masz Rosji (stage 36) and the CHARACTER cards Ondrej
    (stage 38, two variants differing only in the checking rule) and
    Dziubdziuch (stage 39, four variants crossing duration with card
    category).  Every other card in the game, in every deck, has none.
    """
    expected = {SESJA, "AKO", "Nie masz Rosji", "Ondrej", "Dziubdziuch"}
    with_variants = {card.title
                     for deck in library.decks.values()
                     for card in deck.cards if card.has_variants}
    assert with_variants == expected
    assert not any(card.has_variants
                   for card in library.deck(settings.DECK_MOVEMENT).cards)


# ── part 2: variant 1 — the behaviour that shipped ───────────────────────────
def test_the_default_variant_is_the_old_card(library):
    game = make(library)
    assert game.card_variant(settings.DECK_MODS, SESJA) == VARIANT_1
    card = install(game, SESJA)
    assert card.passive == {"abilities_locked": True}
    assert game.abilities_locked
    assert not game.cancels_ability_effects


def test_variant_1_locks_abilities(library):
    game = make(library)
    install(game, SESJA)
    seat = granny_seat(game)
    game.active_player_index = seat
    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not any(isinstance(e, ev.AbilityUsed) for e in events)


def test_variant_1_leaves_a_running_ability_effect_alone(library):
    """The distinction the second variant exists to draw."""
    game = make(library)
    pawn_id = freeze_a_pawn_with_granny(game)
    install(game, SESJA)
    assert game.abilities_locked
    assert game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)


# ── part 3: variant 2 — and what it does not cancel ──────────────────────────
def test_variant_2_also_locks_abilities(library):
    game = make(library, card_variants={SESJA: VARIANT_2})
    install(game, SESJA)
    assert game.abilities_locked
    seat = granny_seat(game)
    game.active_player_index = seat
    events = game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_variant_2_cancels_a_running_ability_effect_on_arrival(library):
    game = make(library, card_variants={SESJA: VARIANT_2})
    pawn_id = freeze_a_pawn_with_granny(game)
    install(game, SESJA)
    assert not game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)


def test_the_cancellation_is_announced(library):
    game = make(library, card_variants={SESJA: VARIANT_2})
    freeze_a_pawn_with_granny(game)
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == SESJA)
    deck.draw_pile.remove(card)
    game.mod_slots[0] = card
    events = game._sync_mod_states()
    assert any(isinstance(e, ev.StatusEnded) for e in events)


def test_variant_2_cancels_only_what_an_ability_started(library):
    """Requirement 11: not a 'clear everything'.

    Three statuses are in play and only one of them came from an ability: a
    Mod's own, a Chest card's promise and Granny Costume's freeze.  The mod
    arrives and exactly one of the three goes.
    """
    game = make(library, card_variants={SESJA: VARIANT_2})
    pawn_id = freeze_a_pawn_with_granny(game)
    other = game.library.pawns[1].id
    game.statuses.add(Status.for_pawn(StatusKind.FROZEN, other,
                                      source="Karta Skrzyni", origin="card"))
    game.statuses.add(Status.for_table(StatusKind.MOVEMENT_REVERSED,
                                       data={"round": game.round_number},
                                       source="Gambit Patusa", origin="card"))
    # A movement bonus nobody's ability granted — deliberately NOT a HIDDEN
    # status, which the rack owns and legitimately clears when no mod hides a
    # pawn: that would be the mod rack answering, not the variant.
    game.statuses.add(Status.for_player(StatusKind.MOVEMENT_BONUS, 1,
                                        data={"amount": 1},
                                        source="Karta Skrzyni", origin="card"))

    install(game, SESJA)

    assert not game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    assert game.statuses.pawn_has(StatusKind.FROZEN, other)
    assert game.statuses.of_kind(StatusKind.MOVEMENT_REVERSED)
    assert game.statuses.of_kind(StatusKind.MOVEMENT_BONUS)


def test_an_ability_effect_is_recognised_by_its_origin_not_its_name(library):
    """How an ability effect is identified, pinned on its own.

    ``source`` is a name for humans; ``origin`` is the machine's answer, and it
    is stamped where the effect is resolved rather than listed anywhere.
    """
    game = make(library)
    pawn_id = freeze_a_pawn_with_granny(game)
    status = game.statuses.find(StatusKind.FROZEN, Subject.PAWN, pawn_id)
    assert status is not None
    assert status.origin == "ability"
    assert status.source == GRANNY
    assert game.statuses.of_origin("ability") == [status]


def test_the_origin_survives_a_snapshot(library):
    game = make(library)
    freeze_a_pawn_with_granny(game)
    restored = [Status.from_dict(raw) for raw in game.statuses.to_list()]
    assert [s.origin for s in restored] == ["ability"]


# ── part 4: changing the variant during the match ────────────────────────────
def set_variant(game, variant: str, title: str = SESJA):
    return game.apply(cmd.SetCardVariant(deck_id=settings.DECK_MODS,
                                         title=title, variant=variant))


def test_the_variant_can_be_changed_through_a_command(library):
    game = make(library)
    events = set_variant(game, VARIANT_2)
    changed = [e for e in events if isinstance(e, ev.CardVariantChanged)]
    assert len(changed) == 1
    assert changed[0].variant == VARIANT_2
    assert game.card_variant(settings.DECK_MODS, SESJA) == VARIANT_2


def test_changing_the_variant_moves_every_copy_in_the_match(library):
    """Two physical copies, one logical card."""
    game = make(library)
    set_variant(game, VARIANT_2)
    copies = [card for card in game.cards_of_deck(settings.DECK_MODS)
              if card.title == SESJA]
    assert len(copies) == 2
    assert {card.variant for card in copies} == {VARIANT_2}
    assert {card.text for card in copies} == {
        sesja_def(library).with_variant(VARIANT_2).text}


def test_a_variant_change_does_not_edit_the_card_definition(library):
    """Requirement 2: the base definition is content and never moves."""
    game = make(library)
    set_variant(game, VARIANT_2)
    printed = sesja_def(library)
    assert printed.text == "Umiejętności postaci nie mogą być używane"
    assert printed.passive == {"abilities_locked": True}
    assert printed.variant == ""


def test_two_matches_can_play_different_variants_at_once(library):
    one = make(library)
    two = make(library, card_variants={SESJA: VARIANT_2})
    assert one.card_variant(settings.DECK_MODS, SESJA) == VARIANT_1
    assert two.card_variant(settings.DECK_MODS, SESJA) == VARIANT_2
    set_variant(one, VARIANT_2)
    assert two.card_variant(settings.DECK_MODS, SESJA) == VARIANT_2
    assert sesja_def(library).selected_variant == VARIANT_1


def test_switching_1_to_2_while_the_card_is_active_cancels(library):
    """Requirement 12: it must not need to be played again."""
    game = make(library)
    pawn_id = freeze_a_pawn_with_granny(game)
    install(game, SESJA)
    assert game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)

    events = set_variant(game, VARIANT_2)

    assert not game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    changed = next(e for e in events if isinstance(e, ev.CardVariantChanged))
    assert changed.cancelled == 1
    assert game.abilities_locked


def test_switching_2_to_1_does_not_bring_anything_back(library):
    """Requirement 13, in the middle of a match."""
    game = make(library, card_variants={SESJA: VARIANT_2})
    pawn_id = freeze_a_pawn_with_granny(game)
    install(game, SESJA)
    assert not game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    set_variant(game, VARIANT_1)
    assert not game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    assert game.abilities_locked


def test_switching_the_variant_of_a_card_nobody_is_playing_cancels_nothing(library):
    game = make(library)
    pawn_id = freeze_a_pawn_with_granny(game)
    set_variant(game, VARIANT_2)          # not in the rack
    assert game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)
    assert not game.abilities_locked


def test_the_lock_lifts_when_the_card_leaves_but_the_effect_stays_cancelled(library):
    """The worked example from the brief, end to end."""
    game = make(library, card_variants={SESJA: VARIANT_2})
    pawn_id = freeze_a_pawn_with_granny(game)
    install(game, SESJA)
    game.mod_slots[0] = None
    game._sync_mod_states()
    assert not game.abilities_locked
    assert not game.statuses.pawn_has(StatusKind.FROZEN, pawn_id)


def test_the_second_copy_is_not_a_second_card(library):
    """Requirement 14: two copies in the rack are still one logical card."""
    game = make(library, card_variants={SESJA: VARIANT_2})
    first = install(game, SESJA, slot=0)
    second = install(game, SESJA, slot=1)
    assert first is not second
    assert first.definition is second.definition
    assert first.variant == second.variant == VARIANT_2
    assert game.abilities_locked


def test_an_unknown_variant_is_refused(library):
    game = make(library)
    events = set_variant(game, "wariant_ktorego_nie_ma")
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.card_variant(settings.DECK_MODS, SESJA) == VARIANT_1


def test_a_card_without_variants_is_refused(library):
    game = make(library)
    events = game.apply(cmd.SetCardVariant(deck_id=settings.DECK_MODS,
                                           title="Halloween", variant="x"))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_setting_the_variant_it_already_has_changes_nothing(library):
    game = make(library)
    events = set_variant(game, VARIANT_1)
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert not any(isinstance(e, ev.CardVariantChanged) for e in events)


def test_the_command_survives_a_round_trip_through_json(library):
    command = cmd.SetCardVariant(deck_id=settings.DECK_MODS, title=SESJA,
                                 variant=VARIANT_2)
    assert cmd.Command.from_dict(command.to_dict()) == command


def test_the_command_belongs_to_no_seat_and_to_no_turn(library):
    """Table bookkeeping, exactly as the library's other commands are."""
    command = cmd.SetCardVariant()
    assert not hasattr(command, "player_index")
    from pedzacy_piotrek.engine.game_state import GameState
    assert not isinstance(command, GameState._OWNED_BY_PLAYER)
    assert not isinstance(command, GameState._TURN_BOUND)
    assert command not in cmd.AUTHORITY_ONLY


def test_any_seat_may_change_the_variant(library):
    """Not the active player's privilege — see the command's docstring."""
    game = make(library)
    game.active_player_index = 1
    game.config.edit_mode = False
    game.config.local_seat = 3
    events = set_variant(game, VARIANT_2)
    assert any(isinstance(e, ev.CardVariantChanged) for e in events)


# ── part 5: configuration, persistence and the wire ──────────────────────────
def test_the_lobby_carries_the_choice_into_the_match(library):
    lobby = LobbyState(card_variants={SESJA: VARIANT_2})
    config = lobby.to_config(seed=7)
    assert config.card_variants == {SESJA: VARIANT_2}


def test_the_choice_survives_the_lobby_snapshot(library):
    lobby = LobbyState(code="ABCDEF", card_variants={SESJA: VARIANT_2})
    mirrored = LobbyState.from_dict(lobby.to_dict())
    assert mirrored.card_variants == {SESJA: VARIANT_2}


def test_an_empty_mapping_means_the_printed_card(library):
    """An older client, or a table that never opened the panel."""
    assert (make(library).card_variant(settings.DECK_MODS, SESJA)
            == make(library, card_variants={}).card_variant(settings.DECK_MODS,
                                                            SESJA)
            == VARIANT_1)


def test_the_mapping_is_cleaned_and_sorted_like_the_count_maps(library):
    assert clean_card_variants({"B": "x", "A": "y"}) == {"A": "y", "B": "x"}
    assert list(clean_card_variants({"B": "x", "A": "y"})) == ["A", "B"]
    assert clean_card_variants(None) == {}
    assert clean_card_variants({"A": None}) == {}


def test_an_unknown_variant_in_the_config_falls_back_to_the_printed_one(library):
    game = make(library, card_variants={SESJA: "nonsense"})
    assert game.card_variant(settings.DECK_MODS, SESJA) == VARIANT_1


def test_the_deck_is_built_under_the_chosen_variant(library):
    game = make(library, card_variants={SESJA: VARIANT_2})
    copies = [c for c in game.decks[settings.DECK_MODS].draw_pile
              if c.title == SESJA]
    assert len(copies) == 2
    assert all(c.passive.get("cancel_ability_effects") for c in copies)


def test_a_variant_does_not_change_the_shuffle(library):
    """It cannot: no variant may touch a card's count, so the pile is the same.

    Worth pinning, because two machines building different piles from one seed
    is the failure mode every deck setting has to avoid.
    """
    printed = [c.title for c in make(library).decks[settings.DECK_MODS].draw_pile]
    chosen = [c.title for c in
              make(library, card_variants={SESJA: VARIANT_2})
              .decks[settings.DECK_MODS].draw_pile]
    assert printed == chosen


def test_the_variant_is_in_the_fingerprint(library):
    """A reconnecting client must not end up playing the other card."""
    one = make(library)
    two = make(library)
    assert one.snapshot()["card_variants"] == two.snapshot()["card_variants"]
    set_variant(two, VARIANT_2)
    assert one.snapshot()["card_variants"] != two.snapshot()["card_variants"]
    assert (settings.DECK_MODS, SESJA, VARIANT_2) in two.snapshot()["card_variants"]


def test_a_title_with_no_copies_in_the_match_still_has_a_setting(library):
    """The lobby may leave the card out entirely; the choice is still real."""
    game = make(library, mod_counts={SESJA: 0}, card_variants={SESJA: VARIANT_2})
    assert not [c for c in game.cards_of_deck(settings.DECK_MODS)
                if c.title == SESJA]
    assert game.card_variant(settings.DECK_MODS, SESJA) == VARIANT_2


def test_a_copy_added_from_the_library_arrives_on_the_chosen_variant(library):
    """The two ways of adding a card must not disagree about the rules."""
    game = make(library, card_variants={SESJA: VARIANT_2})
    game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MODS, title=SESJA,
                                   delta=1))
    copies = [c for c in game.cards_of_deck(settings.DECK_MODS)
              if c.title == SESJA]
    assert len(copies) == 3
    assert {c.variant for c in copies} == {VARIANT_2}


# ── part 6: what the interfaces show ─────────────────────────────────────────
def test_the_state_offers_the_definition_the_library_should_draw(library):
    game = make(library)
    shown = game.variant_definition(settings.DECK_MODS, SESJA)
    assert shown.text == sesja_def(library).with_variant(VARIANT_1).text
    set_variant(game, VARIANT_2)
    shown = game.variant_definition(settings.DECK_MODS, SESJA)
    assert shown.text == sesja_def(library).with_variant(VARIANT_2).text
    assert shown.title == SESJA
    assert shown.art == sesja_def(library).art


def test_a_card_without_variants_is_returned_unchanged(library):
    game = make(library)
    printed = next(c for c in library.deck(settings.DECK_MODS).cards
                   if c.title == "Halloween")
    assert game.variant_definition(settings.DECK_MODS, "Halloween") is printed


def test_the_settings_panel_offers_the_variants(library):
    """The lobby control, without a display: the tab is pure data."""
    tab = _variant_tab(library)
    # Every card that has variants, and only those — from every deck, which is
    # what puts a Chest card and a character in the same tab as two Mods.
    assert set(tab.titles) == {SESJA, "AKO", "Nie masz Rosji", "Ondrej",
                               "Dziubdziuch"}
    assert [choice[0] for choice in tab.choices_for(SESJA)] == [VARIANT_1,
                                                                VARIANT_2]
    # The descriptions are visible, which is the point of the control.
    assert all(choice[2] for choice in tab.choices_for(SESJA))
    assert tab.ids()[SESJA] == VARIANT_1
    tab.bump(SESJA, 1)
    assert tab.ids()[SESJA] == VARIANT_2
    assert tab.value_text(SESJA) == "Wariant 2"
    # ...and each row is independent of the others.
    assert tab.ids()["AKO"] == tab.choices_for("AKO")[0][0]
    tab.reset()
    assert tab.ids()[SESJA] == VARIANT_1


def test_the_panel_takes_a_configuration_back_in(library):
    tab = _variant_tab(library)
    tab.merge_ids({SESJA: VARIANT_2})
    assert tab.ids()[SESJA] == VARIANT_2
    tab.merge_ids({SESJA: "a variant that was renamed away"})
    assert tab.ids()[SESJA] == VARIANT_2


class _FakePanel:
    """Just enough of the panel for :meth:`_variant_tab` to build its rows."""

    def __init__(self, library: ContentLibrary) -> None:
        self.library = library


def _variant_tab(library):
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    return GameSettingsPanel._variant_tab(_FakePanel(library))


# ── part 7: the Card Library's control ───────────────────────────────────────
# Driven against SDL's dummy driver the way test_card_library.py drives it, so
# a control that stops being where the hit test looks fails here rather than in
# somebody's game.
import os                                                     # noqa: E402
import sys                                                    # noqa: E402
from pathlib import Path                                      # noqa: E402

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame                                                 # noqa: E402

from pedzacy_piotrek.net.session import LocalSession          # noqa: E402
from pedzacy_piotrek.ui.app import App                        # noqa: E402
from pedzacy_piotrek.ui.game_screen import GameScreen         # noqa: E402
from pedzacy_piotrek.ui.layout import Layout                  # noqa: E402

WINDOW = (1920, 1080)
#: The displays the project promises to work at.  The geometry test walks them.
REFERENCE_WINDOWS = [(1280, 760), (1920, 1080), (1920, 1200),
                     (2560, 1440), (3840, 2160)]


@pytest.fixture
def screen(library) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3,
                      seed=77),
        library,
    )
    game_screen = GameScreen(app, LocalSession(state))
    app.push(game_screen)
    return game_screen


def draw(screen: GameScreen, size=WINDOW) -> pygame.Surface:
    surface = pygame.Surface(size)
    screen.app.renderer.begin(surface)
    screen.update(0.016, (0, 0))
    screen.draw(surface)
    return surface


def click(screen: GameScreen, position) -> None:
    position = (int(position[0]), int(position[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=position),
        position,
    )
    screen.session.poll()


def open_mods_tab(screen: GameScreen):
    draw(screen)
    screen.card_library.open()
    screen.card_library.select_tab(settings.DECK_MODS)
    draw(screen)
    return screen.card_library


def entry_index(library_ui, title: str) -> int:
    return next(i for i, entry in enumerate(library_ui.entries)
                if entry.title == title)


def reveal(screen: GameScreen, ui, index: int):
    """Scroll until that entry is wholly inside the viewport, and lay out again.

    The library's hit test is ``_visible_indices``, so a control scrolled out
    of sight is out of REACH and not merely invisible — which is the intended
    behaviour and means a test that wants to click something has to bring it
    into view first, exactly as a player does.  A cell is never taller than the
    viewport at any of the reference resolutions, so this always succeeds.
    """
    layout = screen.app.layout
    cell = ui.cell_rect(index, layout)
    content = layout.card_library_content
    if cell.bottom > content.bottom:
        ui.scroll += cell.bottom - content.bottom
    elif cell.top < content.top:
        ui.scroll -= content.top - cell.top
    ui._clamp_scroll(layout)
    draw(screen)
    assert content.contains(ui.cell_rect(index, layout))
    return ui._rows_under_card(index, layout)


def test_the_library_shows_a_variant_control_only_where_there_is_a_choice(screen):
    ui = open_mods_tab(screen)
    layout = screen.app.layout
    sesja = entry_index(ui, SESJA)
    boxes = ui._rows_under_card(sesja, layout)
    assert "variant" in boxes
    assert ui.entries[sesja].card.has_variants

    plain = entry_index(ui, "Halloween")
    assert not ui.entries[plain].card.has_variants
    # The ROOM is reserved for every cell so the grid stays a grid; the button
    # itself is only drawn for a card that has variants (see _draw_deck_controls).


def test_a_tab_with_no_variant_cards_reserves_no_room(screen):
    ui = open_mods_tab(screen)
    layout = screen.app.layout
    assert ui.tab_has_variants
    with_room = layout.card_library_cell_size(False, True)[1]
    without = layout.card_library_cell_size(False, False)[1]
    assert with_room > without

    screen.card_library.select_tab(settings.DECK_MOVEMENT)
    draw(screen)
    assert not screen.card_library.tab_has_variants


def test_the_variant_control_never_covers_the_card(screen):
    """Requirement 4, and stage 31's rule: nothing goes on a card face."""
    ui = open_mods_tab(screen)
    layout = screen.app.layout
    for size in REFERENCE_WINDOWS:
        board = resized_screen(screen.state.library, size)
        ui2 = open_mods_tab(board)
        layout2 = board.app.layout
        index = entry_index(ui2, SESJA)
        card = ui2.card_rect(index, layout2)
        box = ui2._rows_under_card(index, layout2)["variant"]
        assert not box.colliderect(card), size
        assert box.top >= card.bottom, size
        cell = ui2.cell_rect(index, layout2)
        assert cell.contains(box), size


def resized_screen(library, size) -> GameScreen:
    app = App(Layout(), headless=True, size=size)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=5), library)
    game_screen = GameScreen(app, LocalSession(state))
    app.push(game_screen)
    return game_screen


def test_clicking_the_control_asks_the_engine_for_the_next_variant(screen):
    """It submits a Command; it does not reach into the state."""
    ui = open_mods_tab(screen)
    layout = screen.app.layout
    index = entry_index(ui, SESJA)
    box = reveal(screen, ui, index)["variant"]

    assert screen.state.card_variant(settings.DECK_MODS, SESJA) == VARIANT_1
    click(screen, box.center)
    assert screen.state.card_variant(settings.DECK_MODS, SESJA) == VARIANT_2
    # ...and it wraps round rather than stopping at the last one.
    click(screen, reveal(screen, ui, index)["variant"].center)
    assert screen.state.card_variant(settings.DECK_MODS, SESJA) == VARIANT_1


def test_the_library_draws_the_selected_variants_description(screen):
    """Requirement 6: the hover description is the one in force."""
    ui = open_mods_tab(screen)
    index = entry_index(ui, SESJA)
    printed = sesja_def(screen.state.library)
    assert ui.entries[index].card.text == printed.with_variant(VARIANT_1).text

    screen.session.submit(cmd.SetCardVariant(deck_id=settings.DECK_MODS,
                                             title=SESJA, variant=VARIANT_2))
    screen.session.poll()
    draw(screen)
    entry = ui.entries[entry_index(ui, SESJA)]
    assert entry.card.text == printed.with_variant(VARIANT_2).text
    assert entry.card.title == SESJA
    assert entry.card.art == printed.art


def test_the_caption_says_which_variant_is_in_force(screen):
    ui = open_mods_tab(screen)
    entry = ui.entries[entry_index(ui, SESJA)]
    assert ui._variant_caption(entry) == "WARIANT 1  (1/2)"
    screen.session.submit(cmd.SetCardVariant(deck_id=settings.DECK_MODS,
                                             title=SESJA, variant=VARIANT_2))
    screen.session.poll()
    draw(screen)
    entry = ui.entries[entry_index(ui, SESJA)]
    assert ui._variant_caption(entry) == "WARIANT 2  (2/2)"


def test_clicking_an_ordinary_cards_row_changes_nothing(screen):
    """The reserved room under a plain card is not a control."""
    ui = open_mods_tab(screen)
    layout = screen.app.layout
    index = entry_index(ui, "Halloween")
    box = reveal(screen, ui, index)["variant"]
    before = screen.state.snapshot()
    click(screen, box.center)
    assert screen.state.snapshot() == before


def test_the_other_library_controls_still_work(screen):
    """Regression: the variant row is added under them, not over them."""
    ui = open_mods_tab(screen)
    layout = screen.app.layout
    index = entry_index(ui, SESJA)
    boxes = reveal(screen, ui, index)
    before = screen.state.deck_card_count(settings.DECK_MODS, SESJA)

    stepper = ui._stepper(boxes["stepper"], layout)
    click(screen, stepper.rects["plus1"].center)
    assert screen.state.deck_card_count(settings.DECK_MODS,
                                        SESJA) == before + 1

    hand = len(screen.state.player(screen.view_seat).hand)
    click(screen, reveal(screen, ui, index)["draw"].center)
    assert len(screen.state.player(screen.view_seat).hand) == hand + 1
