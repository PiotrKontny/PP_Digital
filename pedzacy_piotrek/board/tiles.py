"""
Tiles and scenery props — the board's vocabulary.

A tile knows *where* it is and *what kind of place* it is; it knows nothing
about how it is painted.  The renderer reads ``TileKind`` and decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

Point = Tuple[float, float]


class TileKind(Enum):
    """What a field looks like and, later, what happens when you land on it."""

    ROAD = "road"          # ordinary field on the trail
    CROSSROAD = "crossroad"  # one of the two fields of a widened stretch
    BRIDGE = "bridge"      # the road crosses a river here
    VILLAGE = "village"    # a settlement stands beside the field
    START = "start"        # field number 1
    FINISH = "finish"      # the last field — the meta


class PropKind(Enum):
    """Decoration types the board renderer knows how to draw."""

    TREE = "tree"
    ROCK = "rock"
    HUT = "hut"
    GRASS = "grass"
    HILL = "hill"


@dataclass
class Tile:
    """One playable field.

    A field is not the same thing as a *board position*.  A widened stretch of
    road holds two fields that share one position: they are ``12a`` and ``12b``,
    both twelve steps from the start.  ``number`` is therefore the position
    number (shared by the pair) and ``variant`` distinguishes them.
    """

    index: int              # 0-based position in the tile list
    number: int             # 1-based number of the board position it belongs to
    position: Point         # centre, in world coordinates
    kind: TileKind = TileKind.ROAD
    #: Distance along the road, used for ordering and for animation paths.
    arc: float = 0.0
    #: Sideways offset from the road centre (double rows use ±lane_offset).
    lateral: float = 0.0
    #: Road heading at this point, in degrees — lets props and fields align.
    angle: float = 0.0
    #: 0-based index of the board position this field belongs to.
    slot: int = 0
    #: "a"/"b" on a doubled position, empty on a single one.
    variant: str = ""
    #: Pawn ids currently standing here, bottom of the stack first.  This is
    #: the "turtle tower" of the original game: order matters, because the
    #: pawn at the bottom is the one a check inspects.
    stack: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """What players see on the field: ``12``, or ``12a`` / ``12b``."""
        return f"{self.number}{self.variant}"

    @property
    def is_doubled(self) -> bool:
        return bool(self.variant)

    @property
    def is_occupied(self) -> bool:
        return bool(self.stack)

    @property
    def bottom_pawn(self) -> Optional[str]:
        return self.stack[0] if self.stack else None

    @property
    def top_pawn(self) -> Optional[str]:
        return self.stack[-1] if self.stack else None


@dataclass
class BoardPosition:
    """One logical step along the road: either one field or a choice of two.

    Movement counts positions, not fields.  Landing on a doubled position is a
    decision — the pawn has arrived, but the player still has to say which of
    the two fields it stands on.
    """

    index: int              # 0-based
    number: int             # 1-based, what players see
    tiles: List[Tile] = field(default_factory=list)

    @property
    def is_doubled(self) -> bool:
        return len(self.tiles) > 1

    @property
    def arc(self) -> float:
        return self.tiles[0].arc if self.tiles else 0.0

    @property
    def centre(self) -> Point:
        """Midpoint of the position — the road centre line, between a pair."""
        if not self.tiles:
            return (0.0, 0.0)
        xs = [t.position[0] for t in self.tiles]
        ys = [t.position[1] for t in self.tiles]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def tile_ids(self) -> List[int]:
        return [tile.index for tile in self.tiles]


@dataclass(frozen=True)
class Prop:
    """A piece of scenery.  Purely decorative, generated from the board seed."""

    kind: PropKind
    position: Point
    scale: float = 1.0
    variant: int = 0
    #: 0..1 depth hint — lets the renderer tint distant scenery.
    depth: float = 0.5


@dataclass(frozen=True)
class River:
    """A watercourse crossing the map, with the road bridging it."""

    points: List[Point]
    width: float
    #: Index of the tile that carries the bridge, if the river meets the road.
    bridge_tile: Optional[int] = None
    bridge_point: Optional[Point] = None
    bridge_angle: float = 0.0


@dataclass(frozen=True)
class CampSlot:
    """A waiting position in the starting camp, before field 1."""

    index: int
    position: Point
