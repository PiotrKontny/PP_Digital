#!/usr/bin/env python3
"""
Frame inspector.

Renders a frame headlessly and reports, per layout region, how much was
actually painted there.  It exists because a rendering bug usually shows up as
a region that is empty, uniform, or overlapping its neighbour — all of which
are visible in the numbers without anyone having to look at a picture.

    python tools/inspect_frame.py --window 1920x1080
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from pedzacy_piotrek.cards.loader import ContentLibrary  # noqa: E402
from pedzacy_piotrek.config.settings import SessionConfig  # noqa: E402
from pedzacy_piotrek.engine.setup import create_game  # noqa: E402
from pedzacy_piotrek.net.session import LocalSession  # noqa: E402
from pedzacy_piotrek.ui.app import App  # noqa: E402
from pedzacy_piotrek.ui.game_screen import GameScreen  # noqa: E402
from pedzacy_piotrek.ui.layout import Layout  # noqa: E402


def region_stats(surface: pygame.Surface, rect: pygame.Rect, step: int = 7):
    rect = rect.clip(surface.get_rect())
    if rect.width < 2 or rect.height < 2:
        return 0, 0.0
    colours = set()
    total = 0
    for x in range(rect.left, rect.right, step):
        for y in range(rect.top, rect.bottom, step):
            colours.add(surface.get_at((x, y))[:3])
            total += 1
    return len(colours), len(colours) / max(1, total)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="1920x1080")
    parser.add_argument("--cells", type=int, default=30)
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--seed", type=int, default=4)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--hover-hand", action="store_true",
                        help="park the cursor over the middle of the fan")
    args = parser.parse_args()

    width, height = (int(v) for v in args.window.lower().split("x"))
    library = ContentLibrary.load()
    app = App(Layout(), headless=True, size=(width, height))
    state = create_game(
        SessionConfig(num_players=args.players, board_cells=args.cells, seed=args.seed),
        library,
    )
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)

    # Play one card so the 'recently played' strip and the walk animation are
    # exercised rather than reported as empty grass.
    from pedzacy_piotrek.engine import commands as cmd

    deck = state.deck("movement")
    playable = next((c for c in deck.draw_pile if c.is_playable), None)
    if playable is not None:
        deck.draw_pile.remove(playable)
        state.active_player.add_card(playable)
        screen.submit(cmd.PlayCard(player_index=state.active_player_index,
                                   card_uid=playable.uid))

    layout = app.layout
    mouse = (
        layout.hand_area.centerx if args.hover_hand else layout.board_viewport.centerx,
        layout.hand_area.centery if args.hover_hand else layout.board_viewport.centery,
    )
    for _ in range(args.frames):
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, mouse)
        screen.draw(app.canvas)

    regions = {
        "left panel": layout.left_panel,
        "  mod slot 0": layout.mod_slot_rect(0),
        "  deck draw 0": layout.deck_draw_rect(0),
        "  deck draw 2": layout.deck_draw_rect(2),
        "turn bar": layout.turn_bar,
        "player strip": layout.player_strip,
        "board": layout.board_viewport,
        "right panel": layout.right_panel,
        "  ability card": layout.character_panel(False)["card"],
        "  char draw": layout.character_panel(False)["char_draw"],
        "  colour grid": layout.pawn_grid_panel(layout.pawn_grid_top(False)),
        "hand area": layout.hand_area,
        "recent slot 0": layout.recent_slot_rect(0),
        "end turn": layout.end_turn_button,
    }

    print(f"window {width}x{height}   layout {layout.win_w}x{layout.win_h}")
    problems = 0
    for name, rect in regions.items():
        colours, ratio = region_stats(app.canvas, rect)
        flag = ""
        if colours <= 2:
            flag = "  <-- looks empty"
            problems += 1
        print(f"{name:<16} {str(rect):<34} colours={colours:<5} variety={ratio:.2f}{flag}")

    # Overlap check: no two top-level regions may intersect.
    tops = {
        "left": layout.left_panel, "turn": layout.turn_bar,
        "players": layout.player_strip, "board": layout.board_viewport,
        "right": layout.right_panel, "hand": layout.hand_area,
    }
    names = list(tops)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if tops[a].colliderect(tops[b]):
                print(f"OVERLAP: {a} and {b}")
                problems += 1

    # Everything must stay on screen.
    window = pygame.Rect(0, 0, layout.win_w, layout.win_h)
    for name, rect in regions.items():
        if not window.contains(rect):
            print(f"OFF-SCREEN: {name.strip()} {rect}")
            problems += 1

    print("problems:", problems)
    pygame.quit()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
