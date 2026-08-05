"""
The WebSocket transport.

This is the only file in the project that knows a socket exists on the client
side, and the only one that imports ``websockets``.  Everything above it sees
:class:`~pedzacy_piotrek.net.transport.Transport`.

WHY WEBSOCKETS.  The requirement is two friends in different countries, behind
different routers, with no port forwarding and no VPN.  That is only possible
if both of them make an *outbound* connection to something publicly reachable,
which is what a client/server architecture means and what a home-hosted peer
can never be.  Given that, WebSockets are the transport that survives the trip:
one long-lived TCP connection carrying framed messages, spoken natively by
every hosting platform and every corporate proxy, upgradeable to TLS by
changing one letter of the URL, and unbothered by the HTTP-only egress rules
that block a raw socket on port 51337.

WHY A THREAD.  ``websockets`` is asyncio, and pygame's loop is not.  Rather
than infect the game with async/await, the event loop runs in a daemon thread
and talks to the game through two thread-safe queues.  The game keeps calling
``poll()`` once a frame and never waits for anything:

    game thread            queues            network thread
    ───────────            ──────            ──────────────
    send(msg)   ──▶ outbox ──▶ ws.send()
    poll()      ◀── inbox  ◀── ws.recv()

The only shared mutable state besides the queues is a handful of scalars —
connection state, error string, generation counter, latency — each written by
one side and read by the other.  No locks, because no compound value is ever
read across a write.

RECONNECTION IS THE TRANSPORT'S JOB.  Losing WiFi for four seconds should cost
four seconds, not the match.  The loop below reconnects with exponential
backoff for as long as the policy allows, and bumps :attr:`generation` when it
succeeds — which is the signal the client layer uses to re-announce itself and
ask for a state sync.
"""

from __future__ import annotations

import asyncio
import queue
import ssl
import threading
import time
from typing import List, Optional

from .config import NetworkConfig
from .protocol import Message, MessageType, ProtocolError
from .transport import ConnectionState, Transport, TransportError

#: Set on import success; checked before a connection is attempted so the
#: failure is a readable sentence rather than an ImportError in a game loop.
try:  # pragma: no cover - exercised by whichever branch the install has
    from websockets.asyncio.client import connect as _ws_connect
    from websockets.exceptions import (
        ConnectionClosed as _ConnectionClosed,
        InvalidURI as _InvalidURI,
        WebSocketException as _WebSocketException,
    )

    WEBSOCKETS_AVAILABLE = True
    WEBSOCKETS_ERROR = ""
except ImportError:  # pragma: no cover - older websockets, legacy API
    try:
        from websockets.legacy.client import connect as _ws_connect  # type: ignore
        from websockets.exceptions import (  # type: ignore
            ConnectionClosed as _ConnectionClosed,
            InvalidURI as _InvalidURI,
            WebSocketException as _WebSocketException,
        )

        WEBSOCKETS_AVAILABLE = True
        WEBSOCKETS_ERROR = ""
    except ImportError as exc:  # pragma: no cover - library missing entirely
        _ws_connect = None  # type: ignore
        _ConnectionClosed = _InvalidURI = _WebSocketException = Exception  # type: ignore
        WEBSOCKETS_AVAILABLE = False
        WEBSOCKETS_ERROR = str(exc)


class WebSocketTransport(Transport):
    """A live WebSocket connection to the game server, polled from the game loop.

    Construction returns immediately; the connection is established in the
    background.  A menu screen therefore never freezes while a server on
    another continent finishes its TLS handshake — it shows "łączę…" and keeps
    drawing, which is what the requirement about a responsive interface means
    in practice.
    """

    def __init__(self, url: str, config: Optional[NetworkConfig] = None,
                 auto_start: bool = True) -> None:
        super().__init__()
        if not WEBSOCKETS_AVAILABLE:
            raise TransportError(
                "Brak biblioteki „websockets” — zainstaluj ją poleceniem "
                "pip install websockets"
            )
        if not url:
            raise TransportError("Nie podano adresu serwera")

        self.config = config or NetworkConfig()
        self.url = url
        self.description = url

        self._inbox: "queue.Queue[Message]" = queue.Queue()
        self._outbox: "queue.Queue[Optional[Message]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        #: Round-trip time of the last application-level ping, in milliseconds.
        self.latency_ms: Optional[float] = None
        #: When the far end last said anything at all.  Drives the timeout.
        self.last_message_at: float = time.monotonic()
        #: How many times reconnection has been attempted since the last
        #: success, so the interface can say "próba 3".
        self.attempt: int = 0

        self.state = ConnectionState.CONNECTING
        if auto_start:
            self.start()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="piotrek-net", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        if self.state is ConnectionState.CLOSED and self._thread is None:
            return
        self._stop.set()
        self._outbox.put(None)          # wake the sender out of its wait
        self.state = ConnectionState.CLOSED
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            # Short join: the loop checks the stop flag between operations, and
            # the thread is a daemon, so a stubborn socket cannot hold the
            # application open at exit.
            thread.join(timeout=1.5)

    # ── Transport ────────────────────────────────────────────────────────────
    def send(self, message: Message) -> None:
        if self._stop.is_set():
            return
        self._outbox.put(message)

    def poll(self) -> List[Message]:
        """Drain the inbox.  Also the moment the heartbeat timeout is judged."""
        messages: List[Message] = []
        while True:
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        if messages:
            self.last_message_at = time.monotonic()
        self._check_timeout()
        return [m for m in messages if not self._absorb(m)]

    def _absorb(self, message: Message) -> bool:
        """Handle the messages that concern the transport itself.

        ``PONG`` is latency measurement and nothing else; passing it upstairs
        would make every layer above learn a message type it has no use for.
        """
        if message.type is MessageType.PONG:
            stamp = message.payload.get("t")
            if isinstance(stamp, (int, float)):
                self.latency_ms = max(0.0, (time.monotonic() - stamp) * 1000.0)
            return True
        return False

    def _check_timeout(self) -> None:
        """Notice a connection that stopped working without being closed.

        A cable pulled out, a laptop lid closed, a NAT entry expiring: none of
        them send anything.  Silence past the timeout is treated as a drop, and
        the network thread reconnects.
        """
        policy = self.config.heartbeat
        if not policy.enabled or self.state is not ConnectionState.CONNECTED:
            return
        if time.monotonic() - self.last_message_at > policy.timeout:
            self._fail("Serwer przestał odpowiadać")
            self.state = ConnectionState.RECONNECTING
            self.last_message_at = time.monotonic()
            loop, self._loop = self._loop, self._loop
            if loop is not None:
                # Cancel the current connection from the game thread; the
                # network loop wakes up, sees the state and reconnects.
                loop.call_soon_threadsafe(self._cancel_current)

    def _cancel_current(self) -> None:  # pragma: no cover - timing dependent
        task = getattr(self, "_socket_task", None)
        if task is not None and not task.done():
            task.cancel()

    # ── network thread ───────────────────────────────────────────────────────
    def _run(self) -> None:
        """The whole networking thread: connect, pump, reconnect, repeat."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._supervise())
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(f"Błąd sieci: {exc}")
            self.state = ConnectionState.CLOSED
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover - shutting down anyway
                pass
            loop.close()
            self._loop = None

    async def _supervise(self) -> None:
        """Keep a connection up for as long as the policy says to try."""
        policy = self.config.reconnect
        while not self._stop.is_set():
            try:
                await self._session()
            except asyncio.CancelledError:
                pass
            except (_ConnectionClosed, _WebSocketException, OSError) as exc:
                self._fail(_readable(exc, self.url))
            except Exception as exc:  # pragma: no cover - defensive
                self._fail(f"Błąd połączenia: {exc}")

            if self._stop.is_set():
                break
            if not policy.enabled:
                self.state = ConnectionState.CLOSED
                return
            self.attempt += 1
            if policy.max_attempts and self.attempt > policy.max_attempts:
                self._fail(self.error or "Nie udało się połączyć z serwerem")
                self.state = ConnectionState.CLOSED
                return
            self.state = ConnectionState.RECONNECTING
            await asyncio.sleep(policy.delay_for(self.attempt))
        self.state = ConnectionState.CLOSED

    async def _session(self) -> None:
        """One connection, from handshake to close."""
        kwargs = {
            "open_timeout": self.config.connect_timeout,
            "ping_interval": None,   # the application heartbeat does this job
            "ping_timeout": None,
            "max_size": self.config.server.max_message_bytes,
        }
        context = self._ssl_context()
        if context is not None:
            kwargs["ssl"] = context

        try:
            connection = await _ws_connect(self.url, **kwargs)
        except _InvalidURI:
            self._fail(f"Nieprawidłowy adres serwera: {self.url}")
            self._stop.set()
            self.state = ConnectionState.CLOSED
            return

        self.attempt = 0
        self.generation += 1
        self.error = None
        self.last_message_at = time.monotonic()
        self.state = ConnectionState.CONNECTED

        try:
            reader = asyncio.ensure_future(self._read(connection))
            writer = asyncio.ensure_future(self._write(connection))
            beater = asyncio.ensure_future(self._heartbeat(connection))
            self._socket_task = reader
            done, pending = await asyncio.wait(
                {reader, writer, beater}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            self._socket_task = None
            if self.state is ConnectionState.CONNECTED:
                self.state = ConnectionState.RECONNECTING
            try:
                await connection.close()
            except Exception:  # pragma: no cover - already gone
                pass

    async def _read(self, connection) -> None:
        async for raw in connection:
            try:
                message = Message.decode(raw)
            except ProtocolError:
                # One bad message must not cost the connection: a newer server
                # may say things this build has never heard of.
                continue
            self.last_message_at = time.monotonic()
            self._inbox.put(message)

    async def _write(self, connection) -> None:
        """Move the outbox onto the wire without blocking either side.

        The queue read is a blocking call, so it runs in a worker thread; the
        alternative is polling it on a timer, which trades latency for CPU in
        the one place the game cannot afford either.
        """
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            message = await loop.run_in_executor(None, self._next_outbound)
            if message is None:
                return
            await connection.send(message.encode())

    def _next_outbound(self) -> Optional[Message]:
        while not self._stop.is_set():
            try:
                return self._outbox.get(timeout=0.25)
            except queue.Empty:
                continue
        return None

    async def _heartbeat(self, connection) -> None:
        """An application-level ping, which doubles as the latency probe.

        The library has its own protocol-level ping; this one is on top of it
        because it measures what the *game* experiences — a round trip through
        the same queues every command takes — and because a proxy that answers
        protocol pings itself would otherwise hide a dead server.
        """
        policy = self.config.heartbeat
        if not policy.enabled:
            return
        while not self._stop.is_set():
            await asyncio.sleep(policy.interval)
            await connection.send(Message.ping(time.monotonic()).encode())

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self.url.startswith("wss://"):
            return None
        tls = self.config.tls
        if tls.ca_file:
            return ssl.create_default_context(cafile=tls.ca_file)
        context = ssl.create_default_context()
        if not tls.verify:
            # Only ever right for a self-signed certificate on a machine the
            # player owns.  Off by default, and the menu says so.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context


def _readable(exc: Exception, url: str) -> str:
    """Turn a networking exception into something a player can act on."""
    if isinstance(exc, ConnectionRefusedError):
        return f"Serwer {url} odrzucił połączenie — czy jest uruchomiony?"
    if isinstance(exc, asyncio.TimeoutError):
        return f"Serwer {url} nie odpowiada"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (-2, -3, -5):
        return f"Nie znaleziono serwera {url}"
    text = str(exc).strip()
    return f"Utracono połączenie z serwerem ({text})" if text else \
        "Utracono połączenie z serwerem"
