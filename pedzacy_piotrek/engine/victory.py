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
from typing import Any, Dict, List, Optional, Tuple

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
    field short, means there is nothing to check.

    It counts against the pawns that are ON THE TABLE, not against the palette.
    Normally those are the same thing.  While Shady is holding a pawn off the
    map they are not, and the hunters only have to gather the ones that are
    left — five pawns onto one field instead of six.  That exception lasts
    exactly as long as the pawn is away; it is not a rule of its own, which is
    why it is one call to ``visible_pawns`` rather than a special case.
    """
    pawns = [pawn.id for pawn in visible_pawns(state)]
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


def visible_pawns(state) -> List:
    """Pawns that are actually on the table (Shady removes one for a round)."""
    getter = getattr(state, "visible_pawns", None)
    return list(getter) if getter is not None else list(state.library.pawns)


def bottom_of(tile: Optional[Tile]) -> Optional[str]:
    """The pawn holding up the tower — the one the hunters get to check."""
    if tile is None or not tile.stack:
        return None
    return tile.stack[0]


def checkable(state, pawn_id: Optional[str]) -> bool:
    """A colour may be checked once.  After that it is known not to be Piotrek."""
    return bool(pawn_id) and pawn_id not in state.eliminated_pawns


def escaped_pawn(state, hidden: str) -> Optional[str]:
    """The pawn whose arrival at the finish wins the match for Piotrek.

    TWO VARIANTS, AND ONLY THE CONDITION DIFFERS.  ``own_pawn`` is the game as
    it has always been: the colour Piotrek is actually hiding behind has to get
    there.  ``any_pawn`` lets any pawn do it.

    WHAT THIS DELIBERATELY DOES NOT TOUCH.  The hidden colour is still hidden,
    still the thing a check is asking about, and still what the reveal shows at
    the end — the ``Verdict`` carries ``hidden`` in both variants, so winning
    on somebody else's pawn does not rename Piotrek or leak which pawn he was.
    Checking, movement and ownership are all read from the same state they
    always were; nothing else in the module asks this question.
    """
    if reached_finish(state, hidden):
        return hidden
    if getattr(state, "victory_variant", "own_pawn") != "any_pawn":
        return None
    for pawn in visible_pawns(state):
        if reached_finish(state, pawn.id):
            return pawn.id
    return None


ICE_BLOCK = "refuse_check"


def ice_block_uses(state) -> int:
    """How many Ice Blocks Piotrek has left, read from the ordinary card.

    The ability-use system IS the counter, exactly as it is for every other
    character: a seat holding the Piotrek card whose ``ability`` is
    ``refuse_check``.  No parallel tally, and a table where the Card Library
    has set the uses to nought simply never opens the window.
    """
    return 0 if ice_block_card(state) is None else max(
        0, int(getattr(ice_block_card(state), "uses_left", 0)))


def ice_block_card(state):
    """The card carrying Ice Block, or ``None``.

    It is one of PIOTREK'S SKILLS — the separate deck he draws from — and not
    a character ability, which is why this looks at ``player.skill``.  Both are
    checked all the same, so a future table that hands Ice Block out as a
    character ability instead needs no change here.
    """
    seat = piotrek_seat(state)
    player = state.player(seat) if seat is not None else None
    if player is None:
        return None
    for card in (getattr(player, "skill", None),
                 getattr(player, "character", None)):
        ability = getattr(card, "ability", None) if card is not None else None
        if ability is not None and str(ability.type) == ICE_BLOCK:
            if getattr(card, "ability_available", False):
                return card
    return None


def ice_block_pending(state, source: str, pawn_id: str) -> List[cmd.Command]:
    """Open Piotrek's window if he may refuse this check, else ``[]``.

    THE ONE GATE EVERY CHECK PASSES THROUGH.  The three checking routes — the
    completed tower, Squid Game's automatic check and Glockboy's deliberate one
    — all ask this before resolving, so a fourth added later inherits the
    ability by calling the same function rather than by remembering to.

    Returns a command rather than mutating, like everything else in this
    module: the window is state every client has to agree about, so it is
    opened by a logged command and not by the authority quietly setting a flag.
    """
    if getattr(state, "pending_check", None) is not None:
        return []                       # already asked; waiting for an answer
    if getattr(state, "check_allowed", None) == pawn_id:
        return []                       # he has already said yes to this one
    if ice_block_uses(state) <= 0:
        return []
    seat = piotrek_seat(state)
    if seat is None:
        return []
    return [cmd.OpenCheckDecision(source=source, pawn_id=pawn_id, seat=seat)]


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
    if getattr(state, "pending_check", None) is not None:
        # A check is on the table waiting for Piotrek.  Nothing is decided
        # until he answers or the clock runs out — including a victory, which
        # could otherwise resolve out from under an unanswered question.
        return []
    hidden = hidden_pawn(state)

    # Alter Ego, before anything else.  The card raised a public flag that
    # names no colour, because the machine that played it may not know one;
    # answering it needs the secret, so it is answered HERE, on the authority,
    # and comes back as a logged command like every other decision that needs
    # the secret (N72).
    swap = getattr(state, "identity_swap", "")
    if swap == getattr(state, "SWAP_REVEALING", "revealing"):
        if hidden is None:
            # A replica, which must never decide anything — or an authority
            # that has genuinely lost the colour, in which case guessing would
            # be worse than waiting.
            return []
        return [cmd.RevealIdentity(pawn_id=hidden)]
    if swap:
        # Waiting for Piotrek to pick.  There is deliberately NO hidden colour
        # during this window, so nothing below could judge anything anyway —
        # but returning early says so rather than relying on that.
        return []

    if hidden is None:
        # The authority does not know who Piotrek is (nobody has chosen yet, or
        # this is a replica that must never decide anything).  Judging on a
        # guess would be worse than not judging at all.
        return []
    seat = piotrek_seat(state)
    player = state.player(seat) if seat is not None else None
    name = player.name if player is not None else ""

    if escaped_pawn(state, hidden) is not None:
        # The VERDICT still names the hidden colour, whichever pawn crossed
        # the line: the reveal is about who Piotrek was, not about which pawn
        # happened to finish.
        return [cmd.DeclareVictory(outcome=Outcome.PIOTREK.value, pawn_id=hidden,
                                   piotrek_seat=seat if seat is not None else -1,
                                   piotrek_name=name)]

    # A check somebody ASKED for is answered before anything else on the
    # table, because it is a question that has been put and is waiting.
    # Glockboy's "Where are you Marcus?" is the only thing that asks it today.
    asked = getattr(state, "pending_pawn_check", None)
    if asked:
        return _asked_check(state, asked, hidden, seat, name)

    # Squid Game REPLACES the checking mechanic rather than adding to it: while
    # it is in the rack, building a tower proves nothing and the only check in
    # the game is the automatic one the round armed.  Both halves are read off
    # the same rule, so the ordinary check cannot survive by accident.
    if getattr(state, "lead_check_only", False):
        return _lead_check(state, hidden, seat, name)

    tile = gathering_tile(state)
    bottom = bottom_of(tile)
    if bottom is None or not checkable(state, bottom):
        # Either the tower is not complete, or its bottom colour has already
        # been ruled out — and a colour is never checked twice.
        return []
    if getattr(state, "check_needs_separation", False):
        # Ice Block refused the last one.  "Pionki muszą być rozdzielone przed
        # kolejnym sprawdzeniem": this same intact tower proves nothing until
        # it has come apart, or a refusal would simply be re-asked next command.
        return []
    waiting = ice_block_pending(state, "tower", bottom)
    if waiting:
        return waiting
    _, commands = _resolve_check(state, bottom, hidden, seat, name)
    return commands


def _asked_check(state, asked, hidden: str, seat: Optional[int],
                 name: str) -> List[cmd.Command]:
    """Settle a check a player deliberately made, and charge them if it failed.

    THE ORDINARY HUNTER VICTORY, not a second ending.  A correct guess produces
    exactly the ``DeclareVictory`` a completed tower would have produced, with
    the same outcome, the same reveal, the same overlay and the same three ways
    out — Glockboy's ability is another route to the existing ending, and there
    is deliberately no second game-over path for it to take.

    A wrong guess answers with TWO commands, and both of them are ordinary:
    the colour is crossed off every notepad exactly as a failed tower crosses
    one off, and the seat that staked itself is out.  The match keeps running.
    """
    checked, staked = str(asked[0]), int(asked[1])
    waiting = ice_block_pending(state, "asked", checked)
    if waiting:
        return waiting
    if not checkable(state, checked):
        # Already ruled out, so the guess cannot teach anybody anything and
        # cannot cost anybody anything.  Drop the question.
        return [cmd.EliminatePawn(pawn_id=checked)]
    found, commands = _resolve_check(state, checked, hidden, seat, name)
    if found:
        return commands
    out: List[cmd.Command] = list(commands)
    if staked >= 0:
        player = state.player(staked)
        who = player.name if player is not None else ""
        out.append(cmd.EliminatePlayer(
            player_index=staked,
            reason=f"Nietrafione sprawdzenie: {checked}" if not who
            else f"{who}: nietrafione sprawdzenie ({checked})",
        ))
    return out


def checked_with(state, pawn_id: str) -> List[str]:
    """Every colour a check on ``pawn_id`` actually inspects, in order.

    ONE CHECK, POSSIBLY SEVERAL COLOURS.  Radar's variant 1 says that checking
    one linked pawn checks the other; the linked pair is therefore not two
    checks in sequence but one check with two answers, and every checking route
    in the game asks this rather than each of them growing a Radar clause.

    Colours already ruled out are dropped: a check never inspects the same
    colour twice, and a partner that has already been crossed off adds nothing.
    The pawn being checked comes FIRST, so a failed check reads as \"we looked
    at blue, and pink came with it\".
    """
    from . import effects

    group = [pawn_id, *effects.checks_together(state, pawn_id)]
    return [colour for colour in group if checkable(state, colour)]


def _resolve_check(state, pawn_id: str, hidden: str, seat: Optional[int],
                   name: str) -> Tuple[bool, List[cmd.Command]]:
    """Answer one check, following any Radar link out from it.

    Returns ``(found_piotrek, commands)``.  A linked partner that turns out to
    be Piotrek ends the match exactly as the checked pawn would have — that is
    what \"both pawns are checked\" means, and the alternative (checking a pawn
    but declining to notice the answer) would be a different rule.
    """
    colours = checked_with(state, pawn_id)
    if not colours:
        return False, []
    if hidden in colours:
        return True, [cmd.DeclareVictory(
            outcome=Outcome.HUNTERS.value, pawn_id=hidden,
            piotrek_seat=seat if seat is not None else -1,
            piotrek_name=name)]
    return False, [cmd.EliminatePawn(pawn_id=colour) for colour in colours]


def _lead_check(state, hidden: str, seat: Optional[int],
                name: str) -> List[cmd.Command]:
    """Settle the automatic check the round armed (Squid Game).

    ``pending_lead_check`` names the colour; naming it was public and happened
    on every machine, so the question is the same everywhere.  ANSWERING it
    needs the hidden colour, so it happens here — on the authority — and comes
    back as one of the two commands the checking rules already use, which is
    what makes the result log, broadcast, replay and fingerprint like every
    other check in the game.
    """
    checked = getattr(state, "pending_lead_check", None)
    if not checked or not checkable(state, checked):
        return []
    waiting = ice_block_pending(state, "lead", checked)
    if waiting:
        return waiting
    # Through the shared resolver, so a pawn Radar has linked drags its partner
    # into the automatic check exactly as it does into a deliberate one.
    _, commands = _resolve_check(state, checked, hidden, seat, name)
    return commands
