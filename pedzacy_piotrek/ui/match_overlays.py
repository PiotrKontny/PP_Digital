"""
The two moments that bracket a match.

* :class:`MatchStartOverlay` — "Gra się rozpoczyna…" for everybody, and for the
  one player holding the Piotrek card, the pawn colours to hide behind.  The
  table is already drawn behind it: people can see the board they are about to
  play on while the one decision that has to come first is made.
* :class:`VictoryOverlay` — the ending.  Two of them, really: an escape and a
  capture, which are opposite results and are coloured, worded and animated as
  opposites so nobody has to read the sentence to know which happened.

Both are PRESENTATION ONLY.  Neither decides anything and neither touches a
command: the start overlay reports which colour was clicked and the victory
overlay reports which button was, and :class:`~pedzacy_piotrek.ui.game_screen.GameScreen`
does something about it.  What counts as a win lives in
:mod:`pedzacy_piotrek.engine.victory` and nowhere else — see CODE QUALITY in
the stage 17 brief, and VICTORY in LLM_Instructions.txt.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import pygame

from ..config.theme import darken, lighten
from ..engine.animation import approach, ease_out_cubic
from ..engine.victory import Outcome, Verdict
from ..render.renderer import Renderer
from .layout import Layout

Color = Tuple[int, int, int]


def _dim(surface: pygame.Surface, alpha: int) -> None:
    shade = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    shade.fill((4, 7, 12, max(0, min(255, int(alpha)))))
    surface.blit(shade, (0, 0))


class MatchStartOverlay:
    """Before the first move: everyone waits, one player chooses.

    Three states, and which one is showing is decided by what the *server* has
    said rather than by anything this class knows:

    ``waiting``   no question was sent to this machine — it is not Piotrek's;
    ``choosing``  the colours arrived, so this is Piotrek and he must pick;
    ``chosen``    he picked and the server has not yet started the match.
    """

    #: Diameter of a pawn button at 1080p, scaled with the window.
    CIRCLE = 84

    def __init__(self) -> None:
        self.active = False
        self.pawns: List[dict] = []
        self.chosen: str = ""
        self.appear = 0.0
        self.rects: List[pygame.Rect] = []
        self.hover: Optional[int] = None

    # ── state ────────────────────────────────────────────────────────────────
    def show(self, pawns: Sequence[dict] = (), chosen: str = "") -> None:
        self.active = True
        self.pawns = [dict(p) for p in pawns]
        self.chosen = chosen

    def hide(self) -> None:
        self.active = False
        self.appear = 0.0
        self.pawns = []
        self.hover = None

    @property
    def choosing(self) -> bool:
        return bool(self.pawns) and not self.chosen

    def pawn_at(self, position: Tuple[int, int]) -> Optional[str]:
        """Which colour is under the cursor, if this player may choose one."""
        if not self.choosing:
            return None
        for pawn, rect in zip(self.pawns, self.rects):
            if rect.collidepoint(position):
                return str(pawn.get("id", ""))
        return None

    # ── frame ────────────────────────────────────────────────────────────────
    def _lay_out(self, layout: Layout) -> pygame.Rect:
        scale = layout.ui_scale
        size = int(self.CIRCLE * scale)
        gap = int(22 * scale)
        count = len(self.pawns)
        row = count * size + max(0, count - 1) * gap
        width = max(int(560 * scale), row + int(96 * scale))
        width = min(width, layout.win_w - 60)
        # Taller when there are pawns to place: the hint above them was landing
        # on the top of the circles at 1080p.
        height = int((340 if count else 200) * scale)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = (layout.win_w // 2, layout.win_h // 2)

        left = panel.centerx - row // 2
        top = panel.bottom - int(96 * scale) - size // 2
        self.rects = [
            pygame.Rect(left + index * (size + gap), top - size // 2, size, size)
            for index in range(count)
        ]
        return panel

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 9.0, dt)
        self.hover = next(
            (i for i, rect in enumerate(self.rects) if rect.collidepoint(mouse)),
            None,
        ) if self.choosing else None

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        panel = self._lay_out(layout)
        fade = ease_out_cubic(min(1.0, self.appear))
        _dim(surface, 210 * fade)
        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.30, shadow=26)

        scale = layout.ui_scale
        if self.choosing:
            title, subtitle = "TWOJA TOŻSAMOŚĆ", "Wybierz pionek, którym uciekasz"
        elif self.chosen:
            title = "TOŻSAMOŚĆ WYBRANA"
            subtitle = "Czekaj na rozpoczęcie gry…"
        else:
            title, subtitle = "GRA SIĘ ROZPOCZYNA", "Przygotuj się…"

        r.spaced_text(title, r.fonts.get(int(28 * scale), bold=True),
                      theme.brass_bright, surface,
                      center=(panel.centerx, panel.top + int(52 * scale)),
                      spacing=4, shadow=True)
        r.text(subtitle, r.fonts.get(int(17 * scale)), theme.text_light, surface,
               center=(panel.centerx, panel.top + int(92 * scale)))

        if self.choosing:
            r.text("Nikt inny się o tym nie dowie",
                   r.fonts.get(int(14 * scale)), theme.text_dim, surface,
                   center=(panel.centerx, panel.top + int(118 * scale)))
            self._draw_pawns(r, surface, scale)
        else:
            self._draw_wait_dots(r, surface, panel, scale)

    def _draw_pawns(self, r: Renderer, surface: pygame.Surface,
                    scale: float) -> None:
        theme = r.theme
        for index, (pawn, rect) in enumerate(zip(self.pawns, self.rects)):
            colour = tuple(pawn.get("color", (200, 200, 200)))
            hovered = index == self.hover
            radius = rect.width // 2
            lift = int(6 * scale) if hovered else 0
            centre = (rect.centerx, rect.centery - lift)
            r.aa_circle((centre[0], centre[1] + int(5 * scale)), radius,
                        darken(theme.background_deep, 0.6), surface=surface)
            if hovered:
                r.ring_glow(centre, radius + int(6 * scale), theme.brass_bright,
                            strength=0.5, surface=surface)
            r.aa_circle(centre, radius, colour,
                        theme.brass_bright if hovered else darken(colour, 0.55),
                        3, surface)
            r.soft_ellipse((centre[0] - radius * 0.3, centre[1] - radius * 0.35),
                           radius * 0.45, radius * 0.3, lighten(colour, 0.75),
                           alpha=150, surface=surface)
            r.text(str(pawn.get("name", "")), r.fonts.get(int(13 * scale)),
                   theme.brass_light if hovered else theme.text_dim, surface,
                   midtop=(rect.centerx, rect.bottom + int(8 * scale)))

    def _draw_wait_dots(self, r: Renderer, surface: pygame.Surface,
                        panel: pygame.Rect, scale: float) -> None:
        """Three dots breathing in turn: something is happening, nothing is stuck."""
        theme = r.theme
        radius = int(6 * scale)
        gap = int(26 * scale)
        centre_y = panel.bottom - int(70 * scale)
        for index in range(3):
            phase = self.appear * 3.0 - index * 0.35
            level = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(phase * 2.2))
            r.aa_circle((panel.centerx + (index - 1) * gap, centre_y), radius,
                        r.mix(theme.panel_bg_light, theme.brass_bright, level),
                        surface=surface)


class VictoryOverlay:
    """The ending, and the only two buttons left.

    Not a message box, and not one screen with the word swapped: an escape is
    green and rises, a capture is red and closes in, and the pawn that was
    hiding is the largest thing on it either way — the reveal is the payoff of
    the whole match.
    """

    RETURN = "lobby"
    MENU = "menu"
    QUIT = "quit"

    def __init__(self) -> None:
        self.active = False
        self.verdict: Optional[Verdict] = None
        self.pawn_color: Color = (200, 200, 200)
        self.pawn_name: str = ""
        self.can_return = True
        self.appear = 0.0
        self.buttons: List[Tuple[str, pygame.Rect]] = []
        self.hover: Optional[str] = None

    # ── state ────────────────────────────────────────────────────────────────
    def show(self, verdict: Verdict, pawn_color: Color, pawn_name: str,
             can_return: bool = True) -> None:
        self.active = True
        self.verdict = verdict
        self.pawn_color = tuple(pawn_color)
        self.pawn_name = pawn_name
        self.can_return = can_return
        self.appear = 0.0

    def hide(self) -> None:
        self.active = False
        self.appear = 0.0

    @property
    def piotrek_won(self) -> bool:
        return self.verdict is not None and self.verdict.piotrek_won

    @property
    def accent(self) -> Color:
        """The colour of the whole ending.  Two results, two palettes."""
        return (86, 196, 122) if self.piotrek_won else (214, 96, 88)

    def button_at(self, position: Tuple[int, int]) -> Optional[str]:
        for key, rect in self.buttons:
            if rect.collidepoint(position):
                return key
        return None

    # ── frame ────────────────────────────────────────────────────────────────
    def _entries(self) -> List[Tuple[str, str]]:
        """Three ways out, in order of how much of the evening they keep.

        "Wróć do poczekalni" holds the room together and is the one people
        will press; "Menu główne" leaves it; "Wyjdź" closes the game.  A
        hot-seat match has no room to hold together, so it does not offer the
        first.
        """
        entries = []
        if self.can_return:
            entries.append((self.RETURN, "Wróć do poczekalni"))
        entries.append((self.MENU, "Menu główne"))
        entries.append((self.QUIT, "Wyjdź z gry"))
        return entries

    def _lay_out(self, layout: Layout) -> pygame.Rect:
        scale = layout.ui_scale
        width = min(int(720 * scale), layout.win_w - 60)
        height = min(int(560 * scale), layout.win_h - 60)
        panel = pygame.Rect(0, 0, width, height)
        panel.center = (layout.win_w // 2, layout.win_h // 2)

        # Stacked rather than in a row: three captions of this length side by
        # side shrink until they are unreadable at 1280×760, and a vertical
        # list is what every other menu in the game looks like anyway.
        entries = self._entries()
        button_w = min(int(320 * scale), panel.width - int(80 * scale))
        button_h = int(46 * scale)
        gap = int(10 * scale)
        block = len(entries) * button_h + (len(entries) - 1) * gap
        top = panel.bottom - block - int(28 * scale)
        left = panel.centerx - button_w // 2
        self.buttons = [
            (key, pygame.Rect(left, top + index * (button_h + gap),
                              button_w, button_h))
            for index, (key, _) in enumerate(entries)
        ]
        self._labels = {key: label for key, label in entries}
        return panel

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 4.5, dt)
        self.hover = self.button_at(mouse)

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active or self.verdict is None:
            return
        theme, scale = r.theme, layout.ui_scale
        panel = self._lay_out(layout)
        fade = ease_out_cubic(min(1.0, self.appear))
        accent = self.accent
        _dim(surface, 225 * fade)

        # The panel arrives from below when Piotrek got away, and closes in from
        # above when the hunters caught him.  Same fade, opposite direction.
        travel = int((1.0 - fade) * 70 * scale)
        panel = panel.move(0, travel if self.piotrek_won else -travel)
        r.premium_panel(panel, surface, radius=18, border=accent,
                        glow=accent, glow_strength=0.45 * fade, shadow=30)

        title = "PIOTREK UCIEKŁ!" if self.piotrek_won else "ŁOWCY ZNALEŹLI PIOTRKA!"
        r.fit_spaced_text(title, pygame.Rect(panel.left + int(30 * scale),
                                             panel.top + int(30 * scale),
                                             panel.width - int(60 * scale),
                                             int(52 * scale)),
                          lighten(accent, 0.45), surface,
                          base_size=int(34 * scale), spacing=3, padding=0)
        subtitle = ("Dobiegł do mety i nikt go nie rozpoznał"
                    if self.piotrek_won
                    else "Wieża stanęła na jego pionku")
        r.text(subtitle, r.fonts.get(int(16 * scale)), theme.text_dim, surface,
               center=(panel.centerx, panel.top + int(96 * scale)))

        self._draw_reveal(r, layout, surface, panel, accent, fade)

        for key, rect in self.buttons:
            hovered = self.hover == key
            # One primary action in the ending's own colour, one plain way out.
            # Both in red for a capture made the pair hard to tell apart.
            fill = accent if key == self.RETURN else theme.panel_bg_light
            style = r.emphasis(
                fill=fill,
                border=lighten(fill, 0.45) if key == self.RETURN else theme.brass,
                text=theme.btn_active_text if key == self.RETURN else theme.text_light,
                hover=1.0 if hovered else 0.0,
                accent=theme.brass_light,
            )
            drawn = r.interactive_panel(rect, style, surface, radius=10)
            r.fit_spaced_text(self._labels[key].upper(), drawn, style.text,
                              surface, base_size=int(16 * scale), spacing=2,
                              padding=14)

    def _draw_reveal(self, r: Renderer, layout: Layout, surface: pygame.Surface,
                     panel: pygame.Rect, accent: Color, fade: float) -> None:
        """Who Piotrek was, and which pawn he was hiding behind.

        The pawn grows into place: it is the one fact the hunters spent the
        whole match trying to buy, so it is not going to slide past in a line
        of text.
        """
        theme, scale = r.theme, layout.ui_scale
        verdict = self.verdict
        assert verdict is not None
        centre = (panel.centerx, panel.top + int(190 * scale))
        radius = int(52 * scale * (0.6 + 0.4 * fade))

        r.aa_circle((centre[0], centre[1] + int(6 * scale)), radius,
                    darken(theme.background_deep, 0.6), surface=surface)
        r.ring_glow(centre, radius + int(10 * scale), accent,
                    strength=0.55 * fade, surface=surface)
        r.aa_circle(centre, radius, self.pawn_color, lighten(accent, 0.3), 3,
                    surface)
        r.soft_ellipse((centre[0] - radius * 0.3, centre[1] - radius * 0.35),
                       radius * 0.45, radius * 0.3,
                       lighten(self.pawn_color, 0.75), alpha=150,
                       surface=surface)

        name = verdict.piotrek_name or "Piotrek"
        r.text(f"Piotrkiem był: {name}", r.fonts.get(int(21 * scale), bold=True),
               theme.text_light, surface,
               midtop=(panel.centerx, centre[1] + radius + int(18 * scale)))
        r.text(f"Ukryty pionek: {self.pawn_name or verdict.pawn_id}",
               r.fonts.get(int(17 * scale)), theme.brass_light, surface,
               midtop=(panel.centerx, centre[1] + radius + int(46 * scale)))


# ── a check that came back negative ──────────────────────────────────────────
class EliminationNotice:
    """"<Kolor> TO NIE PIOTREK", as a card beside the board.

    The result of a check used to be one line in the status bar, in the corner
    of the screen nobody is looking at while a tower is being lifted — so the
    single most important thing the hunters learn all game was the easiest
    thing on screen to miss.

    PRESENTATION ONLY, and deliberately not modal: it fades in, holds, and
    fades out on its own, and it swallows no input at any point.  The notepad
    is still the permanent record; this is the announcement.  Driven from the
    ``PawnEliminated`` event rather than from the state, because unlike the
    match overlays (N74) it is a MOMENT rather than a condition — a
    reconnecting client replaying twenty commands should not be shown four
    announcements it missed, and it is not, because each one replaces the last.
    """

    FADE_IN = 0.28
    HOLD = 3.4
    FADE_OUT = 0.9

    def __init__(self) -> None:
        self.pawn_name = ""
        self.color: Tuple[int, int, int] = (200, 60, 60)
        self.elapsed = 0.0
        self.active = False

    @property
    def lifetime(self) -> float:
        return self.FADE_IN + self.HOLD + self.FADE_OUT

    def show(self, pawn_name: str, color: Tuple[int, int, int]) -> None:
        self.pawn_name = pawn_name
        self.color = color
        self.elapsed = 0.0
        self.active = True

    def hide(self) -> None:
        self.active = False

    @property
    def alpha(self) -> float:
        """0..1 opacity: eased in, held, then faded out."""
        if not self.active:
            return 0.0
        if self.elapsed < self.FADE_IN:
            return ease_out_cubic(self.elapsed / self.FADE_IN)
        if self.elapsed < self.FADE_IN + self.HOLD:
            return 1.0
        left = self.lifetime - self.elapsed
        return max(0.0, left / self.FADE_OUT)

    def update(self, dt: float) -> None:
        if not self.active:
            return
        self.elapsed += dt
        if self.elapsed >= self.lifetime:
            self.active = False

    def draw(self, r: Renderer, layout, surface: pygame.Surface) -> None:
        if not self.active:
            return
        alpha = self.alpha
        if alpha <= 0.01:
            return
        theme = r.theme
        rect = layout.elimination_card_rect()
        scale = layout.ui_scale

        # Drawn on its own surface so ONE alpha fades the whole card — fading
        # each piece separately is how the border ends up outliving the text.
        card = pygame.Surface(rect.size, pygame.SRCALPHA)
        local = pygame.Rect(0, 0, rect.width, rect.height)
        r.premium_panel(local, card, radius=int(12 * scale),
                        border=theme.invalid, glow=theme.invalid,
                        glow_strength=0.5, shadow=int(18 * scale))

        cross_top = int(rect.height * 0.16)
        cross_size = int(rect.width * 0.34)
        centre = (local.centerx, cross_top + cross_size // 2)
        r.heavy_cross(centre, cross_size, theme.invalid,
                     darken(theme.invalid, 0.45), card, scale=scale)

        dot_y = cross_top + cross_size + int(26 * scale)
        radius = max(8, int(15 * scale))
        r.aa_circle((local.centerx, dot_y), radius, self.color,
                    darken(self.color, 0.55), 2, card)

        name_rect = pygame.Rect(int(10 * scale), dot_y + radius + int(8 * scale),
                                local.width - int(20 * scale), int(34 * scale))
        r.fit_text(self.pawn_name.upper(), name_rect, theme.text_light, card,
                   base_size=int(24 * scale), bold=True)
        line_rect = pygame.Rect(int(10 * scale), name_rect.bottom + int(4 * scale),
                                local.width - int(20 * scale), int(30 * scale))
        r.fit_text("TO NIE PIOTREK", line_rect, theme.invalid, card,
                   base_size=int(19 * scale), bold=True)
        note_rect = pygame.Rect(int(10 * scale), line_rect.bottom + int(2 * scale),
                                local.width - int(20 * scale), int(22 * scale))
        r.fit_text("kolor wykreślony", note_rect, theme.text_dim, card,
                   base_size=int(13 * scale), bold=False)

        card.set_alpha(int(255 * alpha))
        surface.blit(card, rect.topleft)
