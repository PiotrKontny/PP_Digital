#!/usr/bin/env python3
"""
Headless screenshot tool.

    python tools/screenshot.py --cells 24 --out shots/

Renders the real game screen against SDL's dummy driver and writes PNGs — the
board on its own, and the full interface.  Useful for reviewing a layout or a
board theme without launching the game, and for spotting rendering regressions
in CI by diffing the output.
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", type=int, default=24)
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--out", type=Path, default=Path("shots"))
    parser.add_argument("--board-scale", type=float, default=0.42)
    parser.add_argument("--window", type=str, default="1920x1080")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    width, height = (int(v) for v in args.window.lower().split("x"))
    library = ContentLibrary.load()
    app = App(Layout(), headless=True, size=(width, height))
    config = SessionConfig(
        num_players=args.players,
        board_cells=args.cells,
        character_choices=[],
        seed=args.seed,
    )
    state = create_game(config, library)
    session = LocalSession(state)
    screen = GameScreen(app, session)
    app.push(screen)

    # Advance a few frames so animations and the camera settle.
    mouse = (app.layout.win_w // 2, app.layout.win_h // 2)
    for _ in range(args.frames):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, mouse)
        screen.draw(app.canvas)

    pygame.image.save(app.canvas, str(args.out / "ui.png"))

    board_surface = screen.board_view.board_renderer.surface
    if board_surface is not None:
        size = (
            int(board_surface.get_width() * args.board_scale),
            int(board_surface.get_height() * args.board_scale),
        )
        scaled = pygame.transform.smoothscale(board_surface, size)
        pygame.image.save(scaled, str(args.out / "board.png"))
        print(f"board world size: {board_surface.get_size()}")

    print(f"ui size: {app.canvas.get_size()}  ->  {args.out}")
    print(f"tiles: {len(state.board.tiles)}  props: {len(state.board.props)}  "
          f"rivers: {len(state.board.rivers)}")
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
