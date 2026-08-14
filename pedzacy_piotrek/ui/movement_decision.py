"""
Answering a paused movement — "Nie masz Rosji" (stage 36).

Two controls under the recently-played strip while an opponent's movement is
waiting on this seat, and a confirmation dialog in front of the card before the
movement is actually stopped.

WHAT THIS FILE DOES NOT DECIDE, and every one of them is load-bearing:

* WHETHER there is a decision, WHO may answer it and WHETHER an answer is still
  valid.  All three come from the authoritative
  :class:`~pedzacy_piotrek.engine.game_state.GameState`, read on the frame it
  is drawn.  This file has no copy of any of it.
* WHEN the window closes.  The countdown here is a PICTURE: it starts when the
  event arrives and it is redrawn every frame, and if it reaches zero before
  the authority says so it simply stops the buttons responding.  The command
  that ends the window comes from the authority (the room online, the local
  session hot-seat), never from a client's clock.
* WHAT HAPPENS to the card.  Both buttons produce a Command and nothing else;
  the engine moves the card, spends the veto and passes the turn.

The card in the dialog goes through
:meth:`~pedzacy_piotrek.render.card_renderer.CardRenderer.draw_in` with the
ordinary ``reveal`` hover, so a Signature card is a Signature card here too and
there is no branch in this file on whether a card has artwork.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

import pygame

from ..cards.base_card import Card
from ..engine import commands as cmd
from ..engine.animation import approach
from ..render.card_renderer import CardRenderer
from ..render.renderer import Renderer
from .layout import Layout

BLOCK_CAPTION = "ZABLOKUJ RUCH"
ACCEPT_CAPTION = "AKCEPTUJ RUCH"
CONFIRM_CAPTION = "AKCEPTUJ"
CANCEL_CAPTION = "ANULUJ"


class MovementDecision:
    """The blocker's controls, and the confirmation in front of the block."""

    def __init__(self, state, seat: Callable[[], int]) -> None:
        self.state = state
        #: Which seat this machine is answering FOR.  A callable because a
        #: hot-seat game changes it between turns, exactly as the hand fan
        #: reads it and for the same reason.
        self.seat = seat
        #: Seconds left of the picture of the countdown.  Never authoritative.
        self.left = 0.0
        self.confirming = False
        self.appear = 0.0
        self.hover = 0.0
        self._card_uid: Optional[int] = None

    # ── what the state says ──────────────────────────────────────────────────
    @property
    def decision(self):
        return self.state.pending_movement

    @property
    def active(self) -> bool:
        """True while THIS seat still owes the table an answer.

        A seat that has already accepted keeps its veto but has nothing left to
        say about this movement, so its buttons go away — while the window may
        still be open for somebody else.
        """
        decision = self.decision
        if decision is None:
            return False
        return self.seat() in decision.waiting_for

    @property
    def card(self) -> Optional[Card]:
        """The card being considered, found the way everything finds cards."""
        decision = self.decision
        if decision is None:
            return None
        return self.state.find_card(decision.card_uid)

    def on_opened(self, seconds: float, card_uid: int) -> None:
        """A window has opened: start drawing a countdown for it."""
        self.left = max(0.0, float(seconds))
        self.confirming = False
        self.appear = 0.0
        self._card_uid = card_uid

    def on_closed(self) -> None:
        self.left = 0.0
        self.confirming = False
        self._card_uid = None

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        decision = self.decision
        if decision is None:
            if self.confirming or self.left:
                self.on_closed()
            return
        if self._card_uid != decision.card_uid:
            # Arrived without the event — a client that rebuilt its replica
            # mid-window, or a hot-seat game where nothing was subscribed.
            self.on_opened(decision.seconds, decision.card_uid)
        self.left = max(0.0, self.left - dt)
        self.appear = approach(self.appear, 1.0, 12.0, dt)
        if self.confirming:
            self.hover = approach(self.hover, 1.0, 10.0, dt)
        else:
            self.hover = 0.0

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int],
                     layout: Layout) -> Optional[cmd.Command]:
        """Returns the command to submit, or ``None``.

        The dialog is MODAL while it is up — it consumes every click, including
        the ones outside it — so a confirmation cannot be answered by accident
        through the table underneath.
        """
        if not self.active:
            return None
        if event.type == pygame.KEYDOWN and self.confirming:
            if event.key == pygame.K_ESCAPE:
                # Cancelling accepts the movement, and the brief is explicit
                # that it also ends the countdown at once: the player has
                # decided, and the veto is not spent.
                self.confirming = False
                return cmd.AcceptMovement(player_index=self.seat())
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.confirming = False
                return cmd.BlockMovement(player_index=self.seat())
            return None
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return None

        if self.confirming:
            confirm, cancel = layout.movement_confirm_buttons()
            if confirm.collidepoint(mouse):
                self.confirming = False
                return cmd.BlockMovement(player_index=self.seat())
            if cancel.collidepoint(mouse):
                self.confirming = False
                return cmd.AcceptMovement(player_index=self.seat())
            return None

        block, accept = layout.movement_decision_buttons()
        if block.collidepoint(mouse):
            # NOT a block yet.  The brief is explicit: the first click opens a
            # confirmation, because spending the only use of a card by
            # mis-clicking a small button is not a decision anybody made.
            self.confirming = True
            return None
        if accept.collidepoint(mouse):
            return cmd.AcceptMovement(player_index=self.seat())
        return None

    def consumes_click(self, mouse: Tuple[int, int], layout: Layout) -> bool:
        """Whether the table underneath must not see this click."""
        if not self.active:
            return False
        if self.confirming:
            return True
        panel = layout.movement_decision_panel()
        return bool(panel.collidepoint(mouse))

    # ── drawing ──────────────────────────────────────────────────────────────
    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        panel = layout.movement_decision_panel()
        scale = layout.ui_scale
        r.premium_panel(panel, surface, radius=10, border=theme.brass_light,
                        glow=theme.warning, glow_strength=0.30, shadow=14)
        r.text(f"NIE MASZ ROSJI  ·  {self.left:0.0f}s",
               r.fonts.get(int(11 * scale), bold=True), theme.text_heading,
               surface, center=(panel.centerx, panel.top + int(14 * scale)))

        block, accept = layout.movement_decision_buttons()
        self._button(r, surface, block, BLOCK_CAPTION, mouse,
                     accent=theme.warning)
        self._button(r, surface, accept, ACCEPT_CAPTION, mouse)

    def _button(self, r: Renderer, surface: pygame.Surface, rect: pygame.Rect,
                caption: str, mouse: Tuple[int, int], accent=None) -> None:
        theme = r.theme
        style = r.emphasis(fill=theme.btn_idle_bg, border=theme.btn_idle_border,
                           text=theme.btn_text,
                           hover=1.0 if rect.collidepoint(mouse) else 0.0,
                           enabled=True,
                           accent=accent or theme.brass_light)
        drawn = r.interactive_panel(rect, style, surface, radius=8)
        r.fit_spaced_text(caption, drawn, style.text, surface, spacing=1,
                          padding=6, min_size=8, height_ratio=0.46)

    def draw_confirm(self, r: Renderer, cards: CardRenderer, layout: Layout,
                     surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        """The confirmation, drawn over everything else."""
        if not self.active or not self.confirming:
            return
        theme = r.theme
        scale = layout.ui_scale
        shade = pygame.Surface((layout.win_w, layout.win_h), pygame.SRCALPHA)
        shade.fill((6, 10, 8, 170))
        surface.blit(shade, (0, 0))

        panel = layout.movement_confirm_panel()
        r.premium_panel(panel, surface, radius=14, border=theme.brass_light,
                        glow=theme.warning, glow_strength=0.35, shadow=20)
        r.spaced_text("ZABLOKOWAĆ TEN RUCH?",
                      r.fonts.get(int(16 * scale), bold=True),
                      theme.text_heading, surface,
                      center=(panel.centerx, panel.top + int(26 * scale)),
                      spacing=3, shadow=True)

        card = self.card
        rect = layout.movement_confirm_card_rect()
        if card is not None:
            # The ordinary renderer, with the ordinary reveal: the dialog is
            # where the player reads the card, so a Signature card opens here
            # exactly as it does in the hand.
            hovered = rect.collidepoint(mouse)
            cards.draw_in(card, rect, surface, highlighted=hovered,
                          border_color=theme.deck_colors.get(card.deck_id),
                          reveal=1.0 if hovered else self.hover)

        r.text("Zużyjesz swoją jedyną możliwość zablokowania",
               r.fonts.get(int(12 * scale)), theme.text_dim, surface,
               midtop=(panel.centerx, rect.bottom + int(8 * scale)))

        confirm, cancel = layout.movement_confirm_buttons()
        self._button(r, surface, confirm, CONFIRM_CAPTION, mouse,
                     accent=theme.warning)
        self._button(r, surface, cancel, CANCEL_CAPTION, mouse)
