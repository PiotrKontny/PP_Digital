"""
Clipboard access.

The **operating system clipboard is the clipboard**.  Copying in the game must
land in Discord, a browser or Notepad, and anything copied there must paste into
the game.  Nothing in here is allowed to make the application feel sandboxed.

An in-process buffer exists only for machines where the system clipboard is
genuinely unreachable (an SDL driver without clipboard support, a clipboard API
that fails, a headless test box).  It is a last resort, never a default: every
copy and every paste tries the system first and only falls back when that
attempt could not be made at all.

Nothing here raises: a clipboard that is unavailable is not a reason to lose a
keystroke, let alone crash a menu.


Why this file talks to ``pygame.scrap`` the long way round
---------------------------------------------------------
``pygame.scrap`` is not one API but three, depending on which pygame and which
platform is installed:

* ``put_text`` / ``get_text`` — convenient, but they exist only in pygame-ce.
  Upstream pygame 2.6 (what ``requirements.txt`` asks for) has neither, and
  calling them raises ``AttributeError``;
* ``put`` / ``get`` with a MIME type — present everywhere, but the *only* type
  upstream pygame 2's SDL2 backend accepts is the exact string
  ``"text/plain;charset=utf-8"``.  ``pygame.SCRAP_TEXT`` is ``"text/plain"``,
  which that backend rejects with "content could not be placed in clipboard";
* platform types (``SCRAP_TEXT``, ``UTF8_STRING``, ``TEXT``) which older builds
  and other backends do want.

So both call styles are tried, and the working text type is discovered once and
then remembered.  Guessing a single one of them is what broke this module
before — see the note in CHANGELOG_LLM.md, Stage 15.
"""

from __future__ import annotations

from typing import Optional

import pygame

#: Used only when the system clipboard could not be reached at all.
_fallback: str = ""
_checked = False
_available = False
#: The MIME/type string this platform actually accepted, learnt on first copy.
_text_type: Optional[str] = None

#: Tried in order.  The SDL2 spelling comes first because it is the one upstream
#: pygame 2 accepts; the rest cover other backends and older builds.
_TEXT_TYPES = (
    "text/plain;charset=utf-8",
    "text/plain",
    "UTF8_STRING",
    "TEXT",
    "STRING",
)


def _candidate_types() -> tuple:
    """Text types to try, best guess first.

    ``pygame.SCRAP_TEXT`` is read at call time rather than at import: it is a
    platform constant and on Windows it is not necessarily a MIME string.
    """
    types = []
    if _text_type:                      # already proven to work here
        types.append(_text_type)
    types.extend(_TEXT_TYPES)
    scrap_text = getattr(pygame, "SCRAP_TEXT", None)
    if scrap_text is not None and scrap_text not in types:
        types.append(scrap_text)
    return tuple(types)


def _ensure_ready() -> bool:
    """Probe the system clipboard, remembering only a definite answer.

    "No display yet" is NOT a definite answer, and caching it as one was a
    latent trap: any call before the window exists would have pinned
    ``_available`` to False for the rest of the session, quietly demoting every
    copy and paste in the game to the internal buffer.  The system clipboard
    would then work for nobody and there would be nothing to see in a log.
    So a missing display leaves the question open and it is asked again next
    time; only a real success or a real failure is recorded.

    "No window yet" is treated the same way, for the same reason: some backends
    need one before ``scrap.init()`` can succeed, and a probe made between
    ``display.init()`` and ``display.set_mode()`` must not decide the session.
    """
    global _checked, _available
    if _checked:
        return _available
    try:
        if not pygame.display.get_init():
            return False            # deliberately not cached
        if pygame.display.get_surface() is None:
            return False            # window not up yet — also not an answer
        scrap = getattr(pygame, "scrap", None)
        if scrap is None:
            _available = False
        else:
            init = getattr(scrap, "init", None)
            if init is not None:
                init()
            get_init = getattr(scrap, "get_init", None)
            _available = bool(get_init()) if get_init is not None else True
            if _available:
                # Target the clipboard proper, not the X11 primary selection.
                # A no-op on SDL2; it matters on the backends that still
                # distinguish the two.
                try:
                    scrap.set_mode(pygame.SCRAP_CLIPBOARD)
                except Exception:   # pragma: no cover - platform dependent
                    pass
    except Exception:       # pragma: no cover - platform dependent
        _available = False
    _checked = True
    return _available


def _decode(raw) -> str:
    """Bytes from the clipboard into text, whatever the platform encoded them as."""
    if isinstance(raw, str):
        return raw
    data = bytes(raw)
    body = data.rstrip(b"\x00")
    if b"\x00" in body:                 # interleaved NULs: UTF-16 from Windows
        for encoding in ("utf-16-le", "utf-16"):
            try:
                return body.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                pass
    for encoding in ("utf-8", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def _system_copy(text: str) -> bool:
    """Write to the OS clipboard.  True if it actually got there."""
    global _text_type
    scrap = getattr(pygame, "scrap", None)
    if scrap is None:
        return False

    put_text = getattr(scrap, "put_text", None)     # pygame-ce
    if put_text is not None:
        try:
            put_text(text)
            return True
        except Exception:   # pragma: no cover - platform dependent
            pass

    put = getattr(scrap, "put", None)
    if put is None:         # pragma: no cover - platform dependent
        return False
    data = text.encode("utf-8")
    for text_type in _candidate_types():
        try:
            put(text_type, data)
        except Exception:
            continue        # this platform does not want that type; try the next
        _text_type = text_type
        return True
    return False


def _system_paste() -> Optional[str]:
    """Read the OS clipboard.

    Returns the text (possibly ``""`` — an empty system clipboard is a real
    answer and must not be papered over with a stale internal buffer), or
    ``None`` when the clipboard could not be read at all, which is the only
    case that justifies the fallback.
    """
    global _text_type
    scrap = getattr(pygame, "scrap", None)
    if scrap is None:
        return None

    get_text = getattr(scrap, "get_text", None)     # pygame-ce
    if get_text is not None:
        try:
            return get_text() or ""
        except Exception:   # pragma: no cover - platform dependent
            pass

    get = getattr(scrap, "get", None)
    if get is None:         # pragma: no cover - platform dependent
        return None
    readable = False
    for text_type in _candidate_types():
        try:
            raw = get(text_type)
        except Exception:
            continue        # unsupported type on this platform, not a failure
        readable = True
        if raw:
            _text_type = text_type
            return _decode(raw)
    return "" if readable else None


def _flatten(text: str) -> str:
    """Strip the trailing NULs some platforms include and flatten newlines.

    These are single-line fields, so a two-line paste becomes one line rather
    than losing everything after the first break.  CRLF is collapsed first:
    text copied in a Windows application would otherwise arrive with a double
    space at every line break.
    """
    text = text.replace("\x00", "").replace("\r\n", "\n")
    return text.replace("\r", " ").replace("\n", " ")


def copy(text: str) -> None:
    """Put text on the system clipboard; keep a copy in case that failed."""
    global _fallback
    _fallback = text
    if not _ensure_ready():
        return
    _system_copy(text)


def paste() -> str:
    """Read the clipboard.  Returns "" rather than failing.

    The system clipboard wins whenever it can be read — including when it is
    empty, because "the user copied nothing" is an answer.  The internal buffer
    is reached only on a machine where reading is impossible.
    """
    if _ensure_ready():
        text = _system_paste()
        if text is not None:
            return _flatten(text)
    return _flatten(_fallback)


def reset() -> None:
    """Forget the fallback buffer and re-probe the platform (tests).

    Also blanks the system clipboard, because after the fix that is where the
    text really is: without this a value copied by one test would still be
    readable in the next one, and tests would pass or fail depending on order.
    Only tests call this.
    """
    global _fallback, _checked, _available, _text_type
    _fallback = ""
    if _ensure_ready():
        _system_copy("")
    _checked = False
    _available = False
    _text_type = None
