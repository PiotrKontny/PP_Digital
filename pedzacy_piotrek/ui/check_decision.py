"""
Answering a paused check — Piotrek's "Ice Block", and the 2a/2b scatter (stage 40).

Two small panels, and both of them are windows onto authoritative state rather
than things that decide anything:

* the CHECK window, while a check is waiting on Piotrek — refuse or allow;
* the SCATTER choice, while a broken tower's last group is about to land on a
  doubled row and Piotrek picks which field.

WHAT THIS FILE DOES NOT DECIDE, all of it load-bearing and all of it the same
list ``movement_decision`` carries for the same reasons:

* WHETHER there is a decision, WHO may answer it, and whether an answer is
  still valid.  Read from the engine on the frame it is drawn; there is no copy
  of any of it here.
* WHEN the window closes.  The countdown is a PICTURE.  It starts when the
  event arrives and stops the buttons responding if it reaches zero, but the
  command that actually ends the window comes from the authority — the room
  online, the local session hot-seat — never from a client's clock.  A machine
  with a fast clock therefore cannot time Piotrek out, and one with a slow
  clock cannot let him answer late: the engine rejects it either way.
* WHAT the answer means.  Both buttons produce a Command and nothing else; the
  engine cancels the check, spends the use, or lets it through.

The refusal is confirmed before it is spent, exactly as blocking a movement is,
because spending the only use of a card by mis-clicking a small button is not a
decision anybody made.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import pygame

from ..engine import commands as cmd
from ..engine.animation import approach
from ..render.renderer import Renderer
from .layout import Layout

REFUSE_CAPTION = "ODMÓW SPRAWDZENIA"
ALLOW_CAPTION = "POZWÓL SPRAWDZIĆ"
CONFIRM_CAPTION = "ODMÓW"
CANCEL_CAPTION = "ANULUJ"


class CheckDecision:
    """Piotrek's Ice Block controls, and the confirmation in front of a refusal."""

    def __init__(self, state, seat: Callable[[], int]) -> None:
        self.state = state
        #: Which seat this machine answers FOR.  A callable because a hot-seat
        #: game changes it between turns, exactly as the hand fan reads it.
        self.seat = seat
        self.left = 0.0
        self.confirming = False
        self.appear = 0.0
        self._pawn: Optional[str] = None

    # ── what the state says ──────────────────────────────────────────────────
    @property
    def decision(self):
        return getattr(self.state, "pending_check", None)

    @property
    def active(self) -> bool:
        """True while THIS seat is the one being asked."""
        decision = self.decision
        if decision is None:
            return False
        return self.seat() == decision.seat

    @property
    def pawn_name(self) -> str:
        decision = self.decision
        if decision is None:
            return ""
        pawn = self.state.library.pawn(decision.pawn_id)
        return pawn.name if pawn is not None else decision.pawn_id

    @property
    def uses_left(self) -> int:
        from ..engine import victory

        card = victory.ice_block_card(self.state)
        return 0 if card is None else int(getattr(card, "uses_left", 0))

    def on_opened(self, seconds: float, pawn_id: str) -> None:
        self.left = max(0.0, float(seconds))
        self.confirming = False
        self.appear = 0.0
        self._pawn = pawn_id

    def on_closed(self) -> None:
        self.left = 0.0
        self.confirming = False
        self._pawn = None

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        decision = self.decision
        if decision is None:
            if self.confirming or self.left:
                self.on_closed()
            return
        if self._pawn != decision.pawn_id:
            # Arrived without the event — a client that rebuilt its replica
            # mid-window, or a hot-seat game where nothing was subscribed.
            self.on_opened(decision.seconds, decision.pawn_id)
        self.left = max(0.0, self.left - dt)
        self.appear = approach(self.appear, 1.0, 12.0, dt)

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int],
                     layout: Layout) -> Optional[cmd.Command]:
        if not self.active:
            return None
        if event.type == pygame.KEYDOWN and self.confirming:
            if event.key == pygame.K_ESCAPE:
                self.confirming = False
                return cmd.AllowCheck(player_index=self.seat())
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.confirming = False
                return cmd.RefuseCheck(player_index=self.seat())
            return None
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return None

        if self.confirming:
            confirm, cancel = layout.movement_confirm_buttons()
            if confirm.collidepoint(mouse):
                self.confirming = False
                return cmd.RefuseCheck(player_index=self.seat())
            if cancel.collidepoint(mouse):
                self.confirming = False
                return cmd.AllowCheck(player_index=self.seat())
            return None

        refuse, allow = layout.check_decision_buttons()
        if refuse.collidepoint(mouse):
            # NOT a refusal yet: one click opens the confirmation.
            self.confirming = True
            return None
        if allow.collidepoint(mouse):
            return cmd.AllowCheck(player_index=self.seat())
        return None

    def consumes_click(self, mouse: Tuple[int, int], layout: Layout) -> bool:
        if not self.active:
            return False
        if self.confirming:
            return True
        return bool(layout.check_decision_panel().collidepoint(mouse))

    # ── drawing ──────────────────────────────────────────────────────────────
    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        panel = layout.check_decision_panel()
        scale = layout.ui_scale
        r.premium_panel(panel, surface, radius=10, border=theme.brass_light,
                        glow=theme.frost, glow_strength=0.30, shadow=14)
        r.text(f"ICE BLOCK  ·  {self.left:0.0f}s",
               r.fonts.get(int(11 * scale), bold=True), theme.text_heading,
               surface, center=(panel.centerx, panel.top + int(14 * scale)))

        refuse, allow = layout.check_decision_buttons()
        _button(r, surface, refuse, REFUSE_CAPTION, mouse, accent=theme.frost)
        _button(r, surface, allow, ALLOW_CAPTION, mouse)

    def draw_confirm(self, r: Renderer, layout: Layout,
                     surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        if not self.active or not self.confirming:
            return
        theme = r.theme
        scale = layout.ui_scale
        shade = pygame.Surface((layout.win_w, layout.win_h), pygame.SRCALPHA)
        shade.fill((6, 10, 8, 170))
        surface.blit(shade, (0, 0))

        panel = layout.movement_confirm_panel()
        r.premium_panel(panel, surface, radius=14, border=theme.brass_light,
                        glow=theme.frost, glow_strength=0.35, shadow=20)
        r.spaced_text("ODMÓWIĆ SPRAWDZENIA?",
                      r.fonts.get(int(16 * scale), bold=True),
                      theme.text_heading, surface,
                      center=(panel.centerx, panel.top + int(30 * scale)),
                      spacing=3, shadow=True)
        r.text(f"Sprawdzany pionek: {self.pawn_name}",
               r.fonts.get(int(13 * scale)), theme.text_light, surface,
               center=(panel.centerx, panel.centery - int(14 * scale)))
        r.text(f"Zużyjesz jedno z {self.uses_left} użyć Ice Block",
               r.fonts.get(int(12 * scale)), theme.text_dim, surface,
               center=(panel.centerx, panel.centery + int(10 * scale)))

        confirm, cancel = layout.movement_confirm_buttons()
        _button(r, surface, confirm, CONFIRM_CAPTION, mouse, accent=theme.frost)
        _button(r, surface, cancel, CANCEL_CAPTION, mouse)


class BreakupChoice:
    """Piotrek picking 2a or 2b for the group that falls furthest back.

    Only ever shown to Piotrek's seat, because the engine refuses the command
    from anybody else — the interface agrees with the rule rather than being
    the place it is enforced.
    """

    def __init__(self, state, seat: Callable[[], int]) -> None:
        self.state = state
        self.seat = seat
        self.appear = 0.0

    @property
    def pending(self):
        return getattr(self.state, "pending_breakup", None)

    @property
    def active(self) -> bool:
        pending = self.pending
        if pending is None or pending.choice_position is None:
            return False
        if pending.chosen_tile is not None:
            return False        # answered; the scatter is just waiting on time
        return self.seat() == pending.seat

    def tiles(self) -> List:
        pending = self.pending
        if pending is None or pending.choice_position is None:
            return []
        return list(self.state.board.tiles_at_position(pending.choice_position))

    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        self.appear = approach(self.appear, 1.0 if self.active else 0.0, 12.0, dt)

    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int],
                     layout: Layout) -> Optional[cmd.Command]:
        if not self.active:
            return None
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return None
        tiles = self.tiles()
        if len(tiles) < 2:
            return None
        for rect, tile in zip(layout.breakup_choice_buttons(), tiles):
            if rect.collidepoint(mouse):
                return cmd.ChooseBreakupTile(player_index=self.seat(),
                                             tile_index=tile.index)
        return None

    def consumes_click(self, mouse: Tuple[int, int], layout: Layout) -> bool:
        if not self.active:
            return False
        return bool(layout.breakup_choice_panel().collidepoint(mouse))

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        tiles = self.tiles()
        if len(tiles) < 2:
            return
        theme = r.theme
        scale = layout.ui_scale
        panel = layout.breakup_choice_panel()
        r.premium_panel(panel, surface, radius=12, border=theme.brass_light,
                        glow=theme.prompt, glow_strength=0.32, shadow=18)
        r.text("GDZIE SPADA OSTATNIA PARA?",
               r.fonts.get(int(12 * scale), bold=True), theme.text_heading,
               surface, center=(panel.centerx, panel.top + int(18 * scale)))

        for rect, tile in zip(layout.breakup_choice_buttons(), tiles):
            label = f"{tile.number}{tile.variant}" if tile.variant \
                else str(tile.number)
            _button(r, surface, rect, label, mouse, accent=theme.prompt)


def _button(r: Renderer, surface: pygame.Surface, rect: pygame.Rect,
            caption: str, mouse: Tuple[int, int], accent=None) -> None:
    theme = r.theme
    style = r.emphasis(fill=theme.btn_idle_bg, border=theme.btn_idle_border,
                       text=theme.btn_text,
                       hover=1.0 if rect.collidepoint(mouse) else 0.0,
                       enabled=True, accent=accent or theme.brass_light)
    drawn = r.interactive_panel(rect, style, surface, radius=8)
    r.fit_spaced_text(caption, drawn, style.text, surface, spacing=1,
                      padding=6, min_size=8, height_ratio=0.46)
