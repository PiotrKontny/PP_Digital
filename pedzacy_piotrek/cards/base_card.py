"""
Card model — pure data, no pygame.

The prototype had a single ``Card`` class that owned both the data *and* the
drawing code, and derived its badge by string-matching the title at render
time.  That is split here:

* :class:`CardDef` — the immutable definition loaded from JSON (shared by every
  copy of the card in the deck, and identical on every machine in a future
  multiplayer session).
* :class:`Card` — one physical copy, with an id so the network layer can refer
  to "that card" without sending its whole payload.
* :class:`Badge` — the at-a-glance summary strip, now declared in the JSON
  instead of inferred from the title.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional

_uid_counter = itertools.count(1)


@dataclass(frozen=True)
class Badge:
    """Bottom-of-card summary: a coloured dot, an optional arrow, a sign.

    ``pawn`` is either the id of a pawn colour or the literal ``"rainbow"``
    meaning "any pawn" (drawn as a colour wheel).

    ``count`` is how many pawn markers the badge shows.  A card that moves two
    pawns says so on its face; before this the badge could only ever draw one
    dot and "Plagiat!" looked exactly like a card that moves a single pawn.
    """

    pawn: str
    sign: str
    arrow: bool = False
    count: int = 1

    @property
    def is_rainbow(self) -> bool:
        return self.pawn == "rainbow"

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Badge":
        return cls(
            pawn=raw.get("pawn", "rainbow"),
            sign=str(raw.get("sign", "")),
            arrow=bool(raw.get("arrow", False)),
            count=max(1, int(raw.get("count", 1))),
        )


@dataclass(frozen=True)
class EffectSpec:
    """What a card or an ability *does*, declared in JSON rather than in code.

    Deliberately generic: a ``type`` plus a free-form parameter bag.  Adding a
    new kind of effect is a JSON entry plus one registered handler in
    ``engine/effects.py`` — no change here, no change to the card model, and no
    chain of ``if title == ...`` anywhere.

    The named accessors below exist because movement is by far the most common
    effect and ``spec.steps`` reads better than ``spec.get("steps", 1)``; they
    are conveniences over the same bag, not a fixed schema.

    ``target``
        ``fixed``    — the pawn named in ``pawn``;
        ``hindmost`` / ``foremost`` — worked out from the board;
        ``piotrek``  — whoever holds the Piotrek character card;
        ``choice``   — the player picks, and the engine asks.
    """

    type: str = "move_pawn"
    params: Mapping[str, Any] = field(default_factory=dict)

    # ── generic access ───────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.params

    # ── movement conveniences ────────────────────────────────────────────────
    @property
    def target(self) -> str:
        return str(self.get("target", "fixed"))

    @property
    def pawn(self) -> Optional[str]:
        value = self.get("pawn")
        return str(value) if value is not None else None

    @property
    def steps(self) -> int:
        return int(self.get("steps", 1))

    @property
    def direction(self) -> str:
        return str(self.get("direction", "forward"))

    @property
    def is_backward(self) -> bool:
        return self.direction == "backward"

    @property
    def signed_steps(self) -> int:
        return -self.steps if self.is_backward else self.steps

    @property
    def duration_turns(self) -> int:
        return int(self.get("duration_turns", 1))

    @property
    def needs_choice(self) -> bool:
        """True when the effect cannot be resolved without asking the player.

        Any parameter set to the literal ``"choice"`` counts — ``target`` on a
        move, ``source`` on Janek, and whatever a future effect names its own
        decisions — so this does not need updating every time an effect is added.
        Open-ended movement (one field or two, either way) counts as well.

        Some effects ask without any parameter saying so: Spy always opens a
        hand, and a multi-pawn move always asks which pawns.  Those declare
        ``"asks": true``.  It matters beyond the interface — the random reveal
        picks only from cards that resolve on their own, because the executor
        cannot open a prompt halfway through applying a plan, and a card that
        lied about it would simply fizzle.
        """
        if bool(self.get("asks")):
            return True
        if any(value == "choice" for value in self.params.values()):
            return True
        return bool(self.get("step_options")) or self.direction == "either"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EffectSpec":
        params = {k: v for k, v in raw.items() if k != "type"}
        return cls(type=str(raw.get("type", "move_pawn")), params=params)


@dataclass(frozen=True)
class Presentation:
    """How a card announces itself before it reaches the hand.

    Only ``role_reveal`` exists (Gamechanger), but it is data so the next card
    that wants a flourish does not need a branch in the interface.
    """

    type: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    @property
    def delay(self) -> float:
        return float(self.get("delay", 1.0))

    def variant(self, role: str) -> Optional[Mapping[str, Any]]:
        variants = self.get("variants") or {}
        return variants.get(role)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Presentation":
        params = {k: v for k, v in raw.items() if k != "type"}
        return cls(type=str(raw.get("type", "")), params=params)


@dataclass(frozen=True)
class CardDef:
    """One entry in a deck's card list."""

    deck_id: str
    title: str
    text: str
    skill: Optional[str] = None
    badge: Optional[Badge] = None
    effect: Optional[EffectSpec] = None
    #: An activated ability (character cards and Piotrek's skills).
    ability: Optional[EffectSpec] = None
    #: What happens the moment this card ARRIVES in a hand, as opposed to when
    #: it is played.  Same registry, same handlers, same Operations — a card
    #: that acts on the way in is data, not a branch in ``_after_draw``.
    on_draw: Optional[EffectSpec] = None
    #: How many times the ability may be used over a whole game.  The card texts
    #: say "1x" / "5x" for humans; this is the number the rules use.
    uses: Optional[int] = None
    #: Always-on modifiers granted by holding the card (ChatGPT's smaller hand).
    passive: Mapping[str, Any] = field(default_factory=dict)
    presentation: Optional[Presentation] = None
    #: A card the player may not play or discard by hand.  Troll is held until
    #: the effect it started resolves itself; letting the player throw it away
    #: would be a way to dodge it.  The engine enforces this, not the interface.
    locked: bool = False
    #: False keeps the card out of the opening deal (Troll: nobody may begin the
    #: game holding one).  ``deal_starting_hands`` withholds and redraws.
    #: Read through :attr:`opens_a_hand`, never directly — a locked card is
    #: excluded whatever this says.
    in_opening_hand: bool = True
    role: Optional[str] = None
    #: Path to artwork relative to ``assets/images``.  Adding a picture to a
    #: card is a one-line change in the JSON; the renderer falls back to the
    #: drawn card face whenever the file is missing.
    image: Optional[str] = None
    count: int = 1

    @property
    def is_piotrek(self) -> bool:
        return self.role == "piotrek"

    @property
    def opens_a_hand(self) -> bool:
        """Whether this card may be part of an opening hand.

        A locked card never may, and that is derived rather than trusted to the
        JSON on purpose.  Locked cards are unlocked by the thing that dealt
        them — Troll and Stańczyk arm themselves through ``on_draw``, which the
        opening deal does not run — so one dealt at setup can be neither played
        nor discarded nor resolved, and quietly costs its owner a hand slot for
        the whole game.  Stańczyk shipped exactly that way for one commit.
        """
        return self.in_opening_hand and not self.locked

    @classmethod
    def from_dict(cls, deck_id: str, raw: Dict[str, Any]) -> "CardDef":
        badge_raw = raw.get("badge")
        effect_raw = raw.get("effect")
        ability_raw = raw.get("ability")
        presentation_raw = raw.get("presentation")
        return cls(
            deck_id=deck_id,
            title=raw.get("title", ""),
            text=raw.get("text", ""),
            skill=raw.get("skill"),
            badge=Badge.from_dict(badge_raw) if badge_raw else None,
            effect=EffectSpec.from_dict(effect_raw) if effect_raw else None,
            ability=EffectSpec.from_dict(ability_raw) if ability_raw else None,
            on_draw=(EffectSpec.from_dict(raw["on_draw"])
                     if raw.get("on_draw") else None),
            uses=int(raw["uses"]) if raw.get("uses") is not None else None,
            passive=dict(raw.get("passive") or {}),
            presentation=(
                Presentation.from_dict(presentation_raw) if presentation_raw else None
            ),
            locked=bool(raw.get("locked", False)),
            in_opening_hand=bool(raw.get("in_opening_hand", True)),
            role=raw.get("role"),
            image=raw.get("image"),
            count=int(raw.get("count", 1)),
        )


@dataclass
class Card:
    """A single physical copy of a :class:`CardDef`.

    Uses are counted here rather than on the player, because the counter
    belongs to the physical card: hand it to somebody else and the remaining
    uses go with it.
    """

    definition: CardDef
    uid: int = field(default_factory=lambda: next(_uid_counter))
    uses_left: Optional[int] = None
    #: What this card was printed as, when something has turned it into
    #: something else (Gamechanger becoming Alter Ego or Kingmaker).  The deck
    #: restores it on the way back, so the pile keeps its own contents.
    original_definition: Optional[CardDef] = None

    def __post_init__(self) -> None:
        if self.uses_left is None:
            self.uses_left = self.definition.uses

    # ── transformation ───────────────────────────────────────────────────────
    @property
    def is_transformed(self) -> bool:
        return self.original_definition is not None

    def transform(self, definition: CardDef) -> None:
        """Become a different card, keeping identity.

        The uid does not change, so anything already holding a reference — the
        hand, an animation, the played-card strip — follows along instead of
        losing track of it.
        """
        if self.original_definition is None:
            self.original_definition = self.definition
        self.definition = definition
        self.uses_left = definition.uses

    def restore(self) -> None:
        """Turn back into what was printed on it."""
        if self.original_definition is not None:
            self.definition = self.original_definition
            self.original_definition = None
            self.uses_left = self.definition.uses

    # Convenience passthroughs so call sites read like the old code.
    @property
    def title(self) -> str:
        return self.definition.title

    @property
    def text(self) -> str:
        return self.definition.text

    @property
    def skill(self) -> Optional[str]:
        return self.definition.skill

    @property
    def badge(self) -> Optional[Badge]:
        return self.definition.badge

    @property
    def effect(self) -> Optional["EffectSpec"]:
        return self.definition.effect

    @property
    def ability(self) -> Optional["EffectSpec"]:
        return self.definition.ability

    @property
    def on_draw(self) -> Optional["EffectSpec"]:
        return self.definition.on_draw

    @property
    def locked(self) -> bool:
        """True when the player may neither play nor discard this by hand."""
        return self.definition.locked

    @property
    def opens_a_hand(self) -> bool:
        return self.definition.opens_a_hand

    @property
    def presentation(self) -> Optional["Presentation"]:
        return self.definition.presentation

    @property
    def passive(self) -> Mapping[str, Any]:
        return self.definition.passive

    @property
    def has_ability(self) -> bool:
        return self.definition.ability is not None

    @property
    def uses_total(self) -> Optional[int]:
        return self.definition.uses

    @property
    def ability_available(self) -> bool:
        """False once every use has been spent."""
        if self.definition.ability is None:
            return False
        return self.uses_left is None or self.uses_left > 0

    def spend_use(self) -> None:
        if self.uses_left is not None:
            self.uses_left = max(0, self.uses_left - 1)

    @property
    def is_playable(self) -> bool:
        """True when the card has an effect the engine can carry out.

        Cards that need a decision count: the engine asks for it.  This used to
        also mean "needs no decision", which quietly made every select-a-token
        card discard itself when clicked.

        A locked card is never playable by hand however good its effect looks:
        Troll's whole mechanic is that the player does not get to choose.
        """
        if self.definition.locked:
            return False
        return self.definition.effect is not None

    @property
    def resolves_without_asking(self) -> bool:
        """True when the effect can run with no input from the player.

        Needed where nobody can be asked — the random reveal picks from these,
        because the executor cannot open a prompt halfway through applying a
        plan.
        """
        effect = self.definition.effect
        return effect is not None and not effect.needs_choice

    @property
    def deck_id(self) -> str:
        return self.definition.deck_id

    @property
    def is_piotrek(self) -> bool:
        return self.definition.is_piotrek

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Card {self.uid} {self.definition.deck_id}:{self.title!r}>"


@dataclass(frozen=True)
class DeckDef:
    """A whole deck as described by the data files."""

    id: str
    name: str
    cards: tuple[CardDef, ...]

    def build_cards(self) -> list[Card]:
        """Expand ``count`` into one :class:`Card` per physical copy."""
        out: list[Card] = []
        for definition in self.cards:
            for _ in range(max(0, definition.count)):
                out.append(Card(definition))
        return out

    def with_counts(self, counts: Mapping[str, int]) -> "DeckDef":
        """A copy of this deck with some titles' copy counts replaced.

        How the lobby resizes the Mody Patusa deck without editing the JSON.
        Titles the mapping does not name keep the count the data gives them, so
        a partial (or empty) mapping means "the printed deck", and a title the
        deck does not contain is ignored rather than invented.

        The card ORDER is the JSON's, untouched — every copy of a title sits
        where the definition sits.  That is what keeps two machines building the
        same pile from the same seed: the shuffle is a permutation of a list,
        and a list built in a different order shuffles differently.  Do not
        rebuild this from the mapping's own iteration order.
        """
        if not counts:
            return self
        cards = tuple(
            replace(card, count=max(0, int(counts[card.title])))
            if card.title in counts else card
            for card in self.cards
        )
        return DeckDef(id=self.id, name=self.name, cards=cards)


@dataclass(frozen=True)
class Pawn:
    """A pawn colour — the pieces on the board (the 'żółwie' of the original)."""

    id: str
    name: str
    color: tuple[int, int, int]
