"""
Round structure.

Ported from ``_compute_round_turn_order`` / ``_chest_recipient_for_round`` with
identical behaviour, now pure functions over plain lists so they can be tested
and reasoned about without a running game.

The cadence comes straight from the design document:

    Piotrek -> Gracz 1 -> Gracz 2 -> Piotrek -> Gracz 3 -> Gracz 1 -> Piotrek

Piotrek acts on every third slot; the hunters cycle continuously through a
fixed order settled at game start.  A round ends the moment every hunter has
appeared in it once — which is why rounds are not all the same length, and why
the same hunter can act twice in one round.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..config.settings import RULES


@dataclass(frozen=True)
class TurnSlot:
    """One entry in the round's turn-order map."""

    name: str
    is_piotrek: bool


def compute_round_turn_order(
    round_num: int,
    piotrek_name: Optional[str],
    hunter_names: Sequence[str],
    period: int = RULES.piotrek_turn_period,
) -> List[TurnSlot]:
    """The ordered slots of a single 1-indexed round."""
    n = len(hunter_names)
    if n == 0:
        return [TurnSlot(piotrek_name, True)] if piotrek_name else []
    if piotrek_name is None:
        # No Piotrek in this game — plain round-robin through everyone once.
        return [TurnSlot(name, False) for name in hunter_names]

    pos = 0
    hunter_cursor = 0
    current_round = 1
    seen: set[str] = set()
    sequence: List[TurnSlot] = []

    while True:
        is_piotrek_slot = (pos % period == 0)
        if is_piotrek_slot:
            slot = TurnSlot(piotrek_name, True)
        else:
            name = hunter_names[hunter_cursor % n]
            hunter_cursor += 1
            slot = TurnSlot(name, False)
            seen.add(name)
        if current_round == round_num:
            sequence.append(slot)
        pos += 1
        if not is_piotrek_slot and len(seen) == n:
            if current_round == round_num:
                break
            current_round += 1
            seen = set()

    return sequence


def chest_recipient_for_round(
    round_num: int, chest_open_round: int, hunter_names: Sequence[str]
) -> Optional[str]:
    """Which hunter draws from the chest this round.

    Before the chest opens, this previews the first hunter in the order — the
    one who will receive it on the opening round anyway.
    """
    if not hunter_names:
        return None
    n = len(hunter_names)
    index = 0 if round_num <= chest_open_round else (round_num - chest_open_round) % n
    return hunter_names[index]


def round_length(
    round_num: int,
    piotrek_name: Optional[str],
    hunter_names: Sequence[str],
    period: int = RULES.piotrek_turn_period,
) -> int:
    return len(compute_round_turn_order(round_num, piotrek_name, hunter_names, period))


@dataclass(frozen=True)
class NextTurn:
    """Where the turn cursor lands next: the seat, its round, and its SLOT.

    The slot travels with the seat deliberately.  A seat can hold several slots
    in one round — Piotrek holds every third — so ``order.index(seat)`` cannot
    recover it: that finds the first slot the seat occupies and rewinds the
    round to its beginning.  Returning the slot is what makes it impossible for
    a caller to reconstruct it wrongly.
    """

    seat: int
    round_number: int
    slot: int
