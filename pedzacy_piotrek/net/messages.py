"""
What the player is told when something goes wrong.

ONE RULE, AND IT IS THE WHOLE POINT OF THIS FILE: no protocol text, no
exception text and no library text ever reaches a screen.  Everything a player
reads about the network is chosen here.

Why a translation table rather than careful wording at each raise site: there
are three sources of failure text and only one of them is ours.  The server
writes Polish sentences, the ``websockets`` library writes English ones with
socket jargon in them, and Python writes ``ConnectionRefusedError(111)``.  The
first is usually fine, the other two never are, and a raise site cannot know
which of them it is passing on.  Funnelling all three through one function is
what makes "Nie mogę połączyć się z serwerem" the *only* thing that can be
shown for a refused connection, no matter which layer noticed it.

This is also why the fallback is generic rather than clever.  An unrecognised
failure says something true and useless ("Coś poszło nie tak z połączeniem")
instead of leaking whatever string arrived — the details go to the debug panel
(F3), which is where somebody who wants them is looking.
"""

from __future__ import annotations

import re
from typing import Optional

# ── the sentences ────────────────────────────────────────────────────────────
# Named, so a screen can compare against them and so the wording is changed in
# one place.  They answer the five questions a player actually has: is the
# server there, is the room there, is there space, am I still connected, and
# was it me who left.

CANNOT_CONNECT = "Nie mogę połączyć się z serwerem gry"
SERVER_UNAVAILABLE = "Serwer gry jest niedostępny"
SERVER_NOT_FOUND = "Nie znaleziono serwera pod tym adresem"
BAD_ADDRESS = "Nieprawidłowy adres serwera"
NO_ADDRESS = "Podaj adres serwera gry"
CONNECTION_LOST = "Utracono połączenie z serwerem"
CONNECTING = "Trwa łączenie z serwerem…"
RECONNECTING = "Połączenie przerwane — próbuję połączyć się ponownie…"

ROOM_NOT_FOUND = "Nie ma pokoju o takim kodzie"
ROOM_FULL = "Pokój jest pełny"
ROOM_CLOSED = "Pokój został zamknięty"
GAME_ALREADY_STARTED = "Gra już się rozpoczęła — nie można teraz dołączyć"
NOT_IN_ROOM = "Nie jesteś w żadnym pokoju"

SIGNED_IN_ELSEWHERE = "Ktoś zalogował się na tego gracza z innego miejsca"
VERSION_MISMATCH = "Twoja wersja gry nie pasuje do serwera — zaktualizuj grę"
HOST_ONLY = "To może zrobić tylko osoba, która założyła pokój"
LEFT_GAME = "Opuściłeś grę"

UNKNOWN = "Coś poszło nie tak z połączeniem"

#: Matched in order against the lower-cased raw reason.  First hit wins, so the
#: specific patterns come before the general ones — "connection refused" must
#: not be swallowed by a bare "connection".
#:
#: WHAT IS DELIBERATELY *NOT* HERE: the server's own Polish sentences.  It says
#: "Nie ma pokoju o kodzie ZZZZZZ" and "Tylko host może rozpocząć grę", and
#: those are better than anything this table could substitute — they name the
#: code the player actually typed and the specific thing they tried to do.  An
#: earlier version of this file matched them too and replaced them with blander
#: constants, which lost information for no gain.  The job here is to catch text
#: that was never written for a player, not to rewrite text that was.
_PATTERNS = (
    # Protocol leakage.  These are the internal messages the brief says a
    # player must never see; they become "still connecting", because that is
    # what is actually true when one arrives.
    (r"przywitanie|hello required|handshake|hello first", CONNECTING),

    # Address problems, before the generic connection ones.
    (r"no address|empty url", NO_ADDRESS),
    (r"invalid uri|invalid url|malformed uri", BAD_ADDRESS),
    (r"name or service not known|nodename nor servname|getaddrinfo|"
     r"temporary failure in name resolution", SERVER_NOT_FOUND),

    # Reachability, in the words the libraries use.
    (r"refused|connection reset by peer", CANNOT_CONNECT),
    (r"timed out|timeout", SERVER_UNAVAILABLE),
    (r"unreachable|no route to host|network is (down|unreachable)|"
     r"\b50[234]\b", SERVER_UNAVAILABLE),

    # A live connection that stopped being one.
    (r"broken pipe|going away|abnormal closure|\b100[16]\b|"
     r"connectionclosed|websocketexception", CONNECTION_LOST),
    (r"\bconnect(ion|ing)?\b.*\b(fail|error|abort)", CANNOT_CONNECT),
)


def friendly(reason: Optional[str], default: str = UNKNOWN) -> str:
    """Turn any failure text into something worth showing a player.

    Accepts ``None`` and empty strings so callers do not each need the same
    guard: a failure with no text at all is still a failure the player has to
    be told about.
    """
    text = (reason or "").strip()
    if not text:
        return default
    lowered = text.lower()
    for pattern, sentence in _PATTERNS:
        if re.search(pattern, lowered):
            return sentence
    if _looks_technical(text):
        return default
    # Something we wrote, in Polish, that no pattern claimed.  The server's
    # validation messages ("Nie wszyscy są gotowi…") land here, and they are
    # already the sentence the player needs.
    return text


def _looks_technical(text: str) -> bool:
    """Would this embarrass us on screen?

    Tracebacks, exception class names, socket errnos, URLs and JSON fragments
    are the four shapes that leak, and all four are recognisable without
    knowing which library produced them.
    """
    if re.search(r"[A-Za-z]+Error\b|Exception\b|Traceback", text):
        return True
    if re.search(r"errno|\[\d+\]|0x[0-9a-f]+", text, re.IGNORECASE):
        return True
    if re.search(r"ws://|wss://|https?://|\bws\b.*\bcode\b", text):
        return True
    if text.startswith("{") or text.startswith("["):
        return True
    # A sentence with no Polish letters and no spaces is a token, not prose.
    return " " not in text and len(text) > 12
