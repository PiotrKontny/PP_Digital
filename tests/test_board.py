"""
Board generation and pawn-stacking tests.

The board is procedural, so these check the *invariants* rather than exact
coordinates: the right number of fields, evenly spaced along the road, the
finish always alone, and the tower rule behaving like the turtles it comes from.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.board.board import BoardModel, BoardTheme, make_rows
from pedzacy_piotrek.board.path import Path2D, ProximityGrid
from pedzacy_piotrek.board.tiles import TileKind
from pedzacy_piotrek.config.settings import BOARD


# ── path maths ───────────────────────────────────────────────────────────────
def test_path_arc_length_is_accurate():
    path = Path2D.from_points([(0, 0), (100, 0), (100, 100)])
    assert path.length == pytest.approx(200.0)
    assert path.point_at(50)[0] == pytest.approx(50.0)
    assert path.point_at(150)[1] == pytest.approx(50.0)


def test_path_normal_is_perpendicular():
    path = Path2D.from_points([(0, 0), (100, 0)])
    tx, ty = path.tangent_at(50)
    nx, ny = path.normal_at(50)
    assert tx * nx + ty * ny == pytest.approx(0.0, abs=1e-9)


def test_proximity_grid_finds_the_nearest_point():
    grid = ProximityGrid([(0, 0), (300, 300)], cell_size=64)
    assert grid.distance_to((0, 10)) == pytest.approx(10.0)
    assert not grid.is_clear((0, 10), clearance=20)
    assert grid.is_clear((150, 0), clearance=20)


# ── row pattern (ported behaviour) ───────────────────────────────────────────
def test_rows_always_finish_on_a_single_field():
    pattern = ("single", "double", "single", "single", "double", "single")
    for count in range(10, 80):
        rows = make_rows(count, pattern)
        assert rows[-1] == "single"
        total = sum(2 if r == "double" else 1 for r in rows)
        assert total >= count
        assert total - count <= 1


def test_too_small_a_board_is_rejected():
    with pytest.raises(ValueError):
        make_rows(9, ("single",))


# ── generated board ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def board() -> BoardModel:
    return BoardModel.generate(cell_count=24, seed=42)


def test_board_has_exactly_the_requested_number_of_fields(board):
    assert len(board.tiles) == 24
    # Numbers count board *positions*, so a doubled row spends one number on
    # two fields: 1, 2a, 2b, 3, ...
    assert [t.number for t in board.tiles] == sorted(t.number for t in board.tiles)
    assert board.tiles[0].number == 1
    assert board.tiles[-1].number == board.position_count


def test_doubled_positions_are_labelled_a_and_b(board):
    doubled = [p for p in board.positions if p.is_doubled]
    assert doubled, "this board should contain widened stretches"
    for position in doubled:
        assert [t.label for t in position.tiles] == [
            f"{position.number}a", f"{position.number}b"
        ]
        assert len({t.number for t in position.tiles}) == 1
    for position in board.positions:
        if not position.is_doubled:
            assert position.tiles[0].label == str(position.number)


def test_the_finish_is_a_single_field(board):
    from pedzacy_piotrek.board.tiles import TileKind

    last = board.positions[-1]
    assert not last.is_doubled
    assert last.tiles[0].kind is TileKind.FINISH


def test_double_frequency_controls_how_often_rows_widen():
    """The generator must let the board be configured, not just patterned."""
    none_doubled = BoardModel.generate(cell_count=40, seed=3, double_frequency=0.0)
    assert not any(p.is_doubled for p in none_doubled.positions)

    rare = BoardModel.generate(cell_count=40, seed=3, double_frequency=0.15)
    often = BoardModel.generate(cell_count=40, seed=3, double_frequency=0.85)
    rare_count = sum(1 for p in rare.positions if p.is_doubled)
    often_count = sum(1 for p in often.positions if p.is_doubled)
    assert rare_count < often_count
    for model in (none_doubled, rare, often):
        assert len(model.tiles) == 40
        assert model.verify_spacing() is None


@pytest.mark.parametrize("count", [10, 17, 24, 40, 63])
def test_field_count_holds_for_any_board_size(count):
    model = BoardModel.generate(cell_count=count, seed=3)
    assert len(model.tiles) == count
    assert model.tiles[0].kind is TileKind.START
    assert model.tiles[-1].kind in (TileKind.FINISH,)


def test_fields_are_evenly_spaced_along_the_road(board):
    """Arc-length parametrisation means spacing survives the curves."""
    arcs = sorted({t.arc for t in board.tiles})
    gaps = [b - a for a, b in zip(arcs, arcs[1:])]
    assert all(g == pytest.approx(BOARD.tile_spacing, rel=1e-6) for g in gaps)


def test_double_rows_sit_side_by_side_across_the_road(board):
    by_arc: dict[float, list] = {}
    for tile in board.tiles:
        by_arc.setdefault(tile.arc, []).append(tile)
    pairs = [tiles for tiles in by_arc.values() if len(tiles) == 2]
    assert pairs, "board should contain at least one widened row"
    for a, b in pairs:
        assert a.lateral == -b.lateral
        assert math.dist(a.position, b.position) == pytest.approx(
            2 * BOARD.lane_offset, rel=0.02
        )


def test_generation_is_deterministic_for_a_seed():
    a = BoardModel.generate(cell_count=30, seed=8)
    b = BoardModel.generate(cell_count=30, seed=8)
    assert [t.position for t in a.tiles] == [t.position for t in b.tiles]
    assert [p.position for p in a.props] == [p.position for p in b.props]
    c = BoardModel.generate(cell_count=30, seed=9)
    assert [t.position for t in a.tiles] != [t.position for t in c.tiles]


def test_scenery_keeps_clear_of_the_road(board):
    from pedzacy_piotrek.board.tiles import PropKind

    road = ProximityGrid(board.path.resample(14.0))
    for prop in board.props:
        if prop.kind in (PropKind.HILL, PropKind.GRASS):
            continue  # hills sit behind everything; grass is allowed on the verge
        assert road.distance_to(prop.position) > 20.0


@pytest.mark.parametrize("seed", [7, 11, 42, 2024])
def test_nothing_grows_inside_the_starting_camp(seed):
    """Trees were sprouting through the camp panel; only background hills may
    pass underneath it."""
    from pedzacy_piotrek.board.tiles import PropKind

    model = BoardModel.generate(cell_count=34, seed=seed)
    xs = [s.position[0] for s in model.camp_slots]
    ys = [s.position[1] for s in model.camp_slots]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half_w, half_h = (max(xs) - min(xs)) / 2 + 80, (max(ys) - min(ys)) / 2 + 90

    intruders = [
        prop for prop in model.props
        if abs(prop.position[0] - cx) < half_w
        and abs(prop.position[1] - cy) < half_h
        and prop.kind is not PropKind.HILL
    ]
    assert not intruders


def test_rivers_are_bridged_where_they_meet_the_road(board):
    for river in board.rivers:
        assert river.bridge_tile is not None
        assert board.tile(river.bridge_tile) is not None


# ── stacking (the turtle-tower rule) ─────────────────────────────────────────
def test_pawns_stack_in_arrival_order():
    model = BoardModel.generate(cell_count=12, seed=1)
    model.place_pawn("czerwony", 3)
    model.place_pawn("zielony", 3)
    model.place_pawn("żółty", 3)
    assert model.tower_of(3) == ["czerwony", "zielony", "żółty"]
    assert model.tile(3).bottom_pawn == "czerwony"
    assert model.tile(3).top_pawn == "żółty"
    assert model.stack_depth("zielony") == 1


def test_a_stacked_pawn_is_drawn_higher():
    model = BoardModel.generate(cell_count=12, seed=1)
    model.place_pawn("czerwony", 2)
    model.place_pawn("zielony", 2)
    bottom = model.pawn_position("czerwony")
    top = model.pawn_position("zielony")
    assert top[1] < bottom[1]
    assert top[0] == bottom[0]


def test_moving_a_pawn_carries_the_ones_riding_it():
    model = BoardModel.generate(cell_count=12, seed=1)
    for pawn in ("czerwony", "zielony", "żółty"):
        model.place_pawn(pawn, 4)
    assert model.carried_pawns("czerwony") == ["zielony", "żółty"]
    assert model.carried_pawns("żółty") == []


def test_leaving_a_field_removes_the_pawn_from_its_stack():
    model = BoardModel.generate(cell_count=12, seed=1)
    model.place_pawn("czerwony", 5)
    model.place_pawn("zielony", 5)
    model.place_pawn("czerwony", 6)
    assert model.tower_of(5) == ["zielony"]
    assert model.tower_of(6) == ["czerwony"]


def test_tile_near_respects_the_snap_radius():
    model = BoardModel.generate(cell_count=12, seed=1)
    tile = model.tiles[5]
    assert model.tile_near(tile.position) is tile
    # Straight out to the side of the road, where no other field can be.
    far = (tile.position[0], tile.position[1] + BOARD.snap_radius * 6)
    assert model.tile_near(far) is None


# ── the spacing guarantee (this stage's bug fix) ─────────────────────────────
@pytest.mark.parametrize("cells", [10, 13, 24, 37, 48, 63])
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_fields_never_crowd_each_other(cells, seed):
    """The generator must not emit a board with overlapping fields.

    This is the regression test for the squashed-corner boards: fields used to
    collide on tight bends because nothing bounded the curvature.
    """
    model = BoardModel.generate(cell_count=cells, seed=seed)
    assert model.verify_spacing() is None


def test_the_road_never_bends_tighter_than_the_derived_limit():
    for seed in range(8):
        model = BoardModel.generate(cell_count=40, seed=seed)
        assert model.geometry is not None
        assert model.geometry.min_radius >= BOARD.min_turn_radius


def test_the_road_never_comes_close_to_itself():
    needed = 2 * BOARD.road_half_width + BOARD.min_road_gap
    for seed in range(8):
        model = BoardModel.generate(cell_count=45, seed=seed)
        assert model.geometry.min_self_distance >= needed


def test_a_straight_road_is_always_an_acceptable_fallback():
    """Amplitude zero satisfies every constraint, so the search terminates."""
    from pedzacy_piotrek.board.board import BoardTheme

    theme = BoardTheme(wave_amplitude=100000.0, wave_length=10.0)
    model = BoardModel.generate(cell_count=30, seed=1, theme=theme)
    assert model.verify_spacing() is None


def test_the_board_runs_left_to_right():
    model = BoardModel.generate(cell_count=30, seed=2)
    assert model.width > model.height
    assert model.tiles[0].position[0] < model.tiles[-1].position[0]
    # The camp waits behind the start line, not beside the road.
    camp_x = sum(s.position[0] for s in model.camp_slots) / len(model.camp_slots)
    assert camp_x < model.tiles[0].position[0]


def test_rivers_cross_the_road_rather_than_running_along_it():
    model = BoardModel.generate(cell_count=40, seed=6)
    for river in model.rivers:
        xs = [p[0] for p in river.points]
        ys = [p[1] for p in river.points]
        assert max(ys) - min(ys) > max(xs) - min(xs)
