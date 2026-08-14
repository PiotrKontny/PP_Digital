"""
Stage 44: modal priority, the round 7 conflict, deferred Mod windows and Esc.

These drive the REAL screen with synthetic pygame events, because the bug this
stage fixes is invisible to a test that only reads flags: the round 7 failure
was two windows both reporting ``active`` — which was correct — while the
CLICK went to the wrong one.  So the assertions here are about who actually
received the input and what actually changed in the state, not about booleans.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame
import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

WINDOW = (1920, 1080)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make_screen(library, **kwargs) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    config = SessionConfig(num_players=5, board_cells=24, chest_open_round=3,
                           seed=77, **kwargs)
    screen = GameScreen(app, LocalSession(create_game(config, library)))
    app.push(screen)
    return screen


@pytest.fixture
def screen(library) -> GameScreen:
    return make_screen(library)


def frame(screen: GameScreen, dt: float = 1 / 60, mouse=(0, 0)) -> None:
    app = screen.app
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(dt, mouse)
    screen.draw(app.canvas)


def settle(screen: GameScreen, frames: int = 20, mouse=(0, 0)) -> None:
    for _ in range(frames):
        frame(screen, mouse=mouse)


def click(screen: GameScreen, pos, button: int = 1) -> None:
    pos = (int(pos[0]), int(pos[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button), pos)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button), pos)


def press(screen: GameScreen, key=pygame.K_ESCAPE) -> None:
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key, unicode=""),
                        (0, 0))


def fill_chest_hands(screen: GameScreen) -> None:
    """Bring every seat up to its chest limit, as round 7 normally would."""
    state = screen.state
    deck = state.deck(settings.DECK_CHEST)
    for player in state.players:
        while len(state.chest_cards(player)) < state.chest_limit(player):
            card = deck.take_card()
            if card is None:
                return
            player.add_card(card)


def open_round(screen: GameScreen, round_number: int):
    events = screen.state._begin_round(round_number)
    screen.bus.emit_all(events)
    frame(screen)
    return screen.state.pending_mod_selection


def round_seven(screen: GameScreen):
    """The reported scenario: mod selection AND a chest discard, together."""
    screen.state.round_number = 6
    fill_chest_hands(screen)
    selection = open_round(screen, 7)
    assert selection is not None, "round 7 is a mod round"
    assert screen.state.pending_chest_choice is not None, "and a hand overflowed"
    return selection


# ── A. the Patus Mod modal has priority over the chest discard ──────────────
def test_round_seven_puts_the_mod_selection_on_top(screen):
    round_seven(screen)
    assert screen.mod_choice.active
    assert screen.chest_choice.active
    # Both are up; exactly one owns input, and it is the Mod selection.
    assert screen.modals.owner() == "mod_choice"


def test_the_mod_selection_is_painted_above_the_chest_discard(screen):
    """Visual order and input order are the SAME list, so this is structural."""
    order = screen.modals.names
    assert order.index("mod_choice") > order.index("chest_choice")


# ── B. input cannot reach the modal underneath ──────────────────────────────
def test_a_click_on_a_mod_card_is_not_eaten_by_the_chest_window(screen):
    """The regression itself: the click used to land on the window beneath."""
    round_seven(screen)
    layout = screen.app.layout
    rect = layout.mod_choice_card_rect(1, RULES.mod_choices)
    # The two windows really do overlap — otherwise this proves nothing.
    assert rect.colliderect(layout.chest_choice_panel(len(screen.chest_choice.cards)))
    wanted = screen.mod_choice.cards[1].uid
    keep_before = list(screen.chest_choice.keep)

    click(screen, rect.center)

    assert screen.state.mod_slots[0] is not None
    assert screen.state.mod_slots[0].uid == wanted
    assert list(screen.chest_choice.keep) == keep_before


def test_a_click_on_the_chest_window_does_nothing_while_mods_are_chosen(screen):
    """Clicking a card in the covered window must not select it."""
    round_seven(screen)
    layout = screen.app.layout
    count = len(screen.chest_choice.cards)
    keep_before = list(screen.chest_choice.keep)
    for index in range(count):
        click(screen, layout.chest_choice_card_rect(index, count).center)
    assert list(screen.chest_choice.keep) == keep_before


def test_the_chest_confirm_button_cannot_be_pressed_through_the_mod_window(screen):
    round_seven(screen)
    seat, _ = screen.state.pending_chest_choice
    held = len(screen.state.chest_cards(screen.state.player(seat)))
    click(screen, screen.app.layout.chest_confirm_rect(
        len(screen.chest_choice.cards)).center)
    assert screen.state.pending_chest_choice is not None
    assert len(screen.state.chest_cards(screen.state.player(seat))) == held


# ── C. the chest discard stays pending, then becomes active ─────────────────
def test_the_chest_discard_waits_and_then_takes_over(screen):
    selection = round_seven(screen)
    assert screen.modals.pending() == ["chest_choice"]
    layout = screen.app.layout

    # Piotrek picks...
    click(screen, layout.mod_choice_card_rect(0, RULES.mod_choices).center)
    frame(screen)
    # ...and the chest window is STILL pending while the hunters vote.
    if screen.state.pending_mod_selection is not None:
        assert screen.modals.owner() == "mod_choice"
        assert "chest_choice" in screen.modals.pending()
    for _ in range(len(selection.hunter_seats)):
        click(screen, layout.mod_choice_card_rect(1, RULES.mod_choices).center)
        frame(screen)

    assert screen.state.pending_mod_selection is None
    assert not screen.mod_choice.active
    # The queue moved on by itself: the chest discard is now the owner.
    assert screen.modals.owner() == "chest_choice"
    assert screen.modals.pending() == []


def test_the_chest_discard_can_then_be_completed_normally(screen):
    selection = round_seven(screen)
    layout = screen.app.layout
    click(screen, layout.mod_choice_card_rect(0, RULES.mod_choices).center)
    frame(screen)
    for _ in range(len(selection.hunter_seats)):
        click(screen, layout.mod_choice_card_rect(1, RULES.mod_choices).center)
        frame(screen)

    # A dealing round feeds TWO seats, so there may be a second prompt behind
    # the first; answering one must let the next through.
    answered = []
    while screen.chest_choice.active:
        seat = screen.chest_choice_seat
        count = len(screen.chest_choice.cards)
        limit = screen.chest_choice.limit
        for index in range(limit):
            click(screen, layout.chest_choice_card_rect(index, count).center)
        click(screen, layout.chest_confirm_rect(count).center)
        frame(screen)
        answered.append((seat, limit))
        assert screen.chest_choice_seat != seat or not screen.chest_choice.active

    assert answered, "the discard really was completable by hand"
    for seat, limit in answered:
        assert len(screen.state.chest_cards(screen.state.player(seat))) == limit
    assert screen.state.pending_chest_choice is None


# ── D. Paczka does not interrupt the rest of the selection ──────────────────
def _paczka(state):
    """A Paczka anywhere it can still be reached: either pile, or the six
    cards a selection has already dealt out of them."""
    deck = state.deck(settings.DECK_MODS)
    piles = [deck.draw_pile, deck.discard_pile]
    selection = state.pending_mod_selection
    if selection is not None:
        piles.extend([selection.piotrek_cards, selection.hunter_cards])
    for pile in piles:
        for card in pile:
            if card.passive.get("reveal_chest"):
                return card
    return None


def _rebuild_overlay(screen) -> None:
    """Force the Mod overlay to re-read the candidates.

    ``_sync_mod_overlay`` deliberately only rebuilds when the SIDE changes —
    otherwise every vote would restart the animations — so a test that rewrites
    the dealt cards has to say so explicitly.
    """
    screen.mod_choice.hide()
    screen._sync_mod_overlay()
    frame(screen)


def drain_modals(screen, limit: int = 12) -> list:
    """Press Esc until nothing is left open.  Returns the owners, in order."""
    seen = []
    for _ in range(limit):
        owner = screen.modals.owner()
        if owner is None:
            break
        if not seen or seen[-1] != owner:
            seen.append(owner)
        press(screen)
        frame(screen)
    return seen


def stage_paczka_for_piotrek(screen) -> int:
    """Make Paczka one of Piotrek's three candidates, and return its uid."""
    selection = screen.state.pending_mod_selection
    card = _paczka(screen.state)
    assert card is not None, "the mods deck still holds a Paczka"
    if card in selection.piotrek_cards:
        # Already on offer; just make sure it is the one the test clicks.
        selection.piotrek_cards.remove(card)
        selection.piotrek_cards.insert(0, card)
        _rebuild_overlay(screen)
        return card.uid
    deck = screen.state.deck(settings.DECK_MODS)
    if card in deck.draw_pile:
        deck.draw_pile.remove(card)
    elif card in deck.discard_pile:
        deck.discard_pile.remove(card)
    elif card in selection.hunter_cards:
        selection.hunter_cards.remove(card)
        spare = deck.take_card()
        if spare is not None:
            selection.hunter_cards.append(spare)
    replaced = selection.piotrek_cards[0]
    selection.piotrek_cards[0] = card
    deck.return_card(replaced)
    _rebuild_overlay(screen)
    return card.uid


def test_paczka_does_not_open_before_every_mod_is_chosen(screen):
    selection = open_round(screen, 3)
    uid = stage_paczka_for_piotrek(screen)
    layout = screen.app.layout

    click(screen, layout.mod_choice_card_rect(0, RULES.mod_choices).center)
    frame(screen)

    assert screen.state.mod_slots[0] is not None
    assert screen.state.mod_slots[0].uid == uid, "Paczka really was chosen"
    # ...and yet nothing has interrupted the hunters.
    assert not screen.chest_reveal.active
    assert selection.followup_uids == [uid], "it is queued, not lost"
    assert screen.modals.owner() == "mod_choice"


def test_the_other_players_can_still_finish_choosing(screen):
    selection = open_round(screen, 3)
    stage_paczka_for_piotrek(screen)
    layout = screen.app.layout
    click(screen, layout.mod_choice_card_rect(0, RULES.mod_choices).center)
    frame(screen)

    for n in range(len(selection.hunter_seats)):
        assert not screen.chest_reveal.active, "still nothing in the way"
        assert screen.mod_choice.mode == "hunters"
        click(screen, layout.mod_choice_card_rect(1, RULES.mod_choices).center)
        frame(screen)

    assert len(selection.votes) == len(selection.hunter_seats)


def test_paczka_opens_once_the_selection_phase_is_over(screen):
    selection = open_round(screen, 3)
    stage_paczka_for_piotrek(screen)
    layout = screen.app.layout
    click(screen, layout.mod_choice_card_rect(0, RULES.mod_choices).center)
    frame(screen)
    for _ in range(len(selection.hunter_seats)):
        click(screen, layout.mod_choice_card_rect(1, RULES.mod_choices).center)
        frame(screen)

    assert screen.state.pending_mod_selection is None
    assert screen.chest_reveal.active, "and now it appears"
    assert screen.modals.owner() == "chest_reveal"
    # ...and it can be interacted with.
    click(screen, layout.chest_reveal_ok_rect(screen.chest_reveal.lines).center)
    assert not screen.chest_reveal.active


def test_a_paczka_outside_a_selection_still_opens_at_once(screen):
    """PlaceMod and Thunderfuck have nothing to wait for — unchanged."""
    card = _paczka(screen.state)
    events = screen.state._arm_mod(card)
    assert any(isinstance(event, ev.ChestCardsRevealed) for event in events)


def test_a_queued_paczka_that_left_the_rack_owes_nothing(screen):
    """Rebuilt from the RACK, so a mod replaced before the pause lifted is gone."""
    selection = open_round(screen, 3)
    uid = stage_paczka_for_piotrek(screen)
    click(screen, screen.app.layout.mod_choice_card_rect(
        0, RULES.mod_choices).center)
    frame(screen)
    assert selection.followup_uids == [uid]

    screen.state.mod_slots[0] = None            # something replaced it
    events = screen.state._finish_mod_selection()
    assert not any(isinstance(event, ev.ChestCardsRevealed) for event in events)


def test_the_deferred_queue_is_in_the_snapshot(screen):
    """Two machines that disagree about it disagree about what happens next."""
    open_round(screen, 3)
    uid = stage_paczka_for_piotrek(screen)
    click(screen, screen.app.layout.mod_choice_card_rect(
        0, RULES.mod_choices).center)
    assert screen.state.snapshot()["mod_selection"]["followups"] == [uid]


def test_a_replayed_paczka_event_is_held_by_the_screen_too(screen):
    """The replica-side net for L19: an old event must not jump the queue."""
    open_round(screen, 3)
    screen.bus.emit_all([ev.ChestCardsRevealed([
        ev.ChestHolding(player_index=0, player_name="Ktoś", titles=["Gejtos"]),
    ])])
    frame(screen)
    assert not screen.chest_reveal.active
    assert screen.modals.owner() == "mod_choice"


# ── E/F. Esc resolves with a valid random choice and leaves nothing behind ──
def test_escape_resolves_the_mod_selection_step_by_step(screen):
    selection = open_round(screen, 3)
    offered = {card.uid for card in selection.piotrek_cards}

    press(screen)
    frame(screen)

    chosen = screen.state.mod_slots[0]
    assert chosen is not None
    assert chosen.uid in offered, "one of the three it was actually dealt"


def test_escape_finishes_a_whole_selection_without_sticking(screen):
    open_round(screen, 3)
    seen = drain_modals(screen)

    assert seen[0] == "mod_choice"
    assert screen.state.pending_mod_selection is None
    assert not screen.mod_choice.active
    assert screen.state.mod_slots[0] is not None
    assert screen.state.mod_slots[1] is not None
    # Nothing invisible is left holding the game — including a window a
    # randomly chosen Mod queued on the way through.
    assert screen.modals.owner() is None
    assert not screen.pause_menu.active, "Esc answered rather than leaving"


def test_escape_on_the_chest_discard_keeps_exactly_the_limit(screen):
    round_seven(screen)
    # Answer the Mod selection first — and anything it queued — so the chest
    # discard is genuinely the one Esc is aimed at.
    while screen.modals.owner() not in ("chest_choice", None):
        press(screen)
        frame(screen)
    assert screen.modals.owner() == "chest_choice"

    seat = screen.chest_choice_seat
    offered = {card.uid for card in screen.chest_choice.cards}
    limit = screen.chest_choice.limit

    press(screen)
    frame(screen)

    kept = screen.state.chest_cards(screen.state.player(seat))
    assert len(kept) == limit
    assert {card.uid for card in kept} <= offered, "never an invented card"
    assert screen.chest_choice_seat != seat or not screen.chest_choice.active
    drain_modals(screen)
    assert screen.state.pending_chest_choice is None


def test_escape_leaves_no_invisible_modal_holding_the_game(screen):
    """The whole point of F: the UI and the game state end up agreeing."""
    round_seven(screen)
    drain_modals(screen)

    assert screen.modals.owner() is None
    assert screen.modals.active() == []
    assert screen.state.pending_mod_selection is None
    assert screen.state.pending_chest_choice is None
    # ...and the table moves again.
    assert screen.can_end_turn or not screen.state.may_control(
        screen.state.active_player_index)


def test_escape_with_one_valid_option_takes_it(screen):
    """"If there is only one valid option, select it" — no coin toss."""
    selection = open_round(screen, 3)
    only = selection.piotrek_cards[0]
    del selection.piotrek_cards[1:]
    _rebuild_overlay(screen)
    assert len(screen.mod_choice.cards) == 1

    press(screen)
    frame(screen)
    assert screen.state.mod_slots[0] is not None
    assert screen.state.mod_slots[0].uid == only.uid


def test_escape_never_answers_for_a_seat_with_nothing_to_decide(screen):
    """Zero valid options is handled, not guessed at: Esc means what it did."""
    selection = open_round(screen, 3)
    selection.piotrek_done = True
    # A machine that owns no voting seat: nothing on this side is answerable,
    # so the overlay is the read-only "waiting" view.
    screen.state.config.edit_mode = False
    screen.state.config.local_seat = selection.piotrek_seat
    _rebuild_overlay(screen)
    assert screen.mod_choice.mode == "waiting"

    press(screen)
    assert screen.state.mod_slots[1] is None, "no vote was invented"
    assert screen.pause_menu.active, "Esc fell through to its old meaning"


def test_escape_does_not_randomly_end_the_match(screen):
    """The ending and the pause menu are navigation, not a choice to roll for."""
    for name in ("victory", "pause_menu"):
        modal = next(m for m in screen.modals.modals if m.name == name)
        assert modal.resolve is None


# ── G. several pending interactions resolve in order ────────────────────────
def test_pending_interactions_resolve_topmost_first(screen):
    round_seven(screen)
    assert screen.modals.owner() == "mod_choice"
    assert screen.modals.pending() == ["chest_choice"]

    seen = drain_modals(screen)

    # A Mod chosen at random along the way may queue a window of its own
    # (Paczka), so the exact list varies — what must hold is the ORDER: the
    # selection is answered first, and the chest discard never jumps it.
    assert seen[0] == "mod_choice"
    assert "chest_choice" in seen
    assert seen.index("chest_choice") > seen.index("mod_choice")
    assert "mod_choice" not in seen[seen.index("chest_choice"):]
    assert screen.modals.owner() is None


def test_a_lower_modal_is_never_the_owner_while_one_is_above_it(screen):
    round_seven(screen)
    for _ in range(6):
        active = screen.modals.active()
        if not active:
            break
        # The owner is always the LAST active one — the topmost.
        assert screen.modals.owner() == active[-1].name
        press(screen)
        frame(screen)


# ── H. the paint order and the input owner are one list ─────────────────────
def test_paint_order_and_input_order_are_the_same_list(screen):
    """There is no second ordering left to drift out of step with the first."""
    order = screen.modals.names
    assert order == [modal.name for modal in screen.modals.modals]
    # And the stack itself picks the last active entry, by construction.
    round_seven(screen)
    active = screen.modals.active()
    assert screen.modals.owner() == active[-1].name


def test_every_modal_declares_how_it_owns_input(screen):
    """A window with no covers and no blocking would leak clicks downwards."""
    for modal in screen.modals.modals:
        assert modal.blocking or modal.covers is not None, modal.name


def test_a_covered_window_absorbs_the_click_rather_than_the_board(screen):
    """Clicking the pending window must not pan the map behind it either."""
    round_seven(screen)
    layout = screen.app.layout
    panel = layout.chest_choice_panel(len(screen.chest_choice.cards))
    # A point inside the chest window but outside the mod panel.
    mod_panel = layout.mod_choice_panel(len(screen.mod_choice.cards))
    point = (panel.left + 6, panel.centery)
    assert not mod_panel.collidepoint(point)
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=point, button=1)
    assert screen.modals.handle_event(event, point) is True


def test_navigation_still_falls_through_a_non_blocking_modal(screen):
    """The pause is not a blindfold: the board can still be looked at."""
    open_round(screen, 3)
    wheel = pygame.event.Event(pygame.MOUSEWHEEL, y=1, x=0)
    assert screen.modals.handle_event(wheel, (960, 540)) is False


def test_the_screen_renders_with_two_windows_up(screen):
    """Nothing in the new paint path raises with a full stack."""
    round_seven(screen)
    settle(screen, 5)
    assert screen.mod_choice.active and screen.chest_choice.active


def test_the_overlap_is_really_painted_mod_over_chest(screen):
    """PIXELS, not flags.

    "Visually on top" is the half of this bug a flag cannot see: both windows
    reported ``active`` and both were correct.  So the two windows are painted
    onto scratch surfaces in each order and the real frame is matched against
    them — whichever order reproduces the frame is the order the screen used.
    """
    round_seven(screen)
    settle(screen, 30)
    layout = screen.app.layout
    mod_panel = layout.mod_choice_panel(len(screen.mod_choice.cards))
    chest_panel = layout.chest_choice_panel(len(screen.chest_choice.cards))
    overlap = mod_panel.clip(chest_panel)
    assert overlap.width > 40 and overlap.height > 40

    r, cards, lay = screen.app.renderer, screen.cards, layout
    real = screen.app.canvas.copy()
    below = screen.app.canvas.copy()
    r.begin(below)
    below.fill(r.theme.background)
    screen.board_view.draw(below)

    def painted(order):
        scratch = below.copy()
        for name in order:
            if name == "chest":
                screen.chest_choice.draw(r, cards, lay, scratch, (0, 0))
            else:
                screen.mod_choice.draw(r, cards, lay, scratch, (0, 0))
        return scratch

    chest_then_mod = painted(("chest", "mod"))
    mod_then_chest = painted(("mod", "chest"))
    differing = [(x, y)
                 for y in range(overlap.top + 4, overlap.bottom - 4, 9)
                 for x in range(overlap.left + 4, overlap.right - 4, 9)
                 if chest_then_mod.get_at((x, y)) != mod_then_chest.get_at((x, y))]
    assert differing, "the two orders really do look different"
    assert all(real.get_at(point) == chest_then_mod.get_at(point)
               for point in differing)
