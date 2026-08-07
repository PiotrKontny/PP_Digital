"""
The authoritative game state.

This module contains the entire rulebook and imports no pygame.  You can run
it in a terminal, in a test, or in a future dedicated host process.  Every
mutation arrives as a :class:`~pedzacy_piotrek.engine.commands.Command` and
leaves as a list of :class:`~pedzacy_piotrek.engine.events.GameEvent`.

All of the prototype's behaviour is preserved:

* five decks with draw/discard/reshuffle;
* a hand limit of 8, drawn from any of the three table decks;
* the two-slot "Mody Patusa" rack that pushes cards in from the left and
  discards whatever falls off the right;
* the character panel — draw a character, draw Piotrek's skill, discard the
  card currently on show;
* the hunters' colour-elimination notepad;
* free dragging of the six pawns, a manual round counter, and player renaming.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..board.board import BoardModel
from ..board.tiles import Tile
from ..cards.base_card import Card, CardDef, EffectSpec, Pawn
from ..cards.deck import Deck
from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import RULES, SessionConfig
from ..players.player import Player
from ..players.roles import Role
from . import commands as cmd
from . import effects
from . import events as ev
from .statuses import STATUS_LABELS, Status, StatusKind, StatusTracker, Subject
from .turn_order import (NextTurn, TurnSlot, chest_recipient_for_round,
                         compute_round_turn_order)
from .victory import MatchPhase, Verdict

Point = Tuple[float, float]

#: How many turns in a row may be taken over by a card before the engine stops
#: chaining them.  Six players each holding a Troll is five interrupts and a
#: playable turn; anything past this is a content bug, and a table that hangs
#: is worse than a card that quietly does nothing.
MAX_TURN_INTERRUPTS = 12

#: Depth limit for cards that draw cards that draw cards.  Troll replaces
#: itself, and the replacement can be another Troll.
MAX_DRAW_CHAIN = 8


@dataclass
class ModSelection:
    """A paused round while both factions choose their Mod Patusa.

    Piotrek picks one of three by clicking; the hunters vote on three of their
    own and the count decides.  The two halves finish independently — Piotrek
    does not wait for the vote and the vote does not wait for Piotrek — and the
    selection is over when both have.

    Everything here is REAL GAME STATE and travels in the snapshot: a client
    that rebuilt its replica mid-selection and did not know a vote had been cast
    would disagree with the server about who won it.
    """

    round_number: int
    #: Seat holding Piotrek, or ``None`` in a table that has no Piotrek.
    piotrek_seat: Optional[int] = None
    #: The three cards each faction was dealt, in the order they were dealt —
    #: which is the order they are drawn in, and therefore what "LEFTMOST" means
    #: when a vote ties.
    piotrek_cards: List[Card] = field(default_factory=list)
    hunter_cards: List[Card] = field(default_factory=list)
    #: Seats entitled to vote.  Fixed when the selection opens, so a seat that
    #: somehow changes faction mid-selection cannot stall it for ever.
    hunter_seats: List[int] = field(default_factory=list)
    #: seat → uid.  A seat appears once; voting again replaces its entry.
    votes: Dict[int, int] = field(default_factory=dict)
    piotrek_done: bool = False
    hunters_done: bool = False

    @property
    def finished(self) -> bool:
        return self.piotrek_done and self.hunters_done

    @property
    def everyone_voted(self) -> bool:
        return bool(self.hunter_seats) and len(self.votes) >= len(self.hunter_seats)

    def tally(self) -> Dict[int, int]:
        """uid → votes, including the cards nobody picked."""
        counts = {card.uid: 0 for card in self.hunter_cards}
        for uid in self.votes.values():
            if uid in counts:
                counts[uid] += 1
        return counts

    def winner(self) -> Optional[Card]:
        """The most-voted card; ties go to the LEFTMOST of the tied cards.

        ``max`` over the cards in dealt order does exactly that, because it
        keeps the first maximum it meets — the leftmost tied card is the one
        already sitting furthest left on screen.
        """
        if not self.hunter_cards:
            return None
        counts = self.tally()
        return max(self.hunter_cards, key=lambda card: counts.get(card.uid, 0))

    def is_tied(self) -> bool:
        """Whether the winner only won because it was leftmost."""
        counts = self.tally()
        if not counts:
            return False
        best = max(counts.values())
        return sum(1 for value in counts.values() if value == best) > 1


@dataclass
class TokenState:
    """A pawn on (or beside) the board."""

    pawn: Pawn
    position: Point
    tile_index: Optional[int] = None
    #: Set while a human is dragging it; suppresses animation.
    held: bool = False

    @property
    def id(self) -> str:
        return self.pawn.id

    @property
    def color(self) -> tuple[int, int, int]:
        return self.pawn.color


class GameState:
    """Everything that makes up a game in progress."""

    def __init__(
        self,
        library: ContentLibrary,
        config: SessionConfig,
        board: BoardModel,
        players: List[Player],
        decks: Dict[str, Deck],
        rng: random.Random,
    ) -> None:
        self.library = library
        self.config = config
        self.board = board
        self.players = players
        self.decks = decks
        self.rng = rng

        self.round_number: int = 1
        self.active_player_index: int = 0
        #: Advances every time the seat changes.  Statuses expire against it,
        #: so "for one full turn" means something exact and reproducible.
        self.turn_counter: int = 0
        #: Position within the current round's turn order.
        self.turn_slot: int = 0
        self.mod_slots: List[Optional[Card]] = [None] * RULES.mod_slots
        #: Every persistent gameplay state in play (frozen, linked, bonuses…).
        self.statuses = StatusTracker()
        #: Chest cards waiting for their owner to choose which to keep.
        #: A QUEUE, because a dealing round now feeds TWO seats and both can go
        #: over the limit on the same round.  While this was a single slot the
        #: second overflow silently overwrote the first, leaving that player
        #: permanently over the limit and taking their extra card out of
        #: circulation for good — which drained the eight-card chest deck after
        #: a few rounds and looked exactly like a distribution bug (N95).
        self._pending_chest_choices: List[Tuple[int, List[int]]] = []
        #: The round is paused while both factions pick a Mod Patusa.
        self.pending_mod_selection: Optional[ModSelection] = None
        #: How deep the current draw-causes-a-draw chain is.  Not game state —
        #: it never leaves a command — so it stays out of the snapshot.
        self._draw_depth: int = 0
        #: Mod card uid -> the round it entered the rack.  Three of the mods
        #: need to know not merely THAT they are active but WHEN they became
        #: active: Paczka shows its window once on arrival, Squid Game starts
        #: checking the round AFTER it appears, and Shady hides a pawn once and
        #: never again.  One mapping serves all three, and dropping an entry
        #: when its card leaves the rack is what makes a mod that comes back
        #: later a fresh arrival rather than a spent one.
        self.armed_mods: Dict[int, int] = {}
        #: The colour Squid Game's automatic check is about to inspect, or
        #: ``None``.  Computed from PUBLIC information in ``_begin_round`` so
        #: every replica agrees on who is being checked; only the authority
        #: turns it into a verdict, because only the authority knows the
        #: hidden colour.  Cleared by whichever command settles it.
        self.pending_lead_check: Optional[str] = None

        self.tokens: Dict[str, TokenState] = {}
        for i, pawn in enumerate(library.pawns):
            self.tokens[pawn.id] = TokenState(pawn=pawn, position=board.camp_position(i))

        #: Where the match is: waiting for Piotrek to choose a colour, running,
        #: or finished.  An online match starts in STARTING and is let out of
        #: it by ``BeginMatch``; a hot-seat game has nobody to wait for and
        #: starts playing at once.
        self.phase: MatchPhase = (MatchPhase.STARTING
                                  if config.piotrek_picks_pawn
                                  else MatchPhase.PLAYING)
        #: Colours a failed check has ruled out, in the order they were ruled
        #: out.  PUBLIC — this is the notepad, and it is the same on every
        #: machine because it arrives as a command like everything else.
        self.eliminated_pawns: List[str] = []
        #: Set once, by ``DeclareVictory``.  Its presence *is* "the game is
        #: over", which is why nothing else needs a second flag.
        self.victory: Optional[Verdict] = None

        # Turn-order roster: fixed for the whole game, shuffled once at start.
        self.piotrek_name: Optional[str] = None
        self.hunter_names: List[str] = []
        self._build_roster()

    # ── roster ───────────────────────────────────────────────────────────────
    def _build_roster(self) -> None:
        piotrek_owner = next((p for p in self.players if p.is_piotrek), None)
        self.piotrek_name = (
            piotrek_owner.character.title if piotrek_owner and piotrek_owner.character else None
        )
        hunters = [p for p in self.players if p is not piotrek_owner]
        self.rng.shuffle(hunters)
        self.hunter_names = [
            (p.character.title if p.character is not None else p.name) for p in hunters
        ]

    # ── convenience accessors ────────────────────────────────────────────────
    @property
    def active_player(self) -> Player:
        return self.players[self.active_player_index]

    @property
    def chest_open_round(self) -> int:
        return self.config.chest_open_round

    @property
    def chest_is_open(self) -> bool:
        return self.round_number >= self.chest_open_round

    @property
    def chest_is_sparse(self) -> bool:
        """Whether this table only gets a chest card every second eligible round.

        Small tables were the problem: the rota is short, so 'every eligible
        round' comes back to the same hunter far too quickly and the chest
        stopped being an event.  Five and six players keep the original
        cadence exactly.

        Derived from the table size, which is fixed for the whole match and the
        same on every machine, so this needs no command, no RNG and nothing in
        the snapshot — every replica answers it identically.
        """
        return len(self.players) <= RULES.chest_sparse_max_players

    def chest_awards_cards(self, round_number: Optional[int] = None) -> bool:
        """Whether THIS round actually hands a chest card out.

        Distinct from :attr:`chest_is_open` on purpose.  On a small table the
        chest can be open — the rota still turns, the indicator still moves —
        while this particular round awards nothing.  The interface reads this
        to decide whether the indicator is filled or outlined, and the engine
        reads it to decide whether to deal; one source of truth for both, so
        the marker can never promise a card that does not arrive.
        """
        number = round_number if round_number is not None else self.round_number
        if number < self.chest_open_round:
            return False
        if not self.chest_is_sparse:
            return True
        interval = max(1, int(RULES.chest_sparse_interval))
        # Counted from the opening round, so the chest ALWAYS awards on the
        # round it opens and then skips every other one.
        return (number - self.chest_open_round) % interval == 0

    # ── the Mod Patusa schedule ──────────────────────────────────────────────
    @property
    def mod_round_first(self) -> int:
        return max(RULES.mod_round_first_min, int(self.config.mod_round_first))

    @property
    def mod_round_interval(self) -> int:
        return max(RULES.mod_round_interval_min, int(self.config.mod_round_interval))

    def is_mod_round(self, round_number: Optional[int] = None) -> bool:
        """Whether this round pauses for a Mod Patusa selection.

        Rounds before the first one never do; from there it is every
        ``mod_round_interval``-th round, so the defaults (3, 2) give 3, 5, 7…
        """
        number = round_number if round_number is not None else self.round_number
        if number < self.mod_round_first:
            return False
        return (number - self.mod_round_first) % self.mod_round_interval == 0

    def next_mod_round(self, after: Optional[int] = None) -> int:
        """The next round that will pause, counting the current one.

        The round panel wants to say 'next mods: round 5' the way it already
        announces the chest, so the arithmetic lives here rather than being
        re-derived in the interface.
        """
        number = after if after is not None else self.round_number
        if number <= self.mod_round_first:
            return self.mod_round_first
        gap = (number - self.mod_round_first) % self.mod_round_interval
        return number if gap == 0 else number + (self.mod_round_interval - gap)

    @property
    def mod_selection_open(self) -> bool:
        return self.pending_mod_selection is not None

    # ── what the active mods change ──────────────────────────────────────────
    @property
    def active_mods(self) -> List[Card]:
        """The mods in play, left slot first.  Empty slots are not mods."""
        return [card for card in self.mod_slots if card is not None]

    def mod_rule(self, key: str, default: Any = None) -> Any:
        """What the rack says about one rule, or ``default`` when it is silent.

        A mod changes the rules for as long as it sits in the rack, which is
        exactly what a ``passive`` is — the same field ChatGPT uses to shrink
        Piotrek's hand.  So the rules are DECLARED in cards.json and read here,
        and no part of the engine ever asks a mod what it is called.  Adding a
        mod that caps movement, or one that locks abilities, is a JSON entry.

        The LEFT slot wins a disagreement, because it is the slot the most
        recently installed mod takes (``_install_mod`` pushes into 0) and the
        one Piotrek owns during a selection.  Nothing declares a conflicting
        pair today; the rule exists so that the day something does, both
        machines resolve it the same way instead of by dictionary order.
        """
        for card in self.active_mods:
            value = card.passive.get(key)
            if value is not None:
                return value
        return default

    @property
    def abilities_locked(self) -> bool:
        """True while a mod forbids character abilities (Sesja na PG).

        Remaining uses are NOT touched by this — the lock is a question asked
        at the moment of use, so a player who still had two charges when the
        mod arrived still has two when it leaves.
        """
        return bool(self.mod_rule("abilities_locked", False))

    @property
    def movement_cap(self) -> Optional[int]:
        """Largest distance a movement CARD may declare (Masa solna).

        ``None`` means uncapped.  It caps what is printed on the card, not the
        whole move: a movement bonus is a status somebody spent an ability on,
        not a movement card, and this must not silently cancel it.
        """
        value = self.mod_rule("movement_cap")
        return None if value is None else max(0, int(value))

    @property
    def requires_neighbour(self) -> bool:
        """True while lone pawns are pinned in place (Halloween)."""
        return bool(self.mod_rule("require_neighbour", False))

    @property
    def reverses_backward_moves(self) -> bool:
        """True while a backward card may be turned around (Speedrun)."""
        return bool(self.mod_rule("reverse_backward", False))

    @property
    def chest_cards_revealed(self) -> bool:
        """True while every Chest card is public knowledge (Paczka)."""
        return bool(self.mod_rule("reveal_chest", False))

    @property
    def lead_check_only(self) -> bool:
        """True while the ONLY way to check a colour is the automatic one.

        Squid Game replaces the checking mechanic rather than adding to it:
        stacking the whole table onto one field stops meaning anything, and the
        pawn out in front is inspected once a round instead.  Both halves of
        that are read from this one rule, so they cannot get out of step.
        """
        return bool(self.mod_rule("lead_check_only", False))

    @property
    def hides_leader(self) -> bool:
        """True while a mod takes the leading pawn off the map (Shady)."""
        return bool(self.mod_rule("hide_leader", False))

    # ── the Chest card that changes a whole round (Gambit Patusa) ────────────
    @property
    def movement_reversed(self) -> bool:
        """True while every movement card travels the opposite way.

        Deliberately NOT a ``mod_rule``: Gambit Patusa is a Chest card, not a
        Mod Patusa, so its rule is not something the rack can answer.  It is a
        promise made in one round about the NEXT one, which is why it lives in
        a status carrying a round number — and why it needs no bookkeeping to
        end, since a status naming round 7 simply stops matching in round 8.
        """
        return self.statuses.movement_reversed_in(self.round_number)

    # ── pawns that are not on the map (Shady) ────────────────────────────────
    def pawn_is_hidden(self, pawn_id: str) -> bool:
        """Whether this pawn has been taken off the map for a round.

        A hidden pawn is not merely invisible: it is ignored by movement, by
        targeting, by the neighbour test and by checking.  Everything that
        walks the pawn list asks this, which is why it is one question with one
        answer rather than a condition repeated in six places.
        """
        return self.statuses.pawn_has(StatusKind.HIDDEN, pawn_id)

    @property
    def hidden_pawn_ids(self) -> Tuple[str, ...]:
        return tuple(
            status.subject_id
            for status in self.statuses.of_kind(StatusKind.HIDDEN)
        )

    @property
    def visible_pawns(self) -> List[Pawn]:
        """Every pawn that is actually on the table.

        Checking counts against this rather than against the palette, which is
        the whole of Shady's exception to the checking rule: while a pawn is
        off the map the hunters only have to gather the ones that are left.
        """
        return [pawn for pawn in self.library.pawns
                if not self.pawn_is_hidden(pawn.id)]

    def leading_pawn(self) -> Optional[str]:
        """The single pawn furthest along the road, or ``None`` if it is a tie.

        Squid Game checks the leader ONLY when the lead is unshared: two pawns
        level with each other means there is no one pawn out in front, and the
        round's check is skipped rather than being broken by a tie-break.  A
        pawn still in the camp has not started and can never lead, and a hidden
        pawn is not on the board to lead at all.

        Public, deterministic and free of the hidden colour, so every replica
        computes the same answer — which is what lets the check be armed on
        every machine and judged on only one.
        """
        best: Optional[int] = None
        leaders: List[str] = []
        for pawn in self.visible_pawns:
            index = self.board.position_of_pawn(pawn.id)
            if index is None:
                continue
            if best is None or index > best:
                best, leaders = index, [pawn.id]
            elif index == best:
                leaders.append(pawn.id)
        if best is None or len(leaders) != 1:
            return None
        return leaders[0]

    def deck(self, deck_id: str) -> Deck:
        return self.decks[deck_id]

    def player(self, index: int) -> Optional[Player]:
        if 0 <= index < len(self.players):
            return self.players[index]
        return None

    def turn_order(self, round_number: Optional[int] = None) -> List[TurnSlot]:
        return compute_round_turn_order(
            round_number if round_number is not None else self.round_number,
            self.piotrek_name,
            self.hunter_names,
        )

    @property
    def chest_interval(self) -> int:
        """How many rounds pass between chest hand-outs at this table size."""
        return (max(1, int(RULES.chest_sparse_interval)) if self.chest_is_sparse
                else 1)

    def chest_recipient(self, round_number: Optional[int] = None) -> Optional[str]:
        return chest_recipient_for_round(
            round_number if round_number is not None else self.round_number,
            self.chest_open_round,
            self.hunter_names,
            self.chest_interval,
        )

    # ── the turn loop ────────────────────────────────────────────────────────
    def seat_order(self, round_number: Optional[int] = None) -> List[int]:
        """The round's turn order as seat indices.

        The cadence itself comes from ``turn_order.py`` unchanged; this just
        works in seats instead of names, because two players may share a name
        and seats are what commands carry.
        """
        piotrek = next((p.index for p in self.players if p.is_piotrek), None)
        hunters = [p.index for p in self.players if not p.is_piotrek]
        slots = compute_round_turn_order(
            round_number if round_number is not None else self.round_number,
            None if piotrek is None else str(piotrek),
            [str(index) for index in hunters],
        )
        return [int(slot.name) for slot in slots]

    def chest_recipient_seat(self, round_number: Optional[int] = None) -> Optional[int]:
        """Which seat is due this round's chest card.

        Works in seats rather than names: ``hunter_names`` holds *character*
        titles, so matching them against player names quietly found nobody.
        """
        hunters = [p.index for p in self.players if not p.is_piotrek]
        if not hunters:
            return None
        name = chest_recipient_for_round(
            round_number if round_number is not None else self.round_number,
            self.chest_open_round,
            [str(index) for index in hunters],
            self.chest_interval,
        )
        return None if name is None else int(name)

    def current_slot(self) -> int:
        """Where in this round's order the active seat is standing.

        ``turn_slot`` is a CURSOR and is the authority: a seat can occupy more
        than one slot in a round (Piotrek occupies every third one), so the
        seat number alone cannot say where in the round we are.  This only
        repairs the cursor when it has genuinely come adrift — a seat set
        directly in edit mode, or a state built before the cursor was carried.
        """
        order = self.seat_order()
        if not order:
            return 0
        if 0 <= self.turn_slot < len(order) \
                and order[self.turn_slot] == self.active_player_index:
            return self.turn_slot
        return self._slot_for_seat(order, self.active_player_index, self.turn_slot)

    @staticmethod
    def _slot_for_seat(order: List[int], seat: int, from_slot: int = 0) -> int:
        """The seat's slot at or after ``from_slot``, else its first, else 0.

        Searching forward matters: rewinding to the first occurrence of a seat
        that appears several times is exactly the bug that made the round
        restart for ever at Piotrek.
        """
        for position in range(max(0, from_slot), len(order)):
            if order[position] == seat:
                return position
        for position, occupant in enumerate(order):
            if occupant == seat:
                return position
        return 0

    def next_turn(self) -> NextTurn:
        """Who plays after the current seat, in which round, and in which slot.

        The slot comes back with the seat because the caller cannot recompute
        it: ``order.index(seat)`` finds the FIRST slot that seat occupies, and
        for a seat that appears several times in a round that silently rewinds
        the round to its beginning.  That was the turn-order bug — the game
        looped over the first three slots for ever and the seats further down
        the round never played at all.
        """
        order = self.seat_order()
        if not order:
            return NextTurn(self.active_player_index, self.round_number, 0)
        position = self.current_slot()
        if position + 1 < len(order):
            return NextTurn(order[position + 1], self.round_number, position + 1)
        next_round = self.round_number + 1
        following = self.seat_order(next_round)
        if not following:
            return NextTurn(self.active_player_index, self.round_number, position)
        return NextTurn(following[0], next_round, 0)

    def next_seat(self) -> Tuple[int, int]:
        """``(seat, round)`` for callers that only want to look ahead.

        Advancing the turn must use :meth:`next_turn`, which also reports the
        slot to move the cursor to.
        """
        upcoming = self.next_turn()
        return upcoming.seat, upcoming.round_number

    def find_card(self, uid: int) -> Optional[Card]:
        for player in self.players:
            card = player.card_by_uid(uid)
            if card is not None:
                return card
        for card in self.mod_slots:
            if card is not None and card.uid == uid:
                return card
        return None

    def pawn_of_player(self, player: Player) -> Optional[str]:
        return player.secret_pawn

    # ── the hidden identity ──────────────────────────────────────────────────
    @property
    def piotrek_seat(self) -> Optional[int]:
        for player in self.players:
            if player.is_piotrek:
                return player.index
        return None

    @property
    def piotrek_pawn(self) -> Optional[str]:
        """The hidden colour, on the copies of the state entitled to know it.

        The authority's copy has it because it was told privately; Piotrek's
        own machine has it because it is his own secret.  Every other client
        holds ``None`` here for the whole match and finds out at the reveal.
        """
        seat = self.piotrek_seat
        player = self.player(seat) if seat is not None else None
        return player.secret_pawn if player is not None else None

    def set_piotrek_pawn(self, pawn_id: str) -> bool:
        """Record the chosen colour.  Refuses an unknown or a second choice.

        Never reached through a command, and that is the point: a command is
        logged and broadcast, and this must not be either.
        """
        seat = self.piotrek_seat
        player = self.player(seat) if seat is not None else None
        if player is None or player.secret_pawn:
            return False
        if self.library.pawn(pawn_id) is None:
            return False
        player.secret_pawn = pawn_id
        return True

    @property
    def finished(self) -> bool:
        return self.phase is MatchPhase.ENDED

    # ── command dispatch ─────────────────────────────────────────────────────
    #: Commands that act on behalf of one player.  Outside edit mode the local
    #: seat is the only one this machine may issue them for.
    _OWNED_BY_PLAYER = (
        cmd.DrawCard, cmd.DiscardCard, cmd.PlayCard, cmd.UseAbility,
        cmd.PlaceMod, cmd.KeepChestCards, cmd.DrawCharacter, cmd.DrawSkill,
        cmd.DiscardTopCharacterCard, cmd.ToggleMark, cmd.RenamePlayer,
        cmd.EndTurn, cmd.ChooseMod, cmd.VoteMod,
    )

    #: Commands that are a *move*.  Outside edit mode they may only be issued
    #: on your own turn; everybody else is watching.  Crossing colours off the
    #: notepad, renaming yourself and answering the chest limit are deliberately
    #: not here — they are private bookkeeping, not moves.
    _TURN_BOUND = (
        cmd.DrawCard, cmd.DiscardCard, cmd.PlayCard, cmd.UseAbility,
        cmd.PlaceMod, cmd.DiscardMod, cmd.DrawCharacter, cmd.DrawSkill,
        cmd.DiscardTopCharacterCard, cmd.PickUpToken, cmd.MoveToken,
        cmd.EndTurn,
    )

    def apply(self, command: cmd.Command, local: bool = True) -> List[ev.GameEvent]:
        """Apply a command and return what happened.

        ``local`` marks a command this machine is originating.  Commands that
        arrive from the network are ``local=False``: the host has already
        checked whose they were and whether it was that player's turn, and a
        client re-checking them against *its own* seat would reject everything
        anybody else does and desync on the spot.
        """
        """Validate and apply one command.  Never raises on bad input."""
        if local:
            refusal = self._authorise(command)
            if refusal is not None:
                return [refusal]
        handler = self._HANDLERS.get(type(command))
        if handler is None:
            return [ev.ActionRejected("Nieobsługiwana komenda", type(command).__name__)]
        try:
            return handler(self, command)
        except Exception as exc:  # pragma: no cover - defensive
            return [ev.ActionRejected(f"Błąd wykonania: {exc}", type(command).__name__)]

    def apply_many(self, batch: Sequence[cmd.Command]) -> List[ev.GameEvent]:
        out: List[ev.GameEvent] = []
        for command in batch:
            out.extend(self.apply(command))
        return out

    # ── handlers: cards ──────────────────────────────────────────────────────
    def _draw_card(self, command: cmd.DrawCard) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        deck = self.decks.get(command.deck_id)
        if deck is None:
            return [ev.ActionRejected("Nieznana talia", command.kind)]
        if player.hand_is_full:
            return [ev.ActionRejected(f"Ręka pełna ({RULES.max_hand} kart)", command.kind)]

        events: List[ev.GameEvent] = []
        needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
        card = deck.take_card()
        if card is None:
            return [ev.ActionRejected(f"Talia „{deck.name}” jest pusta", command.kind)]
        if needed_reshuffle:
            events.append(ev.DeckReshuffled(deck.id))
        player.add_card(card)
        events.append(ev.CardDrawn(player.index, deck.id, card.uid))
        events.extend(self._after_draw(player, card))
        return events

    @staticmethod
    def _variant_effect(variant) -> Optional[EffectSpec]:
        """A transformed card may bring its own effect along.

        Alter Ego and Kingmaker have none yet — but when they get one it is a
        JSON entry inside the variant, not a change here.
        """
        raw = variant.get("effect")
        return EffectSpec.from_dict(raw) if raw else None

    def _after_draw(self, player: Player, card: Card) -> List[ev.GameEvent]:
        """Everything that happens because a card arrived in a hand.

        Two rules live here: a chest card may push the player over their chest
        limit, and some cards announce themselves before they settle (the
        Gamechanger reveal).  Both are declared in data, not detected by title.
        """
        events: List[ev.GameEvent] = []

        presentation = card.presentation
        if presentation is not None and presentation.type == "role_reveal":
            role = "piotrek" if player.is_piotrek else "hunter"
            variant = presentation.variant(role) or {}
            from_title = card.title
            # The transformation is real: what lands in the hand is the card the
            # player was promised, not the one that was printed on the back of
            # the deck.  Announcing it without swapping it left a Gamechanger
            # sitting in the hand pretending to be an Alter Ego.
            card.transform(
                replace(
                    card.definition,
                    title=str(variant.get("title", card.title)),
                    text=str(variant.get("text", card.text)),
                    effect=self._variant_effect(variant),
                    presentation=None,
                )
            )
            events.append(ev.CardTransformed(
                player_index=player.index,
                card_uid=card.uid,
                from_title=from_title,
                to_title=card.title,
                to_text=card.text,
                intro_text=str(presentation.get("intro_text", "")),
                delay=presentation.delay,
            ))

        # What a card does on the way IN.  Same registry and same operations as
        # a card that is played; ``_after_draw`` knowing about Gamechanger by
        # name was already one branch too many, and Troll and Stańczyk would
        # have made it three.
        if card.on_draw is not None and self._draw_depth < MAX_DRAW_CHAIN:
            self._draw_depth += 1
            try:
                result = effects.resolve_on_draw(self, card, player.index)
                if isinstance(result, effects.Plan) and result.ok:
                    events.extend(self._execute(result, player.index))
                    events.append(ev.CardDrawEffect(
                        player.index, card.uid, card.title, result.description))
                elif isinstance(result, (effects.Refusal, effects.NotAvailable)):
                    events.append(ev.ActionRejected(
                        getattr(result, "reason", ""), "on_draw"))
            finally:
                self._draw_depth -= 1

        if card.deck_id == settings.DECK_CHEST:
            held = self.chest_cards(player)
            limit = self.chest_limit(player)
            if len(held) > limit:
                self.queue_chest_choice(player.index, [c.uid for c in held])
                events.append(ev.ChestLimitReached(
                    player_index=player.index,
                    limit=limit,
                    card_uids=[c.uid for c in held],
                    new_card_uid=card.uid,
                ))
        return events

    def _draw_one(self, player: Player, deck: Deck) -> List[ev.GameEvent]:
        """Take one card off a pile into a hand, with everything that follows.

        Factored out because an EFFECT can now draw cards (Troll replaces
        itself, Spy replaces what it took) and a draw owes the same debts
        wherever it comes from: reshuffle when the pile runs out, report the
        draw, and let the card act on the way in — including drawing again.
        """
        if player.hand_is_full:
            return [ev.ActionRejected(f"Ręka pełna ({RULES.max_hand} kart)", "draw")]
        events: List[ev.GameEvent] = []
        needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
        card = deck.take_card()
        if card is None:
            return [ev.ActionRejected(f"Talia „{deck.name}” jest pusta", "draw")]
        if needed_reshuffle:
            events.append(ev.DeckReshuffled(deck.id))
        player.add_card(card)
        events.append(ev.CardDrawn(player.index, deck.id, card.uid))
        events.extend(self._after_draw(player, card))
        return events

    def _discard_card(self, command: cmd.DiscardCard) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        card = player.card_by_uid(command.card_uid)
        if card is None:
            return [ev.ActionRejected("Karty nie ma na ręce", command.kind)]
        if card.locked:
            return [ev.ActionRejected(
                f"„{card.title}” zostaje na ręce — nie możesz jej odrzucić",
                command.kind)]
        player.remove_card(card)
        self.decks[card.deck_id].return_card(card)
        events: List[ev.GameEvent] = [
            ev.CardDiscarded(player.index, card.deck_id, card.uid)
        ]
        # Discarding a movement card is how a player passes: a turn is "resolve
        # one movement card", and a hand where none of them can legally be
        # played would otherwise leave the table stuck for ever.
        if command.player_index == self.active_player_index:
            events.extend(self._after_play(player, card))
        return events

    def _play_card(self, command: cmd.PlayCard) -> List[ev.GameEvent]:
        """Carry out a card's effect, then send the card to its discard pile.

        The engine resolves the effect itself rather than trusting the command,
        which matters the moment this runs over a network: a client can say
        *which card* it played and answer the questions the engine asks, but it
        cannot say what the card does.
        """
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        card = player.card_by_uid(command.card_uid)
        if card is None:
            return [ev.ActionRejected("Karty nie ma na ręce", command.kind)]
        if card.locked:
            # Troll is not a card you play; it is a card that plays you.  The
            # refusal is in the engine rather than in the fan because a client
            # that simply does not draw the lock must still be told no.
            return [ev.ActionRejected(
                f"„{card.title}” zagra się sama, kiedy przyjdzie twoja tura",
                command.kind)]

        result = effects.resolve(self, card, player.index, command.choices)
        pending = self._pending_choice(
            result, player.index, command, card_uid=card.uid
        )
        if pending is not None:
            return pending
        if not result.ok:
            return [self._refusal_event(result, command.kind)]

        events = self._execute(result, player.index)
        player.remove_card(card)
        self.decks[card.deck_id].return_card(card)
        events.append(
            ev.CardPlayed(player.index, card.deck_id, card.uid, card.title,
                          result.description)
        )
        events.extend(self._after_play(player, card))
        return events

    # ── the automatic turn loop ──────────────────────────────────────────────
    def _after_play(self, player: Player, card: Card) -> List[ev.GameEvent]:
        """Refill the hand and hand the turn on.

        Playing a movement card is the whole of a turn, so the two chores that
        used to be manual happen here: the player draws back up to their proper
        hand size and the turn moves on.  Doing it inside the same command keeps
        it atomic — over a network every peer replays one action and reaches the
        same table, with no extra round trips and nothing to get out of order.

        The view animates afterwards; the state does not wait for it, because
        waiting would make the outcome depend on frame rate.
        """
        if not RULES.auto_turn_flow or card.deck_id != settings.DECK_MOVEMENT:
            return []
        events = self._refill_movement_hand(player)
        events.extend(self._end_turn())
        return events

    def _refill_movement_hand(self, player: Player) -> List[ev.GameEvent]:
        """Draw back up to the hand size the rules give this player.

        The number comes from ``setup.starting_hand_size`` — the same function
        that dealt the opening hand — so Piotrek's larger hand and ChatGPT's
        smaller one are respected without this knowing about either.
        """
        from .setup import starting_hand_size

        wanted = starting_hand_size(player)
        deck = self.decks[settings.DECK_MOVEMENT]
        events: List[ev.GameEvent] = []
        held = sum(1 for c in player.hand if c.deck_id == settings.DECK_MOVEMENT)
        while held < wanted and not player.hand_is_full:
            needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
            drawn = deck.take_card()
            if drawn is None:
                break
            if needed_reshuffle:
                events.append(ev.DeckReshuffled(deck.id))
            player.add_card(drawn)
            events.append(ev.CardDrawn(player.index, deck.id, drawn.uid))
            held += 1
        return events

    def _end_turn(self, depth: int = 0) -> List[ev.GameEvent]:
        """Hand the turn to whoever the cadence says is next, then start it."""
        upcoming = self.next_turn()
        events: List[ev.GameEvent] = []
        if upcoming.round_number != self.round_number:
            events.extend(self._begin_round(upcoming.round_number))
        # The cursor is MOVED, never looked up.  ``order.index(seat)`` would
        # find the first slot that seat occupies, so a seat appearing more than
        # once in a round — Piotrek, every third slot — rewound the round to
        # its start and the later seats never played.
        self.turn_slot = upcoming.slot
        # A turn began, so the turn counter moves and statuses age, EVEN IF the
        # same seat is up again.  With a single hunter the cadence really does
        # give them two slots in a row (Piotrek takes every third), and the old
        # test skipped the counter there: statuses outstayed their welcome and
        # the round panel never refreshed.
        self.active_player_index = upcoming.seat
        self.turn_counter += 1
        events.append(ev.ActivePlayerChanged(upcoming.seat))
        events.extend(self._expire_statuses())
        events.extend(self._begin_turn(depth))
        return events

    # ── the start of a turn, which the player may not get to play ────────────
    def _begin_turn(self, depth: int = 0) -> List[ev.GameEvent]:
        """Let anything holding this seat's turn take it.

        Two things can, in this order:

        * ``SKIP_TURN`` — the seat simply loses the move (Lubin, Dziubdziuch,
          and now Stańczyk's ancestors).  This status has existed since stage 4
          and until now NOTHING READ IT, so every card that granted it was
          quietly doing nothing.
        * ``TURN_INTERRUPT`` — a card takes the turn over and says, in its own
          data, what to do with it.

        Both consume the turn: the hand is refilled by the ordinary rule and
        play passes on, exactly as if a card had been played.  That is why the
        end-of-turn draw happens here and not inside the operations — an
        interrupt is a turn, and a turn ends the same way whoever spent it.

        The recursion is real (a skipped turn can hand on to another skipped
        turn) and bounded, because a table where every seat is interrupted for
        ever is a content bug and must not become a hang.
        """
        if depth >= MAX_TURN_INTERRUPTS:
            return []
        player = self.active_player
        events = self._resolve_skip_turn(player)
        if events is None:
            events = self._resolve_turn_interrupt(player)
        if events is None:
            return []
        events.extend(self._refill_movement_hand(player))
        events.extend(self._end_turn(depth + 1))
        return events

    def _resolve_skip_turn(self, player: Player) -> Optional[List[ev.GameEvent]]:
        """Spend a SKIP_TURN status, if this seat has one."""
        status = self.statuses.find(
            StatusKind.SKIP_TURN, Subject.PLAYER, str(player.index)
        )
        if status is None:
            return None
        self.statuses.discard(status)
        return [
            ev.StatusEnded(status.kind.value, status.subject.value,
                           status.subject_id,
                           STATUS_LABELS.get(status.kind, status.kind.value)),
            ev.TurnSkipped(player.index, status.source),
        ]

    def _resolve_turn_interrupt(
        self, player: Player
    ) -> Optional[List[ev.GameEvent]]:
        """Run the oldest turn interrupt queued against this seat.

        The status carries the effect specification, so this method knows
        nothing about Troll or Stańczyk — it looks up a handler like every
        other resolution in the game does.  Whatever the interrupt was for, the
        card that started it is discarded afterwards: it has now happened, and
        leaving a locked card in the hand would lock the hand.
        """
        queued = self.statuses.interrupts_for(player.index)
        if not queued:
            return None
        status = queued[0]
        self.statuses.discard(status)

        events: List[ev.GameEvent] = [
            ev.StatusEnded(status.kind.value, status.subject.value,
                           status.subject_id,
                           STATUS_LABELS.get(status.kind, status.kind.value))
        ]
        card_uid = status.data.get("card_uid")
        raw = status.data.get("effect") or {}
        result = effects.resolve_spec(
            self, EffectSpec.from_dict(raw), player.index,
            source=status.source,
            card_uid=int(card_uid) if card_uid is not None else None,
        )
        if isinstance(result, effects.Plan) and result.ok:
            events.extend(self._execute(result, player.index))
        else:
            # An interrupt that cannot resolve still costs the turn, and says so
            # rather than leaving the table wondering why nothing happened.
            events.append(ev.ActionRejected(
                getattr(result, "reason", "Efekt nie zadziałał"), "turn_interrupt"))

        source = player.card_by_uid(int(card_uid)) if card_uid is not None else None
        if source is not None:
            player.remove_card(source)
            self.decks[source.deck_id].return_card(source)
            events.append(
                ev.CardDiscarded(player.index, source.deck_id, source.uid)
            )
        return events

    def _begin_round(self, round_number: int) -> List[ev.GameEvent]:
        """Move to a new round, handing out a chest card if one is due.

        The order here is the rules' order and not an arbitrary one:

        1. pawns Shady hid last round come back FIRST, so everything after this
           point sees a complete board;
        2. Squid Game's automatic check is armed against that board, before
           anybody has had a turn — which is what "once a round, before the
           first player" means;
        3. the Mod Patusa selection may then pause the round, and a mod chosen
           in it takes effect from here;
        4. the chest deals last, as it always did.
        """
        self.round_number = max(1, round_number)
        self.turn_slot = 0
        events: List[ev.GameEvent] = [ev.RoundChanged(self.round_number)]
        # A Gambit Patusa that named a round now behind us is spent.  Round
        # scope cannot ride on ``expires_after_turn`` — a round is a variable
        # number of turns — so the round loop retires them itself.  ``_set_round``
        # walks every round it crosses, so a jump cannot skip this either.
        events.extend(self._expire_round_statuses())
        events.extend(self._restore_hidden_pawns(before_round=self.round_number))
        events.extend(self._arm_lead_check())
        events.extend(self._open_mod_selection())
        events.extend(self._distribute_chest_card())
        return events

    def _arm_lead_check(self) -> List[ev.GameEvent]:
        """Name the pawn Squid Game inspects this round, if it inspects one.

        Deciding WHO is checked is public and happens on every machine; whether
        that pawn turns out to be Piotrek is not, and is left to the authority
        through :func:`victory.review`.  Splitting it this way is what keeps the
        secret a secret while every replica still agrees about what is going on.

        The first check falls on the round AFTER the mod arrived, so a Squid
        Game chosen in round 5 checks nothing until round 6.  A shared lead is
        skipped outright rather than broken by a tie-break, and a colour that
        has already been ruled out is not checked twice.
        """
        self.pending_lead_check = None
        if not self.lead_check_only or not self.phase.playable:
            return []
        armed = [round_number for uid, round_number in self.armed_mods.items()
                 if self._mod_in_rack(uid, "lead_check_only")]
        if not armed or self.round_number <= min(armed):
            return []

        leader = self.leading_pawn()
        if leader is None:
            return [ev.LeadCheckAnnounced(pawn_id="", skipped=True)]
        if leader in self.eliminated_pawns:
            return [ev.LeadCheckAnnounced(pawn_id=leader, skipped=True)]
        self.pending_lead_check = leader
        return [ev.LeadCheckAnnounced(pawn_id=leader, skipped=False)]

    def _mod_in_rack(self, uid: int, rule: str) -> bool:
        return any(card.uid == uid and card.passive.get(rule)
                   for card in self.active_mods)

    # ── choosing the Mods Patusa ─────────────────────────────────────────────
    def _open_mod_selection(self) -> List[ev.GameEvent]:
        """Pause the round and deal both factions three Mods Patusa each.

        Six cards leave the deck at once and only two come back into play; the
        four losers are discarded when their side settles, so the deck sees the
        same cards again after a reshuffle rather than losing them.

        Nothing happens on a round that is not a mod round, on a table with no
        mods deck, or while a selection is somehow already open — the last of
        which would otherwise strand the first selection's cards outside every
        pile at once.
        """
        if not self.is_mod_round() or self.pending_mod_selection is not None:
            return []
        deck = self.decks.get(settings.DECK_MODS)
        if deck is None:
            return []

        events: List[ev.GameEvent] = []
        wanted = max(1, int(RULES.mod_choices))

        def deal(count: int) -> List[Card]:
            drawn: List[Card] = []
            for _ in range(count):
                needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
                card = deck.take_card()
                if card is None:
                    break
                if needed_reshuffle:
                    events.append(ev.DeckReshuffled(deck.id))
                drawn.append(card)
            return drawn

        piotrek_seat = self.piotrek_seat
        hunter_seats = [p.index for p in self.players if not p.is_piotrek]
        piotrek_cards = deal(wanted) if piotrek_seat is not None else []
        hunter_cards = deal(wanted) if hunter_seats else []

        if not piotrek_cards and not hunter_cards:
            # An exhausted mods deck must not stop the round: put back whatever
            # was dealt and carry on as though this round were not a mod round.
            return []

        selection = ModSelection(
            round_number=self.round_number,
            piotrek_seat=piotrek_seat,
            piotrek_cards=piotrek_cards,
            hunter_cards=hunter_cards,
            hunter_seats=hunter_seats,
        )
        # A side with nobody to decide it, or no cards to decide between, is
        # finished the moment it opens.  Without this a table with no Piotrek
        # (or no hunters) would wait for a decision that can never arrive.
        if not piotrek_cards or piotrek_seat is None:
            selection.piotrek_done = True
        if not hunter_cards or not hunter_seats:
            selection.hunters_done = True
        self.pending_mod_selection = selection

        events.append(ev.ModSelectionStarted(
            round_number=self.round_number,
            piotrek_seat=piotrek_seat,
            piotrek_uids=[c.uid for c in piotrek_cards],
            hunter_uids=[c.uid for c in hunter_cards],
            hunter_seats=list(hunter_seats),
        ))
        if selection.finished:
            events.extend(self._finish_mod_selection())
        return events

    def _choose_mod(self, command: cmd.ChooseMod) -> List[ev.GameEvent]:
        """Piotrek's pick.  The chosen card goes LEFT, the other two are gone."""
        selection = self.pending_mod_selection
        if selection is None:
            return [ev.ActionRejected("Nie wybiera się teraz Modów Patusa",
                                      command.kind)]
        if selection.piotrek_done:
            return [ev.ActionRejected("Mod Patusa jest już wybrany", command.kind)]
        if selection.piotrek_seat is None or command.player_index != selection.piotrek_seat:
            return [ev.ActionRejected("Tylko Piotrek wybiera ten Mod Patusa",
                                      command.kind)]
        chosen = next((c for c in selection.piotrek_cards
                       if c.uid == command.card_uid), None)
        if chosen is None:
            return [ev.ActionRejected("Tej karty nie ma wśród wylosowanych",
                                      command.kind)]

        events = self._settle_mod_side(
            selection, chosen, selection.piotrek_cards, slot=0, faction="piotrek"
        )
        selection.piotrek_done = True
        if selection.finished:
            events.extend(self._finish_mod_selection())
        return events

    def _vote_mod(self, command: cmd.VoteMod) -> List[ev.GameEvent]:
        """One hunter's vote.  The last vote in also decides the winner."""
        selection = self.pending_mod_selection
        if selection is None:
            return [ev.ActionRejected("Nie głosuje się teraz nad Modem Patusa",
                                      command.kind)]
        if selection.hunters_done:
            return [ev.ActionRejected("Głosowanie już się zakończyło", command.kind)]
        if command.player_index not in selection.hunter_seats:
            return [ev.ActionRejected("Tylko Oprawcy głosują nad tym Modem Patusa",
                                      command.kind)]
        chosen = next((c for c in selection.hunter_cards
                       if c.uid == command.card_uid), None)
        if chosen is None:
            return [ev.ActionRejected("Tej karty nie ma wśród wylosowanych",
                                      command.kind)]

        # Replaces rather than refuses: changing your mind is allowed right up
        # until the last hunter votes, and that is the same command again.
        selection.votes[command.player_index] = chosen.uid
        events: List[ev.GameEvent] = [ev.ModVoteCast(
            player_index=command.player_index,
            card_uid=chosen.uid,
            tally=selection.tally(),
            voted=len(selection.votes),
            voters=len(selection.hunter_seats),
        )]
        if not selection.everyone_voted:
            return events

        winner = selection.winner()
        if winner is not None:
            tied = selection.is_tied()
            events.extend(self._settle_mod_side(
                selection, winner, selection.hunter_cards, slot=1,
                faction="hunters", tie_broken=tied,
            ))
        selection.hunters_done = True
        if selection.finished:
            events.extend(self._finish_mod_selection())
        return events

    def _settle_mod_side(self, selection: ModSelection, chosen: Card,
                         candidates: List[Card], slot: int, faction: str,
                         tie_broken: bool = False) -> List[ev.GameEvent]:
        """Install the winning card in its slot and discard the losers.

        The slot is written directly rather than pushed through
        ``_install_mod``: the two factions own one slot each for the rest of the
        game, so Piotrek choosing a card must not shunt the hunters' card along
        the rack the way Thunderfuck does.
        """
        events: List[ev.GameEvent] = []
        if 0 <= slot < len(self.mod_slots):
            previous = self.mod_slots[slot]
            if previous is not None:
                self.decks[previous.deck_id].return_card(previous)
                events.append(ev.ModDiscarded(slot, previous.uid))
            self.mod_slots[slot] = chosen

        losers = [card for card in candidates if card.uid != chosen.uid]
        for card in losers:
            self.decks[card.deck_id].return_card(card)

        events.append(ev.ModSelectionResolved(
            faction=faction,
            slot=slot,
            card_uid=chosen.uid,
            title=chosen.title,
            discarded_uids=[c.uid for c in losers],
            tie_broken=tie_broken,
        ))
        events.extend(self._sync_mod_states())
        return events

    def _finish_mod_selection(self) -> List[ev.GameEvent]:
        selection = self.pending_mod_selection
        if selection is None:
            return []
        self.pending_mod_selection = None
        return [ev.ModSelectionFinished(selection.round_number)]

    # ── mods that DO something the moment they arrive, or when they leave ────
    def _sync_mod_states(self) -> List[ev.GameEvent]:
        """Bring the one-off effects into line with what is in the rack.

        Three mods are not pure passives.  Paczka shows a window as it arrives,
        Squid Game has to remember WHICH ROUND it arrived in so its first check
        falls on the next one, and Shady takes a pawn off the map once.  All
        three are therefore about the TRANSITION into and out of the rack, not
        about the rack's contents, and the difference between the two is what
        this method computes.

        Called after every change to ``mod_slots`` — the selection, PlaceMod
        and Thunderfuck alike.  Doing it in one place is what stops a fourth
        way into the rack from silently skipping an arrival, and it is also
        what guarantees the departure half runs: a mod that leaves must leave
        nothing behind (a hidden pawn is put back, a pending check is dropped),
        because the rack is the only thing keeping its rule alive.
        """
        events: List[ev.GameEvent] = []
        present = {card.uid: card for card in self.active_mods}

        departed = [uid for uid in self.armed_mods if uid not in present]
        for uid in departed:
            del self.armed_mods[uid]

        # A departure can remove the rule that a pending check depends on.  The
        # check is dropped rather than honoured: it belongs to the mod, and the
        # mod is gone.
        if not self.lead_check_only and self.pending_lead_check is not None:
            self.pending_lead_check = None
        # A pawn is held off the map by ONE PARTICULAR mod, so it comes back
        # when THAT mod leaves — even when another card with the same rule is
        # still in the rack.  Rage Quit can replace a Shady with a Shady, and
        # asking only whether the rack still hides SOMEBODY would leave the
        # first pawn off the board for the rest of the match while the new
        # arrival took a second one.
        events.extend(self._restore_pawns_hidden_by(departed))
        # Nothing on the table may keep a pawn off the map, so anybody still
        # held when no mod hides at all comes back too.  This also covers a
        # status recorded before the mod was noted on it.
        if not self.hides_leader:
            events.extend(self._restore_hidden_pawns())

        for uid, card in present.items():
            if uid in self.armed_mods:
                continue
            self.armed_mods[uid] = self.round_number
            events.extend(self._arm_mod(card))
        return events

    def _restore_pawns_hidden_by(self, mod_uids: Sequence[int]
                                 ) -> List[ev.GameEvent]:
        """Put back every pawn taken off the map by one of these mods.

        Runs BEFORE the arming loop, so a replacement Shady chooses its own
        target from a complete board rather than from one the outgoing card was
        still holding a hole in.
        """
        if not mod_uids:
            return []
        wanted = {int(uid) for uid in mod_uids}
        events: List[ev.GameEvent] = []
        for status in list(self.statuses.of_kind(StatusKind.HIDDEN)):
            owner = status.data.get("mod_uid")
            if owner is None or int(owner) not in wanted:
                continue
            self.statuses.discard(status)
            events.extend(self._return_pawn_to_rear(status.subject_id, status))
        return events

    def _arm_mod(self, card: Card) -> List[ev.GameEvent]:
        """Run whatever a mod does at the moment it reaches the rack.

        Keyed on the card's declared ``passive``, never on its title (N98), so
        a second mod that reveals the Chest or hides the leader is a JSON entry
        exactly as a passive rule is.
        """
        events: List[ev.GameEvent] = []
        if card.passive.get("reveal_chest"):
            events.append(self._chest_reveal_event())
        if card.passive.get("hide_leader"):
            events.extend(self._hide_leading_pawn(card.uid))
        return events

    # ── Paczka: every Chest card is face up ──────────────────────────────────
    def _chest_reveal_event(self) -> ev.GameEvent:
        """Who holds which Chest cards, for the window every player is shown.

        DELIBERATELY BREAKS N81, which says a card title must never go into an
        event the whole table sees.  That rule exists because only one player
        was entitled to look; here the card IS the entitlement — Paczka's whole
        text is that the Chest is public — so the exception is the mechanic,
        not a leak.  It is also the ONLY effect allowed to do this: any other
        event carrying Chest titles is a bug.

        Players holding nothing are left out, because a list of empty names is
        noise rather than information.
        """
        entries = [
            ev.ChestHolding(
                player_index=player.index,
                player_name=player.name,
                titles=[card.title for card in self.chest_cards(player)],
            )
            for player in self.players
            if self.chest_cards(player)
        ]
        return ev.ChestCardsRevealed(entries)

    # ── Shady: the leading pawn leaves the map for a round ───────────────────
    def _shady_target(self) -> Optional[str]:
        """The pawn Shady takes: the BOTTOM of the furthest occupied field.

        Not the same question as :meth:`leading_pawn`.  Squid Game wants a pawn
        that is unambiguously out in front and skips its check when the lead is
        shared; Shady is told what to do about a shared field instead — it
        takes the pawn at the bottom of the tower, so the example in the brief
        (pink standing on green) removes green.

        Ties between two fields of one widened position are broken by the field
        index and then by the palette order, which is the tie-break the rest of
        the engine already uses (L7) and is identical on every machine.
        """
        visible = {pawn.id for pawn in self.visible_pawns}
        best_tile: Optional[Tile] = None
        for tile in self.board.tiles:
            occupants = [pawn for pawn in tile.stack if pawn in visible]
            if not occupants:
                continue
            if best_tile is None or (tile.slot, -tile.index) > (best_tile.slot,
                                                               -best_tile.index):
                best_tile = tile
        if best_tile is None:
            return None
        return next(pawn for pawn in best_tile.stack if pawn in visible)

    def _hide_leading_pawn(self, mod_uid: Optional[int] = None
                           ) -> List[ev.GameEvent]:
        """Take the leading pawn off the map, remembering how to put it back.

        Only the ONE pawn leaves.  Anything riding on it stays exactly where it
        was and simply settles onto the field — which is what the brief's own
        example describes (pink standing on green: "green disappears"), and
        what makes the checking exception arithmetic work, since it is written
        for exactly one absent pawn.  The riders are stored anyway, because the
        brief asks for them and because a future reading that takes the tower
        with it would need nothing more than this record.
        """
        pawn_id = self._shady_target()
        if pawn_id is None or self.pawn_is_hidden(pawn_id):
            return []
        riders = list(self.board.carried_pawns(pawn_id))
        tile = self.board.pawn_tile(pawn_id)
        self.board.remove_pawn(pawn_id)
        token = self.tokens.get(pawn_id)
        if token is not None:
            token.tile_index = None
            token.held = False
        self._sync_token_positions()
        self.statuses.add(Status.for_pawn(
            StatusKind.HIDDEN, pawn_id,
            data={"riders": riders, "round": self.round_number,
                  "tile": None if tile is None else tile.index,
                  # WHICH mod is holding this pawn.  Without it a departing
                  # Shady can only be recognised by "does anything still hide?",
                  # which is the wrong question when one Shady replaces another.
                  "mod_uid": mod_uid},
            source="Shady",
        ))
        return [ev.PawnHidden(pawn_id=pawn_id, riders=riders,
                              round_number=self.round_number)]

    def _restore_hidden_pawns(self, before_round: Optional[int] = None
                              ) -> List[ev.GameEvent]:
        """Put hidden pawns back on top of the pawn furthest to the rear.

        The pawn does NOT go back where it came from: that is the point of the
        card, and it is why this reads the board fresh instead of using the
        field recorded when it left.

        ``before_round`` restores only pawns hidden in an EARLIER round, which
        is the ordinary end-of-round call; without it every hidden pawn comes
        back, which is what a departing Shady needs.
        """
        events: List[ev.GameEvent] = []
        for status in list(self.statuses.of_kind(StatusKind.HIDDEN)):
            hidden_round = int(status.data.get("round", self.round_number))
            if before_round is not None and hidden_round >= before_round:
                continue
            pawn_id = status.subject_id
            self.statuses.discard(status)
            events.extend(self._return_pawn_to_rear(pawn_id, status))
        return events

    def _return_pawn_to_rear(self, pawn_id: str,
                             status: Status) -> List[ev.GameEvent]:
        """Place a returning pawn on top of the rearmost pawn on the board."""
        token = self.tokens.get(pawn_id)
        if token is None:
            return []
        rear = self._rearmost_placed_pawn(exclude=pawn_id)
        tile = self.board.pawn_tile(rear) if rear is not None else None
        if tile is None:
            # Nobody has left the camp, so there is no pawn to stand on.  The
            # returning pawn waits in the camp as it did at the start of the
            # game rather than materialising on the road.
            slot = next((i for i, pawn in enumerate(self.library.pawns)
                         if pawn.id == pawn_id), 0)
            token.tile_index = None
            token.position = self.board.camp_position(slot)
            self._sync_token_positions()
            return [ev.PawnRestored(pawn_id=pawn_id, tile_index=None, onto="")]
        self.board.place_pawn(pawn_id, tile.index, on_top=True)
        # The stack it was carrying is restored on top of it, in its old order,
        # for the reading where the tower travels with the pawn.  Today nothing
        # rides along — the riders stayed behind — so this is a no-op unless a
        # rider happens to be standing on the same field again.
        for rider in status.data.get("riders", []):
            if self.board.pawn_tiles.get(rider) == tile.index:
                self.board.place_pawn(rider, tile.index, on_top=True)
        self._sync_token_positions()
        token.tile_index = tile.index
        return [ev.PawnRestored(pawn_id=pawn_id, tile_index=tile.index,
                                onto=rear or "")]

    def _rearmost_placed_pawn(self, exclude: str = "") -> Optional[str]:
        """The pawn furthest from the finish that is actually on the board."""
        best: Optional[Tuple[int, int, int, str]] = None
        for order, pawn in enumerate(self.library.pawns):
            if pawn.id == exclude or self.pawn_is_hidden(pawn.id):
                continue
            index = self.board.position_of_pawn(pawn.id)
            if index is None:
                continue
            key = (index, self.board.stack_depth(pawn.id), order, pawn.id)
            if best is None or key < best:
                best = key
        return best[3] if best is not None else None

    def chest_recipient_seats(self, round_number: Optional[int] = None
                              ) -> List[int]:
        """Every seat due a chest card this round, in the ribbon's own order.

        TWO recipients, not one: PIOTREK ALWAYS, plus whichever hunter the rota
        has reached.  The turn ribbon has marked both since the chest existed —
        a dot under Piotrek's first slot and one under the scheduled hunter —
        but the engine only ever dealt to the hunter, so Piotrek's marker had
        been promising a card that never arrived.

        Piotrek comes first because that is how the ribbon reads, left to
        right, and because a fixed order is what keeps the two draws coming off
        the deck in the same sequence on every machine.

        The hunter rota itself is untouched: it still steps once per hand-out
        through :func:`chest_recipient_for_round`.  Piotrek is not part of that
        rotation and never was — he is simply always fed.
        """
        seats: List[int] = []
        piotrek = self.piotrek_seat
        if piotrek is not None:
            seats.append(piotrek)
        hunter = self.chest_recipient_seat(round_number)
        if hunter is not None and hunter not in seats:
            seats.append(hunter)
        return seats

    def _distribute_chest_card(self) -> List[ev.GameEvent]:
        """Give this round's chest cards to the seats due one.

        The interface already announced who was due and when; this is that
        promise being kept.  Each card goes through the ordinary draw path, so
        the hand limit and its keep-or-discard prompt behave exactly as they do
        for a card drawn by hand.

        On a small table only every second eligible round awards anything —
        ``chest_awards_cards`` decides, and the indicator asks the same
        question, so a filled marker and a dealt card cannot disagree.  The
        ROTA IS NOT PAUSED by a skipped round: the recipient still advances, so
        a skipped round moves the marker on exactly as a dealing round does.
        """
        if not self.chest_awards_cards():
            return []
        deck = self.decks[settings.DECK_CHEST]
        events: List[ev.GameEvent] = []
        for seat in self.chest_recipient_seats():
            player = self.player(seat)
            if player is None:
                continue
            needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
            card = deck.take_card()
            if card is None:
                # An empty chest stops the hand-out for everybody left this
                # round rather than feeding some seats and not others.
                break
            if needed_reshuffle:
                events.append(ev.DeckReshuffled(deck.id))
            player.add_card(card)
            events.append(ev.CardDrawn(player.index, deck.id, card.uid))
            events.append(
                ev.ChestCardAwarded(player.index, card.uid, self.round_number)
            )
            events.extend(self._after_draw(player, card))
        return events

    def _end_turn_command(self, command: cmd.EndTurn) -> List[ev.GameEvent]:
        """Deliberately finish a turn.

        Exactly what happens at the end of a played card, so nothing can drift
        apart between the automatic path and the button: refill, then hand the
        turn on.
        """
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if player.index != self.active_player_index:
            return [ev.ActionRejected("To nie twoja tura", command.kind)]
        if self.pending_chest_choice is not None:
            return [ev.ActionRejected("Najpierw wybierz Karty Skrzyni", command.kind)]

        events = self._refill_movement_hand(player)
        events.extend(self._end_turn())
        return events

    def _use_ability(self, command: cmd.UseAbility) -> List[ev.GameEvent]:
        """Activate a character ability or one of Piotrek's skills."""
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        card = player.skill if command.source == "skill" else player.character
        if card is None:
            return [ev.ActionRejected("Brak karty z umiejętnością", command.kind)]
        if card.ability is None:
            return [ev.ActionRejected("Ta postać nie ma aktywnej umiejętności",
                                      command.kind)]
        if not card.ability_available:
            return [ev.ActionRejected(
                f"Umiejętność „{card.skill or card.title}” została już zużyta",
                command.kind)]
        if self.abilities_locked:
            # Sesja na PG.  Refused BEFORE anything resolves, so no charge is
            # spent and nothing is animated: when the mod leaves the rack the
            # player has exactly the uses they walked in with.  In the engine
            # rather than in the interface for the usual reason — a client that
            # simply does not grey the button must still be unable to act.
            return [ev.ActionRejected(
                "Sesja na PG — umiejętności postaci są zablokowane",
                command.kind)]

        title = card.skill or card.title
        result = effects.resolve_ability(self, card, player.index, command.choices)
        pending = self._pending_choice(
            result, player.index, command, ability_source=command.source
        )
        if pending is not None:
            return pending
        if isinstance(result, effects.NotAvailable):
            # The ability is real, the mechanic it needs is not.  Nothing is
            # spent — the use is still there when checking arrives.
            return [ev.AbilityUnavailable(player.index, title, result.reason)]
        if not result.ok:
            return [self._refusal_event(result, command.kind)]

        events = self._execute(result, player.index)
        card.spend_use()
        events.append(
            ev.AbilityUsed(player.index, title, result.description,
                           uses_left=card.uses_left, source=command.source)
        )
        return events

    # ── the executor ─────────────────────────────────────────────────────────
    def _pending_choice(
        self, result, player_index: int, command, *,
        card_uid: Optional[int] = None, ability_source: Optional[str] = None,
    ) -> Optional[List[ev.GameEvent]]:
        """Turn an unanswered :class:`Choice` into an event, changing nothing.

        The action is legal; a decision is missing.  The interface asks, then
        resubmits the same command with the answer added to ``choices``.
        """
        if not isinstance(result, effects.Choice):
            return None
        return [
            ev.ChoiceRequired(
                player_index=player_index,
                key=result.key,
                kind=result.kind,
                prompt=result.prompt,
                options=[(option.id, option.label) for option in result.options],
                tiles=list(result.tiles),
                pawns=list(result.pawns),
                card_options=[o.card_uid for o in result.options
                              if o.card_uid is not None],
                count=result.count,
                ordered=result.ordered,
                owner=result.owner,
                card_uid=card_uid,
                ability_source=ability_source,
                answered=dict(getattr(command, "choices", {}) or {}),
                description=result.description,
            )
        ]

    @staticmethod
    def _refusal_event(result, kind: str) -> ev.GameEvent:
        return ev.ActionRejected(getattr(result, "reason", "Nie można tego zrobić"),
                                 kind)

    def _execute(self, plan: effects.Plan, actor: int) -> List[ev.GameEvent]:
        """Apply a plan's operations.  The only place gameplay actually changes.

        Every operation is data, so this is a dispatch table rather than a
        chain of special cases, and a new effect type usually needs no change
        here at all — only a new handler that emits existing operations.
        """
        events: List[ev.GameEvent] = []
        for operation in plan.operations:
            handler = self._OPERATIONS.get(type(operation))
            if handler is None:  # pragma: no cover - defensive
                events.append(ev.ActionRejected(
                    f"Nieobsługiwana operacja: {type(operation).__name__}"))
                continue
            events.extend(handler(self, operation, actor))
        return events

    def _op_move_pawn(self, op: effects.MovePawn, actor: int) -> List[ev.GameEvent]:
        """Move a pawn (and everything travelling with it) along its route."""
        tiles = list(op.tiles)
        if not tiles:
            return []
        destination = tiles[-1]
        waypoints: List[Tuple[float, float]] = []
        for index in tiles:
            tile = self.board.tile(index)
            if tile is not None:
                waypoints.append(tile.position)

        self.board.place_pawn(op.pawn_id, destination)
        for rider in op.carried:
            self.board.place_pawn(rider, destination)
        self._sync_token_positions()

        backward = bool(op.route) and op.route[0] < op.from_index
        return [
            ev.TokenWalked(
                pawn_id=op.pawn_id,
                from_index=op.from_index,
                route=list(op.route),
                tiles=tiles,
                waypoints=waypoints,
                carried=list(op.carried),
                backward=backward,
            )
        ]

    def _op_grant_status(self, op: effects.GrantStatus, actor: int) -> List[ev.GameEvent]:
        status = self.statuses.add(op.status, replace=not op.stack)
        return [
            ev.StatusGranted(
                kind=status.kind.value,
                subject=status.subject.value,
                subject_id=status.subject_id,
                label=STATUS_LABELS.get(status.kind, status.kind.value),
                source=status.source,
                expires_after_turn=status.expires_after_turn,
            )
        ]

    def _op_clear_status(self, op: effects.ClearStatus, actor: int) -> List[ev.GameEvent]:
        removed = self.statuses.remove(op.kind, op.subject, op.subject_id)
        if not removed:
            return []
        return [ev.StatusEnded(op.kind.value, op.subject.value, op.subject_id,
                               STATUS_LABELS.get(op.kind, op.kind.value))]

    def _op_spend_status(self, op: effects.SpendStatus, actor: int) -> List[ev.GameEvent]:
        status = self.statuses.find(op.kind, op.subject, op.subject_id)
        if status is None:
            return []
        self.statuses.spend_charge(status)
        if self.statuses.find(op.kind, op.subject, op.subject_id) is None:
            return [ev.StatusEnded(op.kind.value, op.subject.value, op.subject_id,
                                   STATUS_LABELS.get(op.kind, op.kind.value))]
        return []

    def _op_draw_into_mods(
        self, op: effects.DrawIntoMods, actor: int
    ) -> List[ev.GameEvent]:
        """Thunderfuck: shift a fresh mod in from the left.

        With an EMPTY rack this does nothing at all — the card is still played
        and discarded, it simply has no mods to replace.  That is the rule from
        the physical game and it is deliberate: Thunderfuck REPLACES what is in
        play, so before the first selection there is nothing for it to act on
        and it must not quietly seed the rack ahead of schedule.

        With anything in the rack the whole rack shifts one place right: the new
        card takes the LEFT slot, the old LEFT moves to the RIGHT, and whatever
        was on the RIGHT is discarded.  That is exactly the push
        ``_install_mod`` already performs, so this passes the decision straight
        to it rather than restating it.
        """
        if not any(self.mod_slots):
            return []
        deck = self.decks.get(op.deck_id)
        if deck is None:
            return [ev.ActionRejected("Nieznana talia", "draw_into_mods")]
        events: List[ev.GameEvent] = []
        needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
        card = deck.take_card()
        if card is None:
            return [ev.ActionRejected(f"Talia „{deck.name}” jest pusta",
                                      "draw_into_mods")]
        if needed_reshuffle:
            events.append(ev.DeckReshuffled(deck.id))
        events.append(ev.CardDrawn(actor, deck.id, card.uid))
        events.extend(self._install_mod(card, actor))
        return events

    def _op_turn_lost(self, op: effects.TurnLost, actor: int) -> List[ev.GameEvent]:
        return [ev.TurnSkipped(op.player_index, op.source)]

    def _op_announce(self, op: effects.Announce, actor: int) -> List[ev.GameEvent]:
        return [ev.ActionRejected(op.text, "announce")]

    def _op_fizzle(self, op: effects.Fizzle, actor: int) -> List[ev.GameEvent]:
        """A move that legitimately did nothing (Halloween).

        Changes no state on purpose.  The card around it is played, discarded
        and followed by the usual refill and hand-over, because the effect
        resolved — it simply resolved to nothing.
        """
        return [ev.MoveFizzled(op.reason, op.pawn_id)]

    def _op_play_random_card(
        self, op: effects.PlayRandomCard, actor: int
    ) -> List[ev.GameEvent]:
        """Reveal a random playable card from a deck and carry out its effect.

        The draw uses the game's seeded RNG, so every machine reveals the same
        card from the same command — which is why this happens here and not in
        the handler, where a preview would have consumed the randomness.
        """
        deck = self.decks.get(op.deck_id)
        if deck is None:
            return [ev.ActionRejected("Nieznana talia", "play_random_card")]
        candidates = [card for card in deck.draw_pile if card.resolves_without_asking]
        if not candidates:
            return [ev.ActionRejected(
                f"W talii „{deck.name}” nie ma karty, którą można wykonać",
                "play_random_card")]

        card = self.rng.choice(candidates)
        deck.draw_pile.remove(card)
        events: List[ev.GameEvent] = [
            ev.CardRevealed(actor, deck.id, card.uid, card.title, card.text,
                            announce_seconds=op.announce_seconds)
        ]
        # can_ask=False: the card was picked from ``resolves_without_asking``,
        # which is a property of the printed card and cannot know that a mod
        # has since given it a question to ask.  Without this, Speedrun turned
        # every revealed backward card into "bez efektu".
        result = effects.resolve(self, card, actor, can_ask=False)
        if result.ok:
            events.extend(self._execute(result, actor))
            events.append(
                ev.CardPlayed(actor, deck.id, card.uid, card.title,
                              result.description)
            )
        else:
            events.append(ev.ActionRejected(
                getattr(result, "reason", "Wylosowana karta nie mogła zadziałać"),
                "play_random_card"))
        # The revealed card was turned face up and used, so it goes to the
        # discard pile like any other played card rather than back into the
        # draw pile where it could be revealed again.
        deck.return_card(card)
        return events

    def _op_move_by_steps(
        self, op: effects.MoveBySteps, actor: int
    ) -> List[ev.GameEvent]:
        """Move a pawn a distance, routed against the board as it is NOW.

        The difference from :class:`~...effects.MovePawn` is the moment the
        route is worked out.  When a card moves two pawns in order, the second
        one departs from a board the first one has already rearranged — it may
        have been carried along inside a tower.  Recomputing here means the
        second move is an ordinary move of a pawn that really is where the
        engine thinks it is, so stacking, towers and the walk animation need no
        special case at all.
        """
        start = effects.pawn_index(self, op.pawn_id)
        route = effects.route_between(self, start, op.steps)
        if not route:
            return []
        chosen = op.chosen_tile
        if chosen is None and op.random_branch:
            chosen = self._random_half(route[-1])
        return self._op_move_pawn(
            effects.MovePawn(
                pawn_id=op.pawn_id,
                from_index=start,
                route=route,
                tiles=effects.tile_route(self, op.pawn_id, route, chosen),
                carried=(effects.travellers(self, op.pawn_id)
                         if op.carry_riders else ()),
            ),
            actor,
        )

    def _random_half(self, position_index: int) -> Optional[int]:
        """Pick a half of a widened destination with the seeded RNG (Balbinka).

        Here rather than in the handler because a handler may never consume
        randomness (N78): the interface resolves handlers to preview a card
        while it is being dragged, and a die rolled there would change what the
        card does every frame.  Every replica applies the same commands in the
        same order against the same board, so every replica draws the same
        halves in the same sequence.

        Only the DESTINATION is randomised.  Which half a pawn merely passed
        through decides nothing (D8a), so the intermediate steps keep the
        ordinary nearer-half rule and consume no randomness at all — a rule
        that also keeps the RNG in step with a table where nobody is moving
        across a widened row.
        """
        position = self.board.position(position_index)
        if position is None or not position.is_doubled:
            return None
        return self.rng.choice([tile.index for tile in position.tiles])

    def _op_move_and_collect(
        self, op: effects.MoveAndCollect, actor: int
    ) -> List[ev.GameEvent]:
        """Dzieckorolka: walk a pawn and stack what it swept up underneath it.

        THE ORDER IS THE RULE, so it is worth spelling out.  A tile's ``stack``
        is stored BOTTOM FIRST, and the finished tower read downwards from the
        mover has to be the journey in order.  So, onto whoever was already
        standing on the destination:

            1. the collected pawns in REVERSE travel order, last met lowest;
            2. the mover;
            3. anything that was riding on the mover when it set off.

        Reversing in step 1 is what turns "read the path downwards from the
        mover" into a bottom-first list, and it is the only subtle line here.

        Every collected pawn is the TOP of its field, so lifting it can never
        break a tower — there is nothing standing on it to drop.
        """
        tiles = list(op.tiles)
        if not tiles:
            return []
        destination = tiles[-1]

        # Where each collected pawn is standing NOW, so its walk can start from
        # its own field rather than from the mover's.
        pickups = {pawn_id: self.board.pawn_tiles.get(pawn_id)
                   for pawn_id in op.collected}

        for pawn_id in reversed(op.collected):
            self.board.place_pawn(pawn_id, destination, on_top=True)
        self.board.place_pawn(op.pawn_id, destination, on_top=True)
        for rider in op.carried:
            self.board.place_pawn(rider, destination, on_top=True)
        self._sync_token_positions()

        waypoints: List[Tuple[float, float]] = []
        for index in tiles:
            tile = self.board.tile(index)
            if tile is not None:
                waypoints.append(tile.position)

        events: List[ev.GameEvent] = [
            ev.TokenWalked(
                pawn_id=op.pawn_id,
                from_index=op.from_index,
                route=list(op.route),
                tiles=tiles,
                waypoints=waypoints,
                carried=list(op.carried),
                backward=False,
            )
        ]
        # A collected pawn joins the walk part way along, so it gets its own
        # TokenWalked over the TAIL of the route.  Giving it to the leader's
        # ``carried`` instead would send it backwards to the start of the route
        # first, because riders share every waypoint.
        for pawn_id in op.collected:
            picked_up = pickups.get(pawn_id)
            if picked_up is None or picked_up not in tiles:
                continue
            step = tiles.index(picked_up)
            events.append(ev.TokenWalked(
                pawn_id=pawn_id,
                from_index=op.route[step] if step < len(op.route) else op.from_index,
                route=list(op.route[step:]),
                tiles=tiles[step:],
                waypoints=waypoints[step:],
                backward=False,
            ))
        events.append(ev.PawnsCollected(
            pawn_id=op.pawn_id, collected=list(op.collected),
            tile_index=destination,
        ))
        return events

    def _op_replace_mods(
        self, op: effects.ReplaceMods, actor: int
    ) -> List[ev.GameEvent]:
        """Rage Quit: swap every occupied mod slot for a fresh draw.

        THE DRAWS HAPPEN BEFORE THE DISCARDS, and that is not tidiness.  A deck
        whose draw pile has run dry reshuffles its discard pile, so returning
        the outgoing mods first would let the card hand back the very cards it
        was played to get rid of.

        Each slot is written IN PLACE rather than pushed, because the two
        factions own one slot each for the rest of the game (N85): pushing
        would slide Piotrek's replacement into the hunters' slot and discard
        one of the two new cards on the way past.

        ``_sync_mod_states`` afterwards is not optional (N106/N107): two mods
        leave and two arrive in one command, so a departing Shady has to give
        its pawn back and a departing Squid Game has to drop its pending check,
        while the arrivals get their one-off effects.
        """
        deck = self.decks.get(op.deck_id)
        if deck is None:
            return [ev.ActionRejected("Nieznana talia", "replace_mods")]
        occupied = [i for i, card in enumerate(self.mod_slots) if card is not None]
        if not occupied:
            return []

        events: List[ev.GameEvent] = []
        drawn: List[Card] = []
        for _ in occupied:
            needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
            card = deck.take_card()
            if card is None:
                break
            if needed_reshuffle:
                events.append(ev.DeckReshuffled(deck.id))
            drawn.append(card)
        if not drawn:
            return [ev.ActionRejected(f"Talia „{deck.name}” jest pusta",
                                      "replace_mods")]

        for slot, card in zip(occupied, drawn):
            outgoing = self.mod_slots[slot]
            self.mod_slots[slot] = card
            if outgoing is not None:
                self.decks[outgoing.deck_id].return_card(outgoing)
                events.append(ev.ModDiscarded(slot, outgoing.uid))
            events.append(ev.CardDrawn(actor, deck.id, card.uid))
            events.append(ev.ModPlaced(
                actor, slot, card.uid,
                outgoing.uid if outgoing is not None else None,
            ))
        events.extend(self._sync_mod_states())
        return events

    def _op_draw_cards(self, op: effects.DrawCards, actor: int) -> List[ev.GameEvent]:
        """Draw into a hand as part of an effect (Troll's replacement, Spy's)."""
        player = self.player(op.player_index)
        deck = self.decks.get(op.deck_id)
        if player is None or deck is None:
            return [ev.ActionRejected("Nieznana talia lub gracz", "draw_cards")]
        events: List[ev.GameEvent] = []
        for _ in range(max(1, int(op.count))):
            if player.hand_is_full:
                break
            events.extend(self._draw_one(player, deck))
        return events

    def _op_transfer_card(
        self, op: effects.TransferCard, actor: int
    ) -> List[ev.GameEvent]:
        """Move a card from one hand to another (Spy).

        The event names the seats and the uid, never the title: it goes to the
        whole table, and only the thief was shown the hand.
        """
        source = self.player(op.from_player)
        target = self.player(op.to_player)
        if source is None or target is None:
            return [ev.ActionRejected("Nieznany gracz", "transfer_card")]
        card = source.card_by_uid(op.card_uid)
        if card is None:
            return [ev.ActionRejected("Tej karty już tam nie ma", "transfer_card")]
        if target.hand_is_full:
            return [ev.ActionRejected("Ręka jest pełna", "transfer_card")]
        source.remove_card(card)
        target.add_card(card)
        return [ev.CardStolen(source.index, target.index, card.uid, card.deck_id)]

    def _op_highlight_card(
        self, op: effects.HighlightHeldCard, actor: int
    ) -> List[ev.GameEvent]:
        """Point at a card in a hand.  Presentation, with no state behind it."""
        player = self.player(op.player_index)
        card = player.card_by_uid(op.card_uid) if player is not None else None
        if card is None:
            return []
        return [ev.CardSpotlighted(
            player_index=op.player_index, deck_id=card.deck_id, card_uid=card.uid,
            title=card.title, text=card.text, seconds=op.seconds,
            caption=op.caption,
        )]

    def _op_forced_play(
        self, op: effects.ForcedPlay, actor: int
    ) -> List[ev.GameEvent]:
        """Pick a card out of a hand and play it for its owner (Troll).

        The pick uses the game's seeded RNG from a list sorted by uid, so every
        machine reaches for the same card from the same command — the hand's
        own order is not something two replicas are obliged to agree on, and
        relying on it would desync the moment one of them differed.

        A card whose effect is not implemented, or one that would need a
        decision nobody can be asked for in the middle of an execution, is
        played and discarded and does nothing.  That is the rule: a forced play
        must never be able to stop the game.
        """
        player = self.player(op.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", "forced_play")]

        def pool(deck_ids: Sequence[str]) -> List[Card]:
            wanted = set(deck_ids)
            return sorted(
                (c for c in player.hand
                 if c.deck_id in wanted and c.uid != op.source_uid),
                key=lambda c: c.uid,
            )

        candidates = pool(op.priority_decks) or pool(op.fallback_decks)
        if not candidates:
            return [ev.ActionRejected(
                f"{player.name} nie ma karty, którą można zagrać", "forced_play")]

        card = self.rng.choice(candidates)
        events: List[ev.GameEvent] = [ev.CardSpotlighted(
            player_index=player.index, deck_id=card.deck_id, card_uid=card.uid,
            title=card.title, text=card.text, seconds=op.seconds,
            caption=op.caption, forced=True,
        )]

        result = effects.resolve(self, card, player.index, can_ask=False)
        player.remove_card(card)
        self.decks[card.deck_id].return_card(card)
        if isinstance(result, effects.Plan) and result.ok:
            events.extend(self._execute(result, player.index))
            description = result.description
        else:
            description = "bez efektu"
        events.append(ev.CardPlayed(player.index, card.deck_id, card.uid,
                                    card.title, description))
        return events

    def _place_mod(self, command: cmd.PlaceMod) -> List[ev.GameEvent]:
        """Push a card into slot 0; the rack shifts right and overflow is discarded."""
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        card = player.card_by_uid(command.card_uid)
        if card is None:
            return [ev.ActionRejected("Karty nie ma na ręce", command.kind)]

        player.remove_card(card)
        return self._install_mod(card, player.index)

    def _install_mod(self, card: Card, player_index: int) -> List[ev.GameEvent]:
        """Push a card into slot 0; the rack shifts right, overflow is discarded.

        One way in and one implementation.  Thunderfuck used to take the first
        FREE slot instead, which put a new mod on the right while the left one
        stayed put — the opposite of the rule, and invisible until the rack was
        half full.  Both callers now push, and the selection (which owns one
        slot per faction and must not shunt the other along) writes its slot
        directly instead of coming through here.
        """
        events: List[ev.GameEvent] = []
        displaced = self.mod_slots[-1]
        if displaced is not None:
            self.decks[displaced.deck_id].return_card(displaced)
            events.append(ev.ModDiscarded(len(self.mod_slots) - 1, displaced.uid))
        for i in range(len(self.mod_slots) - 1, 0, -1):
            self.mod_slots[i] = self.mod_slots[i - 1]
        self.mod_slots[0] = card
        events.append(
            ev.ModPlaced(player_index, 0, card.uid,
                         displaced.uid if displaced is not None else None)
        )
        events.extend(self._sync_mod_states())
        return events

    def _discard_mod(self, command: cmd.DiscardMod) -> List[ev.GameEvent]:
        if not 0 <= command.slot < len(self.mod_slots):
            return [ev.ActionRejected("Nieprawidłowy slot", command.kind)]
        card = self.mod_slots[command.slot]
        if card is None:
            return [ev.ActionRejected("Slot jest pusty", command.kind)]
        self.decks[card.deck_id].return_card(card)
        self.mod_slots[command.slot] = None
        events: List[ev.GameEvent] = [ev.ModDiscarded(command.slot, card.uid)]
        events.extend(self._sync_mod_states())
        return events

    def _draw_character(self, command: cmd.DrawCharacter) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        deck = self.decks[settings.DECK_CHARACTERS]
        card = deck.take_card()
        if card is None:
            return [ev.ActionRejected("Brak kart postaci", command.kind)]

        events: List[ev.GameEvent] = []
        events.extend(self._return_character_and_skill(player))
        player.character = card
        events.append(ev.CharacterChanged(player.index, card.title))
        return events

    def _draw_skill(self, command: cmd.DrawSkill) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if player.role is not Role.PIOTREK:
            return [ev.ActionRejected("Tylko Piotrek dobiera umiejętność", command.kind)]
        deck = self.decks[settings.DECK_SKILLS]
        card = deck.take_card()
        if card is None:
            return [ev.ActionRejected("Brak kart umiejętności", command.kind)]
        if player.skill is not None:
            deck.return_card(player.skill)
        player.skill = card
        return [ev.SkillChanged(player.index, card.title)]

    def _discard_top_character_card(
        self, command: cmd.DiscardTopCharacterCard
    ) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if player.skill is not None:
            self.decks[settings.DECK_SKILLS].return_card(player.skill)
            player.skill = None
            return [ev.SkillChanged(player.index, None)]
        if player.character is not None:
            self.decks[settings.DECK_CHARACTERS].return_card(player.character)
            player.character = None
            return [ev.CharacterChanged(player.index, None)]
        return [ev.ActionRejected("Nie ma czego odrzucić", command.kind)]

    def _return_character_and_skill(self, player: Player) -> List[ev.GameEvent]:
        events: List[ev.GameEvent] = []
        if player.skill is not None:
            self.decks[settings.DECK_SKILLS].return_card(player.skill)
            player.skill = None
            events.append(ev.SkillChanged(player.index, None))
        if player.character is not None:
            self.decks[settings.DECK_CHARACTERS].return_card(player.character)
            player.character = None
            events.append(ev.CharacterChanged(player.index, None))
        return events

    # ── handlers: board ──────────────────────────────────────────────────────
    def _pick_up_token(self, command: cmd.PickUpToken) -> List[ev.GameEvent]:
        token = self.tokens.get(command.pawn_id)
        if token is None:
            return [ev.ActionRejected("Nieznany pionek", command.kind)]
        token.held = True
        return [ev.TokenPickedUp(token.id)]

    def _move_token(self, command: cmd.MoveToken) -> List[ev.GameEvent]:
        token = self.tokens.get(command.pawn_id)
        if token is None:
            return [ev.ActionRejected("Nieznany pionek", command.kind)]

        origin = token.position
        # Pawns riding on top travel with the one below — the tower rule.
        carried = self.board.carried_pawns(token.id)
        token.held = False

        if command.tile_index is not None:
            tile = self.board.tile(command.tile_index)
            if tile is None:
                return [ev.ActionRejected("Nieznane pole", command.kind)]
            self.board.place_pawn(token.id, tile.index)
            for rider in carried:
                self.board.place_pawn(rider, tile.index)
            self._sync_token_positions()
            token.tile_index = tile.index
            return [
                ev.TokenMoved(
                    token.id, origin, token.position, tile.index, list(carried), snapped=True
                )
            ]

        # Free placement — the prototype's drag-anywhere behaviour.
        self.board.remove_pawn(token.id)
        token.tile_index = None
        token.position = (float(command.x), float(command.y))
        self._sync_token_positions()
        return [ev.TokenMoved(token.id, origin, token.position, None, [], snapped=False)]

    def _sync_token_positions(self) -> None:
        """Re-read every stacked pawn's position from the board."""
        for token in self.tokens.values():
            placed = self.board.pawn_position(token.id)
            if placed is not None:
                token.position = placed
                token.tile_index = self.board.pawn_tiles.get(token.id)
            elif token.tile_index is not None:
                token.tile_index = None

    # ── handlers: flow ───────────────────────────────────────────────────────
    def _set_round(self, command: cmd.SetRound) -> List[ev.GameEvent]:
        """Set the round by hand, dealing any chest cards that fall due.

        The counter is not just a label: moving it forward passes through
        rounds, and each of those rounds owes a hunter a chest card.  Skipping
        that was why some players were fed and others never were — anybody whose
        turn came up while somebody nudged the counter simply lost their card.
        """
        new_round = max(1, int(command.round_number))
        if new_round == self.round_number:
            return []
        if new_round < self.round_number:
            self.round_number = new_round
            return [ev.RoundChanged(self.round_number)]

        events: List[ev.GameEvent] = []
        while self.round_number < new_round:
            events.extend(self._begin_round(self.round_number + 1))
        return events

    # ── who may act ──────────────────────────────────────────────────────────
    @property
    def edit_mode(self) -> bool:
        return bool(self.config.edit_mode)

    @property
    def local_seat(self) -> int:
        return max(0, min(len(self.players) - 1, int(self.config.local_seat)))

    def may_control(self, player_index: int) -> bool:
        """Whether this machine is allowed to act for a seat.

        In edit mode anybody may act for anybody — that is hot-seat play and
        the prototype's behaviour.  With it off, only the local seat, which is
        exactly the rule the network will need; putting it in the engine rather
        than in the interface means a client cannot simply ask nicely.
        """
        return self.edit_mode or player_index == self.local_seat

    def _phase_refusal(self) -> Optional[str]:
        """Why nothing may be played right now, or ``None`` when it may.

        The gate is in the engine rather than in the interface because it has
        to hold against a client that simply does not draw the overlay: until
        Piotrek has chosen a colour there is no game to play, and once somebody
        has won there is nothing left to change.
        """
        if self.phase is MatchPhase.STARTING:
            return "Gra jeszcze się nie zaczęła"
        if self.phase is MatchPhase.ENDED:
            return "Gra została zakończona"
        return None

    def _mod_selection_refusal(self, command: cmd.Command) -> Optional[str]:
        """Why a move must wait for the Mod Patusa selection to finish.

        The round genuinely pauses, so this holds against a client that simply
        does not draw the overlay.  Voting and choosing are obviously exempt —
        they are how the pause ends — and so is renaming yourself, which is
        bookkeeping rather than a move.
        """
        if self.pending_mod_selection is None:
            return None
        if not isinstance(command, self._TURN_BOUND):
            return None
        return "Najpierw wybierzcie Mody Patusa"

    def _authorise(self, command: cmd.Command) -> Optional[ev.GameEvent]:
        """Is this machine allowed to issue this command right now?"""
        if not isinstance(command, cmd.AUTHORITY_ONLY):
            problem = self._phase_refusal()
            if problem is not None:
                return ev.ActionRejected(problem, command.kind)
            paused = self._mod_selection_refusal(command)
            if paused is not None:
                return ev.ActionRejected(paused, command.kind)
        if isinstance(command, self._OWNED_BY_PLAYER):
            refusal = self._reject_foreign(
                getattr(command, "player_index", 0), command.kind
            )
            if refusal is not None:
                return refusal
        if isinstance(command, self._TURN_BOUND):
            return self._reject_out_of_turn(command)
        if isinstance(command, cmd.SetActivePlayer):
            # ``player_index`` is the seat being *handed* the turn, so the check
            # is against the local seat: you may pass your own turn on, not
            # somebody else's.  It lives here rather than in the handler because
            # a command replayed from the host must never be re-judged — the
            # client would reject every move anybody else makes.
            if not self.may_pass_turn(self.local_seat):
                return ev.ActionRejected("To nie twoja tura", command.kind)
        return None

    def authorise_remote(self, command: cmd.Command, seat: int) -> Optional[str]:
        """Why a peer may not do this, or ``None`` when it may.

        The host calls this for commands arriving over the wire.  The seat comes
        from the host's own map, never from the message, so a client cannot
        claim to be somebody else.
        """
        if isinstance(command, cmd.AUTHORITY_ONLY):
            # Starting the match, ruling a colour out and declaring a winner
            # are the server's own words.  A client that sends one is claiming
            # to have won.
            return "Tę decyzję podejmuje serwer"
        problem = self._phase_refusal()
        if problem is not None:
            return problem
        paused = self._mod_selection_refusal(command)
        if paused is not None:
            return paused
        index = getattr(command, "player_index", None)
        if index is not None and int(index) != seat:
            return "To nie jest twoje miejsce przy stole"
        if isinstance(command, cmd.SetActivePlayer):
            if not self.edit_mode and seat != self.active_player_index:
                return "To nie twoja tura"
            return None
        if isinstance(command, self._TURN_BOUND) and not self.may_act(seat):
            active = self.player(self.active_player_index)
            name = active.name if active is not None else "inny gracz"
            return f"Teraz gra {name} — poczekaj na swoją turę"
        return None

    def may_act(self, player_index: int) -> bool:
        """Whether this seat may make a move right now.

        Hot-seat editing bypasses it, because with everyone at one keyboard the
        turn order is something people keep in their heads.  Over a network it
        is the rule that keeps four people from all playing at once.
        """
        return self.edit_mode or player_index == self.active_player_index

    def _reject_out_of_turn(self, command) -> Optional[ev.GameEvent]:
        index = getattr(command, "player_index", self.active_player_index)
        if self.may_act(int(index)):
            return None
        active = self.player(self.active_player_index)
        name = active.name if active is not None else "inny gracz"
        return ev.ActionRejected(f"Teraz gra {name} — poczekaj na swoją turę",
                                 command.kind)

    def may_pass_turn(self, actor_index: int) -> bool:
        """Only the player whose turn it is may hand it on."""
        return self.edit_mode or actor_index == self.active_player_index

    def _reject_foreign(self, player_index: int, kind: str) -> Optional[ev.GameEvent]:
        if self.may_control(player_index):
            return None
        player = self.player(player_index)
        name = player.name if player is not None else "innego gracza"
        return ev.ActionRejected(
            f"Tryb edycji wyłączony — nie możesz grać za {name}", kind
        )

    def _set_active_player(self, command: cmd.SetActivePlayer) -> List[ev.GameEvent]:
        if not 0 <= command.player_index < len(self.players):
            return [ev.ActionRejected("Nieznany gracz", command.kind)]

        if command.player_index == self.active_player_index:
            return []
        self.active_player_index = command.player_index
        # Move the cursor to the slot that seat actually occupies, searching
        # FORWARD from where the round already stands.  Without this the cursor
        # still pointed at the previous seat's slot, and the next end-of-turn
        # resumed the round from the wrong place — a seat jumped to in edit
        # mode either replayed slots already spent or skipped the rest.
        order = self.seat_order()
        self.turn_slot = self._slot_for_seat(
            order, self.active_player_index, self.turn_slot
        ) if order else 0
        self.turn_counter += 1
        events: List[ev.GameEvent] = [ev.ActivePlayerChanged(self.active_player_index)]
        events.extend(self._expire_statuses())
        # A turn handed over directly is still a turn beginning.  Without this
        # a seat holding a Troll could be given the turn in edit mode and keep
        # it: the interrupt would wait for an end-of-turn that had already been
        # skipped past, and the card would look broken to whoever was testing.
        events.extend(self._begin_turn())
        return events

    def _expire_statuses(self) -> List[ev.GameEvent]:
        """Drop anything whose turn is up and report it."""
        return [
            ev.StatusEnded(status.kind.value, status.subject.value,
                           status.subject_id,
                           STATUS_LABELS.get(status.kind, status.kind.value))
            for status in self.statuses.expire(self.turn_counter)
        ]

    def _expire_round_statuses(self) -> List[ev.GameEvent]:
        """Drop statuses scoped to a round that has already gone by."""
        return [
            ev.StatusEnded(status.kind.value, status.subject.value,
                           status.subject_id,
                           STATUS_LABELS.get(status.kind, status.kind.value))
            for status in self.statuses.expire_round_statuses(self.round_number)
        ]

    # ── chest hand limit ─────────────────────────────────────────────────────
    @property
    def pending_chest_choice(self) -> Optional[Tuple[int, List[int]]]:
        """The chest limit currently waiting to be answered, oldest first.

        Kept as a single value because every caller — the turn gate, the
        interface, the tests — only ever deals with one prompt at a time.  The
        queue behind it is what stops a second overflow in the same round from
        erasing the first.
        """
        return self._pending_chest_choices[0] if self._pending_chest_choices else None

    @pending_chest_choice.setter
    def pending_chest_choice(self, value: Optional[Tuple[int, List[int]]]) -> None:
        """Assigning ``None`` clears the whole queue; a tuple replaces it."""
        self._pending_chest_choices = [] if value is None else [value]

    def queue_chest_choice(self, player_index: int, uids: List[int]) -> None:
        """Add a seat's overflow to the queue, replacing any it already had."""
        self._pending_chest_choices = [
            entry for entry in self._pending_chest_choices
            if entry[0] != player_index
        ]
        self._pending_chest_choices.append((player_index, list(uids)))

    def resolve_chest_choice(self, player_index: int) -> None:
        """That seat has answered; drop it and let the next prompt through."""
        self._pending_chest_choices = [
            entry for entry in self._pending_chest_choices
            if entry[0] != player_index
        ]

    def chest_limit(self, player: Player) -> int:
        """How many chest cards this player may hold.

        Piotrek holds more than a hunter, and his ChatGPT skill trades that
        advantage away — a passive declared in the JSON, not a special case
        written here.
        """
        limit = RULES.chest_limit_piotrek if player.is_piotrek else RULES.chest_limit_default
        for card in (player.skill, player.character):
            if card is not None:
                override = card.passive.get("chest_limit")
                if override is not None:
                    limit = int(override)
        return limit

    def chest_cards(self, player: Player) -> List[Card]:
        return [card for card in player.hand if card.deck_id == settings.DECK_CHEST]

    def _keep_chest_cards(self, command: cmd.KeepChestCards) -> List[ev.GameEvent]:
        """Answer the chest limit: keep these, discard the rest."""
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        held = self.chest_cards(player)
        limit = self.chest_limit(player)
        keep = [card for card in held if card.uid in set(command.keep_uids)]
        if len(keep) > limit:
            return [ev.ActionRejected(
                f"Można zatrzymać najwyżej {limit} kart Skrzyni", command.kind)]

        events: List[ev.GameEvent] = []
        for card in held:
            if card in keep:
                continue
            player.remove_card(card)
            self.decks[card.deck_id].return_card(card)
            events.append(ev.CardDiscarded(player.index, card.deck_id, card.uid))
        self.resolve_chest_choice(player.index)
        return events

    def _rename_player(self, command: cmd.RenamePlayer) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if not player.rename(command.name):
            return [ev.ActionRejected("Nazwa nie może być pusta", command.kind)]
        return [ev.PlayerRenamed(player.index, player.name)]

    def _toggle_mark(self, command: cmd.ToggleMark) -> List[ev.GameEvent]:
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if self.library.pawn(command.pawn_id) is None:
            return [ev.ActionRejected("Nieznany kolor", command.kind)]
        marked = player.toggle_mark(command.pawn_id)
        return [ev.MarkToggled(player.index, command.pawn_id, marked)]

    # ── the match itself (authority-issued; see commands.AUTHORITY_ONLY) ─────
    def _begin_match(self, command: cmd.BeginMatch) -> List[ev.GameEvent]:
        if self.phase is not MatchPhase.STARTING:
            return [ev.ActionRejected("Gra już się rozpoczęła", command.kind)]
        self.phase = MatchPhase.PLAYING
        return [ev.MatchBegan()]

    def _eliminate_pawn(self, command: cmd.EliminatePawn) -> List[ev.GameEvent]:
        if self.library.pawn(command.pawn_id) is None:
            return [ev.ActionRejected("Nieznany kolor", command.kind)]
        # The automatic check is settled by whatever it produced, here and in
        # ``_declare_victory``.  It has to be cleared by a COMMAND rather than
        # by the code that armed it, or the authority would clear it on its own
        # copy and every replica would keep waiting for a check that already
        # happened.
        if self.pending_lead_check == command.pawn_id:
            self.pending_lead_check = None
        if command.pawn_id in self.eliminated_pawns:
            # Not an error worth showing anybody: a colour is checked once, and
            # arriving here twice means a duplicate delivery, not a rules bug.
            return []
        self.eliminated_pawns.append(command.pawn_id)
        return [ev.PawnEliminated(command.pawn_id)]

    def _declare_victory(self, command: cmd.DeclareVictory) -> List[ev.GameEvent]:
        verdict = Verdict.from_dict({
            "outcome": command.outcome, "pawn_id": command.pawn_id,
            "piotrek_seat": command.piotrek_seat,
            "piotrek_name": command.piotrek_name,
        })
        if verdict is None:
            return [ev.ActionRejected("Nieznany wynik gry", command.kind)]
        if self.victory is not None:
            return []
        self.victory = verdict
        self.phase = MatchPhase.ENDED
        self.pending_lead_check = None
        # The reveal is now public, so the state may hold it: every client
        # learns the colour here and nowhere else.
        seat = self.player(verdict.piotrek_seat)
        if seat is not None and verdict.pawn_id:
            seat.secret_pawn = verdict.pawn_id
        return [ev.MatchEnded(verdict.outcome.value, verdict.pawn_id,
                              verdict.piotrek_seat, verdict.piotrek_name)]

    # ── snapshot (used by the network layer and by save/load) ────────────────
    def snapshot(self) -> dict:
        return {
            "round": self.round_number,
            "active_player": self.active_player_index,
            "board": self.board.to_dict(),
            "tokens": {
                tid: {"pos": list(t.position), "tile": t.tile_index}
                for tid, t in self.tokens.items()
            },
            "mod_slots": [c.uid if c else None for c in self.mod_slots],
            # An open selection is real turn state: two machines that disagree
            # about who has voted disagree about which mod is about to be in
            # play.  The candidate uids are here, but a uid is not a title —
            # nothing in the fingerprint says what Piotrek was offered.
            "mod_selection": None if self.pending_mod_selection is None else {
                "round": self.pending_mod_selection.round_number,
                "piotrek": [c.uid for c in self.pending_mod_selection.piotrek_cards],
                "hunters": [c.uid for c in self.pending_mod_selection.hunter_cards],
                "votes": {str(seat): uid
                          for seat, uid in sorted(
                              self.pending_mod_selection.votes.items())},
                "piotrek_done": self.pending_mod_selection.piotrek_done,
                "hunters_done": self.pending_mod_selection.hunters_done,
            },
            "decks": {
                did: {"draw": d.draw_count, "discard": d.discard_count}
                for did, d in self.decks.items()
            },
            "players": [p.to_public_dict() for p in self.players],
            # Public, and deliberately so: phase, notepad and verdict are the
            # same on every machine, so they belong in the fingerprint.  The
            # hidden colour is NOT here — it is not in ``to_public_dict``
            # either — which is what lets the authority hold a secret without
            # every client resyncing against it for ever.
            "phase": self.phase.value,
            "eliminated": list(self.eliminated_pawns),
            "victory": self.victory.to_dict() if self.victory else None,
            "piotrek_name": self.piotrek_name,
            "hunter_names": list(self.hunter_names),
            "turn": self.turn_counter,
            # The cursor is in the fingerprint because it is real turn state and
            # cannot be recomputed from the seat: several slots in a round hold
            # the same seat.  Two machines standing on different slots agree on
            # whose turn it is and disagree about the whole rest of the round,
            # which is precisely the kind of drift that used to go unnoticed.
            "turn_slot": self.turn_slot,
            "statuses": self.statuses.to_list(),
            # Which mods are armed, and the pending automatic check, are real
            # shared state: two machines that disagree about the round a Squid
            # Game arrived in disagree about which round it starts checking,
            # and that would never show up as anything but a mysterious extra
            # elimination on one screen.  Uids and a colour only — a uid is not
            # a title, and the checked colour is public the moment it is named.
            "armed_mods": sorted(self.armed_mods.items()),
            "pending_lead_check": self.pending_lead_check,
            "ability_uses": {
                p.index: [
                    card.uses_left
                    for card in (p.character, p.skill) if card is not None
                ]
                for p in self.players
            },
        }

    _OPERATIONS = {
        effects.MovePawn: _op_move_pawn,
        effects.MoveBySteps: _op_move_by_steps,
        effects.MoveAndCollect: _op_move_and_collect,
        effects.ReplaceMods: _op_replace_mods,
        effects.DrawCards: _op_draw_cards,
        effects.TransferCard: _op_transfer_card,
        effects.HighlightHeldCard: _op_highlight_card,
        effects.ForcedPlay: _op_forced_play,
        effects.TurnLost: _op_turn_lost,
        effects.GrantStatus: _op_grant_status,
        effects.ClearStatus: _op_clear_status,
        effects.SpendStatus: _op_spend_status,
        effects.PlayRandomCard: _op_play_random_card,
        effects.DrawIntoMods: _op_draw_into_mods,
        effects.Announce: _op_announce,
        effects.Fizzle: _op_fizzle,
    }

    _HANDLERS = {
        cmd.DrawCard: _draw_card,
        cmd.DiscardCard: _discard_card,
        cmd.PlayCard: _play_card,
        cmd.UseAbility: _use_ability,
        cmd.EndTurn: _end_turn_command,
        cmd.PlaceMod: _place_mod,
        cmd.DiscardMod: _discard_mod,
        cmd.ChooseMod: _choose_mod,
        cmd.VoteMod: _vote_mod,
        cmd.KeepChestCards: _keep_chest_cards,
        cmd.DrawCharacter: _draw_character,
        cmd.DrawSkill: _draw_skill,
        cmd.DiscardTopCharacterCard: _discard_top_character_card,
        cmd.PickUpToken: _pick_up_token,
        cmd.MoveToken: _move_token,
        cmd.SetRound: _set_round,
        cmd.SetActivePlayer: _set_active_player,
        cmd.RenamePlayer: _rename_player,
        cmd.ToggleMark: _toggle_mark,
        cmd.BeginMatch: _begin_match,
        cmd.EliminatePawn: _eliminate_pawn,
        cmd.DeclareVictory: _declare_victory,
    }
