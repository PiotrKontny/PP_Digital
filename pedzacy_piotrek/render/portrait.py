"""
Character PORTRAITS: which picture belongs to which character, and loading it.

A character is represented in the right-hand panel by a picture of its face.
``CharacterPanel`` asks this module one question ("what do I paint for this
character?") and always gets an answer, which is the difference between this
and ``card_art.py``: a card without artwork has a perfectly good parchment face
to fall back on, and a character without a portrait has nothing.  So the
fallback is an ASSET rather than a drawing — ``placeholder.png`` — and the
panel never has to branch.

HOW A FILE FINDS ITS CHARACTER
    Exactly the way a card finds its artwork, and through the same
    :func:`~pedzacy_piotrek.render.card_art.slugify`:

        assets/portraits/Glockboy.png        ->  "glockboy"
        the character named "Glockboy"       ->  "glockboy"

    so the owner's workflow is the one already learned for card art: save the
    file under the character's name.  Case, spaces, hyphens, punctuation and
    Polish diacritics fold away, so ``Big D Randy.png``, ``big_d_randy.PNG``
    and ``big-d-randy.jpg`` are one key, and ``Dziubdziuch.png`` needs no
    special handling.

    There are no scopes here.  Card art needs them because "Shady" is a title
    in two decks; a character name is unique across the one deck characters
    come from, so a portrait folder is flat and stays flat.

WHY THE CHARACTER'S NAME AND NOT THE ABILITY'S
    They are different things and stage 49 exists partly because the Card
    Library had confused them.  ``Big D Randy`` is the character and is what a
    portrait is named after; ``Granny Costume`` is the ability printed on his
    card and is what CARD ART is named after.  Both files can exist, they live
    in different folders, and neither is reachable from the other's lookup.

THE PLACEHOLDER IS NOT A CHARACTER
    ``placeholder.png`` is excluded from the scan, so a future character
    actually called "Placeholder" would not silently adopt it and, more to the
    point, the fallback cannot be shadowed by the folder it lives in.

NOTHING HERE MAY RAISE
    Same policy as ``card_art.py`` and ``card_back.py``, for the same reason: a
    portrait folder is filled in by hand over months and is expected to be
    incomplete.  A missing, half-written or mistyped file costs a character its
    likeness and nothing else.  If even the placeholder is absent the answer is
    ``None`` and the panel draws its empty well, which is what it would have
    drawn before this module existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pygame

from ..config import settings
from .card_art import load_image, slugify


class PortraitLibrary:
    """The portrait folder, indexed once and cached.

    The scan happens at construction rather than per lookup, for the reason
    ``CardArtLibrary`` documents: the panel redraws sixty times a second and
    ``Path.exists`` sixty times a second is a syscall storm for an answer that
    cannot change while the game is running.  Call :meth:`refresh` after
    dropping a file in, if it ever matters.

    Both the folder and the placeholder name are injectable so a test can point
    at a folder of its own without touching the shipped one.
    """

    def __init__(self, directory: Optional[Path] = None,
                 placeholder: Optional[str] = None) -> None:
        self.directory = (
            Path(directory) if directory is not None else settings.PORTRAIT_DIR
        )
        self.placeholder_name = (
            settings.PORTRAIT_PLACEHOLDER if placeholder is None else placeholder
        )
        self._files: Dict[str, Path] = {}
        self._surfaces: Dict[str, Optional[pygame.Surface]] = {}
        self.refresh()

    # ── the index ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Re-scan the folder.  Drops the loaded surfaces with it."""
        self._files = self._scan()
        self._surfaces.clear()

    def _scan(self) -> Dict[str, Path]:
        found: Dict[str, Path] = {}
        if not self.directory.is_dir():
            return found
        try:
            paths = sorted(self.directory.iterdir())
        except OSError:  # pragma: no cover - unreadable folder
            return found

        placeholder_key = slugify(Path(self.placeholder_name).stem)
        for path in paths:
            try:
                if not path.is_file():
                    continue
                if path.suffix.lower() not in settings.CARD_ART_SUFFIXES:
                    continue
            except OSError:  # pragma: no cover - odd filesystem
                continue
            key = slugify(path.stem)
            # The fallback is not a character.  See the module docstring.
            if not key or key == placeholder_key:
                continue
            self._offer(found, key, path)
        return found

    @staticmethod
    def _offer(found: Dict[str, Path], key: str, path: Path) -> None:
        """Register ``path`` under ``key``, preferring the better format.

        ``Piotrek.png`` and ``Piotrek.jpg`` side by side is a normal state for
        a folder somebody is working in.  Ranking by ``CARD_ART_SUFFIXES``
        makes the winner the same one on every machine, which alphabetical
        order would not — the same rule ``CardArtLibrary`` applies, so the two
        folders never disagree about which of two files wins.
        """
        existing = found.get(key)
        if existing is None:
            found[key] = path
            return
        order = settings.CARD_ART_SUFFIXES
        rank = {suffix: index for index, suffix in enumerate(order)}
        limit = len(order)
        if rank.get(path.suffix.lower(), limit) < rank.get(existing.suffix.lower(), limit):
            found[key] = path

    # ── lookup ───────────────────────────────────────────────────────────────
    def key(self, name: Optional[str]) -> Optional[str]:
        """The name this character's portrait is filed under, if there is one.

        ``None`` means "no portrait of its own" — which is not a failure, it is
        the ordinary case for every character nobody has drawn yet.
        """
        if not name:
            return None
        key = slugify(name)
        return key if key in self._files else None

    def path(self, name: Optional[str]) -> Optional[Path]:
        """Where this character's own portrait is, if it has one."""
        key = self.key(name)
        return self._files.get(key) if key else None

    @property
    def placeholder_path(self) -> Path:
        """Where the fallback is.  Not a promise that it is there."""
        return self.directory / self.placeholder_name

    def has_portrait(self, name: Optional[str]) -> bool:
        """Whether this character has a portrait OF ITS OWN that loads.

        False for a character on the placeholder.  Deliberately not the same
        question as "is there something to draw" — :meth:`surface` answers
        that, and answers it yes far more often.
        """
        return self._own_surface(name) is not None

    # ── loading ──────────────────────────────────────────────────────────────
    def _own_surface(self, name: Optional[str]) -> Optional[pygame.Surface]:
        key = self.key(name)
        if key is None:
            return None
        if key in self._surfaces:
            return self._surfaces[key]
        surface = load_image(self._files[key])
        self._surfaces[key] = surface
        return surface

    def placeholder_surface(self) -> Optional[pygame.Surface]:
        """The fallback picture, loaded once.

        Cached under a key that cannot collide with a character's, because the
        scan drops that name from the index.
        """
        cache_key = "\0placeholder"
        if cache_key in self._surfaces:
            return self._surfaces[cache_key]
        surface = load_image(self.placeholder_path)
        self._surfaces[cache_key] = surface
        return surface

    def surface(self, name: Optional[str]) -> Optional[pygame.Surface]:
        """What to paint for this character.  The whole of the fallback rule.

        A character's own portrait when there is one AND it loads; the
        placeholder otherwise.  A file that is missing and a file that is
        corrupt are the same answer on purpose, because both want the same
        fallback — a half-downloaded PNG must cost a character its likeness,
        not its panel.
        """
        own = self._own_surface(name)
        if own is not None:
            return own
        return self.placeholder_surface()
