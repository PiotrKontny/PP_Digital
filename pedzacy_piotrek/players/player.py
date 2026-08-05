"""
Player state.

Deliberately a *passive* object: it holds cards, it does not reach into decks.
All deck traffic goes through :class:`~pedzacy_piotrek.engine.game_state.GameState`,
which is the single authority that a host will later replicate to clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..cards.base_card import Card
from ..config.settings import RULES
from .roles import Role


@dataclass
class Player:
    """One seat at the table."""

    index: int
    name: str
    color: tuple[int, int, int]
    color_name: str

    hand: List[Card] = field(default_factory=list)
    #: The character card from the "Karty Postaci" deck.
    character: Optional[Card] = None
    #: Only meaningful while the character is Piotrek — drawn from the separate
    #: "Umiejętności Piotrka" deck.
    skill: Optional[Card] = None
    #: Hunter-only notepad: pawn colours this player has ruled out.
    marks: Set[str] = field(default_factory=set)
    #: Which pawn this player secretly controls.  Only Piotrek has one for now;
    #: the field exists so the reveal mechanic has somewhere to live.
    secret_pawn: Optional[str] = None
    #: Set once a network client claims this seat.
    owner_id: Optional[str] = None

    # ── derived ──────────────────────────────────────────────────────────────
    @property
    def role(self) -> Role:
        if self.character is not None and self.character.is_piotrek:
            return Role.PIOTREK
        return Role.HUNTER

    @property
    def is_piotrek(self) -> bool:
        return self.role.is_piotrek

    @property
    def display_character(self) -> str:
        return self.character.title if self.character is not None else "brak"

    @property
    def hand_is_full(self) -> bool:
        return len(self.hand) >= RULES.max_hand

    # ── hand ─────────────────────────────────────────────────────────────────
    def add_card(self, card: Card) -> bool:
        if self.hand_is_full:
            return False
        self.hand.append(card)
        return True

    def remove_card(self, card: Card) -> bool:
        if card not in self.hand:
            return False
        self.hand.remove(card)
        return True

    def card_by_uid(self, uid: int) -> Optional[Card]:
        for card in self.hand:
            if card.uid == uid:
                return card
        return None

    # ── hunter notepad ───────────────────────────────────────────────────────
    def toggle_mark(self, pawn_id: str) -> bool:
        """Cross a pawn colour off (or back on).  Returns the new state."""
        if pawn_id in self.marks:
            self.marks.discard(pawn_id)
            return False
        self.marks.add(pawn_id)
        return True

    def rename(self, new_name: str) -> bool:
        cleaned = new_name.strip()[: RULES.max_name_length]
        if not cleaned:
            return False
        self.name = cleaned
        return True

    # ── serialisation (used by the network layer) ────────────────────────────
    def to_public_dict(self) -> Dict[str, object]:
        """What every client may know about this player.

        Note what is *absent*: the contents of the hand, the character card and
        the secret pawn.  Hidden information is the whole point of this game,
        so the split is baked into the data model rather than bolted on later.
        """
        return {
            "index": self.index,
            "name": self.name,
            "color": list(self.color),
            "color_name": self.color_name,
            "hand_size": len(self.hand),
            "has_character": self.character is not None,
            "owner_id": self.owner_id,
        }

    def to_private_dict(self) -> Dict[str, object]:
        """What only the owner of this seat may know."""
        data = self.to_public_dict()
        data.update(
            {
                "hand": [c.uid for c in self.hand],
                "character": self.character.title if self.character else None,
                "skill": self.skill.title if self.skill else None,
                "marks": sorted(self.marks),
                "secret_pawn": self.secret_pawn,
            }
        )
        return data
