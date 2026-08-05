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


...and why the type string alone is not enough
----------------------------------------------
A clipboard type does not only say what the data *is*; on Windows it decides
how the bytes are read.  pygame does not compile the SDL2 backend there —
``src_c/scrap.c`` selects it with ``#if !defined(__WIN32__)``, so Windows gets
the native ``scrap_win.c``, which maps

    "text/plain;charset=utf-8"  ->  CF_UNICODETEXT   (Windows reads UTF-16LE)
    "text/plain"                ->  CF_TEXT          (Windows reads ANSI)

and then ``memcpy``s whatever bytes it was handed straight into the clipboard
**without converting anything**.  Sending UTF-8 to a type named "utf-8" is
therefore correct on the SDL2 backend and corrupting on Windows: "sdh" becomes
摳 (0x73,0x64 read as one UTF-16LE code unit, U+6473).  That was Stage 16.

So the encoding is a property of the FORMAT, not of the module, and the two
travel together in ``_TEXT_FORMATS``.  CF_UNICODETEXT also needs a two-byte
terminator, and pygame's ``pygame_scrap_put`` appends only one zero byte, so
the second is supplied here.
"""

from __future__ import annotations

import os
from typing import Optional

import pygame

#: Used only when the system clipboard could not be reached at all.
_fallback: str = ""
_checked = False
_available = False
#: The format this platform actually accepted, learnt on first copy.
_text_format: Optional[tuple] = None

#: True where pygame compiles scrap_win.c instead of the SDL2 backend.  The
#: condition is scrap.c's own: ``#if !defined(__WIN32__)`` picks SDL2.
_WINDOWS = os.name == "nt"

#: (type, encoding, extra terminator).  Tried in order.
#:
#: Windows: CF_UNICODETEXT is UTF-16LE and needs a two-byte terminator, of
#: which pygame writes one.  CF_TEXT is the local ANSI code page and cannot
#: carry every Polish letter on every machine, so it is a fallback, never the
#: first choice.
_WINDOWS_FORMATS = (
    ("text/plain;charset=utf-8", "utf-16-le", b"\x00"),    # CF_UNICODETEXT
    ("text/plain", "mbcs", b""),                           # CF_TEXT
)

#: Everywhere else the SDL2 backend hands the bytes to SDL_SetClipboardText,
#: which wants UTF-8 and terminates the string itself.
_SDL_FORMATS = (
    ("text/plain;charset=utf-8", "utf-8", b""),
    ("text/plain", "utf-8", b""),
    ("UTF8_STRING", "utf-8", b""),
    ("TEXT", "utf-8", b""),
    ("STRING", "utf-8", b""),
)

#: Kept for the tests that put text on the clipboard the way another
#: application would.
_TEXT_TYPES = tuple(entry[0] for entry in _SDL_FORMATS)


def _candidate_formats() -> tuple:
    """Formats to try, best guess first.

    ``pygame.SCRAP_TEXT`` is read at call time rather than at import: it is a
    platform constant and on Windows it is not necessarily a MIME string.
    """
    formats = []
    if _text_format:                    # already proven to work here
        formats.append(_text_format)
    formats.extend(_WINDOWS_FORMATS if _WINDOWS else _SDL_FORMATS)
    scrap_text = getattr(pygame, "SCRAP_TEXT", None)
    if scrap_text is not None and scrap_text not in [f[0] for f in formats]:
        # An unknown spelling of plain text: the platform default encoding for
        # it is the one the rest of that platform's list uses.
        formats.append((scrap_text,) + formats[-1][1:])
    return tuple(formats)


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


def _encode(text: str, encoding: str, terminator: bytes) -> Optional[bytes]:
    """Text into the bytes this clipboard format is read back as.

    ``None`` when the platform has no such codec — ``mbcs`` exists on Windows
    only — or when the text does not fit the code page, which is why the ANSI
    format is a fallback and not the first choice.
    """
    try:
        return text.encode(encoding) + terminator
    except (LookupError, UnicodeEncodeError):
        return None


def _decode(raw, encoding: str = "") -> str:
    """Bytes from the clipboard into text, whatever the platform encoded them as.

    ``encoding`` is what the format we asked for is *supposed* to hold.  It is
    tried first, but not trusted blindly: another application may have put
    something else in there, and a wrong guess about UTF-16 is silent rather
    than noisy — every byte pair is a valid code unit.  So the shape of the
    data decides, and the declared encoding only breaks ties.
    """
    if isinstance(raw, str):
        return raw
    data = bytes(raw)
    body = data.rstrip(b"\x00")
    wide = encoding.startswith("utf-16") or b"\x00" in body
    if wide:                            # interleaved NULs: UTF-16 from Windows
        # Decoded from the FULL buffer, not the stripped one: the high byte of
        # a final ASCII character is a NUL, and stripping trailing zeroes eats
        # it — "ABCD12" came back as "ABCD1".  The terminator is cut after
        # decoding instead, which is what Windows itself does.
        even = data[:len(data) - len(data) % 2]
        for candidate in ("utf-16-le", "utf-16"):
            try:
                decoded = even.decode(candidate)
            except (UnicodeDecodeError, UnicodeError):
                continue
            decoded = decoded.split("\x00")[0]
            if decoded:
                return decoded
    for candidate in (encoding, "utf-8", "latin-1"):
        if not candidate or candidate.startswith("utf-16"):
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _system_copy(text: str) -> bool:
    """Write to the OS clipboard.  True if it actually got there.

    Each format is encoded the way the platform will read that format back —
    UTF-8 for SDL2, UTF-16LE for Windows' CF_UNICODETEXT.  Encoding to one and
    declaring the other is what turned "sdh" into 摳.
    """
    global _text_format
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
    for text_format in _candidate_formats():
        text_type, encoding, terminator = text_format
        data = _encode(text, encoding, terminator)
        if data is None:
            continue        # no such codec here, or the text does not fit it
        try:
            put(text_type, data)
        except Exception:
            continue        # this platform does not want that type; try the next
        _text_format = text_format
        return True
    return False


def _system_paste() -> Optional[str]:
    """Read the OS clipboard.

    Returns the text (possibly ``""`` — an empty system clipboard is a real
    answer and must not be papered over with a stale internal buffer), or
    ``None`` when the clipboard could not be read at all, which is the only
    case that justifies the fallback.
    """
    global _text_format
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
    for text_format in _candidate_formats():
        text_type, encoding, _terminator = text_format
        try:
            raw = get(text_type)
        except Exception:
            continue        # unsupported type on this platform, not a failure
        readable = True
        if raw:
            _text_format = text_format
            return _decode(raw, encoding)
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
    global _fallback, _checked, _available, _text_format
    _fallback = ""
    if _ensure_ready():
        _system_copy("")
    _checked = False
    _available = False
    _text_format = None
