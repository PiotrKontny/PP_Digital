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
from typing import Dict, List, Optional, Sequence, Tuple

from ..board.board import BoardModel
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
        self.pending_chest_choice: Optional[Tuple[int, List[int]]] = None
        #: How deep the current draw-causes-a-draw chain is.  Not game state —
        #: it never leaves a command — so it stays out of the snapshot.
        self._draw_depth: int = 0

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

    def chest_recipient(self, round_number: Optional[int] = None) -> Optional[str]:
        return chest_recipient_for_round(
            round_number if round_number is not None else self.round_number,
            self.chest_open_round,
            self.hunter_names,
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
        cmd.EndTurn,
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
                self.pending_chest_choice = (
                    player.index, [c.uid for c in held]
                )
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
        """Move to a new round, handing out a chest card if one is due."""
        self.round_number = max(1, round_number)
        self.turn_slot = 0
        events: List[ev.GameEvent] = [ev.RoundChanged(self.round_number)]
        events.extend(self._distribute_chest_card())
        return events

    def _distribute_chest_card(self) -> List[ev.GameEvent]:
        """Give this round's chest card to the hunter whose turn it is to get one.

        The interface already announced who was due and when; this is that
        promise being kept.  It goes through the ordinary draw path, so the
        hand limit and its keep-or-discard prompt behave exactly as they do for
        a card drawn by hand.
        """
        if not self.chest_is_open:
            return []
        seat = self.chest_recipient_seat()
        player = self.player(seat) if seat is not None else None
        if player is None:
            return []
        deck = self.decks[settings.DECK_CHEST]
        events: List[ev.GameEvent] = []
        needed_reshuffle = not deck.draw_pile and bool(deck.discard_pile)
        card = deck.take_card()
        if card is None:
            return []
        if needed_reshuffle:
            events.append(ev.DeckReshuffled(deck.id))
        player.add_card(card)
        events.append(ev.CardDrawn(player.index, deck.id, card.uid))
        events.append(ev.ChestCardAwarded(player.index, card.uid, self.round_number))
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
        """Draw a mod and slot it into the rack."""
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
        events.extend(self._install_mod(card, actor, prefer_free_slot=True))
        return events

    def _op_turn_lost(self, op: effects.TurnLost, actor: int) -> List[ev.GameEvent]:
        return [ev.TurnSkipped(op.player_index, op.source)]

    def _op_announce(self, op: effects.Announce, actor: int) -> List[ev.GameEvent]:
        return [ev.ActionRejected(op.text, "announce")]

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
        result = effects.resolve(self, card, actor)
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
        return self._op_move_pawn(
            effects.MovePawn(
                pawn_id=op.pawn_id,
                from_index=start,
                route=route,
                tiles=effects.tile_route(self, op.pawn_id, route, op.chosen_tile),
                carried=effects.travellers(self, op.pawn_id),
            ),
            actor,
        )

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

        result = effects.resolve(self, card, player.index)
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

    def _install_mod(self, card: Card, player_index: int,
                     prefer_free_slot: bool = False) -> List[ev.GameEvent]:
        """Put a card into the mod rack.

        Two ways in, one implementation.  A card played by hand always pushes
        into slot 0 and shifts the rack right, discarding the overflow — the
        prototype's rule.  A card that arrives on its own (Thunderfuck) takes
        the first free slot if there is one, and otherwise falls back to that
        same push, so it never quietly vanishes.
        """
        events: List[ev.GameEvent] = []
        if prefer_free_slot:
            for index, occupant in enumerate(self.mod_slots):
                if occupant is None:
                    self.mod_slots[index] = card
                    events.append(ev.ModPlaced(player_index, index, card.uid, None))
                    return events

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
        return events

    def _discard_mod(self, command: cmd.DiscardMod) -> List[ev.GameEvent]:
        if not 0 <= command.slot < len(self.mod_slots):
            return [ev.ActionRejected("Nieprawidłowy slot", command.kind)]
        card = self.mod_slots[command.slot]
        if card is None:
            return [ev.ActionRejected("Slot jest pusty", command.kind)]
        self.decks[card.deck_id].return_card(card)
        self.mod_slots[command.slot] = None
        return [ev.ModDiscarded(command.slot, card.uid)]

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

    def _authorise(self, command: cmd.Command) -> Optional[ev.GameEvent]:
        """Is this machine allowed to issue this command right now?"""
        if not isinstance(command, cmd.AUTHORITY_ONLY):
            problem = self._phase_refusal()
            if problem is not None:
                return ev.ActionRejected(problem, command.kind)
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

    # ── chest hand limit ─────────────────────────────────────────────────────
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
        self.pending_chest_choice = None
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
    }

    _HANDLERS = {
        cmd.DrawCard: _draw_card,
        cmd.DiscardCard: _discard_card,
        cmd.PlayCard: _play_card,
        cmd.UseAbility: _use_ability,
        cmd.EndTurn: _end_turn_command,
        cmd.PlaceMod: _place_mod,
        cmd.DiscardMod: _discard_mod,
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
