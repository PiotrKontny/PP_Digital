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
            if isinstance(op, MovePawn):
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


def _speedrun_reversal(spec: EffectSpec, ctx: EffectContext,
                       key: str = "speedrun") -> Resolution:
    """Ask whether a backward card should be turned around (Speedrun).

    Returns ``True`` when the player chose to go forward, ``False`` to keep the
    printed direction, or a :class:`Choice` when nobody has been asked yet.

    ONLY cards that naturally move backwards ask.  ``direction: "either"`` does
    not count as backward: those cards already let the player pick the way, and
    a second question about the same decision would be asked and answered
    twice.  A forward card never asks at all.

    Callers must resolve this BEFORE the pawn question — the order of the
    prompts is part of the rules: direction, then pawn, then which half of a
    widened row.
    """
    if not spec.is_backward or not ctx.state.reverses_backward_moves:
        return False
    if not ctx.can_ask:
        # Nobody to ask (a card played by another card).  Speedrun only ever
        # OFFERS a reversal, so declining is always a legal answer and the card
        # does what it says on its face.
        return False
    answer = ctx.choice(key)
    if answer in ("forward", "backward"):
        return answer == "forward"
    return Choice(
        key=key, kind="option",
        prompt="Speedrun — wybierz kierunek",
        options=(
            ChoiceOption(id="backward", label="Do tyłu"),
            ChoiceOption(id="forward", label="Do przodu"),
        ),
        description="ta karta cofa pionki, ale Speedrun pozwala ją odwrócić",
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

    # 1) Speedrun — before the pawn question.
    reversed_direction = _speedrun_reversal(spec, ctx)
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

    # Masa solna shortens the card, Speedrun may turn it around.  Distance
    # first, then direction, so the two never fight over the sign.
    steps = _capped_steps(ctx, steps)
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

    # Speedrun first, exactly as for a single-pawn card: the direction question
    # comes before the pawn question.  Plagiat! moves backwards, so it is a
    # card Speedrun turns around — it reaching a different handler is an
    # implementation detail and must not make it behave differently.
    reversed_direction = _speedrun_reversal(spec, ctx)
    if isinstance(reversed_direction, (Choice, Refusal, NotAvailable)):
        return reversed_direction

    steps = _capped_steps(ctx, spec.signed_steps)
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
