"""
Networking configuration.

Every networking number in the project is declared here and nowhere else.  A
hard-coded address or timeout is the thing that turns "play with a friend
abroad" into "edit the source and rebuild", so the rule is absolute: if a value
concerns the wire, it lives in :class:`NetworkConfig` and is loaded from
``data/network.json``.

Four layers, later ones winning:

1. the defaults in this file — enough to play against a server on this machine;
2. ``data/network.json``, which is what the owner edits after deploying;
3. the remembered address in the user's own preferences file — the last server
   that a room was actually created on or joined through.  It sits above the
   shipped file so that a player who typed an address once never has to type it
   again, and below the environment so that automation still wins;
4. environment variables, which is how hosting platforms configure a process
   (Railway, Render, Fly and friends all inject ``PORT``, and refusing to read
   it is the single most common reason a deployment answers nothing).

Nothing here imports pygame or websockets, so the dedicated server can import
it without pulling in a display library.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse, urlunparse

from ..config.settings import DATA_DIR

#: Where the configuration file lives.  Shipped with the game and editable by
#: hand; the owner changes ``server_url`` once, after deploying the server.
NETWORK_FILE = DATA_DIR / "network.json"


def user_config_dir() -> Path:
    """A directory this user may write to, whatever the game was installed as.

    ``data/network.json`` is part of the *installation*: it ships with the game,
    the owner edits it deliberately, and under a PyInstaller build it may be
    inside a read-only bundle or a temporary extraction directory that is
    deleted on exit.  So it is the wrong place to record something the game
    decides by itself, like "the last server that worked" — which is why that
    goes here instead, next to every other program's per-user settings.

    Falls back to the home directory, and ultimately to the package's own data
    directory, rather than raising: failing to remember an address must never
    be the thing that stops the game from starting.
    """
    try:
        if os.name == "nt":
            base = os.environ.get("APPDATA")
            root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            base = os.environ.get("XDG_CONFIG_HOME")
            root = Path(base) if base else Path.home() / ".config"
        return root / "pedzacy-piotrek"
    except (OSError, RuntimeError):  # pragma: no cover - exotic environments
        return DATA_DIR


#: Settings the *game* writes, as opposed to the ones the owner writes.
def preferences_file() -> Path:
    return user_config_dir() / "preferences.json"


def load_preferences() -> Dict[str, Any]:
    """Read the remembered settings.  Never raises; a bad file is no file."""
    try:
        with open(preferences_file(), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return dict(loaded) if isinstance(loaded, Mapping) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save_preferences(values: Mapping[str, Any]) -> bool:
    """Merge and write.  Returns False rather than raising when it cannot.

    A read-only home directory, a full disk or a locked profile all end up
    here, and none of them is worth interrupting a game for.
    """
    merged = load_preferences()
    merged.update(values)
    try:
        path = preferences_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return True
    except OSError:
        return False


def is_usable_url(url: str) -> bool:
    """Is this something the game could actually connect to next time?

    Guards the remembered value, and it is not a formality.  What gets offered
    to :func:`remember_server_url` is whatever the transport calls itself, and
    the in-process transport used by the tests calls itself ``in-process:c3``.
    Saved unchecked, that became ``ws://in-process:c3:51337``, whose port is
    not a number — and every later ``urlparse(...).port`` raised ValueError,
    including the one behind the "Serwer gry:" line on the main menu.  One
    unusable string in a preferences file broke the game's first screen until
    the file was deleted by hand.
    """
    try:
        parsed = urlparse(normalise_url(url or ""))
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            return False
        parsed.port                     # raises ValueError when it is nonsense
    except ValueError:
        return False
    return True


def remember_server_url(url: str) -> bool:
    """Record a server address that actually worked.

    Called when a room is successfully created or joined — NOT when one is
    merely typed.  Remembering an address that never connected would helpfully
    re-fill the field with the typo that caused the problem.
    """
    url = (url or "").strip()
    if not url or not is_usable_url(url):
        return False
    return save_preferences({"server_url": url})


def remembered_server_url() -> str:
    value = load_preferences().get("server_url")
    return str(value) if isinstance(value, str) else ""

#: Bumped whenever the message vocabulary changes in a way old builds cannot
#: understand.  The server refuses a mismatched client with a readable reason
#: rather than letting it desync in mysterious ways twenty minutes later.
PROTOCOL_VERSION = 2

#: Default port for a server started on this machine.
DEFAULT_PORT = 51337


@dataclass(frozen=True)
class ReconnectPolicy:
    """How hard, and for how long, a client tries to get back in.

    Exponential backoff with a ceiling: a phone that moves between two WiFi
    access points is back in under a second, while a server that is genuinely
    down is not hammered once a frame.
    """

    enabled: bool = True
    #: 0 means "keep trying until the player gives up and leaves".
    max_attempts: int = 0
    initial_delay: float = 0.5
    max_delay: float = 8.0
    backoff: float = 1.8
    #: How long the *server* holds a seat open for someone who vanished.  After
    #: this the seat is released and the match carries on without them.
    grace_period: float = 180.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before attempt number ``attempt`` (1-based)."""
        if attempt <= 1:
            return self.initial_delay
        delay = self.initial_delay * (self.backoff ** (attempt - 1))
        return min(self.max_delay, delay)


@dataclass(frozen=True)
class HeartbeatPolicy:
    """Keepalive.

    Two jobs, and they are not the same one.  The interval keeps NAT tables and
    idle-connection reapers (every cloud load balancer has one) from quietly
    dropping a connection where nobody is speaking, which is most of a board
    game.  The timeout is how a peer notices that a connection which was never
    closed has stopped working — a cable pulled out does not send a FIN.
    """

    interval: float = 5.0
    timeout: float = 20.0

    @property
    def enabled(self) -> bool:
        return self.interval > 0


@dataclass(frozen=True)
class TlsConfig:
    """Encryption settings.

    ``wss://`` in the URL is what actually turns TLS on; these are the knobs for
    the awkward cases — a self-signed certificate on a home server, or a private
    certificate authority.  Verification defaults to on and should stay that way
    for anything on the public internet.
    """

    verify: bool = True
    #: Optional path to a CA bundle, for a self-signed or private certificate.
    ca_file: Optional[str] = None


@dataclass(frozen=True)
class ServerConfig:
    """Settings for the process that *runs* the server."""

    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    #: Close a room this many seconds after the last player leaves.  Keeps a
    #: long-lived server from accumulating abandoned matches.
    room_idle_timeout: float = 900.0
    #: Largest message accepted, in bytes.  A command is a few dozen; a state
    #: sync of a long match is a few tens of kilobytes.
    max_message_bytes: int = 1 << 20
    #: How many rooms may exist at once.  One lobby is the requirement today;
    #: the registry has always been able to hold more, so this is the only
    #: number that needs changing to open it up.
    max_rooms: int = 1
    #: Log every message.  Very loud; for debugging a real deployment.
    verbose: bool = False


@dataclass(frozen=True)
class NetworkConfig:
    """Everything the networking layer is allowed to know about the wire."""

    #: Where the game connects.  ``ws://`` for plain, ``wss://`` for TLS.
    server_url: str = f"ws://127.0.0.1:{DEFAULT_PORT}"
    #: Shown in the menus as the "play with friends anywhere" option.  Empty
    #: until the owner deploys a server and fills it in.  Unused by the
    #: screens today; the remembered address serves that purpose.
    public_server_url: str = ""
    #: Seconds to wait for the initial connection before saying so.
    connect_timeout: float = 8.0
    #: Seconds a client waits for the server to accept an action before warning
    #: the player.  The action is not cancelled — this only drives the display.
    action_timeout: float = 10.0
    #: Seconds :meth:`WebSocketTransport.close` waits for the network thread to
    #: put the last message on the wire and shut the socket.  It is a CEILING,
    #: not a delay: the thread is woken immediately and normally finishes in
    #: milliseconds.  The wait exists because the last message a leaving player
    #: sends is the one that tells the server they left — abandoning it turns a
    #: deliberate exit into a dropped socket, which the server answers with a
    #: reconnect grace period.
    shutdown_timeout: float = 2.0
    reconnect: ReconnectPolicy = field(default_factory=ReconnectPolicy)
    heartbeat: HeartbeatPolicy = field(default_factory=HeartbeatPolicy)
    tls: TlsConfig = field(default_factory=TlsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    # ── derived ──────────────────────────────────────────────────────────────
    @property
    def is_secure(self) -> bool:
        return urlparse(self.server_url).scheme == "wss"

    @property
    def local_server_url(self) -> str:
        """Where a server started on this machine would be reached."""
        return f"ws://127.0.0.1:{self.server.port}"

    def with_url(self, url: str) -> "NetworkConfig":
        return replace(self, server_url=normalise_url(url, self.server.port))

    def describe_target(self) -> str:
        """The server address in a form worth showing a human.

        Never raises.  It is called while drawing the main menu, so a
        malformed address must produce a shrug rather than take the first
        screen of the game down with it.
        """
        try:
            parsed = urlparse(self.server_url)
            host = parsed.hostname or "?"
            port = parsed.port
        except ValueError:
            return self.server_url or "?"
        return f"{host}:{port}" if port else host

    # ── serialisation ────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NetworkConfig":
        """Build from a mapping, ignoring anything it does not recognise.

        Tolerant on purpose: a configuration file written for a newer build
        must not stop an older one from starting, and a typo should cost the
        one setting rather than the whole file.
        """
        def section(name: str, factory):
            data = raw.get(name)
            if not isinstance(data, Mapping):
                return factory()
            fields = factory().__dataclass_fields__
            return factory(**{k: v for k, v in data.items() if k in fields})

        return cls(
            server_url=normalise_url(str(raw.get("server_url") or
                                         cls.server_url), DEFAULT_PORT),
            public_server_url=str(raw.get("public_server_url") or ""),
            connect_timeout=float(raw.get("connect_timeout", 8.0)),
            action_timeout=float(raw.get("action_timeout", 10.0)),
            shutdown_timeout=float(raw.get("shutdown_timeout", 2.0)),
            reconnect=section("reconnect", ReconnectPolicy),
            heartbeat=section("heartbeat", HeartbeatPolicy),
            tls=section("tls", TlsConfig),
            server=section("server", ServerConfig),
        )

    @classmethod
    def load(cls, path: Optional[Path] = None,
             env: Optional[Mapping[str, str]] = None) -> "NetworkConfig":
        """File, then environment.  Never raises: a broken file falls back.

        A configuration error must not be the thing that stops the game from
        opening — the player would have no way to see the message.
        """
        path = NETWORK_FILE if path is None else path
        raw: Dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, Mapping):
                raw = dict(loaded)
        except (OSError, json.JSONDecodeError):
            raw = {}
        config = cls.from_dict(raw)
        # Three layers, and the order is the point.  The shipped file is what
        # the owner configured; the remembered address is what last actually
        # worked on THIS machine, so it wins over the shipped default; the
        # environment wins over both, because that is how a hosting platform
        # and the --server flag speak.
        remembered = remembered_server_url()
        if remembered and is_usable_url(remembered):
            # Checked again on the way in, not only on the way out: the file
            # is editable by hand and may predate the check above.
            config = config.with_url(remembered)
        return config.with_environment(os.environ if env is None else env)

    def with_environment(self, env: Mapping[str, str]) -> "NetworkConfig":
        """Apply the environment overrides a hosting platform sets.

        ``PORT`` is the important one: every platform-as-a-service picks the
        port itself and tells the process through that variable.  A server that
        ignores it binds the wrong port and answers nothing, which looks
        exactly like a broken deployment.
        """
        config = self
        url = env.get("PIOTREK_SERVER_URL")
        if url:
            config = config.with_url(url)
        public = env.get("PIOTREK_PUBLIC_SERVER_URL")
        if public:
            config = replace(config, public_server_url=public)

        server = config.server
        host = env.get("PIOTREK_SERVER_HOST") or env.get("HOST")
        port = env.get("PIOTREK_SERVER_PORT") or env.get("PORT")
        rooms = env.get("PIOTREK_MAX_ROOMS")
        changes: Dict[str, Any] = {}
        if host:
            changes["host"] = host
        if port:
            try:
                changes["port"] = int(port)
            except ValueError:
                pass
        if rooms:
            try:
                changes["max_rooms"] = max(1, int(rooms))
            except ValueError:
                pass
        if env.get("PIOTREK_SERVER_VERBOSE"):
            changes["verbose"] = True
        if changes:
            config = replace(config, server=replace(server, **changes))
        return config

    def save(self, path: Optional[Path] = None) -> None:
        path = NETWORK_FILE if path is None else path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")


def normalise_url(url: str, default_port: int = DEFAULT_PORT) -> str:
    """Turn whatever the player typed into a WebSocket URL.

    People paste ``example.com``, ``example.com:51337``, ``https://example.com``
    and ``ws://example.com/`` interchangeably, and every one of them means the
    same thing.  Guessing correctly here is the difference between joining a
    game and reading a connection error.

    THE ONE GUESS THAT MATTERS is what a bare hostname means, and it is not one
    answer.  ``192.168.0.14`` or ``localhost`` is somebody's own machine on
    their own network: plain ``ws://`` on the game's port, because nothing on a
    home network has a certificate.  ``piotrek.up.railway.app`` is a deployed
    server behind a hosting platform's proxy: ``wss://`` on 443, because that
    is the only thing such a proxy answers.  Getting this backwards is silent
    and total — the address looks right, and every connection is refused — so
    the distinction is drawn here rather than left to the player to know.
    """
    url = (url or "").strip()
    if not url:
        return ""
    had_scheme = "://" in url
    if not had_scheme:
        url = f"ws://{url}"
    parsed = urlparse(url)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("ws", "wss"):
        scheme = "ws"
    netloc = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""

    host = netloc.rsplit("]", 1)[-1]
    has_port = ":" in host
    if not had_scheme and not has_port and _is_public_hostname(netloc):
        # A bare public domain name: a deployed server, reached through a proxy
        # that terminates TLS on 443.
        scheme = "wss"
    if not has_port and scheme == "ws":
        # No port and no TLS: a bare hostname almost always means the game's
        # own port rather than 80, which is a web server's.
        netloc = f"{netloc}:{default_port}"
    path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def _is_public_hostname(netloc: str) -> bool:
    """Does this look like a name on the internet rather than a local machine?

    A dotted name that is not an IP address and not a ``.local``/``localhost``
    is something DNS resolved for us, which in this project means a deployed
    server behind a hosting proxy.
    """
    host = netloc.split("@")[-1].split(":")[0].strip("[]").lower()
    if not host or host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    if host.endswith(".local") or host.endswith(".localhost"):
        return False
    if re.fullmatch(r"[0-9.]+", host) or ":" in host:
        return False        # IPv4 literal, or an IPv6 address
    return "." in host


#: The live configuration.  Private, and reached through :func:`current`.
#:
#: A module-level constant would be captured by every ``from ... import NETWORK``
#: at import time, so changing it later — which is exactly what ``--server`` and
#: a settings screen need to do — would update the name here and nothing else.
#: One accessor means there is one answer to "which server are we using".
_ACTIVE: NetworkConfig = NetworkConfig.load()


def current() -> NetworkConfig:
    """The configuration the game is running on right now."""
    return _ACTIVE


def use(config: NetworkConfig) -> NetworkConfig:
    """Install a configuration as the live one."""
    global _ACTIVE
    _ACTIVE = config
    return _ACTIVE


def reload_network_config(path: Optional[Path] = None) -> NetworkConfig:
    """Re-read the file and publish the result."""
    return use(NetworkConfig.load(path))


def override_server_url(url: str) -> NetworkConfig:
    """Point the game at a different server, from the command line or a menu."""
    return use(_ACTIVE.with_url(url))
