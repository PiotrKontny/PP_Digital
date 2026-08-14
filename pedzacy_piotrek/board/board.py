"""
The board model.

Replaces the prototype's ``Board`` class, which was three things at once: the
cell layout, a pygame surface cache, and a scrollbar widget.  Here it is only
the *model* — where the road goes, where the fields are, which pawn stands on
which field, and where the scenery sits.  Drawing lives in
``render/board_renderer.py``; scrolling and zooming live in ``engine/camera.py``.

The layout is generated, not hand-placed: give it a field count and a seed and
it produces a winding road of exactly that many fields, with rivers crossed by
bridges, villages at the crossroads, and forest scattered clear of the trail.
The same seed always produces the same map, which is what will let a host and
its clients render identical boards from a single integer.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..config.settings import BOARD, BoardLayout
from .path import Path2D, Point, ProximityGrid
from .tiles import BoardPosition, CampSlot, Prop, PropKind, River, Tile, TileKind


@dataclass
class BoardTheme:
    """Generation parameters for one board style, loaded from ``board.json``."""

    id: str = "forest"
    name: str = "Leśny Trakt"
    description: str = ""
    row_pattern: Tuple[str, ...] = ("single", "double", "single", "single", "double", "single")
    #: When set, rows are doubled with this probability instead of following
    #: ``row_pattern``.  ``None`` keeps the prototype's fixed cycle.
    double_frequency: Optional[float] = None
    wave_length: float = 640.0
    wave_amplitude: float = 300.0
    wave_jitter: float = 0.28
    river_min: int = 1
    river_max: int = 3
    river_width: float = 46.0
    forest_clusters: int = 26
    trees_per_cluster: Tuple[int, int] = (4, 11)
    rocks: int = 34
    villages: int = 3
    huts_per_village: Tuple[int, int] = (3, 5)
    grass_tufts: int = 220
    hills: int = 9
    road_clearance: float = 62.0

    @classmethod
    def from_dict(cls, raw: dict) -> "BoardTheme":
        rivers = raw.get("rivers", {})
        decor = raw.get("decor", {})
        tpc = decor.get("trees_per_cluster", [4, 11])
        hpv = decor.get("huts_per_village", [3, 5])
        return cls(
            id=raw.get("id", "forest"),
            name=raw.get("name", raw.get("id", "forest")),
            description=raw.get("description", ""),
            row_pattern=tuple(raw.get("row_pattern", cls.row_pattern)),
            double_frequency=(
                float(raw["double_frequency"])
                if raw.get("double_frequency") is not None else None
            ),
            wave_length=float(raw.get("wave_length", 640.0)),
            wave_amplitude=float(raw.get("wave_amplitude", 300.0)),
            wave_jitter=float(raw.get("wave_jitter", 0.28)),
            river_min=int(rivers.get("min", 1)),
            river_max=int(rivers.get("max", 3)),
            river_width=float(rivers.get("width", 46.0)),
            forest_clusters=int(decor.get("forest_clusters", 26)),
            trees_per_cluster=(int(tpc[0]), int(tpc[1])),
            rocks=int(decor.get("rocks", 34)),
            villages=int(decor.get("villages", 3)),
            huts_per_village=(int(hpv[0]), int(hpv[1])),
            grass_tufts=int(decor.get("grass_tufts", 220)),
            hills=int(decor.get("hills", 9)),
            road_clearance=float(decor.get("road_clearance", 62.0)),
        )

    @classmethod
    def load(cls, path=None, theme_id: Optional[str] = None) -> "BoardTheme":
        import json

        from ..config import settings

        path = path or settings.BOARD_FILE
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        wanted = theme_id or payload.get("default_theme")
        themes = payload.get("themes", [])
        for raw in themes:
            if raw.get("id") == wanted:
                return cls.from_dict(raw)
        return cls.from_dict(themes[0]) if themes else cls()


def make_rows(
    position_count: int,
    pattern: Sequence[str],
    double_frequency: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> List[str]:
    """Which rows hold one field and which hold two.

    Two modes:

    * **pattern** — the prototype's behaviour, a repeating cycle such as
      ``single, double, single, single, double, single``;
    * **frequency** — each row is doubled with the given probability, drawn
      from the seeded RNG so the board stays reproducible.

    Both keep the prototype's rule that the board always ends on a single
    field, so the finish is never one of a pair.

    ``position_count`` counts *positions*, not fields — ONE ROW IS ONE ROW,
    whether it holds a single field or the pair ``12a``/``12b``.  The list is
    therefore always exactly ``position_count`` long, which is what makes the
    lobby's board size mean "the meta stands on this number".  Budgeting
    *fields* here was the old bug: every doubled row silently ate two of them
    and a board asked for 24 finished at 19.
    """
    if position_count < 10:
        raise ValueError("Plansza musi mieć co najmniej 10 pól")

    use_frequency = double_frequency is not None and rng is not None
    frequency = max(0.0, min(1.0, double_frequency or 0.0))

    rows: List[str] = []
    for index in range(position_count):
        # The draw happens even for the final row, so that the RNG stream
        # depends only on the board size and not on where the doubles fell.
        if use_frequency:
            row_type = "double" if rng.random() < frequency else "single"
        else:
            row_type = pattern[index % len(pattern)]
        if index == position_count - 1:
            row_type = "single"
        rows.append(row_type)
    return rows


@dataclass(frozen=True)
class BoardGeometry:
    """What the generator actually settled on.

    Kept because it is the first thing anyone debugging a strange-looking board
    will want, and because the tests assert on it directly.
    """

    span: float
    amplitude: float
    wave_length: float
    #: Tightest curve measured on the finished road.
    min_radius: float
    #: Closest approach between two separate stretches of road.
    min_self_distance: float
    #: How many amplitude reductions were needed (1 = first try was fine).
    attempts: int = 1


@dataclass
class BoardModel:
    """Generated geometry plus live pawn placement."""

    #: The board's LOGICAL length: how many positions the road holds, and
    #: therefore the number the meta stands on.  This is the value the lobby
    #: shows.  A doubled position spends ONE of these however many fields it
    #: is drawn with, so ``len(tiles) >= cell_count`` while
    #: ``position_count == cell_count`` always.
    cell_count: int
    theme: BoardTheme
    layout: BoardLayout
    seed: int

    width: int = 0
    height: int = 0
    path: Optional[Path2D] = None
    geometry: Optional[BoardGeometry] = None
    tiles: List[Tile] = field(default_factory=list)
    #: Logical steps along the road.  A doubled row is one position with two
    #: fields, so ``len(positions) <= len(tiles)``.
    positions: List[BoardPosition] = field(default_factory=list)
    camp_slots: List[CampSlot] = field(default_factory=list)
    rivers: List[River] = field(default_factory=list)
    props: List[Prop] = field(default_factory=list)
    #: pawn id -> tile index (absent means "not on a field")
    pawn_tiles: Dict[str, int] = field(default_factory=dict)

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def generate(
        cls,
        cell_count: int,
        seed: int = 0,
        theme: Optional[BoardTheme] = None,
        layout: Optional[BoardLayout] = None,
        pawn_count: int = 6,
        double_frequency: Optional[float] = None,
    ) -> "BoardModel":
        """Build a board of ``cell_count`` POSITIONS, meta included.

        ``cell_count`` is the lobby's board size and it is a logical count:
        ask for 24 and the meta is 24, whether the generator widened two rows
        or ten.  ``double_frequency`` therefore changes how many *fields* the
        board has, never how long it is.
        """
        theme = theme or BoardTheme.load()
        layout = layout or BOARD
        if double_frequency is not None:
            theme = replace(theme, double_frequency=double_frequency)
        model = cls(cell_count=cell_count, theme=theme, layout=layout, seed=seed)
        model._build(pawn_count)
        return model

    @property
    def horizontal(self) -> bool:
        return self.layout.orientation != "vertical"

    def _build(self, pawn_count: int) -> None:
        rng = random.Random(self.seed or 0)
        rows = make_rows(
            self.cell_count, self.theme.row_pattern,
            self.theme.double_frequency, random.Random((self.seed or 0) ^ 0x5170),
        )
        needed_length = max(1.0, (len(rows) - 1) * self.layout.tile_spacing)

        self.path, span, self.geometry = self._fit_path(needed_length, rng)

        along_extent = span + self.layout.start_band + self.layout.finish_band
        if self.horizontal:
            self.width = int(round(along_extent))
            self.height = self.layout.canvas_across
        else:
            self.width = self.layout.canvas_across
            self.height = int(round(along_extent))

        self._place_tiles(rows)
        self._place_camp(pawn_count)
        self._carve_rivers(rng)
        self._scatter_decor(rng)

    # ── path fitting ─────────────────────────────────────────────────────────
    def _to_world(self, along: float, across: float, span: float) -> Point:
        """Map travel-space coordinates onto the canvas.

        ``along`` grows from the start of the road, ``across`` is signed and
        centred on the canvas.  Horizontal boards run left → right; vertical
        ones run bottom → top, which is why ``along`` is mirrored there.
        """
        centre = self.layout.canvas_across / 2.0
        if self.horizontal:
            return (self.layout.start_band + along, centre + across)
        total = span + self.layout.start_band + self.layout.finish_band
        return (centre + across, total - self.layout.start_band - along)

    def _control_points(
        self, span: float, amplitude: float, wave_length: float,
        wave_scales: Sequence[float],
    ) -> List[Point]:
        """Control points for one sweeping serpentine, in world space."""
        waves = max(0.75, span / max(200.0, wave_length))
        steps = max(12, int(waves * 14))

        points: List[Point] = []
        for k in range(steps + 1):
            t = k / steps
            along = span * t
            phase = 2.0 * math.pi * waves * t
            index = min(len(wave_scales) - 1, int(waves * t))
            across = math.sin(phase) * amplitude * wave_scales[index]
            points.append(self._to_world(along, across, span))
        return points

    def _fit_path(
        self, needed_length: float, rng: random.Random
    ) -> tuple[Path2D, float, "BoardGeometry"]:
        """Build a road of exactly the right length that can never self-crowd.

        Three constraints have to hold at once, and the previous version only
        enforced the first, which is where the squashed boards came from:

        1. the road must be exactly ``needed_length`` long, so the field count
           comes out right;
        2. no curve may be tighter than :attr:`BoardLayout.min_turn_radius`,
           or the inner lane of a bend compresses its fields into each other;
        3. no two passes of the road may come within ``min_road_gap``.

        Constraint 2 is *derived*, not tuned: for a sine of amplitude ``A`` and
        wavelength ``L`` the tightest radius is ``L²/(4π²A)``, so demanding a
        radius gives a minimum wavelength for the amplitude we want.  The road
        therefore stays as curvy as the theme asks for and simply stretches out
        along the direction of travel to make room for the bends.

        Constraints are then *verified* numerically, because Catmull-Rom
        smoothing and the per-wave jitter can both overshoot the ideal sine.  A
        failed check shrinks the amplitude by 12% and tries again; with
        amplitude zero the road is straight and trivially valid, so the loop
        always terminates.
        """
        layout = self.layout
        min_radius = layout.min_turn_radius

        across_limit = max(30.0, layout.canvas_across / 2.0 - layout.side_margin)
        amplitude = min(self.theme.wave_amplitude, across_limit)
        # Jitter only ever shortens a wave, so the amplitude bound stays valid.
        jitter = max(0.0, min(0.6, self.theme.wave_jitter))
        wave_scales = [1.0 - rng.uniform(0.0, jitter) for _ in range(96)]

        for attempt in range(14):
            wave_length = max(
                self.theme.wave_length,
                2.0 * math.pi * math.sqrt(max(1.0, amplitude * min_radius)) * 1.06,
            )
            span, path = self._span_for_length(
                needed_length, amplitude, wave_length, wave_scales
            )
            radius = self._tightest_radius(path)
            separation = self._closest_self_approach(path)
            needed_separation = 2.0 * layout.road_half_width + layout.min_road_gap
            if radius >= min_radius and separation >= needed_separation:
                return path, span, BoardGeometry(
                    span=span,
                    amplitude=amplitude,
                    wave_length=wave_length,
                    min_radius=radius,
                    min_self_distance=separation,
                    attempts=attempt + 1,
                )
            amplitude *= 0.88

        # Fallback: a straight road always satisfies every constraint.
        span, path = self._span_for_length(needed_length, 0.0, 1000.0, wave_scales)
        return path, span, BoardGeometry(
            span=span, amplitude=0.0, wave_length=1000.0,
            min_radius=float("inf"), min_self_distance=float("inf"), attempts=15,
        )

    def _span_for_length(
        self, needed_length: float, amplitude: float, wave_length: float,
        wave_scales: Sequence[float],
    ) -> tuple[float, Path2D]:
        """Iterate the travel span until the road measures the length we need."""
        span = max(300.0, needed_length * 0.85)
        path = Path2D.from_control_points(
            self._control_points(span, amplitude, wave_length, wave_scales)
        )
        for _ in range(48):
            if abs(needed_length - path.length) < 0.75:
                break
            span = max(150.0, span * (needed_length / max(1.0, path.length)))
            path = Path2D.from_control_points(
                self._control_points(span, amplitude, wave_length, wave_scales)
            )
        return span, path

    @staticmethod
    def _tightest_radius(path: Path2D) -> float:
        """Smallest radius of curvature anywhere on the road.

        Uses the circumscribed-circle formula on triples of resampled points:
        ``R = abc / 4A``.  Collinear triples give an infinite radius, which is
        exactly right for a straight stretch.
        """
        points = path.resample(12.0)
        if len(points) < 3:
            return float("inf")
        best = float("inf")
        for (ax, ay), (bx, by), (cx, cy) in zip(points, points[1:], points[2:]):
            a = math.dist((bx, by), (cx, cy))
            b = math.dist((ax, ay), (cx, cy))
            c = math.dist((ax, ay), (bx, by))
            area2 = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
            if area2 < 1e-9:
                continue
            best = min(best, (a * b * c) / (2.0 * area2))
        return best

    @staticmethod
    def _closest_self_approach(path: Path2D, ignore_arc: float = 260.0) -> float:
        """How close the road comes to another part of itself.

        Neighbouring samples are obviously close, so pairs nearer than
        ``ignore_arc`` along the road are skipped; what is left is the hairpin
        case, where two separate stretches nearly touch.
        """
        step = 18.0
        points = path.resample(step)
        if len(points) < 3:
            return float("inf")
        skip = max(2, int(ignore_arc / step))
        best = float("inf")
        for i in range(len(points) - skip):
            ax, ay = points[i]
            for j in range(i + skip, len(points)):
                bx, by = points[j]
                d = math.hypot(bx - ax, by - ay)
                if d < best:
                    best = d
        return best

    # ── tiles ────────────────────────────────────────────────────────────────
    def _place_tiles(self, rows: Sequence[str]) -> None:
        """Lay the fields out along the road and group them into positions.

        A doubled row produces two fields sharing one position number: ``12a``
        and ``12b``.  They are the same distance from the start, so movement
        counts them once — which is why the model keeps ``positions`` as well
        as ``tiles``.
        """
        assert self.path is not None
        self.tiles.clear()
        self.positions.clear()
        number = 1
        for row_index, row_type in enumerate(rows):
            arc = row_index * self.layout.tile_spacing
            angle = self.path.angle_at(arc)
            doubled = row_type == "double"
            laterals = (
                (-self.layout.lane_offset, self.layout.lane_offset) if doubled else (0.0,)
            )
            slot = len(self.positions)
            position = BoardPosition(index=slot, number=number)

            for variant_index, lateral in enumerate(laterals):
                world = self.path.offset_point(arc, lateral)
                kind = TileKind.CROSSROAD if doubled else TileKind.ROAD
                if slot == 0:
                    kind = TileKind.START
                tile = Tile(
                    index=len(self.tiles),
                    number=number,
                    position=world,
                    kind=kind,
                    arc=arc,
                    lateral=lateral,
                    angle=angle,
                    slot=slot,
                    variant=("ab"[variant_index] if doubled else ""),
                )
                self.tiles.append(tile)
                position.tiles.append(tile)

            # A position with one field is not a choice, so it carries no a/b
            # label.  Single rows are already built that way; this only guards
            # a theme that hands back something other than "single"/"double".
            if len(position.tiles) == 1:
                position.tiles[0].variant = ""
                if position.tiles[0].kind is TileKind.CROSSROAD:
                    position.tiles[0].kind = TileKind.ROAD
            self.positions.append(position)
            number += 1

        for tile in self.positions[-1].tiles if self.positions else []:
            tile.kind = TileKind.FINISH

    def verify_spacing(self) -> Optional[str]:
        """Check that no two fields crowd each other.

        Returns ``None`` when the board is sound, otherwise a description of the
        worst offender.  Called after every generation — a board that fails this
        is a bug, not a cosmetic complaint, because two overlapping fields are
        impossible to click apart.
        """
        limit = self.layout.min_tile_distance
        worst: Optional[tuple[float, Tile, Tile]] = None
        for i, a in enumerate(self.tiles):
            for b in self.tiles[i + 1 :]:
                d = math.dist(a.position, b.position)
                if d < limit and (worst is None or d < worst[0]):
                    worst = (d, a, b)
        if worst is None:
            return None
        d, a, b = worst
        return (
            f"pola {a.number} i {b.number} są zbyt blisko: "
            f"{d:.1f} px < {limit:.1f} px"
        )

    def _place_camp(self, pawn_count: int) -> None:
        """Waiting positions before field 1, laid out in a gentle arc.

        The camp sits *behind* the start along the direction of travel, so on a
        horizontal board it is a column on the left rather than a row below.
        """
        assert self.path is not None
        origin = self.path.point_at(0.0)
        pawn_count = max(1, pawn_count)
        spacing = self.layout.camp_spacing
        total = (pawn_count - 1) * spacing
        back = self.layout.start_band * 0.55

        self.camp_slots = []
        for i in range(pawn_count):
            offset = -total / 2 + i * spacing
            # A shallow bow so the camp does not read as a spreadsheet row.
            bow = 0.0 if pawn_count == 1 else (i / (pawn_count - 1) - 0.5) ** 2
            if self.horizontal:
                x = origin[0] - back + bow * 26.0
                y = origin[1] + offset
            else:
                x = origin[0] + offset
                y = origin[1] + back + bow * 26.0
            x = max(50.0, min(self.width - 50.0, x))
            y = max(50.0, min(self.height - 50.0, y))
            self.camp_slots.append(CampSlot(index=i, position=(x, y)))

    @property
    def camp_bounds(self) -> Optional[Tuple[Point, float, float]]:
        """Centre and half-extents of the camp, with a margin.

        Scenery keeps out of this box; the previous version recomputed the same
        numbers in two places and they drifted apart.
        """
        if not self.camp_slots:
            return None
        xs = [s.position[0] for s in self.camp_slots]
        ys = [s.position[1] for s in self.camp_slots]
        centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        return (
            centre,
            (max(xs) - min(xs)) / 2 + 86.0,
            (max(ys) - min(ys)) / 2 + 86.0,
        )

    # ── scenery ──────────────────────────────────────────────────────────────
    def _carve_rivers(self, rng: random.Random) -> None:
        assert self.path is not None
        self.rivers = []
        if not self.tiles:
            return
        count = rng.randint(self.theme.river_min, max(self.theme.river_min, self.theme.river_max))
        row_arcs = sorted({t.arc for t in self.tiles})
        # Keep water away from the camp and the finishing straight.
        candidates = row_arcs[2:-2]
        if not candidates or count <= 0:
            return

        chosen: List[float] = []
        rng.shuffle(candidates)
        for arc in candidates:
            if len(chosen) >= count:
                break
            if all(abs(arc - c) > self.layout.tile_spacing * 3 for c in chosen):
                chosen.append(arc)

        for arc in sorted(chosen):
            cross = self.path.point_at(arc)
            angle = self.path.angle_at(arc)
            points: List[Point] = []
            wobble = rng.uniform(18.0, 42.0)
            phase = rng.uniform(0.0, math.tau)
            steps = 26
            # A river runs across the direction of travel, so it always meets
            # the road head-on and the bridge sits square on the crossing.
            for i in range(steps + 1):
                t = i / steps
                drift = math.sin(phase + t * math.pi * 2.2) * wobble
                if self.horizontal:
                    y = -60.0 + t * (self.height + 120.0)
                    points.append((cross[0] + drift, y))
                else:
                    x = -60.0 + t * (self.width + 120.0)
                    points.append((x, cross[1] + drift))
            tile = min(self.tiles, key=lambda t: abs(t.arc - arc))
            if tile.kind not in (TileKind.START, TileKind.FINISH):
                tile.kind = TileKind.BRIDGE
                for sibling in self.tiles:
                    if sibling.arc == tile.arc and sibling.kind == TileKind.CROSSROAD:
                        sibling.kind = TileKind.BRIDGE
            self.rivers.append(
                River(
                    points=points,
                    width=self.theme.river_width,
                    bridge_tile=tile.index,
                    bridge_point=cross,
                    bridge_angle=angle,
                )
            )

    def _scatter_decor(self, rng: random.Random) -> None:
        assert self.path is not None
        self.props = []
        road_points = self.path.resample(14.0)
        grid = ProximityGrid(road_points)
        water_points = [p for river in self.rivers for p in river.points]
        water_grid = ProximityGrid(water_points) if water_points else None
        clearance = self.theme.road_clearance

        # Furniture the scenery must not grow through: the starting camp and
        # the banner above the meta.
        keep_out: List[tuple[Point, float, float]] = []
        camp = self.camp_bounds
        if camp is not None:
            keep_out.append(camp)
        finish = next((t for t in self.tiles if t.kind is TileKind.FINISH), None)
        if finish is not None:
            keep_out.append(((finish.position[0], finish.position[1] - 40), 110.0, 90.0))

        def clear(point: Point, extra: float = 0.0) -> bool:
            if grid.distance_to(point) < clearance + extra:
                return False
            if water_grid is not None and water_grid.distance_to(point) < 46.0 + extra:
                return False
            for (cx, cy), half_w, half_h in keep_out:
                if abs(point[0] - cx) < half_w and abs(point[1] - cy) < half_h:
                    return False
            return True

        def sample(margin: float = 30.0, extra: float = 0.0, tries: int = 24) -> Optional[Point]:
            for _ in range(tries):
                p = (
                    rng.uniform(margin, self.width - margin),
                    rng.uniform(margin, self.height - margin),
                )
                if clear(p, extra):
                    return p
            return None

        def depth_of(point: Point) -> float:
            return max(0.0, min(1.0, point[1] / max(1.0, self.height)))

        # Rolling hills sit furthest back and may pass under the road.
        for _ in range(self.theme.hills):
            p = (rng.uniform(0, self.width), rng.uniform(0, self.height))
            self.props.append(
                Prop(PropKind.HILL, p, scale=rng.uniform(1.6, 3.4), variant=rng.randint(0, 1),
                     depth=depth_of(p))
            )

        # Villages cluster beside the crossroads, where the road widens.
        crossroads = [t for t in self.tiles if t.kind in (TileKind.CROSSROAD, TileKind.BRIDGE)]
        rng.shuffle(crossroads)
        for tile in crossroads[: self.theme.villages]:
            side = rng.choice((-1.0, 1.0))
            base = self.path.offset_point(tile.arc, side * rng.uniform(110.0, 165.0))
            for _ in range(rng.randint(*self.theme.huts_per_village)):
                p = (base[0] + rng.uniform(-70, 70), base[1] + rng.uniform(-60, 60))
                if not clear(p, extra=-18.0):
                    continue
                self.props.append(
                    Prop(PropKind.HUT, p, scale=rng.uniform(0.85, 1.25),
                         variant=rng.randint(0, 2), depth=depth_of(p))
                )

        # Forest clusters — trees clumped, not sprinkled, so it reads as woodland.
        for _ in range(self.theme.forest_clusters):
            centre = sample(extra=20.0)
            if centre is None:
                continue
            spread = rng.uniform(40.0, 110.0)
            for _ in range(rng.randint(*self.theme.trees_per_cluster)):
                p = (centre[0] + rng.gauss(0, spread), centre[1] + rng.gauss(0, spread * 0.7))
                if not (0 < p[0] < self.width and 0 < p[1] < self.height):
                    continue
                if not clear(p):
                    continue
                self.props.append(
                    Prop(PropKind.TREE, p, scale=rng.uniform(0.7, 1.35),
                         variant=rng.randint(0, 2), depth=depth_of(p))
                )

        for _ in range(self.theme.rocks):
            p = sample(extra=-14.0)
            if p is None:
                continue
            self.props.append(
                Prop(PropKind.ROCK, p, scale=rng.uniform(0.6, 1.5),
                     variant=rng.randint(0, 2), depth=depth_of(p))
            )

        for _ in range(self.theme.grass_tufts):
            p = sample(margin=12.0, extra=-40.0, tries=6)
            if p is None:
                continue
            self.props.append(
                Prop(PropKind.GRASS, p, scale=rng.uniform(0.5, 1.1),
                     variant=rng.randint(0, 2), depth=depth_of(p))
            )

        # Painter's algorithm: things lower on the map are nearer the viewer.
        self.props.sort(key=lambda prop: prop.position[1])

    # ── queries ──────────────────────────────────────────────────────────────
    def tile(self, index: int) -> Optional[Tile]:
        if 0 <= index < len(self.tiles):
            return self.tiles[index]
        return None

    def position(self, index: int) -> Optional[BoardPosition]:
        if 0 <= index < len(self.positions):
            return self.positions[index]
        return None

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def last_position(self) -> int:
        return len(self.positions) - 1

    @property
    def meta_number(self) -> int:
        """The number printed on the finish — equal to ``cell_count``.

        Kept as a query rather than left implicit because "which number is the
        meta" is the question the lobby setting answers, and it used to have a
        different answer from the one the player chose.
        """
        return self.positions[-1].number if self.positions else 0

    def position_of_pawn(self, pawn_id: str) -> Optional[int]:
        """Which board position a pawn stands on, ignoring which half it chose."""
        tile = self.pawn_tile(pawn_id)
        return tile.slot if tile is not None else None

    def tiles_at_position(self, index: int) -> List[Tile]:
        position = self.position(index)
        return list(position.tiles) if position is not None else []

    def tile_by_label(self, label: str) -> Optional[Tile]:
        for tile in self.tiles:
            if tile.label == label:
                return tile
        return None

    def tile_by_number(self, number: int) -> Optional[Tile]:
        for tile in self.tiles:
            if tile.number == number:
                return tile
        return None

    def tile_near(self, point: Point, radius: Optional[float] = None) -> Optional[Tile]:
        radius = self.layout.snap_radius if radius is None else radius
        best: Optional[Tile] = None
        best_dist = radius
        for tile in self.tiles:
            d = math.dist(point, tile.position)
            if d <= best_dist:
                best, best_dist = tile, d
        return best

    def tile_at(self, point: Point) -> Optional[Tile]:
        """Field directly under a cursor (uses the field's own radius)."""
        return self.tile_near(point, self.layout.tile_radius + 4.0)

    def camp_position(self, slot_index: int) -> Point:
        if not self.camp_slots:
            return (self.width / 2, self.height - 40)
        slot = self.camp_slots[slot_index % len(self.camp_slots)]
        return slot.position

    # ── pawn placement (the stacking rule) ───────────────────────────────────
    def place_pawn(self, pawn_id: str, tile_index: int, *, on_top: bool = True) -> Optional[Tile]:
        """Put a pawn on a field, stacking it above whoever is already there."""
        tile = self.tile(tile_index)
        if tile is None:
            return None
        self.remove_pawn(pawn_id)
        if on_top:
            tile.stack.append(pawn_id)
        else:
            tile.stack.insert(0, pawn_id)
        self.pawn_tiles[pawn_id] = tile.index
        return tile

    def remove_pawn(self, pawn_id: str) -> None:
        old_index = self.pawn_tiles.pop(pawn_id, None)
        if old_index is None:
            return
        old = self.tile(old_index)
        if old is not None and pawn_id in old.stack:
            old.stack.remove(pawn_id)

    def pawn_tile(self, pawn_id: str) -> Optional[Tile]:
        index = self.pawn_tiles.get(pawn_id)
        return self.tile(index) if index is not None else None

    def stack_depth(self, pawn_id: str) -> int:
        tile = self.pawn_tile(pawn_id)
        if tile is None or pawn_id not in tile.stack:
            return 0
        return tile.stack.index(pawn_id)

    def stack_position(self, tile: Tile, depth: int) -> Point:
        """Where the ``depth``-th pawn of a tower is drawn."""
        return (tile.position[0], tile.position[1] - depth * self.layout.stack_lift)

    def pawn_position(self, pawn_id: str) -> Optional[Point]:
        tile = self.pawn_tile(pawn_id)
        if tile is None:
            return None
        return self.stack_position(tile, self.stack_depth(pawn_id))

    def carried_pawns(self, pawn_id: str) -> List[str]:
        """Pawns riding on top of this one — they travel with it.

        This is the rule from Pędzące Żółwie: move the turtle at the bottom and
        the whole tower above it comes along.
        """
        tile = self.pawn_tile(pawn_id)
        if tile is None or pawn_id not in tile.stack:
            return []
        return tile.stack[tile.stack.index(pawn_id) + 1 :]

    def tower_of(self, tile_index: int) -> List[str]:
        tile = self.tile(tile_index)
        return list(tile.stack) if tile else []

    # ── serialisation ────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """The board reduces to four numbers — that is the whole point of
        generating it rather than storing it."""
        return {
            "cell_count": self.cell_count,
            "seed": self.seed,
            "theme": self.theme.id,
            "pawn_tiles": dict(self.pawn_tiles),
            "stacks": {t.index: list(t.stack) for t in self.tiles if t.stack},
        }

    def apply_placement(self, pawn_tiles: Dict[str, int], stacks: Dict[int, Iterable[str]]) -> None:
        for tile in self.tiles:
            tile.stack.clear()
        self.pawn_tiles = dict(pawn_tiles)
        for index, stack in stacks.items():
            tile = self.tile(int(index))
            if tile is not None:
                tile.stack = list(stack)
