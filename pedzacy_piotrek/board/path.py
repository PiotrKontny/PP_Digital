"""
Path geometry.

The board is no longer a grid of rows — it is a curve.  Everything the rest of
the game needs from that curve is here:

* a Catmull-Rom spline through a handful of control points, so the road bends
  smoothly instead of turning corners;
* arc-length parametrisation, so field N is always exactly ``N * spacing``
  pixels of *road* from the start, no matter how sharply the road happens to
  bend there.  Without this, fields bunch up in the curves;
* normals, so a "double" row can place its two fields side by side *across*
  the road, and so decoration can be kept clear of it.

No pygame import: this is pure maths and is unit-testable on its own.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

Point = Tuple[float, float]


def catmull_rom(points: Sequence[Point], samples_per_segment: int = 24) -> List[Point]:
    """Smooth curve through every one of ``points`` (not merely near them).

    Endpoints are duplicated so the curve starts and ends exactly on the first
    and last control point.
    """
    if len(points) < 2:
        return list(points)

    pts: List[Point] = [points[0], *points, points[-1]]
    out: List[Point] = []
    for i in range(len(pts) - 3):
        p0, p1, p2, p3 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        for step in range(samples_per_segment):
            t = step / samples_per_segment
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    out.append(tuple(pts[-2]))  # type: ignore[arg-type]
    return out


@dataclass
class Path2D:
    """A polyline with arc-length lookup."""

    points: List[Point]
    _cumulative: List[float]

    @classmethod
    def from_points(cls, points: Sequence[Point]) -> "Path2D":
        pts = [tuple(map(float, p)) for p in points]
        if len(pts) < 2:
            raise ValueError("Path2D wymaga co najmniej dwóch punktów")
        cumulative = [0.0]
        for a, b in zip(pts, pts[1:]):
            cumulative.append(cumulative[-1] + math.dist(a, b))
        return cls(points=pts, _cumulative=cumulative)  # type: ignore[arg-type]

    @classmethod
    def from_control_points(
        cls, control: Sequence[Point], samples_per_segment: int = 24
    ) -> "Path2D":
        return cls.from_points(catmull_rom(control, samples_per_segment))

    @property
    def length(self) -> float:
        return self._cumulative[-1]

    @property
    def start(self) -> Point:
        return self.points[0]

    @property
    def end(self) -> Point:
        return self.points[-1]

    # ── sampling ─────────────────────────────────────────────────────────────
    def _segment_at(self, distance: float) -> tuple[int, float]:
        """Index of the segment containing ``distance`` and the fraction into it."""
        d = max(0.0, min(self.length, distance))
        idx = bisect.bisect_right(self._cumulative, d) - 1
        idx = max(0, min(len(self.points) - 2, idx))
        seg_len = self._cumulative[idx + 1] - self._cumulative[idx]
        frac = 0.0 if seg_len <= 1e-9 else (d - self._cumulative[idx]) / seg_len
        return idx, frac

    def point_at(self, distance: float) -> Point:
        idx, frac = self._segment_at(distance)
        ax, ay = self.points[idx]
        bx, by = self.points[idx + 1]
        return (ax + (bx - ax) * frac, ay + (by - ay) * frac)

    def tangent_at(self, distance: float) -> Point:
        idx, _ = self._segment_at(distance)
        ax, ay = self.points[idx]
        bx, by = self.points[idx + 1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1.0
        return (dx / norm, dy / norm)

    def normal_at(self, distance: float) -> Point:
        """Unit vector perpendicular to the road, pointing to its right side."""
        tx, ty = self.tangent_at(distance)
        return (-ty, tx)

    def offset_point(self, distance: float, lateral: float) -> Point:
        px, py = self.point_at(distance)
        nx, ny = self.normal_at(distance)
        return (px + nx * lateral, py + ny * lateral)

    def angle_at(self, distance: float) -> float:
        tx, ty = self.tangent_at(distance)
        return math.degrees(math.atan2(-ty, tx))

    def resample(self, step: float) -> List[Point]:
        """Evenly spaced points along the path — used for hit tests and decor."""
        step = max(1.0, step)
        out: List[Point] = []
        d = 0.0
        while d < self.length:
            out.append(self.point_at(d))
            d += step
        out.append(self.end)
        return out


class ProximityGrid:
    """Coarse spatial hash answering "how far is this point from the road?".

    Decoration is scattered by rejection sampling — several thousand candidate
    points, each tested against the road.  A linear scan over the polyline
    would be O(candidates x samples); this makes it effectively constant time.
    """

    def __init__(self, points: Sequence[Point], cell_size: float = 96.0) -> None:
        self.cell_size = cell_size
        self._cells: dict[tuple[int, int], List[Point]] = {}
        for p in points:
            self._cells.setdefault(self._key(p), []).append(tuple(p))  # type: ignore[arg-type]

    def _key(self, p: Point) -> tuple[int, int]:
        return (int(p[0] // self.cell_size), int(p[1] // self.cell_size))

    def distance_to(self, point: Point, max_rings: int = 3) -> float:
        """Distance to the nearest stored point, searching outward by rings."""
        cx, cy = self._key(point)
        best = float("inf")
        for ring in range(max_rings + 1):
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    # Only the freshly added outer ring needs checking.
                    if ring and max(abs(gx - cx), abs(gy - cy)) != ring:
                        continue
                    for p in self._cells.get((gx, gy), ()):
                        d = math.dist(point, p)
                        if d < best:
                            best = d
            if best <= ring * self.cell_size:
                break
        return best

    def is_clear(self, point: Point, clearance: float) -> bool:
        return self.distance_to(point) >= clearance
