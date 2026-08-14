"""
Stage 40 — Ice Block, checking variant 2, and the Piotrek victory variants.

Three features that all sit around the CHECK, so most of these tests are about
ordering: the window comes before the check, the breakup comes after it, and a
check Ice Block refused produces neither an answer nor a breakup.

The window and the two-second pause are both deadlines on the authority's
clock.  Nothing here sleeps; the tests drive the same tick the server does.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine import victory
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, **kwargs):
    kwargs.setdefault("double_frequency", 0.0)
    config = SessionConfig(
        num_players=5, board_cells=30, seed=77,
        chest_open_round=10_000, mod_round_first=10_000, **kwargs,
    )
    return create_game(config, library)


@pytest.fixture
def game(library):
    return make(library)


# ── fixture helpers ──────────────────────────────────────────────────────────
def piotrek(game):
    return next(p for p in game.players if p.is_piotrek)


def give_skill(game, title: str):
    player = piotrek(game)
    deck = game.deck(settings.DECK_SKILLS)
    if player.skill is not None and player.skill.title == title:
        return player.skill
    card = deck.take_titled(title, include_discard=True)
    assert card is not None, title
    if player.skill is not None:
        deck.return_card(player.skill)
    player.skill = card
    return card


def no_ice_block(game) -> None:
    """Take Ice Block away, so a check resolves without a window."""
    player = piotrek(game)
    if player.skill is not None:
        game.deck(settings.DECK_SKILLS).return_card(player.skill)
    player.skill = None


def build_tower(game, position: int, bottom: str) -> None:
    order = [bottom] + [p.id for p in game.library.pawns if p.id != bottom]
    for pawn_id in game.library.pawns:
        game.board.remove_pawn(pawn_id.id)
    for pawn_id in order:
        game.board.place_pawn(pawn_id, game.board.positions[position].tiles[0].index)
    game._sync_token_positions()


def wrong_colour(game) -> str:
    hidden = victory.hidden_pawn(game)
    return next(p.id for p in game.library.pawns if p.id != hidden)


def settle(game):
    """Apply whatever ``review`` says, once."""
    commands = victory.review(game)
    game.apply_many(commands)
    return commands


def open_window(game, bottom: str = None):
    """Build a tower and let the Ice Block window open."""
    bottom = wrong_colour(game) if bottom is None else bottom
    build_tower(game, 6, bottom)
    settle(game)
    return bottom


# ═════════════════════════════════════════════════════════════════════════════
# PART A — Ice Block
# ═════════════════════════════════════════════════════════════════════════════
def test_a_window_opens_before_the_check_resolves(game):
    give_skill(game, "Ice Block")
    bottom = open_window(game)
    assert game.pending_check is not None
    assert game.pending_check.pawn_id == bottom
    assert game.pending_check.seat == piotrek(game).index
    assert game.eliminated_pawns == [], "nothing has been decided yet"


def test_no_window_when_piotrek_has_no_ice_block(game):
    no_ice_block(game)
    build_tower(game, 6, wrong_colour(game))
    commands = settle(game)
    assert game.pending_check is None
    assert [c.kind for c in commands] == ["eliminate_pawn"]


def test_no_window_once_the_uses_are_gone(game):
    skill = give_skill(game, "Ice Block")
    skill.uses_left = 0
    build_tower(game, 6, wrong_colour(game))
    settle(game)
    assert game.pending_check is None
    assert game.eliminated_pawns, "the check simply happened"


def test_the_window_defaults_to_ten_seconds(game):
    give_skill(game, "Ice Block")
    open_window(game)
    assert game.pending_check.seconds == 10.0
    assert RULES.check_decision_default == 10


def test_the_window_length_is_configurable(library):
    game = make(library, check_decision_seconds=4)
    give_skill(game, "Ice Block")
    open_window(game)
    assert game.pending_check.seconds == 4.0


def test_allowing_lets_the_check_proceed(game):
    give_skill(game, "Ice Block")
    bottom = open_window(game)
    game.apply(cmd.AllowCheck(player_index=piotrek(game).index))
    assert game.pending_check is None
    settle(game)
    assert bottom in game.eliminated_pawns


def test_allowing_does_not_spend_a_use(game):
    skill = give_skill(game, "Ice Block")
    open_window(game)
    game.apply(cmd.AllowCheck(player_index=piotrek(game).index))
    assert skill.uses_left == 1


def test_refusing_cancels_the_check(game):
    give_skill(game, "Ice Block")
    bottom = open_window(game)
    events = game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    assert any(isinstance(e, ev.CheckRefused) for e in events)
    assert game.pending_check is None
    assert bottom not in game.eliminated_pawns, "nothing was crossed off"
    assert game.victory is None


def test_refusing_spends_exactly_one_use(game):
    skill = give_skill(game, "Ice Block")
    open_window(game)
    game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    assert skill.uses_left == 0


def test_refusing_reveals_nothing(game):
    """A refused check is not answered, so there is no answer to leak."""
    give_skill(game, "Ice Block")
    open_window(game)
    events = game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    hidden = victory.hidden_pawn(game)
    assert not any(getattr(e, "pawn_id", None) == hidden for e in events)
    assert not any(isinstance(e, ev.IdentityRevealed) for e in events)


def test_the_same_tower_is_not_re_checked_after_a_refusal(game):
    """The card's own text: the pawns must be separated first."""
    give_skill(game, "Ice Block")
    open_window(game)
    game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    assert game.check_needs_separation
    assert victory.review(game) == [], "the intact tower proves nothing now"


def test_separating_the_pawns_arms_checking_again(game):
    give_skill(game, "Ice Block")
    open_window(game)
    game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))

    loose = game.library.pawns[0].id
    game.board.remove_pawn(loose)
    game.board.place_pawn(loose, game.board.positions[3].tiles[0].index)
    game._sync_token_positions()
    assert not game.check_needs_separation


def test_refusing_with_no_uses_left_is_rejected(game):
    skill = give_skill(game, "Ice Block")
    open_window(game)
    skill.uses_left = 0
    events = game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    assert any(isinstance(e, ev.ActionRejected) for e in events)


def test_only_piotrek_may_answer(game):
    give_skill(game, "Ice Block")
    open_window(game)
    hunter = next(p.index for p in game.players if not p.is_piotrek)
    for command in (cmd.RefuseCheck(player_index=hunter),
                    cmd.AllowCheck(player_index=hunter)):
        events = game.apply(command)
        assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert game.pending_check is not None, "still waiting on Piotrek"


def test_nothing_else_is_decided_while_the_window_is_open(game):
    """Not even a victory: an unanswered question holds the table."""
    give_skill(game, "Ice Block")
    open_window(game)
    hidden = victory.hidden_pawn(game)
    game.board.remove_pawn(hidden)
    game.board.place_pawn(hidden, game.board.positions[
        game.board.last_position].tiles[0].index)
    assert victory.review(game) == []


# ── the timeout, driven the way the host drives it ───────────────────────────
def test_the_window_times_out_into_the_check(library):
    game = make(library, check_decision_seconds=3)
    skill = give_skill(game, "Ice Block")
    session = LocalSession(game)
    bottom = open_window(game)

    assert session.tick(now=100.0) == [], "the clock starts on the first tick"
    assert session.tick(now=102.0) == [], "not yet"
    session.tick(now=104.0)
    assert game.pending_check is None
    assert skill.uses_left == 1, "a timeout costs nothing"
    settle(game)
    assert bottom in game.eliminated_pawns


def test_a_late_refusal_after_the_deadline_does_nothing(library):
    """§2: a stale client must not block a check after the deadline."""
    game = make(library, check_decision_seconds=3)
    skill = give_skill(game, "Ice Block")
    session = LocalSession(game)
    open_window(game)
    session.tick(now=100.0)
    session.tick(now=104.0)

    events = game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert skill.uses_left == 1


def test_the_window_is_authoritative_state(game):
    give_skill(game, "Ice Block")
    open_window(game)
    assert game.snapshot()["pending_check"] is not None
    assert cmd.OpenCheckDecision() .__class__ in cmd.AUTHORITY_ONLY
    assert cmd.ExpireCheckDecision().__class__ in cmd.AUTHORITY_ONLY


# ═════════════════════════════════════════════════════════════════════════════
# PART C — checking variant 2: the tower breaks up
# ═════════════════════════════════════════════════════════════════════════════
def group_at(pending, position):
    """The pawns of the group heading for ``position``.

    Not "the last group" any more: with the scatter centred on the tower, the
    doubled row can be the field BEFORE it as easily as the one after.
    """
    return next(list(pawns) for spot, pawns in pending.groups if spot == position)


def broken(library, **kwargs):
    game = make(library, check_variant="break_tower", **kwargs)
    no_ice_block(game)
    return game


def test_variant_1_leaves_the_tower_alone(game):
    """The default is unchanged: a failed check breaks nothing."""
    no_ice_block(game)
    build_tower(game, 6, wrong_colour(game))
    settle(game)
    assert game.pending_breakup is None
    assert all(game.board.position_of_pawn(p.id) == 6 for p in game.library.pawns)


def test_a_successful_check_does_not_break_the_tower(library):
    """Finding Piotrek ends the match; there is nothing left to scatter."""
    game = broken(library)
    build_tower(game, 6, victory.hidden_pawn(game))
    settle(game)
    assert game.pending_breakup is None
    assert game.victory is not None
    assert game.victory.outcome is victory.Outcome.HUNTERS


def test_a_failed_check_arms_the_breakup(library):
    game = broken(library)
    build_tower(game, 6, wrong_colour(game))
    settle(game)
    assert game.pending_breakup is not None
    assert game.pending_breakup.seconds == RULES.tower_breakup_seconds == 2.0


def test_the_breakup_waits_about_two_seconds(library):
    game = broken(library)
    session = LocalSession(game)
    build_tower(game, 6, wrong_colour(game))
    settle(game)

    session.tick(now=50.0)
    assert game.pending_breakup is not None
    session.tick(now=51.0)
    assert game.pending_breakup is not None, "not yet"
    assert all(game.board.position_of_pawn(p.id) == 6 for p in game.library.pawns)
    session.tick(now=52.5)
    assert game.pending_breakup is None, "and now it has"


def test_a_six_pawn_tower_breaks_into_three_pairs(library):
    game = broken(library)
    build_tower(game, 7, wrong_colour(game))
    tower = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    assert len(tower) == 6
    settle(game)
    game.apply(cmd.ResolveTowerBreakup())

    # CENTRED ON THE TOWER'S OWN FIELD.  The bottom pair does not move, the
    # middle pair takes the field immediately BEFORE and the top pair the field
    # immediately AFTER — a tower on 7 ends up on 6, 7 and 8, never on 5.
    assert game.board.position_of_pawn(tower[0]) == 7
    assert game.board.position_of_pawn(tower[1]) == 7
    assert game.board.position_of_pawn(tower[2]) == 6
    assert game.board.position_of_pawn(tower[3]) == 6
    assert game.board.position_of_pawn(tower[4]) == 8
    assert game.board.position_of_pawn(tower[5]) == 8
    assert 5 not in {game.board.position_of_pawn(p) for p in tower}, (
        "nothing walks two fields back any more"
    )


def test_each_pairs_internal_order_is_preserved(library):
    game = broken(library)
    build_tower(game, 7, wrong_colour(game))
    tower = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    settle(game)
    game.apply(cmd.ResolveTowerBreakup())

    for lower, upper in ((0, 1), (2, 3), (4, 5)):
        stack = list(game.board.pawn_tile(tower[lower]).stack)
        assert stack.index(tower[lower]) < stack.index(tower[upper])


def test_a_five_pawn_tower_sends_the_last_pawn_alone(library):
    """§9: Obóz Harcerski is holding one off the map.

    Hidden through the real mechanism, not just lifted off the board: checking
    counts against the pawns that are ON THE TABLE, so a pawn merely removed
    would leave the tower incomplete and no check would happen at all.
    """
    from pedzacy_piotrek.engine.statuses import Status, StatusKind

    game = broken(library)
    build_tower(game, 7, wrong_colour(game))
    full = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    absent = full[-1]                       # the top pawn goes away
    game.statuses.add(Status.for_pawn(StatusKind.HIDDEN, absent, source="test"))
    game.board.remove_pawn(absent)
    game._sync_token_positions()

    tower = list(game.board.pawn_tile(full[0]).stack)
    assert len(tower) == 5 and absent not in tower

    settle(game)
    game.apply(cmd.ResolveTowerBreakup())
    assert game.board.position_of_pawn(tower[0]) == 7
    assert game.board.position_of_pawn(tower[1]) == 7
    assert game.board.position_of_pawn(tower[2]) == 6
    assert game.board.position_of_pawn(tower[3]) == 6
    assert game.board.position_of_pawn(tower[4]) == 8
    assert list(game.board.pawn_tile(tower[4]).stack) == [tower[4]], "alone"


def test_the_pairing_rule_alone():
    """The geometry as a pure function, where a failure is readable."""
    assert effects.tower_pairs(list("abcdef")) == [["a", "b"], ["c", "d"],
                                                   ["e", "f"]]
    assert effects.tower_pairs(list("abcde")) == [["a", "b"], ["c", "d"], ["e"]]
    assert effects.tower_pairs(list("ab")) == [["a", "b"]]


def test_piotrek_chooses_which_field_of_a_doubled_row(library):
    game = broken(library, double_frequency=1.0)
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    pending = game.pending_breakup
    assert pending.choice_position is not None, "a doubled row is involved"
    assert pending.seat == piotrek(game).index

    tiles = game.board.tiles_at_position(pending.choice_position)
    assert len(tiles) == 2, "2a and 2b"
    events = game.apply(cmd.ChooseBreakupTile(player_index=pending.seat,
                                              tile_index=tiles[1].index))
    assert any(isinstance(e, ev.BreakupTileChosen) for e in events)
    game.apply(cmd.ResolveTowerBreakup())
    for pawn_id in group_at(pending, pending.choice_position):
        assert game.board.pawn_tiles[pawn_id] == tiles[1].index


def test_the_card_player_cannot_make_that_choice(library):
    """§7: the choice belongs to Piotrek, not to whoever built the tower."""
    game = broken(library, double_frequency=1.0)
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    pending = game.pending_breakup
    tiles = game.board.tiles_at_position(pending.choice_position)
    hunter = next(p.index for p in game.players if not p.is_piotrek)

    events = game.apply(cmd.ChooseBreakupTile(player_index=hunter,
                                              tile_index=tiles[1].index))
    assert any(isinstance(e, ev.ActionRejected) for e in events)
    assert pending.chosen_tile is None


def test_no_choice_before_the_deadline_uses_the_first_field(library):
    """A disconnected Piotrek must not hang the table."""
    game = broken(library, double_frequency=1.0)
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    pending = game.pending_breakup
    tiles = game.board.tiles_at_position(pending.choice_position)
    group = group_at(pending, pending.choice_position)

    game.apply(cmd.ResolveTowerBreakup())
    for pawn_id in group:
        assert game.board.pawn_tiles[pawn_id] == tiles[0].index


# ── §10: Ice Block happens BEFORE the check, so a refusal stops the breakup ──
def test_a_refused_check_still_breaks_the_tower(library):
    """Ice Block stops the CHECK, not the consequence of attempting one.

    The distinction is the point: nobody learns anything — no colour is crossed
    off and no identity is compared — and the tower comes apart anyway, on the
    ordinary delay.
    """
    game = make(library, check_variant="break_tower")
    skill = give_skill(game, "Ice Block")
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    assert game.pending_check is not None

    game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))
    assert game.eliminated_pawns == [], "nothing was ruled out"
    assert skill.uses_left == 0, "and the use was spent"
    assert game.pending_breakup is not None, "but the tower is coming apart"
    assert game.pending_breakup.seconds == RULES.tower_breakup_seconds

    game.apply(cmd.ResolveTowerBreakup())
    assert len({game.board.position_of_pawn(p.id)
                for p in game.library.pawns}) == 3


def test_a_refused_check_under_variant_1_breaks_nothing(library):
    """Variant 1 has no breakup at all, so there is none to inherit."""
    game = make(library, check_variant="continue")
    skill = give_skill(game, "Ice Block")
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    game.apply(cmd.RefuseCheck(player_index=piotrek(game).index))

    assert skill.uses_left == 0
    assert game.pending_breakup is None
    assert all(game.board.position_of_pawn(p.id) == 7
               for p in game.library.pawns), "the tower still stands"


def test_an_allowed_check_still_breaks_the_tower(library):
    game = make(library, check_variant="break_tower")
    give_skill(game, "Ice Block")
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    game.apply(cmd.AllowCheck(player_index=piotrek(game).index))
    settle(game)
    assert game.pending_breakup is not None


# ═════════════════════════════════════════════════════════════════════════════
# PART D — the victory variants
# ═════════════════════════════════════════════════════════════════════════════
def finish_line(game) -> int:
    return game.board.positions[game.board.last_position].tiles[0].index


def test_variant_1_needs_piotreks_own_pawn(game):
    hidden = victory.hidden_pawn(game)
    other = next(p.id for p in game.library.pawns if p.id != hidden)
    game.board.remove_pawn(other)
    game.board.place_pawn(other, finish_line(game))
    game._sync_token_positions()
    assert victory.review(game) == [], "somebody else's pawn wins nothing"

    game.board.remove_pawn(hidden)
    game.board.place_pawn(hidden, finish_line(game))
    game._sync_token_positions()
    followed = victory.review(game)
    assert [c.kind for c in followed] == ["declare_victory"]
    assert followed[0].outcome == victory.Outcome.PIOTREK.value


def test_variant_2_lets_any_pawn_win_it(library):
    game = make(library, victory_variant="any_pawn")
    hidden = victory.hidden_pawn(game)
    other = next(p.id for p in game.library.pawns if p.id != hidden)
    game.board.remove_pawn(other)
    game.board.place_pawn(other, finish_line(game))
    game._sync_token_positions()

    followed = victory.review(game)
    assert [c.kind for c in followed] == ["declare_victory"]
    assert followed[0].outcome == victory.Outcome.PIOTREK.value


def test_variant_2_still_names_the_hidden_colour_in_the_verdict(library):
    """§11: the victory condition changes and nothing else does."""
    game = make(library, victory_variant="any_pawn")
    hidden = victory.hidden_pawn(game)
    other = next(p.id for p in game.library.pawns if p.id != hidden)
    game.board.remove_pawn(other)
    game.board.place_pawn(other, finish_line(game))
    game._sync_token_positions()

    followed = victory.review(game)
    assert followed[0].pawn_id == hidden, "the reveal is still about Piotrek"
    game.apply_many(followed)
    assert game.victory.pawn_id == hidden


def test_variant_2_does_not_change_who_piotrek_is_or_how_checks_work(library):
    game = make(library, victory_variant="any_pawn")
    no_ice_block(game)
    hidden = victory.hidden_pawn(game)
    before = {p.index: p.secret_pawn for p in game.players}
    wrong = next(p.id for p in game.library.pawns if p.id != hidden)
    build_tower(game, 6, wrong)
    settle(game)

    assert {p.index: p.secret_pawn for p in game.players} == before
    assert wrong in game.eliminated_pawns, "checking is untouched"
    assert victory.hidden_pawn(game) == hidden


def test_the_defaults_preserve_the_old_game(game):
    assert game.check_variant == "continue"
    assert game.victory_variant == "own_pawn"
    assert settings.CHECK_VARIANTS and settings.VICTORY_VARIANTS


# ═════════════════════════════════════════════════════════════════════════════
# The interface and the lobby (stage 40b)
# ═════════════════════════════════════════════════════════════════════════════
def test_the_panel_only_shows_itself_to_piotrek(game):
    """The interface agrees with the rule; the engine is where it is enforced."""
    from pedzacy_piotrek.ui.check_decision import CheckDecision

    give_skill(game, "Ice Block")
    open_window(game)
    seat = piotrek(game).index
    hunter = next(p.index for p in game.players if not p.is_piotrek)

    assert CheckDecision(game, seat=lambda: seat).active
    assert not CheckDecision(game, seat=lambda: hunter).active


def test_the_panel_produces_commands_and_nothing_else(game):
    """Both buttons return a Command; the engine does the work."""
    import pygame
    from pedzacy_piotrek.ui.check_decision import CheckDecision
    from pedzacy_piotrek.ui.layout import Layout

    give_skill(game, "Ice Block")
    open_window(game)
    seat = piotrek(game).index
    panel = CheckDecision(game, seat=lambda: seat)
    layout = Layout(1280, 760)
    refuse, allow = layout.check_decision_buttons()

    def click(point):
        return panel.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=point),
            point, layout)

    # Refusing asks first: one click opens the confirmation and spends nothing.
    assert click(refuse.center) is None
    assert panel.confirming
    assert game.pending_check is not None, "the engine has not been told yet"

    confirm, _ = layout.movement_confirm_buttons()
    command = click(confirm.center)
    assert isinstance(command, cmd.RefuseCheck)
    assert command.player_index == seat

    panel.confirming = False
    assert isinstance(click(allow.center), cmd.AllowCheck)


def test_the_countdown_is_a_picture_not_the_deadline(library):
    """A client's clock reaching zero does not end anybody's window."""
    from pedzacy_piotrek.ui.check_decision import CheckDecision
    from pedzacy_piotrek.ui.layout import Layout

    game = make(library, check_decision_seconds=3)
    give_skill(game, "Ice Block")
    open_window(game)
    seat = piotrek(game).index
    panel = CheckDecision(game, seat=lambda: seat)
    layout = Layout(1280, 760)

    for _ in range(60):
        panel.update(0.1, layout, (0, 0))
    assert panel.left == 0.0, "the drawn countdown ran out"
    assert game.pending_check is not None, "but the window is still open"


def test_the_scatter_choice_is_offered_to_piotrek_only(library):
    from pedzacy_piotrek.ui.check_decision import BreakupChoice

    game = broken(library, double_frequency=1.0)
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    seat = game.pending_breakup.seat
    hunter = next(p.index for p in game.players if not p.is_piotrek)

    assert BreakupChoice(game, seat=lambda: seat).active
    assert not BreakupChoice(game, seat=lambda: hunter).active


def test_the_scatter_choice_names_the_two_fields(library):
    import pygame
    from pedzacy_piotrek.ui.check_decision import BreakupChoice
    from pedzacy_piotrek.ui.layout import Layout

    game = broken(library, double_frequency=1.0)
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    seat = game.pending_breakup.seat
    panel = BreakupChoice(game, seat=lambda: seat)
    layout = Layout(1280, 760)

    tiles = panel.tiles()
    assert len(tiles) == 2
    rect = layout.breakup_choice_buttons()[1]
    command = panel.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center),
        rect.center, layout)
    assert isinstance(command, cmd.ChooseBreakupTile)
    assert command.tile_index == tiles[1].index
    assert command.player_index == seat


def test_the_scatter_choice_goes_away_once_answered(library):
    from pedzacy_piotrek.ui.check_decision import BreakupChoice

    game = broken(library, double_frequency=1.0)
    build_tower(game, 7, wrong_colour(game))
    settle(game)
    seat = game.pending_breakup.seat
    panel = BreakupChoice(game, seat=lambda: seat)
    tiles = panel.tiles()

    game.apply(cmd.ChooseBreakupTile(player_index=seat,
                                     tile_index=tiles[0].index))
    assert not panel.active


def test_the_lobby_carries_both_variants(library):
    from pedzacy_piotrek.net.lobby import LobbyState

    lobby = LobbyState(code="ABCD")
    lobby.check_variant = "break_tower"
    lobby.victory_variant = "any_pawn"
    lobby.check_decision_seconds = 6

    mirrored = LobbyState.from_dict(lobby.to_dict())
    assert mirrored.check_variant == "break_tower"
    assert mirrored.victory_variant == "any_pawn"
    assert mirrored.check_decision_seconds == 6


def test_an_unknown_variant_id_falls_back_to_the_default(library):
    """An older or newer build must not be able to crash the lobby."""
    from pedzacy_piotrek.net.lobby import LobbyState

    raw = LobbyState(code="ABCD").to_dict()
    raw["check_variant"] = "something_from_the_future"
    raw["victory_variant"] = ""
    mirrored = LobbyState.from_dict(raw)
    assert mirrored.check_variant == "continue"
    assert mirrored.victory_variant == "own_pawn"


def rules_panel(library):
    """A real settings panel over a headless app, as the menu builds it."""
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    return GameSettingsPanel(App(Layout(), headless=True), library)


def test_the_settings_panel_offers_both_variants(library):
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    panel = rules_panel(library)
    assert panel.check_variant == "continue", "the default is the old game"
    assert panel.victory_variant == "own_pawn"
    assert panel.check_decision_seconds == RULES.check_decision_default

    tab = panel.tabs[panel._index_of("rules")]
    tab.bump(GameSettingsPanel.CHECK_VARIANT_ROW, 1)
    tab.bump(GameSettingsPanel.VICTORY_VARIANT_ROW, 1)
    assert panel.check_variant == "break_tower"
    assert panel.victory_variant == "any_pawn"


def test_the_panel_choices_reach_a_session_config(library):
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    panel = rules_panel(library)
    tab = panel.tabs[panel._index_of("rules")]
    tab.bump(GameSettingsPanel.CHECK_VARIANT_ROW, 1)
    tab.bump(GameSettingsPanel.VICTORY_VARIANT_ROW, 1)

    config = SessionConfig(
        num_players=5,
        check_variant=panel.check_variant,
        victory_variant=panel.victory_variant,
        check_decision_seconds=panel.check_decision_seconds,
    ).normalised()
    assert config.check_variant == "break_tower"
    assert config.victory_variant == "any_pawn"
    assert config.check_decision_seconds == 10


def test_a_nonsense_config_value_normalises_to_the_default(library):
    config = SessionConfig(num_players=5, check_variant="nope",
                           victory_variant="also nope").normalised()
    assert config.check_variant == "continue"
    assert config.victory_variant == "own_pawn"


# ═════════════════════════════════════════════════════════════════════════════
# Stage 40a — the four bugs the screenshots exposed
# ═════════════════════════════════════════════════════════════════════════════
def rules_tab(library):
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.ui.settings_panel import GameSettingsPanel

    panel = GameSettingsPanel(App(Layout(), headless=True, size=(1920, 1080)),
                              library)
    return panel, panel.tabs[panel._index_of("rules")]


def test_a_variant_row_is_bounded_by_its_own_list(library):
    """The -1/+1 bug: the row was clamped to the TAB's numbers.

    The rules tab mixes a 1..30 timer with a 0..1 variant, so clamping the
    variant to the timer's bounds meant index 1 minus one landed on 0, which
    was below ``low`` of 1, and snapped straight back — "Wariant 2, then -1"
    sat still.  It also let +1 walk the stored index up to 30.
    """
    panel, tab = rules_tab(library)
    for row in (panel.CHECK_VARIANT_ROW, panel.VICTORY_VARIANT_ROW):
        assert tab.bounds_for(row) == (0, 1), row
    assert tab.bounds_for(panel.BLOCK_ROW) == (tab.low, tab.high)


@pytest.mark.parametrize("row_attr", ["CHECK_VARIANT_ROW", "VICTORY_VARIANT_ROW"])
def test_a_variant_selector_walks_both_ways(library, row_attr):
    panel, tab = rules_tab(library)
    row = getattr(panel, row_attr)
    first = tab.chosen(row)[0]

    tab.bump(row, +1)
    second = tab.chosen(row)[0]
    assert second != first, "+1 moved to the other variant"
    tab.bump(row, +1)
    assert tab.chosen(row)[0] == second, "and cannot go past the last one"
    tab.bump(row, -1)
    assert tab.chosen(row)[0] == first, "-1 comes BACK — the reported bug"
    tab.bump(row, -1)
    assert tab.chosen(row)[0] == first, "and cannot go below the first"


def test_the_timer_rows_still_count_normally(library):
    """The per-row bounds must not disturb a quantity row."""
    panel, tab = rules_tab(library)
    tab.values[panel.BLOCK_ROW] = 7
    tab.bump(panel.BLOCK_ROW, -1)
    assert tab.values[panel.BLOCK_ROW] == 6
    for _ in range(60):
        tab.bump(panel.BLOCK_ROW, +1)
    assert tab.values[panel.BLOCK_ROW] == RULES.block_decision_max


@pytest.mark.parametrize("row_attr", ["CHECK_VARIANT_ROW", "VICTORY_VARIANT_ROW"])
def test_the_stepper_well_holds_only_the_variant_name(library, row_attr):
    """Screenshot 1: a whole sentence in the well, spilling over both buttons.

    The well is a small box between -1 and +1.  It holds the NAME; the sentence
    belongs on the row's help line, which is where every choice tab already
    puts it.
    """
    panel, tab = rules_tab(library)
    row = getattr(panel, row_attr)
    for index in (0, 1):
        tab.values[row] = index
        well = tab.value_text(row)
        assert well in ("Wariant 1", "Wariant 2"), well
        description = tab.chosen(row)[2]
        assert description and description != well, "and it is still available"
        assert "Wariant" not in description, "the help line does not repeat it"


def test_the_well_text_actually_fits_between_the_buttons(library):
    """Measured, not asserted about: the old strings really did overlap."""
    import pygame
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.ui.widgets import Stepper

    app = App(Layout(), headless=True, size=(1920, 1080))
    r = app.renderer
    stepper = Stepper(900, 100, button_w=30, value_w=44, gap=5, r=r)
    well, minus, plus = (stepper.rects["value"], stepper.rects["minus1"],
                         stepper.rects["plus1"])
    r.begin(app.canvas)
    for text in ("Wariant 1", "Wariant 2"):
        drawn = r.fit_text(text, well, (255, 255, 255), app.canvas,
                           base_size=Stepper.LABEL_SIZE, padding=6)
        assert drawn.left >= minus.right, text
        assert drawn.right <= plus.left, text


def test_the_bottom_pair_stays_on_the_towers_own_field(library):
    """The geometry, corrected against the board screenshot.

    A six-pawn tower ends up on its own field plus the two behind it, with the
    TOP pair furthest back — that is the pair which reaches the doubled row
    Piotrek then picks a field on.
    """
    game = broken(library)
    build_tower(game, 7, wrong_colour(game))
    tower = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    settle(game)
    assert [position for position, _ in game.pending_breakup.groups] == [7, 6, 8]
    game.apply(cmd.ResolveTowerBreakup())
    assert game.board.position_of_pawn(tower[0]) == 7, "the bottom pair stayed"


def test_the_board_redraws_every_scattered_pawn_at_once(library):
    """Screenshot 2: the ghost tower.

    The breakup places pawns rather than walking them, so neither TokenWalked
    nor TokenMoved was emitted — and those two were the only things that wrote
    ``board_view.visual``.  The engine had every pawn on its new field and the
    x2 badges (which count the authoritative stack) moved immediately, while
    the pawns stayed drawn in a tower on the old field.
    """
    import math as _math

    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.net.session import LocalSession

    game = broken(library, double_frequency=1.0)
    app = App(Layout(), headless=True, size=(1920, 1080))
    screen = GameScreen(app, LocalSession(game))
    app.push(screen)

    build_tower(game, 7, wrong_colour(game))
    pawns = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    for pawn_id in pawns:
        screen.board_view.visual[pawn_id] = game.tokens[pawn_id].position

    settle(game)
    # THROUGH THE SESSION, the way the running game does it: the two-second
    # deadline is aged by the host's tick and the resulting events are what
    # reach the board's bus.  Applying the command straight to the state would
    # move the pawns and tell the view nothing, which is the bug rather than a
    # way of testing it.
    screen.session.tick(now=100.0)
    screen.session.tick(now=103.0)
    if game.pending_breakup is not None:
        # See the note in the Ice Block test below: the screen ticks the
        # session from the real clock too, so step past whatever deadline was
        # actually recorded rather than a made-up one.
        pending = game.pending_breakup
        screen.session.tick(now=pending.opened_at + pending.seconds + 0.5)
    assert game.pending_breakup is None, "the deadline fired"
    for _ in range(120):
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)

    for pawn_id in pawns:
        drawn = screen.board_view.display_position(pawn_id)
        assert _math.dist(drawn, game.tokens[pawn_id].position) < 1.0, (
            f"{pawn_id} is drawn where the engine no longer says it is"
        )
    fields = {game.board.position_of_pawn(p) for p in pawns}
    assert len(fields) == 3, "and they really are on three different fields"


def test_the_confirmation_dialog_actually_draws(library):
    """It crashed on a theme colour that does not exist.

    Nothing reached it: the panel tests drove the buttons and the engine, and
    the dialog is only painted once a refusal has been started.  Driving the
    real screen found it in one click.
    """
    import pygame

    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.net.session import LocalSession

    game = make(library)
    give_skill(game, "Ice Block")
    app = App(Layout(), headless=True, size=(1920, 1080))
    screen = GameScreen(app, LocalSession(game))
    app.push(screen)
    screen.view_seat = piotrek(game).index
    build_tower(game, 6, wrong_colour(game))
    settle(game)

    def draw():
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)

    draw()
    assert screen.check_decision.active
    refuse, _ = app.layout.check_decision_buttons()
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        screen.handle_event(pygame.event.Event(kind, pos=refuse.center, button=1),
                            refuse.center)
    assert screen.check_decision.confirming
    draw()          # the line that used to raise AttributeError


# ═════════════════════════════════════════════════════════════════════════════
# Stage 40b — the scatter is centred, and Ice Block does not stop it
# ═════════════════════════════════════════════════════════════════════════════
def test_the_offsets_expand_outwards_from_the_tower():
    """The rule as a pure function: stay, back one, forward one, then wider."""
    assert effects.breakup_offsets(1) == [0]
    assert effects.breakup_offsets(2) == [0, -1]
    assert effects.breakup_offsets(3) == [0, -1, 1]
    assert effects.breakup_offsets(4) == [0, -1, 1, -2]


def test_the_destinations_surround_the_towers_field(library):
    """The brief's example: a tower on 4 scatters onto 3, 4 and 5."""
    game = broken(library)
    assert effects.breakup_positions(game, 4, 3) == [4, 3, 5]
    assert effects.breakup_positions(game, 4, 2) == [4, 3]
    assert effects.breakup_positions(game, 4, 1) == [4]


def test_a_group_with_nowhere_to_go_stays_on_the_towers_field(library):
    """The first field has no predecessor; nothing is invented for it."""
    game = broken(library)
    assert effects.breakup_positions(game, 1, 3) == [1, 1, 2]
    last = game.board.last_position
    assert effects.breakup_positions(game, last, 2) == [last, last - 1]


def test_nothing_ever_lands_two_fields_back(library):
    """The reported bug: the top group walking back to field 2 from a tower on 4."""
    game = broken(library)
    build_tower(game, 4, wrong_colour(game))
    settle(game)
    positions = [position for position, _ in game.pending_breakup.groups]
    assert positions == [4, 3, 5]
    assert 2 not in positions


def test_an_odd_tower_scatters_the_same_way(library):
    """Five pawns: two, two, and a lone pawn on the field after the tower."""
    from pedzacy_piotrek.engine.statuses import Status, StatusKind

    game = broken(library)
    build_tower(game, 6, wrong_colour(game))
    full = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    absent = full[-1]
    game.statuses.add(Status.for_pawn(StatusKind.HIDDEN, absent, source="test"))
    game.board.remove_pawn(absent)
    game._sync_token_positions()
    tower = list(game.board.pawn_tile(full[0]).stack)
    assert len(tower) == 5

    settle(game)
    game.apply(cmd.ResolveTowerBreakup())
    assert game.board.position_of_pawn(tower[0]) == 6
    assert game.board.position_of_pawn(tower[2]) == 5
    assert game.board.position_of_pawn(tower[4]) == 7
    assert list(game.board.pawn_tile(tower[4]).stack) == [tower[4]]


def test_the_bottom_group_keeps_the_exact_half_it_stood_on(library):
    """A tower on 4b must not shuffle across to 4a on its way to standing still."""
    game = broken(library, double_frequency=1.0)
    tiles = game.board.tiles_at_position(6)
    assert len(tiles) == 2, "a doubled row"
    bottom = wrong_colour(game)
    order = [bottom] + [p.id for p in game.library.pawns if p.id != bottom]
    for pawn in game.library.pawns:
        game.board.remove_pawn(pawn.id)
    for pawn_id in order:
        game.board.place_pawn(pawn_id, tiles[1].index)     # the "b" half
    game._sync_token_positions()

    settle(game)
    game.apply(cmd.ResolveTowerBreakup())
    assert game.board.pawn_tiles[order[0]] == tiles[1].index
    assert game.board.pawn_tiles[order[1]] == tiles[1].index


def test_the_choice_is_never_asked_about_the_group_that_did_not_move(library):
    """The bottom group keeps its tile, so there is nothing to decide about it."""
    game = broken(library, double_frequency=1.0)
    tiles = game.board.tiles_at_position(6)
    build_tower(game, 6, wrong_colour(game))
    settle(game)
    pending = game.pending_breakup
    assert pending.choice_position != 6, "not the tower's own row"
    assert len(game.board.tiles_at_position(pending.choice_position)) > 1


def test_ice_block_then_breakup_leaves_the_board_and_screen_agreeing(library):
    """The whole interaction, end to end, through the real screen and session."""
    import math as _math

    import pygame

    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.net.session import LocalSession

    game = make(library, check_variant="break_tower")
    skill = give_skill(game, "Ice Block")
    app = App(Layout(), headless=True, size=(1920, 1080))
    screen = GameScreen(app, LocalSession(game))
    app.push(screen)
    screen.view_seat = piotrek(game).index

    build_tower(game, 7, wrong_colour(game))
    pawns = list(game.board.pawn_tile(game.library.pawns[0].id).stack)
    for pawn_id in pawns:
        screen.board_view.visual[pawn_id] = game.tokens[pawn_id].position

    def draw():
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)

    settle(game)
    draw()
    assert screen.check_decision.active

    refuse, _ = app.layout.check_decision_buttons()
    confirm, _ = app.layout.movement_confirm_buttons()
    for rect in (refuse, confirm):
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            screen.handle_event(
                pygame.event.Event(kind, pos=rect.center, button=1), rect.center)
        draw()

    assert skill.uses_left == 0
    assert game.eliminated_pawns == [], "nothing was revealed"
    assert game.pending_breakup is not None, "and the tower is still coming apart"

    screen.session.tick(now=200.0)
    screen.session.tick(now=203.0)
    if game.pending_breakup is not None:
        # The SCREEN also ticks the session, from the real clock, so by the
        # time the test gets here ``opened_at`` may already have been stamped
        # with a monotonic timestamp — and a fabricated ``now`` of 200 is then
        # in the past, so the deadline never passes.  Read the deadline the
        # session actually recorded and step past THAT.  (This is a harness
        # trap, not a game bug: mixing a made-up clock with a real one is what
        # made an earlier manual run look as though the breakup had stalled.)
        pending = game.pending_breakup
        screen.session.tick(now=pending.opened_at + pending.seconds + 0.5)
    assert game.pending_breakup is None
    for _ in range(120):
        draw()

    for pawn_id in pawns:
        assert _math.dist(screen.board_view.display_position(pawn_id),
                          game.tokens[pawn_id].position) < 1.0, pawn_id
    assert {game.board.position_of_pawn(p) for p in pawns} == {6, 7, 8}
