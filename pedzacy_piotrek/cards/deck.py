"""
Deck runtime: a draw pile and a discard pile.

Behaviour is identical to the prototype's ``Deck`` with one important change:
the shuffle uses an injected :class:`random.Random` rather than the global
module state.  Every source of randomness in the game now flows from the one
seed in :class:`~pedzacy_piotrek.config.settings.SessionConfig`, which is what
lets a host and its clients reach the same shuffle without transmitting it.
"""

from __future__ import annotations

import random
from typing import Iterable, List, Optional

from .base_card import Card, DeckDef


class Deck:
    def __init__(self, definition: DeckDef, rng: Optional[random.Random] = None) -> None:
        self.definition = definition
        self.rng = rng or random.Random()
        self.draw_pile: List[Card] = definition.build_cards()
        self.discard_pile: List[Card] = []
        self.rng.shuffle(self.draw_pile)

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    # ── operations ───────────────────────────────────────────────────────────
    def take_card(self) -> Optional[Card]:
        """Pop the top card, reshuffling the discard pile in when empty."""
        if not self.draw_pile:
            if not self.discard_pile:
                return None
            self.reshuffle()
        return self.draw_pile.pop()

    def take_titled(self, title: str) -> Optional[Card]:
        """Remove a specific card by title from the draw pile (menu picks)."""
        for card in self.draw_pile:
            if card.title == title:
                self.draw_pile.remove(card)
                return card
        return None

    def return_card(self, card: Card) -> None:
        # A card that was turned into something else goes back as itself, so
        # the deck still contains what it is supposed to contain.
        card.restore()
        self.discard_pile.append(card)

    def return_all(self, cards: Iterable[Card]) -> None:
        for card in cards:
            card.restore()
            self.discard_pile.append(card)

    def reshuffle(self) -> None:
        self.rng.shuffle(self.discard_pile)
        self.draw_pile.extend(self.discard_pile)
        self.discard_pile.clear()

    def shuffle_draw_pile(self) -> None:
        self.rng.shuffle(self.draw_pile)

    # ── introspection ────────────────────────────────────────────────────────
    @property
    def draw_count(self) -> int:
        return len(self.draw_pile)

    @property
    def discard_count(self) -> int:
        return len(self.discard_pile)

    @property
    def top_discard(self) -> Optional[Card]:
        return self.discard_pile[-1] if self.discard_pile else None

    def find_discarded(self, uid: int) -> Optional[Card]:
        """Look up a card that has just been discarded.

        The 'recently played' panel needs the card object after the engine has
        already let go of it, and searching from the top is right because the
        card it wants is almost always the last one added.
        """
        for card in reversed(self.discard_pile):
            if card.uid == uid:
                return card
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Deck {self.id} draw={self.draw_count} discard={self.discard_count}>"
