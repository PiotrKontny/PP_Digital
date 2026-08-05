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
    global _checked, _available
    if _checked:
        return _available
    _checked = True
    try:
        if not pygame.display.get_init():
            _available = False
        else:
            pygame.scrap.init()
            _available = pygame.scrap.get_init()
    except Exception:       # pragma: no cover - platform dependent
        _available = False
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
    """Forget the fallback buffer (tests)."""
    global _fallback
    _fallback = ""
