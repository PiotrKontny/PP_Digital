"""
Running the server from inside the game.

Two ways, for two quite different reasons.

:class:`EmbeddedServer` starts the real asyncio server in a background thread of
the player's own process.  That is what makes a game on one network work with
nothing installed and nothing deployed: one person ticks "uruchom serwer na tym
komputerze", everyone else types their local address, and it behaves exactly
like the deployed version because it *is* the deployed version.  It does not
help across the internet — that machine is still behind a router — which is why
it is offered as a convenience and not as the answer.

:class:`InProcessServer` skips sockets entirely and wires client transports
straight into a :class:`ServerHub` through queues.  It is what the tests run on:
the same hub, the same rooms, the same messages, deterministic and instant.  A
bug that survives a test here is a bug in the socket layer, which is a much
smaller place to look than "somewhere in multiplayer".
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Dict, List, Optional

from ..cards.loader import ContentLibrary
from ..net.config import NetworkConfig
from ..net.protocol import Message
from ..net.transport import ConnectionState, Transport, TransportError
from .app import GameServer
from .hub import ServerHub


class EmbeddedServer:
    """The real server, in a thread, inside the game's own process."""

    def __init__(self, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None) -> None:
        self.config = config or NetworkConfig.load()
        self._library = library
        self._server: Optional[GameServer] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = threading.Event()
        self.error: Optional[str] = None
        self.port: int = self.config.server.port

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def start(self, timeout: float = 5.0) -> bool:
        """Start it and wait until the port is really bound.

        Waiting matters: returning before the listener exists would have the
        game connect to a port that is not there yet and report a refusal for
        a server that is about to work.
        """
        if self.running:
            return True
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="piotrek-server",
                                        daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            self.error = self.error or "Serwer nie wystartował"
            return False
        return self.error is None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._server = GameServer(self.config, self._library)
        except RuntimeError as exc:
            self.error = str(exc)
            self._started.set()
            return

        async def runner() -> None:
            assert self._server is not None
            task = asyncio.ensure_future(self._server.run())
            try:
                await asyncio.wait_for(self._server.ready.wait(), timeout=10)
                self.port = self._server.port
            except (asyncio.TimeoutError, Exception) as exc:  # pragma: no cover
                self.error = f"Nie udało się otworzyć portu: {exc}"
            self._started.set()
            await task

        try:
            loop.run_until_complete(runner())
        except OSError as exc:
            self.error = (f"Nie udało się otworzyć portu "
                          f"{self.config.server.port}: {exc.strerror or exc}")
            self._started.set()
        except Exception as exc:  # pragma: no cover - defensive
            self.error = f"Błąd serwera: {exc}"
            self._started.set()
        finally:
            try:
                loop.close()
            except Exception:  # pragma: no cover
                pass
            self._loop = None

    def stop(self) -> None:
        server, loop = self._server, self._loop
        if server is not None and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(server.stop)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._server = None


class _InProcessTransport(Transport):
    """A client transport wired straight into a hub, with no socket at all."""

    def __init__(self, server: "InProcessServer", cid: str) -> None:
        super().__init__()
        self._server = server
        self.cid = cid
        self._inbox: "queue.Queue[Message]" = queue.Queue()
        self.state = ConnectionState.CONNECTED
        self.generation = 1
        self.latency_ms: Optional[float] = 0.0
        self.description = f"in-process:{cid}"

    def send(self, message: Message) -> None:
        if self.connected:
            self._server.deliver(self.cid, message)

    def poll(self) -> List[Message]:
        messages: List[Message] = []
        while True:
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def close(self) -> None:
        if self.state is ConnectionState.CLOSED:
            return
        self.state = ConnectionState.CLOSED
        self._server.disconnect(self.cid)

    def accept(self, message: Message) -> None:
        self._inbox.put(message)

    # ── test affordances ─────────────────────────────────────────────────────
    def drop(self, reason: str = "Połączenie zerwane") -> None:
        """Cut the wire without a clean goodbye, the way a real drop happens."""
        self._fail(reason)
        self.state = ConnectionState.RECONNECTING
        self._server.disconnect(self.cid)

    def restore(self) -> None:
        """Come back on a new connection, as a reconnecting client would."""
        self.state = ConnectionState.CONNECTED
        self.generation += 1
        self.error = None
        self.cid = self._server.attach(self)


class InProcessServer:
    """A :class:`ServerHub` clients can be plugged into directly."""

    def __init__(self, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None,
                 clock=time.monotonic) -> None:
        self.config = config or NetworkConfig()
        self.hub = ServerHub(self.config, library, clock=clock)
        self._transports: Dict[str, _InProcessTransport] = {}
        self._next = 0

    def transport(self) -> _InProcessTransport:
        """A fresh client connection."""
        self._next += 1
        transport = _InProcessTransport(self, f"c{self._next}")
        transport.cid = self.attach(transport)
        return transport

    def attach(self, transport: _InProcessTransport) -> str:
        self._next += 1
        cid = f"c{self._next}"
        self._transports[cid] = transport
        self._route(self.hub.connect(cid, "in-process"))
        return cid

    def deliver(self, cid: str, message: Message) -> None:
        self._route(self.hub.receive(cid, message))

    def disconnect(self, cid: str) -> None:
        self._transports.pop(cid, None)
        self._route(self.hub.disconnect(cid))

    def tick(self) -> None:
        self._route(self.hub.tick())

    def _route(self, outbound) -> None:
        for cid, message in outbound:
            transport = self._transports.get(cid)
            if transport is not None:
                transport.accept(message)
