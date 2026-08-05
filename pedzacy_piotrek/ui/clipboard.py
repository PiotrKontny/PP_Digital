"""
Clipboard access.

pygame's clipboard needs a display and is not available on every platform or
driver, so this wraps it and keeps an internal buffer as a fallback.  With the
fallback, copy and paste still work *within* the game even when the system
clipboard cannot be reached — which is the common case on a headless test
machine and on some Linux setups.

Nothing here raises: a clipboard that is unavailable is not a reason to lose a
keystroke, let alone crash a menu.
"""

from __future__ import annotations

import pygame

#: Used when the system clipboard is unavailable.
_fallback: str = ""
_checked = False
_available = False


def _ensure_ready() -> bool:
    """Probe the system clipboard, remembering only a definite answer.

    "No display yet" is NOT a definite answer, and caching it as one was a
    latent trap: any call before the window exists would have pinned
    ``_available`` to False for the rest of the session, quietly demoting every
    copy and paste in the game to the internal buffer.  The system clipboard
    would then work for nobody and there would be nothing to see in a log.
    So a missing display leaves the question open and it is asked again next
    time; only a real success or a real failure is recorded.
    """
    global _checked, _available
    if _checked:
        return _available
    try:
        if not pygame.display.get_init():
            return False            # deliberately not cached
        pygame.scrap.init()
        _available = bool(pygame.scrap.get_init())
    except Exception:       # pragma: no cover - platform dependent
        _available = False
    _checked = True
    return _available


def copy(text: str) -> None:
    """Put text on the clipboard, falling back to an internal buffer."""
    global _fallback
    _fallback = text
    if not _ensure_ready():
        return
    try:
        pygame.scrap.put_text(text)
    except Exception:       # pragma: no cover - platform dependent
        pass


def paste() -> str:
    """Read the clipboard.  Returns "" rather than failing."""
    if _ensure_ready():
        try:
            raw = pygame.scrap.get_text()
            if raw:
                # Strip the trailing NULs some platforms include, and flatten
                # newlines: these are single-line fields.
                return raw.replace("\x00", "").replace("\r", " ").replace("\n", " ")
        except Exception:   # pragma: no cover - platform dependent
            pass
    return _fallback


def reset() -> None:
    """Forget the fallback buffer and re-probe the platform (tests)."""
    global _fallback, _checked, _available
    _fallback = ""
    _checked = False
    _available = False
