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
    def seated(self, *nicknames: str, mods: bool = False):
        """A host and clients, all ready, with the game not yet started.

        Mod Patusa rounds are pushed out of range unless a test asks for them.
        A selection pauses the table until both factions have chosen, so a
        helper that walks a dozen turns would otherwise stop dead in round 3 —
        correctly, but for a reason that has nothing to do with what these
        tests check.  Pass ``mods=True`` to get the real schedule.
        """
        host = self.host(nicknames[0] if nicknames else "Kuba")
        clients = [self.join(host.room_code, name) for name in nicknames[1:]]
        for client in clients:
            client.set_ready(True)
        if not mods:
            host.set_settings(mod_round_first=10_000)
        self.pump()
        return host, clients

    def playing(self, *nicknames: str, colour: str = "", mods: bool = False):
        """A match that has actually begun — identity chosen and all.

        Since stage 17 starting a game leaves the table in ``STARTING`` until
        Piotrek names his colour, and nothing may be played before that.  Every
        test that wants to play a turn therefore goes through this step, which
        is the point: the new phase is exercised by the whole existing suite
        rather than by one test that remembers to.
        """
        host, clients = self.seated(*nicknames, mods=mods)
        host.start_game(self.library)
        self.pump()
        self.choose_identity(host, clients, colour)
        return host, clients

    def starting(self, *nicknames: str, mods: bool = False):
        """A match built but NOT begun: Piotrek has not chosen yet."""
        host, clients = self.seated(*nicknames, mods=mods)
        host.start_game(self.library)
        self.pump()
        return host, clients

    def piotrek(self, host, clients):
        """Whichever service was asked for the hidden colour, if any."""
        for service in [host, *clients]:
            if service.identity_request:
                return service
        return None

    def choose_identity(self, host, clients, colour: str = ""):
        """Answer the identity question the way Piotrek's machine would."""
        service = self.piotrek(host, clients)
        if service is None:
            return None
        chosen = colour or service.identity_request[0]["id"]
        service.choose_identity(chosen)
        self.pump()
        return chosen

    def by_seat(self, host, clients) -> dict:
        parties = [host, *clients]
        return {service.session.seat: service for service in parties
                if service.session is not None}

    def room(self, code: str):
        return self.server.hub.rooms.get(code)


def playable_card(service, seat: int):
    """A movement card in that seat's hand — discarding one is how a turn passes.

    Locked cards are skipped.  Troll and Stańczyk sit in the hand refusing to be
    played or discarded by hand (that refusal is the whole mechanic), so a
    helper that took the first movement card it saw passed or failed depending
    on where the shuffle happened to put them.
    """
    player = service.state.player(seat)
    return next(c for c in player.hand
                if c.deck_id == settings.DECK_MOVEMENT and not c.locked)


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
