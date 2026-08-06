"""
The lobby.

A small replicated document.  The server owns it, changes it and broadcasts it
whole; clients keep a read-only mirror and draw it.  This is the one place
where sending state rather than actions is obviously right — it is a few
hundred bytes and changes a handful of times before a match starts.  The moment
the match begins, the lobby stops mattering and everything switches to actions.

The rules live here rather than in the interface because the *server* is the
authority: a client may ask for a character, and the server decides whether it
gets it.  Both sides import the same validation, so the host's Start button is
disabled for exactly the reasons the server would refuse.

ROOM CODES REPLACED IP ADDRESSES.  Asking a friend abroad for "the address" was
the part of the old design that could not work: their machine is behind a
router that will not accept incoming connections.  Everyone now connects
outwards to the same server and finds each other with a six-character code,
which is how every commercial online board game does it and the only part of
this that the player sees.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from ..config import settings
from ..config.settings import RULES, SessionConfig

#: What a player is called when they do not say.
DEFAULT_NICKNAME = "Player"
#: Character choice meaning "deal me a random one".
RANDOM_CHARACTER = ""

#: Alphabet for room codes.  No 0/O, no 1/I/L: a code is read aloud over a
#: voice chat and typed by somebody who is not looking at it.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def make_room_code(rng: Optional[random.Random] = None) -> str:
    source = rng or random.SystemRandom()
    return "".join(source.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def clean_room_code(code: str) -> str:
    """Normalise whatever was typed into the form the server stores.

    People type lower case, add spaces and hyphens, and confuse O with 0 — all
    of which should join the game rather than report "no such room".
    """
    text = (code or "").strip().upper()
    text = "".join(ch for ch in text if ch.isalnum())
    return text.translate(str.maketrans({"0": "O", "1": "I", "L": "I"}))[:CODE_LENGTH]


@dataclass
class LobbySeat:
    """One person in the lobby."""

    peer_id: str
    nickname: str = DEFAULT_NICKNAME
    seat: int = 0
    #: Chosen character title, or empty for a random one.
    character: str = RANDOM_CHARACTER
    is_host: bool = False
    #: False while the player is away but their seat is still being held.
    connected: bool = True
    ready: bool = False

    @property
    def wants_random(self) -> bool:
        return not self.character

    def to_dict(self) -> Dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "nickname": self.nickname,
            "seat": self.seat,
            "character": self.character,
            "is_host": self.is_host,
            "connected": self.connected,
            "ready": self.ready,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LobbySeat":
        return cls(
            peer_id=str(raw.get("peer_id", "")),
            nickname=str(raw.get("nickname", DEFAULT_NICKNAME)),
            seat=int(raw.get("seat", 0)),
            character=str(raw.get("character", "")),
            is_host=bool(raw.get("is_host", False)),
            connected=bool(raw.get("connected", True)),
            ready=bool(raw.get("ready", False)),
        )


@dataclass
class LobbyState:
    """Everything a client needs to draw the lobby, and the rules for starting."""

    #: The code friends type to get in.  Empty on a client that has not joined.
    code: str = ""
    seats: List[LobbySeat] = field(default_factory=list)
    host_peer_id: str = ""
    board_cells: int = RULES.board_cells_default
    chest_open_round: int = RULES.chest_open_default
    double_percent: int = RULES.double_frequency_default
    #: Development option: a two-player table is enough to start.  Set by the
    #: host and broadcast, so every client shows the same requirement.
    debug_version: bool = False
    #: Set once the match begins; clients stop showing the lobby.
    started: bool = False
    #: Why the game cannot start yet, computed by the server.
    problem: str = ""

    # ── queries ──────────────────────────────────────────────────────────────
    def seat_of(self, peer_id: str) -> Optional[LobbySeat]:
        return next((s for s in self.seats if s.peer_id == peer_id), None)

    def seat_at(self, index: int) -> Optional[LobbySeat]:
        return next((s for s in self.seats if s.seat == index), None)

    @property
    def player_count(self) -> int:
        return len(self.seats)

    @property
    def host_seat(self) -> Optional[LobbySeat]:
        return next((s for s in self.seats if s.is_host), None)

    def is_host(self, peer_id: str) -> bool:
        seat = self.seat_of(peer_id)
        return seat is not None and seat.is_host

    def taken_characters(self, except_peer: str = "") -> List[str]:
        return [s.character for s in self.seats
                if s.character and s.peer_id != except_peer]

    @property
    def minimum_players(self) -> int:
        return RULES.debug_min_players if self.debug_version else RULES.min_players

    @property
    def everyone_ready(self) -> bool:
        return bool(self.seats) and all(s.ready or s.is_host for s in self.seats)

    def validate(self) -> str:
        """Why the game cannot start, or an empty string when it can.

        The same rules the single-machine setup screen applies, plus the two
        the network adds: everyone has to be present, and everyone has to have
        said they are ready.
        """
        minimum = self.minimum_players
        if self.player_count < minimum:
            missing = minimum - self.player_count
            return f"Potrzeba jeszcze {missing} graczy (minimum {minimum})"
        if self.player_count > RULES.max_players:
            return f"Za dużo graczy (maksimum {RULES.max_players})"

        absent = [s.nickname for s in self.seats if not s.connected]
        if absent:
            return f"Czekamy na powrót: {', '.join(absent)}"

        chosen = [s.character for s in self.seats if s.character]
        if len(set(chosen)) != len(chosen):
            return "Dwie osoby wybrały tę samą postać"
        if len(chosen) == self.player_count and settings.PIOTREK_TITLE not in chosen:
            return (f"Nikt nie wybrał postaci „{settings.PIOTREK_TITLE}” — "
                    "bez niego nie ma kogo ścigać")

        waiting = [s.nickname for s in self.seats if not s.ready and not s.is_host]
        if waiting:
            return f"Nie wszyscy są gotowi: {', '.join(waiting)}"
        return ""

    @property
    def can_start(self) -> bool:
        return not self.validate()

    def to_config(self, seed: int = 0) -> SessionConfig:
        """The settings the match will be built from.

        Seats keep their assigned index, so the character list lines up with
        the player indices every peer builds locally.
        """
        ordered = sorted(self.seats, key=lambda s: s.seat)
        return SessionConfig(
            num_players=len(ordered),
            board_cells=self.board_cells,
            chest_open_round=self.chest_open_round,
            character_choices=[s.character or None for s in ordered],
            double_frequency=self.double_percent / 100.0,
            debug_version=self.debug_version,
            # An online match never allows hot-seat editing: one machine plays
            # one seat, which is the whole point of the mode.
            edit_mode=False,
            # ...and it is the mode where Piotrek's colour is a secret worth
            # keeping, so he chooses it himself and only the server is told.
            piotrek_picks_pawn=True,
            seed=seed,
        ).normalised()

    def seat_map(self) -> Dict[str, int]:
        """peer id → player index, so every client learns which seat is theirs."""
        return {s.peer_id: s.seat for s in self.seats}

    # ── serialisation ────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "seats": [s.to_dict() for s in self.seats],
            "host_peer_id": self.host_peer_id,
            "board_cells": self.board_cells,
            "chest_open_round": self.chest_open_round,
            "double_percent": self.double_percent,
            "debug_version": self.debug_version,
            "started": self.started,
            "problem": self.validate(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LobbyState":
        return cls(
            code=str(raw.get("code", "")),
            seats=[LobbySeat.from_dict(item) for item in raw.get("seats", [])],
            host_peer_id=str(raw.get("host_peer_id", "")),
            board_cells=int(raw.get("board_cells", RULES.board_cells_default)),
            chest_open_round=int(raw.get("chest_open_round", RULES.chest_open_default)),
            double_percent=int(raw.get("double_percent",
                                       RULES.double_frequency_default)),
            debug_version=bool(raw.get("debug_version", False)),
            started=bool(raw.get("started", False)),
            problem=str(raw.get("problem", "")),
        )

    # ── mutation (server side only — a client's copy is a mirror) ────────────
    def add_seat(self, peer_id: str, nickname: str = "",
                 is_host: bool = False) -> Optional[LobbySeat]:
        """Seat a new arrival, or return None when the table is full."""
        existing = self.seat_of(peer_id)
        if existing is not None:
            return existing
        if self.player_count >= RULES.max_players:
            return None
        seat = LobbySeat(
            peer_id=peer_id,
            nickname=self.unique_nickname(clean_nickname(nickname)),
            seat=self._free_index(),
            is_host=is_host,
        )
        if is_host:
            self.host_peer_id = peer_id
        self.seats.append(seat)
        self.seats.sort(key=lambda s: s.seat)
        return seat

    def _free_index(self) -> int:
        used = {s.seat for s in self.seats}
        index = 0
        while index in used:
            index += 1
        return index

    def remove_seat(self, peer_id: str) -> Optional[LobbySeat]:
        seat = self.seat_of(peer_id)
        if seat is None:
            return None
        self.seats.remove(seat)
        self.renumber()
        return seat

    def renumber(self) -> None:
        """Close the gaps left by someone leaving before the match starts.

        Never called once a match is running: the seat index is baked into
        every command already sent, and shifting it would rewrite history.
        """
        for index, seat in enumerate(sorted(self.seats, key=lambda s: s.seat)):
            seat.seat = index
        self.seats.sort(key=lambda s: s.seat)

    def unique_nickname(self, nickname: str, except_peer: str = "") -> str:
        """Two people called Kuba are confusing; the second becomes Kuba (2)."""
        taken = {s.nickname for s in self.seats if s.peer_id != except_peer}
        if nickname not in taken:
            return nickname
        for suffix in range(2, 20):
            candidate = f"{nickname} ({suffix})"
            if candidate not in taken:
                return candidate
        return nickname

    def promote_new_host(self) -> Optional[LobbySeat]:
        """Hand the host role to whoever has been here longest.

        The room outlives whoever opened it.  Without this, one person's
        connection dropping in the lobby would leave a room nobody can start —
        which is a worse outcome than the mild unfairness of somebody else
        getting the Start button.
        """
        if self.host_seat is not None:
            return self.host_seat
        candidates = [s for s in sorted(self.seats, key=lambda s: s.seat)
                      if s.connected]
        if not candidates:
            return None
        candidates[0].is_host = True
        self.host_peer_id = candidates[0].peer_id
        return candidates[0]


def clean_nickname(nickname: str) -> str:
    """Trim, cap the length, and fall back to the default when empty."""
    cleaned = " ".join((nickname or "").split())[: RULES.max_name_length]
    return cleaned or DEFAULT_NICKNAME
