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
    status: Status


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
class Announce(Operation):
    """Say something happened that has no other mechanical trace."""

    text: str


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
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Choice:
    """A decision the engine needs before it can act.

    ``key`` is where the answer goes in the action's ``choices`` dictionary, so
    an effect that needs three decisions simply asks three times.
    """

    key: str
    kind: str                       # "pawn" | "tile" | "option"
    prompt: str
    options: Tuple[ChoiceOption, ...]
    description: str = ""

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

    def choice(self, key: str) -> Optional[str]:
        value = self.choices.get(key)
        return str(value) if value is not None else None

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
) -> Resolution:
    """Resolve any effect specification against the current state."""
    if spec is None:
        return Refusal("Ta karta nie ma jeszcze zaimplementowanego efektu")
    handler = HANDLERS.get(spec.type)
    if handler is None:
        return Refusal(f"Nieznany typ efektu: {spec.type}")
    return handler(spec, EffectContext(state, actor, dict(choices or {}), source))


def resolve(
    state, card: Card, actor: int = 0,
    choices: Optional[Mapping[str, str]] = None,
) -> Resolution:
    """Resolve a card's effect."""
    return resolve_spec(state, card.effect, actor, choices, card.title)


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
                        card.skill or card.title)


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
        for op in result.routes:
            tiles.extend(op.tiles)
        return tuple(tiles)
    if isinstance(result, Choice) and result.kind == "tile":
        return result.tiles
    return ()


# ── shared helpers ───────────────────────────────────────────────────────────
def pawn_index(state, pawn_id: str) -> int:
    """Board *position* of a pawn, or :data:`CAMP_INDEX` if it has not started."""
    index = state.board.position_of_pawn(pawn_id)
    return CAMP_INDEX if index is None else int(index)


def _ordered_pawns(state) -> List[Tuple[int, int, int, str]]:
    """Every pawn as (position, stack depth, palette order, id), sorted."""
    out: List[Tuple[int, int, int, str]] = []
    for order, pawn in enumerate(state.library.pawns):
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
    return tuple(
        ChoiceOption(id=pawn.id, label=pawn.name, pawn=pawn.id)
        for pawn in state.library.pawns
        if pawn.id not in exclude
    )


def fields_word(count: int) -> str:
    """Polish plural for 'pole' — 1 pole, 2-4 pola, 5+ pól."""
    if count == 1:
        return "pole"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "pola"
    return "pól"


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
    """Move one pawn a number of positions, forwards or backwards."""
    state = ctx.state

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

    name = pawn_name(state, pawn_id)
    if state.statuses.pawn_has(StatusKind.FROZEN, pawn_id):
        return Refusal(f"Pionek {name} jest zamrożony")

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
