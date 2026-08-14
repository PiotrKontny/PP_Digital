"""
The board viewport.

Owns the camera, the animated pawns and every board-side gesture: dragging a
pawn, panning, zooming, and receiving a card dropped from the hand.  It talks
to the rest of the game in one direction only — it *submits commands* — and
listens to events to animate what actually happened.

Three things arrived in this stage:

**Pawns walk.**  A ``TokenWalked`` event carries every field the pawn passes
through, and the view follows that list one segment at a time with a small hop
per field, so a three-space move reads as three steps rather than a slide.
Riders on top of the pawn set off a beat later, which makes a moving tower look
like a tower.

**Stacks open up.**  Picking the third pawn out of a tower used to mean hitting
a 13-pixel sliver.  Hovering a stack now fans it into a ring around its field,
so every pawn in it gets its own generous target; the ring closes again when
the cursor leaves.  The pick radius also grows as you zoom out, so a pawn is
about as easy to grab at 0.4× as at 1.2×.

**Drops from the hand.**  While a card is dragged over the board, the fields it
would move a pawn through are highlighted, and the destination gets a ring.
The highlight comes from the same ``engine.effects`` preview the engine uses to
apply the play, so it cannot promise something different from what happens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pygame

from ..board.tiles import Tile
from ..config import settings
from ..config.theme import darken
from ..engine import commands as cmd
from ..engine import events as ev
from ..engine.animation import Animator, Tween, approach, ease_out_cubic
from ..engine.game_state import GameState
from ..engine.statuses import StatusKind
from ..render.board_renderer import BoardRenderer
from ..render.camera import Camera
from ..render.particles import ParticleSystem
from ..render.renderer import Renderer
from .layout import Layout
from .widgets import ScrollBar, Slider

Point = Tuple[float, float]
Submit = Callable[[cmd.Command], None]

#: World-space grab radius at zoom 1, and the floor it never drops below on
#: screen — this is what makes pawns comfortable to pick up when zoomed out.
PICK_RADIUS_WORLD = 26.0
PICK_RADIUS_SCREEN = 22.0
#: Seconds a pawn spends crossing one field.
STEP_DURATION = 0.22
#: How far a stack fans out when hovered, relative to the field radius.
SPREAD_FACTOR = 1.45


@dataclass
class Walk:
    """A pawn travelling along the road, field by field."""

    points: List[Point]
    index: int = 0
    progress: float = 0.0
    delay: float = 0.0
    hop: float = 10.0
    on_step: Optional[Callable[[Point], None]] = None

    @property
    def finished(self) -> bool:
        return self.index >= len(self.points) - 1 and self.progress >= 1.0


class BoardView:
    def __init__(
        self,
        renderer: Renderer,
        layout: Layout,
        state: GameState,
        submit: Submit,
        bus: ev.EventBus,
    ) -> None:
        self.r = renderer
        self.layout = layout
        self.state = state
        self.submit = submit
        self.bus = bus

        self.board_renderer = BoardRenderer(renderer, state.board)
        self.camera = Camera(
            viewport=layout.board_viewport,
            world_width=state.board.width,
            world_height=state.board.height,
        )
        self.particles = ParticleSystem()
        self.animator = Animator()

        self.visual: Dict[str, Point] = {
            pawn_id: token.position for pawn_id, token in state.tokens.items()
        }
        self.walks: Dict[str, Walk] = {}
        self.dragging: Optional[str] = None
        self.drag_offset: Point = (0.0, 0.0)
        self.panning = False
        self.hover_tile: Optional[Tile] = None
        self.snap_tile: Optional[Tile] = None

        #: Tile whose stack is fanned out, and how far open it is (0..1).
        self.expanded_tile: Optional[int] = None
        self.expansion = 0.0
        #: Fields a dragged card would move a pawn through, set by the screen.
        self.preview_route: List[int] = []
        self.preview_valid = True
        #: Fields the player is being asked to choose between (12a / 12b).
        self.choice_tiles: List[int] = []
        #: Pawns the player is being asked to choose between.
        self.choice_pawns: List[str] = []
        #: Pawns already picked, in the order they were picked.  Drawn with the
        #: number that says when each one moves (Plagiat!).
        self.choice_selected: List[str] = []
        #: Seconds to hold every NEW walk back before it starts.  Set by the
        #: screen while a card the player did not choose is being held up, so
        #: what they watch is: card, pause, movement.  The state has already
        #: changed — this delays the picture, never the rules (N36).
        self.walk_delay = 0.0
        self._choice_pulse = 0.0
        #: True while the left button is dragging the map itself.
        self.map_dragging = False

        self.zoom_slider = Slider(layout.zoom_slider, self.camera.zoom_fraction)
        self.v_scroll = ScrollBar(pygame.Rect(0, 0, 7, 10))
        self.h_scroll = ScrollBar(pygame.Rect(0, 0, 10, 7), horizontal=True)
        self._sync_geometry()

        bus.subscribe(ev.TokenMoved, self._on_token_moved)
        bus.subscribe(ev.TokenWalked, self._on_token_walked)
        bus.subscribe(ev.TileRestacked, self._on_tile_restacked)
        bus.subscribe(ev.TowerGroupPlaced, self._on_tower_group_placed)
        bus.subscribe(ev.MoveUndone, self._on_move_undone)

    def build(self) -> None:
        """Paint the static terrain.  Requires a live display surface."""
        self.board_renderer.build()
        self.camera.set_viewport(self.layout.board_viewport)
        self.camera.move_to(self.state.board.camp_position(0), immediate=True)
        self.camera.clamp()
        self.camera.snap()
        self.zoom_slider.value = self.camera.zoom_fraction

    # ── responsive geometry ──────────────────────────────────────────────────
    def _sync_geometry(self) -> None:
        """Re-place the camera and the board's own widgets after a resize."""
        viewport = self.layout.board_viewport
        if self.camera.viewport != viewport:
            self.camera.set_viewport(viewport)
        self.zoom_slider.rect = self.layout.zoom_slider
        self.v_scroll.rect = pygame.Rect(
            viewport.right - 12, viewport.top + 6, 7, viewport.height - 12
        )
        self.h_scroll.rect = pygame.Rect(
            viewport.left + 6, viewport.bottom - 12, viewport.width - 12, 7
        )

    # ── event reactions ──────────────────────────────────────────────────────
    def forget_pawn(self, pawn_id: str) -> None:
        """Drop a pawn's animation state and snap it to where it now is.

        Shady moves a pawn without walking it: it vanishes, and it comes back
        somewhere it has never been.  Without this the view would still be
        holding the old position and would slide the pawn the length of the
        board when it reappeared — a walk the engine never asked for and the
        rules do not describe.
        """
        self.walks.pop(pawn_id, None)
        if self.dragging == pawn_id:
            self.dragging = None
        token = self.state.tokens.get(pawn_id)
        if token is not None:
            self.visual[pawn_id] = token.position

    def _on_token_moved(self, event: ev.TokenMoved) -> None:
        """Glide a dragged pawn (and anything riding it) to its new home."""
        movers = [event.pawn_id, *event.carried]
        for i, pawn_id in enumerate(movers):
            token = self.state.tokens.get(pawn_id)
            if token is None:
                continue
            start = self.visual.get(pawn_id, token.position)
            end = token.position
            distance = math.dist(start, end)
            if distance < 1.0:
                self.visual[pawn_id] = end
                continue

            def updater(value, pid=pawn_id):
                self.visual[pid] = value

            self.animator.add(
                f"token:{pawn_id}",
                Tween(
                    start=start, end=end,
                    duration=min(0.85, 0.18 + distance / 1400.0),
                    delay=i * 0.05, easing=ease_out_cubic, on_update=updater,
                    on_complete=(
                        lambda pos=end, token=token: self.particles.dust(pos, token.color)
                    ) if event.snapped else None,
                ),
            )

    def _on_token_walked(self, event: ev.TokenWalked) -> None:
        """Walk a pawn through every field of its route."""
        for i, pawn_id in enumerate([event.pawn_id, *event.carried]):
            token = self.state.tokens.get(pawn_id)
            if token is None:
                continue
            start = self.visual.get(pawn_id, token.position)
            # Riders end up stacked above the leader, so each walker aims at its
            # own final position but shares the intermediate waypoints.
            points = [start, *event.waypoints[:-1], token.position] if event.waypoints \
                else [start, token.position]
            self.animator.cancel(f"token:{pawn_id}")
            colour = token.color
            self.walks[pawn_id] = Walk(
                points=points,
                delay=self.walk_delay + i * 0.09,
                hop=12.0 if not event.backward else 7.0,
                on_step=lambda position, c=colour: self.particles.dust(position, c),
            )

    def resync(self) -> None:
        """Rebuild every drawn position from the ENGINE, discarding animation.

        THE GENERAL ANSWER to a state change the board did not watch happen.
        Undo is the first caller and the reason this exists, but the shape of
        the bug is older than undo: ``self.visual`` is the board's own copy of
        where each pawn is drawn, and only the movement reactions ever wrote
        it.  Anything that rearranges the board WITHOUT walking a pawn — a
        rewind, a restack, a scatter — leaves the engine right and the screen
        showing the move that is no longer in the game.

        So this does not animate and does not interpolate: it SNAPS, because a
        rewind is not a journey anybody made and gliding pawns backwards would
        be inventing a movement the game does not contain.  In-flight walks and
        tweens are cancelled first — a tween that survives would carry on
        writing the old destination into ``visual`` after this had corrected
        it, which is the same bug arriving a frame later.

        Everything else drawn from the state — towers, the x2 badges, the
        highlights, the counters — is derived per frame and needs nothing here.
        What is CACHED is exactly what is reset.
        """
        self.animator.clear()
        self.walks.clear()
        self.dragging = None
        self.preview_route = []
        self.expanded_tile = None
        self.expansion = 0.0
        self.visual = {
            pawn_id: token.position for pawn_id, token in self.state.tokens.items()
        }

    def _on_move_undone(self, event: ev.MoveUndone) -> None:
        """A turn was rewound; redraw the board from what is actually there.

        Deliberately NOT "move the pawn back": the checkpoint may have restored
        several pawns, a tower's order, or nothing visible at all, and the view
        has no way to know which.  Asking the engine for every position is the
        only answer that is right for every undoable action rather than for the
        one that happened to be tested.
        """
        self.resync()

    def _on_tile_restacked(self, event: ev.TileRestacked) -> None:
        """Settle a tower that has been stood up in a new order.

        WHY THIS EXISTS.  ``self.visual`` is the board's own copy of where each
        pawn is DRAWN, and it is only ever written by the two movement
        reactions above.  A restack changes a pawn's height without moving it
        between fields, so no walk and no glide is emitted and nothing was
        writing the new heights — the engine had the tower right and the screen
        went on showing the old order until some later card happened to touch
        those pawns and drag the view back into step.

        So this is the missing propagation, not a redraw hack: the authoritative
        stack is already correct and untouched, and this hands the same numbers
        the engine computed to the layer that draws them.  Reusing the tween
        the drag path uses, so a reorder reads as pawns shuffling in place
        rather than teleporting.
        """
        self._settle_pawns(event.order)

    def _on_tower_group_placed(self, event: ev.TowerGroupPlaced) -> None:
        """Settle one group of a broken tower onto its new field.

        THE GHOST TOWER.  The breakup moves pawns between FIELDS, but it does
        it by placing them directly rather than by walking them, so no
        ``TokenWalked`` and no ``TokenMoved`` was ever emitted — and those two
        were the only things that wrote ``self.visual``.  The engine had every
        pawn on its new field, the "x2" badges (which count the authoritative
        stack) moved immediately, and the pawns themselves stayed drawn in a
        tower on the old field until some later card happened to touch them.
        Screenshot 2 is exactly that: three badges in the right places and six
        pawns still stacked in the wrong one.

        So the fix is the missing propagation, not a redraw hack and not
        hiding the old tower: the authoritative positions are already correct
        and are left untouched, and this hands the same numbers to the layer
        that draws them.  Same tween as the restack, so a group slides to its
        field instead of teleporting.
        """
        self._settle_pawns(event.pawns)

    def _settle_pawns(self, pawns) -> None:
        """Redraw these pawns wherever the ENGINE currently says they are."""
        for index, pawn_id in enumerate(pawns):
            token = self.state.tokens.get(pawn_id)
            if token is None:
                continue
            start = self.visual.get(pawn_id, token.position)
            end = token.position
            self.walks.pop(pawn_id, None)
            self.animator.cancel(f"token:{pawn_id}")
            if math.dist(start, end) < 1.0:
                self.visual[pawn_id] = end
                continue

            def updater(value, pid=pawn_id):
                self.visual[pid] = value

            self.animator.add(
                f"token:{pawn_id}",
                Tween(start=start, end=end, duration=0.28,
                      delay=index * 0.04, easing=ease_out_cubic,
                      on_update=updater),
            )

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse_pos: Tuple[int, int]) -> bool:
        """Returns True when the event was consumed by the board."""
        viewport = self.layout.board_viewport

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.zoom_slider.hit(mouse_pos):
                self.zoom_slider.dragging = True
                self.zoom_slider.value = self.zoom_slider.value_at(mouse_pos[0])
                self.camera.set_zoom_fraction(self.zoom_slider.value)
                return True
        if event.type == pygame.MOUSEMOTION and self.zoom_slider.dragging:
            self.zoom_slider.value = self.zoom_slider.value_at(mouse_pos[0])
            self.camera.set_zoom_fraction(self.zoom_slider.value)
            return True
        if event.type == pygame.MOUSEBUTTONUP and self.zoom_slider.dragging:
            self.zoom_slider.dragging = False
            return True

        # Scrollbars.
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for bar, axis in ((self.v_scroll, "y"), (self.h_scroll, "x")):
                if not bar.visible:
                    continue
                thumb = bar.thumb_rect()
                if thumb.collidepoint(mouse_pos):
                    bar.dragging = True
                    bar.grab_offset = (
                        mouse_pos[1] - thumb.top if axis == "y" else mouse_pos[0] - thumb.left
                    )
                    return True
                if bar.rect.collidepoint(mouse_pos):
                    bar.dragging = True
                    bar.grab_offset = (thumb.height if axis == "y" else thumb.width) // 2
                    self._drag_scrollbar(bar, axis, mouse_pos)
                    return True
        if event.type == pygame.MOUSEMOTION:
            if self.v_scroll.dragging:
                self._drag_scrollbar(self.v_scroll, "y", mouse_pos)
                return True
            if self.h_scroll.dragging:
                self._drag_scrollbar(self.h_scroll, "x", mouse_pos)
                return True
        if event.type == pygame.MOUSEBUTTONUP:
            if self.v_scroll.dragging or self.h_scroll.dragging:
                self.v_scroll.dragging = self.h_scroll.dragging = False
                return True

        inside = viewport.collidepoint(mouse_pos)

        # Wheel: zoom with Ctrl (anchored on the cursor), otherwise scroll.
        if event.type == pygame.MOUSEWHEEL and inside:
            mods = pygame.key.get_mods()
            if mods & pygame.KMOD_CTRL:
                self.camera.zoom_by(1.12 ** event.y, anchor=mouse_pos)
                self.zoom_slider.value = self.camera.zoom_fraction
            elif mods & pygame.KMOD_SHIFT:
                self.camera.pan_screen(event.y * settings.SCROLL_STEP, 0)
            else:
                self.camera.pan_screen(
                    -event.x * settings.SCROLL_STEP, event.y * settings.SCROLL_STEP
                )
            return True

        # Middle-drag pans the map.
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 2 and inside:
            self.panning = True
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 2:
            self.panning = False
            return True
        if event.type == pygame.MOUSEMOTION and self.panning:
            self.camera.pan_screen(*event.rel)
            return True

        # Pawns, or — on empty ground — the map itself.
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and inside:
            pawn_id = self.token_at(mouse_pos)
            if pawn_id is not None:
                world = self.camera.screen_to_world(mouse_pos)
                visual = self.display_position(pawn_id)
                self.dragging = pawn_id
                self.drag_offset = (visual[0] - world[0], visual[1] - world[1])
                self.animator.cancel(f"token:{pawn_id}")
                self.walks.pop(pawn_id, None)
                self.submit(cmd.PickUpToken(pawn_id=pawn_id))
                return True
            # Nothing under the cursor: grab the map. Pawn dragging wins the
            # gesture, which is why this comes second and not first.
            self.map_dragging = True
            return True

        if event.type == pygame.MOUSEMOTION and self.map_dragging:
            self.camera.pan_screen(*event.rel, immediate=True)
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.map_dragging:
            self.map_dragging = False
            return True

        if event.type == pygame.MOUSEMOTION and self.dragging is not None:
            world = self.camera.screen_to_world(mouse_pos)
            position = (world[0] + self.drag_offset[0], world[1] + self.drag_offset[1])
            self.visual[self.dragging] = position
            self.snap_tile = (
                self.state.board.tile_near(position)
                if settings.SNAP_TOKENS_TO_TILES
                else None
            )
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging is not None:
            pawn_id = self.dragging
            position = self.visual.get(pawn_id, (0.0, 0.0))
            tile = self.snap_tile
            self.dragging = None
            self.snap_tile = None
            self.submit(
                cmd.MoveToken(
                    pawn_id=pawn_id, x=position[0], y=position[1],
                    tile_index=tile.index if tile is not None else None,
                )
            )
            return True

        return False

    def _drag_scrollbar(self, bar: ScrollBar, axis: str, mouse_pos: Tuple[int, int]) -> None:
        position = (
            mouse_pos[1] - bar.grab_offset if axis == "y" else mouse_pos[0] - bar.grab_offset
        )
        fraction = bar.fraction_from_thumb(position)
        if axis == "y":
            self.camera.set_scroll_fraction(fy=fraction)
        else:
            self.camera.set_scroll_fraction(fx=fraction)

    # ── the 12a / 12b choice ─────────────────────────────────────────────────
    def choice_tile_at(self, mouse_pos: Tuple[int, int]) -> Optional[int]:
        """Which offered field the cursor is over, if any.

        The radius is deliberately generous — this is a modal decision, so
        clicking near the right field should count.
        """
        if not self.choice_tiles:
            return None
        world = self.camera.screen_to_world(mouse_pos)
        radius = max(
            self.state.board.layout.tile_radius * 1.6,
            48.0 / max(0.05, self.camera.zoom),
        )
        best: Optional[int] = None
        best_d = radius
        for index in self.choice_tiles:
            tile = self.state.board.tile(index)
            if tile is None:
                continue
            d = math.dist(world, tile.position)
            if d <= best_d:
                best, best_d = index, d
        return best

    def focus_on_tiles(self, tile_indices: Sequence[int]) -> None:
        """Pan so the offered fields are comfortably in view."""
        tiles = [self.state.board.tile(i) for i in tile_indices]
        points = [t.position for t in tiles if t is not None]
        if not points:
            return
        centre = (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )
        if not self.camera.viewport.collidepoint(self.camera.world_to_screen(centre)):
            self.camera.move_to(centre)

    def _draw_choice(self, surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        theme = self.r.theme
        hovered = self.choice_tile_at(mouse)
        pulse = 0.5 + 0.5 * math.sin(self._choice_pulse * 3.4)
        for index in self.choice_tiles:
            tile = self.state.board.tile(index)
            if tile is None:
                continue
            centre = self.camera.world_to_screen(tile.position)
            radius = int(self.state.board.layout.tile_radius * self.camera.zoom)
            is_hovered = index == hovered
            colour = theme.prompt_bright if is_hovered else theme.prompt
            self.r.ring_glow(centre, radius, colour, surface,
                             strength=0.7 + 0.3 * pulse)
            self.r.aa_ring(centre, radius + 4 + int(3 * pulse), colour,
                           4 if is_hovered else 3, surface)

            label = tile.label
            font = self.r.fonts.get(max(13, int(20 * min(1.4, self.camera.zoom))), bold=True)
            text = self.r.text_surface(label, font, darken(theme.panel_inset, 1.0))
            box = text.get_rect(midbottom=(centre[0], centre[1] - radius - 10))
            backdrop = box.inflate(16, 10)
            self.r.panel(backdrop, colour, darken(colour, 0.35), radius=8, border_width=2,
                         shadow=6, surface=surface)
            surface.blit(text, box.topleft)

    def _draw_pawn_choice(self, surface: pygame.Surface) -> None:
        """Ring the pawns the engine is asking the player to pick between."""
        theme = self.r.theme
        pulse = 0.5 + 0.5 * math.sin(self._choice_pulse * 3.4)
        for pawn_id in self.choice_pawns:
            token = self.state.tokens.get(pawn_id)
            if token is None:
                continue
            centre = self.camera.world_to_screen(self.display_position(pawn_id))
            radius = int(18 * self.camera.zoom) + 6
            order = (self.choice_selected.index(pawn_id) + 1
                     if pawn_id in self.choice_selected else None)
            if order is not None:
                # A picked pawn is ringed brighter and carries the number that
                # says when it moves, because with two of them the order is the
                # decision and not a detail of it.
                self.r.ring_glow(centre, radius, theme.valid, surface,
                                 strength=0.85 + 0.15 * pulse)
                self.r.aa_ring(centre, radius + int(3 * pulse), theme.valid, 4,
                               surface)
                self._draw_pick_number(surface, centre, radius, order)
                continue
            self.r.ring_glow(centre, radius, token.color, surface,
                             strength=0.6 + 0.4 * pulse)
            self.r.aa_ring(centre, radius + int(3 * pulse), theme.prompt_bright, 3, surface)

    def _draw_pick_number(self, surface: pygame.Surface, centre: Tuple[int, int],
                          radius: int, order: int) -> None:
        """The badge on a pawn saying it is the nth to move."""
        theme = self.r.theme
        size = max(9, int(radius * 0.55))
        spot = (centre[0] + radius - size // 2, centre[1] - radius + size // 2)
        self.r.aa_circle(spot, size, theme.valid, darken(theme.valid, 0.55), 2,
                         surface)
        self.r.text(str(order), self.r.fonts.get(max(11, size + 1), bold=True),
                    theme.ink, surface, center=spot)

    def frozen_tiles(self) -> List[int]:
        """Fields holding a pawn that is frozen, computed fresh every frame.

        DERIVED, NEVER STORED.  The highlight is a view of the status and
        nothing else, so every way a freeze can end — it expires, the pawn is
        dragged by hand, Obóz Harcerski takes the pawn off the map, Sesja na PG
        variant 2 cancels it — removes the highlight without any of them
        knowing the board is drawing one.  A cached set would need a line in
        each of those places, and the one that got forgotten would leave a blue
        field with nothing frozen on it.
        """
        tiles: List[int] = []
        for status in self.state.statuses.of_kind(StatusKind.FROZEN):
            tile = self.state.board.pawn_tile(status.subject_id)
            if tile is not None and tile.index not in tiles:
                tiles.append(tile.index)
        return tiles

    def _draw_frozen_fields(self, surface: pygame.Surface) -> None:
        """The blue field under a frozen pawn.

        Drawn with the same ``draw_tile_highlight`` the previews and the snap
        ring use, so it sits in the board's own highlight layer rather than in
        a second rendering system invented for one ability.
        """
        theme = self.r.theme
        for index in self.frozen_tiles():
            tile = self.state.board.tile(index)
            if tile is None:
                continue
            self.board_renderer.draw_tile_highlight(
                surface, self.camera, tile, theme.frost, strength=0.85
            )

    def _draw_status_marks(self, surface: pygame.Surface) -> None:
        """Small badges on pawns carrying a gameplay state.

        Frozen pawns get a pale ring, linked pawns a line drawn between them —
        the states are invisible otherwise, and a player who cannot see why a
        card was refused will think the game is broken.
        """
        theme = self.r.theme
        statuses = self.state.statuses
        for status in statuses.of_kind(StatusKind.LINKED):
            members = [str(m) for m in status.data.get("members", [])]
            points = [
                self.camera.world_to_screen(self.display_position(pawn))
                for pawn in members if pawn in self.state.tokens
            ]
            if len(points) == 2:
                self.r.aa_line(points[0], points[1], theme.link_line, 3, surface)
                middle = ((points[0][0] + points[1][0]) // 2,
                          (points[0][1] + points[1][1]) // 2)
                self.r.aa_circle(middle, 5, theme.link_line, darken(theme.link_line, 0.3), 1, surface)

        for status in statuses.of_kind(StatusKind.FROZEN):
            pawn_id = status.subject_id
            if pawn_id not in self.state.tokens:
                continue
            centre = self.camera.world_to_screen(self.display_position(pawn_id))
            radius = int(18 * self.camera.zoom) + 5
            self.r.aa_ring(centre, radius, theme.frost, 3, surface)
            self.r.aa_ring(centre, radius + 4, theme.frost_dim, 1, surface)

    # ── stacks ───────────────────────────────────────────────────────────────
    def _spread_offset(self, pawn_id: str) -> Point:
        """Where a pawn sits when its tower is fanned open.

        Pawns are placed on a small ring around the field, starting at the top
        and going clockwise, which keeps the order in the tower readable.
        """
        if self.expanded_tile is None or self.expansion <= 0.001:
            return (0.0, 0.0)
        tile = self.state.board.tile(self.expanded_tile)
        if tile is None or pawn_id not in tile.stack:
            return (0.0, 0.0)
        stack = tile.stack
        count = len(stack)
        if count < 2:
            return (0.0, 0.0)
        index = stack.index(pawn_id)
        radius = self.state.board.layout.tile_radius * SPREAD_FACTOR * self.expansion
        angle = -math.pi / 2 + (math.tau * index / count)
        # Undo the stacking lift so the ring is centred on the field itself.
        lift = index * self.state.board.layout.stack_lift * self.expansion
        return (math.cos(angle) * radius, math.sin(angle) * radius + lift)

    def display_position(self, pawn_id: str) -> Point:
        base = self.visual.get(pawn_id)
        if base is None:
            token = self.state.tokens.get(pawn_id)
            base = token.position if token else (0.0, 0.0)
        if pawn_id == self.dragging:
            return base
        dx, dy = self._spread_offset(pawn_id)
        return (base[0] + dx, base[1] + dy)

    def token_at(self, mouse_pos: Tuple[int, int]) -> Optional[str]:
        """Topmost pawn under the cursor.

        The radius is whichever is larger of a fixed world distance and a fixed
        *screen* distance, so zooming out never makes pawns fiddly.
        """
        world = self.camera.screen_to_world(mouse_pos)
        radius = max(PICK_RADIUS_WORLD, PICK_RADIUS_SCREEN / max(0.05, self.camera.zoom))
        best: Optional[str] = None
        best_d = radius
        for pawn_id, _, _ in reversed(self._draw_order()):
            position = self.display_position(pawn_id)
            d = math.dist(world, position)
            if d <= best_d:
                best, best_d = pawn_id, d
        return best

    def _draw_order(self) -> List[Tuple[str, Point, Tuple[int, int, int]]]:
        """Back to front: lower on the map is nearer; higher in a stack is above.

        A pawn Shady has taken off the map is not in here at all, and that one
        omission is what removes it from the board: this list is what gets
        painted AND what :meth:`token_at` hit-tests, so the pawn stops being
        drawn and stops being clickable in the same breath.
        """
        entries: List[Tuple[str, Point, Tuple[int, int, int]]] = []
        for pawn_id, token in self.state.tokens.items():
            if self.state.pawn_is_hidden(pawn_id):
                continue
            entries.append((pawn_id, self.display_position(pawn_id), token.color))
        entries.sort(key=lambda item: (item[1][1], self.state.board.stack_depth(item[0])))
        return entries

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse_pos: Tuple[int, int]) -> None:
        self._sync_geometry()
        self._choice_pulse += dt
        self.animator.update(dt)
        self._update_walks(dt)
        self.camera.update(dt)
        self.particles.update(dt)

        self.zoom_slider.update(mouse_pos, dt)
        if not self.zoom_slider.dragging:
            self.zoom_slider.value = self.camera.zoom_fraction

        fx, fy = self.camera.scroll_fraction()
        rx, ry = self.camera.view_ratio()
        self.v_scroll.fraction, self.v_scroll.view_ratio = fy, ry
        self.h_scroll.fraction, self.h_scroll.view_ratio = fx, rx

        inside = self.layout.board_viewport.collidepoint(mouse_pos)
        if inside and self.dragging is None:
            world = self.camera.screen_to_world(mouse_pos)
            self.hover_tile = self.state.board.tile_at(world)
            self._update_expansion(world, dt)
        else:
            self.hover_tile = None
            self._update_expansion(None, dt)

        for pawn_id, position, color in self._draw_order():
            if self.animator.is_running(f"token:{pawn_id}"):
                self.particles.trail(position, color, count=1)

    def _update_expansion(self, world: Optional[Point], dt: float) -> None:
        """Open the tower under the cursor, close every other one."""
        wanted: Optional[int] = None
        if world is not None:
            radius = max(
                self.state.board.layout.tile_radius * 2.2,
                60.0 / max(0.05, self.camera.zoom),
            )
            tile = self.state.board.tile_near(world, radius)
            if tile is not None and len(tile.stack) > 1:
                wanted = tile.index

        if wanted != self.expanded_tile and self.expansion < 0.05:
            self.expanded_tile = wanted
        target = 1.0 if (wanted is not None and wanted == self.expanded_tile) else 0.0
        self.expansion = approach(self.expansion, target, 14.0, dt)
        if self.expansion < 0.01 and target == 0.0:
            self.expansion = 0.0
            if wanted != self.expanded_tile:
                self.expanded_tile = wanted

    def _update_walks(self, dt: float) -> None:
        """Advance every pawn currently walking its route."""
        finished: List[str] = []
        for pawn_id, walk in self.walks.items():
            if walk.delay > 0:
                walk.delay -= dt
                continue
            if len(walk.points) < 2:
                finished.append(pawn_id)
                continue

            walk.progress += dt / STEP_DURATION
            while walk.progress >= 1.0 and walk.index < len(walk.points) - 2:
                walk.progress -= 1.0
                walk.index += 1
                if walk.on_step is not None:
                    walk.on_step(walk.points[walk.index])
            t = min(1.0, walk.progress)

            a = walk.points[walk.index]
            b = walk.points[min(walk.index + 1, len(walk.points) - 1)]
            eased = ease_out_cubic(t) if walk.index == len(walk.points) - 2 else t
            hop = math.sin(math.pi * t) * walk.hop
            self.visual[pawn_id] = (
                a[0] + (b[0] - a[0]) * eased,
                a[1] + (b[1] - a[1]) * eased - hop,
            )
            if walk.index >= len(walk.points) - 2 and walk.progress >= 1.0:
                token = self.state.tokens.get(pawn_id)
                self.visual[pawn_id] = token.position if token else b
                if walk.on_step is not None:
                    walk.on_step(self.visual[pawn_id])
                finished.append(pawn_id)

        for pawn_id in finished:
            self.walks.pop(pawn_id, None)

    @property
    def busy(self) -> bool:
        """True while pawns are still moving — used to hold off follow-ups."""
        return bool(self.walks) or self.animator.busy

    def draw(self, surface: pygame.Surface, dt: float = 0.0) -> None:
        theme = self.r.theme
        viewport = self.layout.board_viewport
        self.board_renderer.draw(surface, self.camera, dt)

        # Under the previews and the hover ring: a frozen field is a standing
        # fact about the board, and whatever the player is doing right now
        # should be drawn on top of it.
        self._draw_frozen_fields(surface)

        for index in self.preview_route:
            tile = self.state.board.tile(index)
            if tile is None:
                continue
            colour = theme.valid if self.preview_valid else theme.invalid
            last = index == self.preview_route[-1]
            self.board_renderer.draw_tile_highlight(
                surface, self.camera, tile, colour, strength=1.0 if last else 0.5
            )

        if self.snap_tile is not None:
            self.board_renderer.draw_tile_highlight(
                surface, self.camera, self.snap_tile, theme.snap_ring
            )
        elif self.hover_tile is not None and not self.preview_route:
            self.board_renderer.draw_tile_highlight(
                surface, self.camera, self.hover_tile, theme.tile_hover, strength=0.55
            )

        if self.choice_tiles:
            self._draw_choice(surface, pygame.mouse.get_pos())
        if self.choice_pawns:
            self._draw_pawn_choice(surface)
        self._draw_status_marks(surface)

        if self.expanded_tile is not None and self.expansion > 0.02:
            tile = self.state.board.tile(self.expanded_tile)
            if tile is not None:
                centre = self.camera.world_to_screen(tile.position)
                radius = int(
                    self.state.board.layout.tile_radius
                    * SPREAD_FACTOR * self.expansion * self.camera.zoom * 1.5
                )
                self.r.aa_ring(centre, max(4, radius), theme.prompt, 2, surface)

        tokens = [
            (pawn_id, position, color, pawn_id == self.dragging)
            for pawn_id, position, color in self._draw_order()
        ]
        self.board_renderer.draw_tokens(surface, self.camera, tokens, highlight=self.dragging)
        if self.expansion < 0.5:
            self.board_renderer.draw_stack_badges(surface, self.camera, self.state.board)

        old_clip = surface.get_clip()
        surface.set_clip(viewport)
        self.particles.draw(surface, self.camera)
        surface.set_clip(old_clip)

        # The board keeps its own art; only the frame around it joins the new
        # interface — a brass edge with corner brackets, like a mounted map.
        pygame.draw.rect(surface, theme.panel_edge, viewport, 2, border_radius=10)
        pygame.draw.rect(surface, darken(theme.panel_edge, 0.5),
                         viewport.inflate(4, 4), 2, border_radius=12)
        self.r._corner_ornaments(surface, viewport.inflate(-6, -6),
                                 theme.brass_light, 10)
        self.v_scroll.draw(self.r, surface)
        self.h_scroll.draw(self.r, surface)

        self.zoom_slider.draw(self.r, surface)
        self.r.text(
            f"{self.camera.zoom:.2f}x", self.r.fonts.status(), theme.text_dim, surface,
            midleft=(self.layout.zoom_slider.right + 10, self.layout.zoom_slider.centery),
        )
