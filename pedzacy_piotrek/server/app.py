"""
The server process.

    python -m pedzacy_piotrek.server                 # 0.0.0.0:51337
    python -m pedzacy_piotrek.server --port 8080     # or set PORT
    python -m pedzacy_piotrek.server --rooms 8       # several groups at once

This file is the asyncio wrapper and nothing else.  Every decision the server
makes belongs to :class:`~pedzacy_piotrek.server.hub.ServerHub`, which is
synchronous and knows nothing about sockets; what is left here is accepting
connections, moving bytes, and a timer.  That split is deliberate: async code
is the hardest kind to test, so there should be as little of it as possible and
it should contain no rules.

DEPLOYMENT, WHICH IS THE PART THAT ACTUALLY ANSWERS THE BRIEF.  Two friends on
different continents, behind different routers, with no port forwarding, can
only reach each other if they both connect *outwards* to a machine that is
already reachable.  There is no client-side trick that avoids this — a home PC
behind NAT cannot accept an unsolicited connection, which is exactly why the
old "one player hosts" design could never work over the internet.  So this
process runs somewhere with a public address, and both players' games connect
to it.  ``docs/SERWER.md`` walks through doing that on a free hosting plan; the
short version is that it is one file, one command and no router settings.

TLS is not terminated here.  Every hosting platform worth using puts a proxy in
front that handles certificates and speaks ``wss://`` to the world while
speaking plain WebSocket to this process — which is why there is no certificate
handling in this file and why there should not be.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Dict, Optional

from ..cards.loader import ContentLibrary
from ..net.config import NetworkConfig
from ..net.protocol import Message
from .hub import ServerHub

LOGGER = logging.getLogger("piotrek.server")

try:  # pragma: no cover - depends on the installed version
    from websockets.asyncio.server import serve as _ws_serve

    _LEGACY = False
except ImportError:  # pragma: no cover - websockets < 14
    try:
        from websockets.legacy.server import serve as _ws_serve  # type: ignore

        _LEGACY = True
    except ImportError:  # pragma: no cover - library missing
        _ws_serve = None  # type: ignore
        _LEGACY = False


class GameServer:
    """A WebSocket endpoint in front of a :class:`ServerHub`."""

    def __init__(self, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None) -> None:
        if _ws_serve is None:  # pragma: no cover - guarded at startup
            raise RuntimeError(
                "Brak biblioteki „websockets” — zainstaluj: pip install websockets"
            )
        self.config = config or NetworkConfig.load()
        self.hub = ServerHub(self.config, library)
        self._sockets: Dict[str, object] = {}
        self._next_cid = 0
        self._server = None
        self._stopping = asyncio.Event()
        #: Set once the listener is actually bound, so callers (and tests) can
        #: wait for the port rather than sleeping and hoping.
        self.ready = asyncio.Event()
        self.port: int = self.config.server.port

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def run(self) -> None:
        server_config = self.config.server
        kwargs = {
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_size": server_config.max_message_bytes,
        }
        async with _ws_serve(self._handle, server_config.host,
                             server_config.port, **kwargs) as server:
            self._server = server
            self.port = _bound_port(server) or server_config.port
            self.ready.set()
            LOGGER.info("Serwer nasłuchuje na %s:%s (pokoje: %s)",
                        server_config.host, self.port, server_config.max_rooms)
            housekeeping = asyncio.ensure_future(self._housekeeping())
            try:
                await self._stopping.wait()
            finally:
                housekeeping.cancel()
        self.ready.clear()

    def stop(self) -> None:
        self._stopping.set()

    async def _housekeeping(self) -> None:
        """Grace periods and abandoned rooms, on a timer.

        A second is far more often than necessary for timeouts measured in
        minutes, and cheap: with no rooms it is a loop over an empty dict.
        """
        while not self._stopping.is_set():
            await asyncio.sleep(1.0)
            try:
                await self._dispatch(self.hub.tick())
            except Exception:  # pragma: no cover - never kill the server
                LOGGER.exception("Błąd w obsłudze czasu")

    # ── connections ──────────────────────────────────────────────────────────
    async def _handle(self, connection) -> None:
        self._next_cid += 1
        cid = f"c{self._next_cid}"
        self._sockets[cid] = connection
        address = _describe(connection)
        LOGGER.info("Połączenie %s z %s", cid, address)
        await self._dispatch(self.hub.connect(cid, address))
        try:
            async for raw in connection:
                if self.config.server.verbose:
                    LOGGER.debug("%s → %s", cid, raw)
                await self._dispatch(self.hub.receive(cid, raw))
        except Exception as exc:  # pragma: no cover - normal for a dropped peer
            LOGGER.debug("Połączenie %s zakończone: %s", cid, exc)
        finally:
            self._sockets.pop(cid, None)
            LOGGER.info("Rozłączenie %s", cid)
            try:
                await self._dispatch(self.hub.disconnect(cid))
            except Exception:  # pragma: no cover - defensive
                LOGGER.exception("Błąd przy rozłączaniu %s", cid)

    async def _dispatch(self, outbound) -> None:
        """Send everything the hub produced, concurrently.

        One slow or dead client must not hold up the rest of the table, so the
        sends run together and a failure is logged rather than raised — the
        socket layer will notice the closure on its own.
        """
        if not outbound:
            return
        tasks = []
        for cid, message in outbound:
            socket = self._sockets.get(cid)
            if socket is None:
                continue
            tasks.append(self._send(socket, cid, message))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send(self, socket, cid: str, message: Message) -> None:
        try:
            await socket.send(message.encode())
            if self.config.server.verbose:
                LOGGER.debug("%s ← %s", cid, message.type.value)
        except Exception as exc:
            LOGGER.debug("Nie udało się wysłać do %s: %s", cid, exc)


def _bound_port(server) -> Optional[int]:
    """The port actually bound, which matters when the request was 0."""
    sockets = getattr(server, "sockets", None) or []
    for sock in sockets:
        try:
            return int(sock.getsockname()[1])
        except (OSError, IndexError, TypeError):  # pragma: no cover
            continue
    return None


def _describe(connection) -> str:
    remote = getattr(connection, "remote_address", None)
    if isinstance(remote, tuple) and remote:
        return f"{remote[0]}:{remote[1] if len(remote) > 1 else '?'}"
    return "?"


# ── command line ─────────────────────────────────────────────────────────────
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m pedzacy_piotrek.server",
        description="Serwer gry „Pędzący Piotrek”",
    )
    parser.add_argument("--host", default=None,
                        help="adres nasłuchu (domyślnie z data/network.json)")
    parser.add_argument("--port", type=int, default=None,
                        help="port nasłuchu (albo zmienna środowiskowa PORT)")
    parser.add_argument("--rooms", type=int, default=None,
                        help="ile pokoi jednocześnie (domyślnie 1)")
    parser.add_argument("--verbose", action="store_true",
                        help="loguj każdą wiadomość")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> NetworkConfig:
    from dataclasses import replace

    config = NetworkConfig.load()
    changes = {}
    if args.host:
        changes["host"] = args.host
    if args.port is not None:
        changes["port"] = args.port
    if args.rooms is not None:
        changes["max_rooms"] = max(1, args.rooms)
    if args.verbose:
        changes["verbose"] = True
    if changes:
        config = replace(config, server=replace(config.server, **changes))
    return config


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
    )
    config = build_config(args)
    try:
        server = GameServer(config)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            handle = getattr(signal, name, None)
            if handle is None:
                continue
            try:
                loop.add_signal_handler(handle, server.stop)
            except (NotImplementedError, RuntimeError):  # pragma: no cover
                pass                                     # Windows
        await server.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    LOGGER.info("Serwer zatrzymany")
    return 0
