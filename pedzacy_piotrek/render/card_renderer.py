"""
Card rendering.

A card is data (``cards/base_card.py``); this paints it.  The visual result
matches the prototype — wrapped title, divider, body text, badge strip — but
this version paints a card *onto its own surface* and caches it by (definition,
size, state).

That change is what makes the rest of this stage possible:

* the hand fan needs every card rotated by a few degrees, and rotating a cached
  surface is one ``rotozoom`` instead of re-laying out text every frame;
* panels need small cards and the hand needs large ones, so size became a
  parameter instead of a constant — and because the face is *painted* at its
  final size rather than scaled up from 140×200, small cards stay crisp;
* a dragged card is the same surface, drawn wherever the cursor is.

The cache is keyed by content, so the 5 identical copies of "Zerówka" in a hand
share one surface.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import pygame

from ..cards.base_card import Badge, Card
from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.theme import darken, lighten, mix
from .card_art import CardArtLibrary
from .card_back import CardBackLibrary
from .renderer import Renderer

Color = Tuple[int, int, int]
Size = Tuple[int, int]

#: Reference size.  Everything else is expressed as a fraction of it, so a card
#: drawn at any size keeps the same proportions.
CARD_W = 140
CARD_H = 200
NATIVE_SIZE: Size = (CARD_W, CARD_H)
#: Proportions every card keeps, whatever size it is painted at.
CARD_ASPECT = CARD_H / CARD_W

#: ``FontBook`` scale at the reference display (2560x1440).  Card type is
#: quoted against THIS rather than against the live scale, so the fraction of a
#: card its title and description occupy is the same on every monitor.  Keeping
#: it equal to ``ui.layout.TYPE_ANCHOR`` is what makes the reference display
#: render byte-for-byte the type it rendered before stage 29.
CARD_TYPE_ANCHOR = 1.44

#: Title height as a fraction of the card, and how far it may shrink to keep a
#: long word whole.  Named because ``_title_font`` and ``face`` must agree.
TITLE_FRACTION = 0.068
TITLE_MIN_STEP = 0.75

# ── Signature Cards ──────────────────────────────────────────────────────────
#: Every number below is a fraction of the CARD, never of the interface scale,
#: for the reason ``_font`` explains at length: a card of a given pixel size
#: has to render identically on every monitor.
#:
#: The title is larger than a standard card's — it is the only text on the
#: face when nothing is hovered, and it is competing with a photograph.
SIGNATURE_TITLE_FRACTION = 0.086
SIGNATURE_TEXT_FRACTION = 0.052
#: How small the description may get before it stops being worth showing.
SIGNATURE_TEXT_MIN = 0.60
#: Share of the card the description may occupy once revealed.
SIGNATURE_TEXT_ROOM = 0.46
#: The scrim that fades up from the bottom edge: how far up it reaches and how
#: opaque it gets at the very bottom, resting -> revealed.
SIGNATURE_SCRIM_RISE = (0.30, 0.72)
SIGNATURE_SCRIM_ALPHA = (188, 240)
#: The veil over the WHOLE picture when revealed, so the eye moves from the
#: illustration to the words.
SIGNATURE_VEIL_ALPHA = 86
#: Reveal is quantised to this many steps before it reaches the face cache.
#: A continuous 0..1 would paint a new face every frame of the hover — which
#: is exactly the mistake ``SIZE_STEP`` exists to prevent one axis over.
REVEAL_STEPS = 8

# ── card backs ───────────────────────────────────────────────────────────────
#: How much light a fully hovered deck pile gains, added to every channel of
#: the printed back.  The drawn back lightens its base colour by 22%; this is
#: the equivalent for a picture, which has no single base colour to lighten.
#: Kept low on purpose — the shipped backs are already bright metal, and past
#: about 60 the brass frame clips to white and the emblem disappears into it.
BACK_HOVER_LIGHT = 42

_CACHE_LIMIT = 320


def _lerp(low: float, high: float, t: float) -> float:
    """Straight-line interpolation, used for every reveal-driven number.

    One helper rather than the same arithmetic written out six times: the
    resting value and the revealed value then sit side by side at the call
    site, which is the form ``Layout._lerp`` settled on in stage 29 for the
    same reason.
    """
    return low + (high - low) * t


class CardRenderer:
    def __init__(self, renderer: Renderer, library: ContentLibrary,
                 art: Optional[CardArtLibrary] = None,
                 backs: Optional[CardBackLibrary] = None) -> None:
        self.r = renderer
        self.library = library
        self.theme = renderer.theme
        self._images: Dict[str, Optional[pygame.Surface]] = {}
        self._faces: Dict[tuple, pygame.Surface] = {}
        self._rainbow = library.pawn_colors()
        #: Which cards have full-card artwork.  Injectable so a test can point
        #: at a folder of its own without touching the shipped one.
        self.art = art if art is not None else CardArtLibrary()
        #: Which picture is the back of which deck.  Injectable for the same
        #: reason ``art`` is, and SEPARATE from it: a card back is not card
        #: art, and neither may ever resolve through the other's folder.
        self.backs = backs if backs is not None else CardBackLibrary()

    def clear_cache(self) -> None:
        """Drop cached faces — called when the window resizes."""
        self._faces.clear()

    # ── artwork ──────────────────────────────────────────────────────────────
    def _image(self, name: Optional[str]) -> Optional[pygame.Surface]:
        """Load card art on demand; a missing file is not an error."""
        if not name:
            return None
        if name in self._images:
            return self._images[name]
        path = settings.IMAGE_DIR / name
        surface: Optional[pygame.Surface] = None
        if path.exists():
            try:
                surface = pygame.image.load(str(path)).convert_alpha()
            except pygame.error:  # pragma: no cover - broken asset
                surface = None
        self._images[name] = surface
        return surface

    # ── cache plumbing ───────────────────────────────────────────────────────
    def _store(self, key: tuple, surface: pygame.Surface) -> pygame.Surface:
        if len(self._faces) > _CACHE_LIMIT:
            self._faces.clear()
        self._faces[key] = surface
        return surface

    def _font(self, size: Size, fraction: float, bold: bool = False,
              display: bool = False):
        """Type on a card is a fraction of the CARD, not of the interface.

        It used to be both, and that was the whole of the "cards are unreadable
        on my laptop" report (stage 29).  ``size[1]`` already tracks the
        interface scale — a bigger window makes a bigger card — and then
        ``FontBook.get`` multiplied by that scale a SECOND time, so card type
        moved with the scale SQUARED.  Between 2560x1440 and 1920x1200 that
        turned an 17% smaller card into 32% smaller type on it: descriptions
        wrapped into a stack of two-word lines and titles stopped being
        titles.

        Dividing the font scale back out anchors the ratio: a card is now
        equally readable at every size, and at ``CARD_TYPE_ANCHOR`` the
        arithmetic cancels exactly, so the reference display renders the type
        it always did.  The 4K case is fixed by the same line — its cards had
        the OPPOSITE problem, type so large it crowded out the description.
        """
        base = max(8, int(size[1] * fraction))
        scale = max(0.01, float(self.r.fonts.scale))
        # ONE rounding, not two: ``FontBook.get`` multiplies by the scale and
        # rounds itself, so handing it a float makes the rendered size exactly
        # ``base * CARD_TYPE_ANCHOR`` on every display.  Rounding here as well
        # left small cards a pixel or two under, which is visible precisely
        # where it matters least — and unmeasurable where it matters most.
        return self.r.fonts.get(base * CARD_TYPE_ANCHOR / scale, bold=bold,
                                display=display)

    def _title_font(self, size: Size, text: str, max_width: int,
                    fraction: float = TITLE_FRACTION, display: bool = False):
        """The title font, shrunk until the longest WORD fits on a line.

        ``Renderer.wrap_lines`` breaks mid-word when a word cannot fit, which
        is the right fallback for a paragraph and the wrong one for a title:
        "Thunderfuck" came out as "Thunderfuc" over "k" at every resolution,
        the reference display included.  A title is a name, and a name split
        across a line break stops reading as one.

        The type gives way instead, down to three quarters of its size — past
        that the cure is worse than the disease and the mid-word break is
        allowed to happen, because an unreadable whole word is not a win.
        """
        words = text.split()
        if not words:
            return self._font(size, fraction, bold=True, display=display)
        step = 1.0
        while step >= TITLE_MIN_STEP:
            font = self._font(size, fraction * step, bold=True, display=display)
            if all(font.size(word)[0] <= max_width for word in words):
                return font
            step -= 0.04
        return self._font(size, fraction * TITLE_MIN_STEP, bold=True,
                          display=display)

    # ── faces ────────────────────────────────────────────────────────────────
    def face(
        self,
        card: Card,
        size: Size = NATIVE_SIZE,
        *,
        highlighted: bool = False,
        border_color: Optional[Color] = None,
        dim: bool = False,
        reveal: Optional[float] = None,
    ) -> pygame.Surface:
        """The painted front of a card, cached.

        ``reveal`` (0..1) only means anything to a SIGNATURE card — one with
        artwork in ``assets/card_art`` — where it drives the hover state: 0 is
        the picture with its title along the bottom, 1 is the picture darkened
        with the title lifted and the description under it.  ``None`` derives
        it from ``highlighted``, so every caller that already knew about hover
        gets the reveal for free and only the hand fan, which has a smooth
        hover value of its own, needs to pass anything.

        A card with no artwork ignores it entirely and is painted exactly as it
        was before this existed.
        """
        artwork = self.art.surface(card.definition)
        if artwork is not None:
            if reveal is None:
                reveal = 1.0 if highlighted else 0.0
            return self._signature_face(
                card, artwork, size, highlighted=highlighted,
                border_color=border_color, dim=dim, reveal=reveal,
            )

        # The badge is part of the key because it is part of the picture.  It
        # used to be left out on the assumption that a title implies a badge,
        # which stopped being true the moment a badge could carry a pawn COUNT:
        # two cards with the same words and different dots shared one surface.
        # It goes on the END: the size lives at index 4 and tests read it there.
        #
        # The FONT SCALE is on the end too (stage 29).  ``_font`` divides it
        # back out, so a face painted before a resize and one painted after can
        # legitimately want different type at the same pixel size — and a
        # window can be resized without every screen holding a CardRenderer
        # being on the stack to hear about it.
        key = (
            "face", card.definition.deck_id, card.title, card.text,
            size, highlighted, border_color, dim, card.badge,
            round(float(self.r.fonts.scale), 3),
        )
        cached = self._faces.get(key)
        if cached is not None:
            return cached

        w, h = size
        surface = pygame.Surface(size, pygame.SRCALPHA)
        theme = self.theme
        radius = max(5, int(h * 0.05))

        # Parchment, lit from the top: a card in the concept is a physical
        # thing, and a flat fill is the fastest way to lose that.
        top = theme.card_bg_highlight if highlighted else theme.card_bg
        bottom = lighten(theme.card_bg_shade, 0.12) if highlighted else theme.card_bg_shade
        if dim:
            top, bottom = darken(top, 0.86), darken(bottom, 0.86)
        self.r.rounded_gradient(surface, pygame.Rect(0, 0, w, h), top, bottom, radius)

        if border_color is not None:
            border = lighten(border_color, 0.32) if highlighted else darken(border_color, 0.8)
        else:
            border = theme.card_border_hover if highlighted else theme.card_border

        # ONE border, not two.  The concept's double frame — an outer edge plus
        # an inset brass rule — was dropped in stage 31: it read as a stray line
        # across an illustration once cards could carry full-card artwork, and
        # on a parchment card it was never carrying information either.  The
        # outer edge stays, because it is the card's silhouette AND the channel
        # the drag preview colours green/red/prompt through ``border_color``.
        pygame.draw.rect(surface, border, pygame.Rect(0, 0, w, h),
                         max(2, int(h * 0.014)), border_radius=radius)
        # Kept as the text margin it always doubled as; nothing is drawn on it.
        inset = max(3, int(h * 0.028))

        pawn_color = self._pawn_color(card.badge)
        title_color = darken(pawn_color, 0.55) if pawn_color else theme.card_title
        body_font = self._font(size, 0.054)

        pad = inset + max(4, int(w * 0.05))
        title_font = self._title_font(size, card.title, w - 2 * pad)
        title_lines = self.r.wrap_lines(card.title, title_font, w - 2 * pad)[:2]
        row_h = title_font.get_height() + 1
        title_top = inset + max(4, int(h * 0.030))
        for i, line in enumerate(title_lines):
            self.r.text(line, title_font, title_color, surface,
                        midtop=(w // 2, title_top + i * row_h))

        div_y = title_top + len(title_lines) * row_h + max(3, int(h * 0.016))
        # A hairline rule with a small diamond, matching the panel headings.
        pygame.draw.line(surface, theme.card_divider,
                         (pad, div_y), (w - pad, div_y), 1)
        if w > 70:
            self.r.diamond((w // 2, div_y), max(2, int(h * 0.012)),
                           theme.card_divider, surface)

        badge_h = int(h * 0.13) if card.badge is not None else int(h * 0.04)
        body_top = div_y + max(4, int(h * 0.028))

        art = self._image(card.definition.image)
        if art is not None:
            art_h = min(int(h * 0.36), h - div_y - badge_h - int(h * 0.2))
            if art_h > 16:
                scaled = pygame.transform.smoothscale(art, (w - 2 * pad, art_h))
                surface.blit(scaled, (pad, body_top))
                body_top += art_h + 4

        self.r.draw_wrapped(
            card.text, body_font, theme.card_text,
            pygame.Rect(pad, body_top, w - 2 * pad, h - body_top - badge_h - 4),
            surface,
        )

        if card.badge is not None:
            self._draw_badge(surface, size, card.badge)
        return self._store(key, surface)

    # ── signature faces ──────────────────────────────────────────────────────
    def _cover(self, art: pygame.Surface, size: Size) -> pygame.Surface:
        """The artwork scaled to FILL ``size``, centred, overflow cropped.

        Cover, not fit: a picture letterboxed inside a card would show the
        parchment it was supposed to replace, and a picture stretched to the
        card would distort — the brief forbids both.  Scaling by the LARGER of
        the two ratios keeps the aspect exactly and spends the difference on a
        crop, which for a centred subject costs nothing.
        """
        w, h = size
        aw, ah = art.get_size()
        if aw <= 0 or ah <= 0:  # pragma: no cover - defensive
            return pygame.Surface(size, pygame.SRCALPHA)
        factor = max(w / aw, h / ah)
        scaled = pygame.transform.smoothscale(
            art, (max(1, int(math.ceil(aw * factor))),
                  max(1, int(math.ceil(ah * factor)))),
        )
        layer = pygame.Surface(size, pygame.SRCALPHA)
        layer.blit(scaled, ((w - scaled.get_width()) // 2,
                            (h - scaled.get_height()) // 2))
        return layer

    def _scrim(self, size: Size, band: int, alpha: int) -> pygame.Surface:
        """A bottom-up gradient from transparent to ``alpha``.

        Drawn on its own surface and blitted rather than drawn straight onto
        the artwork: pygame's draw functions REPLACE the destination pixel
        including its alpha, so a gradient drawn in place would punch holes in
        the picture instead of shading it.
        """
        w = size[0]
        scrim = pygame.Surface((w, max(1, band)), pygame.SRCALPHA)
        colour = self.theme.card_art_scrim
        for row in range(band):
            # Squared falloff: linear reads as a grey wedge with a visible top
            # edge, which is the one thing a scrim must not have.
            t = (row / max(1, band - 1)) ** 2
            pygame.draw.line(scrim, (*colour, int(alpha * t)),
                             (0, row), (w, row))
        return scrim

    def _outlined(self, surface: pygame.Surface, text: str, font,
                  midtop: Tuple[int, int], thickness: int) -> None:
        """Title type with a hard outline, because the backdrop is a photograph.

        A drop shadow is enough over parchment and not over an arbitrary
        picture: the Troll artwork alone runs from near-white sneaker to black
        smoke behind where the title sits.  An outline is the only thing that
        holds at both ends.
        """
        theme = self.theme
        ring = self.r.text_surface(text, font, theme.card_art_title_outline)
        rect = ring.get_rect(midtop=midtop)
        for dx in (-thickness, 0, thickness):
            for dy in (-thickness, 0, thickness):
                if dx or dy:
                    surface.blit(ring, (rect.x + dx, rect.y + dy))
        self.r.text(text, font, theme.card_art_title, surface, midtop=midtop)

    def _description_font(self, size: Size, text: str, room: Tuple[int, int]):
        """The largest description type whose wrapped lines fit ``room``.

        Quoted as a fraction of the CARD and shrunk from there, so it obeys the
        stage-29 invariant (a card of a given pixel size renders identically on
        every monitor) while still guaranteeing that a long Polish card text
        cannot overflow a small card.
        """
        room_w, room_h = room
        step = 1.0
        font = self._font(size, SIGNATURE_TEXT_FRACTION)
        lines = self.r.wrap_lines(text, font, room_w) if text else []
        while step > SIGNATURE_TEXT_MIN:
            if not lines or len(lines) * (font.get_height() + 2) <= room_h:
                break
            step -= 0.05
            font = self._font(size, SIGNATURE_TEXT_FRACTION * step)
            lines = self.r.wrap_lines(text, font, room_w)
        # Below ``SIGNATURE_TEXT_MIN`` — and on a card so small that the font
        # floor in ``_font`` has taken over — shrinking has stopped helping, so
        # the overflow is CUT rather than allowed to run off the card.  This is
        # what ``Renderer.draw_wrapped`` already does to a standard card's body
        # on a thumbnail; a Signature card must not be the one place where long
        # Polish rules text escapes its frame.
        fits = max(1, room_h // (font.get_height() + 2))
        return font, lines[:fits]

    def _signature_face(
        self,
        card: Card,
        art: pygame.Surface,
        size: Size,
        *,
        highlighted: bool,
        border_color: Optional[Color],
        dim: bool,
        reveal: float,
    ) -> pygame.Surface:
        """A card whose artwork IS the face.

        Two states out of one layout.  The title and description are laid out
        as one block anchored to the bottom margin, and the title's resting
        position is that same block with the description removed.  The reveal
        interpolates between the two, so the title rises by EXACTLY the height
        of the description that is appearing under it — at any card size, for
        any length of text, with no tuned offsets to go wrong on a monitor
        nobody tested on.
        """
        step = max(0.0, min(1.0, reveal))
        step = round(step * REVEAL_STEPS) / REVEAL_STEPS
        key = (
            "signature", self.art.key(card.definition), card.title, card.text,
            size, highlighted, border_color, dim, step,
            round(float(self.r.fonts.scale), 3),
        )
        cached = self._faces.get(key)
        if cached is not None:
            return cached

        w, h = size
        theme = self.theme
        radius = max(5, int(h * 0.05))
        surface = self._cover(art, size)

        # 1. The picture darkens as a whole, so the words win the foreground.
        veil = int(SIGNATURE_VEIL_ALPHA * step) + (60 if dim else 0)
        if veil:
            wash = pygame.Surface(size, pygame.SRCALPHA)
            wash.fill((*theme.card_art_veil, min(255, veil)))
            surface.blit(wash, (0, 0))

        # 2. The lower portion takes a scrim, present even at rest because the
        #    title has to be readable there too.
        band = int(h * _lerp(*SIGNATURE_SCRIM_RISE, step))
        surface.blit(self._scrim(size, band, int(_lerp(*SIGNATURE_SCRIM_ALPHA, step))),
                     (0, h - band))

        # 3. Round the corners.  A rectangular picture inside the rounded cards
        #    the rest of the game draws would read as a bug.
        mask = pygame.Surface(size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, w, h),
                         border_radius=radius)
        surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # 4. The same single border every other card has, so a Signature card
        #    still belongs to this game and hover/selection borders still read.
        #    NOTHING else is drawn over the picture: the artwork is the face,
        #    and a rule laid across it is a scratch on the illustration rather
        #    than a frame around it.  See stage 31.
        if border_color is not None:
            border = lighten(border_color, 0.32) if highlighted else darken(border_color, 0.8)
        else:
            border = theme.card_border_hover if highlighted else theme.card_border
        pygame.draw.rect(surface, border, pygame.Rect(0, 0, w, h),
                         max(2, int(h * 0.014)), border_radius=radius)
        # Kept as the text margin it always doubled as; nothing is drawn on it.
        inset = max(3, int(h * 0.028))

        # 5. Type.
        pad = inset + max(4, int(w * 0.055))
        room_w = max(16, w - 2 * pad)
        gap = max(2, int(h * 0.016))
        bottom_pad = max(4, int(h * 0.05))

        title_font = self._title_font(size, card.title, room_w,
                                      SIGNATURE_TITLE_FRACTION, display=True)
        title_lines = self.r.wrap_lines(card.title, title_font, room_w)[:2]
        title_row = title_font.get_height() + 1
        title_h = len(title_lines) * title_row

        desc_font, desc_lines = self._description_font(
            size, card.text, (room_w, int(h * SIGNATURE_TEXT_ROOM)))
        desc_row = desc_font.get_height() + 2
        desc_h = len(desc_lines) * desc_row

        resting_top = h - bottom_pad - title_h
        # The block, bottom-anchored — never let it climb off the card.
        opened_top = max(inset + gap,
                         h - bottom_pad - desc_h - 2 * gap - title_h)
        title_top = int(round(_lerp(resting_top, opened_top, step)))

        outline = max(1, int(round(h * 0.007)))
        for index, line in enumerate(title_lines):
            self._outlined(surface, line, title_font,
                           (w // 2, title_top + index * title_row), outline)

        # The divider and the description fade in together, on one layer, so
        # ``set_alpha`` is applied to a surface this method owns.  Setting it
        # on a cached ``text_surface`` would dim that string everywhere else on
        # screen for the rest of the frame.
        if step > 0.0 and desc_lines:
            divider_y = title_top + title_h + gap
            block_h = max(1, (h - divider_y))
            block = pygame.Surface((w, block_h), pygame.SRCALPHA)
            pygame.draw.line(block, theme.card_art_divider,
                             (pad, 1), (w - pad, 1), 1)
            if w > 70:
                self.r.diamond((w // 2, 1), max(2, int(h * 0.011)),
                               theme.card_art_divider, block)
            top = gap + 1
            for index, line in enumerate(desc_lines):
                y = top + index * desc_row
                if y + desc_row > block_h:
                    break
                self.r.text(line, desc_font, theme.card_art_text, block,
                            midtop=(w // 2, y), shadow=True)
            block.set_alpha(int(255 * step))
            surface.blit(block, (0, divider_y))

        return self._store(key, surface)

    def back(
        self, size: Size = NATIVE_SIZE, color: Optional[Color] = None,
        brightness: float = 0.0, deck_id: Optional[str] = None,
    ) -> pygame.Surface:
        """The back of a card.

        ``deck_id`` selects the PICTURE — the back of a card is the artwork
        configured for its deck in ``settings.CARD_BACKS``.  This method
        branches on that at its first line, the way ``face()`` branches on card
        artwork, so no caller needs to know whether a deck has a picture and no
        caller ever names a file.

        Without a deck id, or when that deck's picture is missing or will not
        load, the drawn back below is painted instead: a bound cover in the
        deck's own colour.  That path is the fallback and the development
        placeholder, not the normal game path.

        ``brightness`` (0..1) lightens the whole back. Deck hovering uses it:
        lifting the card's own colour reads as "this is live" far better than
        the halo that used to be drawn around the pile, which mostly looked
        like a rendering artefact.
        """
        step = round(max(0.0, min(1.0, brightness)) * 10) / 10

        picture = self.backs.surface(deck_id)
        if picture is not None:
            return self._picture_back(deck_id, picture, size, step)

        key = ("back", size, color, step)
        cached = self._faces.get(key)
        if cached is not None:
            return cached

        w, h = size
        surface = pygame.Surface(size, pygame.SRCALPHA)
        theme = self.theme
        radius = max(5, int(h * 0.05))
        base = color if color is not None else theme.card_back_bg
        if step:
            base = lighten(base, 0.22 * step)

        # A deck back in the concept is a bound cover: deep colour, brass frame,
        # an emblem in the middle.
        self.r.rounded_gradient(surface, pygame.Rect(0, 0, w, h),
                                lighten(base, 0.12), darken(base, 0.74), radius)
        pygame.draw.rect(surface, darken(base, 0.45), pygame.Rect(0, 0, w, h),
                         max(2, int(h * 0.014)), border_radius=radius)

        inset = max(4, int(h * 0.045))
        frame = pygame.Rect(inset, inset, w - 2 * inset, h - 2 * inset)
        pygame.draw.rect(surface, theme.card_back_deco, frame, 1,
                         border_radius=max(3, radius - 2))
        for corner in ((frame.left, frame.top), (frame.right, frame.top),
                       (frame.left, frame.bottom), (frame.right, frame.bottom)):
            self.r.diamond(corner, max(2, int(h * 0.014)), theme.card_back_deco, surface)

        cx, cy = w // 2, h // 2
        emblem = max(6, int(h * 0.13))
        self.r.aa_circle((cx, cy), emblem + 3, darken(base, 0.6),
                         theme.card_back_deco, 1, surface)
        self.r.diamond((cx, cy), emblem, mix(base, theme.card_back_deco, 0.55), surface)
        self.r.diamond((cx, cy), max(2, emblem // 2), darken(base, 0.5), surface)
        return self._store(key, surface)

    def _picture_back(
        self, deck_id: Optional[str], art: pygame.Surface, size: Size, step: float,
    ) -> pygame.Surface:
        """A deck whose back is a picture.

        Three things happen to the artwork and nothing else does — a card back
        carries no text, no badge and no state, so unlike ``_signature_face``
        there is nothing to lay out over it:

        1. COVER-SCALED through the same ``_cover`` a Signature face uses, so a
           back is cropped rather than letterboxed or stretched.  The shipped
           backs are about 1060x1490 against a card's 1:1.43, so the crop is a
           few pixels off the long edge.
        2. Corners rounded to the card's own radius, so a picture back has the
           same silhouette as every other card in the game.
        3. Brightness added, for the deck-panel hover.  An ADDITIVE wash rather
           than ``lighten`` on a base colour, because there is no base colour
           here to lighten — and additive light on the printed brass reads as
           the same "this pile is live" the drawn back gave.

        Cached by ``(deck_id, size, step)``, so a pile of three backs and the
        sixty frames a second behind them are one blit each.  ``deck_id`` is in
        the key because it is what chose the picture: without it the movement
        and chest piles, identical in size, would share one surface.
        """
        key = ("picture_back", deck_id, size, step)
        cached = self._faces.get(key)
        if cached is not None:
            return cached

        w, h = size
        radius = max(5, int(h * 0.05))
        surface = self._cover(art, size)

        if step:
            glow = int(round(BACK_HOVER_LIGHT * step))
            if glow:
                wash = pygame.Surface(size, pygame.SRCALPHA)
                wash.fill((glow, glow, glow, 0))
                surface.blit(wash, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

        mask = pygame.Surface(size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, w, h),
                         border_radius=radius)
        surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        return self._store(key, surface)

    def empty(self, size: Size = NATIVE_SIZE, label: Optional[str] = None) -> pygame.Surface:
        key = ("empty", size, label)
        cached = self._faces.get(key)
        if cached is not None:
            return cached
        w, h = size
        surface = pygame.Surface(size, pygame.SRCALPHA)
        theme = self.theme
        radius = max(5, int(h * 0.05))
        # An empty slot is a hollow in the panel, not a card-shaped rectangle:
        # dashed brass on a recess, so it reads as "something goes here".
        pygame.draw.rect(surface, theme.card_empty_bg, pygame.Rect(0, 0, w, h),
                         border_radius=radius)
        self.r.rounded_gradient(surface, pygame.Rect(1, 1, w - 2, h - 2),
                                darken(theme.panel_bg, 0.9), theme.card_empty_bg,
                                radius)
        dash = max(4, h // 22)
        for x in range(radius, w - radius, dash * 2):
            pygame.draw.line(surface, theme.card_empty_line, (x, 1), (min(x + dash, w - radius), 1))
            pygame.draw.line(surface, theme.card_empty_line, (x, h - 2),
                             (min(x + dash, w - radius), h - 2))
        for y in range(radius, h - radius, dash * 2):
            pygame.draw.line(surface, theme.card_empty_line, (1, y), (1, min(y + dash, h - radius)))
            pygame.draw.line(surface, theme.card_empty_line, (w - 2, y),
                             (w - 2, min(y + dash, h - radius)))
        pygame.draw.rect(surface, theme.card_empty_line, pygame.Rect(0, 0, w, h), 1,
                         border_radius=radius)
        if label:
            self.r.spaced_text(label.upper(), self._font(size, 0.055, bold=True),
                               theme.text_dim, surface, center=(w // 2, h // 2),
                               spacing=1)
        return self._store(key, surface)

    # ── blitting ─────────────────────────────────────────────────────────────
    def draw(
        self,
        card: Card,
        x: int,
        y: int,
        surface: Optional[pygame.Surface] = None,
        *,
        size: Size = NATIVE_SIZE,
        highlighted: bool = False,
        border_color: Optional[Color] = None,
        lift: int = 0,
        dim: bool = False,
        shadow: bool = True,
        reveal: Optional[float] = None,
    ) -> pygame.Rect:
        target = self.r.target(surface)
        y -= lift
        rect = pygame.Rect(x, y, size[0], size[1])
        if shadow:
            self.r.drop_shadow(rect, radius=max(4, int(size[1] * 0.036)),
                               spread=6 + lift, alpha=110, offset=(0, 3 + lift),
                               surface=target)
        target.blit(
            self.face(card, size, highlighted=highlighted,
                      border_color=border_color, dim=dim, reveal=reveal),
            rect.topleft,
        )
        return rect

    def draw_in(
        self, card: Card, rect: pygame.Rect,
        surface: Optional[pygame.Surface] = None, **kwargs,
    ) -> pygame.Rect:
        """Draw a card filling a rect — the form every panel uses."""
        return self.draw(card, rect.x, rect.y, surface, size=(rect.w, rect.h), **kwargs)

    def _silhouette(self, surface: pygame.Surface, key: tuple) -> pygame.Surface:
        """A black copy of a card surface, used as its shadow.

        Multiplying the RGB to zero keeps the alpha channel, so the shadow has
        exactly the card's outline — including its rotation. The previous
        version drew an upright rectangle behind every card, which looked wrong
        the moment the fan tilted them.
        """
        cached = self._faces.get(key)
        if cached is not None:
            return cached
        shadow = surface.copy()
        shadow.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
        # Two blurred-ish passes: scale down and back up to soften the edge.
        small = pygame.transform.smoothscale(
            shadow, (max(1, shadow.get_width() // 6), max(1, shadow.get_height() // 6))
        )
        soft = pygame.transform.smoothscale(small, shadow.get_size())
        soft.set_alpha(130)
        return self._store(key, soft)

    #: Card sizes are rounded to this many pixels of height before a face is
    #: painted.  Without it a smoothly growing card would paint a new face every
    #: frame; with it there are a handful of steps, each one crisp.
    SIZE_STEP = 6

    def quantised(self, size: Size, scale: float = 1.0) -> Size:
        """The size a face will actually be painted at.

        Scaling is applied *before* the face is drawn, not after: enlarging an
        already-rasterised card is what made hovered cards and previews blurry.
        """
        height = max(24, int(round(size[1] * scale / self.SIZE_STEP)) * self.SIZE_STEP)
        return (max(16, int(round(height / CARD_ASPECT))), height)

    def draw_transformed(
        self,
        card: Card,
        centre: Tuple[float, float],
        angle: float,
        surface: Optional[pygame.Surface] = None,
        *,
        size: Size = NATIVE_SIZE,
        scale: float = 1.0,
        highlighted: bool = False,
        border_color: Optional[Color] = None,
        shadow: int = 8,
        reveal: Optional[float] = None,
    ) -> pygame.Rect:
        """Draw a card rotated about its centre — the hand fan and drag ghost.

        ``angle`` is in degrees, positive counter-clockwise (pygame's
        convention), so a fan tilts its right-hand cards with negative angles.
        The shadow is the rotated silhouette offset downwards, which is what a
        card lying above a table actually casts.

        A scaled card is **repainted at the larger size**, not zoomed: the text
        is laid out again at the size it will be seen, so a card that grows
        under the cursor gets sharper rather than softer.
        """
        target = self.r.target(surface)
        paint_size = self.quantised(size, scale) if abs(scale - 1.0) > 0.005 else size
        face = self.face(card, paint_size, highlighted=highlighted,
                         border_color=border_color, reveal=reveal)
        if abs(angle) < 0.05:
            rotated = face
        else:
            rotated = pygame.transform.rotozoom(face, angle, 1.0)
        rect = rotated.get_rect(center=(int(centre[0]), int(centre[1])))

        if shadow:
            # Quantise the transform so the cache is not defeated by animation.
            key = ("shadow", size, round(angle, 1), round(scale, 2))
            silhouette = self._silhouette(rotated, key)
            offset = (shadow // 3, max(3, shadow // 2))
            target.blit(silhouette, (rect.x + offset[0], rect.y + offset[1]))
        target.blit(rotated, rect.topleft)
        return rect

    def draw_back(
        self, x: int, y: int, color: Optional[Color] = None,
        surface: Optional[pygame.Surface] = None, size: Size = NATIVE_SIZE,
        brightness: float = 0.0, deck_id: Optional[str] = None,
    ) -> pygame.Rect:
        target = self.r.target(surface)
        rect = pygame.Rect(x, y, size[0], size[1])
        self.r.drop_shadow(rect, radius=max(4, int(size[1] * 0.036)),
                           spread=5 + int(4 * brightness), alpha=100,
                           offset=(0, 3), surface=target)
        target.blit(self.back(size, color, brightness, deck_id), rect.topleft)
        return rect

    def draw_empty(
        self, x: int, y: int, surface: Optional[pygame.Surface] = None,
        label: Optional[str] = None, size: Size = NATIVE_SIZE,
    ) -> pygame.Rect:
        target = self.r.target(surface)
        target.blit(self.empty(size, label), (x, y))
        return pygame.Rect(x, y, size[0], size[1])

    def draw_pile(
        self,
        x: int,
        y: int,
        count: int,
        color: Optional[Color],
        surface: Optional[pygame.Surface] = None,
        empty_label: str = "pusto",
        size: Size = NATIVE_SIZE,
        brightness: float = 0.0,
        deck_id: Optional[str] = None,
    ) -> pygame.Rect:
        """A draw pile: up to three stacked backs so depth is visible.

        Only the top card takes the hover brightness — the ones underneath stay
        in its shadow, which is what stops the effect looking like a light bulb.

        ``deck_id`` is passed straight down to ``back()``, which is where the
        deck's own picture is chosen.  Every card in a pile belongs to the same
        deck, so all three backs are the same surface out of the cache.
        """
        if count <= 0:
            return self.draw_empty(x, y, surface, empty_label, size)
        lift = int(round(brightness * 2))
        for depth in range(min(3, count) - 1, -1, -1):
            top = depth == 0
            self.draw_back(
                x + depth * 2, y + depth * 2 - (lift if top else 0), color, surface,
                size, brightness if top else 0.0, deck_id,
            )
        return pygame.Rect(x, y, size[0], size[1])

    # ── badge ────────────────────────────────────────────────────────────────
    def _pawn_color(self, badge: Optional[Badge]) -> Optional[Color]:
        if badge is None or badge.is_rainbow:
            return None
        return self.library.pawn_color(badge.pawn)

    def _draw_badge(self, surface: pygame.Surface, size: Size, badge: Badge) -> None:
        w, h = size
        font = self._font(size, 0.062, bold=True)
        radius = max(4, int(h * 0.035))
        cy = h - int(h * 0.075)
        sign_surface = self.r.text_surface(badge.sign, font, (55, 45, 25))
        arrow_surface = (
            self.r.text_surface("\u2191", font, (55, 45, 25)) if badge.arrow else None
        )

        gap = 4
        # Pawn markers overlap slightly, so two of them read as "a pair of
        # pawns" rather than as two unrelated dots — and so a badge showing two
        # still fits under a card in the fan at 1280×760.
        pawns = max(1, badge.count)
        step = radius * 2 if pawns == 1 else int(radius * 1.55)
        pawns_w = radius * 2 + (pawns - 1) * step
        total_w = (
            pawns_w + gap + sign_surface.get_width()
            + (arrow_surface.get_width() + gap if arrow_surface else 0)
        )
        start_x = w // 2 - total_w // 2

        for index in range(pawns):
            cx = start_x + radius + index * step
            if badge.is_rainbow:
                self.r.pie_circle((cx, cy), radius, self._rainbow, surface)
            else:
                color = self.library.pawn_color(badge.pawn) or (200, 200, 200)
                self.r.aa_circle((cx, cy), radius, color, (70, 60, 40), 1, surface)

        nx = start_x + pawns_w + gap
        if arrow_surface is not None:
            surface.blit(arrow_surface, (nx, cy - arrow_surface.get_height() // 2))
            nx += arrow_surface.get_width() + gap
        surface.blit(sign_surface, (nx, cy - sign_surface.get_height() // 2))
