"""
The hub: connections in, messages out.

Everything the server decides happens here or in :mod:`.room`, and neither file
imports asyncio, websockets or sockets.  The whole server is a function from
(connection, message) to a list of (connection, message) — which is why it can
be driven by a test in a loop, by an in-process transport pair, or by the
asyncio layer in :mod:`.app`, with identical behaviour.

TWO IDENTITIES, AND THEY ARE NOT THE SAME THING:

* a **connection id** is one socket.  It dies when the WiFi does.
* a **peer id** is one player.  It survives a reconnection, because that is
  what a reconnection *is* — the same player arriving on a new socket.

The two are joined by a **resume token**, minted on the first ``HELLO`` and
presented on every later one.  The token is the secret; the peer id is public
and appears in the lobby.  Without that separation "reconnect" can only mean
"join again as a stranger", which is what the previous build did and why a
dropped player lost their hand.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..cards.loader import ContentLibrary
from ..net.config import PROTOCOL_VERSION, NetworkConfig
from ..net.lobby import DEFAULT_NICKNAME, clean_nickname, clean_room_code
from ..net.protocol import Message, MessageType, ProtocolError
from .registry import RoomRegistry
from .room import Room

#: An outbound message addressed to a *connection*.
Outbound = Tuple[str, Message]


@dataclass
class Identity:
    """One player, across however many sockets they use."""

    peer_id: str
    resume_token: str
    nickname: str = DEFAULT_NICKNAME
    room_code: str = ""
    #: The connection currently carrying this player, if any.
    connection: Optional[str] = None
    last_seen: float = 0.0


@dataclass
class Connection:
    """One socket, from the hub's point of view."""

    cid: str
    peer_id: Optional[str] = None
    address: str = ""
    opened_at: float = 0.0
    #: Messages received before ``HELLO``; a client that skips the handshake is
    #: told so rather than being quietly ignored.
    greeted: bool = False


class ServerHub:
    """The whole server, minus the sockets."""

    def __init__(self, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None,
                 clock=time.monotonic) -> None:
        self.config = config or NetworkConfig()
        self._clock = clock
        self.rooms = RoomRegistry(self.config, library, clock=clock)
        self.connections: Dict[str, Connection] = {}
        self.identities: Dict[str, Identity] = {}       # peer id → identity
        self._by_token: Dict[str, Identity] = {}
        #: Counters for the operator, not for the game.
        self.messages_in = 0
        self.messages_out = 0

    # ── connections ──────────────────────────────────────────────────────────
    def connect(self, cid: str, address: str = "") -> List[Outbound]:
        """A socket opened.  Nothing happens until it says ``HELLO``."""
        self.connections[cid] = Connection(cid=cid, address=address,
                                           opened_at=self._clock())
        return []

    def disconnect(self, cid: str) -> List[Outbound]:
        """A socket closed.  The player keeps their seat for the grace period."""
        connection = self.connections.pop(cid, None)
        if connection is None or connection.peer_id is None:
            return []
        identity = self.identities.get(connection.peer_id)
        if identity is None or identity.connection != cid:
            # A newer socket already took over this player; the old one closing
            # is bookkeeping, not a disconnection.
            return []
        identity.connection = None
        identity.last_seen = self._clock()
        room = self.rooms.get(identity.room_code)
        if room is None:
            return []
        return self._address(room.mark_absent(identity.peer_id))

    # ── the main entry point ─────────────────────────────────────────────────
    def receive(self, cid: str, raw: str | bytes | Message) -> List[Outbound]:
        """Handle one message.  Never raises; a bad message costs its sender."""
        self.messages_in += 1
        try:
            message = raw if isinstance(raw, Message) else Message.decode(raw)
        except ProtocolError as exc:
            return self._out([(cid, Message.error(str(exc)))])

        connection = self.connections.get(cid)
        if connection is None:
            return []

        if message.type is MessageType.HELLO:
            return self._out(self._hello(connection, message))
        if message.type is MessageType.PING:
            stamp = message.payload.get("t", 0.0)
            return self._out([(cid, Message.pong(
                stamp if isinstance(stamp, (int, float)) else 0.0))])
        if message.type is MessageType.PONG:
            return []
        if connection.peer_id is None:
            # A message that overtook the handshake.  The client is supposed to
            # make this impossible (net/client._send holds everything until it
            # has been welcomed), so reaching here means an old build or a bug —
            # neither of which is worth throwing a player out of the game for.
            # It used to be fatal, which turned one mis-ordered message into
            # "you are disconnected" and showed the player the raw protocol
            # text.  Dropping the message costs one action; dropping the player
            # costs the match.
            return self._out([(cid, Message.error(
                "Trwa łączenie z serwerem — spróbuj jeszcze raz"))])

        identity = self.identities[connection.peer_id]
        identity.last_seen = self._clock()
        return self._out(self._route(connection, identity, message))

    # ── handshake ────────────────────────────────────────────────────────────
    def _hello(self, connection: Connection,
               message: Message) -> List[Tuple[str, Message]]:
        if message.version != PROTOCOL_VERSION:
            return [(connection.cid, Message.error(
                f"Niezgodna wersja gry (serwer {PROTOCOL_VERSION}, "
                f"ty {message.version}) — zaktualizuj grę", fatal=True))]

        nickname = clean_nickname(str(message.payload.get("nickname", "")))
        token = str(message.payload.get("resume_token", "") or "")
        identity = self._by_token.get(token) if token else None

        if identity is None:
            identity = Identity(
                peer_id=f"p-{secrets.token_hex(4)}",
                resume_token=secrets.token_urlsafe(16),
                nickname=nickname,
            )
            self.identities[identity.peer_id] = identity
            self._by_token[identity.resume_token] = identity
            connection.peer_id = identity.peer_id
            identity.connection = connection.cid
            identity.last_seen = self._clock()
            connection.greeted = True
            return [(connection.cid, Message.welcome(identity.peer_id,
                                                     identity.resume_token))]

        # A returning player.  Same peer id, same seat, same hand.
        previous = identity.connection
        identity.connection = connection.cid
        identity.last_seen = self._clock()
        identity.nickname = nickname or identity.nickname
        connection.peer_id = identity.peer_id
        connection.greeted = True

        out: List[Tuple[str, Message]] = [
            (connection.cid, Message.welcome(identity.peer_id,
                                             identity.resume_token))
        ]
        if previous and previous != connection.cid and previous in self.connections:
            # Two sockets claiming one player: the newer one wins and the older
            # is told why, rather than both being fed the same game.
            out.append((previous, Message.bye("Zalogowano się z innego miejsca")))
            self.connections.pop(previous, None)

        room = self.rooms.get(identity.room_code)
        if room is not None and not room.closed:
            accepted, reason = room.reconnect(identity.peer_id)
            if accepted:
                out.extend(room.catch_up(identity.peer_id))
            else:
                identity.room_code = ""
                out.append((connection.cid, Message.error(reason)))
        return out

    # ── routing ──────────────────────────────────────────────────────────────
    def _route(self, connection: Connection, identity: Identity,
               message: Message) -> List[Tuple[str, Message]]:
        kind = message.type
        if kind is MessageType.CREATE_LOBBY:
            return self._create_lobby(identity, message)
        if kind is MessageType.JOIN_LOBBY:
            return self._join_lobby(identity, message)

        room = self.rooms.get(identity.room_code)
        if kind is MessageType.LEAVE_LOBBY or kind is MessageType.BYE:
            return self._leave(identity)
        if room is None:
            return [(connection.cid, Message.error("Nie jesteś w żadnym pokoju"))]

        if kind is MessageType.SET_NICKNAME:
            identity.nickname = clean_nickname(
                str(message.payload.get("nickname", "")))
            return room.set_nickname(identity.peer_id, identity.nickname)
        if kind is MessageType.SET_CHARACTER:
            return room.set_character(identity.peer_id,
                                      str(message.payload.get("character", "")))
        if kind is MessageType.PLAYER_READY:
            return room.set_ready(identity.peer_id,
                                  bool(message.payload.get("ready", True)))
        if kind is MessageType.SET_SETTINGS:
            return room.set_settings(identity.peer_id, message.payload)
        if kind is MessageType.START_GAME:
            seed = message.payload.get("seed")
            return room.start(identity.peer_id,
                              int(seed) if isinstance(seed, int) else None)
        if kind is MessageType.COMMAND:
            return room.submit(identity.peer_id, message.payload)
        if kind is MessageType.REQUEST_SYNC:
            return room.resync(identity.peer_id)
        return [(connection.cid, Message.error(
            f"Serwer nie obsługuje wiadomości „{kind.value}”"))]

    def _create_lobby(self, identity: Identity,
                      message: Message) -> List[Tuple[str, Message]]:
        if identity.room_code:
            self._leave(identity)
        room = self.rooms.create()
        if room is None:
            return [(identity.connection or "", Message.error(
                "Serwer jest zajęty — wszystkie pokoje są w użyciu"))]
        nickname = clean_nickname(str(message.payload.get("nickname", ""))
                                  or identity.nickname)
        identity.nickname = nickname
        accepted, reason = room.join(identity.peer_id, nickname, as_host=True)
        if not accepted:
            return [(identity.connection or "", Message.error(reason))]
        identity.room_code = room.code
        return room.broadcast_lobby()

    def _join_lobby(self, identity: Identity,
                    message: Message) -> List[Tuple[str, Message]]:
        code = clean_room_code(str(message.payload.get("code", "")))
        room = self.rooms.get(code)
        if room is None or room.closed:
            return [(identity.connection or "", Message.error(
                f"Nie ma pokoju o kodzie {code or '—'}"))]
        if identity.room_code and identity.room_code != code:
            self._leave(identity)
        nickname = clean_nickname(str(message.payload.get("nickname", ""))
                                  or identity.nickname)
        identity.nickname = nickname
        accepted, reason = room.join(identity.peer_id, nickname)
        if not accepted:
            return [(identity.connection or "", Message.error(reason))]
        identity.room_code = room.code
        out = room.broadcast_lobby()
        if room.started:
            out.extend(room.catch_up(identity.peer_id))
        return out

    def _leave(self, identity: Identity) -> List[Tuple[str, Message]]:
        room = self.rooms.get(identity.room_code)
        identity.room_code = ""
        if room is None:
            return []
        out = room.leave(identity.peer_id)
        if not room.members:
            self.rooms.close(room.code)
        return out

    def close_room(self, code: str,
                   reason: str = "Pokój został zamknięty") -> List[Outbound]:
        """Shut a room down and tell everybody in it why.

        Goes through the routing layer rather than the registry directly, so
        the people in the room are actually told; closing it underneath them
        would leave every client waiting for a server that had already
        forgotten them.
        """
        outbound = self.rooms.close(code, reason)
        for identity in self.identities.values():
            if identity.room_code == code:
                identity.room_code = ""
        return self._out(outbound)

    # ── periodic work ────────────────────────────────────────────────────────
    def tick(self) -> List[Outbound]:
        """Grace periods and abandoned rooms.  Called on a timer by :mod:`.app`.

        Kept separate from message handling so nothing depends on somebody
        happening to send something: an empty room must go away even when
        every client has gone quiet, which is precisely when it is empty.
        """
        out: List[Tuple[str, Message]] = []
        for room in list(self.rooms):
            out.extend(room.expire_absentees())
        for code in self.rooms.prune():
            for identity in self.identities.values():
                if identity.room_code == code:
                    identity.room_code = ""
        return self._out(out)

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _address(self, outbound: List[Tuple[str, Message]]
                 ) -> List[Outbound]:
        return self._out(outbound)

    def _out(self, outbound: List[Tuple[str, Message]]) -> List[Outbound]:
        """Translate peer ids into connection ids and drop the unreachable.

        Room code addresses *players*; the socket layer needs *connections*.
        Somebody in the grace period has no connection, and their messages are
        dropped here rather than being queued for a socket that may never come
        back — the state sync they get on their return is more current than
        anything held for them would have been.
        """
        resolved: List[Outbound] = []
        for target, message in outbound:
            if not target:
                continue
            if target in self.connections:
                resolved.append((target, message))
                continue
            identity = self.identities.get(target)
            if identity is not None and identity.connection:
                resolved.append((identity.connection, message))
        self.messages_out += len(resolved)
        return resolved

    # ── introspection, for the operator ──────────────────────────────────────
    def describe(self) -> Dict[str, object]:
        return {
            "connections": len(self.connections),
            "players": sum(1 for i in self.identities.values() if i.connection),
            "rooms": [
                {
                    "code": room.code,
                    "players": len(room.members),
                    "present": len(room.present),
                    "started": room.started,
                    "commands": room.sequence,
                }
                for room in self.rooms
            ],
            "messages_in": self.messages_in,
            "messages_out": self.messages_out,
        }
