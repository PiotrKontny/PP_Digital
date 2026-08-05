"""
Entry point.

    python -m pedzacy_piotrek            # play
    python -m pedzacy_piotrek --selftest # headless smoke test (CI)
    python -m pedzacy_piotrek --cells 40 --players 4   # skip the menu

The launcher's only job is composition: load content, build the app, wire the
menu to the game.  Everything it touches is injectable, which is what makes the
self-test possible — it is the real application running against a dummy video
driver, not a parallel code path.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from .cards.loader import ContentLibrary
from .config import settings
from .config.settings import RULES, SessionConfig
from .engine.setup import create_game, new_seed
from .net.config import override_server_url
from .net.session import LocalSession
from .ui.app import App
from .ui.game_screen import GameScreen
from .ui.layout import Layout
from .ui.menu import MenuScreen
from .ui.network_screens import HostSetupScreen, JoinScreen, MainMenuScreen


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pedzacy_piotrek", description="Pędzący Piotrek")
    parser.add_argument("--selftest", action="store_true",
                        help="uruchom kilka klatek bez okna i zakończ")
    parser.add_argument("--frames", type=int, default=120,
                        help="liczba klatek dla --selftest")
    parser.add_argument("--players", type=int, default=None,
                        help="pomiń menu i zacznij grę dla N graczy")
    parser.add_argument("--cells", type=int, default=RULES.board_cells_default,
                        help="liczba pól planszy")
    parser.add_argument("--chest", type=int, default=RULES.chest_open_default,
                        help="runda otwarcia skrzyni")
    parser.add_argument("--seed", type=int, default=0,
                        help="ziarno losowości (0 = losowe)")
    parser.add_argument("--doubles", type=int, default=None,
                        help="jak często pola podwójne, w procentach (0-100)")
    parser.add_argument("--host", action="store_true",
                        help="otwórz od razu ekran zakładania pokoju")
    parser.add_argument("--join", action="store_true",
                        help="otwórz od razu ekran dołączania")
    parser.add_argument("--server", type=str, default=None,
                        help="adres serwera gry, np. wss://piotrek.example.com")
    parser.add_argument("--serve", action="store_true",
                        help="uruchom serwer gry zamiast gry (bez okna)")
    parser.add_argument("--net-debug", action="store_true",
                        help="włącz panel diagnostyki sieci (F3 przełącza)")
    parser.add_argument("--fullscreen", action="store_true",
                        help="uruchom na pełnym ekranie")
    parser.add_argument("--size", type=str, default=None,
                        help="rozmiar okna, np. 1920x1080")
    return parser.parse_args(argv)


def start_game(app: App, library: ContentLibrary, config: SessionConfig) -> None:
    """Straight into a game on this machine, skipping the menus."""
    state = create_game(config, library)
    session = LocalSession(state)
    app.replace(GameScreen(app, session, library=library))


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.serve:
        # The same process can be the server, which is what makes trying
        # multiplayer out a one-line affair before anything is deployed.
        from .server.app import main as serve

        argv2 = ["--verbose"] if args.net_debug else []
        return serve(argv2)

    if args.selftest:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    size = None
    if args.size:
        try:
            width, height = (int(part) for part in args.size.lower().split("x"))
            size = (width, height)
        except ValueError:
            print(f"Nieprawidłowy rozmiar okna: {args.size!r}")
            return 2

    if args.server:
        override_server_url(args.server)

    library = ContentLibrary.load()
    app = App(
        Layout(), headless=args.selftest, size=size,
        fullscreen=args.fullscreen and not args.selftest,
    )

    settings.NETWORK_DEBUG = args.net_debug

    if args.players or args.selftest:
        config = SessionConfig(
            num_players=args.players or RULES.max_players,
            board_cells=args.cells,
            chest_open_round=args.chest,
            character_choices=[],
            double_frequency=(
                None if args.doubles is None
                else max(0, min(100, args.doubles)) / 100.0
            ),
            seed=args.seed or new_seed(),
        )
        start_game(app, library, config)
    elif args.host:
        app.push(MainMenuScreen(app, library))
        app.push(HostSetupScreen(app, library))
        if args.server:
            app.screen.server.value = args.server
    elif args.join:
        app.push(MainMenuScreen(app, library))
        app.push(JoinScreen(app, library))
        if args.server:
            app.screen.server.value = args.server
    else:
        app.push(MainMenuScreen(app, library))

    app.run(max_frames=args.frames if args.selftest else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
