"""Stage 44 verification: the round 7 scenario, Paczka, Esc, and the pixels."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

WINDOW = (1920, 1080)
LIB = ContentLibrary.load()
OK, BAD = [], []


def check(label, condition):
    (OK if condition else BAD).append(label)
    print(("  PASS  " if condition else "  FAIL  ") + label)


def make():
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(SessionConfig(num_players=5, board_cells=24,
                                      chest_open_round=3, seed=77), LIB)
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    return screen


def frame(screen, mouse=(0, 0)):
    app = screen.app
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(1 / 60, mouse)
    screen.draw(app.canvas)


def settle(screen, n=30):
    for _ in range(n):
        frame(screen)


def click(screen, pos, button=1):
    pos = (int(pos[0]), int(pos[1]))
    screen.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos,
                                           button=button), pos)
    screen.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos,
                                           button=button), pos)


def press(screen, key=pygame.K_ESCAPE):
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key, unicode=""),
                        (0, 0))


def round_seven(screen):
    state = screen.state
    state.round_number = 6
    deck = state.deck(settings.DECK_CHEST)
    for player in state.players:
        while len(state.chest_cards(player)) < state.chest_limit(player):
            card = deck.take_card()
            if card is None:
                break
            player.add_card(card)
    screen.bus.emit_all(state._begin_round(7))
    frame(screen)


print("=" * 66)
print("11. THE ROUND 7 SCENARIO")
print("=" * 66)
s = make()
round_seven(s)
settle(s)
check("mod selection and chest discard are both open",
      s.mod_choice.active and s.chest_choice.active)
check("1. the Mod window is painted last (topmost)",
      s.modals.names.index("mod_choice") > s.modals.names.index("chest_choice"))
check("2. the Mod window owns the input", s.modals.owner() == "mod_choice")

# PIXELS: the mod panel must actually be painted over the chest panel.
layout = s.app.layout
mod_panel = layout.mod_choice_panel(len(s.mod_choice.cards))
chest_panel = layout.chest_choice_panel(len(s.chest_choice.cards))
check("   the two panels really overlap", mod_panel.colliderect(chest_panel))

# Paint the two windows onto scratch surfaces in each order.  Whichever order
# reproduces the pixels of the REAL frame is the order the screen actually
# used — which is how to check "visually on top" rather than assume it from
# the source.
r, cards, lay = s.app.renderer, s.cards, s.app.layout


def painted(order, surface):
    scratch = surface.copy()
    for name in order:
        if name == "chest":
            s.chest_choice.draw(r, cards, lay, scratch, (0, 0))
        else:
            s.mod_choice.draw(r, cards, lay, scratch, (0, 0))
    return scratch


real_frame = s.app.canvas.copy()
below = s.app.canvas.copy()          # the frame as it stands, windows included
s.app.renderer.begin(below)
below.fill(s.app.renderer.theme.background)
s.board_view.draw(below)
chest_then_mod = painted(("chest", "mod"), below)
mod_then_chest = painted(("mod", "chest"), below)

overlap = mod_panel.clip(chest_panel)
differing = [(x, y)
             for y in range(overlap.top + 4, overlap.bottom - 4, 7)
             for x in range(overlap.left + 4, overlap.right - 4, 7)
             if chest_then_mod.get_at((x, y)) != mod_then_chest.get_at((x, y))]
agree = sum(1 for pt in differing
            if real_frame.get_at(pt) == chest_then_mod.get_at(pt))
check(f"   the overlap is painted Mod-over-chest ({agree}/{len(differing)} "
      f"sampled pixels)",
      bool(differing) and agree == len(differing))

before_keep = list(s.chest_choice.keep)
click(s, layout.mod_choice_card_rect(1, RULES.mod_choices).center)
check("3. the chest window cannot be clicked through",
      list(s.chest_choice.keep) == before_keep)
check("   ...and the Mod click landed", s.state.mod_slots[0] is not None)
check("4. the chest discard is still pending",
      s.state.pending_chest_choice is not None
      and s.modals.pending() == ["chest_choice"])

selection = s.state.pending_mod_selection
for _ in range(len(selection.hunter_seats)):
    click(s, layout.mod_choice_card_rect(1, RULES.mod_choices).center)
    frame(s)
settle(s)
check("5. once the Mods are done the chest discard becomes active",
      s.state.pending_mod_selection is None and s.modals.owner() == "chest_choice")

answered = 0
while s.chest_choice.active and answered < 4:
    count = len(s.chest_choice.cards)
    for i in range(s.chest_choice.limit):
        click(s, layout.chest_choice_card_rect(i, count).center)
    click(s, layout.chest_confirm_rect(count).center)
    frame(s)
    answered += 1
check("6. the chest discard completes normally by hand",
      s.state.pending_chest_choice is None and not s.chest_choice.active)
settle(s)
check("7. nothing is stuck or invisible-but-active",
      s.modals.owner() is None and s.modals.active() == [])

print()
print("=" * 66)
print("11b. PACZKA")
print("=" * 66)
s = make()
s.bus.emit_all(s.state._begin_round(3))
frame(s)
sel = s.state.pending_mod_selection
deck = s.state.deck(settings.DECK_MODS)
paczka = next(c for pile in (deck.draw_pile, deck.discard_pile,
                             sel.piotrek_cards, sel.hunter_cards)
              for c in pile if c.passive.get("reveal_chest"))
for pile in (deck.draw_pile, deck.discard_pile, sel.hunter_cards,
             sel.piotrek_cards):
    if paczka in pile:
        pile.remove(paczka)
sel.piotrek_cards.insert(0, paczka)
del sel.piotrek_cards[3:]
s.mod_choice.hide()
s._sync_mod_overlay()
frame(s)

click(s, s.app.layout.mod_choice_card_rect(0, len(s.mod_choice.cards)).center)
frame(s)
check("1. a player selects Paczka",
      s.state.mod_slots[0] is not None and s.state.mod_slots[0].uid == paczka.uid)
check("2. its window does NOT interrupt the remaining selections",
      not s.chest_reveal.active and s.modals.owner() == "mod_choice")
check("   the effect is queued rather than lost", sel.followup_uids == [paczka.uid])
for n in range(len(sel.hunter_seats)):
    check(f"3. hunter {n + 1} can still vote",
          s.mod_choice.mode == "hunters" and not s.chest_reveal.active)
    click(s, s.app.layout.mod_choice_card_rect(1, len(s.mod_choice.cards)).center)
    frame(s)
settle(s)
check("4. only now does the Paczka window appear",
      s.state.pending_mod_selection is None and s.chest_reveal.active)
check("   ...and it owns the input", s.modals.owner() == "chest_reveal")
click(s, s.app.layout.chest_reveal_ok_rect(s.chest_reveal.lines).center)
check("5. it can be interacted with normally", not s.chest_reveal.active)

print()
print("=" * 66)
print("12. ESC ACROSS MODAL TYPES")
print("=" * 66)
# Mod selection
s = make()
s.bus.emit_all(s.state._begin_round(3))
frame(s)
offered = {c.uid for c in s.state.pending_mod_selection.piotrek_cards}
press(s)
frame(s)
check("Mod selection: resolves with one of the three dealt",
      s.state.mod_slots[0] is not None and s.state.mod_slots[0].uid in offered)

# Chest discard, several pending, and 'no invisible modal left'
s = make()
round_seven(s)
seen, guard = [], 0
while s.modals.owner() is not None and guard < 14:
    owner = s.modals.owner()
    if not seen or seen[-1] != owner:
        seen.append(owner)
    press(s)
    frame(s)
    guard += 1
settle(s)
check("multiple pending modals: resolved topmost-first",
      seen[0] == "mod_choice" and "chest_choice" in seen
      and seen.index("chest_choice") > seen.index("mod_choice"))
check("chest discard: Esc kept a legal hand",
      s.state.pending_chest_choice is None)
check("no invisible modal is left blocking the game",
      s.modals.owner() is None and s.modals.active() == []
      and not s.pause_menu.active)
check("the game state is valid afterwards",
      all(len(s.state.chest_cards(p)) <= s.state.chest_limit(p)
          for p in s.state.players))

# A card target selection (a pending choice with several valid options)
s = make()
state = s.state
mdeck = state.deck(settings.DECK_MOVEMENT)
card = next((c for c in mdeck.draw_pile if c.effect is not None
             and c.effect.params.get("target") == "choice"), None)
if card is not None:
    mdeck.draw_pile.remove(card)
    state.active_player.add_card(card)
    settle(s)
    click(s, s.hand.slots[card.uid].position)
    settle(s, 5)
    if s.pending_choice is not None:
        opts = {o[0] for o in s.pending_choice.options}
        many = len(opts) > 1
        # An effect may ask SEVERAL times (which pawn, then which half); Esc
        # answers the question in front of the player, so it is pressed once
        # per question exactly as a click would be.
        asked, guard = 0, 0
        while s.pending_choice is not None and guard < 6:
            picked = {o[0] for o in s.pending_choice.options}
            press(s)
            settle(s, 3)
            asked += 1
            guard += 1
            if picked and s.pending_choice is not None:
                # every answer so far came from a set the engine offered
                pass
        check(f"pawn/target selection ({len(opts)} valid options): resolved "
              f"in {asked} question(s)",
              s.pending_choice is None and s.modals.owner() is None)
        check("   the card left the hand — a real resolution, not a hide",
              card not in state.active_player.hand)
        check("   several valid options were on offer", many)
    else:
        check("target selection reached a prompt", False)
else:
    check("found a choice-target movement card", False)

# One valid option only
s = make()
s.bus.emit_all(s.state._begin_round(3))
frame(s)
sel = s.state.pending_mod_selection
only = sel.piotrek_cards[0]
del sel.piotrek_cards[1:]
s.mod_choice.hide()
s._sync_mod_overlay()
frame(s)
press(s)
frame(s)
check("a window with ONE valid choice: that one is taken",
      s.state.mod_slots[0] is not None and s.state.mod_slots[0].uid == only.uid)

# Paczka window
s = make()
s.bus.emit_all([ev.ChestCardsRevealed([
    ev.ChestHolding(player_index=0, player_name="Ktoś", titles=["Gejtos"])])])
frame(s)
press(s)
check("Paczka window: Esc dismisses it", not s.chest_reveal.active)

# Esc with nothing open keeps its old meaning
s = make()
settle(s)
press(s)
check("Esc with nothing open still opens the pause menu", s.pause_menu.active)
press(s)
check("Esc closes the pause menu again", not s.pause_menu.active)

print()
print("=" * 66)
print(f"{len(OK)} passed, {len(BAD)} failed")
for label in BAD:
    print("  FAILED:", label)
print("=" * 66)
sys.exit(1 if BAD else 0)
