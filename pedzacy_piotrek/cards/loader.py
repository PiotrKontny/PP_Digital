"""
Content loading.

One place reads the JSON files; everything else asks the :class:`ContentLibrary`
for decks by id.  Adding a card is editing ``data/cards.json``; adding a whole
deck is adding an entry to ``decks`` plus a colour in the theme.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..config import settings
from .base_card import CardDef, DeckDef, Pawn


class ContentError(RuntimeError):
    """Raised when a data file is missing or malformed."""


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ContentError(f"Brak pliku danych: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:  # pragma: no cover - user data error
        raise ContentError(f"Błąd składni JSON w {path.name}: {exc}") from exc


def _decks_from_payload(payload: dict, source: Path) -> List[DeckDef]:
    decks: List[DeckDef] = []
    for raw_deck in payload.get("decks", []):
        deck_id = raw_deck.get("id")
        if not deck_id:
            raise ContentError(f"Talia bez pola 'id' w {source.name}")
        cards = tuple(
            CardDef.from_dict(deck_id, raw_card) for raw_card in raw_deck.get("cards", [])
        )
        decks.append(DeckDef(id=deck_id, name=raw_deck.get("name", deck_id), cards=cards))
    return decks


@dataclass
class ContentLibrary:
    """All static content of the game."""

    decks: Dict[str, DeckDef]
    pawns: List[Pawn]
    #: Order the deck panel shows the first three decks in.
    deck_order: List[str]

    @classmethod
    def load(
        cls,
        cards_file: Optional[Path] = None,
        characters_file: Optional[Path] = None,
    ) -> "ContentLibrary":
        cards_file = cards_file or settings.CARDS_FILE
        characters_file = characters_file or settings.CHARACTERS_FILE

        cards_payload = _read_json(cards_file)
        chars_payload = _read_json(characters_file)

        decks: Dict[str, DeckDef] = {}
        order: List[str] = []
        for deck in _decks_from_payload(cards_payload, cards_file):
            decks[deck.id] = deck
            order.append(deck.id)
        for deck in _decks_from_payload(chars_payload, characters_file):
            decks[deck.id] = deck
            order.append(deck.id)

        pawns = [
            Pawn(id=p["id"], name=p.get("name", p["id"]), color=tuple(p["color"]))
            for p in chars_payload.get("pawns", [])
        ]
        if not pawns:
            raise ContentError("characters.json nie zawiera listy 'pawns'")

        library = cls(decks=decks, pawns=pawns, deck_order=order)
        library.validate()
        return library

    # ── queries ──────────────────────────────────────────────────────────────
    def deck(self, deck_id: str) -> DeckDef:
        try:
            return self.decks[deck_id]
        except KeyError as exc:
            raise ContentError(f"Nieznana talia: {deck_id}") from exc

    def deck_name(self, deck_id: str) -> str:
        return self.deck(deck_id).name

    def character_titles(self) -> List[str]:
        return [c.title for c in self.deck(settings.DECK_CHARACTERS).cards]

    def pawn(self, pawn_id: str) -> Optional[Pawn]:
        for p in self.pawns:
            if p.id == pawn_id:
                return p
        return None

    def pawn_color(self, pawn_id: str) -> Optional[tuple[int, int, int]]:
        pawn = self.pawn(pawn_id)
        return pawn.color if pawn else None

    def pawn_colors(self) -> List[tuple[int, int, int]]:
        return [p.color for p in self.pawns]

    def validate(self) -> None:
        """Fail loudly at startup rather than mysteriously mid-game."""
        required = (
            settings.DECK_MOVEMENT,
            settings.DECK_MODS,
            settings.DECK_CHEST,
            settings.DECK_CHARACTERS,
            settings.DECK_SKILLS,
        )
        missing = [d for d in required if d not in self.decks]
        if missing:
            raise ContentError("Brakujące talie w danych: " + ", ".join(missing))

        characters = self.deck(settings.DECK_CHARACTERS)
        if not any(c.is_piotrek for c in characters.cards):
            raise ContentError(
                "Talia postaci musi zawierać dokładnie jedną kartę z \"role\": \"piotrek\""
            )

        known_pawns = {p.id for p in self.pawns} | {"rainbow"}
        for deck in self.decks.values():
            for card in deck.cards:
                if card.badge and card.badge.pawn not in known_pawns:
                    raise ContentError(
                        f"Karta {card.title!r} wskazuje nieznany kolor pionka: {card.badge.pawn}"
                    )
