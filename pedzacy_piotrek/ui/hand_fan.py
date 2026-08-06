"""
The player's hand, as a fan.

The prototype laid the hand out as a row of rectangles at fixed x-offsets.  A
row is fine for eight cards and hopeless for a game that wants to feel like a
board game, so the hand is now an arc: cards overlap, tilt with their position
on the arc, lift and straighten under the cursor, and can be dragged onto the
board to be played.

Everything here is presentation and gesture recognition.  The fan never changes
the game: it submits :class:`~pedzacy_piotrek.engine.commands.PlayCard`,
``DiscardCard`` or ``PlaceMod`` and lets the engine decide.  The legality
preview it draws while dragging comes from ``engine.effects.preview``, the same
function the engine itself calls when applying the play, so the highlight and
the outcome can never disagree.

Geometry: cards sit on a circle whose centre (``layout.hand_pivot``) is far
below the window.  Card *i* sits at angle ``(i - (n-1)/2) * step`` from the top
of that circle and is rotated by the same angle, which is what makes a fan look
like a fan rather than a row of tilted cards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pygame

from ..cards.base_card import Card
from ..config import settings
from ..config.theme import lighten
from ..engine import commands as cmd
from ..engine import effects
from ..engine.animation import approach, approach_point
from ..engine.game_state import GameState
from ..render.card_renderer import CardRenderer
from ..render.renderer import Renderer
from .layout import Layout

Point = Tuple[float, float]
Submit = Callable[[cmd.Command], None]

#: Angular step between neighbouring cards, and the widest total fan.
MAX_STEP_DEG = 6.5
MAX_FAN_DEG = 42.0
#: How far a hovered card rises out of the fan, as a fraction of card height.
HOVER_LIFT = 0.30
HOVER_SCALE = 1.16
#: Pointer travel before a press becomes a drag rather than a click.
DRAG_THRESHOLD = 8.0


@dataclass
class FanSlot:
    """Animated state of one card in the fan."""

    uid: int
    card: Card
    position: Point
    angle: float
    scale: float = 1.0
    #: Where it is heading; the drawn values chase these every frame.
    target_position: Point = (0.0, 0.0)
    target_angle: float = 0.0
    target_scale: float = 1.0
    #: 0 → resting in the fan, 1 → fully hovered.  Drives the lift.
    hover: float = 0.0
    fresh: bool = True


class HandFan:
    def __init__(
        self,
        renderer: Renderer,
        cards: CardRenderer,
        layout: Layout,
        state: GameState,
        submit: Submit,
        seat: Optional[Callable[[], int]] = None,
        can_act: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.r = renderer
        self.cards = cards
        self.layout = layout
        self.state = state
        self.submit = submit
        #: Which seat's hand is on the table.  Supplied by the screen, because
        #: "the player at this machine" and "the player whose turn it is" are
        #: different things the moment there is more than one machine.
        self.seat = seat or (lambda: state.active_player_index)
        #: Whether that hand may be played from at all.
        self.can_act = can_act or (lambda: True)

        self.slots: Dict[int, FanSlot] = {}
        self.order: List[int] = []
        self.hovered: Optional[int] = None

        # Drag state.
        self.pressed: Optional[int] = None
        self.press_origin: Point = (0.0, 0.0)
        self.dragging: Optional[int] = None
        self.drag_position: Point = (0.0, 0.0)
        self.drag_preview: Optional[object] = None

        #: Set by the screen so the fan knows where a play may be dropped.
        self.drop_zone: Optional[pygame.Rect] = None
        #: Messages for the status bar.
        self.notify: Callable[[str], None] = lambda message: None

    # ── model sync ───────────────────────────────────────────────────────────
    @property
    def owner(self):
        player = self.state.player(self.seat())
        return player if player is not None else self.state.active_player

    @property
    def hand(self) -> List[Card]:
        return self.owner.hand

    def card_by_uid(self, uid: Optional[int]) -> Optional[Card]:
        slot = self.slots.get(uid) if uid is not None else None
        return slot.card if slot is not None else None

    def _sync(self) -> None:
        """Create slots for new cards, drop slots for cards that left."""
        hand = self.hand
        live = {card.uid for card in hand}
        for uid in list(self.slots):
            if uid not in live:
                del self.slots[uid]
        self.order = [card.uid for card in hand]

        pivot = self.layout.hand_pivot
        for card in hand:
            if card.uid not in self.slots:
                # New cards fly in from the deck side of the screen.
                self.slots[card.uid] = FanSlot(
                    uid=card.uid,
                    card=card,
                    position=(float(self.layout.left_panel.centerx), float(pivot[1])),
                    angle=0.0,
                )

    # ── geometry ─────────────────────────────────────────────────────────────
    def _step_degrees(self, count: int) -> float:
        if count <= 1:
            return 0.0
        step = min(MAX_STEP_DEG, MAX_FAN_DEG / (count - 1))
        # Keep the fan inside the window even with a full hand on a small screen.
        radius = self.layout.hand_arc_radius
        half_span = math.sin(math.radians(step * (count - 1) / 2)) * radius
        limit = self.layout.hand_max_spread / 2
        if half_span > limit and half_span > 0:
            step *= limit / half_span
        return step

    def resting_transform(self, index: int, count: int) -> Tuple[Point, float]:
        """Where card ``index`` of ``count`` sits when nothing is happening."""
        pivot = self.layout.hand_pivot
        radius = self.layout.hand_arc_radius
        step = self._step_degrees(count)
        angle = (index - (count - 1) / 2.0) * step
        radians = math.radians(angle)
        position = (
            pivot[0] + math.sin(radians) * radius,
            pivot[1] - math.cos(radians) * radius,
        )
        return position, angle

    def _card_at(self, point: Point) -> Optional[int]:
        """Topmost card under the cursor.

        Tests against the card's *unrotated* rectangle after rotating the point
        into card space, so the hit area follows the tilt exactly — with a fan
        this matters, the corners overlap a lot.
        """
        card_w, card_h = self.layout.hand_card_size
        for uid in reversed(self._draw_order()):
            slot = self.slots.get(uid)
            if slot is None:
                continue
            dx = point[0] - slot.position[0]
            dy = point[1] - slot.position[1]
            radians = math.radians(slot.angle)
            cos_a, sin_a = math.cos(radians), math.sin(radians)
            local_x = dx * cos_a + dy * sin_a
            local_y = -dx * sin_a + dy * cos_a
            half_w = card_w * slot.scale / 2
            half_h = card_h * slot.scale / 2
            if abs(local_x) <= half_w and abs(local_y) <= half_h:
                return uid
        return None

    def _draw_order(self) -> List[int]:
        """Back to front.  A hovered or dragged card is always on top."""
        order = [uid for uid in self.order if uid in self.slots]
        top = self.dragging if self.dragging is not None else self.hovered
        if top is not None and top in order:
            order.remove(top)
            order.append(top)
        return order

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Point) -> bool:
        """Returns True when the fan consumed the event."""
        if not self.can_act():
            # Somebody else's hand: clicks are swallowed rather than sent and
            # refused, so a spectator never sees an error they cannot avoid.
            if event.type == pygame.MOUSEBUTTONDOWN and self._card_at(mouse):
                self.notify("To nie jest twoja ręka")
                return True
            return False
        if event.type == pygame.MOUSEBUTTONDOWN:
            uid = self._card_at(mouse)
            if uid is None:
                return False
            if event.button == 1:
                self.pressed = uid
                self.press_origin = mouse
                return True
            if event.button == 2:
                self._discard(uid)
                return True
            if event.button == 3:
                return False  # the mod-staging gesture belongs to the screen
            return False

        if event.type == pygame.MOUSEMOTION:
            if self.dragging is not None:
                self.drag_position = mouse
                self._refresh_preview()
                return True
            if self.pressed is not None:
                if math.dist(mouse, self.press_origin) >= DRAG_THRESHOLD:
                    self.dragging = self.pressed
                    self.pressed = None
                    self.drag_position = mouse
                    self._refresh_preview()
                return True
            return False

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging is not None:
                self._release(mouse)
                return True
            if self.pressed is not None:
                uid, self.pressed = self.pressed, None
                self._activate(uid)
                return True
        return False

    def cancel_drag(self) -> None:
        self.pressed = None
        self.dragging = None
        self.drag_preview = None

    def _refresh_preview(self) -> None:
        card = self.card_by_uid(self.dragging)
        self.drag_preview = (
            effects.preview(self.state, card, self.seat())
            if card is not None else None
        )

    def _over_board(self, point: Point) -> bool:
        return self.drop_zone is not None and self.drop_zone.collidepoint(point)

    def _release(self, mouse: Point) -> None:
        uid, self.dragging = self.dragging, None
        card = self.card_by_uid(uid)
        preview, self.drag_preview = self.drag_preview, None
        if card is None:
            return
        if not self._over_board(mouse):
            return  # the slot animates home on its own
        self._play(card, preview)

    def _activate(self, uid: int) -> None:
        """A plain click: play the card if it can be played, else discard it.

        Cards with no effect keep the prototype's behaviour — click discards —
        so nothing that used to work stopped working.  A LOCKED card is the
        exception and is neither: Troll booked a turn the moment it was drawn,
        and discarding it would be a free way out of it.  The engine refuses
        both anyway; this is so the player gets an explanation instead of a
        rejection.
        """
        card = self.card_by_uid(uid)
        if card is None:
            return
        if card.locked:
            self.notify(f"„{card.title}” zadziała sama na początku twojej tury")
            return
        if card.is_playable:
            self._play(card, effects.preview(self.state, card, self.seat()))
        else:
            self._discard(uid)

    def _play(self, card: Card, preview: Optional[object]) -> None:
        if card.locked:
            self.notify(f"„{card.title}” zagra się sama, kiedy przyjdzie twoja tura")
            return
        if preview is None:
            preview = effects.preview(self.state, card, self.seat())
        # A pending decision is not a refusal: submitting makes the engine ask,
        # and the screen takes over from the resulting ChoiceRequired event.
        pending = isinstance(preview, effects.Choice)
        if not getattr(preview, "ok", False) and not pending:
            self.notify(getattr(preview, "reason", "Nie można zagrać tej karty"))
            return
        self.submit(
            cmd.PlayCard(player_index=self.seat(), card_uid=card.uid)
        )

    def _discard(self, uid: int) -> None:
        card = self.card_by_uid(uid)
        if card is not None and card.locked:
            self.notify(f"„{card.title}” zostaje na ręce")
            return
        self.submit(
            cmd.DiscardCard(player_index=self.seat(), card_uid=uid)
        )

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse: Point) -> None:
        self._sync()

        in_hand_area = self.layout.hand_area.collidepoint(mouse)
        if self.dragging is not None:
            self.hovered = None
        elif in_hand_area or self.pressed is not None:
            self.hovered = self._card_at(mouse)
        else:
            self.hovered = None

        count = len(self.order)
        card_w, card_h = self.layout.hand_card_size

        for index, uid in enumerate(self.order):
            slot = self.slots.get(uid)
            if slot is None:
                continue
            rest_position, rest_angle = self.resting_transform(index, count)

            if uid == self.dragging:
                slot.target_position = self.drag_position
                slot.target_angle = 0.0
                slot.target_scale = 1.05
                slot.hover = 1.0
            else:
                hovered = uid == self.hovered
                slot.hover = approach(slot.hover, 1.0 if hovered else 0.0, 16.0, dt)
                lift = slot.hover * card_h * HOVER_LIFT
                slot.target_position = (rest_position[0], rest_position[1] - lift)
                # Hovered cards straighten so their text can be read.
                slot.target_angle = rest_angle * (1.0 - slot.hover * 0.85)
                slot.target_scale = 1.0 + slot.hover * (HOVER_SCALE - 1.0)

            if slot.fresh:
                slot.fresh = False
                slot.angle = slot.target_angle
                slot.scale = 0.7
            speed = 26.0 if uid == self.dragging else 15.0
            slot.position = approach_point(slot.position, slot.target_position, speed, dt)
            slot.angle = approach(slot.angle, slot.target_angle, speed, dt)
            slot.scale = approach(slot.scale, slot.target_scale, speed, dt)

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.r.theme
        layout = self.layout
        card_size = layout.hand_card_size
        player = self.owner

        area = layout.hand_area
        # The hand rests on a shelf that fades into the table rather than a
        # bordered box: the cards are the subject, the shelf is furniture.
        shelf = pygame.Rect(area.left - 20, area.top + 2, area.width + 40,
                            area.height + 20)
        self.r.premium_panel(shelf, surface, radius=18, ornaments=False, shadow=16)

        if self.can_act():
            label = (
                f"{player.name}  \u2014  ręka {len(player.hand)}/{settings.RULES.max_hand}"
                "   ·   L-click zagraj lub odrzuć  ·  przeciągnij na planszę  ·  "
                "P-click do modów  ·  środkowy odrzuca"
            )
        else:
            label = f"{player.name}  \u2014  podgląd cudzej ręki (nie sterujesz)"
        self.r.spaced_text(label.upper(), self.r.fonts.get(
            max(10, int(11 * layout.ui_scale)), bold=True),
            theme.text_dim, surface, center=(area.centerx, area.top + 13), spacing=1)

        if not player.hand:
            self.r.text("Brak kart — kliknij talię po lewej, aby dobrać",
                        self.r.fonts.deck(), theme.text_dim, surface,
                        center=(area.centerx, area.centery))
            return

        for uid in self._draw_order():
            slot = self.slots.get(uid)
            if slot is None:
                continue
            colour = theme.deck_colors.get(slot.card.deck_id)
            playable = slot.card.is_playable
            highlighted = uid in (self.hovered, self.dragging)
            border = colour
            if uid == self.dragging:
                preview = self.drag_preview
                ok = getattr(preview, "ok", False)
                pending = isinstance(preview, effects.Choice)
                over = self._over_board(self.drag_position)
                if not over:
                    border = colour
                elif ok:
                    border = theme.valid
                elif pending:
                    border = theme.prompt      # legal, but a decision is missing
                else:
                    border = theme.invalid
            if highlighted:
                # A warm rim around the card being considered, which is what
                # makes a hovered card feel picked up rather than just bigger.
                # It used to be a circle centred on the card and wider than the
                # card is tall, so a hovered card lit up its neighbours too.
                glow_w = int(card_size[0] * slot.scale)
                glow_h = int(card_size[1] * slot.scale)
                halo = pygame.Rect(0, 0, glow_w, glow_h)
                halo.center = (int(slot.position[0]), int(slot.position[1]))
                self.r.shape_glow(halo, border or theme.brass_light, surface,
                                  radius=10, strength=0.45 + 0.35 * slot.hover)
            self.cards.draw_transformed(
                slot.card, slot.position, -slot.angle, surface,
                size=card_size, scale=slot.scale,
                highlighted=highlighted, border_color=border,
                shadow=16 if highlighted else 7,
            )
            if slot.card.locked:
                self._locked_marker(surface, slot, card_size)
            elif playable and uid != self.dragging:
                self._playable_marker(surface, slot, card_size)

        if self.dragging is not None:
            self._draw_drag_hint(surface)

    def _playable_marker(self, surface: pygame.Surface, slot: FanSlot,
                         card_size: Tuple[int, int]) -> None:
        """A small green pip on cards the engine can actually resolve."""
        radians = math.radians(slot.angle)
        offset = card_size[1] * slot.scale * 0.5 - 10
        centre = (
            slot.position[0] + math.sin(radians) * offset,
            slot.position[1] - math.cos(radians) * offset,
        )
        self.r.aa_circle(centre, 5, self.r.theme.valid,
                         lighten(self.r.theme.valid, 0.6), 1, surface)

    def _locked_marker(self, surface: pygame.Surface, slot: FanSlot,
                       card_size: Tuple[int, int]) -> None:
        """A warning pip on a card the player does not control.

        Same geometry as the playable pip and a different colour, because the
        two answer the same question — "can I click this?" — and putting them
        anywhere near each other in different places would make both harder to
        read.
        """
        radians = math.radians(slot.angle)
        offset = card_size[1] * slot.scale * 0.5 - 10
        centre = (
            slot.position[0] + math.sin(radians) * offset,
            slot.position[1] - math.cos(radians) * offset,
        )
        theme = self.r.theme
        self.r.aa_circle(centre, 5, theme.prompt, lighten(theme.prompt, 0.6), 1,
                         surface)

    def _draw_drag_hint(self, surface: pygame.Surface) -> None:
        preview = self.drag_preview
        theme = self.r.theme
        if preview is None:
            return
        ok = getattr(preview, "ok", False)
        pending = isinstance(preview, effects.Choice)
        if ok or pending:
            text = getattr(preview, "description", "")
        else:
            text = getattr(preview, "reason", "")
        if pending:
            text = f"{text}  ·  wybierzesz pole po upuszczeniu"
        if not text:
            return
        colour = theme.valid if ok else (theme.prompt if pending else theme.invalid)
        if not self._over_board(self.drag_position):
            text = f"{text}  ·  upuść na planszy"
            colour = theme.text_dim
        position = (self.drag_position[0], self.drag_position[1] +
                    self.layout.hand_card_size[1] * 0.62)
        self.r.text(text, self.r.fonts.deck(), colour, surface,
                    center=position, shadow=True)
