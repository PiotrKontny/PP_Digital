"""
The wire protocol.

One message is one JSON object.  WebSockets already carry message boundaries,
so there is no framing to do here — which is most of the reason the transport
moved to them.

The model is **client / server**, not host / peer:

* every player — including whoever pressed "create a room" — is a client;
* the server owns the authoritative :class:`GameState` and the room;
* clients send *intents* (``COMMAND``) and receive *facts* (``COMMAND_ACCEPTED``,
  ``LOBBY_STATE``, ``GAME_START``, ``STATE_SYNC``);
* nothing a client says about who it is, is believed.  The server maps a
  connection to a seat and judges every message against its own map.

WHY ACTIONS RATHER THAN STATE.  A command is a few dozen bytes and a full game
state is tens of kilobytes; more importantly, an action is *what happened*,
which replays into the same state everywhere, while a state dump is a snapshot
whose arrival order matters.  ``STATE_SYNC`` therefore exists only for the two
cases that genuinely need it — a player joining a match in progress and a
player coming back after a drop — and even then it sends the seed and the
accepted command log rather than a serialised board.

THE COMMAND VOCABULARY IS NOT DUPLICATED HERE.  Playing a card, using a skill,
moving a pawn, drawing and ending a turn are already
:class:`~pedzacy_piotrek.engine.commands.Command` types with a registry and
wire names; wrapping them in a second parallel enum would mean two lists to
keep in step.  They travel inside ``COMMAND`` with their ``kind`` intact, and
``COMMAND_KINDS`` below is the readable index of what that means.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from .config import PROTOCOL_VERSION


class MessageType(str, Enum):
    """Every message that may cross the wire.

    String values rather than numbers so a packet capture or a log line reads
    as itself.  The traffic is JSON already; the bytes saved would buy nothing
    and cost every future debugging session.
    """

    # ── connection ───────────────────────────────────────────────────────────
    HELLO = "hello"                    # client → server: protocol + identity
    WELCOME = "welcome"                # server → client: your peer id, server info
    PING = "ping"                      # both ways: keepalive and latency probe
    PONG = "pong"
    ERROR = "error"                    # server → client: your last message failed
    BYE = "bye"                        # both ways: leaving, with a reason

    # ── lobby ────────────────────────────────────────────────────────────────
    CREATE_LOBBY = "create_lobby"      # client → server: open a room, I am host
    JOIN_LOBBY = "join_lobby"          # client → server: let me into room CODE
    LEAVE_LOBBY = "leave_lobby"        # client → server
    LOBBY_STATE = "lobby_state"        # server → room: who is here, what they chose
    SET_NICKNAME = "set_nickname"      # client → server: ask for a name
    SET_CHARACTER = "set_character"    # client → server: ask for a character
    SET_SETTINGS = "set_settings"      # client → server: host-only table settings
    PLAYER_READY = "player_ready"      # client → server: I am ready / not ready

    # ── match ────────────────────────────────────────────────────────────────
    START_GAME = "start_game"          # client → server: host presses Start
    GAME_START = "game_start"          # server → room: build this game and play
    COMMAND = "command"                # client → server: I would like to do this
    COMMAND_ACCEPTED = "command_accepted"   # server → room: this happened
    COMMAND_REJECTED = "command_rejected"   # server → one client: it did not
    CHOICE_REQUIRED = "choice_required"     # server → one client: answer this first
    REQUEST_SYNC = "request_sync"      # client → server: I think I am out of step
    STATE_SYNC = "state_sync"          # server → client: here is the whole match
    PLAYER_DISCONNECTED = "player_disconnected"   # server → room
    PLAYER_RECONNECTED = "player_reconnected"     # server → room
    MATCH_ENDED = "match_ended"        # server → room: the room is closing

    # ── the hidden identity ──────────────────────────────────────────────────
    # The only part of this game that is addressed to ONE player and must never
    # be broadcast.  It does not travel as a COMMAND for that reason: a command
    # is logged and replayed to everybody, which is precisely what a secret
    # cannot survive.
    IDENTITY_REQUIRED = "identity_required"   # server → Piotrek alone: choose
    IDENTITY_CHOSEN = "identity_chosen"       # Piotrek → server: this colour
    IDENTITY_ACCEPTED = "identity_accepted"   # server → Piotrek alone: noted
    RETURN_TO_LOBBY = "return_to_lobby"       # client → server: play again


#: What a ``COMMAND`` message may carry, for the reader's benefit.  The
#: authoritative list is ``engine.commands.COMMAND_REGISTRY``; this is the map
#: from the brief's names to the ones the engine has used since stage 2.
COMMAND_KINDS = {
    "play_card": "PlayCard — play a card from the hand",
    "use_ability": "UseSkill — a character ability or one of Piotrek's skills",
    "move_token": "MovePiece — drag a pawn to a field",
    "pick_up_token": "MovePiece — lift a pawn before moving it",
    "draw_card": "DrawCard — take a card from one of the table decks",
    "discard_card": "DiscardCard — discard, which is also how a player passes",
    "end_turn": "EndTurn — hand the turn on",
    "set_round": "RoundChanged — the manual round counter",
    "place_mod": "PlaceMod — put a card into the Mody Patusa rack",
    "keep_chest_cards": "KeepChestCards — answer the chest hand limit",
}


class ProtocolError(ValueError):
    """A message that could not be understood.  Never fatal to a connection."""


@dataclass
class Message:
    """One protocol message.

    ``room`` and ``peer`` are filled in by the server when it relays; a client
    setting them is ignored, because a client is not the authority on which
    room it is in or who it is.
    """

    type: MessageType
    payload: Dict[str, Any] = field(default_factory=dict)
    #: Server-assigned identity of whoever sent this, on relayed messages.
    peer: Optional[str] = None
    #: Which room this concerns.  Present from the moment a room is joined.
    room: Optional[str] = None
    version: int = PROTOCOL_VERSION

    # ── serialisation ────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"t": self.type.value, "v": self.version,
                                "p": self.payload}
        if self.peer is not None:
            data["peer"] = self.peer
        if self.room is not None:
            data["room"] = self.room
        return data

    def encode(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False,
                          separators=(",", ":"))

    @classmethod
    def decode(cls, raw: str | bytes) -> "Message":
        """Parse one message, or raise :class:`ProtocolError`.

        Callers catch that and drop the message.  A peer sending rubbish must
        cost that peer its message, never everybody else their game.
        """
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError("Wiadomość nie jest tekstem UTF-8") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"Nieprawidłowy JSON: {exc.msg}") from exc
        if not isinstance(data, Mapping):
            raise ProtocolError("Wiadomość nie jest obiektem")
        try:
            kind = MessageType(str(data.get("t")))
        except ValueError as exc:
            raise ProtocolError(f"Nieznany typ wiadomości: {data.get('t')!r}") from exc
        payload = data.get("p")
        return cls(
            type=kind,
            payload=dict(payload) if isinstance(payload, Mapping) else {},
            peer=_optional_str(data.get("peer")),
            room=_optional_str(data.get("room")),
            version=int(data.get("v", PROTOCOL_VERSION) or PROTOCOL_VERSION),
        )

    # ── constructors ─────────────────────────────────────────────────────────
    # Named constructors rather than raw dictionaries at the call sites: the
    # payload keys are then written once, and a typo becomes a missing method
    # rather than a message the other end silently ignores.

    @classmethod
    def hello(cls, nickname: str, resume_token: str = "",
              client: str = "") -> "Message":
        return cls(MessageType.HELLO, {"nickname": nickname,
                                       "resume_token": resume_token,
                                       "client": client})

    @classmethod
    def welcome(cls, peer_id: str, resume_token: str,
                server_version: int = PROTOCOL_VERSION) -> "Message":
        return cls(MessageType.WELCOME, {"peer_id": peer_id,
                                         "resume_token": resume_token,
                                         "protocol": server_version})

    @classmethod
    def create_lobby(cls, nickname: str) -> "Message":
        return cls(MessageType.CREATE_LOBBY, {"nickname": nickname})

    @classmethod
    def join_lobby(cls, code: str, nickname: str) -> "Message":
        return cls(MessageType.JOIN_LOBBY, {"code": code, "nickname": nickname})

    @classmethod
    def lobby_state(cls, state: Dict[str, Any], room: str = "") -> "Message":
        return cls(MessageType.LOBBY_STATE, state, room=room or None)

    @classmethod
    def game_start(cls, config: Dict[str, Any], seats: Dict[str, int],
                   room: str = "") -> "Message":
        return cls(MessageType.GAME_START, {"config": config, "seats": seats},
                   room=room or None)

    @classmethod
    def command(cls, command: Dict[str, Any], sequence: int = 0) -> "Message":
        return cls(MessageType.COMMAND, {"command": command, "seq": sequence})

    @classmethod
    def command_accepted(cls, command: Dict[str, Any], sequence: int,
                         seat: int, fingerprint: str = "") -> "Message":
        """A command the server applied.  ``sequence`` is the match-wide order.

        The fingerprint is a hash of the server's state after applying it.  A
        client that computes a different one knows immediately that it has
        drifted, and asks for a resync instead of playing on in a game nobody
        else is in.
        """
        return cls(MessageType.COMMAND_ACCEPTED,
                   {"command": command, "seq": sequence, "seat": seat,
                    "fingerprint": fingerprint})

    @classmethod
    def command_rejected(cls, reason: str, sequence: int = 0,
                         kind: str = "") -> "Message":
        return cls(MessageType.COMMAND_REJECTED,
                   {"reason": reason, "seq": sequence, "kind": kind})

    @classmethod
    def choice_required(cls, command: Dict[str, Any], sequence: int = 0) -> "Message":
        """The action is legal but the engine needs a decision first.

        Sent to the asking player alone, and never logged.  The command changed
        nothing, so replaying it into everyone else's game would pop a modal on
        four screens asking a question only one person can answer — which is
        exactly what the old host-authoritative build did.
        """
        return cls(MessageType.CHOICE_REQUIRED,
                   {"command": command, "seq": sequence})

    @classmethod
    def state_sync(cls, config: Dict[str, Any], seats: Dict[str, int],
                   log: List[Dict[str, Any]], sequence: int,
                   fingerprint: str = "", room: str = "") -> "Message":
        """Everything needed to rebuild the match exactly.

        The seed plus the accepted command log *is* the state, and it is what
        the engine already knows how to consume — which is why this is a replay
        rather than a serialised board.  No second representation of the game
        to keep in step with the first.
        """
        return cls(MessageType.STATE_SYNC,
                   {"config": config, "seats": seats, "log": log,
                    "seq": sequence, "fingerprint": fingerprint},
                   room=room or None)

    @classmethod
    def identity_required(cls, pawns: List[Dict[str, Any]],
                          room: str = "") -> "Message":
        """Ask Piotrek which pawn he is hiding behind.

        Sent to one peer and nobody else.  The pawn list travels with it so the
        client draws exactly the colours the server will accept.
        """
        return cls(MessageType.IDENTITY_REQUIRED, {"pawns": list(pawns)},
                   room=room or None)

    @classmethod
    def identity_chosen(cls, pawn_id: str) -> "Message":
        return cls(MessageType.IDENTITY_CHOSEN, {"pawn_id": pawn_id})

    @classmethod
    def identity_accepted(cls, pawn_id: str) -> "Message":
        return cls(MessageType.IDENTITY_ACCEPTED, {"pawn_id": pawn_id})

    @classmethod
    def error(cls, reason: str, fatal: bool = False) -> "Message":
        return cls(MessageType.ERROR, {"reason": reason, "fatal": fatal})

    @classmethod
    def bye(cls, reason: str = "") -> "Message":
        return cls(MessageType.BYE, {"reason": reason})

    @classmethod
    def player_disconnected(cls, peer_id: str, seat: int, name: str,
                            grace: float) -> "Message":
        return cls(MessageType.PLAYER_DISCONNECTED,
                   {"peer_id": peer_id, "seat": seat, "name": name,
                    "grace": grace})

    @classmethod
    def player_reconnected(cls, peer_id: str, seat: int, name: str) -> "Message":
        return cls(MessageType.PLAYER_RECONNECTED,
                   {"peer_id": peer_id, "seat": seat, "name": name})

    @classmethod
    def ping(cls, stamp: float) -> "Message":
        return cls(MessageType.PING, {"t": stamp})

    @classmethod
    def pong(cls, stamp: float) -> "Message":
        return cls(MessageType.PONG, {"t": stamp})


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def fingerprint_of(snapshot: Mapping[str, Any]) -> str:
    """Short, stable hash of a game snapshot.

    Two machines showing the same eight characters are in the same game; it is
    the cheapest desync detector there is, and the server puts one on every
    accepted command so a drift is caught on the action that caused it rather
    than twenty minutes later.
    """
    import hashlib

    raw = json.dumps(snapshot, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
