"""
Gameplay states.

Cards and abilities do not set one-off flags on the game state.  They attach a
:class:`Status` to a subject — a pawn, a player, or the table — and every rule
that cares reads the tracker.  Adding "frozen" as ``state.frozen_pawn = ...``
would have worked for exactly one ability; this works for all of them, and the
expiry, the display and the serialisation are written once.

The pieces:

* :class:`StatusKind` — what the status *is*.  New kinds are one enum entry.
* :class:`Status` — one live effect: kind, subject, payload, expiry.
* :class:`StatusTracker` — the collection, with queries the rules ask
  ("is this pawn frozen?", "what is this pawn linked to?").

Expiry is measured in **turns**, counted by ``GameState.turn_counter``, which
advances whenever the active seat changes.  "For one full turn" is therefore
``duration_turns=1``, and a status with no expiry stays until something clears
it.  Nothing expires by wall-clock time: the engine must reach the same state
on every machine from the same list of commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


class StatusKind(str, Enum):
    """Every persistent gameplay state.  Add new mechanics here, not as flags."""

    FROZEN = "frozen"                    # a pawn may not move
    SKIP_TURN = "skip_turn"              # a player loses their next move
    LINKED = "linked"                    # two pawns move (and check) together
    EXTRA_TURN = "extra_turn"            # a player gets an additional move
    MOVEMENT_BONUS = "movement_bonus"    # next movement card reaches further
    RESTRICTED_MOVEMENT = "restricted"   # movement confined to a span of fields
    FORBIDDEN_ADJACENCY = "forbidden_adjacency"   # two pawns may not neighbour
    CHECK_REFUSAL = "check_refusal"      # a check may be declined (Ice Block)
    TURN_INTERRUPT = "turn_interrupt"    # a card takes the player's next turn over
    HIDDEN = "hidden"                    # a pawn is off the map entirely (Shady)
    MOVEMENT_REVERSED = "movement_reversed"   # movement cards run the other way


class Subject(str, Enum):
    """What a status is attached to."""

    PAWN = "pawn"
    PLAYER = "player"
    TABLE = "table"


@dataclass
class Status:
    """One live gameplay state."""

    kind: StatusKind
    subject: Subject = Subject.TABLE
    #: Pawn id or player index, as a string; empty for table-wide statuses.
    subject_id: str = ""
    #: Free-form payload — the partner of a link, the size of a bonus, and so on.
    data: Dict[str, Any] = field(default_factory=dict)
    #: Turn counter value after which this status is gone.  ``None`` = manual.
    expires_after_turn: Optional[int] = None
    #: How many times it can still fire, for statuses that are spent by use
    #: rather than by time (a movement bonus is consumed by one card).
    charges: Optional[int] = None
    #: Human-readable origin, shown in the interface ("Granny Costume").
    source: str = ""

    def is_expired(self, turn: int) -> bool:
        if self.charges is not None and self.charges <= 0:
            return True
        return self.expires_after_turn is not None and turn > self.expires_after_turn

    def matches(self, subject: Subject, subject_id: str) -> bool:
        return self.subject is subject and self.subject_id == str(subject_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject.value,
            "subject_id": self.subject_id,
            "data": dict(self.data),
            "expires_after_turn": self.expires_after_turn,
            "charges": self.charges,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Status":
        return cls(
            kind=StatusKind(raw["kind"]),
            subject=Subject(raw.get("subject", "table")),
            subject_id=str(raw.get("subject_id", "")),
            data=dict(raw.get("data") or {}),
            expires_after_turn=raw.get("expires_after_turn"),
            charges=raw.get("charges"),
            source=raw.get("source", ""),
        )

    # ── constructors for the common shapes ──────────────────────────────────
    @classmethod
    def for_pawn(cls, kind: StatusKind, pawn_id: str, **kwargs) -> "Status":
        return cls(kind=kind, subject=Subject.PAWN, subject_id=pawn_id, **kwargs)

    @classmethod
    def for_player(cls, kind: StatusKind, player_index: int, **kwargs) -> "Status":
        return cls(kind=kind, subject=Subject.PLAYER,
                   subject_id=str(player_index), **kwargs)

    @classmethod
    def for_table(cls, kind: StatusKind, **kwargs) -> "Status":
        return cls(kind=kind, subject=Subject.TABLE, **kwargs)


class StatusTracker:
    """Every status currently in play, with the queries the rules need."""

    def __init__(self) -> None:
        self._statuses: List[Status] = []

    # ── mutation ─────────────────────────────────────────────────────────────
    def add(self, status: Status, replace: bool = True) -> Status:
        """Attach a status.  By default it replaces one of the same kind on the
        same subject, so using an ability twice refreshes rather than stacks."""
        if replace:
            self._statuses = [
                s for s in self._statuses
                if not (s.kind is status.kind
                        and s.matches(status.subject, status.subject_id))
            ]
        self._statuses.append(status)
        return status

    def remove(self, kind: StatusKind, subject: Subject, subject_id: str = "") -> int:
        before = len(self._statuses)
        self._statuses = [
            s for s in self._statuses
            if not (s.kind is kind and s.matches(subject, str(subject_id)))
        ]
        return before - len(self._statuses)

    def discard(self, status: Status) -> bool:
        """Drop ONE particular status object.

        :meth:`remove` takes everything of a kind on a subject, which is wrong
        for statuses that legitimately stack: a player holding two Trolls has
        two turn interrupts queued and resolving the first must not silently
        cancel the second.
        """
        for index, existing in enumerate(self._statuses):
            if existing is status:
                del self._statuses[index]
                return True
        return False

    def clear_kind(self, kind: StatusKind) -> int:
        before = len(self._statuses)
        self._statuses = [s for s in self._statuses if s.kind is not kind]
        return before - len(self._statuses)

    def spend_charge(self, status: Status) -> None:
        if status.charges is not None:
            status.charges -= 1
        if status.is_expired(-1) or (status.charges is not None and status.charges <= 0):
            if status in self._statuses:
                self._statuses.remove(status)

    def expire(self, turn: int) -> List[Status]:
        """Drop everything whose time is up.  Returns what was removed."""
        gone = [s for s in self._statuses if s.is_expired(turn)]
        if gone:
            self._statuses = [s for s in self._statuses if not s.is_expired(turn)]
        return gone

    def clear(self) -> None:
        self._statuses.clear()

    # ── queries ──────────────────────────────────────────────────────────────
    def all(self) -> List[Status]:
        return list(self._statuses)

    def of_kind(self, kind: StatusKind) -> List[Status]:
        return [s for s in self._statuses if s.kind is kind]

    def find(self, kind: StatusKind, subject: Subject,
             subject_id: str = "") -> Optional[Status]:
        for status in self._statuses:
            if status.kind is kind and status.matches(subject, str(subject_id)):
                return status
        return None

    def on_pawn(self, pawn_id: str) -> List[Status]:
        return [s for s in self._statuses if s.matches(Subject.PAWN, pawn_id)]

    def on_player(self, player_index: int) -> List[Status]:
        return [s for s in self._statuses if s.matches(Subject.PLAYER, str(player_index))]

    def pawn_has(self, kind: StatusKind, pawn_id: str) -> bool:
        return self.find(kind, Subject.PAWN, pawn_id) is not None

    def player_has(self, kind: StatusKind, player_index: int) -> bool:
        return self.find(kind, Subject.PLAYER, str(player_index)) is not None

    def interrupts_for(self, player_index: int) -> List[Status]:
        """Turn interrupts queued against a seat, oldest first.

        They STACK rather than replace, because "you drew a second Troll" is a
        second forced turn, not a re-run of the first one.  Order is the order
        they were granted, which is the same on every machine because they all
        replay the same commands.
        """
        return [
            status for status in self._statuses
            if status.kind is StatusKind.TURN_INTERRUPT
            and status.matches(Subject.PLAYER, str(player_index))
        ]

    def linked_partners(self, pawn_id: str) -> List[str]:
        """Pawns that travel with this one because of a link.

        A link is stored once, on both pawns, so either end can be moved and
        both find the other.
        """
        partners: List[str] = []
        for status in self.of_kind(StatusKind.LINKED):
            members = [str(m) for m in status.data.get("members", [])]
            if pawn_id in members:
                partners.extend(m for m in members if m != pawn_id)
        return partners

    def movement_bonus_for(self, player_index: int) -> Tuple[int, Optional[Status]]:
        """Extra range the next movement card gets, and the status granting it."""
        status = self.find(StatusKind.MOVEMENT_BONUS, Subject.PLAYER, str(player_index))
        if status is None:
            return 0, None
        return int(status.data.get("amount", 1)), status

    def movement_reversed_in(self, round_number: int) -> bool:
        """Whether a Gambit Patusa turns every movement card around this round.

        Round-scoped rather than turn-scoped, and that is the whole reason it
        cannot use :attr:`Status.expires_after_turn`: the card names a ROUND,
        and a round is a variable number of turns (Piotrek holds every third
        slot, so rounds differ in length).  The round the reversal belongs to
        is therefore carried in the payload and compared here.

        Several Gambits may be in flight at once — they are granted with
        ``stack=True`` — so this asks whether ANY of them names this round.
        Replacing instead of stacking would let a Gambit played DURING a
        reversed round cancel the reversal it was played under.
        """
        return any(int(status.data.get("round", -1)) == round_number
                   for status in self.of_kind(StatusKind.MOVEMENT_REVERSED))

    def expire_round_statuses(self, round_number: int) -> List[Status]:
        """Drop round-scoped statuses whose round is already behind us.

        Turn expiry cannot do this (see :meth:`movement_reversed_in`), so the
        round loop clears them instead.  Anything naming the CURRENT round
        survives: a reversal is granted in round N for round N+1, and
        ``_begin_round(N+1)`` must not throw it away before it has been read.
        """
        gone = [
            status for status in self._statuses
            if status.kind is StatusKind.MOVEMENT_REVERSED
            and int(status.data.get("round", -1)) < round_number
        ]
        for status in gone:
            self.discard(status)
        return gone

    def movement_range(self) -> Optional[Tuple[int, int]]:
        """Span of board positions movement is confined to, if any."""
        status = self.find(StatusKind.RESTRICTED_MOVEMENT, Subject.TABLE)
        if status is None:
            return None
        low = status.data.get("from")
        high = status.data.get("to")
        if low is None or high is None:
            return None
        return int(low), int(high)

    # ── serialisation ────────────────────────────────────────────────────────
    def to_list(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._statuses]

    def load(self, raw: Iterable[Mapping[str, Any]]) -> None:
        self._statuses = [Status.from_dict(item) for item in raw]

    def __len__(self) -> int:
        return len(self._statuses)

    def __iter__(self):
        return iter(self._statuses)


#: Short Polish labels for the interface.  Kept beside the enum so a new status
#: cannot be added without somebody deciding what players will see.
STATUS_LABELS: Dict[StatusKind, str] = {
    StatusKind.FROZEN: "Zamrożony",
    StatusKind.SKIP_TURN: "Ruch pominięty",
    StatusKind.LINKED: "Sklejone",
    StatusKind.EXTRA_TURN: "Dodatkowy ruch",
    StatusKind.MOVEMENT_BONUS: "Zasięg +1",
    StatusKind.RESTRICTED_MOVEMENT: "Ruch ograniczony",
    StatusKind.FORBIDDEN_ADJACENCY: "Zakaz sąsiedztwa",
    StatusKind.CHECK_REFUSAL: "Odmowa sprawdzenia",
    StatusKind.TURN_INTERRUPT: "Tura przejęta",
    StatusKind.HIDDEN: "Poza mapą",
    StatusKind.MOVEMENT_REVERSED: "Odwrócony ruch",
}
