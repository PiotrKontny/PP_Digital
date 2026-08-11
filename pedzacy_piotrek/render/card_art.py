"""
Signature Card artwork: which file belongs to which card, and loading it.

A card with a picture in ``assets/card_art`` is drawn as a SIGNATURE CARD —
the artwork fills the face and the game overlays the title and, on hover, the
description.  Every other card keeps the parchment face it has always had.
``CardRenderer`` asks this module one question ("is there a picture for this
card?") and branches on the answer; the branch is the whole of the feature.

WHY A SEPARATE FOLDER FROM ``assets/images/cards``
    They mean different things.  ``CardDef.image`` is a small illustration
    drawn INSIDE the parchment body of a standard card, addressed by path.  A
    file here REPLACES the face, and is addressed by NAME.  Sharing one folder
    would make "does this card have a picture?" un-answerable without opening
    the file.

HOW A FILE FINDS ITS CARD
    Both sides are put through :func:`slugify` and compared:

        assets/card_art/Troll.png            ->  "troll"
        the card titled "Troll"              ->  "troll"

    so the owner's workflow is "save the file under the card's name".  It
    survives the spelling differences that actually happen — ``Rage Quit.png``,
    ``Rage_Quit.png`` and ``rage-quit.PNG`` are one key, and ``Stanczyk.png``
    finds "Stańczyk" because the diacritics fold.

    Titles are NOT unique across decks: "Shady" is both a Mod Patusa and a
    Chest card, and they are different pictures.  A file in a SUBFOLDER named
    after a deck is scoped to it, and is tried first:

        assets/card_art/mods/Shady.png       ->  "mods/shady"
        assets/card_art/chest/Shady.png      ->  "chest/shady"

    A bare name that two different subfolders both claim is ambiguous and is
    dropped rather than guessed at — the scoped keys still work.

    ``CardDef.art`` overrides the derived name when the file cannot be called
    after the card.  It is a NAME, not a path: ``"art": "chest/shady"``.

WHY MATCHING ON THE TITLE IS NOT N7
    N7 forbids inferring card BEHAVIOUR from titles, because the prototype's
    badges and effects broke the moment a card was renamed.  Nothing here
    touches behaviour: the worst a rename can do is un-match a picture, and an
    un-matched picture is the standard card face, which is the same fallback a
    missing file gets.  Pin it with ``art`` if that matters.

NOTHING HERE MAY RAISE
    A card-art folder is filled in by hand over months.  Half of it will be
    missing, one file will be a renamed ``.psd``, and none of that may stop a
    game: every failure path returns ``None`` and the card renders as it always
    did.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Dict, Optional, Tuple

import pygame

from ..cards.base_card import CardDef
from ..config import settings

#: Letters that ``unicodedata`` will not decompose, because they are not a base
#: letter plus a mark.  Polish "ł" is the one that matters here; the rest are
#: cheap insurance for a future card named in another language.
_FOLD = {"ł": "l", "Ł": "l", "đ": "d", "ø": "o", "æ": "ae", "ß": "ss"}


def slugify(text: str) -> str:
    """A filesystem- and diacritic-insensitive key for a name.

    ``"Stańczyk"`` and ``"Stanczyk"``, ``"Rage Quit"`` and ``"rage-quit"`` all
    reduce to the same string, so the owner does not have to remember how a
    card was spelled to name its picture.
    """
    folded = "".join(_FOLD.get(char, char) for char in text)
    stripped = "".join(
        char for char in unicodedata.normalize("NFKD", folded)
        if not unicodedata.combining(char)
    )
    out: list[str] = []
    for char in stripped.lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


def _scoped(scope: str, name: str) -> str:
    return f"{scope}/{name}" if scope else name


class CardArtLibrary:
    """The card-art folder, indexed once and cached.

    The scan happens at construction rather than per lookup: a hand redraws
    sixty times a second and ``Path.exists`` sixty times a second per card is a
    syscall storm for an answer that cannot change while the game is running.
    Call :meth:`refresh` after dropping a file in, if it ever matters.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory is not None else settings.CARD_ART_DIR
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
        # Which scope each BARE key came from, so a name two decks both use can
        # be recognised as ambiguous instead of resolving to whichever file the
        # filesystem happened to list first.
        bare_scope: Dict[str, str] = {}
        ambiguous: set[str] = set()
        if not self.directory.is_dir():
            return found

        try:
            paths = sorted(self.directory.rglob("*"))
        except OSError:  # pragma: no cover - unreadable folder
            return found

        for path in paths:
            try:
                if not path.is_file():
                    continue
                if path.suffix.lower() not in settings.CARD_ART_SUFFIXES:
                    continue
                relative = path.relative_to(self.directory)
            except (OSError, ValueError):  # pragma: no cover - odd filesystem
                continue

            name = slugify(path.stem)
            if not name:
                continue
            scope = slugify(relative.parent.name) if relative.parent.name else ""

            self._offer(found, _scoped(scope, name), path)
            if scope:
                # A scoped file also answers to its bare name, unless another
                # scope has already claimed it.
                if name in ambiguous:
                    pass
                elif name in bare_scope and bare_scope[name] != scope:
                    ambiguous.add(name)
                    found.pop(name, None)
                else:
                    bare_scope[name] = scope
                    self._offer(found, name, path)

        return found

    @staticmethod
    def _offer(found: Dict[str, Path], key: str, path: Path) -> None:
        """Register ``path`` under ``key``, preferring the better format.

        ``Troll.png`` and ``Troll.jpg`` side by side is a normal state for a
        folder somebody is working in.  Ranking by ``CARD_ART_SUFFIXES`` makes
        the winner the same one on every machine, which alphabetical order
        would not.
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
    def keys_for(self, definition: CardDef) -> Tuple[str, ...]:
        """The names this card answers to, most specific first.

        Three cases, and the difference between the last two matters:

            art is None   ->  derive the name from the title (the default)
            art is "..."  ->  use exactly that name
            art is ""     ->  NO artwork, and do not go looking

        The empty string is the opt-out.  Without it there is no way to say
        "this card keeps the parchment face" other than renaming the picture,
        which is a strange thing to have to do — and a test that builds a
        throwaway card out of a real definition needs to strip the artwork the
        same way it already strips ``image``.
        """
        declared = definition.art
        if declared is not None:
            declared = declared.strip()
            if not declared:
                return ()
            # An explicit name may carry a scope; slugify each part so
            # ``"Chest/Rage Quit"`` and ``"chest/rage_quit"`` are one key.
            parts = [slugify(part) for part in declared.replace("\\", "/").split("/")]
            key = "/".join(part for part in parts if part)
            return (key,) if key else ()
        title = slugify(definition.title)
        if not title:
            return ()
        return (_scoped(slugify(definition.deck_id), title), title)

    def key(self, definition: CardDef) -> Optional[str]:
        """The name this card's artwork is actually filed under, if any."""
        for candidate in self.keys_for(definition):
            if candidate in self._files:
                return candidate
        return None

    def path(self, definition: CardDef) -> Optional[Path]:
        key = self.key(definition)
        return self._files.get(key) if key else None

    def has_art(self, definition: CardDef) -> bool:
        """Whether this card would render as a Signature Card.

        True only when the file is there AND loads: a card configured for
        artwork whose picture is missing or corrupt is a STANDARD card, not a
        broken one.
        """
        return self.surface(definition) is not None

    # ── loading ──────────────────────────────────────────────────────────────
    def surface(self, definition: CardDef) -> Optional[pygame.Surface]:
        """The artwork, loaded once.  ``None`` for "draw the normal card"."""
        key = self.key(definition)
        if key is None:
            return None
        if key in self._surfaces:
            return self._surfaces[key]
        surface = self._load(self._files[key])
        self._surfaces[key] = surface
        return surface

    @staticmethod
    def _load(path: Path) -> Optional[pygame.Surface]:
        """Read one file, or answer ``None``.

        Broad on purpose.  ``pygame.image.load`` raises ``pygame.error`` for a
        truncated PNG, ``FileNotFoundError`` for a file deleted between the
        scan and now, and has been known to raise ``ValueError`` on a file
        whose extension lies about its contents.  None of those may reach the
        frame loop, and the answer to all three is the same.
        """
        try:
            image = pygame.image.load(str(path))
        except Exception:
            return None
        try:
            # ``convert_alpha`` needs a display; without one (headless tests,
            # a screenshot tool) the unconverted surface draws correctly, just
            # more slowly.
            return image.convert_alpha()
        except pygame.error:
            return image
