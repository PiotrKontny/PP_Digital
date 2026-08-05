"""
THE highlight system — one visual language for every interactive thing.

Stage 12 exists because there were two.  Most controls said "I am under the
cursor" the way the concept art asks: the surface lightens a little, the border
warms, the shadow deepens and the whole thing lifts a pixel.  A handful of
others drew a big radial halo *centred on the component* — which, on anything
wider than it is tall, is a glowing circle spilling across half the screen.

The rule that replaced it:

    A HIGHLIGHT FOLLOWS THE SHAPE OF THE THING IT HIGHLIGHTS.

Rectangles get a rounded-rect bloom (``Renderer.shape_glow``); round things —
pawns, turn-order portraits, board fields — get a rim bloom that reaches a
fixed fraction past their own radius (``Renderer.ring_glow``).  Neither can
grow beyond its component, because both take the component's geometry as their
input instead of a multiplier somebody picked by eye.

Everything else in this module is the *amount*: how much lighter a hovered
surface gets, how far a pressed one drops, how strong the bloom is on the
active player's tile.  Those numbers live here, once, so a new panel or button
inherits the same behaviour by asking for it rather than by copying a line of
``lighten(fill, 0.18 if hovered else 0.0)`` that slowly drifts from its
neighbours — which is how the interface ended up with five different hovers.

Call ``emphasis()`` to turn a component's base colours plus its state into an
:class:`Emphasis`, then hand that to ``Renderer.interactive_panel``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..config.theme import Theme, darken, lighten

Color = Tuple[int, int, int]


# ── how far a highlight may reach ────────────────────────────────────────────
#: A glow may bloom this fraction past the edge of its component and no further.
#: The old radial halos used 1.7–2.6 *times* the component size; this is the
#: number that makes "subtle" mechanical rather than a matter of taste.
BLOOM = 0.42
#: Absolute ceiling in pixels, so a wide panel does not get a wide-screen halo.
BLOOM_MAX = 26
BLOOM_MIN = 5

# ── hover ────────────────────────────────────────────────────────────────────
#: One hover, everywhere: lighten the surface, warm the border, lift it a little
#: and deepen the shadow underneath.  Depth, not a colour swap.
HOVER_FILL = 0.16
HOVER_BORDER = 0.34
HOVER_TEXT = 0.20
HOVER_LIFT = 1
HOVER_GLOW = 0.30

# ── selection / focus / active ───────────────────────────────────────────────
#: "This one is chosen", "this is the seat in turn", "the engine is asking about
#: this".  Stronger than hover and it keeps the accent colour, but it is still a
#: bloom around the outline — never a disc.
SELECTED_FILL = 0.07
SELECTED_GLOW = 0.55
FOCUS_GLOW = 0.45

# ── pressed ──────────────────────────────────────────────────────────────────
#: Pressed drops onto the surface and loses its shadow, so the depth reads.
PRESSED_DROP = 2
PRESSED_DARKEN = 0.88

# ── shadows ──────────────────────────────────────────────────────────────────
SHADOW_RESTING = 5
SHADOW_HOVER = 12
SHADOW_SELECTED = 10
SHADOW_QUIET = 3


@dataclass(frozen=True)
class Emphasis:
    """A component's colours and depth for one frame, in one object.

    Produced by :func:`emphasis` and consumed by
    ``Renderer.interactive_panel``.  Nothing outside this module should work
    out its own hover colours: that is exactly the drift this stage removed.
    """

    fill: Color
    border: Color
    text: Color
    glow: Optional[Color] = None
    glow_strength: float = 0.0
    shadow: int = SHADOW_RESTING
    #: Vertical offset — negative lifts, positive presses down.
    offset: int = 0

    def with_fill(self, fill: Color) -> "Emphasis":
        return Emphasis(fill, self.border, self.text, self.glow,
                        self.glow_strength, self.shadow, self.offset)


def emphasis(
    theme: Theme,
    *,
    fill: Optional[Color] = None,
    border: Optional[Color] = None,
    text: Optional[Color] = None,
    hover: float = 0.0,
    selected: bool = False,
    focused: bool = False,
    pressed: bool = False,
    enabled: bool = True,
    accent: Optional[Color] = None,
    quiet: bool = False,
) -> Emphasis:
    """Resolve a component's base colours and its state into one description.

    ``hover`` is a 0..1 level rather than a flag, because every hover in this
    game eases in — a control that snapped between two looks would be the one
    thing on screen that felt cheap.

    ``selected`` is the state the engine or the rules care about: the seat in
    turn, the card being kept, the field being asked about.  ``focused`` is the
    keyboard's idea of the same thing.  ``quiet`` asks for the resting shadow of
    something that is decoration rather than a control.
    """
    base_fill = fill if fill is not None else theme.btn_idle_bg
    base_border = border if border is not None else theme.btn_idle_border
    base_text = text if text is not None else theme.btn_text
    accent_colour = accent if accent is not None else theme.accent

    if not enabled:
        return Emphasis(
            fill=theme.btn_disabled_bg,
            border=theme.panel_line,
            text=theme.btn_disabled_text,
            glow=None,
            glow_strength=0.0,
            shadow=0,
            offset=0,
        )

    hover = max(0.0, min(1.0, hover))
    body = base_fill
    edge = base_border
    label = base_text
    strength = 0.0
    glow_colour: Optional[Color] = None

    if selected:
        body = lighten(body, SELECTED_FILL)
        glow_colour = accent_colour
        strength = SELECTED_GLOW
    elif focused:
        glow_colour = accent_colour
        strength = FOCUS_GLOW

    if hover > 0.01:
        body = lighten(body, HOVER_FILL * hover)
        edge = lighten(edge, HOVER_BORDER * hover)
        label = lighten(label, HOVER_TEXT * hover)
        glow_colour = glow_colour or accent_colour
        strength = max(strength, HOVER_GLOW * hover)

    if pressed:
        body = darken(body, PRESSED_DARKEN)
        return Emphasis(body, edge, label, glow_colour, strength * 0.5, 0,
                        PRESSED_DROP)

    resting = SHADOW_QUIET if quiet else SHADOW_RESTING
    settled = SHADOW_SELECTED if selected else resting
    shadow = int(settled + (SHADOW_HOVER - settled) * hover)
    offset = -int(round(HOVER_LIFT * hover))
    return Emphasis(body, edge, label, glow_colour, strength, shadow, offset)


def bloom_for(width: int, height: int) -> int:
    """How far a rectangle's glow may reach past its own edge, in pixels."""
    return max(BLOOM_MIN, min(BLOOM_MAX, int(min(width, height) * BLOOM)))
