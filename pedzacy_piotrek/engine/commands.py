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
    """Hunter notepad: cross a pawn colour off the suspect list."""

    kind: ClassVar[str] = "toggle_mark"
    player_index: int = 0
    pawn_id: str = ""


COMMAND_REGISTRY: Dict[str, Type[Command]] = {
    cls.kind: cls
    for cls in (
        DrawCard,
        DiscardCard,
        PlayCard,
        UseAbility,
        EndTurn,
        KeepChestCards,
        PlaceMod,
        DiscardMod,
        DrawCharacter,
        DrawSkill,
        DiscardTopCharacterCard,
        MoveToken,
        PickUpToken,
        SetRound,
        SetActivePlayer,
        RenamePlayer,
        ToggleMark,
    )
}
