"""THE MODAL STACK — one ordered list behind both painting and input.

WHY THIS EXISTS
===============
Until stage 44 ``GameScreen`` kept TWO hand-written orderings of the same
dialogs: the chain of ``if ... .active:`` tests at the top of ``handle_event``
and the sequence of ``.draw()`` calls at the bottom of ``draw``.  Nothing tied
them together, so they drifted — and a drift between them is not cosmetic, it
is a window that is painted on top while the window underneath answers the
click.  That is exactly what round 7 produced: the Mod Patusa selection drawn
over the Chest limit, and the Chest limit eating the clicks.

So there is now ONE list.  Registration order IS priority IS paint order:
the first modal registered is painted first and is therefore the bottom of the
stack; the last registered is painted last and owns the screen.

    INVARIANT:  the visually topmost ACTIVE modal is the one that receives
                input, and no other modal receives any.

THE RULES
---------
* Only the topmost active modal is offered an event.  A lower modal is
  PENDING: still on screen, still holding its state, but not actionable until
  everything above it has resolved.  This is what turns two simultaneously
  clickable windows into a queue.
* A modal may be ``blocking`` (it swallows everything, like the chest limit)
  or not (it lets navigation reach the board, like the mod vote).  A
  NON-blocking modal still swallows any click that lands inside a lower active
  modal, or the player would be clicking a window that is visibly covered.
* Keyboard is separate from mouse, because most of these dialogs deliberately
  let S/F/Tab through to the game.  ``blocks_keyboard`` says otherwise.
* Esc is a modal's LAST resort, not its first: the modal's own ``handle`` sees
  the key first (several of them already give Esc a meaning), and only if it
  declines does ``resolve`` run the random valid fallback.

WHAT A MODAL IS NOT
-------------------
Announcements that nobody clicks (the elimination notice, the connection
banner, the debug panel) are not modals and are not registered.  A thing
belongs here only if it can OWN input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import pygame

Point = Tuple[int, int]

#: Events that carry a cursor position, and are therefore the ones ownership
#: has to be strict about.  A modal that is on top must not let one of these
#: reach a modal underneath it.
_MOUSE_EVENTS = (
    pygame.MOUSEBUTTONDOWN,
    pygame.MOUSEBUTTONUP,
    pygame.MOUSEMOTION,
    pygame.MOUSEWHEEL,
)


@dataclass
class Modal:
    """One interactive window, described to the stack rather than by it.

    Every field is a callable so the stack holds no copy of anything: it asks
    the overlay what is true THIS frame, exactly as the HUD panels read the
    live state instead of caching it.
    """

    #: Stable identifier.  Tests assert on these, and the status of the input
    #: owner is far easier to read as a name than as an index.
    name: str
    #: Whether this window is on screen at all right now.
    is_active: Callable[[], bool]
    #: Offer it an event.  True means "consumed, stop here".
    handle: Callable[[pygame.event.Event, Point], bool]
    #: Paint it.  Overlays no-op when inactive, so this is called every frame.
    draw: Callable[[], None]
    #: True: swallow every event, so nothing behind it moves.  False: let
    #: anything it did not want fall through to the board and the panels.
    blocking: bool = True
    #: True: swallow KEYDOWN as well.  Most dialogs deliberately do not, so
    #: that looking around the board keeps working while one is open.
    blocks_keyboard: bool = True
    #: Is this point inside the window?  Used two ways: a non-blocking modal
    #: consumes clicks on its own furniture, and a PENDING modal underneath
    #: absorbs clicks so they cannot fall through to the game behind it.
    covers: Optional[Callable[[Point], bool]] = None
    #: Esc's random valid fallback.  Returns True if it resolved something.
    #: ``None`` means "Esc has no business auto-answering this" — the pause
    #: menu and the ending are navigation, not a choice a die can make.
    resolve: Optional[Callable[[], bool]] = None


class ModalStack:
    """Ordered registry of every window that can own input.

    Registration order is bottom-to-top.  Read the order in
    ``GameScreen._register_modals`` as the answer to "what is above what": it
    is the only place that decision is written down.
    """

    def __init__(self) -> None:
        self._modals: List[Modal] = []

    # ── building ─────────────────────────────────────────────────────────────
    def register(self, modal: Modal) -> Modal:
        self._modals.append(modal)
        return modal

    @property
    def modals(self) -> Sequence[Modal]:
        return tuple(self._modals)

    @property
    def names(self) -> List[str]:
        return [modal.name for modal in self._modals]

    # ── what is up ───────────────────────────────────────────────────────────
    def active(self) -> List[Modal]:
        """Every active modal, BOTTOM first."""
        return [modal for modal in self._modals if modal.is_active()]

    def top(self) -> Optional[Modal]:
        """The modal that owns input, or ``None`` if none is up."""
        live = self.active()
        return live[-1] if live else None

    def owner(self) -> Optional[str]:
        """The name of the input owner — the one assertion tests really want."""
        top = self.top()
        return None if top is None else top.name

    def pending(self) -> List[str]:
        """Names of the modals that are up but NOT actionable, topmost first.

        These are the queue: still drawn, still holding their state, waiting
        for everything above them to resolve.
        """
        return [modal.name for modal in reversed(self.active()[:-1])]

    def is_pending(self, name: str) -> bool:
        return name in self.pending()

    def index_of(self, name: str) -> int:
        return self.names.index(name)

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Point) -> bool:
        """Route one event.  True means the caller must go no further.

        The whole fix lives in the first two lines: ONE modal is asked, and it
        is the topmost active one.  Everything after that decides how much of
        what it did not want is allowed to escape.
        """
        live = self.active()
        if not live:
            return False
        top = live[-1]

        if top.handle(event, mouse):
            return True

        # Esc, second: the modal's own handler had first refusal, because
        # several of them already read Esc as "back out of the confirmation"
        # and that meaning is not ours to take away (N123).
        if (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                and top.resolve is not None):
            if top.resolve():
                return True

        if event.type == pygame.KEYDOWN:
            return top.blocks_keyboard
        if event.type not in _MOUSE_EVENTS:
            return top.blocking
        if top.blocking:
            return True

        # A non-blocking modal lets NAVIGATION past — the wheel and the middle
        # drag, so a hunter waiting on four other votes can still look around —
        # but never a CLICK onto another window.  A press inside its own panel,
        # or inside any pending modal underneath, stops here: those pixels
        # belong to a dialog, and neither the game nor the covered dialog may
        # act on them.
        if event.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            return False
        if top.covers is not None and top.covers(mouse):
            return True
        for modal in live[:-1]:
            if modal.covers is not None and modal.covers(mouse):
                return True
        return False

    def resolve_top(self) -> bool:
        """Answer the active modal at random, as Esc does.  For tests."""
        top = self.top()
        if top is None or top.resolve is None:
            return False
        return top.resolve()

    # ── painting ─────────────────────────────────────────────────────────────
    def draw(self) -> None:
        """Paint bottom to top, which is the order they were registered in.

        Every overlay checks its own ``active`` and returns, so this does not
        filter: painting is allowed to be unconditional, ownership is not.
        """
        for modal in self._modals:
            modal.draw()
