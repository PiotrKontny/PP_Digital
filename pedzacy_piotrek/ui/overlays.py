"""
Overlays.

Three things now interrupt the ordinary screen, and all three are driven by
engine events rather than by the click that caused them — which is what will
let a remote player's decision appear on everybody's table later.

* :class:`ChoicePrompt` — the engine needs an answer (which pawn? which half of
  a doubled field? one space or two?).  One panel handles every question,
  because the engine describes what it is asking rather than the interface
  knowing about particular cards.
* :class:`RevealOverlay` — a card announcing itself in the middle of the
  screen: Gamechanger turning into Alter Ego or Kingmaker, and the random card
  Seks z pedałami turns up.
* :class:`ChestChoice` — the chest hand limit, laying the candidates out side
  by side so the player can pick what to keep.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pygame

from ..cards.base_card import Card
from ..config.theme import darken, lighten
from ..engine.animation import approach, ease_out_cubic
from ..render.card_renderer import CardRenderer
from ..render.renderer import Renderer
from .layout import Layout

Color = Tuple[int, int, int]


def _dim(surface: pygame.Surface, rect: pygame.Rect, alpha: int = 150) -> None:
    """Darken everything behind an overlay so the eye goes to the front."""
    shade = pygame.Surface(rect.size, pygame.SRCALPHA)
    shade.fill((6, 10, 8, alpha))
    surface.blit(shade, rect.topleft)


# ── the engine is asking something ───────────────────────────────────────────
@dataclass
class ChoiceOptionView:
    id: str
    label: str
    rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    color: Optional[Color] = None
    hover: float = 0.0


class ChoicePrompt:
    """A question from the engine, answered by clicking.

    Pawn questions also accept a click on the pawn itself out on the board, and
    field questions expect one — the buttons are the reliable path, the board is
    the natural one.
    """

    def __init__(self) -> None:
        self.active = False
        self.prompt = ""
        self.description = ""
        self.kind = "option"
        self.options: List[ChoiceOptionView] = []
        self.appear = 0.0

    def show(self, prompt: str, kind: str, options: Sequence[Tuple[str, str]],
             description: str = "", colors: Optional[Dict[str, Color]] = None) -> None:
        self.active = True
        self.prompt = prompt
        self.kind = kind
        self.description = description
        self.appear = 0.0
        self.options = [
            ChoiceOptionView(id=oid, label=label, color=(colors or {}).get(oid))
            for oid, label in options
        ]

    def hide(self) -> None:
        self.active = False
        self.options = []

    @property
    def is_pawn_choice(self) -> bool:
        return self.kind == "pawn"

    # ── geometry ─────────────────────────────────────────────────────────────
    def _lay_out(self, layout: Layout) -> pygame.Rect:
        panel = layout.choice_prompt
        if not self.options:
            return panel
        gap = 14 if self.is_pawn_choice else 10
        count = len(self.options)

        if self.is_pawn_choice:
            # Pawn choices are drawn as the pawns themselves, so the hit area is
            # a square around the token plus room for its name underneath.
            size = min(74, max(40, (panel.width - (count + 1) * gap) // count))
            size = min(size, panel.height - 62)
            width = height = size
        else:
            width = min(190, max(90, (panel.width - (count + 1) * gap) // count))
            height = min(52, panel.height - 56)

        total = count * width + (count - 1) * gap
        x = panel.centerx - total // 2
        y = panel.bottom - height - (26 if self.is_pawn_choice else 14)
        for option in self.options:
            option.rect = pygame.Rect(x, y, width, height)
            x += width + gap
        return panel

    def option_at(self, position: Tuple[int, int]) -> Optional[str]:
        for option in self.options:
            if option.rect.collidepoint(position):
                return option.id
        return None

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 14.0, dt)
        for option in self.options:
            wanted = 1.0 if option.rect.collidepoint(mouse) else 0.0
            option.hover = approach(option.hover, wanted, 16.0, dt)

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        panel = self._lay_out(layout)
        eased = ease_out_cubic(min(1.0, self.appear))
        slide = int((1.0 - eased) * 26)
        panel = panel.move(0, slide)

        r.premium_panel(panel, surface, radius=14, border=r.theme.brass_light,
                        glow=r.theme.brass, glow_strength=0.35, shadow=18)
        r.spaced_text(self.prompt.upper(), r.fonts.get(19, bold=True), theme.prompt, surface,
              center=(panel.centerx, panel.top + 22), spacing=2, shadow=True)
        if self.description:
            r.text(self.description, r.fonts.deck(), theme.text_dim, surface,
                   midtop=(panel.centerx, panel.top + 36))

        for option in self.options:
            rect = option.rect.move(0, slide - int(option.hover * 4))
            if self.is_pawn_choice:
                self._draw_pawn_option(r, option, rect, surface)
            else:
                self._draw_button_option(r, option, rect, surface)

        r.text("Esc anuluje", r.fonts.label(), theme.text_dim, surface,
               midbottom=(panel.centerx, panel.bottom - 2))


    def _draw_pawn_option(self, r: Renderer, option: ChoiceOptionView,
                          rect: pygame.Rect, surface: pygame.Surface) -> None:
        """A pawn choice is drawn as the pawn, not as a button with its name.

        The player is picking a token on the board; showing them a row of
        labelled rectangles would make them read where they should just look.
        """
        colour = option.color or r.theme.brass
        radius = rect.width // 2 - 2
        centre = (rect.centerx, rect.centery)

        # A lit rim around the token, not a lamp behind it: the pawn is round,
        # so its highlight is a ring that reaches a fixed fraction past its own
        # edge rather than a disc two and a half times its size.
        r.ring_glow(centre, radius, lighten(colour, 0.3), surface,
                    strength=0.45 + 0.55 * option.hover)
        r.drop_shadow(pygame.Rect(0, 0, radius * 2, radius * 2).move(
            centre[0] - radius, centre[1] - radius + 4),
            radius=radius, spread=6, alpha=120, offset=(0, 3), surface=surface)

        r.aa_circle(centre, radius, colour, darken(colour, 0.45),
                    3 if option.hover > 0.4 else 2, surface)
        # A highlight blob, so the token reads as a physical piece.
        r.soft_ellipse((centre[0] - radius * 0.28, centre[1] - radius * 0.34),
                       radius * 0.42, radius * 0.3, lighten(colour, 0.75),
                       alpha=150, surface=surface)
        if option.hover > 0.05:
            r.aa_ring(centre, radius + 3 + int(option.hover * 3),
                      r.theme.prompt_bright, 2, surface)

        r.text(option.label, r.fonts.label(),
               r.theme.prompt_bright if option.hover > 0.4 else r.theme.text_dim, surface,
               midtop=(rect.centerx, rect.bottom + 3), shadow=True)

    def _draw_button_option(self, r: Renderer, option: ChoiceOptionView,
                            rect: pygame.Rect, surface: pygame.Surface) -> None:
        """An option in a decision dialog — a button like any other button.

        These used to grow a halo of their own; a dialog that asks a question
        should look like the rest of the interface, not announce itself.
        """
        base = option.color or r.theme.btn_idle_bg
        text = r.theme.text_light if sum(base) < 420 else r.theme.ink
        style = r.emphasis(fill=base, border=darken(base, 0.7), text=text,
                           hover=option.hover, accent=r.theme.prompt)
        drawn = r.interactive_panel(rect, style, surface, radius=9)
        r.fit_text(option.label, drawn, style.text, surface, base_size=17,
                   padding=10)


# ── a card announcing itself ─────────────────────────────────────────────────
@dataclass
class RevealPhase:
    title: str
    text: str
    seconds: float
    color: Optional[Color] = None
    subtitle: str = ""


class RevealOverlay:
    """A big card in the middle of the screen, in one or two phases.

    Gamechanger uses two: it shows itself, then turns into Alter Ego or
    Kingmaker with a flip.  A random reveal uses two as well: the card that
    caused it, then the card it found.
    """

    FLIP_SECONDS = 0.45

    def __init__(self) -> None:
        self.phases: List[RevealPhase] = []
        self.index = 0
        self.elapsed = 0.0
        self.flip = 0.0
        self.card: Optional[Card] = None

    @property
    def active(self) -> bool:
        return self.index < len(self.phases)

    def show(self, phases: Sequence[RevealPhase], card: Optional[Card] = None) -> None:
        self.phases = list(phases)
        self.index = 0
        self.elapsed = 0.0
        self.flip = 0.0
        self.card = card

    def card_rect(self, layout: Layout) -> pygame.Rect:
        size = layout.reveal_card_size
        rect = pygame.Rect(0, 0, size[0], size[1])
        rect.center = layout.reveal_centre
        return rect

    def hit(self, layout: Layout, position: Tuple[int, int]) -> bool:
        return self.active and self.card_rect(layout).collidepoint(position)

    def dismiss(self) -> None:
        self.phases = []
        self.index = 0
        self.card = None

    def update(self, dt: float) -> None:
        if not self.active:
            return
        self.elapsed += dt
        phase = self.phases[self.index]
        if self.elapsed >= phase.seconds:
            self.elapsed = 0.0
            self.index += 1
            self.flip = 1.0 if self.index < len(self.phases) else 0.0
        if self.flip > 0:
            self.flip = max(0.0, self.flip - dt / self.FLIP_SECONDS)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface) -> None:
        if not self.active:
            return
        theme = r.theme
        phase = self.phases[self.index]
        size = layout.reveal_card_size
        centre = layout.reveal_centre
        _dim(surface, layout.board_viewport, 130)

        # The flip: squeeze the card horizontally as one face becomes the other.
        squeeze = abs(math.cos(math.pi * (1.0 - self.flip))) if self.flip > 0 else 1.0
        squeeze = max(0.06, squeeze)
        entrance = min(1.0, self.elapsed / 0.28)
        scale = 0.86 + 0.14 * ease_out_cubic(entrance)

        card_w = max(8, int(size[0] * squeeze * scale))
        card_h = int(size[1] * scale)
        rect = pygame.Rect(0, 0, card_w, card_h)
        rect.center = centre

        colour = phase.color or theme.brass_light
        r.drop_shadow(rect, radius=16, spread=26, alpha=170, offset=(0, 10),
                      surface=surface)
        r.rounded_gradient(surface, rect, theme.card_bg, theme.card_bg_shade, 16)
        pygame.draw.rect(surface, colour, rect, 4, border_radius=16)
        pygame.draw.rect(surface, theme.card_frame, rect.inflate(-14, -14), 1,
                         border_radius=12)

        if squeeze > 0.45:
            pad = int(card_w * 0.09)
            title_font = r.fonts.get(max(16, int(card_h * 0.075)), bold=True)
            for line in r.wrap_lines(phase.title, title_font, card_w - 2 * pad)[:2]:
                r.text(line, title_font, darken(colour, 0.6), surface,
                       midtop=(rect.centerx, rect.top + int(card_h * 0.08)))
                break
            body_font = r.fonts.get(max(12, int(card_h * 0.048)))
            r.draw_wrapped(
                phase.text, body_font, theme.card_text,
                pygame.Rect(rect.left + pad, rect.top + int(card_h * 0.26),
                            card_w - 2 * pad, card_h - int(card_h * 0.34)),
                surface,
            )
            if phase.subtitle:
                r.text(phase.subtitle, r.fonts.deck(), theme.prompt_bright, surface,
                       midtop=(rect.centerx, rect.bottom + 12), shadow=True)


# ── the chest hand limit ─────────────────────────────────────────────────────
class ChestChoice:
    """Pick which chest cards to keep when a draw goes over the limit."""

    def __init__(self) -> None:
        self.active = False
        self.cards: List[Card] = []
        self.new_uid: Optional[int] = None
        self.limit = 1
        self.keep: List[int] = []
        self.appear = 0.0
        self.hover: Dict[int, float] = {}

    def show(self, cards: Sequence[Card], limit: int, new_uid: Optional[int]) -> None:
        self.active = True
        self.cards = list(cards)
        self.limit = limit
        self.new_uid = new_uid
        # Start with the newest card selected: it is the one the player just
        # chose to draw, so it is the likeliest thing they want.
        self.keep = [new_uid] if new_uid is not None and limit > 0 else []
        self.appear = 0.0
        self.hover = {}

    def hide(self) -> None:
        self.active = False
        self.cards = []
        self.keep = []

    @property
    def ready(self) -> bool:
        return len(self.keep) == self.limit

    def toggle(self, uid: int) -> None:
        if uid in self.keep:
            self.keep.remove(uid)
        elif len(self.keep) < self.limit:
            self.keep.append(uid)
        else:
            # Replacing the oldest selection is friendlier than refusing the
            # click and making the player deselect something first.
            self.keep.pop(0)
            self.keep.append(uid)

    def card_at(self, layout: Layout, position: Tuple[int, int]) -> Optional[int]:
        for index, card in enumerate(self.cards):
            if layout.chest_choice_card_rect(index, len(self.cards)).collidepoint(position):
                return card.uid
        return None

    def confirm_hit(self, layout: Layout, position: Tuple[int, int]) -> bool:
        return (
            self.ready
            and layout.chest_confirm_rect(len(self.cards)).collidepoint(position)
        )

    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 12.0, dt)
        for index, card in enumerate(self.cards):
            rect = layout.chest_choice_card_rect(index, len(self.cards))
            wanted = 1.0 if rect.collidepoint(mouse) else 0.0
            self.hover[card.uid] = approach(self.hover.get(card.uid, 0.0), wanted,
                                            16.0, dt)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        count = len(self.cards)
        panel = layout.chest_choice_panel(count)
        _dim(surface, surface.get_rect(), int(150 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.4, shadow=22)

        r.spaced_text("LIMIT KART SKRZYNI", r.fonts.get(22, bold=True),
                      theme.brass_bright, surface,
                      center=(panel.centerx, panel.top + 24), spacing=3, shadow=True)
        r.text(
            f"Zatrzymaj {self.limit} — resztę odrzucisz "
            f"(wybrano {len(self.keep)}/{self.limit})",
            r.fonts.deck(), theme.text_dim, surface,
            midtop=(panel.centerx, panel.top + 38),
        )

        size = layout.chest_choice_card_size(count)
        for index, card in enumerate(self.cards):
            rect = layout.chest_choice_card_rect(index, count)
            hover = self.hover.get(card.uid, 0.0)
            chosen = card.uid in self.keep
            lift = int(10 * hover + (10 if chosen else 0))
            draw_rect = rect.move(0, -lift)
            border = theme.valid if chosen else theme.deck_colors.get(card.deck_id)
            if chosen or hover > 0.02:
                # Was a circle drawn around a rectangular card, which reached a
                # long way past both of its corners.  Now the card's own outline
                # lights up.
                r.shape_glow(draw_rect, theme.valid if chosen else theme.prompt,
                             surface, radius=10,
                             strength=0.7 if chosen else 0.35 * hover)
            cards.draw_in(card, draw_rect, surface, highlighted=chosen or hover > 0.5,
                          border_color=border)
            if chosen:
                pygame.draw.rect(surface, theme.valid, draw_rect.inflate(6, 6), 3,
                                 border_radius=10)
                r.text("ZATRZYMUJESZ", r.fonts.label(), theme.valid, surface,
                       midtop=(draw_rect.centerx, draw_rect.bottom + 8), shadow=True)
            elif card.uid == self.new_uid:
                r.text("nowa karta", r.fonts.label(), theme.prompt, surface,
                       midtop=(draw_rect.centerx, draw_rect.bottom + 4), shadow=True)

        confirm = layout.chest_confirm_rect(count)
        enabled = self.ready
        hovered = enabled and confirm.collidepoint(mouse)
        style = r.emphasis(fill=theme.btn_primary_bg,
                           border=theme.btn_primary_border,
                           text=theme.btn_primary_text,
                           hover=1.0 if hovered else 0.0, enabled=enabled,
                           accent=theme.brass_light)
        drawn = r.interactive_panel(confirm, style, surface, radius=10)
        r.fit_spaced_text("ZATWIERDŹ" if enabled else f"WYBIERZ {self.limit}",
                          drawn, style.text, surface, base_size=17, spacing=2,
                          padding=14)


# ── pause / leave ────────────────────────────────────────────────────────────
class PauseMenu:
    """Esc menu: leave the match or quit the game.

    Deliberately short.  There is no "return to lobby" because a match cannot be
    un-started: the host would have to tear the session down and rebuild the
    lobby, and pretending otherwise would leave clients staring at a table that
    no longer exists.  Leaving sends everyone back to the main menu, which is
    the honest version of the same thing.
    """

    def __init__(self) -> None:
        self.active = False
        self.entries: List[Tuple[str, str]] = []
        self.rects: List[pygame.Rect] = []
        self.appear = 0.0
        self.hover: Optional[int] = None

    def open(self, entries: Sequence[Tuple[str, str]]) -> None:
        self.active = True
        self.entries = list(entries)
        self.appear = 0.0

    def close(self) -> None:
        self.active = False

    def _lay_out(self, layout: Layout, r: Optional[Renderer] = None) -> pygame.Rect:
        """Measured from the captions, like every other button block."""
        width, height, gap = 320, 52, 12
        if r is not None:
            from .widgets import BUTTON_PAD_X, BUTTON_SPACING, BUTTON_TEXT_SIZE

            font = r.fonts.get(BUTTON_TEXT_SIZE, bold=True)
            widest = max(
                (r.spaced_width(label.upper(), font, BUTTON_SPACING)
                 for _, label in self.entries), default=0)
            width = max(width, int(widest + 2 * BUTTON_PAD_X))
            height = max(height, font.get_height() + 30)
            width = min(width, max(200, layout.win_w - 80))
        total = len(self.entries) * height + (len(self.entries) - 1) * gap
        panel = pygame.Rect(0, 0, width + 60, total + 110)
        panel.center = (layout.win_w // 2, layout.win_h // 2)
        self.rects = [
            pygame.Rect(panel.centerx - width // 2,
                        panel.top + 76 + index * (height + gap), width, height)
            for index in range(len(self.entries))
        ]
        return panel

    def entry_at(self, position: Tuple[int, int]) -> Optional[str]:
        for (key, _), rect in zip(self.entries, self.rects):
            if rect.collidepoint(position):
                return key
        return None

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 14.0, dt)
        self.hover = next(
            (i for i, rect in enumerate(self.rects) if rect.collidepoint(mouse)), None
        )

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface) -> None:
        if not self.active:
            return
        panel = self._lay_out(layout, r)
        _dim(surface, surface.get_rect(), int(170 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=14, border=r.theme.brass_light,
                        glow=r.theme.brass, glow_strength=0.3, shadow=22)
        r.spaced_text("MENU", r.fonts.get(26, bold=True), r.theme.brass_bright,
                      surface, center=(panel.centerx, panel.top + 34), spacing=4,
                      shadow=True)

        for index, ((key, label), rect) in enumerate(zip(self.entries, self.rects)):
            hovered = index == self.hover
            accent = (r.theme.warning_bg if key in ("leave", "quit")
                      else r.theme.btn_active_bg)
            style = r.emphasis(fill=accent, border=lighten(accent, 0.45),
                               text=r.theme.btn_active_text,
                               hover=1.0 if hovered else 0.0,
                               accent=r.theme.brass_light)
            drawn = r.interactive_panel(rect, style, surface, radius=10)
            r.fit_spaced_text(label.upper(), drawn, style.text, surface,
                              base_size=17, spacing=2, padding=16)
