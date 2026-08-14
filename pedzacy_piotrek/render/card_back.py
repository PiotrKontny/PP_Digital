"""
Card BACKS: which picture belongs to which deck, and loading it.

Every deck has its own back — Karty Ruchu, Mody Patusa, Karty Skrzyni,
Umiejętności Piotrka and the character-exchange deck — and the back is a
PICTURE, not a drawing.  ``CardRenderer.back`` asks this module one question
("is there a back for this deck?") and branches on the answer, which is the
same shape ``card_art.py`` gave the card FACE in stage 30.

WHY A SEPARATE MODULE AND FOLDER FROM ``card_art``
    They are addressed differently, and that is the whole reason.  A card-art
    file is found by the CARD's name — the filename IS the configuration, and
    the folder is scanned.  A card back belongs to a DECK, of which there are
    exactly five and always will be, so it is a TABLE rather than a scan:
    ``settings.CARD_BACKS`` maps deck id -> file name and nothing else in the
    codebase names a card-back file.

        movement         -> movement.png
        mods             -> mods.png
        chest            -> chest.png
        piotrek_skills   -> piotrek_skills.png
        characters       -> characters.png

    Scanning would have meant inventing a second name-folding convention for
    five constants that already have names, and would have made "which file is
    the chest back?" a question you answer by listing a directory.

HOW TO REPLACE ONE BACK
    Drop the new picture into ``assets/card_backs`` and point that deck's line
    in ``settings.CARD_BACKS`` at it.  Keep the old file name and even that is
    unnecessary.  No rendering code changes, ever — if you find yourself
    opening ``card_renderer.py`` to swap a picture, the wiring has been broken.

NOTHING HERE MAY RAISE
    Same policy as ``card_art.py``, for the same reason: an asset folder is
    filled in by hand, and a missing or half-written file must cost a deck its
    picture and nothing more.  Every failure path answers ``None`` and the
    renderer paints the drawn back it painted before stage 47.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

import pygame

from ..config import settings
from .card_art import load_image


class CardBackLibrary:
    """The five card backs, resolved from a table and loaded once each.

    Loading is LAZY and cached per deck, the way ``CardArtLibrary`` loads a
    card face: a deck panel redraws sixty times a second, and the picture
    behind it cannot change while the game is running.  Nothing is read from
    disk until a deck is actually painted, so the headless engine and the
    tests that never draw pay nothing.

    Both the folder and the table are injectable so a test can point at a
    folder of its own without touching the shipped one.
    """

    def __init__(
        self,
        directory: Optional[Path] = None,
        mapping: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.directory = (
            Path(directory) if directory is not None else settings.CARD_BACK_DIR
        )
        self.mapping: Dict[str, str] = dict(
            settings.CARD_BACKS if mapping is None else mapping
        )
        self._surfaces: Dict[str, Optional[pygame.Surface]] = {}

    # ── the table ────────────────────────────────────────────────────────────
    @property
    def deck_ids(self) -> Tuple[str, ...]:
        """The decks that have a back configured, in table order."""
        return tuple(self.mapping)

    def filename(self, deck_id: Optional[str]) -> Optional[str]:
        """The file this deck's back is configured to use, if any."""
        if not deck_id:
            return None
        name = self.mapping.get(deck_id)
        return name or None

    def path(self, deck_id: Optional[str]) -> Optional[Path]:
        """Where this deck's back should be.  Not a promise that it is there."""
        name = self.filename(deck_id)
        return (self.directory / name) if name else None

    def refresh(self) -> None:
        """Re-read the table and drop the loaded surfaces with it.

        The counterpart of ``CardArtLibrary.refresh``: call it after replacing
        a file, if that ever needs to happen without a restart.
        """
        self.mapping = dict(settings.CARD_BACKS)
        self._surfaces.clear()

    # ── loading ──────────────────────────────────────────────────────────────
    def surface(self, deck_id: Optional[str]) -> Optional[pygame.Surface]:
        """This deck's back picture, loaded once.

        ``None`` means "draw the back the old way" — the deck is not in the
        table, or its file is missing, or it is not really an image.  All three
        are the same answer on purpose, because all three want the same
        fallback.
        """
        if not deck_id:
            return None
        if deck_id in self._surfaces:
            return self._surfaces[deck_id]
        path = self.path(deck_id)
        surface = load_image(path) if path is not None else None
        self._surfaces[deck_id] = surface
        return surface

    def has_back(self, deck_id: Optional[str]) -> bool:
        """Whether this deck would render a picture back.

        True only when the file is there AND loads: a deck configured for a
        picture whose file is missing or corrupt gets the drawn back, not a
        broken one.
        """
        return self.surface(deck_id) is not None
