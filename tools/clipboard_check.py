#!/usr/bin/env python3
"""
Clipboard checker.

Walks through the cross-application scenarios that a test suite cannot reach:
a headless test box has no Discord, no browser and no Notepad, and pygame's
own clipboard on such a machine is a buffer inside the process — so a green
test run proves the API is used correctly, not that text crossed the window
manager.  This is the tool for proving the second part, on a real desktop.

    python tools/clipboard_check.py

It opens a small window (SDL needs one for the clipboard), reports which
backend it found, and then asks you to paste in and out of another application.
The window must stay open while another application pastes: on X11 the copying
program serves the text on request, which is why the game keeps running.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame  # noqa: E402

from pedzacy_piotrek.ui import clipboard  # noqa: E402

MARKER = "PEDZACY-PIOTREK-K7M2QD-Zażółć-gęślą-jaźń"


def _pump(seconds: float) -> None:
    """Run an event loop, because a clipboard owner has to answer requests."""
    end = time.time() + seconds
    while time.time() < end:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
        time.sleep(0.02)


SAMPLES = [
    "abc",
    "ABCD12",
    "123456",
    "ąćęłńóśźż",
    "wss://piotrek.up.railway.app",
]


def _backend() -> str:
    scrap = getattr(pygame, "scrap", None)
    if scrap is None:
        return "pygame.scrap is missing entirely"
    if getattr(scrap, "put_text", None) is not None:
        return "pygame.scrap.put_text / get_text (pygame-ce)"
    return "pygame.scrap.put / get (upstream pygame)"


def main() -> int:
    pygame.init()
    pygame.display.set_mode((520, 140))
    pygame.display.set_caption("Pędzący Piotrek — clipboard check")

    print(f"pygame {pygame.version.ver}, SDL {'.'.join(map(str, pygame.version.SDL))}")
    print(f"video driver: {pygame.display.get_driver()}")
    print(f"backend:      {_backend()}")

    ready = clipboard._ensure_ready()
    print(f"system clipboard available: {ready}")
    if not ready:
        print("\n!! The system clipboard could not be reached on this machine.")
        print("   Copy and paste will still work inside the game, through the")
        print("   internal fallback — but not with other applications.")
        return 1

    print("\n0/2  Every sample survives a copy")
    for sample in SAMPLES:
        clipboard.copy(sample)
        back = clipboard.paste()
        print(f"     {'ok ' if back == sample else 'BAD'} {sample!r} -> {back!r}")
    print(f"     format used: {clipboard._text_format}")
    print("     (this only proves the round trip through pygame; on Windows the")
    print("      corruption of stage 16 was invisible here, because pygame hands")
    print("      back its own bytes while the game owns the clipboard.  Step 1")
    print("      below is the one that counts.)")

    print("\n1/2  Game → other application")
    clipboard.copy(MARKER)
    print(f"     Copied: {MARKER}")
    print("     Now paste into Discord, a browser or Notepad, and check that")
    print("     what arrives is that string CHARACTER FOR CHARACTER — this is")
    print("     where UTF-8 written into a UTF-16 clipboard format shows up.")
    print("     (this window stays alive for 30 s so the paste can be served)")
    _pump(30)

    print("\n2/2  Other application → game")
    print("     Copy anything in another application now (20 s)...")
    before = clipboard.paste()
    deadline = time.time() + 20
    seen = before
    while time.time() < deadline:
        _pump(0.5)
        seen = clipboard.paste()
        if seen != before:
            break

    if seen and seen != MARKER and seen != before:
        print(f"     Read back: {seen!r}")
        print("\nOK — text crossed in both directions.")
        return 0

    print(f"     Read back: {seen!r}")
    print("\nNothing new arrived. If you did copy something elsewhere, the")
    print("system clipboard is not reaching this process — say so in the bug")
    print("report along with the three lines at the top of this output.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        pygame.quit()
