"""
A room: one lobby, and then one match.

This is where the authority actually lives.  The room owns the only
:class:`~pedzacy_piotrek.engine.game_state.GameState` that counts; every client
keeps a replica built from the same seed and fed the same accepted commands,
and the room's copy is the tie-breaker whenever they disagree.

**Deliberately synchronous and free of I/O.**  Nothing in this file knows what
a socket is; ``handle`` takes a message and returns a list of messages to send.
That is what makes the whole of multiplayer — seating, ownership, turn order,
disconnection, grace periods, state sync, desync recovery — testable in
milliseconds without a network, and it is why the asyncio layer above it is
under two hundred lines with no game logic in it at all.

THE COMMAND PIPELINE, which is the heart of the thing:

    client sends COMMAND
        │
        ├─ is this peer seated?            no → COMMAND_REJECTED
        ├─ authorise_remote(cmd, seat)     no → COMMAND_REJECTED   (whose seat,
        │                                                          whose turn)
        ├─ apply(cmd, local=False)
        │     ├─ ActionRejected            → COMMAND_REJECTED (sender only)
        │     ├─ ChoiceRequired            → CHOICE_REQUIRED  (sender only,
        │     │                              nothing changed, nothing logged)
        │     └─ applied                   → append to the log, and
        └────────────────────────────────────  COMMAND_ACCEPTED to everyone,
                                               carrying the new fingerprint.

The client never applies anything it has not been told about.  There is no
optimistic prediction and no rollback: this game hinges on hidden information
and exact card counts, and a client that guessed wrong would have to be told
what it guessed wrong about.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import (RULES, SessionConfig, clamp_ability_uses,
                               clamp_card_counts, clamp_mod_counts,
                               clean_card_variants)
from ..engine import commands as cmd
from ..engine import events as ev
from ..engine import victory
from ..engine.game_state import GameState
from ..engine.setup import create_game, new_seed
from ..engine.victory import MatchPhase
from ..net.config import NetworkConfig
from ..net.lobby import LobbyState, clean_nickname
from ..net.protocol import Message, MessageType, fingerprint_of

#: One outbound message, addressed to a player rather than to a connection.
#: The hub above translates peer ids into whatever it is holding open.
Outbound = Tuple[str, Message]


@dataclass
class RoomMember:
    """A player in this room, present or merely expected.

    ``connected`` going false does not free the seat.  A seat is released only
    when the grace period runs out or the player says they are leaving, because
    the alternative — dropping somebody the instant their WiFi hiccups — is the
    single most annoying thing an online board game can do.
    """

    peer_id: str
    nickname: str
    connected: bool = True
    #: When the connection was lost, for the grace period.  None while present.
    absent_since: Optional[float] = None
    #: Highest command sequence this player has been sent, for reconnection.
    acknowledged: int = 0


class Room:
    """One lobby and the match it becomes."""

    def __init__(self, code: str, config: Optional[NetworkConfig] = None,
                 library: Optional[ContentLibrary] = None,
                 clock=time.monotonic) -> None:
        self.code = code
        self.config = config or NetworkConfig()
        self._library = library
        self._clock = clock

        self.lobby = LobbyState(code=code)
        self.members: Dict[str, RoomMember] = {}

        #: The authoritative game.  None until the host starts the match.
        self.state: Optional[GameState] = None
        self.session_config: Optional[SessionConfig] = None
        #: Every accepted command, in the order it was applied.  Seed plus this
        #: list *is* the match — it is what a late joiner and a returning
        #: player are sent, and what makes a second representation of the game
        #: state unnecessary.
        self.command_log: List[Dict[str, Any]] = []
        #: Matches started in this room.  A room outlives its matches now
        #: (MATCH -> LOBBY -> MATCH), so "has this room played yet?" is no
        #: longer the same question as "is a match running?".
        self.matches_played: int = 0
        self.fingerprint: str = ""
        #: Which peer was asked for Piotrek's colour, and whether it arrived.
        #: The colour ITSELF is never stored here — it goes straight into the
        #: authoritative state, whose public snapshot does not carry it.
        self.identity_peer: Optional[str] = None
        self.identity_settled: bool = False
        self.closed: bool = False
        self.created_at: float = self._clock()
        self.empty_since: Optional[float] = self.created_at

    # ── properties ───────────────────────────────────────────────────────────
    @property
    def started(self) -> bool:
        return self.state is not None

    @property
    def library(self) -> ContentLibrary:
        if self._library is None:
            self._library = ContentLibrary.load()
        return self._library

    @property
    def abandoned(self) -> bool:
        """Nobody is here and nobody is expected back.

        NOT the same as "nobody is connected": a player whose socket dropped
        mid-match still HAS a seat, and their member entry is what
        ``expire_absentees`` is holding open for them.  Membership is what
        empties — through a deliberate exit, through a lobby disconnection, or
        through a grace period running out — and when it does there is nothing
        left in this room for anybody to come back to.
        """
        return not self.members

    @property
    def present(self) -> List[str]:
        return [m.peer_id for m in self.members.values() if m.connected]

    @property
    def sequence(self) -> int:
        return len(self.command_log)

    def member(self, peer_id: str) -> Optional[RoomMember]:
        return self.members.get(peer_id)

    def seat_of(self, peer_id: str) -> Optional[int]:
        seat = self.lobby.seat_of(peer_id)
        return None if seat is None else seat.seat

    # ── membership ───────────────────────────────────────────────────────────
    def join(self, peer_id: str, nickname: str,
             as_host: bool = False) -> Tuple[bool, str]:
        """Seat somebody.  Returns (accepted, reason-if-not)."""
        if self.closed:
            return False, "Ten pokój został zamknięty"
        if peer_id in self.members:
            return self.reconnect(peer_id)
        if self.started:
            # A match in progress only takes back people it already knows; a
            # stranger cannot be dealt a hand halfway through a game whose
            # hidden information was decided at the start.
            return False, "Gra już się rozpoczęła"
        seat = self.lobby.add_seat(peer_id, nickname, is_host=as_host)
        if seat is None:
            return False, f"Stół jest pełny (maksimum {RULES.max_players} graczy)"
        self.members[peer_id] = RoomMember(peer_id=peer_id, nickname=seat.nickname)
        self.empty_since = None
        return True, ""

    def reconnect(self, peer_id: str) -> Tuple[bool, str]:
        """Somebody who was already here is back."""
        member = self.members.get(peer_id)
        if member is None:
            return False, "Nie ma cię przy tym stole"
        member.connected = True
        member.absent_since = None
        seat = self.lobby.seat_of(peer_id)
        if seat is not None:
            seat.connected = True
        self.empty_since = None
        return True, ""

    def mark_absent(self, peer_id: str) -> List[Outbound]:
        """The connection went away.  Hold the seat and tell the table."""
        member = self.members.get(peer_id)
        if member is None or not member.connected:
            return []
        member.connected = False
        member.absent_since = self._clock()
        seat = self.lobby.seat_of(peer_id)
        if seat is not None:
            seat.connected = False
            seat.ready = False
        if not self.present:
            self.empty_since = self._clock()

        if not self.started:
            # Nobody has anything invested yet, so a seat held open would only
            # block the table.  Free it and renumber.
            return self.leave(peer_id, announce=False) + self.broadcast_lobby()

        grace = self.config.reconnect.grace_period
        name = seat.nickname if seat else member.nickname
        index = seat.seat if seat else -1
        return self.broadcast(
            Message.player_disconnected(peer_id, index, name, grace)
        )

    def leave(self, peer_id: str, announce: bool = True) -> List[Outbound]:
        """A deliberate exit, or a grace period that ran out."""
        member = self.members.pop(peer_id, None)
        seat = self.lobby.seat_of(peer_id)
        if member is None and seat is None:
            return []
        name = seat.nickname if seat else (member.nickname if member else "Gracz")
        index = seat.seat if seat else -1

        if self.started:
            # The seat index is baked into every command already in the log;
            # renumbering now would rewrite history.  The seat stays, empty.
            if seat is not None:
                seat.connected = False
                seat.ready = False
        else:
            self.lobby.remove_seat(peer_id)
            self.lobby.promote_new_host()

        if not self.present:
            self.empty_since = self._clock()
        out: List[Outbound] = []
        if announce and self.started:
            out.extend(self.broadcast(
                Message.player_disconnected(peer_id, index, name, 0.0)
            ))
        if announce and not self.started:
            out.extend(self.broadcast_lobby())
        return out

    def expire_absentees(self) -> List[Outbound]:
        """Release seats whose owner never came back.

        The match carries on without them: their pawn stays where it is and
        their turn is skipped by the ordinary turn rules, which is the least
        disruptive thing that can happen to the other four people.
        """
        if not self.started:
            return []
        grace = self.config.reconnect.grace_period
        now = self._clock()
        out: List[Outbound] = []
        for peer_id, member in list(self.members.items()):
            if member.connected or member.absent_since is None:
                continue
            if now - member.absent_since >= grace:
                out.extend(self.leave(peer_id))
        return out

    def expire_decisions(self) -> List[Outbound]:
        """End a Nie masz Rosji window whose time is up.

        THE AUTHORITY OWNS THE CLOCK.  The engine holds how LONG the window is
        (configuration, identical everywhere and in the snapshot); when it
        started is wall-clock time, which is different on every machine, so it
        is noted here and nowhere else.  A client's countdown is a picture of
        the command this produces arriving.

        Called from the same periodic tick that releases absent players, so it
        does not depend on anybody happening to send a message — which is
        precisely the situation a decision window times out in.
        """
        state = self.state
        if state is None or not self.started:
            return []
        now = self._clock()
        # Checking variant 2's two-second pause before the tower comes apart.
        # Same tick, same clock, no sleep.
        breakup = state.pending_breakup
        if breakup is not None:
            if breakup.opened_at is None:
                breakup.opened_at = now
                return []
            if now - breakup.opened_at < breakup.seconds:
                return []
            out = self._authoritative(cmd.ResolveTowerBreakup())
            out.extend(self.review_victory())
            return out
        # Ice Block's window is timed the same way and on the same tick, so a
        # check cannot sit open for ever because nobody happened to send a
        # message — which is exactly the situation a timeout exists for.
        check = state.pending_check
        if check is not None:
            if check.opened_at is None:
                check.opened_at = now
                return []
            if now - check.opened_at < check.seconds:
                return []
            out = self._authoritative(cmd.ExpireCheckDecision())
            out.extend(self.review_victory())
            return out
        decision = state.pending_movement
        if decision is None:
            return []
        if decision.opened_at is None:
            decision.opened_at = now
            return []
        if now - decision.opened_at < decision.seconds:
            return []
        out = self._authoritative(cmd.ExpireMovementDecision())
        out.extend(self.review_victory())
        return out

    # ── lobby changes a client may ask for ───────────────────────────────────
    def set_nickname(self, peer_id: str, nickname: str) -> List[Outbound]:
        seat = self.lobby.seat_of(peer_id)
        if seat is None:
            return []
        seat.nickname = self.lobby.unique_nickname(clean_nickname(nickname),
                                                   except_peer=peer_id)
        member = self.members.get(peer_id)
        if member is not None:
            member.nickname = seat.nickname
        if self.state is not None:
            player = self.state.player(seat.seat)
            if player is not None:
                player.name = seat.nickname
        return self.broadcast_lobby()

    def set_character(self, peer_id: str, character: str) -> List[Outbound]:
        """A player picks their own character; the server decides if they get it.

        Refused when somebody already has it.  The client's own list hides
        taken characters, but a client is not to be trusted on that — which is
        the whole reason this check is on this side of the wire.
        """
        seat = self.lobby.seat_of(peer_id)
        if seat is None or self.started:
            return []
        wanted = (character or "").strip()
        if wanted and wanted in self.lobby.taken_characters(except_peer=peer_id):
            return [(peer_id, Message.error("Ta postać jest już zajęta"))]
        seat.character = wanted
        return self.broadcast_lobby()

    def set_ready(self, peer_id: str, ready: bool) -> List[Outbound]:
        seat = self.lobby.seat_of(peer_id)
        if seat is None:
            return []
        seat.ready = bool(ready)
        return self.broadcast_lobby()

    def set_settings(self, peer_id: str, payload: Mapping[str, Any]
                     ) -> List[Outbound]:
        """Table settings.  Host only, checked here rather than in the menu."""
        if not self.lobby.is_host(peer_id) or self.started:
            return [(peer_id, Message.error("Tylko host zmienia ustawienia stołu"))]
        lobby = self.lobby
        if "board_cells" in payload:
            lobby.board_cells = max(RULES.board_cells_min,
                                    int(payload["board_cells"]))
        if "chest_open_round" in payload:
            lobby.chest_open_round = max(RULES.chest_open_min,
                                         int(payload["chest_open_round"]))
        if "mod_round_first" in payload:
            lobby.mod_round_first = max(RULES.mod_round_first_min,
                                        int(payload["mod_round_first"]))
        if "mod_round_interval" in payload:
            lobby.mod_round_interval = max(RULES.mod_round_interval_min,
                                           int(payload["mod_round_interval"]))
        if "mod_counts" in payload:
            # Merged, not replaced: the panel sends the titles it knows about,
            # and a host on an older build that sends none must not wipe the
            # deck.  Clamped here as well as in the panel because the server
            # settles what the settings ARE — a hand-written message is not
            # allowed to seat a deck with -3 copies of a card in it.
            incoming = clamp_mod_counts(payload.get("mod_counts"))
            merged = dict(lobby.mod_counts)
            merged.update(incoming)
            lobby.mod_counts = clamp_mod_counts(merged)
        # The other three mappings merge on exactly the same terms, through one
        # loop rather than three copies of the block above — four hand-written
        # merges differing only in their clamp is how one of them stops merging.
        # ``card_variants`` joins them on exactly the same terms — merged so a
        # panel that names one card does not wipe the settings of the others,
        # and cleaned here as well as in the panel because the server settles
        # what the settings ARE.
        for key, clamp in (("movement_counts", clamp_card_counts),
                           ("chest_counts", clamp_card_counts),
                           ("ability_uses", clamp_ability_uses),
                           ("card_variants", clean_card_variants)):
            if key in payload:
                merged = dict(getattr(lobby, key))
                merged.update(clamp(payload.get(key)))
                setattr(lobby, key, clamp(merged))
        if "block_decision_seconds" in payload:
            lobby.block_decision_seconds = max(
                RULES.block_decision_min,
                min(RULES.block_decision_max,
                    int(payload["block_decision_seconds"])))
        if "check_decision_seconds" in payload:
            lobby.check_decision_seconds = max(
                RULES.block_decision_min,
                min(RULES.block_decision_max,
                    int(payload["check_decision_seconds"])))
        # An unknown variant id is ignored rather than accepted: the lobby
        # keeps whatever it had, which is a legal table, instead of taking a
        # value the engine would have to fall back on anyway.
        for key, table in (("check_variant", settings.CHECK_VARIANTS),
                           ("victory_variant", settings.VICTORY_VARIANTS)):
            name = str(payload.get(key, ""))
            if name in table:
                setattr(lobby, key, name)
        if "double_percent" in payload:
            lobby.double_percent = max(0, min(100, int(payload["double_percent"])))
        if "debug_version" in payload:
            lobby.debug_version = bool(payload["debug_version"])
        if "edit_mode" in payload:
            lobby.edit_mode = bool(payload["edit_mode"])
        # READY IS CONSENT TO A TABLE, and changing the table withdraws it —
        # but ONLY once this room has actually played something.  Before the
        # first match the settings arrive as part of setting up: the host
        # screen sends them the moment the room exists, and the test harness
        # sends them while seating people, so clearing here would leave a lobby
        # that can never start because every settings message unreadies it.
        #
        # After a match it is the opposite.  The host returns to the poczekalni
        # mid-game, opens the settings and changes the deck, and the room would
        # otherwise still be unanimously ready for the game it just abandoned —
        # ready about a configuration nobody at the table has seen.
        #
        # A rematch on the SAME settings changes nothing and stays one click,
        # which is the behaviour ``return_to_lobby`` deliberately preserves.
        # The host's own flag is untouched because ``everyone_ready`` never
        # reads it: the host starts the match, which is consent enough.
        if self.matches_played:
            for seat in lobby.seats:
                if not seat.is_host:
                    seat.ready = False
        return self.broadcast_lobby()

    # ── starting the match ───────────────────────────────────────────────────
    def start(self, peer_id: str, seed: Optional[int] = None) -> List[Outbound]:
        """Build the authoritative game and tell everyone to build the same one.

        Only the configuration crosses the wire — including the seed, which is
        what makes every client's board, shuffle and dealing identical without
        a single card being transmitted.
        """
        if self.started:
            return [(peer_id, Message.error("Gra już trwa"))]
        if not self.lobby.is_host(peer_id):
            return [(peer_id, Message.error("Tylko host może rozpocząć grę"))]
        problem = self.lobby.validate()
        if problem:
            return [(peer_id, Message.error(problem))]

        config = self.lobby.to_config(seed=seed if seed is not None else new_seed())
        self.session_config = config
        # The server plays no seat.  ``local_seat`` is meaningless here and
        # ``edit_mode`` is off, so every command is judged by
        # ``authorise_remote`` against the seat map and nothing else.
        self.state = create_game(config, self.library)
        for seat in self.lobby.seats:
            player = self.state.player(seat.seat)
            if player is not None:
                player.name = seat.nickname
                player.owner_id = seat.peer_id
        self.lobby.started = True
        #: How many matches this room has hosted.  Read by ``_apply_settings``
        #: to tell "still setting the room up" from "changing a table people
        #: have already agreed to".
        self.matches_played += 1
        self.command_log = []
        self.fingerprint = fingerprint_of(self.state.snapshot())
        self.identity_settled = False
        self.identity_peer = None

        payload = Message.game_start(asdict(config), self.lobby.seat_map(),
                                     room=self.code)
        out = self.broadcast(payload)
        out.extend(self.ask_for_identity())
        return out

    # ── the hidden identity ──────────────────────────────────────────────────
    def ask_for_identity(self) -> List[Outbound]:
        """Ask whoever drew Piotrek which colour he is hiding behind.

        To that one peer.  Everybody else is looking at "Gra się rozpoczyna…"
        and is told nothing at all — not even that somebody is being asked
        something, because the round trip is short and the wait is shared.
        """
        if self.state is None or self.identity_settled:
            return []
        if self.state.phase is not MatchPhase.STARTING:
            return []
        seat = self.state.piotrek_seat
        if seat is None:
            # No Piotrek at this table (a content or setup problem, not a
            # player's fault).  Rather than hang on a question nobody can
            # answer, start the match — it is still playable.
            return self._authoritative(cmd.BeginMatch())
        lobby_seat = next((s for s in self.lobby.seats if s.seat == seat), None)
        if lobby_seat is None:
            return []
        self.identity_peer = lobby_seat.peer_id
        pawns = [{"id": pawn.id, "name": pawn.name, "color": list(pawn.color)}
                 for pawn in self.state.library.pawns]
        return [(self.identity_peer,
                 Message.identity_required(pawns, room=self.code))]

    def set_identity(self, peer_id: str, pawn_id: str) -> List[Outbound]:
        """Piotrek has chosen.  Store it, tell him, and start everybody at once.

        Checked against the server's own seat map, like every other message:
        a client claiming to be Piotrek is just a client claiming something.
        """
        if self.state is None:
            return [(peer_id, Message.error("Gra jeszcze się nie zaczęła"))]
        swapping = self.state.identity_swap == self.state.SWAP_CHOOSING
        if self.identity_settled and not swapping:
            return [(peer_id, Message.error("Kolor został już wybrany"))]
        if peer_id != self.identity_peer:
            return [(peer_id, Message.error("To nie ty wybierasz kolor"))]
        if str(pawn_id) == self.state.swap_forbidden_pawn():
            return [(peer_id, Message.error("Ten kolor został właśnie odkryty"))]
        if not self.state.set_piotrek_pawn(str(pawn_id)):
            return [(peer_id, Message.error("Nieznany kolor pionka"))]
        self.identity_settled = True
        # The fingerprint does not move: the colour is not in the snapshot,
        # which is exactly what keeps five replicas agreeing with a server
        # that knows something they do not.
        out: List[Outbound] = [(peer_id, Message.identity_accepted(str(pawn_id)))]
        if swapping:
            # Alter Ego: the table is already playing and is merely paused, so
            # it resumes rather than starting.  The resume is a COMMAND for the
            # ordinary reason — every replica has to leave the pause on the same
            # message rather than each guessing that the answer arrived.
            out.extend(self._authoritative(cmd.FinishIdentitySwap()))
        else:
            out.extend(self._authoritative(cmd.BeginMatch()))
        return out

    # ── the command pipeline ─────────────────────────────────────────────────
    def submit(self, peer_id: str, payload: Mapping[str, Any]) -> List[Outbound]:
        sequence = int(payload.get("seq", 0) or 0)
        raw = payload.get("command")
        if self.state is None:
            return [(peer_id, Message.command_rejected(
                "Gra jeszcze się nie zaczęła", sequence))]
        if not isinstance(raw, Mapping):
            return [(peer_id, Message.command_rejected(
                "Pusta komenda", sequence))]

        try:
            command = cmd.Command.from_dict(dict(raw))
        except (ValueError, TypeError, KeyError) as exc:
            return [(peer_id, Message.command_rejected(str(exc), sequence))]

        seat = self.seat_of(peer_id)
        if seat is None:
            return [(peer_id, Message.command_rejected(
                "Nie masz miejsca przy stole", sequence, command.kind))]

        problem = self.state.authorise_remote(command, seat)
        if problem is not None:
            return [(peer_id, Message.command_rejected(problem, sequence,
                                                       command.kind))]

        events = self.state.apply(command, local=False)
        rejection = next((e for e in events if isinstance(e, ev.ActionRejected)),
                         None)
        if rejection is not None:
            return [(peer_id, Message.command_rejected(rejection.reason, sequence,
                                                       command.kind))]

        if any(isinstance(e, ev.ChoiceRequired) for e in events):
            # Legal, but a decision is missing.  Nothing changed, so nothing is
            # logged and nobody else hears about it; the asking player's own
            # engine will raise the same question when it applies this locally.
            return [(peer_id, Message.choice_required(command.to_dict(), sequence))]

        encoded = command.to_dict()
        self.command_log.append(encoded)
        self.fingerprint = fingerprint_of(self.state.snapshot())
        out = self.broadcast(
            Message.command_accepted(encoded, self.sequence, seat,
                                     self.fingerprint)
        )
        # ...and then: did that just end the game?  The question is asked here,
        # once, after every accepted command, rather than inside the rules for
        # moving a pawn — which is what keeps win conditions out of the effect
        # engine and out of the interface.
        out.extend(self.review_victory())
        return out

    # ── the authority's own commands ─────────────────────────────────────────
    def review_victory(self) -> List[Outbound]:
        """Let :mod:`engine.victory` look at the table and act on its answer."""
        if self.state is None:
            return []
        out: List[Outbound] = []
        for followed in victory.review(self.state):
            out.extend(self._authoritative(followed))
        out.extend(self.ask_for_new_identity())
        return out

    def ask_for_new_identity(self) -> List[Outbound]:
        """Alter Ego: hand the colour question back, minus the colour just left.

        The SAME message the opening uses, so Piotrek's machine opens the same
        overlay and nothing new had to be invented on either side — the only
        difference is a shorter list of pawns.

        Asked at most once per swap: ``identity_peer`` is cleared when the
        answer arrives, so a swap that is already waiting does not re-ask on
        every command the table would otherwise process (there are none, but a
        reconnection still runs through here).
        """
        state = self.state
        if state is None:
            return []
        if state.identity_swap != state.SWAP_CHOOSING:
            return []
        if self.identity_peer is not None and not self.identity_settled:
            return []          # already asked, still waiting
        seat = state.piotrek_seat
        lobby_seat = next((s for s in self.lobby.seats if s.seat == seat), None)
        if lobby_seat is None:
            return []
        self.identity_peer = lobby_seat.peer_id
        self.identity_settled = False
        forbidden = state.swap_forbidden_pawn()
        pawns = [{"id": pawn.id, "name": pawn.name, "color": list(pawn.color)}
                 for pawn in state.library.pawns if pawn.id != forbidden]
        return [(self.identity_peer,
                 Message.identity_required(pawns, room=self.code))]

    def _authoritative(self, command: cmd.Command) -> List[Outbound]:
        """Apply a command of the server's own, and log it like any other.

        Deliberately the same road every player command takes: it goes into the
        log, it moves the fingerprint, and it reaches everybody as
        ``COMMAND_ACCEPTED``.  So a player who reconnects five minutes later
        replays it, sees the same colour crossed off and the same winner, and
        no second mechanism exists for "things the server decided".

        The seat is ``-1``: nobody's.
        """
        if self.state is None:
            return []
        events = self.state.apply(command, local=False)
        if not events or any(isinstance(e, ev.ActionRejected) for e in events):
            return []
        encoded = command.to_dict()
        self.command_log.append(encoded)
        self.fingerprint = fingerprint_of(self.state.snapshot())
        return self.broadcast(
            Message.command_accepted(encoded, self.sequence, -1,
                                     self.fingerprint)
        )

    # ── after the match ──────────────────────────────────────────────────────
    def return_to_lobby(self, peer_id: str) -> List[Outbound]:
        """Put the room back the way it was, ready for another match.

        TWO CALLERS, ONE TRANSITION.  After somebody has won, anybody may ask —
        the match is over and there is nothing left to end.  While it is still
        running only the HOST may, because a mid-match reset ends everybody
        else's game and that is a room-level decision; the host is already the
        one who may start a match and close the room, so it is their say here
        too.  A non-host asking mid-match is refused exactly as it always was.

        Seats survive — the same people are still sitting there — but
        everything the match consisted of is dropped rather than wound back:
        the state, the config, the log, the fingerprint and the hidden colour.
        The next match is BUILT FRESH from the lobby in :meth:`start`, which is
        the only reason a stale hand or an old board cannot follow it there.

        Ready flags are deliberately KEPT.  They are what makes "jeszcze raz"
        one click for the host, and the people who set them are still sitting
        in the same seats.  Changing the settings is what clears them — see
        :meth:`_apply_settings` — because ready is consent to a table, and that
        is the moment the table changes.
        """
        if self.state is None:
            return self.lobby_message(peer_id)
        if not self.state.finished and not self.lobby.is_host(peer_id):
            return [(peer_id,
                     Message.error("Tylko host może wrócić do poczekalni"))]
        self.state = None
        self.session_config = None
        self.command_log = []
        self.fingerprint = ""
        self.identity_peer = None
        self.identity_settled = False
        self.lobby.started = False
        for seat in list(self.lobby.seats):
            # Anybody who left during the match kept their seat so the log
            # stayed valid.  The log is gone now, so the seat can go too.
            if seat.peer_id not in self.members:
                self.lobby.remove_seat(seat.peer_id)
        self.lobby.promote_new_host()
        return self.broadcast_lobby()

    # ── synchronisation ──────────────────────────────────────────────────────
    def sync_message(self) -> Optional[Message]:
        """The whole match, as configuration plus the accepted command log.

        This is the only message that carries a match's worth of data, and it
        is sent exactly twice in a normal game's life: to somebody arriving
        late, and to somebody coming back.  Everything else is one action.
        """
        if self.state is None or self.session_config is None:
            return None
        return Message.state_sync(
            asdict(self.session_config), self.lobby.seat_map(),
            list(self.command_log), self.sequence, self.fingerprint,
            room=self.code,
        )

    def resync(self, peer_id: str) -> List[Outbound]:
        message = self.sync_message()
        if message is None:
            return self.lobby_message(peer_id)
        return [(peer_id, message)]

    def catch_up(self, peer_id: str) -> List[Outbound]:
        """Everything a returning player needs, in one go."""
        out = self.lobby_message(peer_id)
        message = self.sync_message()
        if message is not None:
            out.append((peer_id, message))
        seat = self.lobby.seat_of(peer_id)
        if seat is not None and self.started:
            out.extend(self.broadcast(
                Message.player_reconnected(peer_id, seat.seat, seat.nickname),
                skip=peer_id,
            ))
        if peer_id == self.identity_peer and not self.identity_settled:
            # Piotrek dropped while choosing.  Nobody else can answer for him
            # and the table is waiting, so ask again the moment he is back.
            out.extend(self.ask_for_identity())
        elif peer_id == self.identity_peer and self.state is not None:
            # ...and if he already chose, tell him again what he chose.  A
            # returning client rebuilds its replica from the seed and the log,
            # and the colour is in neither — by design.  Without this the badge
            # in his own panel would come back empty for the rest of the match.
            colour = self.state.piotrek_pawn
            if colour:
                out.append((peer_id, Message.identity_accepted(colour)))
        return out

    # ── outbound helpers ─────────────────────────────────────────────────────
    def broadcast(self, message: Message,
                  skip: Optional[str] = None) -> List[Outbound]:
        message.room = self.code
        return [(peer_id, message) for peer_id in self.present if peer_id != skip]

    def broadcast_lobby(self) -> List[Outbound]:
        return self.broadcast(Message.lobby_state(self.lobby.to_dict(), self.code))

    def lobby_message(self, peer_id: str) -> List[Outbound]:
        return [(peer_id, Message.lobby_state(self.lobby.to_dict(), self.code))]

    def close(self, reason: str = "Pokój został zamknięty") -> List[Outbound]:
        out = self.broadcast(Message(MessageType.MATCH_ENDED, {"reason": reason},
                                     room=self.code))
        self.closed = True
        return out

    # ── housekeeping ─────────────────────────────────────────────────────────
    def is_stale(self) -> bool:
        """Nobody has been here for longer than the idle timeout."""
        if self.empty_since is None:
            return False
        return (self._clock() - self.empty_since) >= self.config.server.room_idle_timeout
