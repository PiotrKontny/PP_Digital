"""
The dedicated game server.

Split in two on purpose:

* :mod:`.hub`, :mod:`.room` and :mod:`.registry` are the server — synchronous,
  free of I/O, and therefore testable in milliseconds;
* :mod:`.app` is the asyncio WebSocket wrapper around them, and contains no
  game rules at all.

Run it with ``python -m pedzacy_piotrek.server``.  See ``docs/SERWER.md`` for
putting it somewhere both players can reach.
"""

from __future__ import annotations

from .hub import ServerHub
from .registry import RoomRegistry
from .room import Room

__all__ = ["ServerHub", "RoomRegistry", "Room"]
