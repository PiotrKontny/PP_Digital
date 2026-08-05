"""
Shared harness for the multiplayer tests.

Everything here runs against :class:`InProcessServer` — the real
:class:`ServerHub`, the real rooms, the real messages, with queues where the
sockets would be.  That is a deliberate choice: it makes the whole of
multiplayer testable in milliseconds and deterministically, so the socket layer
is the only thing left that a test cannot reach.  One test in
``test_multiplayer.py`` covers that layer over genuine WebSockets.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.net.config import NetworkConfig
from pedzacy_piotrek.net.service import ClientService, HostService
from pedzacy_piotrek.server.embedded import InProcessServer


def server_config(**server: object) -> NetworkConfig:
    """A configuration with room for several rooms, for the tests that need it."""
    config = NetworkConfig()
    return replace(config, server=replace(config.server,
                                          **{"max_rooms": 4, **server}))


class Table:
    """A server plus the services sitting at it, pumped by hand."""

    def __init__(self, library: ContentLibrary,
                 config: Optional[NetworkConfig] = None) -> None:
        self.library = library
        self.config = config or server_config()
        self.server = InProcessServer(self.config, library)
        self.services: List[object] = []

    # ── membership ───────────────────────────────────────────────────────────
    def host(self, nickname: str = "Kuba") -> HostService:
        service = HostService(nickname, config=self.config,
                              transport=self.server.transport(),
                              library=self.library)
        self.services.append(service)
        self.pump()
        return service

    def join(self, code: str, nickname: str) -> ClientService:
        service = ClientService(code, nickname, config=self.config,
                                transport=self.server.transport(),
                                library=self.library)
        self.services.append(service)
        self.pump()
        return service

    def pump(self, rounds: int = 8) -> None:
        """Let every message in flight arrive and be answered."""
        for _ in range(rounds):
            for service in list(self.services):
                service.poll(self.library)

    def tick(self) -> None:
        self.server.tick()
        self.pump()

    def close(self) -> None:
        for service in self.services:
            try:
                service.close()
            except Exception:
                pass

    # ── convenience ──────────────────────────────────────────────────────────
    def seated(self, *nicknames: str):
        """A host and clients, all ready, with the game not yet started."""
        host = self.host(nicknames[0] if nicknames else "Kuba")
        clients = [self.join(host.room_code, name) for name in nicknames[1:]]
        for client in clients:
            client.set_ready(True)
        self.pump()
        return host, clients

    def playing(self, *nicknames: str):
        host, clients = self.seated(*nicknames)
        host.start_game(self.library)
        self.pump()
        return host, clients

    def by_seat(self, host, clients) -> dict:
        parties = [host, *clients]
        return {service.session.seat: service for service in parties
                if service.session is not None}

    def room(self, code: str):
        return self.server.hub.rooms.get(code)


def playable_card(service, seat: int):
    """A movement card in that seat's hand — discarding one is how a turn passes."""
    player = service.state.player(seat)
    return next(c for c in player.hand if c.deck_id == settings.DECK_MOVEMENT)


def take_a_turn(table: Table, host, clients) -> int:
    """Whoever is active discards a movement card.  Returns the seat that acted."""
    seat = host.state.active_player_index
    actor = table.by_seat(host, clients)[seat]
    card = playable_card(actor, seat)
    actor.session.submit(cmd.DiscardCard(player_index=seat, card_uid=card.uid))
    table.pump()
    return seat


def snapshots(*services) -> List[dict]:
    return [s.state.snapshot() for s in services if s.state is not None]


def all_agree(*services) -> bool:
    shots = snapshots(*services)
    return bool(shots) and all(shot == shots[0] for shot in shots)
