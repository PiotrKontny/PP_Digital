"""
The client half of the protocol.

:class:`GameClient` is the state machine that sits between a
:class:`~pedzacy_piotrek.net.transport.Transport` and the rest of the game.  It
knows the message vocabulary and nothing about pygame; the screens talk to
:mod:`pedzacy_piotrek.net.service`, which is a thin layer on top of this one.

It is responsible for four things, in order of how often they matter:

**1. Turning accepted commands into game state.**  Every ``COMMAND_ACCEPTED``
is applied to the local replica with ``local=False`` and its fingerprint
compared against the server's.  A mismatch is not survivable and is not
guessed at: the client asks for a full ``STATE_SYNC`` and rebuilds.  This is
the whole desync story, and it is four lines, because the state is a pure
function of the seed and the command log.

**2. Reconnection.**  The transport reconnects the socket; this class
reconnects the *player*.  It watches ``Transport.generation`` — which only ever
increases on a fresh connection — re-sends ``HELLO`` with the resume token it
was given, and lets the server put it back in its seat and catch it up.  The
game keeps drawing throughout.

**3. The lobby mirror.**  A read-only copy of the server's document, replaced
wholesale whenever ``LOBBY_STATE`` arrives.  Local edits are never applied
optimistically: the interface asks and shows what comes back, which is why a
character that is already taken cannot appear to have been claimed.

**4. Saying what happened, in Polish.**  Every failure that reaches the player
arrives here as a message and leaves as a notice or a ``disconnected`` reason.
Nothing above this layer catches a networking exception, because none is
allowed to escape it.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Mapping, Optional

from ..cards.loader import ContentLibrary
from ..config.settings import SessionConfig
from ..engine import commands as cmd
from ..engine import events as ev
from ..engine.game_state import GameState
from ..engine.setup import create_game
from .config import NetworkConfig
from .lobby import DEFAULT_NICKNAME, LobbyState, clean_room_code
from .protocol import Message, MessageType, fingerprint_of
from .session import NetworkSession, NetworkStats
from .transport import ConnectionState, Transport


class GameClient:
    """One player's connection to a game server."""

    def __init__(self, transport: Transport, nickname: str = DEFAULT_NICKNAME,
                 config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None) -> None:
        self.transport = transport
        self.config = config or NetworkConfig()
        self.nickname = nickname or DEFAULT_NICKNAME
        self._library = library

        self.peer_id: Optional[str] = None
        self.resume_token: str = ""
        self.lobby_state = LobbyState()
        self.session: Optional[NetworkSession] = None
        self.state: Optional[GameState] = None
        self.seat: Optional[int] = None

        #: Set when the player is out of the game for good, with the reason.
        #: A temporary drop does NOT set it — that is what reconnection is for.
        self.disconnected: Optional[str] = None
        self.error: Optional[str] = None
        self.notices: List[str] = []
        self.stats = NetworkStats(mode="client")
        self.stats.server = transport.description

        #: What we asked for but have not yet been given, replayed after a
        #: reconnection so a click that happened during a drop is not lost.
        self._intent: Optional[Message] = None
        self._greeted_generation: int = 0
        self._sequence: int = 0
        self._sent_counter: int = 0
        self._last_sync_request: float = 0.0

    # ── properties the interface asks about ──────────────────────────────────
    @property
    def library(self) -> ContentLibrary:
        if self._library is None:
            self._library = ContentLibrary.load()
        return self._library

    @property
    def in_game(self) -> bool:
        return self.session is not None

    @property
    def is_host(self) -> bool:
        return bool(self.peer_id) and self.lobby_state.is_host(self.peer_id)

    @property
    def room_code(self) -> str:
        return self.lobby_state.code

    @property
    def connection_state(self) -> ConnectionState:
        return self.transport.state

    @property
    def reconnecting(self) -> bool:
        return self.transport.state is ConnectionState.RECONNECTING

    def notify(self, text: str) -> None:
        self.notices.append(text)

    def drain_notices(self) -> List[str]:
        notices, self.notices = self.notices, []
        return notices

    # ── outgoing ─────────────────────────────────────────────────────────────
    def _send(self, message: Message) -> None:
        self.transport.send(message)
        self.stats.note_sent(message.type.value)

    def create_lobby(self) -> None:
        self._intent = Message.create_lobby(self.nickname)
        self._flush_intent()

    def join_lobby(self, code: str) -> None:
        code = clean_room_code(code)
        self._intent = Message.join_lobby(code, self.nickname)
        self._flush_intent()

    def _flush_intent(self) -> None:
        """Send the pending create/join, but only once the server knows us.

        A screen calls ``create_lobby`` the moment the player clicks, which is
        usually before the connection has finished its handshake — and a room
        request from a peer the server has not greeted is refused, not queued.
        Holding the intent here means the click is honoured either way, and it
        is the same mechanism that replays it after a reconnection.
        """
        if self._intent is None or self.peer_id is None:
            return
        if not self.transport.connected:
            return
        self._send(self._intent)

    def leave_lobby(self) -> None:
        self._intent = None
        self._send(Message(MessageType.LEAVE_LOBBY))

    def set_nickname(self, nickname: str) -> None:
        self.nickname = nickname or DEFAULT_NICKNAME
        self._send(Message(MessageType.SET_NICKNAME, {"nickname": self.nickname}))

    def set_character(self, character: str) -> None:
        self._send(Message(MessageType.SET_CHARACTER, {"character": character}))

    def set_ready(self, ready: bool = True) -> None:
        self._send(Message(MessageType.PLAYER_READY, {"ready": bool(ready)}))

    def set_settings(self, **settings: Any) -> None:
        self._send(Message(MessageType.SET_SETTINGS, dict(settings)))

    def start_game(self) -> None:
        self._send(Message(MessageType.START_GAME))

    def request_sync(self) -> None:
        """Ask for the whole match again.  Rate-limited, because a client that
        is confused would otherwise ask once per frame and drown the server."""
        now = time.monotonic()
        if now - self._last_sync_request < 1.0:
            return
        self._last_sync_request = now
        self.stats.resyncs += 1
        self._send(Message(MessageType.REQUEST_SYNC))

    def submit_command(self, command: cmd.Command) -> None:
        self._sent_counter += 1
        self._send(Message.command(command.to_dict(), self._sent_counter))

    # ── the frame tick ───────────────────────────────────────────────────────
    def poll(self, library: Optional[ContentLibrary] = None) -> None:
        """Called once a frame.  Never blocks and never raises."""
        if library is not None:
            self._library = library
        if self.disconnected is not None:
            return

        self._check_connection()
        for message in self.transport.poll():
            try:
                self._handle(message)
            except Exception as exc:  # pragma: no cover - defensive
                self.notify(f"Błąd przetwarzania wiadomości: {exc}")
        self._refresh_stats()

    def _check_connection(self) -> None:
        """Notice the transport coming back, and re-announce ourselves.

        The socket reconnecting is not the same as the *player* reconnecting:
        the server has never heard of this new connection.  Sending ``HELLO``
        with the resume token is what turns one into the other.
        """
        transport = self.transport
        if transport.generation != self._greeted_generation and transport.connected:
            first = self._greeted_generation == 0
            self._greeted_generation = transport.generation
            if not first:
                self.stats.reconnects += 1
                self.notify("Połączenie odzyskane — synchronizuję…")
            # The intent is replayed from ``_on_welcome`` once the server has
            # greeted this connection; sending it now would race the handshake.
            self._send(Message.hello(self.nickname, self.resume_token))
            return

        if transport.state is ConnectionState.CLOSED and self.disconnected is None:
            self._drop(transport.error or "Utracono połączenie z serwerem")

    def _refresh_stats(self) -> None:
        stats, transport = self.stats, self.transport
        stats.state = transport.state
        stats.ping_ms = getattr(transport, "latency_ms", None)
        stats.room = self.lobby_state.code
        stats.seat = self.seat
        stats.players = self.lobby_state.player_count
        stats.sequence = self._sequence
        stats.mode = "host" if self.is_host else "client"

    # ── incoming ─────────────────────────────────────────────────────────────
    def _handle(self, message: Message) -> None:
        self.stats.note_received(message.type.value)
        handler = self._HANDLERS.get(message.type)
        if handler is not None:
            handler(self, message)

    def _on_welcome(self, message: Message) -> None:
        self.peer_id = str(message.payload.get("peer_id", "")) or self.peer_id
        token = str(message.payload.get("resume_token", ""))
        if token:
            self.resume_token = token
        # Whatever the player asked for while the handshake was still in
        # flight happens now, in the order they asked for it.
        self._flush_intent()

    def _on_lobby_state(self, message: Message) -> None:
        self.lobby_state = LobbyState.from_dict(message.payload)
        seat = self.lobby_state.seat_of(self.peer_id or "")
        if seat is not None:
            self.seat = seat.seat
        if self.lobby_state.code:
            self._intent = None     # we are in; nothing left to retry

    def _on_game_start(self, message: Message) -> None:
        self._build_match(message.payload.get("config"),
                          message.payload.get("seats"))

    def _on_state_sync(self, message: Message) -> None:
        """Rebuild the match from scratch and replay everything that happened.

        Used for exactly two things: arriving after the match began, and coming
        back after a drop.  Both are the same operation, which is why there is
        one code path for them.
        """
        payload = message.payload
        session = self._build_match(payload.get("config"), payload.get("seats"))
        if session is None:
            return
        log = payload.get("log") or []
        replayed = 0
        for raw in log:
            try:
                command = cmd.Command.from_dict(dict(raw))
            except (ValueError, TypeError, KeyError):
                continue
            session.state.apply(command, local=False)
            replayed += 1
        self._sequence = int(payload.get("seq", replayed) or replayed)
        expected = str(payload.get("fingerprint", ""))
        mine = fingerprint_of(session.state.snapshot())
        if expected and mine != expected:
            # Replaying the server's own log did not reproduce the server's
            # state.  That is a rules bug, not a networking one, and pretending
            # otherwise would hide it; say so rather than looping on resyncs.
            self.notify("Uwaga: stan gry różni się od serwera — zgłoś to")
        else:
            self.notify("Gra zsynchronizowana")
        # The replay was silent — the view rebuilds from the state rather than
        # from events, so a returning player does not watch twenty animations.
        self.bus_reset()

    def _build_match(self, raw_config: Any,
                     raw_seats: Any) -> Optional[NetworkSession]:
        if not isinstance(raw_config, Mapping):
            return None
        seats = {str(k): int(v) for k, v in (raw_seats or {}).items()}
        my_seat = seats.get(self.peer_id or "", self.seat or 0)
        config = _session_config(dict(raw_config), my_seat)

        state = create_game(config, self.library)
        for peer_id, index in seats.items():
            seat = self.lobby_state.seat_of(peer_id)
            player = state.player(index)
            if player is not None:
                player.owner_id = peer_id
                if seat is not None:
                    player.name = seat.nickname
        self.seat = my_seat
        self.state = state

        if self.session is None:
            self.session = NetworkSession(state, self.submit_command,
                                          seat=my_seat, stats=self.stats)
        else:
            self.session.replace_state(state)
            self.session.seat = my_seat
        self.lobby_state.started = True
        return self.session

    def _on_command_accepted(self, message: Message) -> None:
        session = self.session
        if session is None:
            self.request_sync()
            return
        payload = message.payload
        sequence = int(payload.get("seq", 0) or 0)
        if sequence and sequence <= self._sequence:
            return                      # already applied; a duplicate delivery
        if sequence and sequence != self._sequence + 1:
            # A gap means something was missed, almost certainly during a drop.
            # Guessing at the missing action is not an option; ask for the log.
            self.request_sync()
            return
        try:
            command = cmd.Command.from_dict(dict(payload.get("command") or {}))
        except (ValueError, TypeError, KeyError):
            self.request_sync()
            return

        session.apply_authoritative(command)
        self._sequence = sequence or self._sequence + 1

        expected = str(payload.get("fingerprint", ""))
        if expected and fingerprint_of(session.state.snapshot()) != expected:
            self.notify("Rozjazd stanu gry — pobieram od serwera…")
            self.request_sync()

    def _on_command_rejected(self, message: Message) -> None:
        reason = str(message.payload.get("reason", "Odrzucone przez serwer"))
        if self.session is not None:
            self.session.reject(reason, str(message.payload.get("kind", "")))
        else:
            self.notify(reason)

    def _on_choice_required(self, message: Message) -> None:
        """The action is legal but the engine wants a decision first.

        Applied locally rather than being turned into an event by hand: the
        engine emits exactly the ``ChoiceRequired`` the interface already knows
        how to draw, and — because a choice changes nothing — the replica stays
        identical to the server's.
        """
        session = self.session
        if session is None:
            return
        try:
            command = cmd.Command.from_dict(dict(message.payload.get("command") or {}))
        except (ValueError, TypeError, KeyError):
            return
        session.note_answer()
        session.bus.emit_all(session.state.apply(command, local=False))

    def _on_player_disconnected(self, message: Message) -> None:
        name = str(message.payload.get("name", "Gracz"))
        grace = float(message.payload.get("grace", 0) or 0)
        if grace > 0:
            self.notify(f"{name} stracił połączenie — czekamy "
                        f"{int(grace // 60)} min")
        else:
            self.notify(f"{name} opuścił grę")

    def _on_player_reconnected(self, message: Message) -> None:
        self.notify(f"{message.payload.get('name', 'Gracz')} wrócił do gry")

    def _on_error(self, message: Message) -> None:
        reason = str(message.payload.get("reason", "Błąd"))
        self.error = reason
        if message.payload.get("fatal"):
            self._drop(reason)
        else:
            self.notify(reason)

    def _on_bye(self, message: Message) -> None:
        self._drop(str(message.payload.get("reason", "Serwer zakończył połączenie")))

    def _on_match_ended(self, message: Message) -> None:
        self._drop(str(message.payload.get("reason", "Gra została zakończona")))

    _HANDLERS: Dict[MessageType, Callable[["GameClient", Message], None]] = {}

    # ── shutting down ────────────────────────────────────────────────────────
    def _drop(self, reason: str) -> None:
        self.disconnected = reason
        self.error = reason
        self.stats.state = ConnectionState.CLOSED
        self.transport.close()

    def close(self) -> None:
        if self.transport.alive:
            self._send(Message.bye("Gracz wyszedł"))
        self.transport.close()

    def bus_reset(self) -> None:
        """Hook for the view after a resync.  The bus itself is left alone.

        Subscribers survive: the screen is the same screen, and it will redraw
        from the new state on the next frame.
        """
        return None


GameClient._HANDLERS = {
    MessageType.WELCOME: GameClient._on_welcome,
    MessageType.LOBBY_STATE: GameClient._on_lobby_state,
    MessageType.GAME_START: GameClient._on_game_start,
    MessageType.STATE_SYNC: GameClient._on_state_sync,
    MessageType.COMMAND_ACCEPTED: GameClient._on_command_accepted,
    MessageType.COMMAND_REJECTED: GameClient._on_command_rejected,
    MessageType.CHOICE_REQUIRED: GameClient._on_choice_required,
    MessageType.PLAYER_DISCONNECTED: GameClient._on_player_disconnected,
    MessageType.PLAYER_RECONNECTED: GameClient._on_player_reconnected,
    MessageType.ERROR: GameClient._on_error,
    MessageType.BYE: GameClient._on_bye,
    MessageType.MATCH_ENDED: GameClient._on_match_ended,
}


def _session_config(raw: Dict[str, Any], seat: int) -> SessionConfig:
    """Build a config from the server's payload, tolerating a newer server.

    A field this build has never heard of must cost that field, not the match:
    somebody running yesterday's executable should still be able to play.
    """
    raw["local_seat"] = seat
    try:
        return SessionConfig(**raw)
    except TypeError:
        known = {k: v for k, v in raw.items()
                 if k in SessionConfig.__dataclass_fields__}
        known["local_seat"] = seat
        return SessionConfig(**known)
