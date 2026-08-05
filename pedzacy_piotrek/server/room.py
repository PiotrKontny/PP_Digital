"""
A room: one lobby, and then one match.

This is where the authority actually lives.  The room owns the only
:class:`~pedzacy_piotrek.engine.game_state.GameState` that counts; every client
keeps a replica built from the same seed and fed the same accepted commands,
and the room's copy is the tie-breaker whenever they disagree.

**Deliberately synchronous and free of I/O.**  Nothing in this file knows what
a socket is; ``handle`` takes a message and returns a list of messages to send.
That is what makes the whole of multiplayer — seating, ownership, turn order,
disconnection, grace periods, state sync, desync recovery — testable in
milliseconds without a network, and it is why the asyncio layer above it is
under two hundred lines with no game logic in it at all.

THE COMMAND PIPELINE, which is the heart of the thing:

    client sends COMMAND
        │
        ├─ is this peer seated?            no → COMMAND_REJECTED
        ├─ authorise_remote(cmd, seat)     no → COMMAND_REJECTED   (whose seat,
        │                                                          whose turn)
        ├─ apply(cmd, local=False)
        │     ├─ ActionRejected            → COMMAND_REJECTED (sender only)
        │     ├─ ChoiceRequired            → CHOICE_REQUIRED  (sender only,
        │     │                              nothing changed, nothing logged)
        │     └─ applied                   → append to the log, and
        └────────────────────────────────────  COMMAND_ACCEPTED to everyone,
                                               carrying the new fingerprint.

The client never applies anything it has not been told about.  There is no
optimistic prediction and no rollback: this game hinges on hidden information
and exact card counts, and a client that guessed wrong would have to be told
what it guessed wrong about.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..cards.loader import ContentLibrary
from ..config.settings import RULES, SessionConfig
from ..engine import commands as cmd
from ..engine import events as ev
from ..engine.game_state import GameState
from ..engine.setup import create_game, new_seed
from ..net.config import NetworkConfig
from ..net.lobby import LobbyState, clean_nickname
from ..net.protocol import Message, MessageType, fingerprint_of

#: One outbound message, addressed to a player rather than to a connection.
#: The hub above translates peer ids into whatever it is holding open.
Outbound = Tuple[str, Message]


@dataclass
class RoomMember:
    """A player in this room, present or merely expected.

    ``connected`` going false does not free the seat.  A seat is released only
    when the grace period runs out or the player says they are leaving, because
    the alternative — dropping somebody the instant their WiFi hiccups — is the
    single most annoying thing an online board game can do.
    """

    peer_id: str
    nickname: str
    connected: bool = True
    #: When the connection was lost, for the grace period.  None while present.
    absent_since: Optional[float] = None
    #: Highest command sequence this player has been sent, for reconnection.
    acknowledged: int = 0


class Room:
    """One lobby and the match it becomes."""

    def __init__(self, code: str, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None,
                 clock=time.monotonic) -> None:
        self.code = code
        self.config = config or NetworkConfig()
        self._library = library
        self._clock = clock

        self.lobby = LobbyState(code=code)
        self.members: Dict[str, RoomMember] = {}

        #: The authoritative game.  None until the host starts the match.
        self.state: Optional[GameState] = None
        self.session_config: Optional[SessionConfig] = None
        #: Every accepted command, in the order it was applied.  Seed plus this
        #: list *is* the match — it is what a late joiner and a returning
        #: player are sent, and what makes a second representation of the game
        #: state unnecessary.
        self.command_log: List[Dict[str, Any]] = []
        self.fingerprint: str = ""
        self.closed: bool = False
        self.created_at: float = self._clock()
        self.empty_since: Optional[float] = self.created_at

    # ── properties ───────────────────────────────────────────────────────────
    @property
    def started(self) -> bool:
        return self.state is not None

    @property
    def library(self) -> ContentLibrary:
        if self._library is None:
            self._library = ContentLibrary.load()
        return self._library

    @property
    def present(self) -> List[str]:
        return [m.peer_id for m in self.members.values() if m.connected]

    @property
    def sequence(self) -> int:
        return len(self.command_log)

    def member(self, peer_id: str) -> Optional[RoomMember]:
        return self.members.get(peer_id)

    def seat_of(self, peer_id: str) -> Optional[int]:
        seat = self.lobby.seat_of(peer_id)
        return None if seat is None else seat.seat

    # ── membership ───────────────────────────────────────────────────────────
    def join(self, peer_id: str, nickname: str,
             as_host: bool = False) -> Tuple[bool, str]:
        """Seat somebody.  Returns (accepted, reason-if-not)."""
        if self.closed:
            return False, "Ten pokój został zamknięty"
        if peer_id in self.members:
            return self.reconnect(peer_id)
        if self.started:
            # A match in progress only takes back people it already knows; a
            # stranger cannot be dealt a hand halfway through a game whose
            # hidden information was decided at the start.
            return False, "Gra już się rozpoczęła"
        seat = self.lobby.add_seat(peer_id, nickname, is_host=as_host)
        if seat is None:
            return False, f"Stół jest pełny (maksimum {RULES.max_players} graczy)"
        self.members[peer_id] = RoomMember(peer_id=peer_id, nickname=seat.nickname)
        self.empty_since = None
        return True, ""

    def reconnect(self, peer_id: str) -> Tuple[bool, str]:
        """Somebody who was already here is back."""
        member = self.members.get(peer_id)
        if member is None:
            return False, "Nie ma cię przy tym stole"
        member.connected = True
        member.absent_since = None
        seat = self.lobby.seat_of(peer_id)
        if seat is not None:
            seat.connected = True
        self.empty_since = None
        return True, ""

    def mark_absent(self, peer_id: str) -> List[Outbound]:
        """The connection went away.  Hold the seat and tell the table."""
        member = self.members.get(peer_id)
        if member is None or not member.connected:
            return []
        member.connected = False
        member.absent_since = self._clock()
        seat = self.lobby.seat_of(peer_id)
        if seat is not None:
            seat.connected = False
            seat.ready = False
        if not self.present:
            self.empty_since = self._clock()

        if not self.started:
            # Nobody has anything invested yet, so a seat held open would only
            # block the table.  Free it and renumber.
            return self.leave(peer_id, announce=False) + self.broadcast_lobby()

        grace = self.config.reconnect.grace_period
        name = seat.nickname if seat else member.nickname
        index = seat.seat if seat else -1
        return self.broadcast(
            Message.player_disconnected(peer_id, index, name, grace)
        )

    def leave(self, peer_id: str, announce: bool = True) -> List[Outbound]:
        """A deliberate exit, or a grace period that ran out."""
        member = self.members.pop(peer_id, None)
        seat = self.lobby.seat_of(peer_id)
        if member is None and seat is None:
            return []
        name = seat.nickname if seat else (member.nickname if member else "Gracz")
        index = seat.seat if seat else -1

        if self.started:
            # The seat index is baked into every command already in the log;
            # renumbering now would rewrite history.  The seat stays, empty.
            if seat is not None:
                seat.connected = False
                seat.ready = False
        else:
            self.lobby.remove_seat(peer_id)
            self.lobby.promote_new_host()

        if not self.present:
            self.empty_since = self._clock()
        out: List[Outbound] = []
        if announce and self.started:
            out.extend(self.broadcast(
                Message.player_disconnected(peer_id, index, name, 0.0)
            ))
        if announce and not self.started:
            out.extend(self.broadcast_lobby())
        return out

    def expire_absentees(self) -> List[Outbound]:
        """Release seats whose owner never came back.

        The match carries on without them: their pawn stays where it is and
        their turn is skipped by the ordinary turn rules, which is the least
        disruptive thing that can happen to the other four people.
        """
        if not self.started:
            return []
        grace = self.config.reconnect.grace_period
        now = self._clock()
        out: List[Outbound] = []
        for peer_id, member in list(self.members.items()):
            if member.connected or member.absent_since is None:
                continue
            if now - member.absent_since >= grace:
                out.extend(self.leave(peer_id))
        return out

    # ── lobby changes a client may ask for ───────────────────────────────────
    def set_nickname(self, peer_id: str, nickname: str) -> List[Outbound]:
        seat = self.lobby.seat_of(peer_id)
        if seat is None:
            return []
        seat.nickname = self.lobby.unique_nickname(clean_nickname(nickname),
                                                   except_peer=peer_id)
        member = self.members.get(peer_id)
        if member is not None:
            member.nickname = seat.nickname
        if self.state is not None:
            player = self.state.player(seat.seat)
            if player is not None:
                player.name = seat.nickname
        return self.broadcast_lobby()

    def set_character(self, peer_id: str, character: str) -> List[Outbound]:
        """A player picks their own character; the server decides if they get it.

        Refused when somebody already has it.  The client's own list hides
        taken characters, but a client is not to be trusted on that — which is
        the whole reason this check is on this side of the wire.
        """
        seat = self.lobby.seat_of(peer_id)
        if seat is None or self.started:
            return []
        wanted = (character or "").strip()
        if wanted and wanted in self.lobby.taken_characters(except_peer=peer_id):
            return [(peer_id, Message.error("Ta postać jest już zajęta"))]
        seat.character = wanted
        return self.broadcast_lobby()

    def set_ready(self, peer_id: str, ready: bool) -> List[Outbound]:
        seat = self.lobby.seat_of(peer_id)
        if seat is None:
            return []
        seat.ready = bool(ready)
        return self.broadcast_lobby()

    def set_settings(self, peer_id: str, payload: Mapping[str, Any]
                     ) -> List[Outbound]:
        """Table settings.  Host only, checked here rather than in the menu."""
        if not self.lobby.is_host(peer_id) or self.started:
            return [(peer_id, Message.error("Tylko host zmienia ustawienia stołu"))]
        lobby = self.lobby
        if "board_cells" in payload:
            lobby.board_cells = max(RULES.board_cells_min,
                                    int(payload["board_cells"]))
        if "chest_open_round" in payload:
            lobby.chest_open_round = max(RULES.chest_open_min,
                                         int(payload["chest_open_round"]))
        if "double_percent" in payload:
            lobby.double_percent = max(0, min(100, int(payload["double_percent"])))
        if "debug_version" in payload:
            lobby.debug_version = bool(payload["debug_version"])
        return self.broadcast_lobby()

    # ── starting the match ───────────────────────────────────────────────────
    def start(self, peer_id: str, seed: Optional[int] = None) -> List[Outbound]:
        """Build the authoritative game and tell everyone to build the same one.

        Only the configuration crosses the wire — including the seed, which is
        what makes every client's board, shuffle and dealing identical without
        a single card being transmitted.
        """
        if self.started:
            return [(peer_id, Message.error("Gra już trwa"))]
        if not self.lobby.is_host(peer_id):
            return [(peer_id, Message.error("Tylko host może rozpocząć grę"))]
        problem = self.lobby.validate()
        if problem:
            return [(peer_id, Message.error(problem))]

        config = self.lobby.to_config(seed=seed if seed is not None else new_seed())
        self.session_config = config
        # The server plays no seat.  ``local_seat`` is meaningless here and
        # ``edit_mode`` is off, so every command is judged by
        # ``authorise_remote`` against the seat map and nothing else.
        self.state = create_game(config, self.library)
        for seat in self.lobby.seats:
            player = self.state.player(seat.seat)
            if player is not None:
                player.name = seat.nickname
                player.owner_id = seat.peer_id
        self.lobby.started = True
        self.command_log = []
        self.fingerprint = fingerprint_of(self.state.snapshot())

        payload = Message.game_start(asdict(config), self.lobby.seat_map(),
                                     room=self.code)
        return self.broadcast(payload)

    # ── the command pipeline ─────────────────────────────────────────────────
    def submit(self, peer_id: str, payload: Mapping[str, Any]) -> List[Outbound]:
        sequence = int(payload.get("seq", 0) or 0)
        raw = payload.get("command")
        if self.state is None:
            return [(peer_id, Message.command_rejected(
                "Gra jeszcze się nie zaczęła", sequence))]
        if not isinstance(raw, Mapping):
            return [(peer_id, Message.command_rejected(
                "Pusta komenda", sequence))]

        try:
            command = cmd.Command.from_dict(dict(raw))
        except (ValueError, TypeError, KeyError) as exc:
            return [(peer_id, Message.command_rejected(str(exc), sequence))]

        seat = self.seat_of(peer_id)
        if seat is None:
            return [(peer_id, Message.command_rejected(
                "Nie masz miejsca przy stole", sequence, command.kind))]

        problem = self.state.authorise_remote(command, seat)
        if problem is not None:
            return [(peer_id, Message.command_rejected(problem, sequence,
                                                       command.kind))]

        events = self.state.apply(command, local=False)
        rejection = next((e for e in events if isinstance(e, ev.ActionRejected)),
                         None)
        if rejection is not None:
            return [(peer_id, Message.command_rejected(rejection.reason, sequence,
                                                       command.kind))]

        if any(isinstance(e, ev.ChoiceRequired) for e in events):
            # Legal, but a decision is missing.  Nothing changed, so nothing is
            # logged and nobody else hears about it; the asking player's own
            # engine will raise the same question when it applies this locally.
            return [(peer_id, Message.choice_required(command.to_dict(), sequence))]

        encoded = command.to_dict()
        self.command_log.append(encoded)
        self.fingerprint = fingerprint_of(self.state.snapshot())
        return self.broadcast(
            Message.command_accepted(encoded, self.sequence, seat,
                                     self.fingerprint)
        )

    # ── synchronisation ──────────────────────────────────────────────────────
    def sync_message(self) -> Optional[Message]:
        """The whole match, as configuration plus the accepted command log.

        This is the only message that carries a match's worth of data, and it
        is sent exactly twice in a normal game's life: to somebody arriving
        late, and to somebody coming back.  Everything else is one action.
        """
        if self.state is None or self.session_config is None:
            return None
        return Message.state_sync(
            asdict(self.session_config), self.lobby.seat_map(),
            list(self.command_log), self.sequence, self.fingerprint,
            room=self.code,
        )

    def resync(self, peer_id: str) -> List[Outbound]:
        message = self.sync_message()
        if message is None:
            return self.lobby_message(peer_id)
        return [(peer_id, message)]

    def catch_up(self, peer_id: str) -> List[Outbound]:
        """Everything a returning player needs, in one go."""
        out = self.lobby_message(peer_id)
        message = self.sync_message()
        if message is not None:
            out.append((peer_id, message))
        seat = self.lobby.seat_of(peer_id)
        if seat is not None and self.started:
            out.extend(self.broadcast(
                Message.player_reconnected(peer_id, seat.seat, seat.nickname),
                skip=peer_id,
            ))
        return out

    # ── outbound helpers ─────────────────────────────────────────────────────
    def broadcast(self, message: Message,
                  skip: Optional[str] = None) -> List[Outbound]:
        message.room = self.code
        return [(peer_id, message) for peer_id in self.present if peer_id != skip]

    def broadcast_lobby(self) -> List[Outbound]:
        return self.broadcast(Message.lobby_state(self.lobby.to_dict(), self.code))

    def lobby_message(self, peer_id: str) -> List[Outbound]:
        return [(peer_id, Message.lobby_state(self.lobby.to_dict(), self.code))]

    def close(self, reason: str = "Pokój został zamknięty") -> List[Outbound]:
        out = self.broadcast(Message(MessageType.MATCH_ENDED, {"reason": reason},
                                     room=self.code))
        self.closed = True
        return out

    # ── housekeeping ─────────────────────────────────────────────────────────
    def is_stale(self) -> bool:
        """Nobody has been here for longer than the idle timeout."""
        if self.empty_since is None:
            return False
        return (self._clock() - self.empty_since) >= self.config.server.room_idle_timeout
