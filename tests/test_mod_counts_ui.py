"""
The Mody Patusa deck panel, and the ability button under Sesja na PG.

The panel is the interface half of parts 1 and 2: eight `[-] count [+]` rows
that decide what goes into the mods deck.  It is an overlay rather than eight
more settings rows because both screens that host it lay themselves out by
shrinking their gaps until they fit, and neither has room — the tests at the
bottom of this file are the ones that would catch a regression back to rows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pygame
import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.menu import MenuScreen
from pedzacy_piotrek.ui.network_screens import HostSetupScreen


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    pygame.init()
    return ContentLibrary.load()


def click(screen, position) -> None:
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=position, button=1),
        position,
    )


def menu(library, size=(1600, 900)):
    app = App(Layout(), headless=True, size=size)
    captured: list = []
    screen = MenuScreen(app, library, captured.append)
    app.push(screen)
    screen.update(1 / 60, (0, 0))
    return screen, captured


def host_screen(library, size=(1600, 900)):
    app = App(Layout(), headless=True, size=size)
    screen = HostSetupScreen(app, library)
    app.push(screen)
    return screen


# ── the panel opens, counts, and closes ──────────────────────────────────────
def test_the_panel_starts_shut(library):
    screen, _ = menu(library)
    assert not screen.settings_panel.active


def test_the_button_opens_it(library):
    screen, _ = menu(library)
    click(screen, screen.mod_deck_button.rect.center)
    assert screen.settings_panel.active


def test_it_offers_every_mod_in_the_deck(library):
    """Read from the data file, so a new mod needs no code here."""
    screen, _ = menu(library)
    panel = screen.settings_panel
    assert panel.titles == [c.title for c in
                            library.deck(settings.DECK_MODS).cards]
    # One stepper per VISIBLE row since stage 26, not one per title: the
    # movement tab is thirty titles long and the list scrolls.  Eight mods fit
    # in one screenful, so every mod still has a row of its own.
    assert len(panel.visible_titles) == len(panel.titles)
    assert len(panel.steppers) >= len(panel.titles)


def test_the_defaults_are_the_printed_deck(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    assert panel.counts == {
        "Speedrun": 2, "Masa solna": 2, "AKO": 1, "Halloween": 1,
        "Sesja na PG": 2, "Paczka": 2, "Squid Game": 1, "Shady": 2,
    }
    assert panel.total == 13
    assert panel.is_default


def test_the_steppers_change_one_card_each(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    click(screen, screen.mod_deck_button.rect.center)

    index = panel.titles.index("Speedrun")
    plus = panel.steppers[index].rects["plus1"].center
    click(screen, plus)
    assert panel.counts["Speedrun"] == 3
    assert panel.counts["Masa solna"] == 2, "a neighbour moved too"

    minus = panel.steppers[index].rects["minus1"].center
    for _ in range(2):
        click(screen, minus)
    assert panel.counts["Speedrun"] == 1


def test_a_count_cannot_go_below_zero_or_past_the_ceiling(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    click(screen, screen.mod_deck_button.rect.center)
    index = panel.titles.index("AKO")

    minus = panel.steppers[index].rects["minus1"].center
    for _ in range(6):
        click(screen, minus)
    assert panel.counts["AKO"] == RULES.mod_count_min

    plus = panel.steppers[index].rects["plus1"].center
    for _ in range(RULES.mod_count_max + 4):
        click(screen, plus)
    assert panel.counts["AKO"] == RULES.mod_count_max


def test_zero_is_allowed_because_leaving_a_mod_out_is_a_real_choice(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    panel.counts["Shady"] = 0
    assert panel.total == 11


def test_the_reset_button_restores_the_printed_deck(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    click(screen, screen.mod_deck_button.rect.center)
    panel.counts["Speedrun"] = 7
    click(screen, panel.reset_button.rect.center)
    assert panel.is_default


def test_done_and_escape_both_close_it(library):
    screen, _ = menu(library)
    panel = screen.settings_panel

    click(screen, screen.mod_deck_button.rect.center)
    click(screen, panel.close_button.rect.center)
    assert not panel.active

    click(screen, screen.mod_deck_button.rect.center)
    screen.handle_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), (0, 0))
    assert not panel.active


def test_clicking_away_closes_it(library):
    screen, _ = menu(library)
    panel = screen.settings_panel
    click(screen, screen.mod_deck_button.rect.center)
    click(screen, (4, 4))
    assert not panel.active


def test_it_warns_about_a_deck_too_small_to_choose_from(library):
    """A selection deals three cards to each of two factions."""
    screen, _ = menu(library)
    panel = screen.settings_panel
    assert panel.warning == ""
    for title in panel.titles:
        panel.counts[title] = 0
    assert "pusta" in panel.warning
    panel.counts["AKO"] = 2
    assert panel.warning, "two cards cannot fill two sets of three"
    for title in panel.titles:
        panel.counts[title] = 1
    assert panel.warning == "", "eight is enough for six"


# ── the panel is modal ───────────────────────────────────────────────────────
def test_an_open_panel_swallows_clicks_meant_for_the_screen_beneath(library):
    """It covers the steppers underneath, so nothing may fall through."""
    screen, _ = menu(library)
    click(screen, screen.mod_deck_button.rect.center)
    before = screen.num_players

    click(screen, screen.players_stepper.rects["minus1"].center)
    assert screen.num_players == before

    # ...and works again once the panel is shut.
    click(screen, screen.settings_panel.close_button.rect.center)
    click(screen, screen.players_stepper.rects["minus1"].center)
    assert screen.num_players == before - 1


# ── the counts reach the game ────────────────────────────────────────────────
def test_the_counts_travel_with_the_session_config(library):
    screen, captured = menu(library)
    click(screen, screen.mod_deck_button.rect.center)
    index = screen.settings_panel.titles.index("Halloween")
    click(screen, screen.settings_panel.steppers[index].rects["plus1"].center)
    click(screen, screen.settings_panel.close_button.rect.center)
    click(screen, screen.start_button.rect.center)

    assert captured, "the game never started"
    assert captured[0].mod_counts["Halloween"] == 2


def test_the_config_builds_the_deck_it_describes(library):
    """The panel to the pile, in one step, the way the menu does it."""
    screen, captured = menu(library)
    screen.settings_panel.counts["Speedrun"] = 5
    click(screen, screen.start_button.rect.center)
    config = captured[0]

    game = create_game(
        SessionConfig(num_players=4, seed=3, mod_round_first=10_000,
                      mod_counts=config.mod_counts),
        library,
    )
    titles = [c.title for c in game.decks[settings.DECK_MODS].draw_pile]
    assert titles.count("Speedrun") == 5


# ── the host screen offers exactly the same thing ────────────────────────────
def test_the_host_screen_has_the_same_panel(library):
    screen = host_screen(library)
    assert screen.settings_panel.titles == [
        c.title for c in library.deck(settings.DECK_MODS).cards]
    assert screen.settings_panel.is_default


def test_the_host_button_opens_the_panel(library):
    screen = host_screen(library)
    screen.handle_click(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                           pos=screen.mod_deck_button.rect.center, button=1),
        screen.mod_deck_button.rect.center,
    )
    assert screen.settings_panel.active


def test_the_open_host_panel_is_modal_too(library):
    screen = host_screen(library)
    screen.settings_panel.open()
    before = screen.board_cells
    position = screen.cells_stepper.rects["minus1"].center
    screen.handle_click(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=position, button=1),
        position,
    )
    assert screen.board_cells == before


# ── nothing the panel does may disturb the layout underneath ─────────────────
@pytest.mark.parametrize("size", [(1280, 760), (1600, 900), (1920, 1080),
                                  (2560, 1440)])
def test_the_deck_button_never_lands_on_another_row(library, size):
    """The bug this test exists for.

    The button first sat UNDER the Mod Patusa row, which looked right at 1080p
    and covered the "pola podwójne" label at 1280x760, where the elastic gaps
    are at their tightest.  It belongs beside the row, in the margin.
    """
    screen, _ = menu(library, size)
    button = screen.mod_deck_button.rect
    for stepper in (screen.players_stepper, screen.cells_stepper,
                    screen.chest_stepper, screen.doubles_stepper,
                    screen.mod_first_stepper, screen.mod_interval_stepper):
        for rect in stepper.rects.values():
            assert not button.colliderect(rect), f"button over a stepper at {size}"
    assert not button.colliderect(screen.start_button.rect)
    assert button.right <= screen.app.layout.win_w
    assert button.top >= 0


@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080)])
def test_the_panel_fits_the_window(library, size):
    screen, _ = menu(library, size)
    screen.settings_panel.open()
    panel = screen.settings_panel.panel
    assert panel.width <= size[0] and panel.height <= size[1]
    assert panel.top >= 0 and panel.bottom <= size[1]
    for stepper in screen.settings_panel.steppers:
        for rect in stepper.rects.values():
            assert panel.contains(rect), "a stepper hangs out of the panel"


def test_the_panel_survives_a_resize(library):
    screen, _ = menu(library)
    screen.settings_panel.open()
    screen.settings_panel.counts["Speedrun"] = 4
    screen.app.layout.resize(1280, 760)
    screen.on_resize()
    assert screen.settings_panel.counts["Speedrun"] == 4, "settings lost"
    assert screen.settings_panel.active


# ── Sesja na PG greys the ability button ─────────────────────────────────────
def _game_with(library, title: str):
    game = create_game(
        SessionConfig(num_players=4, seed=99, chest_open_round=10_000,
                      mod_round_first=10_000, piotrek_picks_pawn=False),
        library,
    )
    deck = game.decks[settings.DECK_MODS]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.mod_slots[0] = card
    return game


def test_the_state_says_the_buttons_are_locked(library):
    """What the interface reads.  The engine refuses regardless."""
    game = _game_with(library, "Sesja na PG")
    assert game.abilities_locked
    game.mod_slots[0] = None
    assert not game.abilities_locked


def test_a_locked_ability_still_reports_its_remaining_uses(library):
    """Locked is not spent, and the caption must not say it is.

    The uses are what come back when the mod leaves, so a button reading
    "ZUŻYTE" would be telling the player something false.
    """
    game = _game_with(library, "Sesja na PG")
    seat = next(p.index for p in game.players
                if p.character is not None and p.character.has_ability)
    card = game.players[seat].character
    assert card.ability_available, "the charge is untouched"
    assert card.uses_left == card.uses_total

    game.active_player_index = seat
    game.apply(cmd.UseAbility(player_index=seat, source="character"))
    assert card.uses_left == card.uses_total, "a refused use cost a charge"
