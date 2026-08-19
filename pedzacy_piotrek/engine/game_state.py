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
from . import undo
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
class PendingTowerBreakup:
    """A failed check under variant 2: the tower is about to come apart.

    NOT A SLEEP.  The two-second pause is a deadline on the authority's clock,
    aged by the same tick that times out a movement decision and an Ice Block
    window, so nothing blocks and every machine sees the same thing happen at
    the same point in the command log.

    ``choice_position`` is a doubled row — 2a / 2b — that PIOTREK picks a field
    on.  It is his choice and not the mover's: the tower was built by somebody
    else's card, and the brief gives the scattering to him.  If he says nothing
    before the deadline the first field is used, deterministically, so a
    disconnected Piotrek cannot hang the table.
    """

    tile_index: int
    #: ``[(board position, [pawns bottom-to-top]), ...]``, nearest field first.
    groups: List[Tuple[int, List[str]]] = field(default_factory=list)
    seat: int = -1
    #: The doubled position awaiting Piotrek's pick, or ``None``.
    choice_position: Optional[int] = None
    chosen_tile: Optional[int] = None
    seconds: float = 2.0
    opened_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"tile": self.tile_index, "seat": self.seat,
                "groups": [[position, list(pawns)]
                           for position, pawns in self.groups],
                "choice_position": self.choice_position,
                "chosen_tile": self.chosen_tile,
                "seconds": self.seconds}


@dataclass
class PendingCheckDecision:
    """A CHECK that has been paused, waiting on Piotrek's Ice Block answer.

    The same shape as :class:`PendingMovementDecision` and for the same
    reasons.  The check has been RECOGNISED and not resolved: nothing has been
    crossed off, no identity has been compared and no tower has been broken, so
    refusing has nothing to undo.  Allowing runs the same check against the
    same table, which is deterministic.

    ``pawn_id`` — the colour about to be checked — is PUBLIC and always was:
    everybody can see which pawn is at the bottom of the tower.  What stays
    secret is the answer, which is why refusing produces no reveal.

    THE CLOCK IS NOT HERE, for the reason given on the movement decision:
    ``seconds`` is configuration and identical everywhere, ``opened_at`` is
    wall-clock time and lives on the authority only.
    """

    #: Where the check came from, so the authority resolves the right one.
    source: str
    pawn_id: str
    #: Piotrek's seat — the only one entitled to answer.
    seat: int
    seconds: float = 10.0
    opened_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "pawn": self.pawn_id,
                "seat": self.seat, "seconds": self.seconds}


@dataclass
class PendingMovementDecision:
    """A movement that has been PAUSED, waiting on the opponents who may stop it.

    The card has been played and its effect RESOLVED, and then nothing else has
    happened: the plan was thrown away rather than carried out, and the card is
    still in its owner's hand.  That is the whole of "rollback before
    irreversible consequences" — there is nothing to roll back, because nothing
    ran.  Accepting re-runs the same command against the same board, which is
    deterministic; blocking discards the card and never runs it at all.

    Everything here is REAL GAME STATE and travels in the snapshot, because two
    machines that disagree about whether the table is waiting for an answer
    disagree about whose turn it is.

    THE CLOCK IS NOT HERE.  ``seconds`` is the length of the window, which is
    configuration and identical everywhere; WHEN it started is wall-clock time,
    which is different on every machine, so it lives on the authority
    (``opened_at``, set by the session or the room) and is deliberately left
    out of the fingerprint.
    """

    #: The seat whose card is waiting, and the command that will replay it.
    player_index: int
    card_uid: int
    deck_id: str
    title: str
    choices: Dict[str, str] = field(default_factory=dict)
    #: Seats holding a usable veto against THIS movement.  Fixed when the
    #: window opens: a veto granted while the window is open belongs to the
    #: next movement, not to the one already on the table.
    blockers: List[int] = field(default_factory=list)
    #: Blockers who have said "let it happen".  The window ends when every
    #: blocker has, so one hunter's acceptance cannot spend Piotrek's chance.
    accepted: List[int] = field(default_factory=list)
    seconds: float = 7.0
    #: Wall clock, on the authority only.  ``None`` on a client, which counts
    #: its own countdown from the event it was sent purely to draw it.
    opened_at: Optional[float] = None

    @property
    def waiting_for(self) -> List[int]:
        return [seat for seat in self.blockers if seat not in self.accepted]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player": self.player_index,
            "card": self.card_uid,
            "blockers": sorted(self.blockers),
            "accepted": sorted(self.accepted),
            "seconds": self.seconds,
        }


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
    #: Uids of mods that reached the rack DURING this selection and owe a
    #: follow-up window (Paczka's list, today).  A mod chosen by one faction
    #: must not interrupt the other faction's choice with a window of its own,
    #: so the arrival is recorded here and replayed when the whole selection
    #: finishes.  Uids rather than events, so it survives the snapshot and a
    #: reconnecting client rebuilds the same queue from the same state.
    followup_uids: List[int] = field(default_factory=list)

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
        #: A check somebody ASKED for, as ``(colour, seat staking themselves)``.
        #: Glockboy's ability is the only thing that raises it today.  Exactly
        #: the same split as ``pending_lead_check`` above and for exactly the
        #: same reason: naming the colour is public and happens everywhere,
        #: answering it needs the secret and happens on the authority only.
        #: The seat is carried because a wrong answer costs that player the
        #: game, and the authority has to know who to charge.
        self.pending_pawn_check: Optional[Tuple[str, int]] = None
        #: (deck id, title) -> the variant THIS MATCH plays that card under.
        #:
        #: Seeded from the config the match was built from, so it starts out
        #: agreeing with the definitions ``setup.build_decks`` already applied,
        #: and it is the thing ``SetCardVariant`` moves.  It is configuration
        #: rather than a second copy of the cards: the definitions on the
        #: physical copies are its PROJECTION, rewritten from here whenever it
        #: changes, and cards.json is never touched by either.
        #:
        #: Every title the content declares variants for is in here from the
        #: start, including titles with no copies in the match — a deck the
        #: lobby set to zero copies still has a setting, and it has to survive
        #: somebody adding a copy back from the library.
        self.card_variants: Dict[Tuple[str, str], str] = {}
        self._seed_card_variants()
        #: The movement waiting on an opponent's answer, or ``None``.
        #: Nothing about that card has happened while this is set — see
        #: :class:`PendingMovementDecision`.
        self.pending_movement: Optional[PendingMovementDecision] = None
        #: The last played card's checkpoint, and the seat that may rewind it.
        #: Undo and Liskowy Konkurs share it, which is what makes them share a
        #: window without either knowing about the other.
        self.turn_window: Optional[undo.TurnCheckpoint] = None
        #: seat -> card plays still owed this turn.  Liskowy Konkurs used
        #: BEFORE playing grants one, and ``_after_play`` spends it instead of
        #: handing the turn on.
        self.extra_plays: Dict[int, int] = {}
        #: A check paused on Piotrek's Ice Block answer.
        self.pending_check: Optional[PendingCheckDecision] = None
        #: A colour Piotrek has ALLOWED through, so the check that opened the
        #: window resolves instead of re-opening it for ever.  Cleared the
        #: moment it is used.
        self.check_allowed: Optional[str] = None
        #: Set when Ice Block refuses.  "Pionki muszą być rozdzielone przed
        #: kolejnym sprawdzeniem" — the card's own text is the rule that stops
        #: the same intact tower being re-checked on the very next command.
        #: Cleared as soon as the pawns are no longer all on one field.
        self.check_needs_separation: bool = False
        #: Checking variant 2: a failed check has armed a tower breakup.
        self.pending_breakup: Optional[PendingTowerBreakup] = None
        #: The card uid currently being REPLAYED after its decision window
        #: closed.  Not game state — it never leaves the command that set it,
        #: exactly as ``_draw_depth`` does not — and it exists so that a
        #: movement which has just been allowed is not held for a second
        #: decision by the very veto that let it through.
        self._resolved_movement: Optional[int] = None

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
        #: Where an Alter Ego swap has got to, or "" when none is running.
        #: PUBLIC and in the snapshot — it stops the table, so every machine has
        #: to agree that it is stopped — and it never names a colour, which is
        #: what lets a replica that has never been told the secret hold exactly
        #: the same value as the authority.
        self.identity_swap: str = ""
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

    # ── card variants ────────────────────────────────────────────────────────
    def _seed_card_variants(self) -> None:
        """Start every variant card off on the variant the config chose.

        Falls back to the card's FIRST variant, which is why a match built by
        an older client — or by any of the hundred tests that pass no mapping
        at all — plays exactly the card that shipped before variants existed.
        An id the card does not declare is dropped here rather than stored, so
        one stale entry cannot make the library and the deck disagree.
        """
        chosen = dict(getattr(self.config, "card_variants", {}) or {})
        for deck_id in self.library.deck_order:
            try:
                definition = self.library.deck(deck_id)
            except Exception:      # pragma: no cover - defensive
                continue
            for card in definition.cards:
                if not card.has_variants:
                    continue
                wanted = str(chosen.get(card.title, ""))
                if wanted not in card.variant_ids:
                    wanted = card.default_variant
                self.card_variants[(deck_id, card.title)] = wanted

    def card_variant(self, deck_id: str, title: str) -> str:
        """Which variant this match plays that card under, or "" for none."""
        return self.card_variants.get((deck_id, title), "")

    def variant_definition(self, deck_id: str, title: str) -> Optional[CardDef]:
        """The card as this match reads it: printed definition plus variant.

        What the Card Library draws.  It builds its display cards from the
        content library, which holds the PRINTED definitions and knows nothing
        about this match — so without this the book would show variant 1's
        sentence on a table playing variant 2.
        """
        definition = self._definition_of(deck_id, title)
        if definition is None or not definition.has_variants:
            return definition
        return definition.with_variant(self.card_variant(deck_id, title))

    @property
    def cancels_ability_effects(self) -> bool:
        """True while a mod in the rack cancels ability effects as it lands.

        Sesja na PG's second variant.  A ``mod_rule`` like every other, so the
        rack answers it and no part of the engine asks a mod what it is called
        or which variant it is — the variant chose the ``passive``, and the
        passive is the rule.
        """
        return bool(self.mod_rule("cancel_ability_effects", False))

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
    def carries_neighbour(self) -> bool:
        """True while every movement card drags a neighbouring pawn along (AKO).

        A ``mod_rule`` like every other, so the rack answers it and nothing in
        the engine asks a mod what it is called.  WHICH neighbour is a decision
        for the effect engine, which is the only thing that knows the direction
        the card is about to move in.
        """
        return bool(self.mod_rule("carry_neighbour", False))

    @property
    def carries_neighbour_alone(self) -> bool:
        """True while that neighbour travels WITHOUT its tower (AKO variant 2).

        The card says "TYLKO jednego" and this is the whole of the difference
        between the two variants: variant 1 lets the ordinary tower rule carry
        whatever is standing on the chosen pawn, variant 2 lifts that one pawn
        out of the stack and leaves the rest where it was.

        Meaningless on its own — a rack that says this and not
        :attr:`carries_neighbour` moves nobody — because both variants declare
        both keys and the second only ever narrows the first.
        """
        return bool(self.mod_rule("carry_neighbour_alone", False))

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

    # ── the gate every character ability passes through ──────────────────────
    def pawns_on_start(self) -> List[str]:
        """Pawns that have not left the starting camp yet.

        A pawn Obóz Harcerski is holding off the map is NOT one of these.  It
        has left START — it is simply somewhere else for a round — and treating
        it as though it were still in the camp would lock every ability in the
        game for the length of that card, which is not a rule anybody wrote.
        """
        return [pawn.id for pawn in self.visible_pawns
                if self.board.position_of_pawn(pawn.id) is None]

    def ability_refusal(self) -> Optional[str]:
        """Why NO character ability may be activated right now, or ``None``.

        ONE GATE FOR EVERY ABILITY, PRESENT AND FUTURE.  The brief's global
        rule — nothing may be activated while a pawn is still on START — is
        deliberately not implemented inside the four abilities this stage
        touches.  A rule written four times is a rule the fifth ability will
        not have, and the fifth ability is the one that will be written by
        somebody who never read this paragraph.

        Sesja na PG's lock lives here too, so that ``_use_ability`` asks one
        question rather than a growing list of them and the interface can grey
        the button using the same answer the engine will give.
        """
        if self.abilities_locked:
            return "Sesja na PG — umiejętności postaci są zablokowane"
        waiting = self.pawns_on_start()
        if waiting:
            names = ", ".join(
                pawn.name for pawn in self.library.pawns if pawn.id in waiting
            )
            return (f"Nie można używać umiejętności, dopóki pionki stoją na "
                    f"starcie ({names})")
        return None

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

    #: The two stages of an Alter Ego swap.  ``SWAP_REVEALING`` means the card
    #: has been played and the authority has not yet published the old colour;
    #: ``SWAP_CHOOSING`` means it has, and Piotrek is picking a new one.
    SWAP_REVEALING = "revealing"
    SWAP_CHOOSING = "choosing"

    @property
    def awaiting_identity(self) -> bool:
        """True while the table is stopped for Piotrek to pick a colour.

        Read alongside ``phase.playable`` everywhere a command is judged, so
        that an Alter Ego pause refuses moves for the same reason and by the
        same route the opening pause does.
        """
        return bool(self.identity_swap)

    def swap_forbidden_pawn(self) -> Optional[str]:
        """The colour Piotrek may NOT choose during a swap: the one he just left.

        It is ``eliminated_pawns[-1]`` rather than something remembered
        separately, because ``RevealIdentity`` wipes the notepad down to exactly
        that one colour — so the list IS the answer, and there is no second
        copy of it to fall out of step.
        """
        if self.identity_swap != self.SWAP_CHOOSING:
            return None
        return self.eliminated_pawns[-1] if self.eliminated_pawns else None

    def set_piotrek_pawn(self, pawn_id: str) -> bool:
        """Record the chosen colour.  Refuses an unknown or an illegal choice.

        Never reached through a command, and that is the point: a command is
        logged and broadcast, and this must not be either.

        A SECOND choice is refused as before, EXCEPT during an Alter Ego swap —
        which is the one rule in the game that hands the question back.  The
        colour just revealed is refused there, because the card trades one
        identity for a different one and hunters have already been told that
        colour is not Piotrek.
        """
        seat = self.piotrek_seat
        player = self.player(seat) if seat is not None else None
        if player is None:
            return False
        if self.library.pawn(pawn_id) is None:
            return False
        if self.identity_swap == self.SWAP_CHOOSING:
            if pawn_id == self.swap_forbidden_pawn():
                return False
            player.secret_pawn = pawn_id
            return True
        if player.secret_pawn:
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
        cmd.DrawCard, cmd.DrawTitledCard, cmd.DiscardCard, cmd.PlayCard,
        cmd.UseAbility,
        cmd.PlaceMod, cmd.KeepChestCards, cmd.DrawCharacter, cmd.DrawSkill,
        cmd.DiscardTopCharacterCard, cmd.ToggleMark, cmd.RenamePlayer,
        cmd.EndTurn, cmd.ChooseMod, cmd.VoteMod,
        # Answering a movement is about a particular seat — and it happens on
        # SOMEBODY ELSE's turn, so it is owned but deliberately not turn-bound.
        cmd.AcceptMovement, cmd.BlockMovement,
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
        events.extend(self._deliver_card(player, deck, card, command.kind))
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

    def _deliver_card(self, player: Player, deck: Deck, card: Card,
                      kind: str) -> List[ev.GameEvent]:
        """A card has left the deck; give it to a hand, with what that owes.

        Factored out of ``_draw_one`` when 'Dobierz kartę' arrived (stage 33):
        a card that arrives by name owes exactly the same debts as one that
        arrives off the top — it is reported as a draw, and it acts on the way
        in, which for a chest card can mean a limit prompt and for the
        Gamechanger a reveal.  Two callers taking cards out of a deck by
        different routes and then doing the arrival themselves is how one of
        them quietly stops running ``_after_draw``.
        """
        player.add_card(card)
        events: List[ev.GameEvent] = [ev.CardDrawn(player.index, deck.id, card.uid)]
        events.extend(self._after_draw(player, card))
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
        events.extend(self._deliver_card(player, deck, card, "draw"))
        return events

    def _draw_titled_card(self, command: cmd.DrawTitledCard) -> List[ev.GameEvent]:
        """'Dobierz kartę': fetch ONE NAMED card out of ONE named deck.

        Everything about this is scoped to the deck the library asked about, so
        a Chest card and a Movement card that happen to share a title cannot be
        confused for one another — the deck id comes from the tab the button
        was under, and only that deck is searched.

        There is no fabrication anywhere: if the deck does not hold a copy, the
        answer is a refusal and the hand is not touched.  The refusal names the
        card, because 'nie ma' about an unnamed card is not feedback.
        """
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if command.deck_id not in settings.TABLE_DECKS:
            return [ev.ActionRejected("Z tej talii nie można dobierać",
                                      command.kind)]
        deck = self.decks.get(command.deck_id)
        if deck is None:
            return [ev.ActionRejected("Nieznana talia", command.kind)]
        if player.hand_is_full:
            return [ev.ActionRejected(f"Ręka pełna ({RULES.max_hand} kart)",
                                      command.kind)]

        card = deck.take_titled(command.title, include_discard=True)
        if card is None:
            return [ev.ActionRejected(
                f"Brak karty „{command.title}” w talii", command.kind)]
        return self._deliver_card(player, deck, card, command.kind)

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

        # THE CHECKPOINT IS TAKEN HERE, after the questions and the refusals
        # and before anything moves.  Earlier would photograph a table that was
        # never reached (a card that turns out to be illegal changes nothing);
        # later would photograph the change itself.  It also CLOSES the
        # previous player's window — see ``_open_turn_window``.
        self._open_turn_window(player.index, card.uid)

        # Nie masz Rosji.  The plan is RESOLVED and then set aside: the card
        # stays in the hand, the board is untouched, and nothing that a
        # movement causes — a tower, a check, a victory — has happened yet.
        # Accepting replays this exact command; blocking never runs it at all.
        held = self._open_movement_decision(player, card, command, result)
        if held is not None:
            return held

        events = self._execute(result, player.index)
        player.remove_card(card)
        self.decks[card.deck_id].return_card(card)
        events.append(
            ev.CardPlayed(player.index, card.deck_id, card.uid, card.title,
                          result.description)
        )
        events.extend(self._after_play(player, card))
        return events

    # ── pausing a movement (Nie masz Rosji, Przerwanie Systemowe) ────────────
    #: Kept as a name because tests and the interface read it, but the list
    #: itself now lives in ``effects.blockable_decks`` — a veto narrows it per
    #: variant, and two copies would drift the moment one of them did.
    _BLOCKABLE_DECKS = effects.blockable_decks()

    def _open_movement_decision(
        self, player: Player, card: Card, command: cmd.PlayCard, result
    ) -> Optional[List[ev.GameEvent]]:
        """Hold this movement if an opponent is entitled to stop it.

        Returns the events to send instead of playing the card, or ``None``
        when the card should simply be played — which is the ordinary case and
        the one every other card in the game takes.

        The AUTOMATIC FINAL BLOCK is decided here rather than after a window,
        because a window nobody has a reason to answer is not a decision: if
        this is the last movement the veto could ever stop, the card promised
        one block and this is it.
        """
        if self.pending_movement is not None:
            return None
        if self._resolved_movement == card.uid:
            return None
        if card.deck_id not in effects.blockable_decks():
            return None
        if not effects.plan_moves_pawns(result):
            return None
        blockers = self._blockers_for(player.index, card.deck_id)
        if not blockers:
            return None

        forced = [seat for seat in blockers
                  if self._veto_is_last_chance(self.veto_of(seat), player.index)]
        if forced:
            # Lowest seat, so every replica picks the same one without anybody
            # being asked — the same fixed tie-break the rest of the engine uses.
            return self._apply_block(forced[0], player, card, automatic=True)

        self.pending_movement = PendingMovementDecision(
            player_index=player.index, card_uid=card.uid, deck_id=card.deck_id,
            title=card.title, choices=dict(command.choices or {}),
            blockers=blockers, seconds=self.block_decision_seconds,
        )
        return [ev.MovementDecisionOpened(
            player_index=player.index, card_uid=card.uid, title=card.title,
            deck_id=card.deck_id, blockers=list(blockers),
            seconds=self.block_decision_seconds,
        )]

    def _resume_movement(self) -> List[ev.GameEvent]:
        """Let the held card be played after all.

        Replays the ORIGINAL command through the ordinary path, with the
        decision cleared so it is not held a second time.  Nothing has changed
        in between — every other command is refused while a decision is open —
        so the effect resolves to what it resolved to before.
        """
        decision = self.pending_movement
        self.pending_movement = None
        if decision is None:
            return []
        self._resolved_movement = decision.card_uid
        try:
            return self._play_card(cmd.PlayCard(
                player_index=decision.player_index,
                card_uid=decision.card_uid,
                choices=dict(decision.choices),
            ))
        finally:
            self._resolved_movement = None

    def _apply_block(self, seat: int, player: Player, card: Card,
                     automatic: bool = False) -> List[ev.GameEvent]:
        """Stop a movement: spend the veto, discard the card, pass the turn.

        THE MOVEMENT NEVER HAPPENS.  There is no board change to undo and no
        consequence to prevent afterwards, because the plan is simply dropped —
        which is why a blocked movement cannot have moved a pawn onto a tower,
        cannot have triggered a check and cannot have won anybody the game.

        The card goes through the ordinary card lifecycle: out of the hand and
        onto its own deck's discard pile, exactly as a played card does, and
        the turn ends the way playing that card would have ended it.
        """
        status = self.veto_of(seat)
        events: List[ev.GameEvent] = []
        if status is not None:
            self.statuses.spend_charge(status)
            events.append(ev.StatusEnded(
                status.kind.value, status.subject.value, status.subject_id,
                STATUS_LABELS.get(status.kind, status.kind.value)))
        self.pending_movement = None

        player.remove_card(card)
        self.decks[card.deck_id].return_card(card)
        events.append(ev.MovementBlocked(
            blocker_index=seat, player_index=player.index, card_uid=card.uid,
            title=card.title, deck_id=card.deck_id, automatic=automatic,
        ))
        events.append(ev.CardDiscarded(player.index, card.deck_id, card.uid))
        events.extend(self._after_play(player, card))
        return events

    def _decision_card(self):
        """The held card and its owner, or ``(None, None)``."""
        decision = self.pending_movement
        if decision is None:
            return None, None
        player = self.player(decision.player_index)
        if player is None:
            return None, None
        return player, player.card_by_uid(decision.card_uid)

    def _accept_movement(self, command: cmd.AcceptMovement) -> List[ev.GameEvent]:
        """One blocker says "let it happen".

        The veto is NOT spent — the card promises one block during its whole
        life, and declining to use it here leaves it available for the next
        eligible movement.  The window closes once every blocker has said so,
        because one hunter's acceptance must not spend Piotrek's chance.
        """
        decision = self.pending_movement
        if decision is None:
            return [ev.ActionRejected("Nie ma ruchu do rozpatrzenia",
                                      command.kind)]
        seat = int(command.player_index)
        if seat not in decision.blockers:
            return [ev.ActionRejected("Nie możesz rozpatrywać tego ruchu",
                                      command.kind)]
        if seat not in decision.accepted:
            decision.accepted.append(seat)
        events: List[ev.GameEvent] = [ev.MovementAccepted(
            blocker_index=seat, player_index=decision.player_index,
            card_uid=decision.card_uid, timeout=False,
        )]
        if decision.waiting_for:
            return events
        events.extend(self._resume_movement())
        return events

    def _block_movement(self, command: cmd.BlockMovement) -> List[ev.GameEvent]:
        """One blocker stops the movement.  First one through wins.

        The authority applies commands one at a time and in order, so the
        second blocker's message finds no decision to answer and is refused —
        which is what stops two machines from independently cancelling the same
        movement.
        """
        decision = self.pending_movement
        if decision is None:
            return [ev.ActionRejected("Nie ma ruchu do zablokowania",
                                      command.kind)]
        seat = int(command.player_index)
        if seat not in decision.blockers:
            return [ev.ActionRejected("Nie możesz zablokować tego ruchu",
                                      command.kind)]
        player, card = self._decision_card()
        if player is None or card is None:
            self.pending_movement = None
            return [ev.ActionRejected("Karty już nie ma", command.kind)]
        return self._apply_block(seat, player, card)

    def _expire_movement_decision(
        self, command: cmd.ExpireMovementDecision
    ) -> List[ev.GameEvent]:
        """The window ran out: the movement is accepted, nobody's veto is spent.

        AUTHORITY ONLY.  The countdown a client draws is a picture of this
        command arriving, never the thing that decides it — a machine with a
        fast clock cannot time anybody else out, and one with a slow clock
        cannot give itself longer to think.
        """
        decision = self.pending_movement
        if decision is None:
            return []
        events: List[ev.GameEvent] = [ev.MovementAccepted(
            blocker_index=-1, player_index=decision.player_index,
            card_uid=decision.card_uid, timeout=True,
        )]
        events.extend(self._resume_movement())
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
        if self.extra_play_pending(player.index):
            # LISKOWY KONKURS, USED BEFORE THE MOVE: the turn does not pass
            # after the first card.  No refill either — the extra card was
            # dealt when the ability was activated, and drawing again here
            # would hand out a third.
            self.extra_plays[player.index] -= 1
            if self.extra_plays[player.index] <= 0:
                self.extra_plays.pop(player.index, None)
            return [ev.ExtraPlayUsed(player_index=player.index,
                                     plays_left=int(self.extra_plays.get(
                                         player.index, 0)))]
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

    # ── Nie masz Rosji: the veto, and what a "full round" means ─────────────
    @property
    def block_decision_seconds(self) -> float:
        """How long an opponent gets to answer a blockable movement."""
        return float(max(RULES.block_decision_min,
                         min(RULES.block_decision_max,
                             int(self.config.block_decision_seconds))))

    @property
    def check_decision_seconds(self) -> float:
        """How long Piotrek gets to answer a check he may refuse."""
        return float(max(RULES.block_decision_min,
                         min(RULES.block_decision_max,
                             int(getattr(self.config, "check_decision_seconds",
                                         RULES.check_decision_default)))))

    @property
    def check_variant(self) -> str:
        return str(getattr(self.config, "check_variant", "continue"))

    @property
    def victory_variant(self) -> str:
        return str(getattr(self.config, "victory_variant", "own_pawn"))

    def vetoes(self) -> List[Status]:
        """Every live Nie masz Rosji, in seat order."""
        return sorted(self.statuses.of_kind(StatusKind.MOVEMENT_VETO),
                      key=lambda status: int(status.subject_id or 0))

    def veto_of(self, seat: int) -> Optional[Status]:
        return self.statuses.find(StatusKind.MOVEMENT_VETO, Subject.PLAYER,
                                  str(seat))

    def are_opponents(self, one: int, other: int) -> bool:
        """Whether these two seats are on opposite sides of the table.

        THE ONLY DEFINITION OF "OPPONENT" IN THIS FEATURE, and it is read from
        the roles the game already has: Piotrek against every hunter, every
        hunter against Piotrek, and no hunter against another hunter.  No
        character title appears anywhere near it.
        """
        first, second = self.player(one), self.player(other)
        if first is None or second is None or one == other:
            return False
        return first.is_piotrek != second.is_piotrek

    def veto_covers(self, status: Status, mover: int,
                    deck_id: Optional[str] = None) -> bool:
        """Whether this veto is entitled to stop THIS movement.

        THE ONE PLACE THE VARIANTS DIFFER, and everything downstream — the
        window, the countdown, the automatic final block, the interface's
        buttons — asks it rather than working the answer out again.  Two
        questions, both read off the status rather than off a card title:

        * WHO is moving.  ``opponents`` is Nie masz Rosji's rule, anybody on
          the other side of the table.  ``piotrek`` is Przerwanie Systemowe's,
          and it is narrower than "opponent" even for a hunter — a table with
          no Piotrek seat gives nobody to interrupt rather than everybody.
        * WHAT they played.  The deck, which is the game's own card category:
          Przerwanie Systemowe's variants 2 and 4 allow the movement deck only,
          so a Chest card that moves a pawn passes ``are_opponents`` and is
          still not blockable by them.

        ``deck_id`` of ``None`` asks the looser question "could this veto ever
        stop a movement by that seat", which is what the last-chance simulation
        needs when it is looking ahead at turns whose cards do not exist yet.
        """
        owner = int(status.subject_id or 0)
        targets = str(status.data.get("targets", effects.VETO_OPPONENTS))
        if targets == effects.VETO_PIOTREK:
            piotrek = self.piotrek_seat
            if piotrek is None or mover != piotrek or owner == piotrek:
                return False
        elif not self.are_opponents(owner, mover):
            return False
        if deck_id is None:
            return True
        allowed = status.data.get("decks") or effects.blockable_decks()
        return deck_id in [str(deck) for deck in allowed]

    def _note_turn_completed(self, seat: int) -> List[ev.GameEvent]:
        """A seat has finished a turn: age every veto by that much.

        A FULL ROUND IS NOT THE ROUND COUNTER.  It is "everybody has had a
        turn since the card was played", which is a different thing on this
        table because the round counter restarts on a cadence in which Piotrek
        holds every third slot — so his second appearance inside one round must
        not end anything, and a round boundary in the middle of the effect must
        not either.  Each veto therefore carries the set of seats that still
        owe it a turn; when that empties, one full round has passed.
        """
        events: List[ev.GameEvent] = []
        for status in self.vetoes():
            if int(status.data.get("rounds", 1)) == effects.UNLIMITED_ROUNDS:
                # Przerwanie Systemowe's variants 1 and 2: no clock at all.
                # It is spent by being used or it is not spent, and turns
                # passing do nothing to it.
                continue
            pending = [int(s) for s in status.data.get("pending", [])]
            if seat in pending:
                pending.remove(seat)
            if pending:
                status.data["pending"] = pending
                continue
            left = int(status.data.get("rounds_left", 1)) - 1
            status.data["rounds_left"] = left
            if left <= 0:
                self.statuses.discard(status)
                events.append(ev.StatusEnded(
                    status.kind.value, status.subject.value,
                    status.subject_id,
                    STATUS_LABELS.get(status.kind, status.kind.value)))
                continue
            # Another full round begins, and it owes the same debt again —
            # every seat except the one whose veto this is, which has just been
            # given its turn back by the round that ended.
            status.data["pending"] = [
                player.index for player in self.players
                if str(player.index) != status.subject_id
            ]
        return events

    def _upcoming_seats(self, limit: int = 200) -> List[int]:
        """The seats that will act after the current one, in order.

        Walks the real cadence forward through as many rounds as it needs,
        which is the only honest way to answer "is there another opponent turn
        before this expires?" on a table where one seat holds every third slot.
        """
        seats: List[int] = []
        round_number = self.round_number
        slot = self.current_slot()
        order = self.seat_order(round_number)
        while len(seats) < limit:
            slot += 1
            if slot >= len(order):
                round_number += 1
                order = self.seat_order(round_number)
                slot = 0
                if not order:
                    break
            seats.append(order[slot])
        return seats

    def _veto_is_last_chance(self, status: Status, mover: int) -> bool:
        """Whether this movement is the veto's FINAL opportunity.

        Simulates the rest of the effect's life against the real turn order:
        the current turn completes, seats keep completing turns, full rounds
        keep elapsing, and the question is whether any LATER turn belongs to an
        opponent of the veto's owner while the effect is still alive.  If none
        does, this movement is the last one it could ever stop — which is when
        the brief says it fires by itself rather than opening a window nobody
        would have a reason to answer.
        """
        owner = int(status.subject_id or 0)
        pending = {int(s) for s in status.data.get("pending", [])}
        rounds_left = int(status.data.get("rounds_left", 1))
        unlimited = int(status.data.get("rounds", 1)) == effects.UNLIMITED_ROUNDS
        everyone = [player.index for player in self.players]

        def complete(seat: int) -> bool:
            """Age the simulated veto by one finished turn.  False = expired."""
            nonlocal pending, rounds_left
            if unlimited:
                # A veto with no expiry is never on its last chance because a
                # turn passed; only a table with no opponent turns left at all
                # could end it, and the walk below decides that.
                return True
            pending.discard(seat)
            if pending:
                return True
            rounds_left -= 1
            if rounds_left <= 0:
                return False
            pending = {index for index in everyone if index != owner}
            return True

        if not complete(mover):
            return True
        for seat in self._upcoming_seats():
            if self.veto_covers(status, seat):
                return False        # another chance is still to come
            if not complete(seat):
                return True
        return True

    def _blockers_for(self, mover: int,
                      deck_id: Optional[str] = None) -> List[int]:
        """Seats that may stop a movement made by ``mover``.

        The DECK is passed through, so a veto restricted to Movement Cards is
        simply not a blocker for a Chest card — it is not asked, no window
        opens for it, and its interface shows nothing.  That is the whole of
        Przerwanie Systemowe's variants 2 and 4.
        """
        return [int(status.subject_id) for status in self.vetoes()
                if self.veto_covers(status, mover, deck_id)]

    def _end_turn(self, depth: int = 0) -> List[ev.GameEvent]:
        """Hand the turn to whoever the cadence says is next, then start it."""
        events_veto = self._note_turn_completed(self.active_player_index)
        upcoming = self.next_turn()
        events: List[ev.GameEvent] = list(events_veto)
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
        events.extend(self._expire_full_round_statuses(upcoming.seat))
        events.extend(self._begin_turn(depth))
        return events

    def _expire_full_round_statuses(self, seat: int) -> List[ev.GameEvent]:
        """End effects that were to last until this seat played again.

        "One full round" for a character ability is "until the turn comes back
        to the player who used it" — Big D Randy's freeze lasts from his turn
        to his next turn, which is what the brief means and what the round
        counter cannot express (a round is a variable number of turns here).

        Checked when the seat BEGINS a turn, not when it ends one, so the
        effect is already gone by the time that player acts.  ``granted_turn``
        keeps the activation's own turn from satisfying it immediately.
        """
        events: List[ev.GameEvent] = []
        for status in list(self.statuses.all()):
            owner = status.data.get(effects.FULL_ROUND_KEY)
            if owner is None or int(owner) != int(seat):
                continue
            if self.turn_counter <= int(status.data.get("granted_turn", -1)):
                continue
            self.statuses.discard(status)
            events.append(ev.StatusEnded(
                status.kind.value, status.subject.value, status.subject_id,
                STATUS_LABELS.get(status.kind, status.kind.value)))
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
        # AN ELIMINATED SEAT IS SKIPPED FIRST, and skipped for ever.  Before
        # SKIP_TURN, because a one-off skip must not be SPENT on a turn that
        # was never going to happen; before the interrupt, because a card
        # cannot hijack the turn of somebody who has none.  The seat stays in
        # the order and this runs every time it comes round, so a character
        # holding several slots loses all of them.
        events = self._resolve_eliminated(player)
        if events is None:
            events = self._resolve_skip_turn(player)
        if events is None:
            events = self._resolve_turn_interrupt(player)
        if events is None:
            return []
        # An eliminated seat is not dealt back up to a hand size it will never
        # play from: that would drain the movement deck one card per skipped
        # turn for the rest of the match.  Everybody else refills as usual,
        # because a skipped turn is still a turn spent.
        if not player.eliminated:
            events.extend(self._refill_movement_hand(player))
        events.extend(self._end_turn(depth + 1))
        return events

    def _resolve_eliminated(self, player: Player) -> Optional[List[ev.GameEvent]]:
        """Hand straight on past a player who is out of the game."""
        if not player.eliminated:
            return None
        return [ev.TurnSkipped(player.index, "odpadł z gry")]

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
        """Both factions have settled: unpause, then let the queue out.

        ORDER MATTERS.  ``ModSelectionFinished`` first, so the selection
        overlay is gone before anything it queued appears; the follow-up
        windows after, so they arrive into a screen that has room for them.
        They are rebuilt from the RACK rather than from remembered events —
        a mod that was chosen and then immediately replaced is no longer in
        play and owes nothing.
        """
        selection = self.pending_mod_selection
        if selection is None:
            return []
        self.pending_mod_selection = None
        events: List[ev.GameEvent] = [ev.ModSelectionFinished(selection.round_number)]
        rack = {card.uid: card for card in self.active_mods}
        for uid in selection.followup_uids:
            card = rack.get(uid)
            if card is not None:
                events.extend(self._mod_followup_events(card))
        return events

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

    #: Passives whose arrival opens a SECOND interactive window.  A mod that
    #: only changes the board (Shady) or the statuses (Sesja na PG variant 2)
    #: is not one of these: it interrupts nobody, so it lands at once.
    _MOD_FOLLOWUP_PASSIVES = ("reveal_chest",)

    def _arm_mod(self, card: Card) -> List[ev.GameEvent]:
        """Run whatever a mod does at the moment it reaches the rack.

        Keyed on the card's declared ``passive``, never on its title (N98), so
        a second mod that reveals the Chest or hides the leader is a JSON entry
        exactly as a passive rule is.

        A mod that opens a WINDOW is different from one that simply changes the
        state, and the difference is timing.  Piotrek settling his half of a
        selection while four hunters are still voting must not put a window in
        front of all five of them; the arrival is recorded on the selection and
        replayed by :meth:`_finish_mod_selection`.  Outside a selection —
        PlaceMod, Thunderfuck, Rage Quit — there is nothing to wait for and the
        window opens immediately, exactly as it always did.
        """
        events: List[ev.GameEvent] = []
        if card.passive.get("hide_leader"):
            events.extend(self._hide_leading_pawn(card.uid))
        if card.passive.get("cancel_ability_effects"):
            events.extend(self._cancel_ability_effects())

        if not any(card.passive.get(rule) for rule in self._MOD_FOLLOWUP_PASSIVES):
            return events
        selection = self.pending_mod_selection
        if selection is not None:
            if card.uid not in selection.followup_uids:
                selection.followup_uids.append(card.uid)
            return events
        events.extend(self._mod_followup_events(card))
        return events

    def _mod_followup_events(self, card: Card) -> List[ev.GameEvent]:
        """The secondary window a mod opens once it is allowed to open it."""
        if card.passive.get("reveal_chest"):
            return [self._chest_reveal_event()]
        return []

    # ── Sesja na PG, variant 2: what is already running stops ────────────────
    def _cancel_ability_effects(self) -> List[ev.GameEvent]:
        """End every effect that a character ability put into play.

        THE SELECTION IS BY ORIGIN, NEVER BY KIND.  A frozen pawn may be Big D
        Randy's Granny Costume or it may be something a Chest card did, and the
        two look identical from the outside; ``Status.origin`` is stamped where
        the effect was resolved and is the only thing that tells them apart.
        Clearing by kind — or clearing everything — would cancel Mods, Chest
        promises and ordinary movement statuses along with the abilities, which
        is a different card from the one the brief describes.

        NOTHING IS REMEMBERED.  A cancelled effect does not come back when the
        mod leaves: it was cancelled, not suspended, and the departure half of
        :meth:`_sync_mod_states` is deliberately silent about this rule for
        that reason.  The ability lock is a passive and lifts by itself.
        """
        events: List[ev.GameEvent] = []
        for status in self.statuses.cancel_origin("ability"):
            events.append(ev.StatusEnded(
                status.kind.value, status.subject.value, status.subject_id,
                STATUS_LABELS.get(status.kind, status.kind.value),
            ))
        # A frozen pawn that has just been unfrozen is a pawn whose picture on
        # the board is now wrong, and the board reads the statuses rather than
        # being told, so there is nothing else to do here.
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
        # A pawn that is no longer on the board cannot go on being frozen in
        # place (brief §11).  The freeze ends NOW rather than being suspended:
        # when the pawn comes back a round later it comes back free, and the
        # blue field highlight — which is drawn from the status — goes with it
        # rather than being left behind on a field the pawn has left.
        events = self._thaw_dragged(pawn_id)
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
        return events + [ev.PawnHidden(pawn_id=pawn_id, riders=riders,
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
        if player.eliminated:
            # A player who guessed wrong is an observer.  Their remaining
            # charges are still printed on the card and still shown, and none
            # of them may ever be spent again.
            return [ev.ActionRejected(
                "Odpadłeś z gry — nie możesz używać umiejętności", command.kind)]
        # THE GLOBAL GATE.  Sesja na PG and the pawns-on-START rule, asked as
        # one question so that no ability can be added that forgets either.
        # Refused BEFORE anything resolves, so no charge is spent and nothing
        # is animated.  In the engine rather than in the interface for the
        # usual reason — a client that simply does not grey the button must
        # still be unable to act.
        blocked = self.ability_refusal()
        if blocked is not None:
            return [ev.ActionRejected(blocked, command.kind)]

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
        """Move a pawn (and everything travelling with it) along its route.

        THE GROUP IS PUT DOWN IN THE ORDER IT WAS PICKED UP.  ``carried`` is
        ordered bottom-to-top over the board the move started from, and the
        mover's own place in that order is not always the bottom — a pawn
        linked to it by Radar may have been standing UNDER it.  Sorting the
        whole group before placing is what keeps a linked pair the right way up
        after a move made from either end of it; placing the mover first and
        the riders on top would silently invert the pair.

        For an ordinary tower the mover IS the bottom, so this is exactly the
        old behaviour with the order written down instead of assumed.
        """
        tiles = list(op.tiles)
        if not tiles:
            return []
        destination = tiles[-1]
        waypoints: List[Tuple[float, float]] = []
        for index in tiles:
            tile = self.board.tile(index)
            if tile is not None:
                waypoints.append(tile.position)

        group = [op.pawn_id, *op.carried]
        group.sort(key=lambda pawn_id: effects.stack_order_key(self, pawn_id))

        # DŁUG U TOMASZA, asked once, here.  Every movement in the game lands
        # through this operation, so a pair that may not neighbour is protected
        # from cards nobody has written yet — no effect needs to know the ban
        # exists.  The move is CANCELLED rather than trimmed: shortening it
        # would invent a distance the card never had, and the brief asks for
        # the offending movement to be stopped, not rewritten.  Other pawns'
        # movements are untouched, because this operation only ever describes
        # one group.
        landing = self.board.tile(destination)
        if landing is not None:
            would_be = {pawn_id: landing.slot for pawn_id in group}
            clash = effects.separation_blocks(self, would_be)
            if clash is not None:
                first, second = clash
                return [ev.MoveFizzled(
                    f"Dług u Tomasza: {self.library.pawn(first).name} i "
                    f"{self.library.pawn(second).name} nie mogą sąsiadować",
                    op.pawn_id)]
        for pawn_id in group:
            self.board.place_pawn(pawn_id, destination)
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

    def _op_spend_ability_use(
        self, op: effects.SpendAbilityUse, actor: int
    ) -> List[ev.GameEvent]:
        """Charge a use to the character whose ability Herold borrowed.

        The card the OWNER is holding, not a copy of its definition: the
        exception is a per-match lobby setting, so nothing about the printed
        ability may change.
        """
        player = self.player(int(op.player_index))
        card = getattr(player, "character", None) if player is not None else None
        if card is None or not card.ability_available:
            return []
        card.spend_use()
        title = op.title or (card.skill or card.title)
        return [ev.AbilityUsed(int(op.player_index), title,
                               "użycie zabrane przez Herolda",
                               int(card.uses_left or 0))]

    def _op_grant_extra_play(
        self, op: effects.GrantExtraPlay, actor: int
    ) -> List[ev.GameEvent]:
        """An extra card, dealt now, and a second play owed this turn."""
        player = self.player(int(op.player_index))
        if player is None:
            return []
        events: List[ev.GameEvent] = []
        deck = self.decks[settings.DECK_MOVEMENT]
        if not player.hand_is_full:
            drawn = deck.take_card()
            if drawn is not None:
                player.add_card(drawn)
                events.append(ev.CardDrawn(player.index, deck.id, drawn.uid))
        self._grant_extra_play(int(op.player_index))
        events.append(ev.ExtraTurnGranted(player_index=int(op.player_index),
                                          before_move=True))
        return events

    def _op_grant_extra_turn(
        self, op: effects.GrantExtraTurn, actor: int
    ) -> List[ev.GameEvent]:
        """Give the turn back, WITHOUT rewinding the move that ended it.

        The previous card stays played and discarded and the card drawn at the
        end of that turn stays in hand — it is a second turn, not a second
        chance.  The window closes as it is taken, so the same activation
        cannot also be undone afterwards.
        """
        seat = int(op.player_index)
        if self.player(seat) is None:
            return []
        self.active_player_index = seat
        self.turn_counter += 1
        self._close_turn_window()
        events: List[ev.GameEvent] = [
            ev.ExtraTurnGranted(player_index=seat, before_move=False),
            ev.ActivePlayerChanged(seat),
        ]
        events.extend(self._begin_turn())
        return events

    def _op_restack_tile(
        self, op: effects.RestackTile, actor: int
    ) -> List[ev.GameEvent]:
        """Stand one field's tower up in a new order.

        Defensive about the order it was handed: anything named that is not on
        the field is skipped and anything on the field that was not named is
        appended in its current order, so a stale plan reorders what it can
        instead of dropping a pawn off the board.
        """
        tile = self.board.tile(op.tile_index)
        if tile is None or not tile.stack:
            return []
        present = list(tile.stack)
        ordered = [pawn_id for pawn_id in op.order if pawn_id in present]
        ordered.extend(pawn_id for pawn_id in present if pawn_id not in ordered)
        if ordered == present:
            return []
        tile.stack[:] = ordered
        for pawn_id in ordered:
            self.board.pawn_tiles[pawn_id] = tile.index
        self._sync_token_positions()
        return [ev.TileRestacked(tile_index=tile.index, order=list(ordered))]

    def _op_request_pawn_check(
        self, op: effects.RequestPawnCheck, actor: int
    ) -> List[ev.GameEvent]:
        """Record that a colour is being checked, and say nothing about it.

        This runs on EVERY machine, which is the point: the question is public
        and identical everywhere.  The answer arrives separately, as commands
        from the authority, exactly as Squid Game's does.
        """
        self.pending_pawn_check = (op.pawn_id, int(op.staked_seat))
        return [ev.PawnCheckRequested(pawn_id=op.pawn_id,
                                      staked_seat=int(op.staked_seat))]

    def _op_eliminate_player(
        self, op: effects.EliminatePlayer, actor: int
    ) -> List[ev.GameEvent]:
        return self._eliminate_seat(int(op.player_index), op.reason)

    def _eliminate_seat(self, index: int, reason: str = "") -> List[ev.GameEvent]:
        """Put a seat out of the game, keeping it at the table.

        The seat is NOT removed from ``self.players`` and NOT removed from the
        turn order.  Both are indexed by seat number in the command log, in the
        server's seat map and in every snapshot ever taken, so removing one
        would renumber the others and invalidate the log.  Elimination is a
        property of the player instead, read by ``_begin_turn`` (the turns are
        skipped) and by ``_use_ability`` (the abilities refuse).
        """
        player = self.player(index)
        if player is None or player.eliminated:
            return []
        player.eliminated = True
        return [ev.PlayerEliminated(player_index=index, reason=reason)]

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

    def _op_transfer_stack(
        self, op: effects.TransferStack, actor: int
    ) -> List[ev.GameEvent]:
        """Gejtos: pick a tower up whole and put it down on another field.

        Bottom first, so the tower arrives in the order it left: each
        ``place_pawn`` lands on top of the one before it, and the block ends up
        sitting on whatever was already on the destination.

        The pawns are read BEFORE anything moves.  ``Tile.stack`` is the live
        list, so walking it while placing out of it would skip every other
        pawn.

        A FROZEN PAWN STAYS WHERE IT IS and the rest of the tower goes without
        it.  Asked through the shared ``pawn_may_move`` rather than tested
        here, so this is the freeze rule and not a Gejtos rule — the operation
        does not know which card built it, and should not.  The tower's order
        is preserved among the pawns that do travel.
        """
        source = self.board.tile(op.from_tile)
        destination = self.board.tile(op.to_tile)
        if source is None or destination is None or not source.stack:
            return []
        travelling = [pawn_id for pawn_id in source.stack
                      if effects.pawn_may_move(self, pawn_id)]
        if not travelling:
            return []
        for pawn_id in travelling:
            self.board.place_pawn(pawn_id, op.to_tile, on_top=True)
        self._sync_token_positions()

        events: List[ev.GameEvent] = []
        for pawn_id in travelling:
            events.append(ev.TokenWalked(
                pawn_id=pawn_id,
                from_index=source.slot,
                route=[destination.slot],
                tiles=[op.to_tile],
                waypoints=[destination.position],
                backward=destination.slot < source.slot,
            ))
        events.append(ev.StackTransferred(
            from_tile=op.from_tile, to_tile=op.to_tile, pawns=travelling,
        ))
        return events

    def _op_request_identity_swap(
        self, op: effects.RequestIdentitySwap, actor: int
    ) -> List[ev.GameEvent]:
        """Alter Ego: raise the public flag and stop the table.

        Every replica runs this and reaches the same state, because there is
        nothing here that depends on knowing the colour.  The authority alone
        answers the question this poses, through ``victory.review`` — the same
        hook that decides an elimination, and for the same reason (N72).
        """
        if self.identity_swap:
            return []
        if self.piotrek_seat is None:
            return [ev.ActionRejected("Przy tym stole nie ma Piotrka",
                                      "identity_swap")]
        self.identity_swap = self.SWAP_REVEALING
        return [ev.IdentitySwapStarted(self.piotrek_seat)]

    def _op_swap_piotrek_role(
        self, op: effects.SwapPiotrekRole, actor: int
    ) -> List[ev.GameEvent]:
        """Kingmaker: hand the character cards across the table.

        THE ROLE IS THE CHARACTER CARD AND NOTHING ELSE.  ``Player.role`` is
        derived from ``character.is_piotrek`` (players/roles.py exists so that
        the question is asked once), so moving the two cards is the whole of
        the role swap: ``piotrek_seat``, ``seat_order``, the chest limit, the
        Mod Patusa factions, every ability that says "only Piotrek", the win
        conditions, the right-hand panel and the ability button all read that
        one fact and all move together.  There is deliberately no second
        "who is Piotrek" flag to fall out of step with it.

        WHAT TRAVELS, AND WHY EACH:

        * ``character`` — the role itself, both ways.
        * ``skill`` — the Umiejętność Piotrka belongs to the Piotrek seat, so
          it goes with the role.  The hunter had none, so the outgoing Piotrek
          is left with none, which is exactly what a hunter should have.
          Handing the CARD over rather than dealing a fresh one is the rule the
          project already states: uses are counted on the physical card, and
          "hand it to somebody else and the remaining uses go with it"
          (cards/base_card.py).  The new Piotrek may exchange it through the
          ordinary Umiejętności deck on his panel, like any Piotrek.
        * ``secret_pawn`` — swapped WITHOUT BEING READ.  On a replica this
          moves ``None`` onto ``None``; on the authority and on the outgoing
          Piotrek's own machine it moves the real colour, so that the pause
          raised immediately after still has a colour to give up.  It is given
          up a moment later: ``RevealIdentity`` publishes it and clears it, and
          the new Piotrek chooses his own.  It is never inherited.

        WHAT DOES NOT TRAVEL: ``marks``.  The notepad is a player's own
        working-out, not part of the role, and swapping it would show one
        player another player's private deductions on his own screen — a real
        leak, from a card whose entire job is to move hidden information about
        carefully.  Each seat keeps its own; the outgoing Piotrek's is empty
        because he has had no notepad to write on.

        THE UNDO WINDOW CLOSES.  A checkpoint records hands, piles, charges and
        scalars — it does not record who holds which character card, and a
        rewind that put the cards back but not the roles would leave the table
        quietly wrong.  Kingmaker is therefore not undoable, and says so by
        closing the window the way ``GrantExtraTurn`` already does rather than
        by a new rule somewhere else.
        """
        old_seat = self.piotrek_seat
        challenger = self.player(int(op.hunter_seat))
        piotrek = self.player(old_seat) if old_seat is not None else None
        if piotrek is None:
            return [ev.ActionRejected("Przy tym stole nie ma Piotrka",
                                      "swap_roles")]
        if challenger is None or challenger is piotrek:
            return [ev.ActionRejected("Nie ma z kim zamienić się rolami",
                                      "swap_roles")]

        piotrek.character, challenger.character = \
            challenger.character, piotrek.character
        piotrek.skill, challenger.skill = challenger.skill, piotrek.skill
        # Colourless on every machine that is not entitled to the colour.
        piotrek.secret_pawn, challenger.secret_pawn = \
            challenger.secret_pawn, piotrek.secret_pawn

        self._close_turn_window()
        return [
            ev.RolesSwapped(from_seat=piotrek.index, to_seat=challenger.index),
            ev.CharacterChanged(piotrek.index, piotrek.display_character),
            ev.CharacterChanged(challenger.index, challenger.display_character),
            ev.SkillChanged(piotrek.index,
                            piotrek.skill.title if piotrek.skill else None),
            ev.SkillChanged(challenger.index,
                            challenger.skill.title if challenger.skill else None),
        ]

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

    # ── the card library (stage 32) ──────────────────────────────────────────
    #
    # WHAT "HOW MANY COPIES" MEANS DURING A MATCH, and why it is not the draw
    # pile.  The lobby's ``[-] n [+]`` says how many copies of a title are
    # PRINTED INTO the deck before the shuffle; from the first deal onwards
    # those copies are spread across the draw pile, the discard pile, several
    # hands, the mod rack and possibly an open Mod Patusa selection.  Counting
    # only the draw pile would mean the library disagreed with the lobby the
    # instant anybody drew, so it counts them all: the number the library shows
    # is the number of copies IN THE MATCH, which is exactly the number the
    # lobby configures.
    #
    # Nothing here reads ``config.movement_counts`` and friends.  Those are the
    # settings the match was BUILT from and they say nothing about what has
    # happened since; the cards themselves are the only honest answer, and
    # keeping a second mapping in step with them is the two-sources-of-truth
    # problem the library was explicitly not to create.
    def _deck_bounds(self, deck_id: str) -> Tuple[int, int]:
        """The legal range for one title's copy count, per the lobby's rules."""
        if deck_id == settings.DECK_MODS:
            return (RULES.mod_count_min, RULES.mod_count_max)
        return (RULES.card_count_min, RULES.card_count_max)

    def cards_of_deck(self, deck_id: str) -> List[Card]:
        """Every physical card of ``deck_id`` currently in the match.

        Piles, hands, the mod rack and the cards an open selection is holding.
        A card is asked which deck it came from rather than being looked for in
        one place, because a Mod Patusa in the rack and a Chest card in a hand
        are both still copies their deck no longer has.
        """
        found: List[Card] = []
        deck = self.decks.get(deck_id)
        if deck is not None:
            found.extend(deck.draw_pile)
            found.extend(deck.discard_pile)
        for player in self.players:
            found.extend(card for card in player.hand if card.deck_id == deck_id)
            for held in (player.character, player.skill):
                if held is not None and held.deck_id == deck_id:
                    found.append(held)
        found.extend(card for card in self.mod_slots
                     if card is not None and card.deck_id == deck_id)
        selection = self.pending_mod_selection
        if selection is not None:
            found.extend(card for card in selection.piotrek_cards
                         if card.deck_id == deck_id)
            found.extend(card for card in selection.hunter_cards
                         if card.deck_id == deck_id)
        return found

    def deck_card_count(self, deck_id: str, title: str) -> int:
        """How many copies of ``title`` this deck has in the match."""
        return sum(1 for card in self.cards_of_deck(deck_id)
                   if card.title == title)

    def deck_composition(self, deck_id: str) -> Dict[str, int]:
        """Title → copies in the match, for every title the deck defines.

        In the DEFINITION's order, which is the order the lobby lists and the
        order the library shows: a shuffle is a permutation of a list and the
        printed order is the one stable thing about it.
        """
        counts: Dict[str, int] = {}
        try:
            definition = self.library.deck(deck_id)
        except Exception:
            return counts
        for card in definition.cards:
            counts.setdefault(card.title, 0)
        for card in self.cards_of_deck(deck_id):
            if card.title in counts:
                counts[card.title] += 1
        return counts

    def _next_card_uid(self, deck_id: str) -> int:
        """A uid for a card added mid-match, agreed on by every machine.

        ``setup.build_decks`` numbers a deck from ``(ordinal + 1) * 10_000``,
        so one past the highest uid the deck currently holds is both free and
        DETERMINISTIC — every replica applies the same commands to the same
        cards and therefore computes the same number.  A uid from the global
        counter would be a different number on every machine, and a command
        naming a card by uid would then name a different card on each.
        """
        used = [card.uid for card in self.cards_of_deck(deck_id)]
        order = list(self.library.deck_order)
        base = ((order.index(deck_id) + 1) * 10_000
                if deck_id in order else 10_000)
        return max([base - 1] + used) + 1

    def _definition_of(self, deck_id: str, title: str) -> Optional[CardDef]:
        try:
            definition = self.library.deck(deck_id)
        except Exception:
            return None
        return next((card for card in definition.cards if card.title == title),
                    None)

    def _adjust_deck_count(self, command: cmd.AdjustDeckCount) -> List[ev.GameEvent]:
        """Add or remove one printed copy of a title while the match runs.

        THE SAFETY RULE, and it is the whole of the active-game question the
        brief asks about: a copy is added to the DRAW pile and removed from the
        DRAW pile first, the DISCARD pile second, and from NOWHERE ELSE.  A
        hand, the mod rack and an open selection are cards that are out on the
        table in front of somebody; taking one back would delete a card a
        player is holding — or worse, one they are about to play — so when
        every remaining copy is out there the command is refused and says so.
        Nothing is duplicated either: an added copy is a NEW card with a fresh
        deterministic uid, so no two cards ever share one.
        """
        if command.deck_id not in settings.TABLE_DECKS:
            return [ev.ActionRejected("Tej talii nie można zmieniać",
                                      command.kind)]
        deck = self.decks.get(command.deck_id)
        # THE MATCH'S reading of the card, not the printed one.  A copy added
        # here has to be the same card as the copies already in the pile: one
        # arriving on the printed variant while the rest play another is
        # exactly the "two physical copies became two different cards" that
        # the variant system exists to prevent.
        definition = self.variant_definition(command.deck_id, command.title)
        if deck is None or definition is None:
            return [ev.ActionRejected("Nieznana karta", command.kind)]
        delta = 1 if int(command.delta) > 0 else -1
        low, high = self._deck_bounds(command.deck_id)
        current = self.deck_card_count(command.deck_id, command.title)
        wanted = current + delta
        if wanted > high:
            return [ev.ActionRejected(
                f"Najwyżej {high} kopii karty „{command.title}”", command.kind)]
        if wanted < low:
            return [ev.ActionRejected(
                f"Najmniej {low} kopii karty „{command.title}”", command.kind)]

        if delta > 0:
            card = Card(definition, uid=self._next_card_uid(command.deck_id))
            # Somewhere in the middle rather than on top: a card conjured onto
            # the top of the pile is the next card somebody draws, which is a
            # way to hand a chosen card to the next player.  ``deck.rng`` is
            # seeded from the session seed and has been advanced by exactly the
            # same shuffles on every machine, so the position is agreed on
            # without being predictable.
            position = deck.rng.randrange(len(deck.draw_pile) + 1)
            deck.draw_pile.insert(position, card)
            where = "draw"
        else:
            where = self._remove_one_copy(deck, command.title)
            if where is None:
                return [ev.ActionRejected(
                    f"Wszystkie kopie karty „{command.title}” są w grze",
                    command.kind)]
        return [ev.DeckCountChanged(
            deck_id=command.deck_id, title=command.title,
            count=self.deck_card_count(command.deck_id, command.title),
            delta=delta, where=where,
        )]

    @staticmethod
    def _remove_one_copy(deck: Deck, title: str) -> Optional[str]:
        """Take one copy of ``title`` out of a pile.  Never out of a hand."""
        for pile, name in ((deck.draw_pile, "draw"),
                           (deck.discard_pile, "discard")):
            for index in range(len(pile) - 1, -1, -1):
                if pile[index].title == title:
                    del pile[index]
                    return name
        return None

    def _set_card_variant(self, command: cmd.SetCardVariant) -> List[ev.GameEvent]:
        """Play one card under a different variant, from now on, for everybody.

        Three things happen, in this order, and the order is the rule:

        1. the match's configuration moves — this is the record, and the one
           thing the snapshot carries;
        2. every physical copy already in the match is re-read under the new
           variant, so the two copies of ``Sesja na PG`` stay ONE logical card
           rather than becoming two different ones the moment somebody clicks;
        3. if that turned a mod already sitting in the rack into a cancelling
           one, the cancellation fires — because becoming active and being made
           active are the same transition to everything downstream, and the
           brief is explicit that a card already in play must not have to be
           played again.

        Step 3 is asked as "did the RACK's answer change", not "was this card
        the one edited": the rule belongs to the rack (``mod_rule``), so a
        second copy arriving later, a Rage Quit, or an edit to a card nobody
        has in play all give the same honest answer.
        """
        definition = self._definition_of(command.deck_id, command.title)
        if definition is None or not definition.has_variants:
            return [ev.ActionRejected("Ta karta nie ma wariantów", command.kind)]
        wanted = str(command.variant)
        if wanted not in definition.variant_ids:
            return [ev.ActionRejected(
                f"Karta „{command.title}” nie ma wariantu {wanted!r}",
                command.kind)]
        key = (command.deck_id, command.title)
        if self.card_variants.get(key) == wanted:
            return [ev.ActionRejected(
                f"„{command.title}” już gra w tym wariancie", command.kind)]

        was_cancelling = self.cancels_ability_effects
        self.card_variants[key] = wanted
        chosen = definition.with_variant(wanted)
        self._reread_copies(command.deck_id, command.title, chosen)

        events: List[ev.GameEvent] = []
        cancelled = 0
        if self.cancels_ability_effects and not was_cancelling:
            cancelled_events = self._cancel_ability_effects()
            cancelled = len(cancelled_events)
            events.extend(cancelled_events)
        variant = chosen.variant_def(wanted)
        events.append(ev.CardVariantChanged(
            deck_id=command.deck_id, title=command.title, variant=wanted,
            label=variant.label if variant else "", text=chosen.text,
            cancelled=cancelled,
        ))
        return events

    def _reread_copies(self, deck_id: str, title: str,
                       definition: CardDef) -> int:
        """Point every copy of a title at a new reading of its definition.

        The DEFINITION is what a variant changes, and a definition is shared by
        every copy — so this is how "the same logical card" survives a variant
        change with two physical copies in the rack, a third in a hand and a
        fourth in the discard pile.  The uid, the remaining uses and the card's
        position are untouched: it is the same card, read differently.

        A TRANSFORMED card (Gamechanger) is followed on both sides, so a card
        that is currently something else still comes back to the right reading
        of what it was printed as.
        """
        changed = 0
        for card in self.cards_of_deck(deck_id):
            if card.definition.title == title:
                card.definition = definition
                changed += 1
            if (card.original_definition is not None
                    and card.original_definition.title == title):
                card.original_definition = definition
                changed += 1
        return changed

    # ── abilities: the default and what is left of it ────────────────────────
    #
    # ``Card.uses_left`` is the runtime counter and ``CardDef.uses`` is the
    # configured default — already the lobby's number, because
    # ``DeckDef.with_uses`` rewrote the definition before the deck was built.
    # So "restore" needs no memory of its own: it copies one onto the other.
    def ability_card(self, title: str) -> Optional[Card]:
        """The one physical copy of an ability card, wherever it currently is.

        Dealt character cards and Piotrek's skill live on their player; the
        rest are still in their deck.  Searched in a fixed order — seats, then
        decks — so every replica finds the same card if content ever ships two
        copies of one title.
        """
        for player in self.players:
            for held in (player.character, player.skill):
                if held is not None and held.title == title:
                    return held
        for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS):
            deck = self.decks.get(deck_id)
            if deck is None:
                continue
            for card in list(deck.draw_pile) + list(deck.discard_pile):
                if card.title == title:
                    return card
        return None

    def ability_default_uses(self, title: str) -> Optional[int]:
        """The configured default for an ability, or ``None`` if it has none."""
        card = self.ability_card(title)
        return None if card is None else card.uses_total

    def _ability_for_command(self, command) -> Tuple[Optional[Card], Optional[ev.GameEvent]]:
        card = self.ability_card(command.title)
        if card is None:
            return None, ev.ActionRejected("Nieznana umiejętność", command.kind)
        if card.ability is None or card.uses_total is None:
            return None, ev.ActionRejected(
                "Ta karta nie ma umiejętności z ładunkami", command.kind)
        return card, None

    def _adjust_ability_uses(
        self, command: cmd.AdjustAbilityUses
    ) -> List[ev.GameEvent]:
        """Move an ability's REMAINING uses by one, in either direction.

        Floor of zero and no ceiling.  The ceiling is left off on purpose: the
        table is allowed to hand an ability more charges than it was printed
        with, and the default it would be restored to does not move when they
        do.
        """
        card, refusal = self._ability_for_command(command)
        if card is None:
            return [refusal]      # type: ignore[list-item]
        delta = 1 if int(command.delta) > 0 else -1
        current = card.uses_left if card.uses_left is not None else 0
        wanted = current + delta
        if wanted < 0:
            return [ev.ActionRejected("Zostało już zero użyć", command.kind)]
        card.uses_left = wanted
        return [ev.AbilityUsesChanged(title=card.title, uses_left=wanted,
                                      default=int(card.uses_total or 0))]

    def _restore_ability_uses(
        self, command: cmd.RestoreAbilityUses
    ) -> List[ev.GameEvent]:
        """Remaining uses := the configured default.  The default itself moves not at all."""
        card, refusal = self._ability_for_command(command)
        if card is None:
            return [refusal]      # type: ignore[list-item]
        default = int(card.uses_total or 0)
        if card.uses_left == default:
            return [ev.ActionRejected(
                f"„{card.skill or card.title}” ma już {default} użyć",
                command.kind)]
        card.uses_left = default
        return [ev.AbilityUsesChanged(title=card.title, uses_left=default,
                                      default=default, restored=True)]

    # ── handlers: board ──────────────────────────────────────────────────────
    def _reset_board(self, command: cmd.ResetBoard) -> List[ev.GameEvent]:
        """Put every pawn back in its camp and clear what the board held.

        THE BOARD, NOT THE MATCH.  Hands, decks, the round, the turn and a
        declared verdict are deliberately untouched — a reset that also dealt
        new hands would be a restart, and the lobby already owns restarting.

        What goes with the placements:

        * the TOWERS, because a stack is board state and there is nothing left
          to stack on;
        * every status that names a PAWN — a freeze, an Ondrej link, a
          forbidden adjacency — because each of them describes a relationship
          between pawns and fields that has just stopped existing.  Statuses on
          a PLAYER stay: a queued turn interrupt or a movement bonus is about
          the person, not about the board;
        * every decision waiting on a TOWER (``pending_check``,
          ``pending_breakup``, ``pending_lead_check``, ``pending_pawn_check``),
          because answering one afterwards would check a tower that is not
          there.  ``pending_movement`` goes with them: it is an offer to block
          a movement whose destination has just been swept away.

        The camp slot is taken from the pawn's index in the content library,
        the same way ``__init__`` and ``_restore_hidden_pawn`` take it, so a
        reset lands the pawns exactly where the match started them.
        """
        placed = len(self.board.pawn_tiles)
        for tile in self.board.tiles:
            tile.stack.clear()
        self.board.pawn_tiles.clear()
        for slot, pawn in enumerate(self.library.pawns):
            token = self.tokens.get(pawn.id)
            if token is None:
                continue
            token.tile_index = None
            token.held = False
            token.position = self.board.camp_position(slot)

        cleared = 0
        for status in self.statuses.all():
            if status.subject is Subject.PAWN:
                self.statuses.discard(status)
                cleared += 1

        self.pending_check = None
        self.pending_breakup = None
        self.pending_lead_check = None
        self.pending_pawn_check = None
        self.pending_movement = None
        self._resolved_movement = None

        # NOT ``_sync_token_positions``: it speaks for the pawns that are ON
        # the board, and there are none left — the camp slots above are the
        # answer for every one of them.
        self._release_check_lock()
        return [ev.BoardReset(pawns=placed, statuses_cleared=cleared)]

    def _pick_up_token(self, command: cmd.PickUpToken) -> List[ev.GameEvent]:
        token = self.tokens.get(command.pawn_id)
        if token is None:
            return [ev.ActionRejected("Nieznany pionek", command.kind)]
        token.held = True
        return [ev.TokenPickedUp(token.id)]

    @staticmethod
    def _requested_tile_index(value: Any) -> Optional[int]:
        """A field index out of a message, or ``None`` if it is not one.

        A command arrives as JSON from a machine this one does not control, so
        ``tile_index`` may be anything at all.  ``board.tile`` compares it with
        ``0 <=`` and raises on a string — which ``apply`` turns into a refusal,
        but only after the handler has already run.  Asked here instead, where
        the answer can still be acted on cheaply.
        """
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _requested_point(x: Any, y: Any) -> Optional[Point]:
        """A world position out of a message, or ``None`` if it is not one."""
        try:
            return (float(x), float(y))
        except (TypeError, ValueError):
            return None

    def _move_token(self, command: cmd.MoveToken) -> List[ev.GameEvent]:
        """Put a pawn on a field, or anywhere at all.  The hand-editing tool.

        EVERY REFUSAL IS DECIDED BEFORE ANYTHING MOVES, and that ordering is
        the rule rather than a tidiness preference.  A refused command is
        neither logged nor broadcast (``Room.submit``), so a handler that
        changes the table and only then refuses puts the authority into a state
        its own command log cannot reproduce — every client replays the log,
        lands somewhere else, and is told so permanently.  See "A REFUSED
        COMMAND CHANGES NOTHING" in LLM_Instructions.txt.
        """
        token = self.tokens.get(command.pawn_id)
        if token is None:
            return [ev.ActionRejected("Nieznany pionek", command.kind)]

        # ── everything that can say no, asked first ──────────────────────────
        tile: Optional[Tile] = None
        free: Optional[Point] = None
        if command.tile_index is not None:
            index = self._requested_tile_index(command.tile_index)
            tile = self.board.tile(index) if index is not None else None
            if tile is None:
                return [ev.ActionRejected("Nieznane pole", command.kind)]
        else:
            # Free placement — the prototype's drag-anywhere behaviour.
            free = self._requested_point(command.x, command.y)
            if free is None:
                return [ev.ActionRejected("Nieznane miejsce", command.kind)]

        # ── from here on nothing can refuse, so the table may change ─────────
        origin = token.position
        # Pawns riding on top travel with the one below — the tower rule.
        carried = self.board.carried_pawns(token.id)
        token.held = False
        # DRAGGING A PAWN BY HAND ENDS ITS FREEZE, on purpose (brief §7).  This
        # is the testing tool, not a move: it is the one path in the game that
        # is not a card, an ability or a rule, so it is the one path that is
        # allowed to overrule one.  Everything else in the game asks
        # ``effects.pawn_may_move`` and is refused.
        thaw = self._thaw_dragged(token.id, carried)

        if tile is not None:
            self.board.place_pawn(token.id, tile.index)
            for rider in carried:
                self.board.place_pawn(rider, tile.index)
            self._sync_token_positions()
            token.tile_index = tile.index
            return thaw + [
                ev.TokenMoved(
                    token.id, origin, token.position, tile.index, list(carried), snapped=True
                )
            ]

        self.board.remove_pawn(token.id)
        token.tile_index = None
        token.position = free                      # type: ignore[assignment]
        self._sync_token_positions()
        return thaw + [
            ev.TokenMoved(token.id, origin, token.position, None, [], snapped=False)
        ]

    def _thaw_dragged(self, pawn_id: str,
                      carried: Sequence[str] = ()) -> List[ev.GameEvent]:
        """Drop the temporary effects on a pawn that is being moved by hand.

        The testing tool is not a move, and it is the one path in the game that
        is allowed to overrule a rule.  Granny Costume's freeze goes, and so
        does an Ondrej link — a pair whose halves have just been put on
        different fields by hand is not a pair any of the movement rules could
        honour, and leaving the status behind would mean the next card dragged
        one of them back to the other.

        The riders matter for the freeze: the tower rule carries them, so they
        are being moved too, and a pawn that has just been physically moved
        while still marked frozen is a board whose blue highlight is a lie.
        """
        events: List[ev.GameEvent] = []
        for moved in [pawn_id, *carried]:
            status = self.statuses.find(StatusKind.FROZEN, Subject.PAWN, moved)
            if status is None:
                continue
            self.statuses.discard(status)
            events.append(ev.StatusEnded(
                status.kind.value, status.subject.value, status.subject_id,
                STATUS_LABELS.get(status.kind, status.kind.value)))
        # The link is a status about a PAIR, not about one pawn, so it is
        # looked up by membership rather than by subject id.  Dług u Tomasza's
        # separation ban is the same shape and goes the same way: both are
        # promises about where two pawns stand relative to each other, and
        # hand-placing one of them is exactly the move that can make the
        # promise impossible to keep.
        for moved in [pawn_id, *carried]:
            for status in [effects.link_status(self, moved),
                           self._adjacency_ban(moved)]:
                if status is None or status not in self.statuses.all():
                    continue
                self.statuses.discard(status)
                events.append(ev.StatusEnded(
                    status.kind.value, status.subject.value, status.subject_id,
                    STATUS_LABELS.get(status.kind, status.kind.value)))
        return events

    def _adjacency_ban(self, pawn_id: str) -> Optional[Status]:
        """The Dług u Tomasza status covering this pawn, or ``None``."""
        for status in self.statuses.of_kind(StatusKind.FORBIDDEN_ADJACENCY):
            if pawn_id in [str(m) for m in status.data.get("members", [])]:
                return status
        return None

    def _release_check_lock(self) -> None:
        """Drop the post-refusal lock once the pawns are no longer gathered.

        Read from the board rather than remembered: any effect at all that
        breaks the tower — a card, an ability, a manual drag — separates the
        pawns, and none of them should have to know Ice Block exists.
        """
        if not self.check_needs_separation:
            return
        from . import victory as _victory
        if _victory.gathering_tile(self) is None:
            self.check_needs_separation = False

    def _sync_token_positions(self) -> None:
        """Re-read every stacked pawn's position from the board."""
        self._release_check_lock()
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
        if self.awaiting_identity:
            # Alter Ego stops the table exactly the way the opening does, and
            # through the same gate rather than a second one: for the length of
            # the swap there is no hidden colour AT ALL, so a tower checked now
            # would be checked against nobody.
            return "Piotrek wybiera nową tożsamość"
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

    #: The only commands that mean anything while a movement is waiting to be
    #: answered.  Everything else is refused — the table is genuinely stopped,
    #: and a card played into the pause would resolve against a board that is
    #: about to change (or about to not change).
    _DECISION_COMMANDS = (cmd.AcceptMovement, cmd.BlockMovement,
                          cmd.ExpireMovementDecision)

    def _movement_decision_refusal(self, command: cmd.Command) -> Optional[str]:
        """Why nothing else may happen while a movement is being answered."""
        if self.pending_movement is None:
            return None
        if isinstance(command, self._DECISION_COMMANDS):
            return None
        if isinstance(command, (self._TURN_BOUND, cmd.PlayCard, cmd.PlaceMod)):
            return "Trwa decyzja o zablokowaniu ruchu"
        return None

    #: What an eliminated player MAY still do.  They are an observer, not a
    #: ghost: they keep their name, and a movement decision put to the table is
    #: put to them as well.  Everything else is acting, and they are out.
    _ALLOWED_WHEN_ELIMINATED = (cmd.RenamePlayer, cmd.AcceptMovement,
                                cmd.BlockMovement, cmd.ToggleMark)

    def _reject_eliminated(self, command: cmd.Command) -> Optional[ev.GameEvent]:
        """Why a player who is out of the game may not do this."""
        if isinstance(command, self._ALLOWED_WHEN_ELIMINATED):
            return None
        player = self.player(int(getattr(command, "player_index", -1)))
        if player is None or not player.eliminated:
            return None
        return ev.ActionRejected("Odpadłeś z gry — możesz tylko obserwować",
                                 command.kind)

    def _authorise(self, command: cmd.Command) -> Optional[ev.GameEvent]:
        """Is this machine allowed to issue this command right now?"""
        # NO ``EDITOR_ONLY`` GATE HERE, deliberately.  This is the path a
        # machine takes for its OWN commands, and a single-machine game has
        # nobody to take an advantage from — the tester fetching a card is the
        # whole point of the tool.  The gate belongs on ``authorise_remote``,
        # where a command arrives from somebody else's process.
        if not isinstance(command, cmd.AUTHORITY_ONLY):
            problem = self._phase_refusal()
            if problem is not None:
                return ev.ActionRejected(problem, command.kind)
            paused = self._mod_selection_refusal(command)
            if paused is not None:
                return ev.ActionRejected(paused, command.kind)
        held = self._movement_decision_refusal(command)
        if held is not None:
            return ev.ActionRejected(held, command.kind)
        if isinstance(command, self._OWNED_BY_PLAYER):
            refusal = self._reject_foreign(
                getattr(command, "player_index", 0), command.kind
            )
            if refusal is not None:
                return refusal
            # AN ELIMINATED SEAT MAY NOT ACT AT ALL.  In the authorisation
            # layer rather than in ``_use_ability`` alone, because the brief
            # says they cannot move EITHER — and because their turns are
            # skipped, the only way such a command arrives is stale or
            # malicious, which is exactly what this layer is for.  Renaming
            # yourself and answering a movement are left alone: they are not
            # acting, and an observer keeps their name and their vote.
            out = self._reject_eliminated(command)
            if out is not None:
                return out
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
        if isinstance(command, cmd.EDITOR_ONLY) and not self.edit_mode:
            # EDITING IS A PROPERTY OF THE TABLE, not of the client asking.
            # The host fixes it in the lobby before the match and it travels in
            # SessionConfig with everything else, so every machine agrees about
            # it and no client can turn it on for itself.
            return "Tryb edycji jest wyłączony przy tym stole"
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
        # A seat handed the turn directly in edit mode has still had the turn
        # come round to it, so an effect that was to last until it played again
        # is over.  Called here as well as in ``_end_turn`` because these are
        # the only two ways the active seat ever changes, and a rule added to
        # one and not the other drifts.
        events.extend(
            self._expire_full_round_statuses(self.active_player_index))
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
        if (self.pending_pawn_check is not None
                and self.pending_pawn_check[0] == command.pawn_id):
            self.pending_pawn_check = None
        self._arm_tower_breakup(command.pawn_id)
        if command.pawn_id in self.eliminated_pawns:
            # Not an error worth showing anybody: a colour is checked once, and
            # arriving here twice means a duplicate delivery, not a rules bug.
            return []
        self.eliminated_pawns.append(command.pawn_id)
        return [ev.PawnEliminated(command.pawn_id)]

    def _eliminate_player(self, command: cmd.EliminatePlayer) -> List[ev.GameEvent]:
        """A seat is out.  Clears the check that knocked them out, if any.

        THE GAME DOES NOT END HERE.  A hunter dropping out is not a hunter
        defeat and not a Piotrek victory: the remaining players carry on and
        the ordinary victory conditions decide the match, exactly as they would
        have. ``victory.review`` is not consulted about it and has nothing to
        say about it.
        """
        player = self.player(command.player_index)
        if player is None:
            return [ev.ActionRejected("Nieznany gracz", command.kind)]
        if (self.pending_pawn_check is not None
                and self.pending_pawn_check[1] == command.player_index):
            self.pending_pawn_check = None
        events = self._eliminate_seat(int(command.player_index), command.reason)
        if not events:
            return []
        # An eliminated player may be the one whose turn it is — the wrong
        # guess is made on their own turn — so the turn has to move on, or the
        # table waits for somebody who can no longer act.
        if self.active_player_index == command.player_index:
            events.extend(self._end_turn())
        return events

    def _arm_tower_breakup(self, checked: str) -> None:
        """A check was ATTEMPTED on a tower — under variant 2 it comes apart.

        THE TRIGGER IS THE ATTEMPT, NOT THE ANSWER.  Called from two places,
        and the difference between them is the whole of this feature's trickiest
        rule: from ``_eliminate_pawn`` when a check resolved and the colour was
        crossed off, and from ``_refuse_check`` when Ice Block cancelled it.
        Ice Block stops the CHECK — no identity is compared and no colour is
        ruled out — but it does not stop the tower being pulled apart by the
        attempt.

        It used to be armed only from the elimination, so a refusal produced no
        elimination and therefore no breakup.  That read "no check, no breakup",
        which is one reading of the rule and not the one the game wants.

        A successful check is not routed here at all: finding Piotrek ends the
        match, and there is nothing left to scatter.
        """
        if self.check_variant != "break_tower":
            return
        from . import victory as _victory

        tile = _victory.gathering_tile(self)
        if tile is None or (checked and checked not in tile.stack):
            # Not a tower check — Squid Game's automatic one can cross a colour
            # off while the pawns are scattered, and there is nothing to break.
            return
        groups = effects.tower_breakup_plan(self, tile)
        if not groups:
            return
        # A doubled row among the destinations is Piotrek's to choose a field
        # on.  Only a group that actually MOVES gets the question: the bottom
        # group stays on the exact tile it was already standing on, so there is
        # nothing to decide about it even when its row happens to be doubled.
        choice_position = next(
            (position for position, _ in groups[1:]
             if position != tile.slot
             and len(self.board.tiles_at_position(position)) > 1),
            None,
        )
        self.pending_breakup = PendingTowerBreakup(
            tile_index=tile.index, groups=groups,
            seat=self.piotrek_seat if self.piotrek_seat is not None else -1,
            choice_position=choice_position,
            seconds=float(RULES.tower_breakup_seconds),
        )

    def _choose_breakup_tile(
        self, command: cmd.ChooseBreakupTile
    ) -> List[ev.GameEvent]:
        """Piotrek picks 2a or 2b.  ONLY Piotrek."""
        pending = self.pending_breakup
        if pending is None or pending.choice_position is None:
            return [ev.ActionRejected("Nie ma czego wybierać", command.kind)]
        if int(command.player_index) != pending.seat:
            # NOT the player whose card built the tower.  The brief is explicit
            # and so is this: the scattering belongs to Piotrek.
            return [ev.ActionRejected("Tylko Piotrek wybiera pole",
                                      command.kind)]
        allowed = [tile.index for tile
                   in self.board.tiles_at_position(pending.choice_position)]
        if int(command.tile_index) not in allowed:
            return [ev.ActionRejected("To pole nie należy do tego rzędu",
                                      command.kind)]
        pending.chosen_tile = int(command.tile_index)
        return [ev.BreakupTileChosen(tile_index=int(command.tile_index),
                                     seat=pending.seat)]

    def _resolve_tower_breakup(
        self, command: cmd.ResolveTowerBreakup
    ) -> List[ev.GameEvent]:
        """Scatter the tower.  Each group keeps its internal order."""
        pending = self.pending_breakup
        self.pending_breakup = None
        if pending is None:
            return []
        events: List[ev.GameEvent] = []
        for index, (position, pawns) in enumerate(pending.groups):
            tiles = self.board.tiles_at_position(position)
            if not tiles:
                continue
            target = tiles[0]
            if index == 0:
                # The bottom group has not moved, so it keeps the EXACT tile it
                # was standing on — a tower on 4b must not shuffle across to 4a
                # just because the group was re-placed.
                target = next((t for t in tiles
                               if t.index == pending.tile_index), tiles[0])
            elif position == pending.choice_position:
                chosen = next((t for t in tiles
                               if t.index == pending.chosen_tile), None)
                # No answer before the deadline: the first field, chosen the
                # same way on every machine, rather than a hung table.
                target = chosen if chosen is not None else tiles[0]
            for pawn_id in pawns:          # bottom first, so the order survives
                self.board.place_pawn(pawn_id, target.index)
            events.append(ev.TowerGroupPlaced(
                tile_index=target.index, position=position, pawns=list(pawns)))
        self._sync_token_positions()
        events.append(ev.TowerBrokeUp(tile_index=pending.tile_index))
        return events

    def _open_check_decision(
        self, command: cmd.OpenCheckDecision
    ) -> List[ev.GameEvent]:
        """Pause a check and ask Piotrek.  Nothing about it is resolved yet."""
        if self.pending_check is not None:
            return []
        self.pending_check = PendingCheckDecision(
            source=str(command.source), pawn_id=str(command.pawn_id),
            seat=int(command.seat), seconds=self.check_decision_seconds,
        )
        return [ev.CheckDecisionOpened(
            pawn_id=str(command.pawn_id), seat=int(command.seat),
            source=str(command.source), seconds=self.check_decision_seconds,
        )]

    def _allow_check(self, command: cmd.AllowCheck) -> List[ev.GameEvent]:
        decision = self.pending_check
        if decision is None:
            return [ev.ActionRejected("Nie ma sprawdzenia do rozpatrzenia",
                                      command.kind)]
        if int(command.player_index) != decision.seat:
            return [ev.ActionRejected("Tylko Piotrek może o tym zdecydować",
                                      command.kind)]
        return self._close_check_decision(timed_out=False)

    def _expire_check_decision(
        self, command: cmd.ExpireCheckDecision
    ) -> List[ev.GameEvent]:
        if self.pending_check is None:
            return []
        return self._close_check_decision(timed_out=True)

    def _close_check_decision(self, timed_out: bool) -> List[ev.GameEvent]:
        """Let the check happen.  ICE BLOCK IS NOT SPENT either way.

        Saying yes and saying nothing have the same cost — none — which is the
        brief's rule and also the only one that makes a timeout safe: a player
        who loses their connection must not lose a charge for it.
        """
        decision = self.pending_check
        if decision is None:
            return []
        self.pending_check = None
        # Remembered so ``review`` resolves the check instead of asking again
        # the moment it looks at the same unchanged tower.
        self.check_allowed = decision.pawn_id
        return [ev.CheckAllowed(pawn_id=decision.pawn_id, seat=decision.seat,
                                timed_out=timed_out)]

    def _refuse_check(self, command: cmd.RefuseCheck) -> List[ev.GameEvent]:
        """Cancel the check and spend one Ice Block use.

        THE CHECK IS NOT ANSWERED, so there is nothing to reveal: no colour is
        crossed off, no identity is compared, and under checking variant 2 no
        tower breaks — the breakup is a consequence of a FAILED check, and this
        check never happened.
        """
        decision = self.pending_check
        if decision is None:
            return [ev.ActionRejected("Nie ma sprawdzenia do rozpatrzenia",
                                      command.kind)]
        if int(command.player_index) != decision.seat:
            return [ev.ActionRejected("Tylko Piotrek może odmówić sprawdzenia",
                                      command.kind)]
        from . import victory as _victory
        card = _victory.ice_block_card(self)
        if card is None:
            return [ev.ActionRejected("Ice Block został już zużyty",
                                      command.kind)]

        card.spend_use()
        self.pending_check = None
        self.check_allowed = None
        # The card's own text: the pawns have to be separated before another
        # check.  Without this the identical tower would be re-checked on the
        # next command and the refusal would have bought nothing.
        self.check_needs_separation = True
        # ICE BLOCK STOPS THE CHECK, NOT THE CONSEQUENCE OF ATTEMPTING ONE.
        # Under variant 2 the tower still comes apart, on the same delay: what
        # Piotrek bought is that nobody learned anything, not that the tower
        # stayed standing.  Armed here rather than inside the elimination
        # handler because a refusal deliberately eliminates nothing.
        self._arm_tower_breakup(decision.pawn_id)
        return [ev.CheckRefused(pawn_id=decision.pawn_id, seat=decision.seat,
                                uses_left=int(card.uses_left))]

    # ── the turn window: undo, and Liskowy Konkurs ───────────────────────────
    def _open_turn_window(self, seat: int, card_uid: int) -> None:
        """Photograph the table, and close the previous player's window.

        ONE WINDOW EXISTS AT A TIME and it belongs to the player who last
        played a card.  The next card played is what closes it — which is
        exactly the rule both features are written against, so they cannot
        drift apart: the undo button and Liskowy Konkurs are offered and
        withdrawn by the same fact.

        Piotrek's own extra play under Liskowy Konkurs does NOT open a second
        window; the checkpoint from the first card is kept, because the whole
        turn is his and rewinding half of it would be a state nobody played.
        """
        if self.turn_window is not None and self.turn_window.seat == seat \
                and self.extra_plays.get(seat, 0) > 0:
            return
        self.turn_window = undo.capture(self, seat, card_uid)

    def _close_turn_window(self) -> None:
        self.turn_window = None

    def can_undo(self, seat: int) -> bool:
        """Whether this seat may rewind its last turn right now.

        NOT WHILE THE TABLE IS WAITING FOR A COLOUR.  ``UndoMove`` is exempt
        from ``_phase_refusal`` (it is authority-only, and the gate runs before
        that exemption), so without this an Alter Ego or Kingmaker pause could
        be rewound from underneath: the checkpoint restores ``identity_swap``
        and the hands, but a secret that has already been given up is not in it
        and neither is who holds which character card.  The window is still
        there when the swap finishes; it is only closed to the middle of one.
        """
        window = self.turn_window
        return (window is not None and not window.spent
                and window.seat == int(seat)
                and self.phase.playable
                and not self.awaiting_identity)

    def _undo_move(self, command: cmd.UndoMove) -> List[ev.GameEvent]:
        """Rewind the last played card as though it had never been played.

        Everything the card touched goes back: pawns, towers, the card itself,
        the hand, both piles, the statuses it granted, the turn it ended and
        the charges it spent.  The card DRAWN at the end of that turn goes back
        to the top of the draw pile as a consequence of restoring the pile's
        order, not as a separate step — which is also why the corrective turn
        draws that same card again.
        """
        seat = int(command.player_index)
        if not self.can_undo(seat):
            # Stale or forged: the window has closed, or it was never this
            # player's.  The engine refuses; the button being hidden is a
            # convenience, not the rule.
            return [ev.ActionRejected("Nie można już cofnąć ruchu", command.kind)]
        window = self.turn_window
        undo.restore(self, window)
        self.turn_window = None
        self.extra_plays.pop(seat, None)
        return [ev.MoveUndone(player_index=seat, card_uid=window.card_uid)]

    def extra_play_pending(self, seat: int) -> bool:
        """Whether this seat still owes a second card play this turn."""
        return int(self.extra_plays.get(int(seat), 0)) > 0

    def _grant_extra_play(self, seat: int) -> None:
        self.extra_plays[int(seat)] = int(self.extra_plays.get(int(seat), 0)) + 1

    def _reveal_identity(self, command: cmd.RevealIdentity) -> List[ev.GameEvent]:
        """Alter Ego: publish the old colour and wipe the notepad down to it.

        THE OLD CROSSINGS GO.  They were evidence about an identity that no
        longer exists — and the brief's own example turns on it: Piotrek moves
        to a colour the hunters had already ruled out, which is only possible
        because the ruling out is void. What survives is the colour he just
        left, which the hunters now know for certain.

        The secret itself is cleared here rather than overwritten later, so
        that between this command and the next one NO machine believes it knows
        who Piotrek is — including the authority.  ``victory.review`` reads
        exactly that and declines to judge, which is what stops a check landing
        in the middle of the swap.
        """
        if self.identity_swap != self.SWAP_REVEALING:
            return []
        if self.library.pawn(command.pawn_id) is None:
            return [ev.ActionRejected("Nieznany kolor", command.kind)]
        cleared = [p for p in self.eliminated_pawns if p != command.pawn_id]
        self.eliminated_pawns = [command.pawn_id]
        # An automatic check aimed at the old identity is void with it.
        self.pending_lead_check = None
        seat = self.piotrek_seat
        player = self.player(seat) if seat is not None else None
        if player is not None:
            player.secret_pawn = None
        self.pending_pawn_check = None
        self.identity_swap = self.SWAP_CHOOSING
        return [ev.IdentityRevealed(command.pawn_id, cleared)]

    def _finish_identity_swap(
        self, command: cmd.FinishIdentitySwap
    ) -> List[ev.GameEvent]:
        """Piotrek has a new colour; everybody leaves the pause together.

        A command rather than a local flag flip for the ordinary reason: the
        authority knows the answer arrived and nobody else does, so the resume
        has to be told rather than guessed.
        """
        if self.identity_swap != self.SWAP_CHOOSING:
            return []
        self.identity_swap = ""
        seat = self.piotrek_seat
        return [ev.IdentitySwapFinished(seat if seat is not None else -1)]

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
        self.pending_pawn_check = None
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
                # Real state, not a UI detail: two machines that disagree about
                # which mods still owe a window disagree about what happens the
                # moment the pause lifts.
                "followups": list(self.pending_mod_selection.followup_uids),
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
            # Public and colourless.  The table is stopped, so every machine
            # must agree that it is — but nothing here says what it is stopped
            # waiting for a decision ABOUT.
            "identity_swap": self.identity_swap,
            # WHICH SEAT HOLDS THE ROLE.  Public — the turn order announces
            # Piotrek every round — and in the fingerprint since Kingmaker made
            # it something a match can CHANGE.  Nothing else here would notice:
            # ``piotrek_name`` is the character title and the titles in play are
            # the same after a swap as before it, ``has_character`` is true on
            # both seats either way, and ``ability_uses`` is keyed by seat.  Two
            # machines disagreeing about who Piotrek is would therefore have
            # agreed about everything in this dictionary while disagreeing about
            # the turn order, the win condition and every "only Piotrek" rule.
            # A seat number, never a colour.
            "piotrek_seat": self.piotrek_seat,
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
            # The check a player ASKED for, and the seat staking itself on it.
            # In the fingerprint for the same reason the automatic one is: two
            # machines that disagree about whether a check is outstanding
            # disagree about whether somebody is about to be knocked out, and
            # nothing else here would notice.  A colour and a seat number —
            # both public the moment the ability resolved.
            "pending_pawn_check": (None if self.pending_pawn_check is None
                                   else list(self.pending_pawn_check)),
            "ability_uses": {
                p.index: [
                    card.uses_left
                    for card in (p.character, p.skill) if card is not None
                ]
                for p in self.players
            },
            # THE LIBRARY'S TWO NUMBERS, in the fingerprint because the library
            # can change both mid-match and neither shows up anywhere else.
            # Pile SIZES above would not notice two machines adding a copy of
            # different titles, and ``ability_uses`` above only covers the
            # cards that have been dealt — the library can top up a character
            # nobody is playing.  Titles and counts only: a count is not a
            # hand, and nothing here says who holds what.
            "deck_composition": {
                deck_id: sorted(self.deck_composition(deck_id).items())
                for deck_id in settings.TABLE_DECKS
            },
            # THE CHOSEN VARIANTS.  In the fingerprint because a table playing
            # Sesja na PG's second variant and one playing its first are
            # playing different rules, and nothing else in this dictionary
            # would notice: the card counts, the pile sizes and the uids are
            # identical either way.  Titles and ids only.
            # The paused movement.  Two machines that disagree about whether
            # the table is waiting for an answer disagree about whose turn it
            # is; the CLOCK is not here, only the length of the window (see
            # PendingMovementDecision).
            "pending_movement": (None if self.pending_movement is None
                                 else self.pending_movement.to_dict()),
            # Ice Block.  Two machines that disagree about whether the table is
            # waiting on Piotrek disagree about whether a check has happened.
            "pending_check": (None if self.pending_check is None
                              else self.pending_check.to_dict()),
            "check_allowed": self.check_allowed,
            "check_needs_separation": self.check_needs_separation,
            "pending_breakup": (None if self.pending_breakup is None
                                else self.pending_breakup.to_dict()),
            "card_variants": sorted(
                (deck_id, title, variant)
                for (deck_id, title), variant in self.card_variants.items()
            ),
            "ability_charges": sorted(
                (card.title, card.uses_left)
                for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS)
                for card in self.cards_of_deck(deck_id)
                if card.ability is not None
            ),
        }

    _OPERATIONS = {
        effects.MovePawn: _op_move_pawn,
        effects.MoveBySteps: _op_move_by_steps,
        effects.MoveAndCollect: _op_move_and_collect,
        effects.TransferStack: _op_transfer_stack,
        effects.RequestIdentitySwap: _op_request_identity_swap,
        effects.SwapPiotrekRole: _op_swap_piotrek_role,
        effects.ReplaceMods: _op_replace_mods,
        effects.DrawCards: _op_draw_cards,
        effects.TransferCard: _op_transfer_card,
        effects.HighlightHeldCard: _op_highlight_card,
        effects.ForcedPlay: _op_forced_play,
        effects.TurnLost: _op_turn_lost,
        effects.SpendAbilityUse: _op_spend_ability_use,
        effects.GrantExtraPlay: _op_grant_extra_play,
        effects.GrantExtraTurn: _op_grant_extra_turn,
        effects.RestackTile: _op_restack_tile,
        effects.RequestPawnCheck: _op_request_pawn_check,
        effects.EliminatePlayer: _op_eliminate_player,
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
        cmd.DrawTitledCard: _draw_titled_card,
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
        cmd.AdjustDeckCount: _adjust_deck_count,
        cmd.AdjustAbilityUses: _adjust_ability_uses,
        cmd.RestoreAbilityUses: _restore_ability_uses,
        cmd.SetCardVariant: _set_card_variant,
        cmd.AcceptMovement: _accept_movement,
        cmd.BlockMovement: _block_movement,
        cmd.ExpireMovementDecision: _expire_movement_decision,
        cmd.ResetBoard: _reset_board,
        cmd.PickUpToken: _pick_up_token,
        cmd.MoveToken: _move_token,
        cmd.SetRound: _set_round,
        cmd.SetActivePlayer: _set_active_player,
        cmd.RenamePlayer: _rename_player,
        cmd.ToggleMark: _toggle_mark,
        cmd.BeginMatch: _begin_match,
        cmd.EliminatePawn: _eliminate_pawn,
        cmd.EliminatePlayer: _eliminate_player,
        cmd.UndoMove: _undo_move,
        cmd.ChooseBreakupTile: _choose_breakup_tile,
        cmd.ResolveTowerBreakup: _resolve_tower_breakup,
        cmd.OpenCheckDecision: _open_check_decision,
        cmd.AllowCheck: _allow_check,
        cmd.RefuseCheck: _refuse_check,
        cmd.ExpireCheckDecision: _expire_check_decision,
        cmd.RevealIdentity: _reveal_identity,
        cmd.FinishIdentitySwap: _finish_identity_swap,
        cmd.DeclareVictory: _declare_victory,
    }
