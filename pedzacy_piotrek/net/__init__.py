"""
Networking.

Layered on purpose, and the layers do not leak into each other:

    ui/  ──►  service.py   what a screen is allowed to know
              client.py    the protocol state machine
              session.py   commands in, game events out
              protocol.py  message vocabulary and JSON
              transport.py the Transport interface
              websocket.py the only client file that knows a socket exists
              config.py    every networking number in the project
              lobby.py     the replicated lobby document

The server lives in :mod:`pedzacy_piotrek.server` and shares ``protocol``,
``lobby`` and ``config`` with the client — one definition of the wire, so the
two ends cannot drift apart.
"""

from __future__ import annotations

from .config import NetworkConfig, current as network_config
from .lobby import LobbySeat, LobbyState
from .protocol import Message, MessageType
from .session import LocalSession, NetworkSession, NetworkStats
from .transport import ConnectionState, Transport, TransportError

__all__ = [
    "NetworkConfig", "network_config", "LobbySeat", "LobbyState", "Message",
    "MessageType", "LocalSession", "NetworkSession", "NetworkStats",
    "ConnectionState", "Transport", "TransportError",
]
