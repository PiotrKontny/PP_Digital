"""
The effect engine.

Everything a card or an ability does goes through here:

    card / ability  →  EffectSpec (JSON)  →  handler  →  Plan of Operations
                                                              ↓
                                          GameState executes, emits events
                                                              ↓
                                                     the view animates

A handler is a **pure function** of the state: it never mutates anything, it
returns a description of what should happen.  Two things fall out of that:

* the interface can call :func:`resolve` while a card is being dragged to show
  exactly what the play would do, using the same code that will carry it out;
* the executor is the only place that changes the game, so every mutation is
  already in one funnel for the network layer.

Adding a new effect is a JSON entry plus one ``@effect("name")`` function.
There is no chain of ``if card.title == ...`` anywhere in the project, and
there must never be one: card titles are content, not code.

A handler returns one of:

* :class:`Plan`        — operations ready to execute;
* :class:`Choice`      — legal, but a decision is missing (which pawn? which
  half of a doubled field?).  The interface asks and resubmits the same action
  with the answer in ``choices``;
* :class:`Refusal`     — cannot be done, with a reason fit for the status bar;
* :class:`NotAvailable` — the rules this needs do not exist yet (checking).
  Distinct from a refusal so the interface can say so honestly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..cards.base_card import Card, EffectSpec
from .statuses import Status, StatusKind, Subject

# A pawn that has not left the camp is "behind" position 1.
CAMP_INDEX = -1


# ── operations: the vocabulary an effect can express ─────────────────────────
@dataclass(frozen=True)
class Operation:
    """Base class.  Operations are data; the executor knows how to apply them."""


@dataclass(frozen=True)
class MovePawn(Operation):
    """Walk a pawn along a route of board positions.

    ``route`` holds *position* indices (a doubled position such as 12a/12b
    counts once); ``tiles`` holds the concrete field visited at each of them.
    ``teleport`` marks a jump that should glide rather than walk field by field.
    """

    pawn_id: str
    from_index: int
    route: Tuple[int, ...]
    tiles: Tuple[int, ...] = ()
    carried: Tuple[str, ...] = ()
    teleport: bool = False


@dataclass(frozen=True)
class GrantStatus(Operation):
    """Attach a status.

    ``stack`` decides what happens when the subject already has one of the same
    kind.  The default replaces it, which is right for almost everything —
    using an ability twice should refresh a freeze, not queue a second one.
    Turn interrupts are the exception: drawing a second Troll is a second
    hijacked turn, and silently dropping it would make the card unreliable in
    exactly the situation it is most likely to come up.
    """

    status: Status
    stack: bool = False


@dataclass(frozen=True)
class ClearStatus(Operation):
    kind: StatusKind
    subject: Subject = Subject.TABLE
    subject_id: str = ""


@dataclass(frozen=True)
class SpendStatus(Operation):
    """Consume one charge of a status (a movement bonus is used up by a card)."""

    kind: StatusKind
    subject: Subject = Subject.TABLE
    subject_id: str = ""


@dataclass(frozen=True)
class PlayRandomCard(Operation):
    """Reveal a random playable card from a deck and carry out its effect.

    The draw needs the game's seeded RNG, so unlike everything else here it is
    resolved by the executor rather than by the handler — a preview must not
    consume randomness, or dragging a card over the board would change what it
    does.
    """

    deck_id: str
    announce_seconds: float = 2.0


@dataclass(frozen=True)
class DrawIntoMods(Operation):
    """Draw a card and put it straight into the active mod rack.

    Like :class:`PlayRandomCard`, the draw needs the game's deck state, so the
    executor does it — a handler must stay pure.
    """

    deck_id: str = "mods"


@dataclass(frozen=True)
class MoveBySteps(Operation):
    """Move a pawn a number of positions, worked out AT EXECUTION TIME.

    :class:`MovePawn` carries a finished route, which is right for a card that
    moves one pawn: the handler sees the board the move will happen on.  It is
    wrong for a card that moves several pawns in a chosen order, because the
    second pawn departs from a board the first one has already changed — it may
    have been carried along in a tower, or had a tower land on top of it.

    So the route is recomputed by the executor, against the board as it is when
    this operation's turn comes.  The one thing that cannot wait is the 12a/12b
    question: nobody can be asked halfway through applying a plan, so the
    handler works out the destination *position* (which is pure) and asks in
    advance.  ``preview_tiles`` is the handler's projection, for the highlight
    only — the executor never reads it.
    """

    pawn_id: str
    steps: int
    chosen_tile: Optional[int] = None
    preview_tiles: Tuple[int, ...] = ()
    #: Whether the pawns riding on this one travel with it.  True is the tower
    #: rule and the default.  Balbinka moves EVERY pawn its own two fields, so
    #: it turns this off: a rider carried along AND moved in its own right would
    #: travel four, and the card says two.
    carry_riders: bool = True
    #: Let the executor pick the half of a widened destination at random rather
    #: than asking (Balbinka: "no player input").  It has to be the executor
    #: because randomness may never be consumed by a handler (N78) — the
    #: interface resolves handlers to draw previews while a card is dragged.
    random_branch: bool = False


@dataclass(frozen=True)
class MoveAndCollect(Operation):
    """Walk a pawn, sweeping up one pawn from every field it passes through.

    Dzieckorolka.  :class:`MovePawn` cannot express it, and not only because it
    moves other pawns: the ORDER of the resulting tower is the rule.  The pawns
    picked up hang below the mover in the order they were met, so the finished
    stack reads as a record of the journey — first collected nearest the top.

    ``collected`` is in TRAVEL ORDER.  The executor reverses it on the way into
    the stack, because a stack is stored bottom-first and the travel order is
    read downwards from the mover.
    """

    pawn_id: str
    from_index: int
    route: Tuple[int, ...]
    tiles: Tuple[int, ...] = ()
    carried: Tuple[str, ...] = ()
    #: Pawns swept up along the way, in the order the mover met them.
    collected: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplaceMods(Operation):
    """Swap every occupied Mod Patusa slot for a fresh draw (Rage Quit).

    Thunderfuck's neighbour, and deliberately NOT Thunderfuck's operation: that
    one PUSHES a single card in from the left and lets the rack shift, which is
    right for "draw a new mod" and wrong for "replace the ones in play".  This
    writes each occupied slot in place, the way the faction selection does, so
    Piotrek's slot stays Piotrek's and the hunters' stays theirs.

    An EMPTY slot is left empty.  The card exchanges what is ACTIVE, and
    seeding the rack with a mod nobody chose is exactly what N86 exists to
    prevent.
    """

    deck_id: str = "mods"


@dataclass(frozen=True)
class TransferStack(Operation):
    """Move a whole field's tower to another field, intact and in order.

    Gejtos.  NOT a movement operation and deliberately not built out of one:
    the pawns are not walking a route, they are being picked up as a block and
    put down somewhere else, so there is no distance, no direction, no widened
    row to settle on the way and nothing for a Mod Patusa to shorten.

    The tower keeps its order and lands ON TOP of whatever is already standing
    on the destination, which is the ordinary stacking rule — the arriving
    block is simply several pawns deep.
    """

    from_tile: int
    to_tile: int


@dataclass(frozen=True)
class RequestIdentitySwap(Operation):
    """Alter Ego: stop the table and hand the identity question back to Piotrek.

    Carries NO COLOUR, and that is the whole design.  This operation runs on
    every replica, and every replica but the authority's (and Piotrek's own)
    has never been told which colour is his — so an operation that named it
    would produce a different plan on different machines, which is precisely
    what N72 exists to prevent.

    All this does is raise a public flag.  The reveal itself comes back from
    the authority as an ordinary logged command, exactly the way an elimination
    does.
    """


@dataclass(frozen=True)
class DrawCards(Operation):
    """Draw cards into a hand, through the ordinary draw path.

    Ordinary matters: a card drawn this way still announces itself, still
    triggers its own ``on_draw``, and still trips the chest limit.  Troll draws
    a replacement so the player keeps a playable hand, and if that replacement
    is another Troll the second one queues up behind the first by itself.
    """

    player_index: int
    deck_id: str = "movement"
    count: int = 1


@dataclass(frozen=True)
class TransferCard(Operation):
    """Move one card from one hand to another (Spy).

    Deliberately silent about what the card IS: the event this produces names
    the seats and the uid, never the title, because it is broadcast to the
    whole table and only the thief is allowed to have seen the hand.
    """

    from_player: int
    to_player: int
    card_uid: int


@dataclass(frozen=True)
class HighlightHeldCard(Operation):
    """Point at a card in a hand for a few seconds before anything happens.

    Presentation with a mechanical guarantee attached: the player is about to
    lose control of their turn, so they are shown exactly which card did it.
    The STATE does not wait for the animation (see N36) — the view holds the
    card up and delays the walk that follows it.
    """

    player_index: int
    card_uid: int
    seconds: float = 2.0
    caption: str = ""


@dataclass(frozen=True)
class ForcedPlay(Operation):
    """Choose a card from a hand and play it for the player (Troll).

    The choice needs the seeded RNG, so — like :class:`PlayRandomCard` — the
    executor makes it rather than the handler: a preview must never consume
    randomness or dragging a card about would change what it does.

    ``priority_decks`` is tried first and ``fallback_decks`` after it, which is
    the whole of Troll's rule ("a Chest card if you have one, otherwise a
    Movement card") expressed as data.
    """

    player_index: int
    source_uid: int = 0
    priority_decks: Tuple[str, ...] = ()
    fallback_decks: Tuple[str, ...] = ()
    seconds: float = 2.5
    caption: str = ""


@dataclass(frozen=True)
class TurnLost(Operation):
    """Report that a seat is not getting a move out of this turn.

    Only a report: the turn is already being consumed by the interrupt
    machinery.  It exists so that "you were skipped" and "the game played a
    card for you" are distinguishable to the interface, which has to say
    different things about them.
    """

    player_index: int
    source: str = ""


@dataclass(frozen=True)
class Announce(Operation):
    """Say something happened that has no other mechanical trace."""

    text: str


@dataclass(frozen=True)
class Fizzle(Operation):
    """The card resolved, and its movement did nothing.

    Not a :class:`Refusal`: a refusal means the play was ILLEGAL and the card
    stays in the hand, and Halloween's rule is the opposite — the card is
    played and discarded like any other, it simply moves nobody.  An empty
    :class:`Plan` cannot express that either, because ``Plan.ok`` is
    ``bool(operations)`` and an empty one reads as a refusal to every caller.

    So this follows the idiom Thunderfuck already established for an empty
    rack: emit a real operation whose executor does nothing but say so.  The
    reason reaches the status bar, because a card that silently did nothing
    looks like a bug to the player.
    """

    reason: str
    pawn_id: str = ""


# ── results ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Plan:
    """A resolved, executable effect."""

    operations: Tuple[Operation, ...] = ()
    description: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.operations)

    @property
    def routes(self) -> Tuple[MovePawn, ...]:
        return tuple(op for op in self.operations if isinstance(op, MovePawn))


@dataclass(frozen=True)
class ChoiceOption:
    """One thing the player may pick."""

    id: str
    label: str
    pawn: Optional[str] = None
    tile: Optional[int] = None
    #: The card being offered, when the question is "which of these cards?".
    card_uid: Optional[int] = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Choice:
    """A decision the engine needs before it can act.

    ``key`` is where the answer goes in the action's ``choices`` dictionary, so
    an effect that needs three decisions simply asks three times.
    """

    key: str
    kind: str                       # "pawn" | "tile" | "option" | "card"
    prompt: str
    options: Tuple[ChoiceOption, ...]
    description: str = ""
    #: How many things must be picked.  One is the ordinary case; more than one
    #: turns the prompt into a multi-select with a confirm button, and the
    #: answer comes back as the ids joined by commas.
    count: int = 1
    #: Whether the ORDER of a multi-select matters.  Plagiat! moves the pawns in
    #: the order they were picked, so the interface numbers them.
    ordered: bool = False
    #: Whose cards are on offer, for a "card" question.  The interface needs it
    #: to find them; nobody else is ever sent this question (see N40).
    owner: Optional[int] = None

    @property
    def ok(self) -> bool:
        return False

    @property
    def reason(self) -> str:
        return self.prompt

    @property
    def labels(self) -> Tuple[str, ...]:
        return tuple(option.label for option in self.options)

    @property
    def tiles(self) -> Tuple[int, ...]:
        return tuple(o.tile for o in self.options if o.tile is not None)

    @property
    def pawns(self) -> Tuple[str, ...]:
        return tuple(o.pawn for o in self.options if o.pawn is not None)


@dataclass(frozen=True)
class Refusal:
    """Why an effect cannot be carried out right now."""

    reason: str

    @property
    def ok(self) -> bool:
        return False


@dataclass(frozen=True)
class NotAvailable:
    """The mechanic this effect needs has not been built yet."""

    reason: str

    @property
    def ok(self) -> bool:
        return False


Resolution = object  # Plan | Choice | Refusal | NotAvailable


class EffectError(ValueError):
    """Raised only for malformed data, never for a merely illegal play."""


# ── context and registry ─────────────────────────────────────────────────────
@dataclass
class EffectContext:
    """Everything a handler is allowed to look at."""

    state: Any
    actor: int = 0
    choices: Mapping[str, str] = field(default_factory=dict)
    source: str = ""
    #: The card this effect came from, when there is one.  A card that acts on
    #: the way into a hand has to be able to refer to itself — Troll's status
    #: has to know which physical Troll started it.
    card_uid: Optional[int] = None
    #: Where the effect came from: ``"card"``, ``"ability"`` or ``"on_draw"``.
    #: The Mody Patusa need it — Masa solna and Halloween change what MOVEMENT
    #: CARDS do and must leave character abilities alone, and Dziad's ability is
    #: an ordinary ``move_pawn`` that would otherwise be caught by both.
    origin: str = "card"
    #: Which deck the card came from, so a rule can say "movement cards" and
    #: mean it.  Chest cards have no movement effect today; when one gets one,
    #: it must not silently inherit the movement deck's restrictions.
    deck_id: str = ""
    #: False when nobody can be asked a question — a card revealed and played
    #: by another card (Seks z pedałami, Troll's forced play).  A handler that
    #: would open a prompt must fall back to something legal instead, or the
    #: card fizzles: those call sites pick their card from
    #: ``resolves_without_asking``, which is a property of the printed card and
    #: cannot know that a mod has just made it ask.
    can_ask: bool = True

    def choice(self, key: str) -> Optional[str]:
        value = self.choices.get(key)
        return str(value) if value is not None else None

    @property
    def from_movement_card(self) -> bool:
        """True when this is a movement card being played, not an ability."""
        from ..config import settings

        return self.origin == "card" and self.deck_id == settings.DECK_MOVEMENT

    @property
    def board(self):
        return self.state.board

    @property
    def statuses(self):
        return self.state.statuses


Handler = Callable[[EffectSpec, EffectContext], Resolution]
HANDLERS: Dict[str, Handler] = {}


def effect(name: str) -> Callable[[Handler], Handler]:
    """Register a handler for an effect ``type`` from the JSON."""

    def decorator(function: Handler) -> Handler:
        if name in HANDLERS:
            raise EffectError(f"Efekt {name!r} jest już zarejestrowany")
        HANDLERS[name] = function
        return function

    return decorator


def resolve_spec(
    state, spec: Optional[EffectSpec], actor: int = 0,
    choices: Optional[Mapping[str, str]] = None, source: str = "",
    card_uid: Optional[int] = None, origin: str = "card",
    deck_id: str = "", can_ask: bool = True,
) -> Resolution:
    """Resolve any effect specification against the current state."""
    if spec is None:
        return Refusal("Ta karta nie ma jeszcze zaimplementowanego efektu")
    handler = HANDLERS.get(spec.type)
    if handler is None:
        return Refusal(f"Nieznany typ efektu: {spec.type}")
    return handler(
        spec,
        EffectContext(state, actor, dict(choices or {}), source, card_uid,
                      origin=origin, deck_id=deck_id, can_ask=can_ask),
    )


def resolve(
    state, card: Card, actor: int = 0,
    choices: Optional[Mapping[str, str]] = None,
    can_ask: bool = True,
) -> Resolution:
    """Resolve a card's effect.

    ``can_ask=False`` is for a card nobody chose to play and nobody can be
    asked about — see :attr:`EffectContext.can_ask`.
    """
    return resolve_spec(state, card.effect, actor, choices, card.title,
                        card.uid, origin="card", deck_id=card.deck_id,
                        can_ask=can_ask)


def resolve_on_draw(state, card: Card, actor: int = 0) -> Resolution:
    """What a card does the moment it lands in a hand.

    Separate entry point, same registry: ``_after_draw`` used to know about
    Gamechanger by name, and adding Troll and Stańczyk that way would have been
    the second and third branches of exactly the chain this module exists to
    prevent.  A card that acts on the way in declares ``on_draw`` and is done.
    """
    if card.on_draw is None:
        return Refusal("Ta karta nic nie robi przy dobraniu")
    return resolve_spec(state, card.on_draw, actor, None, card.title, card.uid,
                        origin="on_draw", deck_id=card.deck_id)


def resolve_ability(
    state, card: Card, actor: int = 0,
    choices: Optional[Mapping[str, str]] = None,
) -> Resolution:
    """Resolve a character card's or skill's ability."""
    if card.ability is None:
        return Refusal("Ta postać nie ma aktywnej umiejętności")
    if not card.ability_available:
        return Refusal(f"Umiejętność „{card.skill or card.title}” została już zużyta")
    return resolve_spec(state, card.ability, actor, choices,
                        card.skill or card.title, origin="ability",
                        deck_id=card.deck_id)


def preview(
    state, card: Card, actor: int = 0,
    choices: Optional[Mapping[str, str]] = None,
) -> Resolution:
    """What a card would do, without doing it.

    Named separately from :func:`resolve` because that is how the interface
    uses it; they are the same function, which is the point — the highlight and
    the outcome cannot disagree.
    """
    return resolve(state, card, actor, choices)


def preview_tiles(state, result: Resolution) -> Tuple[int, ...]:
    """Fields the board should highlight for a resolution, whatever its kind."""
    if isinstance(result, Plan):
        tiles: List[int] = []
        for op in result.operations:
            if isinstance(op, (MovePawn, MoveAndCollect)):
                tiles.extend(op.tiles)
            elif isinstance(op, MoveBySteps):
                tiles.extend(op.preview_tiles)
        return tuple(tiles)
    if isinstance(result, Choice) and result.kind == "tile":
        return result.tiles
    return ()


# ── shared helpers ───────────────────────────────────────────────────────────
def pawn_index(state, pawn_id: str) -> int:
    """Board *position* of a pawn, or :data:`CAMP_INDEX` if it has not started."""
    index = state.board.position_of_pawn(pawn_id)
    return CAMP_INDEX if index is None else int(index)


def is_hidden(state, pawn_id: str) -> bool:
    """Whether Shady has taken this pawn off the map.

    A hidden pawn is ignored for movement, for targeting and for the neighbour
    test.  Everything that walks the pawn list goes through here or through
    :func:`live_pawns`, so 'ignore it' is one decision rather than six.
    """
    checker = getattr(state, "pawn_is_hidden", None)
    return bool(checker(pawn_id)) if checker is not None else False


def live_pawns(state) -> List:
    """Pawns that are on the table — the palette minus anything hidden."""
    return [pawn for pawn in state.library.pawns if not is_hidden(state, pawn.id)]


def _ordered_pawns(state) -> List[Tuple[int, int, int, str]]:
    """Every pawn as (position, stack depth, palette order, id), sorted.

    Hidden pawns are left out entirely, which is what makes ``hindmost`` and
    ``foremost`` ignore a pawn Shady has removed without either of them having
    to know Shady exists.
    """
    out: List[Tuple[int, int, int, str]] = []
    for order, pawn in enumerate(state.library.pawns):
        if is_hidden(state, pawn.id):
            continue
        index = pawn_index(state, pawn.id)
        depth = state.board.stack_depth(pawn.id) if index != CAMP_INDEX else 0
        out.append((index, depth, order, pawn.id))
    out.sort()
    return out


def hindmost_pawn(state) -> Optional[str]:
    """The pawn furthest from the finish.

    Ties are broken deterministically — lower in the tower first, then the
    palette order — because host and clients replay the same commands and must
    agree without exchanging anything.
    """
    ordered = _ordered_pawns(state)
    return ordered[0][3] if ordered else None


def foremost_pawn(state) -> Optional[str]:
    ordered = _ordered_pawns(state)
    return ordered[-1][3] if ordered else None


def piotrek_player(state) -> Optional[int]:
    for player in state.players:
        if player.is_piotrek:
            return player.index
    return None


def route_between(state, start: int, steps: int) -> Tuple[int, ...]:
    """Board positions walked when moving ``steps`` from ``start``.

    Forward from the camp enters the board, so a pawn in camp moving one step
    lands on position 1.  Movement clamps at both ends: the finish cannot be
    overshot, and a pawn can never be pushed back off the board into the camp.
    """
    last = state.board.last_position
    if last < 0 or steps == 0:
        return ()
    if steps > 0:
        first = 0 if start == CAMP_INDEX else start + 1
        target = min(last, (start + steps) if start != CAMP_INDEX else steps - 1)
        if target < first:
            return ()
        return tuple(range(first, target + 1))
    if start == CAMP_INDEX:
        return ()
    target = max(0, start + steps)
    if target >= start:
        return ()
    return tuple(range(start - 1, target - 1, -1))


def tile_route(state, pawn_id: str, route: Sequence[int],
               chosen_tile: Optional[int] = None) -> Tuple[int, ...]:
    """Pick the concrete field the pawn passes through at each position.

    On a doubled position the pawn takes the nearer half — walking a tower
    through the far side of a widened stretch would look like a detour.  The
    *destination* is the exception: that one is the player's decision.
    """
    board = state.board
    current = board.pawn_tile(pawn_id)
    previous = current.position if current is not None else board.camp_position(0)

    tiles: List[int] = []
    for step, position_index in enumerate(route):
        position = board.position(position_index)
        if position is None or not position.tiles:
            continue
        last_step = step == len(route) - 1
        if last_step and chosen_tile is not None:
            tile = next((t for t in position.tiles if t.index == chosen_tile),
                        position.tiles[0])
        else:
            tile = min(position.tiles, key=lambda t: math.dist(previous, t.position))
        tiles.append(tile.index)
        previous = tile.position
    return tuple(tiles)


def travellers(state, pawn_id: str) -> Tuple[str, ...]:
    """Everyone who moves when this pawn moves.

    Two rules combine here: pawns riding on top of it (the tower rule from the
    original game) and pawns linked to it by Ondrej's Radar.
    """
    riders = list(state.board.carried_pawns(pawn_id))
    for partner in state.statuses.linked_partners(pawn_id):
        if partner not in riders and partner != pawn_id:
            riders.append(partner)
            riders.extend(
                p for p in state.board.carried_pawns(partner) if p not in riders
            )
    return tuple(riders)


def pawn_name(state, pawn_id: str) -> str:
    pawn = state.library.pawn(pawn_id)
    return pawn.name if pawn is not None else pawn_id


def pawn_options(state, exclude: Sequence[str] = ()) -> Tuple[ChoiceOption, ...]:
    """The pawns a player may pick from.

    A pawn that is off the map is never offered: the card would resolve and do
    nothing, and offering a choice that cannot do anything is a worse answer
    than not offering it.
    """
    return tuple(
        ChoiceOption(id=pawn.id, label=pawn.name, pawn=pawn.id)
        for pawn in live_pawns(state)
        if pawn.id not in exclude
    )


def fields_word(count: int) -> str:
    """Polish plural for 'pole' — 1 pole, 2-4 pola, 5+ pól."""
    if count == 1:
        return "pole"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "pola"
    return "pól"


def split_ids(answer: Optional[str]) -> List[str]:
    """Read a multi-select answer back out of the command.

    Answers travel in ``PlayCard.choices``, which is ``Dict[str, str]`` and has
    to survive a trip through JSON unchanged, so a list of picks is one comma
    separated string rather than a new command field.  Order is preserved
    because for some cards it is the whole point.
    """
    if not answer:
        return []
    return [part for part in (piece.strip() for piece in answer.split(",")) if part]


def join_ids(ids: Sequence[str]) -> str:
    return ",".join(ids)


class MoveProjection:
    """Where the pawns stand once the moves already planned have happened.

    A handler is pure, so it cannot move a pawn and look again.  But a card
    that moves two pawns in order has to know where the second one is AFTER the
    first one went — otherwise a pawn carried along in a tower is moved twice
    from a position it left a moment ago.

    Only positions are projected, and only for the pawns this plan touches.
    Everything else — who ends up carrying whom, which half of a widened row a
    tower walks through — is left to the executor, which sees the real board.
    """

    def __init__(self, state) -> None:
        self.state = state
        self._moved: Dict[str, int] = {}

    def index_of(self, pawn_id: str) -> int:
        if pawn_id in self._moved:
            return self._moved[pawn_id]
        return pawn_index(self.state, pawn_id)

    def move(self, pawn_id: str, destination: int) -> None:
        """Record a move, taking the pawn's tower with it."""
        self._moved[pawn_id] = destination
        for rider in travellers(self.state, pawn_id):
            self._moved[rider] = destination

    @property
    def positions(self) -> Dict[str, int]:
        """Every pawn's projected position — what a neighbour test needs.

        The whole field, not only the pawns this plan has moved: a pawn is
        pinned or freed by where EVERYBODY is, and asking about only the ones
        already moved would find no neighbours at all on the first move.
        """
        return {pawn.id: self.index_of(pawn.id)
                for pawn in live_pawns(self.state)}


# ── the Mody Patusa that change how movement cards behave ────────────────────
def has_neighbour(state, pawn_id: str,
                  positions: Optional[Mapping[str, int]] = None) -> bool:
    """Whether a pawn has company directly in front of or behind it (Halloween).

    Neighbour means the position immediately ahead or immediately behind is
    occupied.  Sharing a field does NOT count: a tower is one field, and the
    rule is about what is in front and behind, not what is underneath.

    THE CAMP IS ONE CLUSTER, and that is a decision rather than a reading of
    the rule.  Every pawn starts in the camp at :data:`CAMP_INDEX`, so under
    the letter of "one in front or one behind" no pawn in the camp ever has a
    neighbour — and with ``mod_round_first`` set to 1 in the lobby, Halloween
    could reach the rack while the whole field is still waiting to start.  The
    board would then be frozen permanently: no movement card could move
    anything, nobody could reach the finish and no tower could ever be built,
    so neither faction could win.  Pawns waiting shoulder to shoulder in the
    camp are treated as neighbours of one another, which is both deadlock-free
    and what the picture on the table actually looks like.

    ``positions`` overrides where the pawns are, for a multi-pawn card that
    moves them one after another and has to see the board each move leaves.
    """
    if positions is None:
        positions = {pawn.id: pawn_index(state, pawn.id)
                     for pawn in live_pawns(state)}
    mine = positions.get(pawn_id, CAMP_INDEX)
    for other, index in positions.items():
        if other == pawn_id:
            continue
        if mine == CAMP_INDEX and index == CAMP_INDEX:
            return True
        if abs(index - mine) == 1:
            return True
    return False


def _capped_steps(ctx: EffectContext, steps: int) -> int:
    """Shrink a movement card's distance to what Masa solna allows.

    Applies to the number PRINTED ON THE CARD only.  A character ability that
    happens to move a pawn (Dziad) is not a movement card, and neither is a
    chest card — hence :attr:`EffectContext.from_movement_card`.  A movement
    BONUS is not capped either: it is a charge somebody spent an ability to
    get, it is added after this, and cancelling it here would quietly rewrite
    ChatGPT's skill instead of the movement deck.

    The sign is preserved, so a card that moves two back moves one back.
    """
    if not ctx.from_movement_card:
        return steps
    cap = ctx.state.movement_cap
    if cap is None or abs(steps) <= cap:
        return steps
    return cap if steps > 0 else -cap


def direction_is_flipped(ctx: EffectContext) -> bool:
    """Whether Gambit Patusa is turning every movement card around this round.

    A CHEST card's rule about MOVEMENT CARDS, so it is gated on
    :attr:`EffectContext.from_movement_card` exactly as Masa solna and
    Halloween are (N103) — a character ability that happens to move a pawn is
    not a movement card, and neither is Dzieckorolka.
    """
    return ctx.from_movement_card and ctx.state.movement_reversed


def _speedrun_reversal(spec: EffectSpec, ctx: EffectContext,
                       key: str = "speedrun", flipped: bool = False) -> Resolution:
    """Ask whether a backward card should be turned around (Speedrun).

    Returns ``True`` when the player chose to go forward, ``False`` to keep the
    printed direction, or a :class:`Choice` when nobody has been asked yet.

    ONLY cards that move backwards ask.  ``direction: "either"`` does not
    count: those cards already let the player pick the way, and a second
    question about the same decision would be asked and answered twice.  A
    forward card never asks at all.

    ``flipped`` is Gambit Patusa, and it is the EFFECTIVE direction that
    decides whether Speedrun speaks — not the printed one.  A backward card
    under a Gambit is already travelling forwards, so offering to turn it round
    would be offering to undo the Gambit while describing it as undoing the
    card.  The two rules compose the other way instead: Speedrun asks about
    what the card is actually about to do.

    Callers must resolve this BEFORE the pawn question — the order of the
    prompts is part of the rules: direction, then pawn, then which half of a
    widened row.
    """
    if spec.direction == "either":
        return False
    backward = spec.is_backward != flipped
    if not backward or not ctx.state.reverses_backward_moves:
        return False
    if not ctx.can_ask:
        # Nobody to ask (a card played by another card).  Speedrun only ever
        # OFFERS a reversal, so declining is always a legal answer and the card
        # does what it says on its face.
        return False
    answer = ctx.choice(key)
    if answer in ("forward", "backward"):
        return answer == "forward"
    because = ("Gambit Patusa cofa tę kartę, ale Speedrun pozwala ją odwrócić"
               if flipped else
               "ta karta cofa pionki, ale Speedrun pozwala ją odwrócić")
    return Choice(
        key=key, kind="option",
        prompt="Speedrun — wybierz kierunek",
        options=(
            ChoiceOption(id="backward", label="Do tyłu"),
            ChoiceOption(id="forward", label="Do przodu"),
        ),
        description=because,
    )


def _turn_expiry(state, spec: EffectSpec) -> Optional[int]:
    turns = int(spec.get("duration_turns", 0) or 0)
    if turns <= 0:
        return None
    return state.turn_counter + turns


# ── movement ─────────────────────────────────────────────────────────────────
def _movement_target(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Which pawn a movement effect acts on, asking if the player must choose."""
    target = spec.target
    if target == "fixed":
        pawn_id = spec.pawn
        if pawn_id is None:
            raise EffectError("Efekt 'fixed' bez wskazanego pionka")
        return pawn_id
    if target == "hindmost":
        return hindmost_pawn(ctx.state)
    if target == "foremost":
        return foremost_pawn(ctx.state)
    if target == "choice":
        chosen = ctx.choice("pawn")
        if chosen and ctx.state.library.pawn(chosen) is not None:
            return chosen
        return Choice(
            key="pawn", kind="pawn",
            prompt="Wybierz pionek",
            options=pawn_options(ctx.state),
        )
    raise EffectError(f"Nieznany cel efektu: {target!r}")


def _movement_steps(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """How far and which way, asking when the card leaves it open."""
    options = [int(v) for v in (spec.get("step_options") or [])]
    either = spec.direction == "either"
    if not options and not either:
        return spec.signed_steps

    magnitudes = options or [spec.steps]
    directions = [1, -1] if either else [-1 if spec.is_backward else 1]
    chosen = ctx.choice("move")
    if chosen is not None:
        try:
            return int(chosen)
        except ValueError:
            pass
    choices: List[ChoiceOption] = []
    for direction in directions:
        for magnitude in magnitudes:
            signed = direction * magnitude
            way = "do przodu" if direction > 0 else "do tyłu"
            choices.append(ChoiceOption(
                id=str(signed),
                label=f"{magnitude} {fields_word(magnitude)} {way}",
            ))
    return Choice(key="move", kind="option", prompt="Wybierz ruch",
                  options=tuple(choices))


@effect("move_pawn")
def _move_pawn(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Move one pawn a number of positions, forwards or backwards.

    THE ORDER OF THE QUESTIONS IS PART OF THE RULES and it is the order they
    appear in below: Speedrun's direction first, then which pawn, then which
    half of a widened row.  Speedrun comes first because it can be settled
    before anybody knows which pawn is moving — the card's printed direction is
    all it needs — and asking it after the pawn would mean the player picking a
    pawn to move backwards and only then being told it could go forwards.
    """
    state = ctx.state

    # 0) Gambit Patusa, which asks nothing: it is a fact about the round, and
    #    it is settled first because Speedrun's question depends on it.
    flipped = direction_is_flipped(ctx)

    # 1) Speedrun — before the pawn question.
    reversed_direction = _speedrun_reversal(spec, ctx, flipped=flipped)
    if isinstance(reversed_direction, (Choice, Refusal, NotAvailable)):
        return reversed_direction

    # 2) which pawn.
    pawn_id = _movement_target(spec, ctx)
    if isinstance(pawn_id, (Choice, Refusal, NotAvailable)):
        return pawn_id
    if pawn_id is None:
        return Refusal("Nie ma pionka, który mógłby się poruszyć")
    if state.library.pawn(pawn_id) is None:
        raise EffectError(f"Efekt wskazuje nieznany pionek: {pawn_id!r}")

    steps = _movement_steps(spec, ctx)
    if isinstance(steps, (Choice, Refusal, NotAvailable)):
        return steps

    # Masa solna shortens the card; Gambit Patusa then turns it around, and
    # Speedrun may turn it back.  Distance first, then direction, so the three
    # never fight over the sign — and the DISTANCE is untouched by both
    # reversals, which is what "kierunek" means on either card.
    steps = _capped_steps(ctx, steps)
    if flipped:
        steps = -steps
    if reversed_direction:
        steps = abs(steps)

    name = pawn_name(state, pawn_id)

    # Shady: a pawn that is off the map cannot be moved, and the card does not
    # refuse — it resolves, is discarded and does nothing, exactly like a
    # blocked move.  Checked BEFORE the freeze, which is a refusal: a pawn that
    # is not on the board at all has nothing to say about being frozen.
    if is_hidden(state, pawn_id):
        return Plan(
            (Fizzle(f"Shady: pionek {name} zniknął z mapy", pawn_id),),
            f"{name}: poza mapą",
        )

    if state.statuses.pawn_has(StatusKind.FROZEN, pawn_id):
        return Refusal(f"Pionek {name} jest zamrożony")

    # Halloween pins a pawn with nobody in front of or behind it.  This is NOT
    # a refusal — the card is played and discarded, its movement just does
    # nothing — and it is checked before the widened-row question, because
    # asking which half of a field to land on makes no sense for a pawn that is
    # not going anywhere.
    if (ctx.from_movement_card and state.requires_neighbour
            and not has_neighbour(state, pawn_id)):
        return Plan(
            (Fizzle(f"Halloween: {name} nie ma sąsiadów i zostaje w miejscu",
                    pawn_id),),
            f"{name}: bez ruchu (Halloween)",
        )

    operations: List[Operation] = []
    # A movement bonus (ChatGPT) stretches the next card by one field and is
    # then spent — including when the card moves backwards, where "further" is
    # further back.
    bonus, bonus_status = state.statuses.movement_bonus_for(ctx.actor)
    if bonus and spec.get("ignore_bonus") is not True:
        steps += bonus if steps > 0 else -bonus
        operations.append(SpendStatus(
            StatusKind.MOVEMENT_BONUS, Subject.PLAYER, str(ctx.actor)
        ))

    start = pawn_index(state, pawn_id)
    route = route_between(state, start, steps)
    if not route:
        backward = steps < 0
        if backward and start == CAMP_INDEX:
            return Refusal(f"Pionek {name} jeszcze nie wyruszył")
        if backward:
            return Refusal(f"Pionek {name} jest już na starcie")
        return Refusal(f"Pionek {name} jest już na mecie")

    allowed = state.statuses.movement_range()
    if allowed is not None and not (allowed[0] <= route[-1] <= allowed[1]):
        return Refusal(
            f"Ruch ograniczony do pól {allowed[0] + 1}–{allowed[1] + 1}"
        )

    destination = state.board.position(route[-1])
    chosen_tile: Optional[int] = None
    if destination is not None and destination.is_doubled:
        answer = ctx.choice("tile")
        valid = {tile.index: tile for tile in destination.tiles}
        if answer is not None and int(answer) in valid:
            chosen_tile = int(answer)
        else:
            return Choice(
                key="tile", kind="tile",
                prompt=f"Wybierz pole: {' albo '.join(t.label for t in destination.tiles)}",
                options=tuple(
                    ChoiceOption(id=str(t.index), label=t.label, tile=t.index)
                    for t in destination.tiles
                ),
                description=_move_description(name, route, steps),
            )

    operations.append(MovePawn(
        pawn_id=pawn_id,
        from_index=start,
        route=route,
        tiles=tile_route(state, pawn_id, route, chosen_tile),
        carried=travellers(state, pawn_id),
    ))
    return Plan(tuple(operations), _move_description(name, route, steps))


def _move_description(name: str, route: Sequence[int], steps: int) -> str:
    direction = "do tyłu" if steps < 0 else "do przodu"
    return f"{name}: {len(route)} {fields_word(len(route))} {direction}"


@effect("stack_pawn")
def _stack_pawn(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Put one pawn straight onto another's field (Mitoman's PAA)."""
    state = ctx.state
    source = _named_pawn(spec.get("source", "foremost"), ctx, "pawn")
    if isinstance(source, (Choice, Refusal, NotAvailable)):
        return source
    destination = _named_pawn(spec.get("destination", "hindmost"), ctx, "pawn_b")
    if isinstance(destination, (Choice, Refusal, NotAvailable)):
        return destination
    if source is None or destination is None:
        return Refusal("Brakuje pionków do przeniesienia")
    if source == destination:
        return Refusal("Ten pionek już tam stoi")
    for pawn_id in (source, destination):
        if is_hidden(state, pawn_id):
            return Plan(
                (Fizzle(f"Shady: pionek {pawn_name(state, pawn_id)} "
                        f"zniknął z mapy", pawn_id),),
                f"{pawn_name(state, pawn_id)}: poza mapą",
            )
    if state.statuses.pawn_has(StatusKind.FROZEN, source):
        return Refusal(f"Pionek {pawn_name(state, source)} jest zamrożony")

    target_index = pawn_index(state, destination)
    if target_index == CAMP_INDEX:
        return Refusal(f"Pionek {pawn_name(state, destination)} jeszcze nie wyruszył")
    tile = state.board.pawn_tile(destination)
    if tile is None:
        return Refusal("Nie wiadomo, gdzie stoi pionek docelowy")

    return Plan(
        (MovePawn(
            pawn_id=source,
            from_index=pawn_index(state, source),
            route=(target_index,),
            tiles=(tile.index,),
            carried=travellers(state, source),
            teleport=True,
        ),),
        f"{pawn_name(state, source)} → na {pawn_name(state, destination)}",
    )


def _named_pawn(name: str, ctx: EffectContext, choice_key: str) -> Resolution:
    """Resolve a pawn reference from a spec: foremost, hindmost, or a choice."""
    if name == "foremost":
        return foremost_pawn(ctx.state)
    if name == "hindmost":
        return hindmost_pawn(ctx.state)
    if name == "choice":
        chosen = ctx.choice(choice_key)
        if chosen and ctx.state.library.pawn(chosen) is not None:
            return chosen
        return Choice(key=choice_key, kind="pawn", prompt="Wybierz pionek",
                      options=pawn_options(ctx.state))
    if ctx.state.library.pawn(name) is not None:
        return name
    raise EffectError(f"Nieznane odwołanie do pionka: {name!r}")


# ── statuses ─────────────────────────────────────────────────────────────────
@effect("freeze_pawn")
def _freeze_pawn(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Freeze a pawn for a turn (Big D Randy's Granny Costume)."""
    state = ctx.state
    pawn_id = _named_pawn(spec.target, ctx, "pawn")
    if isinstance(pawn_id, (Choice, Refusal, NotAvailable)):
        return pawn_id
    if pawn_id is None:
        return Refusal("Nie ma pionka do zamrożenia")

    name = pawn_name(state, pawn_id)
    if spec.get("require_alone"):
        tile = state.board.pawn_tile(pawn_id)
        if tile is None:
            return Refusal(f"Pionek {name} jeszcze nie wyruszył")
        if len(tile.stack) > 1:
            return Refusal(f"Pionek {name} nie stoi samotnie")

    return Plan(
        (GrantStatus(Status.for_pawn(
            StatusKind.FROZEN, pawn_id,
            expires_after_turn=_turn_expiry(state, spec),
            source=ctx.source,
        )),),
        f"{name} zamrożony",
    )


@effect("freeze_player")
def _freeze_player(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Take away a player's next move (Lubin, Dziubdziuch)."""
    state = ctx.state
    target = spec.target
    if target == "piotrek":
        index = piotrek_player(state)
        if index is None:
            return Refusal("Nikt nie gra Piotrkiem")
    elif target == "self":
        index = ctx.actor
    elif target == "choice":
        chosen = ctx.choice("player")
        if chosen is None:
            return Choice(
                key="player", kind="option", prompt="Wybierz gracza",
                options=tuple(
                    ChoiceOption(id=str(p.index), label=p.name) for p in state.players
                ),
            )
        index = int(chosen)
    else:
        raise EffectError(f"Nieznany cel: {target!r}")

    player = state.player(index)
    name = player.name if player is not None else f"gracz {index}"
    return Plan(
        (GrantStatus(Status.for_player(
            StatusKind.SKIP_TURN, index,
            expires_after_turn=_turn_expiry(state, spec),
            source=ctx.source,
        )),),
        f"{name}: ruch pominięty",
    )


@effect("link_pawns")
def _link_pawns(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Tie two pawns together for a turn (Ondrej's Radar)."""
    state = ctx.state
    first = ctx.choice("pawn")
    if first is None or state.library.pawn(first) is None:
        return Choice(key="pawn", kind="pawn", prompt="Wybierz pierwszy pionek",
                      options=pawn_options(state))
    second = ctx.choice("pawn_b")
    if second is None or state.library.pawn(second) is None or second == first:
        return Choice(
            key="pawn_b", kind="pawn", prompt="Wybierz drugi pionek",
            options=pawn_options(state, exclude=(first,)),
            description=f"sklejone z: {pawn_name(state, first)}",
        )

    members = sorted((first, second))
    return Plan(
        (GrantStatus(Status(
            kind=StatusKind.LINKED,
            subject=Subject.TABLE,
            subject_id="+".join(members),
            data={"members": members},
            expires_after_turn=_turn_expiry(state, spec),
            source=ctx.source,
        )),),
        f"{pawn_name(state, first)} + {pawn_name(state, second)} sklejone",
    )


@effect("forbid_adjacency")
def _forbid_adjacency(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Two pawns may not stand next to each other (Dług u Tomasza).

    The state is recorded and shown; enforcing it belongs with the checking
    rules, which do not exist yet, so nothing validates against it today.
    """
    state = ctx.state
    first = ctx.choice("pawn")
    if first is None or state.library.pawn(first) is None:
        return Choice(key="pawn", kind="pawn", prompt="Wybierz pierwszy pionek",
                      options=pawn_options(state))
    second = ctx.choice("pawn_b")
    if second is None or state.library.pawn(second) is None or second == first:
        return Choice(
            key="pawn_b", kind="pawn", prompt="Wybierz drugi pionek",
            options=pawn_options(state, exclude=(first,)),
            description=f"nie mogą sąsiadować z: {pawn_name(state, first)}",
        )

    members = sorted((first, second))
    return Plan(
        (GrantStatus(Status(
            kind=StatusKind.FORBIDDEN_ADJACENCY,
            subject=Subject.TABLE,
            subject_id="+".join(members),
            data={"members": members},
            expires_after_turn=_turn_expiry(state, spec),
            source=ctx.source,
        )),),
        f"{pawn_name(state, first)} i {pawn_name(state, second)} nie mogą sąsiadować",
    )


@effect("restrict_movement")
def _restrict_movement(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Confine movement to the span between the last and first pawn (Norbur)."""
    state = ctx.state
    ordered = [entry for entry in _ordered_pawns(state) if entry[0] != CAMP_INDEX]
    if len(ordered) < 2:
        return Refusal("Za mało pionków na planszy")

    low, high = ordered[0][0], ordered[-1][0]
    minimum = int(spec.get("min_gap", 0))
    if high - low < minimum:
        return Refusal(
            f"Pionki muszą dzielić co najmniej {minimum} {fields_word(minimum)}"
        )

    return Plan(
        (GrantStatus(Status.for_table(
            StatusKind.RESTRICTED_MOVEMENT,
            data={"from": low, "to": high},
            expires_after_turn=_turn_expiry(state, spec),
            source=ctx.source,
        )),),
        f"Ruch tylko między polami {low + 1} a {high + 1}",
    )


@effect("grant_extra_turn")
def _grant_extra_turn(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Award an additional move (Atencjusz's Liskowy Konkurs)."""
    state = ctx.state
    index = ctx.actor if spec.target in ("self", "fixed") else ctx.actor
    player = state.player(index)
    name = player.name if player is not None else "gracz"
    return Plan(
        (GrantStatus(Status.for_player(
            StatusKind.EXTRA_TURN, index,
            data={"count": int(spec.get("count", 1))},
            source=ctx.source,
        )),),
        f"{name}: dodatkowy ruch",
    )


@effect("movement_bonus")
def _movement_bonus(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """The next movement card reaches one field further (ChatGPT)."""
    state = ctx.state
    amount = int(spec.get("amount", 1))
    if state.statuses.find(StatusKind.MOVEMENT_BONUS, Subject.PLAYER, str(ctx.actor)):
        return Refusal("Bonus do ruchu jest już aktywny")
    return Plan(
        (GrantStatus(Status.for_player(
            StatusKind.MOVEMENT_BONUS, ctx.actor,
            data={"amount": amount},
            charges=1,
            source=ctx.source,
        )),),
        f"Następna karta ruchu: +{amount} {fields_word(amount)}",
    )


# ── waiting on the checking mechanic ─────────────────────────────────────────
@effect("check_pawn")
def _check_pawn(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    return NotAvailable(
        "Sprawdzanie pionków nie jest jeszcze zaimplementowane — "
        "rozstrzygnijcie tę umiejętność przy stole"
    )


@effect("refuse_check")
def _refuse_check(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    return NotAvailable(
        "Sprawdzanie pionków nie jest jeszcze zaimplementowane — "
        "Ice Block czeka na tę mechanikę"
    )


# ── special presentation ─────────────────────────────────────────────────────
@effect("draw_into_mods")
def _draw_into_mods(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Draw a Mod Patusa and put it into the rack (Thunderfuck)."""
    return Plan(
        (DrawIntoMods(deck_id=spec.get("deck", "mods")),),
        "Nowy Mod Patusa",
    )


@effect("random_movement_card")
def _random_movement_card(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Announce, then play a random movement card (Seks z pedałami)."""
    return Plan(
        (PlayRandomCard(
            deck_id=spec.get("deck", "movement"),
            announce_seconds=float(spec.get("announce_seconds", 2.0)),
        ),),
        "Losowa karta ruchu",
    )


# ── a card that takes over the player's next turn ────────────────────────────
@effect("turn_interrupt")
def _turn_interrupt(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Queue something to happen INSTEAD of the player's next move.

    Declared by Troll and Stańczyk on ``on_draw``.  The status carries the
    effect specification it should run when the turn arrives, so the mechanism
    is one status kind and one resolution step rather than one of each per
    card: a future Chest card that hijacks a turn writes JSON and nothing else.

    The card itself STAYS IN THE HAND.  That is what gives the interface
    something to point at when the turn comes round, and — because the card is
    also ``locked`` — what stops the player throwing the consequence away.
    """
    state = ctx.state
    interrupt = spec.get("interrupt")
    if not isinstance(interrupt, Mapping):
        raise EffectError("turn_interrupt bez opisu efektu w polu 'interrupt'")

    operations: List[Operation] = [GrantStatus(
        Status.for_player(
            StatusKind.TURN_INTERRUPT, ctx.actor,
            data={"effect": dict(interrupt), "card_uid": ctx.card_uid},
            source=ctx.source,
        ),
        stack=True,
    )]

    # Some interrupts hand a replacement card over straight away, so the player
    # still has a normal number of things to do on the turns before it fires.
    draw = spec.get("draw")
    if isinstance(draw, Mapping):
        operations.append(DrawCards(
            player_index=ctx.actor,
            deck_id=str(draw.get("deck", "movement")),
            count=max(1, int(draw.get("count", 1))),
        ))

    player = state.player(ctx.actor)
    name = player.name if player is not None else "gracz"
    return Plan(tuple(operations), f"{name}: następna tura przejęta")


@effect("skip_turn")
def _skip_turn(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Stańczyk: show the card, then lose the turn.

    Everything mechanical about "the turn is skipped" belongs to the interrupt
    machinery in ``GameState._begin_turn`` — an interrupt consumes the turn by
    definition — so all this contributes is the two seconds the player spends
    looking at the card that did it.
    """
    lost = TurnLost(player_index=ctx.actor, source=ctx.source)
    if ctx.card_uid is None:
        return Plan((lost,), "Tura pominięta")
    return Plan(
        (
            HighlightHeldCard(
                player_index=ctx.actor,
                card_uid=ctx.card_uid,
                seconds=float(spec.get("highlight_seconds", 2.0)),
                caption=str(spec.get("caption", "tura pominięta")),
            ),
            lost,
        ),
        "Tura pominięta",
    )


@effect("forced_play")
def _forced_play(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Troll: the game picks a card out of the hand and plays it.

    Which card needs the seeded RNG, so the handler only describes the search —
    the executor performs it (see :class:`ForcedPlay`).
    """
    decks = spec.get("priority_decks") or ["chest"]
    fallback = spec.get("fallback_decks") or ["movement"]
    return Plan(
        (ForcedPlay(
            player_index=ctx.actor,
            source_uid=ctx.card_uid or 0,
            priority_decks=tuple(str(d) for d in decks),
            fallback_decks=tuple(str(d) for d in fallback),
            seconds=float(spec.get("highlight_seconds", 2.5)),
            caption=str(spec.get("caption", "zagrywasz tę kartę")),
        ),),
        "Wymuszone zagranie",
    )


# ── taking a card out of somebody else's hand ────────────────────────────────
def _steal_victim(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Whose hand Spy looks into.

    A hunter always robs Piotrek — he is the one worth robbing and there is
    only one of him.  Piotrek picks which hunter, because from his side they
    are interchangeable and the choice is the interesting part.
    """
    state = ctx.state
    actor = state.player(ctx.actor)
    if actor is None:
        return Refusal("Nieznany gracz")

    if not actor.is_piotrek:
        seat = piotrek_player(state)
        if seat is None:
            return Refusal("Nikt nie gra Piotrkiem")
        return seat

    answer = ctx.choice("victim")
    hunters = [p for p in state.players if p.index != actor.index]
    if not hunters:
        return Refusal("Nie ma kogo okraść")
    seats = {str(p.index): p.index for p in hunters}
    if answer in seats:
        return seats[answer]
    if len(hunters) == 1:
        return hunters[0].index
    return Choice(
        key="victim", kind="option", prompt="Kogo przejrzeć?",
        options=tuple(
            ChoiceOption(id=str(p.index), label=p.name) for p in hunters
        ),
    )


@effect("steal_card")
def _steal_card(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Spy: look through one deck's worth of an opponent's hand and take one.

    Only the cards of ``deck`` are ever offered, which is the rule and also the
    secrecy: Piotrek's Chest cards stay face down, and so does everything else
    about the hand.  The question itself reaches ONE player — the engine sends
    ChoiceRequired to whoever asked and to nobody else (N40) — so the hidden
    information never crosses a wire it should not.
    """
    state = ctx.state
    deck_id = str(spec.get("deck", "movement"))
    actor = state.player(ctx.actor)
    if actor is None:
        return Refusal("Nieznany gracz")

    victim_index = _steal_victim(spec, ctx)
    if isinstance(victim_index, (Choice, Refusal, NotAvailable)):
        return victim_index
    victim = state.player(int(victim_index))
    if victim is None:
        return Refusal("Nieznany gracz")
    if victim.index == actor.index:
        return Refusal("Nie okradniesz sam siebie")

    offered = [card for card in victim.hand if card.deck_id == deck_id]
    if not offered:
        return Refusal(f"{victim.name} nie ma kart ruchu do zabrania")
    if actor.hand_is_full:
        return Refusal("Twoja ręka jest pełna")

    answer = ctx.choice("stolen")
    available = {str(card.uid): card for card in offered}
    if answer not in available:
        return Choice(
            key="stolen", kind="card",
            prompt=f"Karty ruchu: {victim.name}",
            options=tuple(
                ChoiceOption(id=str(card.uid), label=card.title, card_uid=card.uid)
                for card in offered
            ),
            description="Wybierz jedną kartę do zabrania",
            owner=victim.index,
        )

    card = available[answer]
    # The description is broadcast with CardPlayed, so it names the seats and
    # not the card.  Everyone may know that Spy was played and on whom; only
    # the two hands involved may know what changed.
    return Plan(
        (
            TransferCard(from_player=victim.index, to_player=actor.index,
                         card_uid=card.uid),
            DrawCards(player_index=victim.index, deck_id=deck_id, count=1),
        ),
        f"{actor.name} zabiera kartę ruchu: {victim.name}",
    )


# ── moving several pawns, in a chosen order ──────────────────────────────────
@effect("move_pawns")
def _move_pawns(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Move ``count`` chosen pawns the same distance, one after another.

    Written for Plagiat! and deliberately not written for Plagiat!: the number
    of pawns, the distance and the direction are all parameters, so "move three
    pawns one forward" is a JSON entry and no code at all.

    The pawns move STRICTLY in the order they were picked, and each move sees
    the board the previous one left behind — hence :class:`MoveBySteps` and
    :class:`MoveProjection`.  Stacking, towers, widened rows and the animation
    are then exactly what they are for any other move, because by the time the
    executor runs it is doing an ordinary move.
    """
    state = ctx.state
    wanted = max(1, int(spec.get("count", 2)))
    key = str(spec.get("choice_key", "pawns"))

    # Gambit Patusa and Speedrun, in that order and for the same reasons as in
    # ``_move_pawn``.  Plagiat! moves backwards through THIS handler, so every
    # rule about direction has to be written here as well or the two paths
    # drift apart and the difference only shows on the one card that takes this
    # route (N104).
    flipped = direction_is_flipped(ctx)
    reversed_direction = _speedrun_reversal(spec, ctx, flipped=flipped)
    if isinstance(reversed_direction, (Choice, Refusal, NotAvailable)):
        return reversed_direction

    steps = _capped_steps(ctx, spec.signed_steps)
    if flipped:
        steps = -steps
    if reversed_direction:
        steps = abs(steps)

    known = {pawn.id for pawn in state.library.pawns}
    picked: List[str] = []
    for pawn_id in split_ids(ctx.choice(key)):
        if pawn_id in known and pawn_id not in picked:
            picked.append(pawn_id)

    if len(picked) != wanted:
        way = "do tyłu" if steps < 0 else "do przodu"
        return Choice(
            key=key, kind="pawn",
            prompt=f"Wybierz {wanted} pionki w kolejności ruchu",
            options=pawn_options(state),
            description=f"każdy przesunie się o {abs(steps)} "
                        f"{fields_word(abs(steps))} {way}",
            count=wanted, ordered=True,
        )

    allowed = state.statuses.movement_range()
    projection = MoveProjection(state)
    operations: List[Operation] = []
    names: List[str] = []

    blocked: List[str] = []

    for order, pawn_id in enumerate(picked):
        name = pawn_name(state, pawn_id)

        # Shady, per pawn and on the same terms as Halloween below: the pawn
        # is skipped and the rest of the card carries on.  A rule added to
        # ``_move_pawn`` has to be added here too or the two paths drift, and
        # the difference only shows up on the one card that takes this one.
        if is_hidden(state, pawn_id):
            blocked.append(f"{name} (poza mapą)")
            continue

        if state.statuses.pawn_has(StatusKind.FROZEN, pawn_id):
            return Refusal(f"Pionek {name} jest zamrożony")

        # Halloween is judged per pawn and against the board THIS move sees,
        # not the one the card was played on: the pawns move one after another,
        # so an earlier move can give a later pawn the neighbour it needed, or
        # take it away.  A pinned pawn is skipped and the rest of the card
        # carries on — it is one pawn that does nothing, not a refused card.
        if ctx.from_movement_card and state.requires_neighbour:
            if not has_neighbour(state, pawn_id, projection.positions):
                blocked.append(f"{name} (Halloween, bez sąsiadów)")
                continue

        start = projection.index_of(pawn_id)
        route = route_between(state, start, steps)
        if not route:
            if steps < 0 and start == CAMP_INDEX:
                return Refusal(f"Pionek {name} jeszcze nie wyruszył")
            if steps < 0:
                return Refusal(f"Pionek {name} jest już na starcie")
            return Refusal(f"Pionek {name} jest już na mecie")
        if allowed is not None and not (allowed[0] <= route[-1] <= allowed[1]):
            return Refusal(
                f"Ruch ograniczony do pól {allowed[0] + 1}–{allowed[1] + 1}"
            )

        destination = state.board.position(route[-1])
        chosen_tile: Optional[int] = None
        if destination is not None and destination.is_doubled:
            # One question per pawn, keyed by its place in the order, so two
            # pawns landing on widened rows ask twice and neither answer
            # overwrites the other.
            tile_key = f"{key}_tile{order}"
            answer = ctx.choice(tile_key)
            valid = {tile.index for tile in destination.tiles}
            if answer is not None and int(answer) in valid:
                chosen_tile = int(answer)
            else:
                halves = " albo ".join(t.label for t in destination.tiles)
                return Choice(
                    key=tile_key, kind="tile",
                    prompt=f"Pionek {name} — wybierz pole: {halves}",
                    options=tuple(
                        ChoiceOption(id=str(t.index), label=t.label, tile=t.index)
                        for t in destination.tiles
                    ),
                    description=f"ruch {order + 1} z {wanted}",
                )

        operations.append(MoveBySteps(
            pawn_id=pawn_id, steps=steps, chosen_tile=chosen_tile,
            preview_tiles=tile_route(state, pawn_id, route, chosen_tile),
        ))
        projection.move(pawn_id, route[-1])
        names.append(name)

    way = "do tyłu" if steps < 0 else "do przodu"
    if blocked:
        # Every chosen pawn was pinned or absent: the card still resolves and
        # is still discarded, so it needs an operation to carry that rather
        # than an empty plan, which every caller would read as a refusal.
        note = f"Bez ruchu: {', '.join(blocked)}"
        if not operations:
            return Plan((Fizzle(note),), "bez ruchu")
        operations.append(Fizzle(note))
    return Plan(
        tuple(operations),
        f"{' → '.join(names)}: {abs(steps)} {fields_word(abs(steps))} {way}",
    )


# ── the Karty Skrzyni ────────────────────────────────────────────────────────
# A chest card is NOT a movement card.  Everything below therefore escapes Masa
# solna, Halloween and Gambit Patusa by construction, because all three are
# gated on ``ctx.from_movement_card`` (N103) and ``deck_id`` here is "chest".
# That is a rule, not an oversight: a mod that shortens the movement deck must
# not silently shorten the Chest as well.
def _branch_key(step: int) -> str:
    """Where the answer to "which half of this widened row?" is stored.

    Keyed by the STEP along the route rather than by the position index, so the
    keys are 0, 1, 2 whatever part of the board the pawn is on — and stable
    across the resubmissions, because the route is fixed once the pawn is
    known.  A card that asks twice must not be able to overwrite its own first
    answer, which is the same reasoning ``_move_pawns`` uses for ``pawns_tile0``.
    """
    return f"branch{step}"


def _walk_with_branches(
    state, pawn_id: str, route: Sequence[int], ctx: EffectContext,
) -> Resolution:
    """Choose the concrete field for every position along a route, asking.

    The ordinary rule (D8a) picks the nearer half of an intermediate widened
    row by itself, because nothing depends on where a pawn merely passed
    through.  Dzieckorolka is the card that makes it depend: which half it
    walks through decides WHICH PAWN it sweeps up, so every widened position on
    the route is a real decision and each one is asked separately.

    Returns the tuple of field indices, or the first unanswered
    :class:`Choice`.
    """
    board = state.board
    current = board.pawn_tile(pawn_id)
    previous = current.position if current is not None else board.camp_position(0)

    tiles: List[int] = []
    for step, position_index in enumerate(route):
        position = board.position(position_index)
        if position is None or not position.tiles:
            continue
        if not position.is_doubled:
            tile = position.tiles[0]
        else:
            key = _branch_key(step)
            answer = ctx.choice(key)
            valid = {t.index: t for t in position.tiles}
            if answer is not None and int(answer) in valid:
                tile = valid[int(answer)]
            else:
                halves = " albo ".join(t.label for t in position.tiles)
                return Choice(
                    key=key, kind="tile",
                    prompt=f"Wybierz drogę: {halves}",
                    options=tuple(
                        ChoiceOption(id=str(t.index), label=t.label, tile=t.index)
                        for t in position.tiles
                    ),
                    description=f"krok {step + 1} z {len(route)} — "
                                f"zabierzesz pionka z wybranego pola",
                )
        tiles.append(tile.index)
        previous = tile.position
    return tuple(tiles)


@effect("move_and_collect")
def _move_and_collect(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Dzieckorolka: move a pawn forward, sweeping up one pawn per field.

    Three rules, and the third is the one worth reading twice:

    1. ONE pawn per field, and it is the TOP one.  Taking the top can never
       disturb a tower, because by definition nothing is standing on it.
    2. Fields the pawn walks THROUGH are swept; the field it lands on is an
       ordinary landing.  The two are indistinguishable in the finished stack —
       a collected destination pawn would be inserted exactly where it already
       was — so this is the reading that matches "po drodze" without changing
       any outcome.
    3. THE ORDER OF THE FINISHED TOWER IS THE PATH.  Reading downwards from the
       mover: first collected, then second, then whoever was already standing
       on the destination.  ``collected`` is therefore in travel order and the
       executor reverses it into the stack.

    The card is a Chest card, so no Mod Patusa touches it, and it does not
    consume ChatGPT's movement bonus either: the collection is written against
    a route of exactly the printed length, and a card whose distance a skill
    could stretch would sweep a field its own text never promised.
    """
    state = ctx.state
    steps = abs(int(spec.get("steps", 3)))

    pawn_id = _movement_target(spec, ctx)
    if isinstance(pawn_id, (Choice, Refusal, NotAvailable)):
        return pawn_id
    if pawn_id is None:
        return Refusal("Nie ma pionka, który mógłby się poruszyć")
    if state.library.pawn(pawn_id) is None:
        raise EffectError(f"Efekt wskazuje nieznany pionek: {pawn_id!r}")

    name = pawn_name(state, pawn_id)
    if is_hidden(state, pawn_id):
        return Plan(
            (Fizzle(f"Shady: pionek {name} zniknął z mapy", pawn_id),),
            f"{name}: poza mapą",
        )
    if state.statuses.pawn_has(StatusKind.FROZEN, pawn_id):
        return Refusal(f"Pionek {name} jest zamrożony")

    start = pawn_index(state, pawn_id)
    route = route_between(state, start, steps)
    if not route:
        return Refusal(f"Pionek {name} jest już na mecie")

    allowed = state.statuses.movement_range()
    if allowed is not None and not (allowed[0] <= route[-1] <= allowed[1]):
        return Refusal(f"Ruch ograniczony do pól {allowed[0] + 1}–{allowed[1] + 1}")

    tiles = _walk_with_branches(state, pawn_id, route, ctx)
    if isinstance(tiles, (Choice, Refusal, NotAvailable)):
        return tiles

    riders = travellers(state, pawn_id)
    collected: List[str] = []
    passed_over: List[str] = []
    for tile_index in tiles[:-1]:
        tile = state.board.tile(tile_index)
        if tile is None or not tile.stack:
            continue
        top = tile.stack[-1]
        if top == pawn_id or top in riders:
            continue
        # A frozen pawn may not move, and being swept up IS being moved.  The
        # field simply yields nothing rather than the sweep reaching past the
        # top pawn, because "always take the TOP pawn" is the rule and digging
        # underneath one would take a tower apart.
        if state.statuses.pawn_has(StatusKind.FROZEN, top):
            passed_over.append(f"{pawn_name(state, top)} (zamrożony)")
            continue
        collected.append(top)

    description = f"{name}: {len(route)} {fields_word(len(route))} do przodu"
    if collected:
        description += (" — zabiera: "
                        + ", ".join(pawn_name(state, p) for p in collected))
    if passed_over:
        description += f" (pomija: {', '.join(passed_over)})"

    return Plan(
        (MoveAndCollect(
            pawn_id=pawn_id,
            from_index=start,
            route=route,
            tiles=tuple(tiles),
            carried=riders,
            collected=tuple(collected),
        ),),
        description,
    )


@effect("move_all_pawns")
def _move_all_pawns(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Balbinka: every pawn moves the same distance, the player picks the way.

    NOBODY IS CARRIED.  The tower rule would move a rider twice — once inside
    its tower and once in its own right — and the card says two fields, not
    four.  Each pawn therefore travels alone, which is also why the ORDER
    matters and is not arbitrary:

    * going FORWARD the furthest pawn moves first, going BACKWARD the rearmost
      does, so a pawn never lands on a field whose occupant has not yet left.
      Get this wrong and the occupant, moving later, walks out from underneath
      a tower it acquired in between;
    * within one field the bottom pawn moves first, so a tower arrives in the
      order it left.

    Only pawns already sharing a field can converge (two pawns a field apart
    stay a field apart when both move the same distance), so those two rules
    are the whole of it — except at the finish and the start, where movement
    clamps and the pawn behind ends up on top of the pawn in front.  That is
    the ordinary stacking rule doing what it always does.

    The widened rows are decided by the executor, at random and with no
    prompt: a card that moved six pawns would otherwise ask six questions
    nobody has an interesting answer to.
    """
    state = ctx.state
    distance = max(1, abs(int(spec.get("steps", 2))))

    if spec.direction == "either":
        answer = ctx.choice("direction")
        if answer not in ("forward", "backward"):
            return Choice(
                key="direction", kind="option",
                prompt="Balbinka — wybierz kierunek",
                options=(
                    ChoiceOption(id="forward", label="Do przodu"),
                    ChoiceOption(id="backward", label="Do tyłu"),
                ),
                description=f"wszystkie pionki ruszą o {distance} "
                            f"{fields_word(distance)}",
            )
        steps = distance if answer == "forward" else -distance
    else:
        steps = -distance if spec.is_backward else distance

    random_branch = bool(spec.get("random_branch", True))
    forward = steps > 0

    movers: List[Tuple[int, int, int, str]] = []
    for order, pawn in enumerate(live_pawns(state)):
        index = pawn_index(state, pawn.id)
        depth = state.board.stack_depth(pawn.id) if index != CAMP_INDEX else 0
        # Furthest first going forward, rearmost first going backward; bottom
        # of a tower before the pawns standing on it, either way.
        rank = -index if forward else index
        movers.append((rank, depth, order, pawn.id))
    movers.sort()

    operations: List[Operation] = []
    moved: List[str] = []
    blocked: List[str] = []
    for _, _, _, pawn_id in movers:
        name = pawn_name(state, pawn_id)
        if state.statuses.pawn_has(StatusKind.FROZEN, pawn_id):
            blocked.append(f"{name} (zamrożony)")
            continue
        if not route_between(state, pawn_index(state, pawn_id), steps):
            continue
        operations.append(MoveBySteps(
            pawn_id=pawn_id, steps=steps,
            carry_riders=False, random_branch=random_branch,
        ))
        moved.append(name)

    way = "do przodu" if forward else "do tyłu"
    if not operations:
        return Plan(
            (Fizzle("Żaden pionek nie może się ruszyć"),),
            f"Balbinka: bez ruchu {way}",
        )
    description = (f"Wszystkie pionki: {distance} {fields_word(distance)} {way}"
                   f" ({len(moved)})")
    if blocked:
        operations.append(Fizzle(f"Bez ruchu: {', '.join(blocked)}"))
    return Plan(tuple(operations), description)


@effect("replace_mods")
def _replace_mods(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Rage Quit: both active Mods Patusa are thrown away and redrawn.

    Thunderfuck's rule for an EMPTY rack applies here for the same reason: this
    card exchanges what is in PLAY, and before the first selection there is
    nothing in play to exchange.  Seeding the rack with a mod nobody chose is
    what N86 exists to prevent, so an empty rack resolves the card, discards it
    and says why (N99).
    """
    if not ctx.state.active_mods:
        return Plan(
            (Fizzle("Rage Quit: żaden Mod Patusa nie jest aktywny"),),
            "bez efektu",
        )
    return Plan(
        (ReplaceMods(deck_id=str(spec.get("deck", "mods"))),),
        "Wymiana Modów Patusa",
    )


@effect("reverse_movement")
def _reverse_movement(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Gambit Patusa: next round, every movement card runs the other way.

    NOT immediately — ``delay_rounds`` is 1 — and for exactly one round, after
    which it lapses on its own.  Both facts live in the payload as a round
    NUMBER rather than in an expiry, because statuses expire by TURN and a
    round is a variable number of turns: Piotrek takes every third slot, so
    rounds differ in length and "one round" is not a number of turns at all.

    Granted with ``stack=True``.  Replacing would let a second Gambit, played
    DURING the round the first one reversed, cancel the reversal it is being
    played under — the two are separate promises about two separate rounds.
    """
    state = ctx.state
    delay = max(0, int(spec.get("delay_rounds", 1)))
    target = state.round_number + delay
    return Plan(
        (GrantStatus(
            Status.for_table(
                StatusKind.MOVEMENT_REVERSED,
                data={"round": target},
                source=ctx.source,
            ),
            stack=True,
        ),),
        f"Runda {target}: karty ruchu działają odwrotnie",
    )


@effect("manual")
def _manual(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """A card whose rule the PLAYERS carry out, not the engine.

    This is the difference between "not designed yet" and "unplayable".  A card
    with no ``effect`` at all is not playable — ``Card.is_playable`` asks
    exactly that — so a Chest card waiting on a ruling used to sit in the hand
    refusing to be clicked, and a player holding two of them was holding two
    dead cards at the chest limit.

    So an undesigned card declares ``manual`` and behaves like every other:
    it shows, it resolves to nothing, it goes to the discard pile, and the text
    on its face reaches the status bar so the table knows what to do about it.
    Replacing this with a real handler later is a JSON edit and a function; it
    is NOT a stub in the sense N10 forbids, because nothing here pretends the
    rule was applied.
    """
    text = str(spec.get("text", "")).strip()
    if not text and ctx.card_uid is not None:
        card = ctx.state.find_card(ctx.card_uid)
        text = str(getattr(card, "text", "") or "").strip()
    if not text:
        text = ctx.source or "Ta karta jest rozliczana przy stole"
    return Plan((Fizzle(text),), text)


# ── Gejtos ───────────────────────────────────────────────────────────────────
# One card, two rules that are mirror images, so they share their reading of
# the board and differ only in where the neighbours end up.
def _neighbour_positions(state, centre: int) -> List[int]:
    """The positions immediately in front of and behind a field.

    Ahead first, so the questions come in a stable order and the keys below
    mean the same thing on every machine.  The camp is not a neighbour of
    anything here: Gejtos gathers what is ON THE ROAD, and a pawn that has not
    started yet is not one field behind the first one — it is not on the board.
    """
    out = []
    for offset in (1, -1):
        index = centre + offset
        if 0 <= index <= state.board.last_position:
            out.append(index)
    return out


def _pick_half(state, position_index: int, ctx: EffectContext, key: str,
               prompt: str, description: str) -> Resolution:
    """Which field of a widened row this rule means, asking when it is two.

    Returns the chosen :class:`Tile`, or the unanswered :class:`Choice`.  A row
    with one field answers itself and consumes no key at all, so a board with no
    widened rows asks Gejtos nothing.
    """
    position = state.board.position(position_index)
    if position is None or not position.tiles:
        return None
    if not position.is_doubled:
        return position.tiles[0]
    answer = ctx.choice(key)
    valid = {tile.index: tile for tile in position.tiles}
    if answer is not None and int(answer) in valid:
        return valid[int(answer)]
    return Choice(
        key=key, kind="tile", prompt=prompt,
        options=tuple(ChoiceOption(id=str(t.index), label=t.label, tile=t.index)
                      for t in position.tiles),
        description=description,
    )


def _gejtos_centre(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Which pawn the card is played on.  Both halves ask this the same way."""
    answer = ctx.choice("pawn")
    if answer:
        if ctx.state.library.pawn(answer) is None:
            raise EffectError(f"Efekt wskazuje nieznany pionek: {answer!r}")
        return answer
    if not ctx.can_ask:
        return NotAvailable("Gejtos potrzebuje wskazanego pionka")
    options = pawn_options(ctx.state)
    if not options:
        return Refusal("Nie ma pionka, na którym można zagrać tę kartę")
    return Choice(key="pawn", kind="pawn", prompt="Wybierz pionka",
                  options=options,
                  description=str(spec.get("prompt", "")) or None)


def _gejtos_option(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Mężczyzna or Kobieta — asked FIRST, because it changes everything after.

    The two halves ask about different fields (Kobieta needs a destination
    beyond each neighbour, Mężczyzna does not), so the option cannot be settled
    after the pawn without the widened-row questions changing meaning halfway
    through a resubmission.
    """
    answer = ctx.choice("option")
    if answer in ("gather", "scatter"):
        return answer
    if not ctx.can_ask:
        return NotAvailable("Gejtos potrzebuje wybranej opcji")
    return Choice(
        key="option", kind="option", prompt="Gejtos — wybierz opcję",
        options=(
            ChoiceOption(id="gather", label="Mężczyzna"),
            ChoiceOption(id="scatter", label="Kobieta"),
        ),
        description="Mężczyzna przyciąga sąsiadów, Kobieta ich odpycha",
    )


@effect("gejtos")
def _gejtos(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Gather the neighbouring fields onto a pawn, or push them one further out.

    THE CHOSEN PAWN NEVER MOVES under either half.  It is the anchor the rule
    is measured from, so moving it would change the neighbours mid-resolution.

    Mężczyzna transfers each neighbouring stack ONTO the anchor's field, whole
    and in order, landing on its head.  Kobieta sends each neighbouring stack
    one field FURTHER AWAY — the one in front goes forward, the one behind goes
    back — which is the same move mirrored.

    Kobieta REFUSES rather than clamping when the pawns behind would be pushed
    off the front of the board.  Every other backward move in the game clamps at
    field one, and that is right for a card that says "move back": arriving at
    the start is a legal outcome.  Here it is not a move at all, the card says
    so, and a card that silently did three quarters of its rule would be worse
    than one that would not be played.
    """
    state = ctx.state

    option = _gejtos_option(spec, ctx)
    if isinstance(option, (Choice, Refusal, NotAvailable)):
        return option

    centre_pawn = _gejtos_centre(spec, ctx)
    if isinstance(centre_pawn, (Choice, Refusal, NotAvailable)):
        return centre_pawn

    name = pawn_name(state, centre_pawn)
    if is_hidden(state, centre_pawn):
        return Plan((Fizzle(f"Shady: pionek {name} zniknął z mapy", centre_pawn),),
                    f"{name}: poza mapą")
    centre = pawn_index(state, centre_pawn)
    if centre == CAMP_INDEX:
        return Refusal(f"Pionek {name} jeszcze nie wystartował")

    gather = option == "gather"
    # The anchor's own field needs no question: the pawn is STANDING on it, so
    # which half of a widened row it occupies is a fact rather than a choice.
    # Only the neighbours are ambiguous.
    anchor = state.board.pawn_tile(centre_pawn)
    if anchor is None:
        return Refusal(f"Pionek {name} stoi poza planszą")

    operations: List[Operation] = []
    moved: List[str] = []
    for step, neighbour in enumerate(_neighbour_positions(state, centre)):
        ahead = neighbour > centre
        side = "przed" if ahead else "za"
        source = _pick_half(
            state, neighbour, ctx, f"from{step}",
            f"Wybierz pole {side} pionkiem {name}",
            "z którego pola ruszą pionki",
        )
        if isinstance(source, (Choice, Refusal, NotAvailable)):
            return source
        if source is None or not source.stack:
            continue

        if gather:
            destination = anchor
        else:
            beyond = neighbour + (1 if ahead else -1)
            if beyond < 0:
                # "przed polem 1" — the start area.  The whole card is refused,
                # not just this side of it: it is one effect and it either
                # happens or does not.
                return Refusal(
                    "Kobieta zepchnęłaby pionki przed pole 1 — nie można zagrać")
            if beyond > state.board.last_position:
                # Off the far end is the finish, which every other movement
                # rule clamps to.  Clamping here keeps Gejtos consistent with
                # the rest of the game at the only edge the card does not name.
                beyond = state.board.last_position
            destination = _pick_half(
                state, beyond, ctx, f"to{step}",
                f"Wybierz pole, na które trafią pionki {side} {name}",
                "dokąd zostaną odepchnięte",
            )
            if isinstance(destination, (Choice, Refusal, NotAvailable)):
                return destination
            if destination is None:
                continue

        if destination.index == source.index:
            continue
        operations.append(TransferStack(
            from_tile=source.index, to_tile=destination.index,
        ))
        moved.extend(source.stack)

    if not operations:
        return Plan(
            (Fizzle(f"Gejtos: {name} nie ma sąsiadów"),),
            f"{name}: brak sąsiadów",
        )
    which = "Mężczyzna" if gather else "Kobieta"
    return Plan(
        tuple(operations),
        f"Gejtos ({which}) — {name}: {len(moved)} "
        f"{'pionek' if len(moved) == 1 else 'pionki'}",
    )


# ── Gamechanger ──────────────────────────────────────────────────────────────
@effect("swap_identity")
def _swap_identity(spec: EffectSpec, ctx: EffectContext) -> Resolution:
    """Alter Ego: Piotrek gives up the colour he is hiding behind for a new one.

    THIS HANDLER NEVER TOUCHES THE COLOUR, and cannot.  It runs on every
    replica to build the plan, and every replica but the authority's and
    Piotrek's own holds ``None`` for the secret all match (N72/N73) — so a
    handler that read it would build a different plan on different machines and
    desync the table on the one card that must not.

    All it does is raise a flag that names nobody.  The authority answers it
    through ``victory.review``, the same hook that decides an elimination, and
    the answer comes back as an ordinary logged, broadcast command.

    ONLY PIOTREK MAY PLAY IT.  The card is dealt from the Chest like any other
    and a hunter can end up holding one, so the refusal is a real rule rather
    than a formality — and it is a Refusal, not a Fizzle: the card stays in the
    hand to be played by whoever it belongs to.
    """
    state = ctx.state
    actor = state.player(ctx.actor)
    if actor is None or not actor.is_piotrek:
        return Refusal("Tylko Piotrek może zmienić tożsamość")
    if state.piotrek_seat is None:
        return Refusal("Przy tym stole nie ma Piotrka")
    if state.identity_swap:
        return Refusal("Zmiana tożsamości już trwa")
    return Plan((RequestIdentitySwap(),), "Alter Ego — nowa tożsamość Piotrka")
