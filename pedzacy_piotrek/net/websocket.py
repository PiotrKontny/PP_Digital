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

AND EXACTLY ONE THREAD, WHICH IS THE POINT.  The outbox used to be read with
``loop.run_in_executor(None, ...)`` around a blocking ``queue.get``, which put a
*second* thread — one belonging to asyncio's default ThreadPoolExecutor — inside
a loop it only left when :attr:`_stop` was set.  ``concurrent.futures.thread``
registers a ``threading._register_atexit`` hook that joins every executor worker
it ever created, DAEMON FLAG INCLUDED, and that hook runs inside
``threading._shutdown()`` before daemon threads are abandoned.  So a game that
exited without closing its transport — which is every game that was closed by
its window button — left the interpreter blocked forever in ``_python_exit``,
joining a worker that was never going to return.  The window was gone, the
process was not, and it could not be stopped from a debugger because the main
thread was inside a non-interruptible ``join()``.

The writer below therefore waits on an :class:`asyncio.Event` woken from the
game thread with ``call_soon_threadsafe``, and no executor is involved: the
queue is only ever drained without blocking.  That removes the shutdown hazard
at its source rather than arranging for the flag to be set more reliably.

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
from .messages import (BAD_ADDRESS, CANNOT_CONNECT, CONNECTION_LOST,
                       NO_ADDRESS, SERVER_NOT_FOUND, SERVER_UNAVAILABLE,
                       friendly)
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
            raise TransportError(NO_ADDRESS)

        self.config = config or NetworkConfig()
        self.url = url
        self.description = url

        self._inbox: "queue.Queue[Message]" = queue.Queue()
        self._outbox: "queue.Queue[Message]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        #: Set from the GAME thread whenever there is something to send or the
        #: transport has been asked to stop.  Lives on the network thread's
        #: loop; see :meth:`_wake`, which is the only way it is ever touched
        #: from outside.
        self._pending: Optional[asyncio.Event] = None

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
        """Send what is already queued, then let go.  Safe to call twice.

        THE OUTBOX IS DRAINED, NOT DROPPED.  The last thing a leaving player
        puts on it is the message that says so, and a close that raced it
        turned a deliberate departure into a socket that merely stopped
        answering — which the server correctly treats as a temporary failure
        and holds a seat open for.  The writer below finishes its queue before
        it returns, and only then is the connection shut.

        The wait is bounded by ``config.shutdown_timeout`` and is a ceiling
        rather than a delay: the network thread is woken immediately and
        normally finishes in milliseconds.
        """
        if self.state is ConnectionState.CLOSED and self._thread is None:
            return
        self._stop.set()
        self._wake()                    # drain the queue and shut the socket
        self.state = ConnectionState.CLOSED
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.config.shutdown_timeout)

    def _wake(self) -> None:
        """Tell the network thread there is something to do.

        Called from the GAME thread, which is why it goes through
        ``call_soon_threadsafe`` rather than touching the event directly.  A
        loop that has already finished is not an error: there is nothing left
        to wake, and whatever was queued is going nowhere either way.
        """
        loop, pending = self._loop, self._pending
        if loop is None or pending is None:
            return
        try:
            loop.call_soon_threadsafe(pending.set)
        except RuntimeError:
            # The loop closed between the two lines above.  The thread it ran
            # on is already finished, so there is no waiter left to notify.
            return

    # ── Transport ────────────────────────────────────────────────────────────
    def send(self, message: Message) -> None:
        if self._stop.is_set():
            return
        self._outbox.put(message)
        self._wake()

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
            self._fail(SERVER_UNAVAILABLE)
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
        # Built here rather than in ``__init__`` so it belongs to the loop that
        # will actually wait on it.  Anything the game sent before this moment
        # is already in the outbox and is drained by the writer's first pass.
        self._pending = asyncio.Event()
        try:
            loop.run_until_complete(self._supervise())
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(friendly(str(exc), default=CONNECTION_LOST))
            self.state = ConnectionState.CLOSED
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover - shutting down anyway
                pass
            loop.close()
            self._loop = None
            self._pending = None

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
                self._fail(friendly(str(exc), default=CANNOT_CONNECT))

            if self._stop.is_set():
                break
            if not policy.enabled:
                self.state = ConnectionState.CLOSED
                return
            self.attempt += 1
            if policy.max_attempts and self.attempt > policy.max_attempts:
                self._fail(self.error or CANNOT_CONNECT)
                self.state = ConnectionState.CLOSED
                return
            self.state = ConnectionState.RECONNECTING
            await self._backoff(policy.delay_for(self.attempt))
        self.state = ConnectionState.CLOSED

    async def _backoff(self, delay: float) -> None:
        """Wait out the retry delay, but not past a request to stop.

        A plain sleep here would keep the network thread inside an eight-second
        pause it could not be woken from, so closing the game while the server
        was unreachable meant waiting for the backoff to end before anything
        was released.  The wake event is set by :meth:`close`, so the wait ends
        the moment there is a reason for it to.
        """
        pending = self._pending
        if pending is None:
            await asyncio.sleep(delay)
            return
        pending.clear()
        try:
            await asyncio.wait_for(pending.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return                      # the delay simply elapsed

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
            self._fail(BAD_ADDRESS)
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

        Nothing here ever blocks a thread: the queue is drained with
        ``get_nowait`` and the coroutine then suspends on an event the game
        thread sets.  The previous version handed a blocking ``queue.get`` to
        asyncio's default executor, and that worker — which the interpreter
        joins at exit whatever its daemon flag says — is what used to keep the
        process alive for ever.  See the module docstring.

        THE ORDER OF THE THREE STEPS IS LOAD-BEARING.  The event is cleared
        *before* the queue is inspected, so a message that arrives during the
        drain leaves the event set and is collected on the next pass instead of
        waiting for one that never comes.  The stop flag is read *after* the
        drain, so a goodbye queued a moment before ``close()`` still reaches the
        server.
        """
        pending = self._pending
        while True:
            if pending is not None:
                pending.clear()
            while True:
                try:
                    message = self._outbox.get_nowait()
                except queue.Empty:
                    break
                await connection.send(message.encode())
            if self._stop.is_set() or pending is None:
                return
            await pending.wait()

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
    """Turn a networking exception into something a player can act on.

    The URL is deliberately NOT interpolated any more.  It was, and the result
    was a player being shown ``ws://piotrek-server.up.railway.app:51337`` in a
    red error line, which tells them nothing they can act on and looks like a
    crash.  The address is on the screen they just came from and in the debug
    panel; the error line gets the sentence.
    """
    if isinstance(exc, ConnectionRefusedError):
        return CANNOT_CONNECT
    if isinstance(exc, asyncio.TimeoutError):
        return SERVER_UNAVAILABLE
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (-2, -3, -5):
        return SERVER_NOT_FOUND
    return friendly(str(exc), default=CONNECTION_LOST)
