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
class CardVariant:
    """One of a card's two-or-more predefined readings.

    A VARIANT IS NOT A CARD.  ``Sesja na PG`` with variant 2 selected is still
    ``Sesja na PG``: same title, same artwork, same count, same deck identity,
    same entry in cards.json.  Only what the card SAYS and what it DOES may
    differ, which is exactly the three fields below.

    Everything a variant leaves out is inherited from the card it belongs to,
    so a variant that only rewrites the text is three keys of JSON and a
    variant that only changes the rule is three others.  Deliberately small:
    ``AKO`` and ``Nie masz Rosji`` are the next two cards to use this, and both
    of them are a different sentence plus a different rule.
    """

    id: str
    label: str = ""
    #: Replacement rules text, or ``None`` to keep the card's printed one.
    text: Optional[str] = None
    #: Replacement ``passive`` bag.  ``None`` keeps the card's own; a variant
    #: that declares one REPLACES rather than merges, because "the same rule
    #: minus one key" is a thing a merge cannot express.
    passive: Optional[Mapping[str, Any]] = None
    #: Replacement active effect, for a variant that changes what playing the
    #: card does rather than what holding it does.
    effect: Optional[EffectSpec] = None
    #: Replacement ACTIVATED ability, for a character card whose two variants
    #: differ in what using the ability does.  Ondrej's Radar is the first:
    #: both variants link two pawns and only the checking rule differs, so the
    #: variant is the same ``link_pawns`` spec with one key changed.  A
    #: character card is a card and its variants are variants; this is the
    #: fourth field of the same small shape rather than a parallel mechanism.
    ability: Optional[EffectSpec] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CardVariant":
        effect_raw = raw.get("effect")
        ability_raw = raw.get("ability")
        return cls(
            id=str(raw.get("id", "")),
            label=str(raw.get("label", "")),
            text=None if raw.get("text") is None else str(raw["text"]),
            passive=(None if raw.get("passive") is None
                     else dict(raw["passive"])),
            effect=EffectSpec.from_dict(effect_raw) if effect_raw else None,
            ability=EffectSpec.from_dict(ability_raw) if ability_raw else None,
        )


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
    #: SIGNATURE CARD artwork identifier — a NAME, not a path, resolved against
    #: ``assets/card_art`` by ``render/card_art.py``.  Leave it out and the name
    #: is derived from the title, so dropping ``Troll.png`` into that folder is
    #: the whole of "give Troll artwork".  Set it when the file cannot be named
    #: after the card: a shared picture, or a title that two decks both use
    #: (``"art": "chest/shady"``).
    #:
    #: This is NOT ``image``.  ``image`` is a small illustration drawn INSIDE
    #: the parchment body of a standard card; ``art`` REPLACES the face.  A
    #: card may declare either, neither, or — pointlessly but harmlessly —
    #: both, in which case the Signature face wins and ``image`` is unused.
    art: Optional[str] = None
    count: int = 1
    #: The readings this card may be played under, in the data file's order.
    #: EMPTY IS THE ORDINARY CASE — a card with fewer than two variants has no
    #: variant setting anywhere in the interface, which is what keeps the
    #: control off the twenty-nine cards that do not need one.
    variants: tuple[CardVariant, ...] = ()
    #: Which of them this definition is currently expressing.  Empty means
    #: "the printed card", which for a card WITH variants is its first one.
    variant: str = ""
    #: The PRINTED definition, kept when a variant has been applied on top of
    #: it.  Without it a second :meth:`with_variant` would inherit the first
    #: variant's text wherever the second one declares none, and switching from
    #: variant 2 back to variant 1 would leave variant 2's sentence on the
    #: card.  Never compared and never printed: it is the same card.
    base: Optional["CardDef"] = field(default=None, repr=False, compare=False)

    @property
    def is_piotrek(self) -> bool:
        return self.role == "piotrek"

    # ── variants ─────────────────────────────────────────────────────────────
    @property
    def has_variants(self) -> bool:
        """Whether a variant is a choice at all.

        Two is the minimum: one "variant" is the card, and offering a
        single-option selector would be a control that cannot do anything.
        """
        return len(self.variants) > 1

    @property
    def variant_ids(self) -> tuple[str, ...]:
        return tuple(v.id for v in self.variants)

    @property
    def default_variant(self) -> str:
        """The reading a table gets when nobody chose one.

        The FIRST in the data file, which is why ``Sesja na PG``'s variant 1 is
        the behaviour that shipped: a game that never opens the panel plays
        exactly the card it played before this existed.
        """
        return self.variants[0].id if self.variants else ""

    def variant_def(self, variant_id: str) -> Optional[CardVariant]:
        return next((v for v in self.variants if v.id == variant_id), None)

    @property
    def selected_variant(self) -> str:
        return self.variant or self.default_variant

    def with_variant(self, variant_id: str) -> "CardDef":
        """This card read under one of its variants.

        Returns SELF when the card has no such variant, so an unknown id from
        an older save or a hand-written message is ignored rather than
        producing a card with no rules on it.

        Only ``text``, ``passive`` and ``effect`` are touched.  The title, the
        artwork, the count and the deck id are the card's IDENTITY and no
        variant may reach them — that is the difference between this and simply
        printing two cards.
        """
        printed = self.printed
        variant = printed.variant_def(variant_id)
        if variant is None:
            return self
        return replace(
            printed,
            text=printed.text if variant.text is None else variant.text,
            passive=(dict(printed.passive) if variant.passive is None
                     else dict(variant.passive)),
            effect=printed.effect if variant.effect is None else variant.effect,
            ability=printed.ability if variant.ability is None else variant.ability,
            variant=variant.id,
            base=printed,
        )

    @property
    def printed(self) -> "CardDef":
        """What cards.json says, whatever variant is currently applied."""
        return self.base or self

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
            # ``"art": false`` reads more naturally in JSON than ``"art": ""``
            # and means the same thing: never look for a picture for this card.
            art=("" if raw.get("art") is False
                 else (str(raw["art"]) if raw.get("art") is not None else None)),
            count=int(raw.get("count", 1)),
            variants=tuple(CardVariant.from_dict(item)
                           for item in (raw.get("variants") or [])),
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
    def art(self) -> Optional[str]:
        """The Signature Card artwork identifier, if the JSON names one.

        Read through the DEFINITION rather than cached, so a transformed card
        (Gamechanger becoming Alter Ego) shows the artwork of what it has
        become rather than of what it was printed as.
        """
        return self.definition.art

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
    def variant(self) -> str:
        """Which reading this physical copy is being played under.

        Read through the DEFINITION for the reason :attr:`art` is: the card is
        whatever it has become, not what it was printed as.
        """
        return self.definition.selected_variant

    @property
    def has_variants(self) -> bool:
        return self.definition.has_variants

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

    def with_variants(self, variants: Mapping[str, str]) -> "DeckDef":
        """A copy of this deck with some titles read under a chosen variant.

        The third member of the :meth:`with_counts` / :meth:`with_uses` family
        and it keeps both of their properties, for both of their reasons: a
        title the mapping does not name keeps the variant the data gives it, so
        an empty mapping means "as printed"; and the card ORDER is untouched,
        because the shuffle is a permutation of this list and a list built in
        another order shuffles differently.

        A variant changes no card's ``count``, so unlike :meth:`with_counts`
        this cannot change the SIZE of the pile — which is why applying it
        before or after the counts makes no difference to what gets shuffled.
        """
        if not variants:
            return self
        cards = tuple(
            card.with_variant(str(variants[card.title]))
            if card.title in variants and card.has_variants else card
            for card in self.cards
        )
        return DeckDef(id=self.id, name=self.name, cards=cards)

    def with_uses(self, uses: Mapping[str, int]) -> "DeckDef":
        """A copy of this deck with some titles' ability charges replaced.

        The counterpart of :meth:`with_counts` for the character and skill
        decks, where the lobby sets how many times an ability may be used
        rather than how many copies exist.  Same two properties, for the same
        reasons: a title the mapping does not name keeps the number the data
        gives it, so an empty mapping means "as printed"; and the card ORDER is
        untouched, because the shuffle is a permutation of this list.

        Only cards that actually declare an ability are changed.  Setting
        ``uses`` on a card with no ability would put a counter on the face of a
        card that can never spend it.
        """
        if not uses:
            return self
        cards = tuple(
            replace(card, uses=max(0, int(uses[card.title])))
            if card.title in uses and card.ability else card
            for card in self.cards
        )
        return DeckDef(id=self.id, name=self.name, cards=cards)


@dataclass(frozen=True)
class Pawn:
    """A pawn colour — the pieces on the board (the 'żółwie' of the original)."""

    id: str
    name: str
    color: tuple[int, int, int]
