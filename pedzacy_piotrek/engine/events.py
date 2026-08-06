"""
Events — what the engine announces after something happened.

The rendering layer never asks the state "did anything change?"; it is told.
That is what makes animations, sounds and (later) network broadcasts possible
without sprinkling calls to the renderer through the rules code: the engine
emits ``TokenMoved`` and *something else* decides whether that means a tween,
a puff of dust, a sound effect, or a packet.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, DefaultDict, Dict, List, Optional, Tuple, Type


@dataclass
class GameEvent:
    """Base class for everything the engine reports."""

    @property
    def name(self) -> str:
        return type(self).__name__


# ── card flow ────────────────────────────────────────────────────────────────
@dataclass
class CardDrawn(GameEvent):
    player_index: int
    deck_id: str
    card_uid: int


@dataclass
class CardDiscarded(GameEvent):
    player_index: int
    deck_id: str
    card_uid: int


@dataclass
class CardPlayed(GameEvent):
    """A card was played and its effect carried out.

    Carries enough to animate and to fill the 'ostatnio zagrane' strip without
    the listener having to look anything up.
    """

    player_index: int
    deck_id: str
    card_uid: int
    title: str
    description: str = ""


@dataclass
class DeckReshuffled(GameEvent):
    deck_id: str


@dataclass
class ModPlaced(GameEvent):
    player_index: int
    slot: int
    card_uid: int
    displaced_uid: Optional[int] = None


@dataclass
class ModDiscarded(GameEvent):
    slot: int
    card_uid: int


@dataclass
class ModSelectionStarted(GameEvent):
    """The round paused: both factions now choose a Mod Patusa.

    Two lists of candidates, because the two factions choose from different
    cards.  Piotrek's are secret and the hunters' are not; keeping them in one
    event is safe because every machine still only DRAWS what its own seat is
    entitled to see, exactly as the chest limit does.
    """

    round_number: int
    piotrek_seat: Optional[int] = None
    piotrek_uids: List[int] = field(default_factory=list)
    hunter_uids: List[int] = field(default_factory=list)
    hunter_seats: List[int] = field(default_factory=list)


@dataclass
class ModVoteCast(GameEvent):
    """A hunter voted, or changed their vote.  Everybody sees this."""

    player_index: int
    card_uid: int
    #: uid → number of votes, after this vote landed.
    tally: Dict[int, int] = field(default_factory=dict)
    voted: int = 0
    voters: int = 0


@dataclass
class ModSelectionResolved(GameEvent):
    """One side settled on a card, which is now in its slot.

    ``faction`` is "piotrek" or "hunters"; the slot is 0 for Piotrek (LEFT) and
    1 for the hunters (RIGHT).  ``discarded_uids`` are the candidates that lost.
    """

    faction: str
    slot: int
    card_uid: int
    title: str = ""
    discarded_uids: List[int] = field(default_factory=list)
    tie_broken: bool = False


@dataclass
class ModSelectionFinished(GameEvent):
    """Both factions have chosen; the table may move again."""

    round_number: int


@dataclass
class CharacterChanged(GameEvent):
    player_index: int
    title: Optional[str]


@dataclass
class SkillChanged(GameEvent):
    player_index: int
    title: Optional[str]


# ── board ────────────────────────────────────────────────────────────────────
@dataclass
class TokenMoved(GameEvent):
    pawn_id: str
    from_position: tuple[float, float]
    to_position: tuple[float, float]
    tile_index: Optional[int]
    carried: List[str] = field(default_factory=list)
    snapped: bool = False


@dataclass
class TokenWalked(GameEvent):
    """A pawn travelled along the road, field by field.

    ``waypoints`` are world positions in visiting order, so the view can walk
    the pawn through every space instead of teleporting it.  ``carried`` are the
    pawns riding on top, which follow the same waypoints.
    """

    pawn_id: str
    from_index: int
    #: Board positions visited, in order (a doubled position counts once).
    route: List[int] = field(default_factory=list)
    #: The concrete field visited at each of those positions.
    tiles: List[int] = field(default_factory=list)
    waypoints: List[tuple[float, float]] = field(default_factory=list)
    carried: List[str] = field(default_factory=list)
    backward: bool = False


@dataclass
class MoveFizzled(GameEvent):
    """A movement card resolved and moved nobody, on purpose.

    Halloween's pinned pawns are the first case.  Distinct from
    ``ActionRejected`` because nothing was rejected: the card was played, it is
    on the discard pile and the turn has passed.  The view says so rather than
    leaving the player to wonder whether the click registered.
    """

    reason: str
    pawn_id: str = ""


@dataclass
class ChoiceRequired(GameEvent):
    """An action is waiting for a decision: which pawn, which field, how far.

    Emitted so the interface can put itself into choice mode from the event
    stream rather than from the click that caused it — which is what will let a
    spectator or a future remote client see that the table is waiting.

    ``key`` is where the answer belongs in the action's ``choices`` map and
    ``kind`` says what is being picked ("pawn", "tile" or "option"), so one
    modal handles every effect that ever needs to ask something.
    """

    player_index: int
    key: str
    kind: str
    prompt: str
    #: (id, label) pairs — ids go straight back into ``choices``.
    options: List[tuple] = field(default_factory=list)
    #: Board fields to highlight, when the decision is about fields.
    tiles: List[int] = field(default_factory=list)
    #: Pawns to highlight, when the decision is about pawns.
    pawns: List[str] = field(default_factory=list)
    #: Cards to lay out, when the decision is about cards (Spy).
    card_options: List[int] = field(default_factory=list)
    #: How many things must be picked, and whether the order matters.  One and
    #: False is the ordinary single answer; anything else turns the prompt into
    #: a multi-select whose answer travels as the ids joined by commas.
    count: int = 1
    ordered: bool = False
    #: Whose cards are being shown, for a card question.  This event goes to the
    #: asking player alone (N40), which is what keeps the hand hidden.
    owner: Optional[int] = None
    #: The action that is waiting, so the interface can resubmit it.
    card_uid: Optional[int] = None
    ability_source: Optional[str] = None
    answered: Dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass
class StatusGranted(GameEvent):
    """A persistent gameplay state started."""

    kind: str
    subject: str
    subject_id: str
    label: str
    source: str = ""
    expires_after_turn: Optional[int] = None


@dataclass
class StatusEnded(GameEvent):
    kind: str
    subject: str
    subject_id: str
    label: str = ""


@dataclass
class AbilityUsed(GameEvent):
    """A character ability or Piotrek skill was activated."""

    player_index: int
    title: str
    description: str
    uses_left: Optional[int] = None
    source: str = "character"


@dataclass
class AbilityUnavailable(GameEvent):
    """An ability exists but the rules it needs do not."""

    player_index: int
    title: str
    reason: str


@dataclass
class CardRevealed(GameEvent):
    """A card was turned face up by another card (Seks z pedałami).

    ``announce_seconds`` is how long the interface should dwell on the card
    that caused it before showing this one.
    """

    player_index: int
    deck_id: str
    card_uid: int
    title: str
    text: str = ""
    announce_seconds: float = 2.0


@dataclass
class CardTransformed(GameEvent):
    """A drawn card becomes something else on its way to the hand (Gamechanger).

    The interface plays the reveal; the card that lands in the hand is the one
    named by ``title``.
    """

    player_index: int
    card_uid: int
    from_title: str
    to_title: str
    to_text: str
    intro_text: str = ""
    delay: float = 1.0


@dataclass
class CardSpotlighted(GameEvent):
    """Hold a card up for a few seconds before what it does happens.

    Emitted when the game takes a turn over: Troll's forced play and Stańczyk's
    skipped turn both point at a card the player did not choose, and the player
    is entitled to see which one before the board moves under them.

    The state does NOT wait for it (N36) — by the time this is read the card has
    already resolved.  ``seconds`` is how long the view should dwell, and it is
    also how long the view holds back the walk that follows, so what the player
    watches is: card, pause, movement.
    """

    player_index: int
    deck_id: str
    card_uid: int
    title: str
    text: str = ""
    seconds: float = 2.0
    caption: str = ""
    #: True when the game chose the card, false when it merely names the card
    #: that caused what is about to happen.
    forced: bool = False


@dataclass
class TurnSkipped(GameEvent):
    """A seat lost its move — Stańczyk, Lubin, Dziubdziuch."""

    player_index: int
    source: str = ""


@dataclass
class CardStolen(GameEvent):
    """A card changed hands (Spy).

    Names the seats and the uid and NOT the title: this is broadcast to
    everybody, and only the two players involved are allowed to know what moved.
    A client that holds the card can look the title up in its own hand; one that
    does not, cannot.
    """

    from_player: int
    to_player: int
    card_uid: int
    deck_id: str


@dataclass
class CardDrawEffect(GameEvent):
    """A card did something the moment it was drawn (Troll, Stańczyk)."""

    player_index: int
    card_uid: int
    title: str
    description: str = ""


@dataclass
class ChestCardAwarded(GameEvent):
    """A chest card was handed out automatically at the start of a round."""

    player_index: int
    card_uid: int
    round_number: int


@dataclass
class ChestLimitReached(GameEvent):
    """More chest cards than the player may hold: they must choose.

    Carries every candidate so the interface can lay them out side by side.
    """

    player_index: int
    limit: int
    card_uids: List[int] = field(default_factory=list)
    new_card_uid: Optional[int] = None


@dataclass
class TokenPickedUp(GameEvent):
    pawn_id: str


# ── flow ─────────────────────────────────────────────────────────────────────
@dataclass
class RoundChanged(GameEvent):
    round_number: int


@dataclass
class ActivePlayerChanged(GameEvent):
    player_index: int


@dataclass
class PlayerRenamed(GameEvent):
    """A player changed their display name.

    The field is ``new_name`` and not ``name`` because ``GameEvent.name`` is a
    read-only property giving the event's class name.  A dataclass field called
    ``name`` generates an ``__init__`` that assigns straight over it, so every
    rename raised ``property 'name' has no setter`` and came back as a refusal.
    Nothing caught it because renaming was the one player action with no test.
    """

    player_index: int
    new_name: str


@dataclass
class MarkToggled(GameEvent):
    player_index: int
    pawn_id: str
    marked: bool


# ── the match itself ─────────────────────────────────────────────────────────
@dataclass
class MatchBegan(GameEvent):
    """Piotrek has chosen; the table is live.  Everyone gets this at once."""


@dataclass
class PawnEliminated(GameEvent):
    """A checked colour turned out not to be Piotrek's.

    Every notepad crosses it off, on every machine, because everybody watched
    the same tower being lifted.
    """

    pawn_id: str


@dataclass
class LeadCheckAnnounced(GameEvent):
    """Squid Game looked at the front of the field at the start of a round.

    Emitted on every machine, including when the check is SKIPPED — a round
    where nothing happens because two pawns are level looks identical to a
    broken mod otherwise, and the players deserve to be told which it was.

    The verdict does not travel with it.  This says who is being checked; what
    that turns out to mean arrives separately, as the ordinary
    :class:`PawnEliminated` or :class:`MatchEnded`, because only the authority
    can decide it.
    """

    pawn_id: str = ""
    skipped: bool = False


@dataclass
class PawnHidden(GameEvent):
    """A pawn left the map for a round (Shady)."""

    pawn_id: str
    riders: List[str] = field(default_factory=list)
    round_number: int = 0


@dataclass
class PawnRestored(GameEvent):
    """A hidden pawn came back, on top of the pawn furthest to the rear.

    ``tile_index`` is ``None`` when there was nobody on the road to stand on
    and the pawn went back to the camp instead.
    """

    pawn_id: str
    tile_index: Optional[int] = None
    onto: str = ""


@dataclass
class ChestHolding(GameEvent):
    """One player's Chest cards, as shown by Paczka."""

    player_index: int
    player_name: str
    titles: List[str] = field(default_factory=list)


@dataclass
class ChestCardsRevealed(GameEvent):
    """Paczka reached the rack: every Chest card is now public.

    Carries card TITLES to the whole table, which every other event in this
    game is forbidden to do (N81).  That is the mod's entire text rather than
    an oversight — the Chest stops being hidden information while Paczka is in
    play — and it is the only event allowed the exception.

    Every machine builds the same list from its own replica and shows its own
    window, dismissed independently.  Nothing about the game changes.
    """

    holdings: List[ChestHolding] = field(default_factory=list)


@dataclass
class MatchEnded(GameEvent):
    """Somebody won.  Carries the reveal, because the secret is now public."""

    outcome: str
    pawn_id: str
    piotrek_seat: int
    piotrek_name: str = ""


@dataclass
class ActionRejected(GameEvent):
    """A command could not be applied.  Carries a message fit for the status bar."""

    reason: str
    command: str = ""


Listener = Callable[[GameEvent], None]


class EventBus:
    """Minimal synchronous pub/sub.

    Subscribe to a concrete event class for targeted handling, or to
    :class:`GameEvent` to receive everything (the network layer does that).
    """

    def __init__(self) -> None:
        self._listeners: DefaultDict[Type[GameEvent], List[Listener]] = defaultdict(list)
        self._history: List[GameEvent] = []
        self.history_limit = 200

    def subscribe(self, event_type: Type[GameEvent], listener: Listener) -> Callable[[], None]:
        self._listeners[event_type].append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners[event_type]:
                self._listeners[event_type].remove(listener)

        return unsubscribe

    def emit(self, event: GameEvent) -> None:
        self._history.append(event)
        if len(self._history) > self.history_limit:
            del self._history[: len(self._history) - self.history_limit]
        for event_type, listeners in self._listeners.items():
            if isinstance(event, event_type):
                for listener in list(listeners):
                    listener(event)

    def emit_all(self, events: List[GameEvent]) -> None:
        for event in events:
            self.emit(event)

    @property
    def history(self) -> List[GameEvent]:
        return list(self._history)

    def clear(self) -> None:
        self._listeners.clear()
        self._history.clear()
