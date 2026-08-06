"""
How a match ends.

Two ways, and this module is the only place either of them is decided:

* **Piotrek escapes** — the pawn he secretly chose reaches the finish;
* **the hunters find him** — every pawn ends up on one field and the pawn at
  the BOTTOM of that tower is his.  A wrong tower crosses that colour off for
  good and play continues.

Like the rest of ``engine/``, this imports no pygame and knows nothing about
sockets.  It is a pure function of the state:  :func:`review` looks at a game
and answers with the commands that ought to follow.  Whoever holds the
authority — the dedicated server in an online match, the session in a hot-seat
one — is the one that calls it and the only one that may act on the answer.

WHY IT RETURNS COMMANDS RATHER THAN MUTATING.  Everything else in this game
changes through a command that is logged and broadcast, and a verdict must
travel the same road: a player who reconnects replays the log and arrives at
the same ending, and the fingerprint that catches desyncs keeps working because
the ending is part of the state every client can compute.  A victory applied
directly to the server's copy would be invisible to all of that.

WHAT IS **NOT** HERE.  The hidden colour itself.  ``review`` reads it from the
state it is given, and only the authority's copy has it: on a client
``Player.secret_pawn`` is ``None`` for everybody, which is why a client calling
this would decide nothing.  See HIDDEN IDENTITY in LLM_Instructions.txt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from ..board.tiles import Tile, TileKind
from . import commands as cmd


class MatchPhase(Enum):
    """Where a match is in its life.

    ``STARTING`` exists because Piotrek picks his colour before anybody moves:
    the table is built and everyone can see it, but no move is legal yet.  It
    is a phase rather than a flag on the screen so the engine can refuse a
    command that arrives early, whatever any client believes.
    """

    STARTING = "starting"
    PLAYING = "playing"
    ENDED = "ended"

    @property
    def playable(self) -> bool:
        return self is MatchPhase.PLAYING


class Outcome(Enum):
    PIOTREK = "piotrek"
    HUNTERS = "hunters"

    @property
    def is_piotrek(self) -> bool:
        return self is Outcome.PIOTREK


@dataclass(frozen=True)
class Verdict:
    """A finished match, and everything the reveal needs.

    The hidden colour is in here on purpose: this object only ever exists once
    the match is over, and the whole point of the ending is that it stops being
    a secret.
    """

    outcome: Outcome
    pawn_id: str
    piotrek_seat: int
    piotrek_name: str = ""

    @property
    def piotrek_won(self) -> bool:
        return self.outcome.is_piotrek

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "pawn_id": self.pawn_id,
            "piotrek_seat": self.piotrek_seat,
            "piotrek_name": self.piotrek_name,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> Optional["Verdict"]:
        try:
            outcome = Outcome(str(raw.get("outcome")))
        except ValueError:
            return None
        return cls(
            outcome=outcome,
            pawn_id=str(raw.get("pawn_id", "")),
            piotrek_seat=int(raw.get("piotrek_seat", -1) or -1),
            piotrek_name=str(raw.get("piotrek_name", "")),
        )


# ── reading the table ────────────────────────────────────────────────────────
def piotrek_seat(state) -> Optional[int]:
    """Which seat holds the Piotrek character card."""
    for player in state.players:
        if player.is_piotrek:
            return player.index
    return None


def hidden_pawn(state) -> Optional[str]:
    """The colour Piotrek is hiding behind, if this copy of the state knows it.

    ``None`` on every machine but the authority's — which is exactly what makes
    it safe to run the rest of this module anywhere.
    """
    seat = piotrek_seat(state)
    if seat is None:
        return None
    player = state.player(seat)
    return player.secret_pawn if player is not None else None


def reached_finish(state, pawn_id: str) -> bool:
    """Is that pawn standing on the finish?

    The tile kind decides rather than the number, because a board is generated
    and the last field is the one the generator marked as the meta.
    """
    if not pawn_id:
        return False
    tile = state.board.pawn_tile(pawn_id)
    if tile is None:
        return False
    if tile.kind is TileKind.FINISH:
        return True
    # Belt and braces for a board theme that never marks a finish: standing on
    # the last position is reaching the end of the road by any reading.
    return tile.slot >= state.board.last_position


def gathering_tile(state) -> Optional[Tile]:
    """The field every single pawn is standing on, if there is one.

    This is the hunters' move: they have to get the whole table into one tower
    before they may look underneath it.  A pawn still loose in the camp, or one
    field short, means there is nothing to check — which is why this counts the
    tower against the pawns that exist rather than against those on the board.
    """
    pawns = [pawn.id for pawn in state.library.pawns]
    if len(pawns) < 2:
        return None
    tile = state.board.pawn_tile(pawns[0])
    if tile is None:
        return None
    if len(tile.stack) != len(pawns):
        return None
    if any(state.board.pawn_tiles.get(pawn) != tile.index for pawn in pawns):
        return None
    return tile


def bottom_of(tile: Optional[Tile]) -> Optional[str]:
    """The pawn holding up the tower — the one the hunters get to check."""
    if tile is None or not tile.stack:
        return None
    return tile.stack[0]


def checkable(state, pawn_id: Optional[str]) -> bool:
    """A colour may be checked once.  After that it is known not to be Piotrek."""
    return bool(pawn_id) and pawn_id not in state.eliminated_pawns


# ── the one decision ─────────────────────────────────────────────────────────
def review(state) -> List[cmd.Command]:
    """What follows from the table as it now stands.

    Called by the authority after every accepted command.  Returns an empty
    list almost always, one command when something happened, and never more
    than one: an elimination and a victory cannot both be true of the same
    tower, and a match that has ended is not looked at again.
    """
    if not state.phase.playable:
        return []
    hidden = hidden_pawn(state)
    if hidden is None:
        # The authority does not know who Piotrek is (nobody has chosen yet, or
        # this is a replica that must never decide anything).  Judging on a
        # guess would be worse than not judging at all.
        return []
    seat = piotrek_seat(state)
    player = state.player(seat) if seat is not None else None
    name = player.name if player is not None else ""

    if reached_finish(state, hidden):
        return [cmd.DeclareVictory(outcome=Outcome.PIOTREK.value, pawn_id=hidden,
                                   piotrek_seat=seat if seat is not None else -1,
                                   piotrek_name=name)]

    tile = gathering_tile(state)
    bottom = bottom_of(tile)
    if bottom is None or not checkable(state, bottom):
        # Either the tower is not complete, or its bottom colour has already
        # been ruled out — and a colour is never checked twice.
        return []
    if bottom == hidden:
        return [cmd.DeclareVictory(outcome=Outcome.HUNTERS.value, pawn_id=hidden,
                                   piotrek_seat=seat if seat is not None else -1,
                                   piotrek_name=name)]
    return [cmd.EliminatePawn(pawn_id=bottom)]
