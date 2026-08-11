"""
Visual identity: colours, fonts, spacing.

Themes are plain data objects, so a second theme (a night board, a winter
board, a Steam-ready "premium" skin) is a matter of instantiating another
``Theme`` — no renderer code has to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

Color = Tuple[int, int, int]


def darken(color: Color, factor: float = 0.62) -> Color:
    return tuple(max(0, min(255, int(c * factor))) for c in color)  # type: ignore[return-value]


def lighten(color: Color, amount: float = 0.35) -> Color:
    return tuple(max(0, min(255, int(c + (255 - c) * amount))) for c in color)  # type: ignore[return-value]


def mix(a: Color, b: Color, t: float) -> Color:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


@dataclass(frozen=True)
class Theme:
    """All colours used by the UI and the board renderer.

    The palette is taken from the concept art: a near-black navy table, panels
    of dark slate edged in worn brass, parchment cards, and one warm gold used
    for anything the eye is meant to land on.  Nothing outside this file should
    contain a literal colour — the whole point of gathering them here is that
    the next skin is a second ``Theme``, not a search-and-replace.
    """

    name: str = "obsidian"

    # ── chrome ───────────────────────────────────────────────────────────────
    #: The table the game sits on: nearly black, faintly blue, so parchment and
    #: the board read as lit objects rather than bright rectangles.
    background: Color = (9, 13, 20)
    background_deep: Color = (4, 7, 12)
    background_glow: Color = (22, 30, 44)

    #: Panels are two layers: a body and a slightly lighter top edge, which is
    #: what stops them looking like flat cut-outs.
    panel_bg: Color = (16, 22, 33)
    panel_bg_light: Color = (24, 32, 45)
    panel_inset: Color = (11, 15, 23)
    panel_line: Color = (74, 62, 44)
    panel_edge: Color = (140, 112, 68)
    panel_highlight: Color = (46, 58, 76)
    panel_shadow: Color = (0, 0, 0)

    #: Worn brass, used for borders, rules and small ornaments.
    brass: Color = (150, 118, 68)
    brass_light: Color = (206, 172, 112)
    brass_bright: Color = (243, 214, 152)

    text_light: Color = (226, 218, 200)
    text_dim: Color = (136, 132, 120)
    text_heading: Color = (198, 168, 116)
    status: Color = (150, 146, 132)

    #: Reserved for the one thing that matters most on screen at any moment.
    accent: Color = (120, 214, 158)
    accent_dim: Color = (44, 86, 66)
    warning: Color = (214, 108, 96)

    # ── buttons ──────────────────────────────────────────────────────────────
    btn_idle_bg: Color = (26, 33, 46)
    btn_active_bg: Color = (42, 74, 58)
    btn_idle_border: Color = (108, 88, 56)
    btn_active_border: Color = (140, 220, 168)
    btn_text: Color = (208, 196, 170)
    btn_active_text: Color = (238, 250, 230)
    btn_primary_bg: Color = (58, 38, 68)
    btn_primary_border: Color = (186, 150, 92)
    btn_primary_text: Color = (244, 222, 172)
    btn_disabled_bg: Color = (20, 24, 32)
    btn_disabled_text: Color = (96, 94, 88)

    # ── interactions ─────────────────────────────────────────────────────────
    #: Something the engine is asking about, or a legal drop.
    prompt: Color = (243, 214, 152)
    prompt_bright: Color = (255, 236, 176)
    valid: Color = (140, 216, 152)
    invalid: Color = (216, 116, 104)
    #: Board overlays: the field a pawn will snap to, a link between pawns, the
    #: frost on a frozen one.
    snap_ring: Color = (250, 232, 168)
    link_line: Color = (168, 214, 236)
    frost: Color = (176, 224, 244)
    frost_dim: Color = (104, 156, 194)
    #: The tick and rules on notepad-style surfaces.
    ink: Color = (26, 28, 30)
    #: The round counter's two steppers, and the "this ends something" accent.
    counter_minus: Color = (112, 44, 44)
    counter_plus: Color = (48, 92, 60)
    warning_bg: Color = (86, 44, 44)

    # ── cards ────────────────────────────────────────────────────────────────
    card_bg: Color = (243, 228, 197)
    card_bg_shade: Color = (226, 206, 168)
    card_bg_highlight: Color = (252, 242, 214)
    card_border: Color = (96, 70, 40)
    card_border_hover: Color = (226, 186, 112)
    # ``card_frame`` lived here until stage 31.  It coloured the inset rule of
    # the old double frame, which was removed from the card face, the Signature
    # face and the reveal overlay at once; nothing draws it any more, so the
    # colour went with it rather than sitting here inviting somebody to put the
    # line back.  ``card_divider`` is the surviving hairline (title/body rule).
    card_shadow: Color = (0, 0, 0)
    card_title: Color = (54, 34, 18)
    card_text: Color = (78, 62, 46)
    card_divider: Color = (186, 154, 106)
    card_back_bg: Color = (28, 40, 62)
    card_back_deco: Color = (188, 154, 96)
    card_empty_bg: Color = (14, 19, 28)
    card_empty_line: Color = (52, 46, 36)

    # ── Signature Cards (full-card artwork) ──────────────────────────────────
    #: A card with artwork in ``assets/card_art`` replaces the parchment face
    #: with the picture, and the game draws the title and description over it.
    #: The picture is the variable here, so the type has to carry its own
    #: contrast: near-white on a near-black scrim, with an outline.
    #: The veil laid over the WHOLE picture on hover, so the eye moves from the
    #: illustration to the words.
    card_art_veil: Color = (6, 8, 12)
    #: The scrim that fades up from the bottom edge and holds the text.
    card_art_scrim: Color = (5, 6, 10)
    card_art_title: Color = (250, 246, 236)
    card_art_title_outline: Color = (12, 10, 8)
    card_art_text: Color = (234, 228, 214)
    card_art_divider: Color = (206, 172, 112)

    # ── mod panel ────────────────────────────────────────────────────────────
    mod_bg: Color = (16, 22, 33)
    mod_border: Color = (108, 88, 56)
    mod_title: Color = (198, 168, 116)
    mod_instr: Color = (136, 132, 120)
    mod_select_ring: Color = (243, 214, 152)
    mod_place_bg: Color = (26, 36, 30)

    # board terrain
    terrain_far: Color = (46, 74, 52)
    terrain_near: Color = (62, 94, 60)
    hill_light: Color = (76, 110, 68)
    hill_dark: Color = (44, 68, 48)
    road_fill: Color = (206, 176, 126)
    road_edge: Color = (128, 98, 60)
    road_shadow: Color = (30, 42, 30)
    tile_light: Color = (240, 217, 181)
    tile_dark: Color = (211, 178, 133)
    tile_border: Color = (86, 60, 32)
    tile_label: Color = (110, 82, 48)
    tile_hover: Color = (255, 244, 190)
    water: Color = (62, 108, 150)
    water_light: Color = (98, 156, 196)
    bridge: Color = (146, 106, 66)
    bridge_dark: Color = (104, 72, 44)
    rock: Color = (124, 126, 122)
    rock_dark: Color = (86, 88, 86)
    tree_trunk: Color = (78, 56, 36)
    tree_leaf: Color = (48, 104, 58)
    tree_leaf_light: Color = (74, 138, 72)
    village_wall: Color = (200, 178, 140)
    village_roof: Color = (152, 78, 60)
    grass_tuft: Color = (86, 126, 74)

    # board furniture
    camp_bg: Color = (46, 58, 70)
    camp_border: Color = (90, 110, 130)
    camp_label: Color = (170, 190, 210)
    finish_a: Color = (245, 245, 240)
    finish_b: Color = (40, 40, 44)

    # tokens
    token_ring: Color = (180, 180, 180)
    token_ring_drag: Color = (255, 230, 50)
    token_shadow: Color = (20, 20, 20)

    #: Accent colour per deck id — the concept's card backs: oxblood, olive,
    #: plum, brass and violet, all muted enough to sit under a brass frame.
    deck_colors: Dict[str, Color] = field(
        default_factory=lambda: {
            "movement": (128, 52, 44),
            "mods": (104, 118, 58),
            "chest": (118, 76, 110),
            "characters": (156, 122, 62),
            "piotrek_skills": (96, 74, 132),
        }
    )

    def deck_color(self, deck_id: str | None) -> Color | None:
        if deck_id is None:
            return None
        return self.deck_colors.get(deck_id)


THEME = Theme()


class FontBook:
    """Lazy, cached font loader.

    Prefers a bundled TTF from ``assets/fonts`` when one is present (so the
    game looks the same on every machine and can ship as a single .exe), and
    falls back to the system fonts the prototype used.
    """

    _PREFERRED_FILES = ("Inter.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf")
    _PREFERRED_BOLD = ("Inter-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf")
    #: The DECORATIVE face, used for Signature Card titles and nothing else.
    #: Empty by default — drop a legally usable serif/blackletter-ish TTF in
    #: ``assets/fonts`` under one of these names and every artwork card picks
    #: it up with no code change.  When none is present the request falls back
    #: to the bold UI face, so a clean checkout still renders a readable title.
    #: See ``assets/card_art/README.md``.
    _PREFERRED_DISPLAY = ("Display-Bold.ttf", "Display.ttf", "Title-Bold.ttf")
    _SYSTEM_NAMES = ("DejaVu Sans", "FreeSans", "Ubuntu", "Verdana", "Arial")

    def __init__(self, font_dir=None) -> None:
        from .settings import FONT_DIR

        self.font_dir = font_dir or FONT_DIR
        self._cache: Dict[Tuple[int, bool, bool], "object"] = {}
        #: Every requested size is multiplied by this before the font is built.
        #: Sizes are quoted for a 1080-tall window; a 1440 display gets bigger
        #: glyphs *rendered at that size*, which is what keeps text crisp rather
        #: than merely larger.  Set by the App on resize.
        self._scale = 1.0

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, scale: float) -> None:
        scale = max(0.85, min(1.8, float(scale)))
        if abs(scale - self._scale) < 0.005:
            return
        self._scale = scale
        # Cached fonts were built for the old scale; keeping them would mix two
        # sizes on screen.
        self._cache.clear()

    def get(self, size: int, bold: bool = False, display: bool = False):
        import pygame

        size = max(8, int(round(size * self._scale)))
        key = (size, bold, display)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        font = None
        if display:
            # A decorative face is an OVERRIDE, never a requirement: the game
            # ships without one, and asking for it when the file is absent must
            # give the bold UI face rather than a box-glyph fallback.
            for filename in self._PREFERRED_DISPLAY:
                path = self.font_dir / filename
                if path.exists():
                    font = pygame.font.Font(str(path), size)
                    break
            if font is None:
                font = self.get(size / max(0.01, self._scale), bold=True)
        if font is None:
            names = self._PREFERRED_BOLD if bold else self._PREFERRED_FILES
            for filename in names:
                path = self.font_dir / filename
                if path.exists():
                    font = pygame.font.Font(str(path), size)
                    break
        if font is None:
            for name in self._SYSTEM_NAMES:
                candidate = pygame.font.SysFont(name, size, bold=bold)
                if candidate:
                    font = candidate
                    break
        if font is None:
            font = pygame.font.Font(None, size)
        self._cache[key] = font
        return font

    # Named roles keep call sites readable and make a future accessibility
    # option ("larger text") a single multiplier instead of 30 edits.
    def title(self):
        return self.get(14, bold=True)

    def body(self):
        return self.get(12)

    def label(self):
        return self.get(13)

    def deck(self):
        return self.get(13, bold=True)

    def button(self):
        return self.get(13, bold=True)

    def status(self):
        return self.get(11)

    def name(self):
        return self.get(19, bold=True)

    def circle(self):
        return self.get(14, bold=True)

    def big_number(self):
        return self.get(28, bold=True)

    def info(self):
        return self.get(16, bold=True)

    def mod_title(self):
        return self.get(18, bold=True)

    def tile_name(self):
        return self.get(21, bold=True)

    def tile_count(self):
        return self.get(23, bold=True)
