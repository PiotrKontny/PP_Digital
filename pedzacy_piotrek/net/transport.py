"""
Transports.

A transport moves :class:`~pedzacy_piotrek.net.protocol.Message` objects between
this process and the server.  Everything above it — the client state machine,
the session, the whole game — is written against :class:`Transport`, so the
choice of wire is made in this package and nowhere else.

THREE THINGS EVERY TRANSPORT MUST DO, because the layers above rely on them:

1. **Never block.**  ``poll()`` is called once a frame from inside the render
   loop.  A transport that waits for the network makes the interface stutter
   whenever the connection does, which is exactly when the player most wants it
   to keep drawing.
2. **Never raise.**  A cable pulled out is a *state*, not an exception.  Failure
   sets :attr:`Transport.state` and fills in :attr:`Transport.error`; the
   session notices on its next poll and tells the player in Polish.
3. **Count its reconnections.**  :attr:`Transport.generation` increases every
   time a fresh connection is established.  That is how the layer above knows
   the difference between "still connected" and "connected again, and the
   server has forgotten nothing but I must re-announce myself".

Implementations:

* :class:`LoopbackTransport` — an in-process pair.  Not a stub: it is a complete
  transport, and it is what the tests and the embedded server run on.
* :class:`NullTransport` — goes nowhere.  Used by the single-machine game so
  that code path has no special cases.
* :class:`~pedzacy_piotrek.net.websocket.WebSocketTransport` — the real one.
"""

from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional, Tuple

from .protocol import Message


class ConnectionState(str, Enum):
    """Where a connection is in its life.

    ``RECONNECTING`` is deliberately distinct from ``CONNECTING``: the interface
    says "łączę ponownie…" and keeps the board on screen, rather than throwing
    the player back to a menu for a hiccup that will be over in a second.
    """

    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"

    @property
    def is_live(self) -> bool:
        return self is ConnectionState.CONNECTED

    @property
    def is_working(self) -> bool:
        """Connected, or on its way back.  The game keeps running either way."""
        return self in (ConnectionState.CONNECTING, ConnectionState.CONNECTED,
                        ConnectionState.RECONNECTING)


class TransportError(RuntimeError):
    """Raised only by constructors and connect helpers, never during play."""


class Transport(ABC):
    """A message channel to the server."""

    def __init__(self) -> None:
        self.state: ConnectionState = ConnectionState.IDLE
        #: Why the connection ended or is retrying, in Polish, for the player.
        self.error: Optional[str] = None
        #: Increases on every successful connection, first one included.
        self.generation: int = 0
        #: Human-readable description of the far end, for the debug panel.
        self.description: str = ""

    # ── interface ────────────────────────────────────────────────────────────
    @abstractmethod
    def send(self, message: Message) -> None:
        """Queue a message.  Silently dropped while not connected."""

    @abstractmethod
    def poll(self) -> List[Message]:
        """Every message that arrived since the last call.  Never blocks."""

    @abstractmethod
    def close(self) -> None:
        """Release everything.  Must be safe to call twice."""

    # ── shared behaviour ─────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self.state.is_live

    @property
    def alive(self) -> bool:
        """True while the transport still expects to deliver messages."""
        return self.state.is_working

    def send_all(self, messages: List[Message]) -> None:
        for message in messages:
            self.send(message)

    def _fail(self, reason: str) -> None:
        if self.error is None:
            self.error = reason


class NullTransport(Transport):
    """Goes nowhere.  The hot-seat game's transport, so it has no special case."""

    def __init__(self) -> None:
        super().__init__()
        self.state = ConnectionState.CONNECTED
        self.generation = 1
        self.description = "brak sieci"

    def send(self, message: Message) -> None:
        return None

    def poll(self) -> List[Message]:
        return []

    def close(self) -> None:
        self.state = ConnectionState.CLOSED


class LoopbackTransport(Transport):
    """One end of an in-process pair.

    Two of these wired together behave exactly like two machines: messages are
    queued, delivered whole, and only on ``poll()``.  That is what lets the
    entire client/server stack — rooms, seats, reconnection, state sync — be
    tested without a socket, a thread or an event loop anywhere in sight.
    """

    def __init__(self, inbox: "queue.Queue[Message]",
                 outbox: "queue.Queue[Message]", name: str = "loopback") -> None:
        super().__init__()
        self._inbox = inbox
        self._outbox = outbox
        self.state = ConnectionState.CONNECTED
        self.generation = 1
        self.description = name

    @classmethod
    def pair(cls) -> Tuple["LoopbackTransport", "LoopbackTransport"]:
        """Two transports wired to each other."""
        a_to_b: "queue.Queue[Message]" = queue.Queue()
        b_to_a: "queue.Queue[Message]" = queue.Queue()
        return cls(b_to_a, a_to_b, "loopback-a"), cls(a_to_b, b_to_a, "loopback-b")

    def send(self, message: Message) -> None:
        if self.connected:
            self._outbox.put(message)

    def poll(self) -> List[Message]:
        messages: List[Message] = []
        while True:
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def close(self) -> None:
        self.state = ConnectionState.CLOSED

    def drop(self, reason: str = "Połączenie zerwane") -> None:
        """Simulate the wire being cut, for tests and for the debug menu."""
        self._fail(reason)
        self.state = ConnectionState.CLOSED
