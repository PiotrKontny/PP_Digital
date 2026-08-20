"""
Network services.

The layer between the protocol and the screens.  A screen asks a service what
is happening and tells it what the player did; it never sees a socket, a
message or a peer id.  That is what keeps ``ui/`` free of networking and
``net/`` free of pygame, and it is the seam that let the entire transport be
replaced in this stage without a single screen changing shape.

Two services, mirroring the two things a player can do:

* :class:`HostService` — asks the server to open a room and becomes its host.
* :class:`ClientService` — joins an existing room by its code.

**Neither of them is a server.**  That is the change this stage exists for.
The host has one extra button (Start) and one extra permission (table
settings); its connection is the same outbound WebSocket as everybody else's,
which is why a host behind a home router now works at all.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..cards.loader import ContentLibrary
from ..engine.game_state import GameState
from .client import GameClient
from .config import NetworkConfig, current as network_config, normalise_url
from .lobby import DEFAULT_NICKNAME, LobbyState
from .session import NetworkSession, NetworkStats
from .transport import ConnectionState, Transport, TransportError


class NetworkService:
    """What a screen may ask of either role."""

    def __init__(self, client: GameClient) -> None:
        self.client = client
        self.notices: List[str] = []

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def mode(self) -> str:
        """"host" or "client".

        Only ever a *role in the lobby*.  It used to mean "this machine runs
        the networking", and every place that assumed so is gone.
        """
        return "host" if self.client.is_host else "client"

    @property
    def is_host(self) -> bool:
        return self.client.is_host

    @property
    def peer_id(self) -> str:
        return self.client.peer_id or ""

    #: Kept because the lobby screen asks both services the same question.
    local_peer_id = peer_id

    @property
    def room_code(self) -> str:
        return self.client.room_code

    @property
    def server_url(self) -> str:
        return self.client.transport.description

    @property
    def host(self) -> str:
        return self.client.config.describe_target()

    # ── state ────────────────────────────────────────────────────────────────
    @property
    def lobby_state(self) -> LobbyState:
        return self.client.lobby_state

    @property
    def session(self) -> Optional[NetworkSession]:
        return self.client.session

    @property
    def state(self) -> Optional[GameState]:
        return self.client.state

    @property
    def in_game(self) -> bool:
        return self.client.in_game

    @property
    def stats(self) -> NetworkStats:
        return self.client.stats

    @property
    def connection_state(self) -> ConnectionState:
        return self.client.connection_state

    @property
    def reconnecting(self) -> bool:
        return self.client.reconnecting

    @property
    def disconnected(self) -> Optional[str]:
        return self.client.disconnected

    @property
    def error(self) -> Optional[str]:
        return self.client.error

    @error.setter
    def error(self, value: Optional[str]) -> None:
        self.client.error = value

    # ── actions ──────────────────────────────────────────────────────────────
    def set_nickname(self, nickname: str) -> None:
        self.client.set_nickname(nickname)

    def set_character(self, character: str) -> None:
        self.client.set_character(character)

    def set_ready(self, ready: bool = True) -> None:
        self.client.set_ready(ready)

    def set_settings(self, **settings: Any) -> None:
        self.client.set_settings(**settings)

    def close_room(self, reason: str = "") -> None:
        self.client.close_room(reason)

    def leave_room(self) -> None:
        """This player is leaving the room deliberately.

        What every "go back" and "quit" in the interface calls, and the only
        thing they need to know about the difference between choosing to leave
        and losing a connection.  See
        :meth:`~pedzacy_piotrek.net.client.GameClient.leave_room`.
        """
        self.client.leave_room()

    @property
    def departed(self) -> bool:
        return self.client.departed

    def start_game(self, library: Optional[ContentLibrary] = None
                   ) -> Optional[NetworkSession]:
        """Ask the server to begin.

        Returns ``None`` and leaves :attr:`error` set when the server will not
        — and also when it simply has not answered yet, because the answer is a
        broadcast that arrives on a later frame.  The lobby screen therefore
        watches :attr:`session` rather than this return value, exactly as a
        client already did; there is now one code path instead of two.
        """
        if library is not None:
            self.client._library = library
        problem = self.lobby_state.validate()
        if problem:
            self.client.error = problem
            return None
        self.client.error = None
        self.client.start_game()
        return self.client.session

    # ── the hidden identity ──────────────────────────────────────────────────
    @property
    def identity_request(self) -> List[dict]:
        """The colours this player is being asked to choose between.

        Empty on every machine except Piotrek's, and empty again the moment he
        has chosen.  The screens ask this rather than the client so nothing
        above this layer has to know a message type.
        """
        return self.client.identity_request

    @property
    def identity_pawn(self) -> str:
        return self.client.identity_pawn

    def choose_identity(self, pawn_id: str) -> None:
        self.client.choose_identity(pawn_id)

    def return_to_lobby(self) -> None:
        self.client.return_to_lobby()

    def poll(self, library: Optional[ContentLibrary] = None) -> None:
        self.client.poll(library)
        self.notices.extend(self.client.drain_notices())

    def notify(self, text: str) -> None:
        self.notices.append(text)

    def drain_notices(self) -> List[str]:
        notices, self.notices = self.notices, []
        return notices

    def close(self) -> None:
        self.client.close()


class HostService(NetworkService):
    """Opened the room; holds the Start button and the table settings."""

    def __init__(self, nickname: str = DEFAULT_NICKNAME,
                 config: Optional[NetworkConfig] = None,
                 transport: Optional[Transport] = None,
                 library: Optional[ContentLibrary] = None,
                 url: str = "") -> None:
        config = config or network_config()
        transport = transport or connect_transport(url or config.server_url, config)
        client = GameClient(transport, nickname, config, library)
        super().__init__(client)
        client.create_lobby()


class ClientService(NetworkService):
    """Joined somebody else's room with its code."""

    def __init__(self, code: str, nickname: str = DEFAULT_NICKNAME,
                 config: Optional[NetworkConfig] = None,
                 transport: Optional[Transport] = None,
                 library: Optional[ContentLibrary] = None,
                 url: str = "") -> None:
        config = config or network_config()
        transport = transport or connect_transport(url or config.server_url, config)
        client = GameClient(transport, nickname, config, library)
        super().__init__(client)
        client.join_lobby(code)


def connect_transport(url: str, config: Optional[NetworkConfig] = None
                      ) -> Transport:
    """Open a transport to a server, or raise a readable
    :class:`~pedzacy_piotrek.net.transport.TransportError`.

    Returns *immediately*: the connection is established in the background and
    the menu shows its progress.  A blocking connect here is what used to make
    the game freeze for five seconds when somebody typed an address wrong.
    """
    from .websocket import WebSocketTransport

    config = config or network_config()
    target = normalise_url(url, config.server.port)
    if not target:
        raise TransportError("Nie podano adresu serwera")
    return WebSocketTransport(target, config)
