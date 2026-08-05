"""
The room registry.

One lobby at a time is the requirement, and ``ServerConfig.max_rooms`` defaults
to 1 to enforce it.  Everything else in this file is already written for many:
rooms are keyed by code, created on demand and reaped when abandoned, and no
other module holds a reference to "the room".  Raising the limit is the whole
of the work needed to run a public server for several groups at once, which is
what "design it so multiple lobbies would be minimal work later" asks for —
without shipping a half-built version of it now.
"""

from __future__ import annotations

import random
from typing import Dict, Iterator, List, Optional

from ..cards.loader import ContentLibrary
from ..net.config import NetworkConfig
from ..net.lobby import clean_room_code, make_room_code
from .room import Room


class RoomRegistry:
    """Every live room on this server."""

    def __init__(self, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None,
                 clock=None, rng: Optional[random.Random] = None) -> None:
        self.config = config or NetworkConfig()
        self.library = library
        self.rooms: Dict[str, Room] = {}
        self._rng = rng
        self._clock = clock

    def __len__(self) -> int:
        return len(self.rooms)

    def __iter__(self) -> Iterator[Room]:
        return iter(list(self.rooms.values()))

    @property
    def full(self) -> bool:
        return len(self.rooms) >= max(1, self.config.server.max_rooms)

    def get(self, code: str) -> Optional[Room]:
        return self.rooms.get(clean_room_code(code))

    def create(self) -> Optional[Room]:
        """Open a room with a fresh code, or None when the server is full."""
        self.prune()
        if self.full:
            return None
        code = self._unique_code()
        kwargs = {}
        if self._clock is not None:
            kwargs["clock"] = self._clock
        room = Room(code, config=self.config, library=self.library, **kwargs)
        self.rooms[code] = room
        return room

    def _unique_code(self) -> str:
        for _ in range(200):
            code = make_room_code(self._rng)
            if code not in self.rooms:
                return code
        # Astronomically unlikely with a 31-character alphabet and six places;
        # falling back to a counter is still better than looping for ever.
        suffix = len(self.rooms) + 1
        return f"ROOM{suffix:02d}"

    def close(self, code: str, reason: str = "Pokój został zamknięty") -> List:
        room = self.rooms.pop(clean_room_code(code), None)
        if room is None:
            return []
        return room.close(reason)

    def prune(self) -> List[str]:
        """Forget rooms nobody has been in for a while.  Returns their codes."""
        gone: List[str] = []
        for code, room in list(self.rooms.items()):
            if room.closed or room.is_stale():
                self.rooms.pop(code, None)
                gone.append(code)
        return gone
