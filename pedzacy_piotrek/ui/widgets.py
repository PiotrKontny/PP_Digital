"""
Reusable widgets.

The prototype drew its buttons, checkboxes and dropdowns inline, which is why
the menu loop was 160 lines of collision tests.  These widgets own their own
rect, hover state and hit test, and return whether they were used — so screens
describe *what* they contain rather than *how* to draw a rounded rectangle.

Every widget takes a :class:`Renderer` and draws through it, so a theme change
propagates everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import pygame

from . import clipboard

from ..config.theme import darken
from ..render.renderer import Renderer

Color = Tuple[int, int, int]


class Widget:
    """Common behaviour: a rect, a hover flag, an enabled flag."""

    def __init__(self, rect: pygame.Rect, enabled: bool = True) -> None:
        self.rect = rect
        self.enabled = enabled
        self.hovered = False
        self.pressed = False
        self._hover_anim = 0.0

    def update(self, mouse_pos: Tuple[int, int], dt: float = 0.0) -> None:
        self.hovered = self.enabled and self.rect.collidepoint(mouse_pos)
        target = 1.0 if self.hovered else 0.0
        self._hover_anim += (target - self._hover_anim) * min(1.0, dt * 12.0 if dt else 1.0)

    def hit(self, pos: Tuple[int, int]) -> bool:
        return self.enabled and self.rect.collidepoint(pos)


#: Space between a button's label and its border.  One number, so a menu of
#: buttons with different labels still reads as a set.
BUTTON_PAD_X = 24
BUTTON_PAD_Y = 15
#: Letter spacing used by every button label.
BUTTON_SPACING = 2
#: Type size a button aims for before its box is measured.  Chosen to land on
#: the same visual weight the old fixed 52–58 px boxes produced, so this stage
#: changes what fits rather than how a button looks.
BUTTON_TEXT_SIZE = 19


class Button(Widget):
    """A raised button in the game's visual language.

    Three states, each a small change rather than a different look: idle sits
    with a shadow under it, hover lightens and lifts, pressed drops onto the
    surface and loses the shadow.  That is what makes a button feel like a
    physical thing instead of a coloured rectangle that changes colour.  The
    amounts come from ``render/highlight.py``, which is what keeps a button,
    a player tile and a dialog option reacting identically.

    SIZING (stage 12).  A button knows how much room its own label needs:
    :meth:`natural_size` measures it and :meth:`fit` grows the rect to hold it.
    Screens call ``fit`` while laying out and stop guessing widths — "Gra
    lokalna (hot-seat)" and "Wróć" are not the same width, and picking one
    number for both is how the long ones ended up clipped.  Drawing shrinks the
    type as a last resort, so a label cannot overflow even if a layout insists
    on a box too small for it.
    """

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        font_role: str = "button",
        radius: int = 10,
        accent: Optional[Tuple[int, int, int]] = None,
        enabled: bool = True,
        primary: bool = False,
        pad_x: int = BUTTON_PAD_X,
        pad_y: int = BUTTON_PAD_Y,
        text_size: int = BUTTON_TEXT_SIZE,
    ) -> None:
        super().__init__(rect, enabled)
        self.label = label
        self.font_role = font_role
        self.radius = radius
        self.accent = accent
        self.primary = primary
        self.pressed = False
        self.pad_x = pad_x
        self.pad_y = pad_y
        self.text_size = text_size
        #: Animated 0..1 hover level, so the change eases in.
        self.glow = 0.0

    # ── sizing ───────────────────────────────────────────────────────────────
    def natural_size(self, r) -> Tuple[int, int]:
        """The smallest box this label fits in comfortably, at full type size."""
        font = r.fonts.get(self.text_size, bold=True)
        width = r.spaced_width(self.label.upper(), font, BUTTON_SPACING)
        return (int(width + 2 * self.pad_x),
                int(font.get_height() + 2 * self.pad_y))

    def fit(self, r, *, min_width: int = 0, min_height: int = 0,
            max_width: Optional[int] = None) -> pygame.Rect:
        """Grow the rect until the label fits, keeping the centre where it was.

        Callers that anchor afterwards (``rect.midtop = ...``) can simply call
        this first; the anchor wins.
        """
        centre = self.rect.center
        width, height = self.natural_size(r)
        width = max(width, min_width)
        height = max(height, min_height)
        if max_width is not None:
            width = min(width, max_width)
        self.rect.size = (int(width), int(height))
        self.rect.center = centre
        return self.rect

    def update(self, mouse_pos: Tuple[int, int], dt: float = 0.0) -> None:
        super().update(mouse_pos, dt)
        from ..engine.animation import approach

        self.glow = approach(self.glow, 1.0 if (self.hovered and self.enabled) else 0.0,
                             14.0, dt)
        if not pygame.mouse.get_pressed()[0]:
            self.pressed = False

    def handle(self, event: pygame.event.Event) -> bool:
        """Track the press so the button can sink under the finger."""
        if not self.enabled:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.pressed = True
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was = self.pressed
            self.pressed = False
            return was and self.rect.collidepoint(event.pos)
        return False

    def draw(self, r, surface: Optional[pygame.Surface] = None) -> None:
        theme = r.theme
        if self.primary:
            base = (theme.btn_primary_bg, theme.btn_primary_border,
                    theme.btn_primary_text)
        else:
            base = (self.accent or theme.btn_idle_bg, theme.btn_idle_border,
                    theme.btn_text)
        style = r.emphasis(fill=base[0], border=base[1], text=base[2],
                           hover=self.glow, pressed=self.pressed,
                           enabled=self.enabled,
                           accent=theme.brass_light if self.primary else None)
        rect = r.interactive_panel(self.rect, style, surface, radius=self.radius)
        r.fit_spaced_text(self.label.upper(), rect, style.text, surface,
                          base_size=self.text_size, spacing=BUTTON_SPACING,
                          padding=max(6, self.pad_x - 12), shadow=self.enabled)


def fit_buttons(r, buttons: Sequence[Button], *, min_width: int = 0,
                min_height: int = 0, max_width: Optional[int] = None) -> Tuple[int, int]:
    """Size a group of buttons to the widest label among them.

    A column of menu buttons should be one column, not a ragged edge — but it
    also must not be narrower than its longest caption.  Measuring all of them
    and giving everyone the maximum is both.
    """
    width, height = min_width, min_height
    for button in buttons:
        needed = button.natural_size(r)
        width = max(width, needed[0])
        height = max(height, needed[1])
    if max_width is not None:
        width = min(width, max_width)
    for button in buttons:
        centre = button.rect.center
        button.rect.size = (int(width), int(height))
        button.rect.center = centre
    return int(width), int(height)


class CircleButton(Widget):
    """Round button — the round counter's ± controls."""

    def __init__(self, rect: pygame.Rect, label: str, color: Color, hover_color: Color) -> None:
        super().__init__(rect)
        self.label = label
        self.color = color
        self.hover_color = hover_color

    def draw(self, r: Renderer, surface: Optional[pygame.Surface] = None,
             text_color: Optional[Color] = None, font=None) -> None:
        target = r.target(surface)
        color = self.hover_color if self.hovered else self.color
        r.aa_circle(self.rect.center, self.rect.width // 2, color, surface=target)
        r.text(self.label, font or r.fonts.button(), text_color, target,
               center=self.rect.center)


class Stepper:
    """``[-10] [-1]  value  [+1] [+10]`` row, centred on a given x.

    The step buttons used to be a flat 40 px square whatever was written on
    them, which is exactly wide enough for "+1" and not for "-10" once the
    window (and with it the type) grows.  Pass a renderer and the row measures
    its own labels instead; ``button_w`` becomes a floor rather than the answer.
    """

    #: Type size of the ± labels, and the space left around them.
    LABEL_SIZE = 22
    PAD_X = 14

    def __init__(
        self,
        centre_x: int,
        row_y: int,
        *,
        big_steps: bool = False,
        button_w: int = 40,
        value_w: int = 100,
        gap: int = 8,
        r=None,
    ) -> None:
        parts = (["minus10"] if big_steps else []) + ["minus1", "value", "plus1"] \
            + (["plus10"] if big_steps else [])
        labels = {"minus10": "-10", "minus1": "-1", "plus1": "+1", "plus10": "+10"}
        # The row keeps the height it was asked for; only the widths grow, or a
        # long label would make the whole row taller as well as wider.
        row_h = button_w
        if r is not None:
            font = r.fonts.get(self.LABEL_SIZE, bold=True)
            widest = max(font.size(labels[p])[0] for p in parts if p != "value")
            button_w = max(button_w, int(widest + 2 * self.PAD_X))
            value_w = max(value_w, int(font.size("000%")[0] + 2 * self.PAD_X))
            row_h = max(row_h, int(font.get_height() + 14))
        widths = {
            "minus10": button_w, "minus1": button_w,
            "value": value_w,
            "plus1": button_w, "plus10": button_w,
        }
        total = sum(widths[p] for p in parts) + gap * (len(parts) - 1)
        x = centre_x - total // 2
        self.rects: dict[str, pygame.Rect] = {}
        for part in parts:
            self.rects[part] = pygame.Rect(x, row_y, widths[part], row_h)
            x += widths[part] + gap
        #: Row height, so a screen can advance past it without re-deriving it.
        self.height = row_h
        self.labels = {"minus10": "-10", "minus1": "-1", "plus1": "+1", "plus10": "+10"}
        self.deltas = {"minus10": -10, "minus1": -1, "plus1": 1, "plus10": 10}
        #: Row position, so a screen can label the row without re-deriving it.
        self.y = row_y

    def hit(self, pos: Tuple[int, int]) -> Optional[int]:
        """Returns the delta of the clicked button, or ``None``."""
        for key, rect in self.rects.items():
            if key == "value":
                continue
            if rect.collidepoint(pos):
                return self.deltas[key]
        return None

    def draw(self, r: Renderer, value_text: str, mouse_pos: Tuple[int, int],
             surface: Optional[pygame.Surface] = None) -> None:
        theme = r.theme
        target = r.target(surface)
        for key, label in self.labels.items():
            rect = self.rects.get(key)
            if rect is None:
                continue
            hovered = rect.collidepoint(mouse_pos)
            style = r.emphasis(hover=1.0 if hovered else 0.0, quiet=True,
                               text=theme.brass_bright if hovered else theme.btn_text)
            drawn = r.interactive_panel(rect, style, target, radius=7)
            r.fit_text(label, drawn, style.text, target,
                       base_size=self.LABEL_SIZE, padding=6)
        value_rect = self.rects["value"]
        r.inset_well(value_rect, target, radius=7)
        r.fit_text(value_text, value_rect, theme.text_light, target,
                   base_size=self.LABEL_SIZE, padding=6)


class Checkbox(Widget):
    def __init__(self, rect: pygame.Rect, checked: bool = False) -> None:
        super().__init__(rect)
        self.checked = checked

    def toggle(self) -> bool:
        self.checked = not self.checked
        return self.checked

    def draw(self, r: Renderer, surface: Optional[pygame.Surface] = None) -> None:
        theme = r.theme
        target = r.target(surface)
        if self.hovered and self.enabled:
            # The same rim of light every other control uses, sized to the box.
            r.shape_glow(self.rect, theme.accent, target, radius=5, strength=0.3)
        r.inset_well(self.rect, target, radius=5,
                     border=theme.panel_edge if self.hovered else None)
        if self.checked:
            pygame.draw.line(target, theme.accent,
                             (self.rect.left + 4, self.rect.centery),
                             (self.rect.centerx - 1, self.rect.bottom - 4), 3)
            pygame.draw.line(target, theme.accent,
                             (self.rect.centerx - 1, self.rect.bottom - 4),
                             (self.rect.right - 4, self.rect.top + 4), 3)


class Dropdown(Widget):
    """Closed control plus an overlay list drawn on top of everything else."""

    ITEM_H = 24

    def __init__(self, rect: pygame.Rect, options: Sequence[str],
                 value: Optional[str] = None) -> None:
        super().__init__(rect)
        self.options = list(options)
        self.value = value
        self.open = False
        self.disabled_options: set[str] = set()
        self.max_bottom = 10_000

    def list_rect(self) -> pygame.Rect:
        h = len(self.options) * self.ITEM_H
        below = pygame.Rect(self.rect.x, self.rect.bottom + 2, self.rect.width, h)
        if below.bottom > self.max_bottom:
            return pygame.Rect(self.rect.x, self.rect.top - h - 2, self.rect.width, h)
        return below

    def option_at(self, pos: Tuple[int, int]) -> Optional[str]:
        rect = self.list_rect()
        if not rect.collidepoint(pos):
            return None
        index = (pos[1] - rect.y) // self.ITEM_H
        if 0 <= index < len(self.options):
            option = self.options[index]
            return None if option in self.disabled_options else option
        return None

    def draw(self, r: Renderer, surface: Optional[pygame.Surface] = None,
             placeholder: str = "\u2014") -> None:
        theme = r.theme
        target = r.target(surface)
        font = r.fonts.get(15)
        if not self.enabled:
            r.panel(self.rect, theme.btn_disabled_bg, theme.panel_line, radius=7,
                    border_width=1, surface=target)
            r.text(placeholder, font, theme.text_dim, target,
                   midleft=(self.rect.left + 8, self.rect.centery))
            return
        active = self.hovered or self.open
        style = r.emphasis(hover=1.0 if active else 0.0, quiet=True,
                           text=theme.text_light)
        drawn = r.interactive_panel(self.rect, style, target, radius=7)
        r.text(self.value or placeholder, font, style.text, target,
               midleft=(drawn.left + 8, drawn.centery))
        r.text("\u25be", font, style.text, target,
               midright=(drawn.right - 8, drawn.centery))

    def draw_overlay(self, r: Renderer, mouse_pos: Tuple[int, int],
                     surface: Optional[pygame.Surface] = None) -> None:
        if not self.open:
            return
        theme = r.theme
        target = r.target(surface)
        font = r.fonts.get(15)
        rect = self.list_rect()
        r.premium_panel(rect, target, radius=8, fill=theme.panel_inset,
                        border=theme.panel_edge, ornaments=False, shadow=12)
        for i, option in enumerate(self.options):
            item = pygame.Rect(rect.x, rect.y + i * self.ITEM_H, rect.width, self.ITEM_H)
            disabled = option in self.disabled_options
            hovered = not disabled and item.collidepoint(mouse_pos)
            if hovered:
                # An inset row rather than a flat fill, so the open list belongs
                # to the same furniture as the control that opened it.
                inner = item.inflate(-4, -2)
                pygame.draw.rect(target, theme.btn_active_bg, inner, border_radius=5)
                pygame.draw.rect(target, theme.panel_edge, inner, 1, border_radius=5)
            color = (theme.btn_disabled_text if disabled
                     else (theme.btn_active_text if hovered else theme.text_light))
            r.text(option, font, color, target, midleft=(item.left + 10, item.centery))


class Slider(Widget):
    """Horizontal slider — the board zoom control."""

    def __init__(self, rect: pygame.Rect, value: float = 0.5) -> None:
        super().__init__(rect)
        self.value = value
        self.dragging = False

    @property
    def hit_rect(self) -> pygame.Rect:
        return self.rect.inflate(0, 18)

    def hit(self, pos: Tuple[int, int]) -> bool:
        return self.enabled and self.hit_rect.collidepoint(pos)

    def value_at(self, mouse_x: int) -> float:
        fraction = (mouse_x - self.rect.left) / max(1, self.rect.width)
        return max(0.0, min(1.0, fraction))

    def draw(self, r: Renderer, surface: Optional[pygame.Surface] = None,
             knob_color: Optional[Color] = None) -> None:
        theme = r.theme
        target = r.target(surface)
        pygame.draw.line(target, theme.panel_line, self.rect.midleft, self.rect.midright, 3)
        knob_x = int(self.rect.left + self.value * self.rect.width)
        color = knob_color or theme.mod_select_ring
        if self.hovered or self.dragging:
            r.ring_glow((knob_x, self.rect.centery), 8, color, target,
                        strength=0.9 if self.dragging else 0.6)
        r.aa_circle((knob_x, self.rect.centery), 8, color, darken(color, 0.6), 2, target)


class ScrollBar(Widget):
    """Track plus thumb, used for both board axes."""

    def __init__(self, rect: pygame.Rect, horizontal: bool = False) -> None:
        super().__init__(rect)
        self.horizontal = horizontal
        self.fraction = 0.0
        self.view_ratio = 1.0
        self.dragging = False
        self.grab_offset = 0

    @property
    def visible(self) -> bool:
        return self.view_ratio < 0.999

    def thumb_rect(self) -> pygame.Rect:
        if self.horizontal:
            w = max(28, int(self.rect.width * self.view_ratio))
            x = self.rect.left + int((self.rect.width - w) * self.fraction)
            return pygame.Rect(x, self.rect.y, w, self.rect.height)
        h = max(28, int(self.rect.height * self.view_ratio))
        y = self.rect.top + int((self.rect.height - h) * self.fraction)
        return pygame.Rect(self.rect.x, y, self.rect.width, h)

    def fraction_from_thumb(self, position: int) -> float:
        thumb = self.thumb_rect()
        if self.horizontal:
            usable = self.rect.width - thumb.width
            return 0.0 if usable <= 0 else max(0.0, min(1.0, (position - self.rect.left) / usable))
        usable = self.rect.height - thumb.height
        return 0.0 if usable <= 0 else max(0.0, min(1.0, (position - self.rect.top) / usable))

    def draw(self, r: Renderer, surface: Optional[pygame.Surface] = None) -> None:
        if not self.visible:
            return
        target = r.target(surface)
        theme = r.theme
        pygame.draw.rect(target, theme.panel_highlight, self.rect, border_radius=3)
        color = theme.prompt if self.dragging else theme.brass_light
        pygame.draw.rect(target, color, self.thumb_rect(), border_radius=3)


@dataclass
class TextField:
    """Inline editing state for renaming a player."""

    buffer: str = ""
    active: bool = False
    max_length: int = 16
    target_index: Optional[int] = None

    def start(self, index: int, initial: str = "") -> None:
        self.active = True
        self.target_index = index
        self.buffer = initial[: self.max_length]
        pygame.key.start_text_input()

    def stop(self) -> None:
        self.active = False
        self.target_index = None
        self.buffer = ""
        pygame.key.stop_text_input()

    def handle(self, event: pygame.event.Event) -> Optional[str]:
        """Feed an event.  Returns the final text when editing is confirmed."""
        if not self.active:
            return None
        if event.type == pygame.TEXTINPUT:
            if len(self.buffer) < self.max_length:
                self.buffer += event.text
            return None
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.buffer = self.buffer[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                return self.buffer.strip()
            elif event.key == pygame.K_ESCAPE:
                self.stop()
        return None

    def caret(self) -> str:
        return "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else ""


class TextInput(Widget):
    """A text box that behaves like a native one.

    Distinct from :class:`TextField`, the inline rename-a-player editor: this is
    a standing form field used by the join and host screens, and it supports
    what people expect from any desktop text box — a caret they can place, a
    selection they can drag, and Ctrl+A/C/X/V.

    Three details worth keeping in mind when touching this:

    * **Typed characters come from TEXTINPUT only.**  SDL sends *both* a KEYDOWN
      carrying ``unicode`` and a TEXTINPUT for the same keystroke, so a field
      that accepts either inserts everything twice.  TEXTINPUT is the correct
      one: it understands dead keys, compose sequences and the Polish letters
      this game is full of.
    * **Held keys repeat on the field's own timer.**  pygame's global key repeat
      would also spam every in-game shortcut, so the field watches the key
      itself.  Screens MUST call ``update()`` each frame or nothing repeats.
    * **Hit-testing needs the font**, which only the renderer has, so the font
      used to draw is remembered and reused for caret placement.
    """

    REPEAT_DELAY = 0.42
    REPEAT_INTERVAL = 0.035
    #: Keys that repeat while held.
    REPEATING = (pygame.K_BACKSPACE, pygame.K_DELETE, pygame.K_LEFT, pygame.K_RIGHT)
    PADDING = 12

    def __init__(
        self,
        rect: pygame.Rect,
        label: str = "",
        value: str = "",
        placeholder: str = "",
        max_length: int = 32,
        numeric: bool = False,
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.value = value
        self.placeholder = placeholder
        self.max_length = max_length
        self.numeric = numeric
        self.focused = False
        self.caret = len(value)
        #: Where a selection started, or ``None`` when nothing is selected.
        self.anchor: Optional[int] = None
        self._held_key: Optional[int] = None
        self._held_shift = False
        self._held_for = 0.0
        self._repeats = 0
        self._dragging = False
        self._press_index = 0
        self._font: Optional[pygame.font.Font] = None

    # ── focus ────────────────────────────────────────────────────────────────
    def focus(self, select_all: bool = False) -> None:
        if not self.focused:
            self.focused = True
            self._release()
            pygame.key.start_text_input()
        if select_all:
            self.select_all()

    def blur(self) -> None:
        if self.focused:
            self.focused = False
            self._release()
            self.anchor = None
            self._dragging = False
            pygame.key.stop_text_input()

    def _release(self) -> None:
        self._held_key = None
        self._held_for = 0.0
        self._repeats = 0

    # ── selection ────────────────────────────────────────────────────────────
    @property
    def selection(self) -> Optional[Tuple[int, int]]:
        if self.anchor is None or self.anchor == self.caret:
            return None
        return (min(self.anchor, self.caret), max(self.anchor, self.caret))

    @property
    def selected_text(self) -> str:
        span = self.selection
        return self.value[span[0]:span[1]] if span else ""

    def select_all(self) -> None:
        self.anchor = 0
        self.caret = len(self.value)

    def clear_selection(self) -> None:
        self.anchor = None

    def _delete_selection(self) -> bool:
        span = self.selection
        if span is None:
            return False
        self.value = self.value[:span[0]] + self.value[span[1]:]
        self.caret = span[0]
        self.anchor = None
        return True

    def _move_caret(self, position: int, extend: bool) -> None:
        position = max(0, min(len(self.value), position))
        if extend:
            if self.anchor is None:
                self.anchor = self.caret
        else:
            self.anchor = None
        self.caret = position

    # ── editing ──────────────────────────────────────────────────────────────
    def insert(self, text: str) -> None:
        """Type text in, replacing whatever is selected."""
        cleaned = "".join(
            c for c in text
            if c.isprintable() and (c.isdigit() or not self.numeric)
        )
        if not cleaned:
            return
        self._delete_selection()
        room = self.max_length - len(self.value)
        if room <= 0:
            return
        cleaned = cleaned[:room]
        self.value = self.value[:self.caret] + cleaned + self.value[self.caret:]
        self.caret += len(cleaned)
        self.anchor = None

    def backspace(self) -> None:
        if self._delete_selection():
            return
        if self.caret > 0:
            self.value = self.value[:self.caret - 1] + self.value[self.caret:]
            self.caret -= 1

    def delete_forward(self) -> None:
        if self._delete_selection():
            return
        if self.caret < len(self.value):
            self.value = self.value[:self.caret] + self.value[self.caret + 1:]

    def copy_selection(self) -> None:
        if self.selected_text:
            clipboard.copy(self.selected_text)

    def cut_selection(self) -> None:
        if self.selected_text:
            clipboard.copy(self.selected_text)
            self._delete_selection()

    def paste(self) -> None:
        self.insert(clipboard.paste())

    # ── events ───────────────────────────────────────────────────────────────
    def handle(self, event: pygame.event.Event) -> bool:
        """Feed an event.  Returns True when it was consumed."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.focus()
                self.caret = self.index_at(event.pos[0])
                # A click is not a selection.  Leaving a zero-length one behind
                # meant the first character typed became "selected" and the
                # second replaced it.
                self.anchor = None
                self._press_index = self.caret
                self._dragging = True
                return True
            return False
        if event.type == pygame.MOUSEMOTION and self._dragging:
            if self.anchor is None:
                self.anchor = self._press_index
            self._move_caret(self.index_at(event.pos[0]), extend=True)
            return True
        if event.type == pygame.MOUSEBUTTONUP and self._dragging:
            self._dragging = False
            if self.anchor == self.caret:
                self.anchor = None
            return True

        if not self.focused:
            return False

        if event.type == pygame.TEXTINPUT:
            self.insert(event.text)
            return True

        if event.type == pygame.KEYUP and event.key == self._held_key:
            self._release()
            return True

        if event.type != pygame.KEYDOWN:
            return False

        mods = pygame.key.get_mods()
        ctrl = bool(mods & pygame.KMOD_CTRL)
        shift = bool(mods & pygame.KMOD_SHIFT)

        if ctrl and event.key == pygame.K_a:
            self.select_all()
            return True
        if ctrl and event.key == pygame.K_c:
            self.copy_selection()
            return True
        if ctrl and event.key == pygame.K_x:
            self.cut_selection()
            return True
        if ctrl and event.key == pygame.K_v:
            self.paste()
            return True

        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            return False        # the screen decides what those mean
        if event.key == pygame.K_ESCAPE:
            self.blur()
            return True

        if event.key == pygame.K_BACKSPACE:
            self.backspace()
        elif event.key == pygame.K_DELETE:
            self.delete_forward()
        elif event.key == pygame.K_LEFT:
            self._move_caret(self.caret - 1, shift)
        elif event.key == pygame.K_RIGHT:
            self._move_caret(self.caret + 1, shift)
        elif event.key == pygame.K_HOME:
            self._move_caret(0, shift)
        elif event.key == pygame.K_END:
            self._move_caret(len(self.value), shift)
        else:
            # Everything printable arrives as TEXTINPUT; handling it here too is
            # the double-insertion bug this comment guards.
            return True

        if event.key in self.REPEATING:
            self._held_key = event.key
            self._held_shift = shift
            self._held_for = 0.0
            self._repeats = 0
        return True

    def _repeat_once(self) -> None:
        if self._held_key == pygame.K_BACKSPACE:
            self.backspace()
        elif self._held_key == pygame.K_DELETE:
            self.delete_forward()
        elif self._held_key == pygame.K_LEFT:
            self._move_caret(self.caret - 1, self._held_shift)
        elif self._held_key == pygame.K_RIGHT:
            self._move_caret(self.caret + 1, self._held_shift)

    def update(self, mouse_pos: Tuple[int, int], dt: float = 0.0) -> None:
        super().update(mouse_pos, dt)
        if not self.focused or self._held_key is None:
            return
        # Trust the keyboard rather than KEYUP alone: a focus change can eat the
        # release and leave a key repeating for ever.
        if not pygame.key.get_pressed()[self._held_key]:
            self._release()
            return
        self._held_for += dt
        if self._held_for < self.REPEAT_DELAY:
            return
        due = int((self._held_for - self.REPEAT_DELAY) / self.REPEAT_INTERVAL) + 1
        while self._repeats < due:
            self._repeats += 1
            self._repeat_once()

    # ── measuring ────────────────────────────────────────────────────────────
    def _measure(self, text: str) -> int:
        if self._font is None:
            return len(text) * 9        # a rough guess before the first frame
        return self._font.size(text)[0]

    def index_at(self, x: int) -> int:
        """Character index nearest a screen x — where a click puts the caret."""
        offset = x - (self.rect.left + self.PADDING)
        if offset <= 0:
            return 0
        for index in range(len(self.value) + 1):
            if self._measure(self.value[:index]) >= offset:
                # Snap to whichever side of the character is closer.
                before = self._measure(self.value[:max(0, index - 1)])
                after = self._measure(self.value[:index])
                return index if (offset - before) > (after - offset) else max(0, index - 1)
        return len(self.value)

    def caret_x(self) -> int:
        return self.rect.left + self.PADDING + self._measure(self.value[:self.caret])

    # ── drawing ──────────────────────────────────────────────────────────────
    def draw(self, r, surface: Optional[pygame.Surface] = None) -> None:
        theme = r.theme
        font = r.fonts.get(18)
        self._font = font
        target = r.target(surface)

        if self.label:
            r.text(self.label, r.fonts.get(15), theme.text_dim, surface,
                   bottomleft=(self.rect.left + 2, self.rect.top - 6))
        border = theme.prompt if self.focused else theme.panel_line
        if self.focused:
            # Focus is one of the five states in the shared language, so it gets
            # the same rim as a selected tile rather than a border of its own.
            r.shape_glow(self.rect, theme.prompt, target, radius=8, strength=0.4)
        elif self.hovered:
            r.shape_glow(self.rect, theme.accent, target, radius=8, strength=0.22)
            border = theme.panel_edge
        r.panel(self.rect, theme.panel_inset, border, radius=8,
                border_width=2 if self.focused else 1,
                shadow=6 if self.focused else 0, surface=surface)

        span = self.selection
        if span and self.focused:
            left = self.rect.left + self.PADDING + self._measure(self.value[:span[0]])
            width = self._measure(self.value[span[0]:span[1]])
            highlight = pygame.Rect(left, self.rect.top + 6, max(2, width),
                                    self.rect.height - 12)
            pygame.draw.rect(target, theme.accent_dim, highlight, border_radius=3)

        if self.value:
            r.text(self.value, font, theme.text_light, surface,
                   midleft=(self.rect.left + self.PADDING, self.rect.centery))
        elif self.placeholder:
            # Placeholder text is a hint, not content: dimmer than anything the
            # player could type, so the two are never confused.
            r.text(self.placeholder, font, darken(theme.text_dim, 0.72), surface,
                   midleft=(self.rect.left + self.PADDING, self.rect.centery))

        if self.focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            x = self.caret_x()
            pygame.draw.line(target, theme.prompt, (x, self.rect.top + 7),
                             (x, self.rect.bottom - 7), 2)
