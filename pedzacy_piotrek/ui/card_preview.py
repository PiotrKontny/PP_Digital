"""
The enlarged hover preview — one card, drawn large, beside the one being hovered.

WHAT PROBLEM THIS SOLVES
    A card drawn in a PANEL SLOT is small.  At 1280x760 the character panel's
    ability card is 99x142 px and a mod slot card is 74x107, and at that size
    neither a Polish ability description nor a Mod Patusa's rules text can be
    read, however good the type fitting in ``CardRenderer`` is.  There is no
    font that fixes a card the size of a postage stamp.

    Until stage 48 the answer was the Signature-card REVEAL: hover the card and
    its own face re-lays itself out, title lifted, description underneath.
    That works beautifully in the hand, where a card is 200-300 px tall, and it
    fails in a slot for the reason above — it puts MORE text in the SAME small
    rectangle.  Worse, on the ability card it also took away the artwork the
    player was looking at, so hovering to read the ability replaced the ability
    with a darker version of itself.

THE RULE THIS MODULE IMPLEMENTS
    The original card is never touched.  A SECOND, larger copy of the same card
    is drawn beside it, and that copy is where the title and the description
    are read.  When the cursor leaves the card the copy goes.

WHAT IT IS NOT
    It is not a card.  Nothing here creates a ``Card``, touches a deck, a hand,
    a count or a use; nothing here hit-tests, and no click ever reaches it —
    the panels answer clicks from ``layout`` rects exactly as they did before.
    It is a picture of a card that already exists, and it lives for one frame.

HOW IT IS DRIVEN
    A panel that draws a slot calls :meth:`CardPreview.request` on the frame it
    finds the cursor over that slot; ``GameScreen`` calls :meth:`draw` once,
    late, so the preview lands above every panel and below every dialog.  The
    request is CONSUMED by that draw, so a panel that stops asking — because
    the cursor moved, or because the panel is no longer on screen — stops
    getting a preview on the very next frame with no state to unwind.

    One request per frame, last caller wins.  Two slots cannot be under one
    cursor, and if a future layout ever overlaps two, one preview is right and
    two is a mess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pygame

from ..cards.base_card import Card

Color = Tuple[int, int, int]


@dataclass
class PreviewRequest:
    """One slot's claim on this frame's preview."""

    card: Card
    #: The rect the ORIGINAL card is drawn in.  The preview is placed relative
    #: to it and never overlaps it; see ``Layout.hover_preview_gap``.
    anchor: pygame.Rect
    border_color: Optional[Color] = None


class CardPreview:
    """Collects at most one preview request per frame and draws it last."""

    def __init__(self) -> None:
        self._pending: Optional[PreviewRequest] = None
        #: Where the preview landed last time it was drawn.  Read by tests and
        #: by nothing in the game — no gesture consults it, on purpose.
        self.rect: Optional[pygame.Rect] = None

    # ── the panel side ───────────────────────────────────────────────────────
    def request(self, card: Optional[Card], anchor: pygame.Rect,
                border_color: Optional[Color] = None) -> None:
        """Ask for ``card`` to be previewed beside ``anchor`` this frame."""
        if card is None:
            return
        self._pending = PreviewRequest(card, pygame.Rect(anchor), border_color)

    def clear(self) -> None:
        self._pending = None
        self.rect = None

    @property
    def pending(self) -> Optional[PreviewRequest]:
        return self._pending

    # ── the screen side ──────────────────────────────────────────────────────
    def draw(self, ctx) -> Optional[pygame.Rect]:
        """Draw this frame's preview, if any, and consume the request.

        Returns the rect it was drawn in, or ``None``.  ``ctx`` is a
        :class:`~pedzacy_piotrek.ui.hud.HudContext`; it is not imported here
        because ``hud`` imports this module.
        """
        request, self._pending = self._pending, None
        if request is None:
            self.rect = None
            return None

        layout, cards = ctx.layout, ctx.cards
        # Size first, then SNAP it the way every other enlarged card in the
        # game is snapped: ``quantised`` rounds the height to a handful of
        # steps so the face cache is not defeated, and — the part that matters
        # here — the face is REPAINTED at the size it will be seen rather than
        # scaled up from the slot.  A blown-up 74x107 card is exactly as
        # unreadable as the original, which is the trap stage 9 documented.
        size = cards.quantised(layout.hover_preview_size(request.anchor))
        rect = layout.hover_preview_rect(request.anchor, size)

        # A shadow deep enough to lift it off whatever it is covering — it is
        # floating over panels and the board, not sitting in a well.
        #
        # Sized FROM THE GAP, because a shadow is paint like any other and the
        # promise this feature makes is that the original card is not touched.
        # A fixed spread reached back across the gap and darkened the right-hand
        # six pixels of the very card being previewed by one unit per channel —
        # invisible to the eye, caught by the byte-for-byte test, and a real
        # violation of the promise.  Reaching ``gap - 3`` keeps it clear at
        # every ui_scale, including the 0.76 of the smallest window.
        inset = max(6, int(rect.width * 0.05))
        spread = inset + max(2, layout.hover_preview_gap - 3)
        ctx.r.drop_shadow(rect.inflate(-2 * inset, -2 * inset), radius=10,
                          spread=spread, alpha=155, offset=(0, 7),
                          surface=ctx.surface)
        # The SAME face every other card in the game is drawn with, at the
        # SAME reveal the card-art hover uses.  ``reveal=1.0`` is the whole
        # point: on a Signature card that is the darkened artwork with the
        # title lifted and the description under it — the presentation this
        # preview exists to make readable — and a card without artwork ignores
        # it and paints the parchment face it always has, title, divider,
        # description and badge included.  There is no second card renderer.
        cards.draw_in(request.card, rect, ctx.surface, highlighted=True,
                      border_color=request.border_color, reveal=1.0)

        self.rect = rect
        return rect
