"""
Sessions.

A session is the object the interface talks to.  It owns the game state and the
event bus and exposes two methods — ``submit`` and ``poll`` — so ``ui/`` is
written once and does not care whether the game is on this machine or shared
with four people in three countries.

* :class:`LocalSession` — hot-seat.  Commands are applied here and now.
* :class:`NetworkSession` — online.  Commands are *proposals*: they go to the
  server, and the state changes only when the server says it did.

WHY NOTHING IS APPLIED OPTIMISTICALLY.  Predicting the result locally would
shave a round trip off the feel of a card being played, and would have to be
undone whenever the prediction was wrong.  In this game a wrong prediction
means having briefly shown a hand that should have stayed hidden, or a card
count that gives away who Piotrek is — the rollback leaks more than the
latency costs.  So the client draws "sent, waiting" and the *animation* covers
the trip, which is the part the player actually perceives.  The interface never
blocks: ``submit`` returns immediately and the frame carries on.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from ..engine import commands as cmd
from ..engine import events as ev
from ..engine import victory
from ..engine.game_state import GameState
from .transport import ConnectionState, NullTransport, Transport


class NetworkStats:
    """Numbers for the debug panel.  Read-only to everything that displays it.

    Deliberately dumb: nothing here influences the game, so leaving the panel
    on costs nothing and turning it off hides nothing that matters.
    """

    def __init__(self, mode: str = "local") -> None:
        self.mode = mode
        self.state: ConnectionState = ConnectionState.CONNECTED
        self.room: str = ""
        self.server: str = ""
        self.seat: Optional[int] = None
        self.players: int = 0
        self.ping_ms: Optional[float] = None
        self.sent: int = 0
        self.received: int = 0
        self.last_sent: str = "—"
        self.last_received: str = "—"
        self.sequence: int = 0
        self.pending: int = 0
        self.resyncs: int = 0
        self.reconnects: int = 0
        #: Messages this build could not make sense of.  The player is never
        #: told; this is where the detail goes instead (F3).
        self.dropped_messages: int = 0
        #: Outbound messages waiting for the handshake to finish.  Non-zero
        #: here for more than a moment means the server is not answering.
        self.held: int = 0

    @property
    def connected(self) -> bool:
        return self.state.is_live

    def note_sent(self, kind: str) -> None:
        self.sent += 1
        self.last_sent = kind

    def note_received(self, kind: str) -> None:
        self.received += 1
        self.last_received = kind


class Session:
    """Base session: apply commands, publish the resulting events."""

    def __init__(self, state: GameState, bus: Optional[ev.EventBus] = None) -> None:
        self.state = state
        self.bus = bus or ev.EventBus()
        self.stats = NetworkStats()

    def submit(self, command: cmd.Command) -> List[ev.GameEvent]:
        events = self.state.apply(command)
        self.bus.emit_all(events)
        return events

    def poll(self) -> None:
        """Pump the network.  A local session has nothing to do."""

    def tick(self, now: float) -> List[ev.GameEvent]:
        """Periodic work the AUTHORITY owes the table.  Nothing, by default.

        A client's session has nothing to do here: the server owns the clock,
        and this machine learns that a decision window closed the way it learns
        everything else — by being sent the command.
        """
        return []

    def close(self) -> None:
        pass

    @property
    def disconnected(self) -> Optional[str]:
        return None

    @staticmethod
    def _rejected(events: List[ev.GameEvent]) -> bool:
        return any(isinstance(e, ev.ActionRejected) for e in events)


class LocalSession(Session):
    """One machine, no networking — and therefore its own authority.

    In an online match the server is the one that notices somebody has won; on
    one machine there is nobody else to notice, so this session runs the same
    :func:`~pedzacy_piotrek.engine.victory.review` after every command and
    applies whatever it answers.  The rules live in one module either way; only
    the caller differs.
    """

    def __init__(self, state: GameState, bus: Optional[ev.EventBus] = None) -> None:
        super().__init__(state, bus)
        self.transport: Transport = NullTransport()

    def tick(self, now: float) -> List[ev.GameEvent]:
        """Close a Nie masz Rosji window whose time is up.

        A hot-seat game has no server, so this session is the authority and
        owns the clock exactly as the room does online.  ``now`` is passed in
        rather than read here so the caller decides what "now" means — the
        interface passes real time, a test passes whatever it likes, and
        neither depends on a frame rate or on anything sleeping.
        """
        breakup = self.state.pending_breakup
        if breakup is not None:
            if breakup.opened_at is None:
                breakup.opened_at = now
                return []
            if now - breakup.opened_at < breakup.seconds:
                return []
            return self.submit_authoritative(cmd.ResolveTowerBreakup())
        # Ice Block's window first, and on the same clock: a hot-seat table is
        # its own authority, so the check must time out here exactly as it does
        # in the room.
        check = self.state.pending_check
        if check is not None:
            if check.opened_at is None:
                check.opened_at = now
                return []
            if now - check.opened_at < check.seconds:
                return []
            return self.submit_authoritative(cmd.ExpireCheckDecision())
        decision = self.state.pending_movement
        if decision is None:
            return []
        if decision.opened_at is None:
            decision.opened_at = now
            return []
        if now - decision.opened_at < decision.seconds:
            return []
        return self.submit_authoritative(cmd.ExpireMovementDecision())

    def submit_authoritative(self, command: cmd.Command) -> List[ev.GameEvent]:
        """Apply a command of the game's own, and review the outcome after it."""
        events = self.state.apply(command, local=False)
        self.bus.emit_all(events)
        for followed in victory.review(self.state):
            extra = self.state.apply(followed, local=False)
            self.bus.emit_all(extra)
            events.extend(extra)
        return events

    def submit(self, command: cmd.Command) -> List[ev.GameEvent]:
        events = super().submit(command)
        if self._rejected(events):
            return events
        for followed in victory.review(self.state):
            extra = self.state.apply(followed, local=False)
            self.bus.emit_all(extra)
            events.extend(extra)
        return events


class NetworkSession(Session):
    """An online match, seen from one player's machine.

    The state here is a *replica*: built from the same configuration and seed
    as the server's, and advanced only by commands the server has accepted.  It
    is never mutated any other way, which is what makes the fingerprint check
    meaningful — a difference can only come from a bug, never from local
    guesswork.
    """

    def __init__(self, state: GameState, send: Callable[[cmd.Command], None],
                 bus: Optional[ev.EventBus] = None, seat: int = 0,
                 stats: Optional[NetworkStats] = None) -> None:
        super().__init__(state, bus)
        self._send = send
        self.seat = seat
        self.stats = stats or NetworkStats(mode="client")
        #: Commands sent and not yet answered, so the interface can show that
        #: something is in flight rather than looking like it ignored a click.
        self.in_flight: int = 0

    def submit(self, command: cmd.Command) -> List[ev.GameEvent]:
        """Ask the server.  Returns nothing, because nothing has happened yet."""
        self._send(command)
        self.in_flight += 1
        self.stats.pending = self.in_flight
        return []

    def apply_authoritative(self, command: cmd.Command) -> List[ev.GameEvent]:
        """Apply a command the server has accepted.

        ``local=False`` on purpose: the server has already decided whose
        command this was and whether it was that player's turn.  Re-judging it
        against *this* machine's seat would reject every move anybody else
        makes and desync on the spot.
        """
        events = self.state.apply(command, local=False)
        self.bus.emit_all(events)
        if self.in_flight:
            self.in_flight -= 1
        self.stats.pending = self.in_flight
        return events

    def note_answer(self) -> None:
        """The server replied to something, accepted or not."""
        if self.in_flight:
            self.in_flight -= 1
        self.stats.pending = self.in_flight

    def replace_state(self, state: GameState) -> None:
        """Adopt a freshly rebuilt state after a sync, and start over cleanly."""
        self.state = state
        self.in_flight = 0
        self.stats.pending = 0

    def reject(self, reason: str, kind: str = "") -> None:
        self.note_answer()
        self.bus.emit(ev.ActionRejected(reason, kind))
