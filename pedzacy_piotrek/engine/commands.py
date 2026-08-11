"""
Commands — the only way anything mutates the game.

The UI never edits state directly.  It builds a command and submits it; the
state validates and applies it and answers with events.  Two payoffs:

1. Host-authoritative multiplayer becomes a routing problem, not a rewrite.  A
   client submits the same command object, it is serialised, the host applies
   it, and the resulting events go back out to everyone.
2. Undo, replays and automated balance runs all become possible, because the
   whole game is a list of commands applied to a seed.

Every command is a frozen dataclass with ``to_dict``/``from_dict`` so it
survives a trip through JSON without any per-message plumbing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Dict, Optional, Tuple, Type


def frozen_field():
    """A default empty mapping for a frozen dataclass field."""
    return field(default_factory=dict)


@dataclass(frozen=True)
class Command:
    """Base class.  ``kind`` is the wire name."""

    kind: ClassVar[str] = "command"

    #: Which seat issued this.  ``None`` means "the local hot-seat player",
    #: which is how the current single-machine build runs.
    origin: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Command":
        kind = data.get("kind")
        cls = COMMAND_REGISTRY.get(kind or "")
        if cls is None:
            raise ValueError(f"Nieznana komenda: {kind!r}")
        allowed = {f.name for f in fields(cls)}
        payload = {k: v for k, v in data.items() if k in allowed}
        # JSON has no tuples; restore them so equality with the original holds.
        for spec in fields(cls):
            value = payload.get(spec.name)
            if isinstance(value, list) and "Tuple" in str(spec.type):
                payload[spec.name] = tuple(value)
        return cls(**payload)


# ── cards ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DrawCard(Command):
    kind: ClassVar[str] = "draw_card"
    player_index: int = 0
    deck_id: str = ""


@dataclass(frozen=True)
class DrawTitledCard(Command):
    """Take ONE NAMED card out of a deck and into a hand (stage 33).

    ``DrawCard`` takes whatever is on top; this takes the copy you asked for,
    from anywhere in the deck.  It exists for the Card Library's 'Dobierz
    kartę', whose whole purpose is getting a particular card in front of you
    without drawing thirty others first.

    It carries a ``player_index`` — unlike the library's other three commands —
    because this one IS about a particular player: the card lands in the hand
    of whoever clicked, so the seat is part of the request and the existing
    ``_OWNED_BY_PLAYER`` check is exactly the authorisation wanted.  It is
    deliberately NOT turn-bound: fetching yourself a card to try is not a move.
    """

    kind: ClassVar[str] = "draw_titled_card"
    player_index: int = 0
    deck_id: str = ""
    title: str = ""


@dataclass(frozen=True)
class DiscardCard(Command):
    kind: ClassVar[str] = "discard_card"
    player_index: int = 0
    card_uid: int = 0


@dataclass(frozen=True)
class PlaceMod(Command):
    """Push a card from a hand into the shared 'Mody Patusa' rack."""

    kind: ClassVar[str] = "place_mod"
    player_index: int = 0
    card_uid: int = 0


@dataclass(frozen=True)
class PlayCard(Command):
    """Play a card from a hand: carry out its effect, then discard it.

    This is the game action multiplayer will synchronise.  Decisions the effect
    needs travel in ``choices``; deterministic cards leave it empty and the
    engine works the target out itself, so a client cannot smuggle a different
    one past the host.
    """

    kind: ClassVar[str] = "play_card"
    player_index: int = 0
    card_uid: int = 0
    #: Answers to whatever the effect asked for: which pawn, which half of a
    #: doubled field (12a / 12b), how far to move.  Keys come from the engine's
    #: own :class:`~pedzacy_piotrek.engine.effects.Choice` objects, so a new
    #: effect that needs three decisions needs no new command field — and the
    #: decisions travel with the action, replaying identically everywhere.
    choices: Dict[str, str] = frozen_field()


@dataclass(frozen=True)
class UseAbility(Command):
    """Activate a character's ability or one of Piotrek's skills.

    ``source`` picks which card the ability comes from — the character card in
    the panel, or the skill card above it.  Uses are counted on the card, and
    the engine refuses once they run out.
    """

    kind: ClassVar[str] = "use_ability"
    player_index: int = 0
    source: str = "character"          # "character" | "skill"
    choices: Dict[str, str] = frozen_field()


@dataclass(frozen=True)
class EndTurn(Command):
    """Finish the current player's turn without playing anything.

    The turn normally ends by itself when a movement card resolves; this is the
    same ending reached deliberately — the hand is refilled and the next player
    starts.  It exists because testing with two people should not require
    finding a legal card first.
    """

    kind: ClassVar[str] = "end_turn"
    player_index: int = 0


@dataclass(frozen=True)
class KeepChestCards(Command):
    """Answer the chest-hand limit: these are the cards to keep.

    Anything over the limit that is not listed goes to the discard pile.
    """

    kind: ClassVar[str] = "keep_chest_cards"
    player_index: int = 0
    keep_uids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class DiscardMod(Command):
    kind: ClassVar[str] = "discard_mod"
    slot: int = 0


@dataclass(frozen=True)
class ChooseMod(Command):
    """Piotrek picks one of the three Mods Patusa he was dealt.

    Only the seat holding Piotrek may issue this, and only while a selection is
    open.  The engine checks the uid against the candidates it dealt rather than
    trusting the message, so a client cannot name a card it was never offered.
    """

    kind: ClassVar[str] = "choose_mod"
    player_index: int = 0
    card_uid: int = 0


@dataclass(frozen=True)
class VoteMod(Command):
    """One hunter's vote for one of the three Mods Patusa on offer.

    Votes are PUBLIC — every hunter sees the tally build up — and a hunter may
    change theirs until the last one has voted, which is why this replaces the
    seat's previous vote instead of being refused as a duplicate.
    """

    kind: ClassVar[str] = "vote_mod"
    player_index: int = 0
    card_uid: int = 0


@dataclass(frozen=True)
class DrawCharacter(Command):
    kind: ClassVar[str] = "draw_character"
    player_index: int = 0


@dataclass(frozen=True)
class DrawSkill(Command):
    kind: ClassVar[str] = "draw_skill"
    player_index: int = 0


@dataclass(frozen=True)
class DiscardTopCharacterCard(Command):
    """Discard whichever card the character panel is currently showing."""

    kind: ClassVar[str] = "discard_character_top"
    player_index: int = 0


# ── the card library (stage 32) ──────────────────────────────────────────────
# Three commands, and they are commands for the ordinary reason: the library is
# open DURING a match, so everything it changes has to travel the same road as
# a played card or every table would be looking at a different deck.  None of
# them carries a ``player_index``, and that is deliberate — they are table
# bookkeeping rather than a move, so they are neither owned by a seat nor bound
# to a turn, and any player may issue one.
@dataclass(frozen=True)
class AdjustDeckCount(Command):
    """Add or remove one printed copy of a title from a table deck.

    ``delta`` rather than an absolute count so two players clicking ``+`` at the
    same moment add two cards rather than one: the server applies both, in
    order, and neither is a stale absolute overwriting the other's work.

    The engine decides WHERE a copy is added or removed from; see
    ``GameState._adjust_deck_count``.  A copy is never taken out of a hand.
    """

    kind: ClassVar[str] = "adjust_deck_count"
    deck_id: str = ""
    title: str = ""
    delta: int = 0


@dataclass(frozen=True)
class AdjustAbilityUses(Command):
    """Change how many uses of an ability are LEFT, by ``delta``.

    Not the configured default — that is set in the lobby and is what
    :class:`RestoreAbilityUses` restores to.  There is no upper bound (a table
    may deliberately hand an ability more charges than it was printed with) and
    a hard floor of zero.
    """

    kind: ClassVar[str] = "adjust_ability_uses"
    title: str = ""
    delta: int = 0


@dataclass(frozen=True)
class RestoreAbilityUses(Command):
    """Put an ability's remaining uses back to its configured default.

    Any player may do this for any character: at the table this is somebody
    reaching over and resetting a counter, not the character using their own
    ability, so it is not restricted to the owner.
    """

    kind: ClassVar[str] = "restore_ability_uses"
    title: str = ""


# ── board ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MoveToken(Command):
    """Move a pawn to a world position and/or onto a field.

    ``tile_index`` of ``None`` leaves the pawn free-floating, which preserves
    the prototype's drag-anywhere behaviour.
    """

    kind: ClassVar[str] = "move_token"
    pawn_id: str = ""
    x: float = 0.0
    y: float = 0.0
    tile_index: Optional[int] = None
    animate: bool = True


@dataclass(frozen=True)
class PickUpToken(Command):
    kind: ClassVar[str] = "pick_up_token"
    pawn_id: str = ""


# ── flow ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SetRound(Command):
    kind: ClassVar[str] = "set_round"
    round_number: int = 1


@dataclass(frozen=True)
class SetActivePlayer(Command):
    kind: ClassVar[str] = "set_active_player"
    player_index: int = 0


@dataclass(frozen=True)
class RenamePlayer(Command):
    kind: ClassVar[str] = "rename_player"
    player_index: int = 0
    name: str = ""


@dataclass(frozen=True)
class ToggleMark(Command):
    """Hunter notepad: cross a pawn colour off the suspect list.

    Kept, but no longer issued by the interface: since stage 17 the notepad is
    filled in by the server, which is the only party that knows whether a check
    failed.  A player crossing colours off by hand could disagree with it.
    """

    kind: ClassVar[str] = "toggle_mark"
    player_index: int = 0
    pawn_id: str = ""


# ── the match itself ─────────────────────────────────────────────────────────
# These three are issued by the AUTHORITY, never by a player: the server (or,
# in a hot-seat game, the local session) appends them to the same log every
# other command travels in, so a replaying client reaches the same ending and
# the state fingerprint keeps matching.  ``authorise_remote`` refuses them from
# a client for exactly that reason.
@dataclass(frozen=True)
class BeginMatch(Command):
    """Piotrek has chosen his colour; the table may move.

    The moment everyone starts, in one message, so nobody plays a turn against
    a table that is still being set up.
    """

    kind: ClassVar[str] = "begin_match"


@dataclass(frozen=True)
class EliminatePawn(Command):
    """A tower was checked and its bottom colour was not Piotrek.

    That colour is out of suspicion for the rest of the match, on every
    notepad at once — the hunters share what they learn, because they all
    watched the same tower being lifted.
    """

    kind: ClassVar[str] = "eliminate_pawn"
    pawn_id: str = ""


@dataclass(frozen=True)
class DeclareVictory(Command):
    """The match is over, and this is who won and what was hidden.

    Carries the reveal rather than pointing at it: by the time this is applied
    the secret is public, and a client has no other way to learn it — its own
    copy of Piotrek's ``secret_pawn`` has been ``None`` all game.
    """

    kind: ClassVar[str] = "declare_victory"
    outcome: str = ""                  # "piotrek" | "hunters"
    pawn_id: str = ""
    piotrek_seat: int = -1
    piotrek_name: str = ""


@dataclass(frozen=True)
class RevealIdentity(Command):
    """Alter Ego: the colour Piotrek was hiding behind becomes public.

    Sent by the AUTHORITY once Alter Ego has raised its flag, for the same
    reason an elimination is: answering it needs the secret, and only the
    authority has it (N72).  Carrying the colour is safe because by the time
    this is broadcast it is not a secret any more — Piotrek has given it up.

    Applying it WIPES the notepad and leaves this one colour crossed off.  The
    old crossings were evidence about an identity that no longer exists; the
    only thing still known is that Piotrek is not the colour he just left.
    """

    kind: ClassVar[str] = "reveal_identity"
    pawn_id: str = ""


@dataclass(frozen=True)
class FinishIdentitySwap(Command):
    """Alter Ego: Piotrek has picked a new colour, so play resumes.

    Says nothing about which — the new secret travels the private path the
    first one did, and this command exists purely so that every replica leaves
    the pause on the same command rather than each on its own guess.
    """

    kind: ClassVar[str] = "finish_identity_swap"


COMMAND_REGISTRY: Dict[str, Type[Command]] = {
    cls.kind: cls
    for cls in (
        DrawCard,
        DrawTitledCard,
        DiscardCard,
        PlayCard,
        UseAbility,
        EndTurn,
        KeepChestCards,
        PlaceMod,
        DiscardMod,
        ChooseMod,
        VoteMod,
        DrawCharacter,
        DrawSkill,
        DiscardTopCharacterCard,
        AdjustDeckCount,
        AdjustAbilityUses,
        RestoreAbilityUses,
        MoveToken,
        PickUpToken,
        SetRound,
        SetActivePlayer,
        RenamePlayer,
        ToggleMark,
        BeginMatch,
        EliminatePawn,
        DeclareVictory,
        RevealIdentity,
        FinishIdentitySwap,
    )
}

#: Commands only the authority may issue.  A client sending one is not making a
#: mistake, it is cheating: these are how a match starts, how a colour is ruled
#: out and how a winner is declared.
AUTHORITY_ONLY = (BeginMatch, EliminatePawn, DeclareVictory, RevealIdentity,
                  FinishIdentitySwap)
