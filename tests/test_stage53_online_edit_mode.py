"""
EDITING IS A PROPERTY OF THE TABLE, and the board can be started again.

Two things, one authorisation model.

The Card Library has always been able to reshape a deck, hand out a named card,
grant charges and wind the round on, and the button that opens it is drawn
unconditionally.  ``authorise_remote`` gated on ``AUTHORITY_ONLY``, on
``player_index == seat`` and on ``_TURN_BOUND``; those commands are in none of
the three, so until stage 53 an IDLE CLIENT IN AN ORDINARY MATCH could issue
every one of them, on somebody else's turn, and have it accepted, logged and
broadcast.  Nothing desynced — it was an authorisation hole, not a state bug,
which is why no fingerprint ever complained about it.

``EDITOR_ONLY`` names those commands and ``edit_mode`` — a host-only lobby
setting that travels in ``SessionConfig`` — decides whether they are allowed.
``ResetBoard`` is the new one and is gated with them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from netkit import Table, replay_fingerprint
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.statuses import Status, StatusKind, Subject
from pedzacy_piotrek.engine.victory import MatchPhase
from pedzacy_piotrek.net.lobby import LobbyState
from pedzacy_piotrek.net.protocol import fingerprint_of


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library):
    made = Table(library)
    yield made
    made.close()


def seats(table, host, clients):
    room = table.room(host.room_code)
    by_seat = table.by_seat(host, clients)
    active = room.state.active_player_index
    idle = next(s for s in by_seat if s != active)
    return room, by_seat, active, idle


def gated_commands(state, seat):
    """One of each command in ``EDITOR_ONLY``, built against a live table.

    The line is ADVANTAGE, not "is it an edit": each of these hands somebody
    something private or moves the match on.
    """
    deck = state.decks["movement"]
    return [
        ("DrawTitledCard", cmd.DrawTitledCard(
            player_index=seat, deck_id="movement",
            title=deck.draw_pile[-1].definition.title)),
        ("SetRound", cmd.SetRound(round_number=state.round_number + 1)),
        ("ResetBoard", cmd.ResetBoard()),
    ]


def bookkeeping_commands(state):
    """The Card Library's OTHER commands, which stay open on any table.

    A shared house rule: everybody sees the result and nobody gains anything
    private.  That any seat may issue these on anybody's turn is a decision
    this project made before stage 53 and tested in ``test_card_library_sync``
    and ``test_card_variants_sync``; these assertions exist so that a future
    tightening of ``EDITOR_ONLY`` has to argue with a test rather than with a
    comment.
    """
    deck = state.decks["movement"]
    return [
        ("AdjustDeckCount", cmd.AdjustDeckCount(
            deck_id="movement", title=deck.draw_pile[0].definition.title,
            delta=1)),
        ("AdjustAbilityUses", cmd.AdjustAbilityUses(title="Big D Randy",
                                                    delta=5)),
        ("RestoreAbilityUses", cmd.RestoreAbilityUses(title="Big D Randy")),
    ]


# ── 1/3. authorisation ───────────────────────────────────────────────────────
def test_an_ordinary_table_refuses_the_commands_that_confer_advantage(table):
    """THE HOLE.  Every one of these was accepted before stage 53."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    assert not room.state.edit_mode

    accepted = []
    for label, command in gated_commands(room.state, idle):
        before = len(room.command_log)
        fingerprint = fingerprint_of(room.state.snapshot())
        by_seat[idle].session.submit(command)
        table.pump()
        if len(room.command_log) != before:
            accepted.append(label)
        assert fingerprint_of(room.state.snapshot()) == fingerprint, (
            f"{label} zmieniło stan mimo odmowy")
    assert not accepted, "przyjęte bez trybu edycji: " + ", ".join(accepted)


def test_an_ordinary_table_still_allows_the_shared_bookkeeping(table):
    """DECIDED BEFORE STAGE 53, and left alone by it.

    Reshaping a deck or switching a card variant changes a house rule the whole
    table plays under.  Any seat may do it on anybody's turn — that is what
    ``test_card_library_sync.py`` and ``test_card_variants_sync.py`` already
    say, and gating it would have overruled a decision somebody made
    deliberately.
    """
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    assert not room.state.edit_mode

    refused = []
    for label, command in bookkeeping_commands(room.state):
        before = len(room.command_log)
        by_seat[idle].session.submit(command)
        table.pump()
        if len(room.command_log) == before:
            refused.append(label)
    assert not refused, "zablokowane bez potrzeby: " + ", ".join(refused)


def test_an_editing_table_allows_them(table):
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    assert room.state.edit_mode

    refused = []
    for label, command in gated_commands(room.state, idle):
        before = len(room.command_log)
        by_seat[idle].session.submit(command)
        table.pump()
        if len(room.command_log) == before:
            refused.append(label)
    assert not refused, "odrzucone przy włączonym trybie edycji: " + \
        ", ".join(refused)


def test_edit_mode_is_the_host_s_setting_and_nobody_else_s(table):
    """A client asking for it is refused, which is what makes the flag mean
    something: it cannot be turned on by the machine that wants to use it."""
    host, clients = table.seated("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    assert not room.lobby.edit_mode

    clients[0].set_settings(edit_mode=True)
    table.pump()
    assert not room.lobby.edit_mode, "klient włączył sobie tryb edycji"

    host.set_settings(edit_mode=True)
    table.pump()
    assert room.lobby.edit_mode


def test_edit_mode_cannot_be_turned_on_mid_match(table):
    """``set_settings`` refuses once ``started``, so the table cannot become
    editable underneath the people playing on it."""
    host, clients = table.playing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)

    host.set_settings(edit_mode=True)
    table.pump()

    assert not room.lobby.edit_mode
    assert not room.state.edit_mode


def test_every_client_agrees_about_edit_mode(table):
    """It travels in SessionConfig with the seed, so nobody has their own idea."""
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    for service in [host, *clients]:
        assert service.state.edit_mode is True

    other = Table(table.library)
    try:
        plain_host, plain_clients = other.playing("A", "B", "C")
        for service in [plain_host, *plain_clients]:
            assert service.state.edit_mode is False
    finally:
        other.close()


def test_the_setting_survives_the_wire(library):
    """It is in ``to_dict``/``from_dict``, so a joining client sees it too."""
    lobby = LobbyState(code="ABCD")
    lobby.edit_mode = True
    assert LobbyState.from_dict(lobby.to_dict()).edit_mode is True
    assert LobbyState.from_dict(LobbyState(code="ABCD").to_dict()).edit_mode \
        is False


# ── 2/12. an edit by one client reaches all of them ──────────────────────────
def test_an_edit_by_one_client_is_reflected_on_every_replica(table, library):
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)

    for _, command in [*gated_commands(room.state, idle),
                       *bookkeeping_commands(room.state)]:
        by_seat[idle].session.submit(command)
        table.pump()

    authoritative = fingerprint_of(room.state.snapshot())
    for service in [host, *clients]:
        assert fingerprint_of(service.state.snapshot()) == authoritative
    assert replay_fingerprint(room, library) == authoritative


# ── 4/5. the card library, and what it must not leak ─────────────────────────
def test_a_card_dealt_by_the_library_does_not_enter_the_public_snapshot(table):
    """The hand SIZE crosses in the snapshot, the hand does not.

    ``to_public_dict`` publishes ``hand_size`` and nothing else, and dealing a
    card through the editor must not be the one path that gets round it.  Note
    what this does NOT claim — see the two tests below, which state plainly
    what an editing table does and does not keep secret.

    THE SIZE IS READ, NOT COUNTED ON (stage 57).  It used to assert
    ``before + 1``, which is wrong roughly one run in seven: the title comes
    off the top of a freshly seeded deck, and when that card is Troll its
    ``on_draw`` legitimately brings two more with it.  The game was right and
    the arithmetic was not.  What this test is actually about is that whatever
    the hand ends up being, the published size matches it and the CARDS are not
    there — so that is what it now says.
    """
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    title = room.state.decks["movement"].draw_pile[-1].definition.title
    before = len(room.state.player(idle).hand)

    by_seat[idle].session.submit(cmd.DrawTitledCard(
        player_index=idle, deck_id="movement", title=title))
    table.pump()

    hand = room.state.player(idle).hand
    assert len(hand) > before, "the deal did nothing"
    published = [p for p in room.state.snapshot()["players"]
                 if p["index"] == idle][0]
    assert published["hand_size"] == len(hand)
    assert "hand" not in published and "cards" not in published


def test_an_editing_table_does_not_give_up_piotrek(table):
    """THE LINE THAT HOLDS EVEN HERE.

    The hidden colour is never in the log and never in the snapshot — the
    server is told privately and tells nobody — so no amount of editing
    reveals it.  This is the invariant worth protecting; the hands are not.
    """
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    secret = room.state.piotrek_pawn
    assert secret

    holders = [s for s in [host, *clients]
               if any(p.secret_pawn == secret for p in s.state.players)]
    assert len(holders) == 1, "kolor Piotrka zna więcej niż jedna maszyna"
    # NOT a substring search: a seat's own ``color_name`` is a pawn colour too
    # and is public, so the colour word legitimately appears.  What must not
    # appear is any field SAYING WHOSE it is.
    for published in room.state.snapshot()["players"]:
        assert "secret_pawn" not in published
        assert "character" not in published and "hand" not in published


def test_an_editing_table_does_show_the_other_hands_and_that_is_the_point(table):
    """DECIDED, NOT ACCIDENTAL, and stated here so it is not a surprise.

    Every client already RECONSTRUCTS every hand — that is what replaying the
    command log means — and the interface refuses to draw the ones that are not
    yours (``GameScreen.may_view``).  ``may_view`` returns True in edit mode, so
    turning the mode on turns the hands face up for everybody at the table.
    That is what a hot-seat editing table is; anybody switching it on is
    switching off the hidden-hand game, and the lobby setting is where they say
    so.  Piotrek's colour is a different matter and stays hidden — above.
    """
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    for service in [host, *clients]:
        assert service.state.edit_mode
        for player in service.state.players:
            assert len(player.hand) == len(room.state.player(player.index).hand)


def test_the_library_deals_the_named_card_and_nothing_else(table, library):
    """Data-driven: the command names a TITLE and the deck answers.  No branch
    anywhere is keyed on which card it is.

    ASKED FOR BY TITLE, CHECKED BY TITLE (stage 57).  It used to read
    ``hand[-1]``, which assumed the named card lands last and alone; when the
    top of a freshly seeded deck is Troll, its ``on_draw`` rule deals two more
    behind it and the assertion caught the game doing its job.  The claim in the
    name survives — the named card arrived, it came out of the named deck, and
    the room still replays to its own fingerprint — without depending on which
    card the shuffle happened to put on top.
    """
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    deck = room.state.decks["movement"]
    title = deck.draw_pile[-1].definition.title
    before = len(room.state.player(idle).hand)

    by_seat[idle].session.submit(cmd.DrawTitledCard(
        player_index=idle, deck_id="movement", title=title))
    table.pump()

    hand = room.state.player(idle).hand
    assert len(hand) > before, "nothing was dealt"
    assert title in [c.definition.title for c in hand], \
        "the deck did not answer the title it was given"
    assert all(c.deck_id == "movement" for c in hand[before:]), \
        "a deck nobody named was drawn from"
    assert replay_fingerprint(room, library) == fingerprint_of(
        room.state.snapshot())


# ── 6/7. manual movement on an editing table ─────────────────────────────────
def test_manual_movement_out_of_turn_works_and_stays_in_step(table, library):
    """Editing lifts the TURN rule — that is what the mode is for — and the
    movement still travels as an ordinary logged command."""
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, active, idle = seats(table, host, clients)
    pawn = room.state.library.pawns[0].id
    tile = room.state.board.tiles[5]

    by_seat[idle].session.submit(cmd.MoveToken(
        pawn_id=pawn, x=tile.position[0], y=tile.position[1],
        tile_index=tile.index))
    table.pump()

    assert room.state.board.pawn_tiles.get(pawn) == tile.index
    authoritative = fingerprint_of(room.state.snapshot())
    for service in [host, *clients]:
        assert fingerprint_of(service.state.snapshot()) == authoritative
    assert replay_fingerprint(room, library) == authoritative


def test_an_invalid_edit_still_changes_nothing(table):
    """Stage 51's invariant, on an editing table this time.

    Editing lifts the turn rule; it does not lift atomicity.
    """
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    pawn = room.state.library.pawns[0].id
    room.state.statuses.add(Status(kind=StatusKind.FROZEN, subject=Subject.PAWN,
                                   subject_id=pawn, source="Granny Costume"))
    before = fingerprint_of(room.state.snapshot())
    log_before = len(room.command_log)

    by_seat[idle].session.submit(cmd.MoveToken(pawn_id=pawn, x=1.0, y=2.0,
                                               tile_index=99_999))
    table.pump()

    assert fingerprint_of(room.state.snapshot()) == before
    assert len(room.command_log) == log_before
    assert room.state.statuses.pawn_has(StatusKind.FROZEN, pawn)


# ── 8/9/10/11/12. Reset Board ────────────────────────────────────────────────
def prepared_board(table, host, clients):
    """Pawns out on the road, a tower, a freeze — something worth clearing."""
    room, by_seat, _, idle = seats(table, host, clients)
    pawns = [p.id for p in room.state.library.pawns]
    for offset, pawn_id in enumerate(pawns[:3]):
        tile = room.state.board.tiles[2 + offset * 2]
        by_seat[idle].session.submit(cmd.MoveToken(
            pawn_id=pawn_id, x=tile.position[0], y=tile.position[1],
            tile_index=tile.index))
        table.pump()
    # ...and one on top of another, which is a tower.
    tower_tile = room.state.board.tiles[2]
    by_seat[idle].session.submit(cmd.MoveToken(
        pawn_id=pawns[3], x=tower_tile.position[0], y=tower_tile.position[1],
        tile_index=tower_tile.index))
    table.pump()
    room.state.statuses.add(Status(kind=StatusKind.FROZEN, subject=Subject.PAWN,
                                   subject_id=pawns[0], source="Granny Costume"))
    return room, by_seat, idle, pawns


def test_reset_board_sends_every_pawn_back_to_its_camp_slot(table):
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, idle, pawns = prepared_board(table, host, clients)
    assert room.state.board.pawn_tiles, "przygotowanie nic nie ustawiło"

    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    assert room.state.board.pawn_tiles == {}
    assert not any(tile.stack for tile in room.state.board.tiles)
    for slot, pawn in enumerate(room.state.library.pawns):
        token = room.state.tokens[pawn.id]
        assert token.tile_index is None
        assert token.position == room.state.board.camp_position(slot)
        assert token.held is False


def test_reset_board_clears_what_the_board_was_holding(table):
    """Pawn statuses and the decisions waiting on a tower go with it."""
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, idle, pawns = prepared_board(table, host, clients)
    room.state.statuses.add(Status(kind=StatusKind.MOVEMENT_BONUS,
                                   subject=Subject.PLAYER, subject_id="0",
                                   source="ChatGPT"))

    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    assert not [s for s in room.state.statuses.all()
                if s.subject is Subject.PAWN], "status pionka przeżył reset"
    assert [s for s in room.state.statuses.all()
            if s.subject is Subject.PLAYER], "status gracza NIE jest planszą"
    assert room.state.pending_check is None
    assert room.state.pending_breakup is None
    assert room.state.pending_movement is None


def test_reset_board_is_a_board_reset_and_not_a_restart(table):
    """Hands, decks, the round and the turn are deliberately untouched."""
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, idle, _ = prepared_board(table, host, clients)
    hands = {p.index: [c.uid for c in p.hand] for p in room.state.players}
    draw = [c.uid for c in room.state.decks["movement"].draw_pile]
    round_number = room.state.round_number
    active = room.state.active_player_index

    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    assert {p.index: [c.uid for c in p.hand]
            for p in room.state.players} == hands
    assert [c.uid for c in room.state.decks["movement"].draw_pile] == draw
    assert room.state.round_number == round_number
    assert room.state.active_player_index == active


def test_reset_board_reaches_every_replica_and_replays(table, library):
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, idle, _ = prepared_board(table, host, clients)

    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    assert room.command_log[-1]["kind"] == "reset_board"
    authoritative = fingerprint_of(room.state.snapshot())
    for service in [host, *clients]:
        assert fingerprint_of(service.state.snapshot()) == authoritative
        assert service.state.board.pawn_tiles == {}
        assert not [n for n in service.drain_notices()
                    if "różni się" in n or "Rozjazd" in n]
    assert replay_fingerprint(room, library) == authoritative


def test_reset_board_twice_is_the_same_board(table):
    """Idempotent, because "put them back" has one answer."""
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, idle, _ = prepared_board(table, host, clients)

    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()
    once = fingerprint_of(room.state.snapshot())
    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    assert fingerprint_of(room.state.snapshot()) == once


# ── 13. the verdict boundary, stated rather than left implicit ───────────────
def test_a_finished_match_cannot_have_its_board_reset(table):
    """CHOSEN, and it follows the existing gate rather than a new rule.

    ``ResetBoard`` is not in ``AUTHORITY_ONLY``, so ``authorise_remote`` runs
    ``_phase_refusal`` on it like any other command, and ENDED answers "Gra
    została zakończona".  A verdict therefore stands: sweeping the board after
    somebody has won would leave a declared winner next to an empty road, and
    the lobby already owns starting again.
    """
    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room, by_seat, _, idle = seats(table, host, clients)
    finish = [t for t in room.state.board.tiles if t.kind.value == "finish"][0]
    by_seat[idle].session.submit(cmd.MoveToken(
        pawn_id=room.state.piotrek_pawn, x=finish.position[0],
        y=finish.position[1], tile_index=finish.index))
    table.pump()
    if room.state.phase is not MatchPhase.ENDED:
        pytest.skip("ten wariant zwycięstwa nie zakończył partii")

    log_before = len(room.command_log)
    before = fingerprint_of(room.state.snapshot())
    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    assert len(room.command_log) == log_before
    assert fingerprint_of(room.state.snapshot()) == before
    assert room.state.victory is not None


# ── the controls, at every resolution the game supports ──────────────────────
UI_SIZES = [(1280, 760), (1600, 900), (1920, 1080), (2560, 1440)]


@pytest.mark.parametrize("size", UI_SIZES)
def test_the_edit_checkbox_never_collides_with_the_debug_one(library, size):
    """It SHARES the debug row, so the two must stay apart as fonts scale.

    The form already reached the bottom of a 1280x760 window, so there was no
    version of "one more row" that fit — stripped to a bare checkbox with no
    hint and no gap it still pushed the error line off the screen.  Sharing the
    row means the gap has to be MEASURED from the debug caption rather than
    guessed, because the captions grow with the window and a gap that clears at
    1280 is swallowed at 2560.  ``test_menu_layout`` catches the text
    overlapping; this catches the boxes themselves.
    """
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.layout import Layout
    from pedzacy_piotrek.ui.network_screens import HostSetupScreen

    app = App(Layout(), headless=True, size=size)
    screen = HostSetupScreen(app, library)
    screen.on_resize()

    debug, edit = screen.debug_checkbox.rect, screen.edit_checkbox.rect
    assert not debug.colliderect(edit)
    assert edit.left > debug.right, "kolejność przełączników się odwróciła"
    assert edit.right < size[0], "przełącznik wyszedł poza okno"
    assert edit.top == debug.top, "przełączniki nie są w jednym rzędzie"


# ── stage 54: the reset is behind Esc, and the screen believes it ────────────
def a_local_screen(library, edit_mode=True):
    """A GameScreen over a LocalSession, the way ``test_ui`` builds one."""
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from pedzacy_piotrek.config.settings import SessionConfig
    from pedzacy_piotrek.net.session import LocalSession
    from pedzacy_piotrek.engine.setup import create_game
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout

    app = App(Layout(), headless=True, size=(1920, 1080))
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3,
                      seed=77, edit_mode=edit_mode),
        library)
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    return screen


def settle(*screens, frames: int = 200):
    """Run the view's tweens out.

    A move GLIDES: ``_on_token_moved`` starts a tween and ``visual`` catches up
    over the next second or so, which is the animation doing its job.  A test
    that read ``visual`` straight after a move would be reading the middle of
    it.  A RESET does not glide — ``resync`` writes every position at once,
    because there is no journey to draw — so settling first is what makes the
    two comparable.
    """
    for _ in range(frames):
        for screen in screens:
            screen.board_view.update(1 / 60.0, (0, 0))


def test_the_standalone_reset_button_is_gone(library):
    """It used to sit directly under COFNIJ RUCH — a few pixels below the
    control people press by reflex, for the one irreversible action there is."""
    from pedzacy_piotrek.ui.layout import Layout

    screen = a_local_screen(library)
    assert not hasattr(Layout, "reset_board_button_rect")
    assert not hasattr(screen, "_draw_reset_board_button")
    assert not hasattr(screen, "_reset_board_click")


def test_the_esc_menu_offers_the_reset_last(library):
    screen = a_local_screen(library)
    entries = screen._pause_entries()
    labels = [label for _, label in entries]

    assert "Resetuj Planszę" in labels
    assert "Pionki od nowa" not in labels and "PIONKI OD NOWA" not in labels
    assert entries[-1][0] == "reset_board", "reset musi być ostatni"
    assert labels[0] == "Wróć do gry", "wyjście z menu zostaje pierwsze"


def test_the_esc_menu_hides_the_reset_on_an_ordinary_table(library):
    """The engine refuses it there, so the menu does not offer it."""
    screen = a_local_screen(library, edit_mode=False)
    assert "Resetuj Planszę" not in [l for _, l in screen._pause_entries()]


def test_choosing_the_reset_closes_the_menu_and_submits_the_command(library):
    import pygame

    screen = a_local_screen(library)
    pawn = screen.state.library.pawns[0].id
    screen.submit(cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0, tile_index=7))
    assert screen.state.board.pawn_tiles.get(pawn) == 7

    screen.pause_menu.open(screen._pause_entries())
    screen.pause_menu._lay_out(screen.app.layout, screen.app.renderer)
    index = [key for key, _ in screen.pause_menu.entries].index("reset_board")
    target = screen.pause_menu.rects[index].center
    screen._handle_pause_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=target),
        target)

    assert not screen.pause_menu.active
    assert screen.state.board.pawn_tiles == {}


# ── stage 54: the bug itself — visual positions after a reset ────────────────
def test_the_drawn_pawns_follow_the_reset(library):
    """THE BUG.  ``BoardReset`` was emitted and nothing was listening.

    ``board_view.visual`` is the board's own copy of where each pawn is DRAWN
    and is only ever written by the movement reactions, so the engine had the
    pawns back in their camps while the screen went on showing them out on the
    road.
    """
    screen = a_local_screen(library)
    view = screen.board_view
    pawn = screen.state.library.pawns[0].id

    screen.submit(cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0, tile_index=7))
    settle(screen)
    on_the_road = view.visual[pawn]
    assert on_the_road == screen.state.tokens[pawn].position

    screen.submit(cmd.ResetBoard())

    assert view.visual[pawn] != on_the_road, "pionek został narysowany na drodze"
    for slot, definition in enumerate(screen.state.library.pawns):
        assert view.visual[definition.id] == \
            screen.state.board.camp_position(slot)


def test_a_movement_after_a_reset_starts_from_the_camp(library):
    """Logical and drawn positions stay together through the NEXT move.

    The symptom that was reported: reset a pawn from field 7, move it forward
    by one, and it appeared to jump from 7 to 1 because only the second step
    ever wrote ``visual``.
    """
    screen = a_local_screen(library)
    view = screen.board_view
    pawn = screen.state.library.pawns[0].id
    first_tile = screen.state.board.positions[1].tiles[0].index

    screen.submit(cmd.MoveToken(pawn_id=pawn, x=0.0, y=0.0, tile_index=7))
    settle(screen)
    screen.submit(cmd.ResetBoard())
    # NOT settled here on purpose: the reset must be on screen IMMEDIATELY, so
    # the next move starts from the camp rather than from field 7.
    assert view.visual[pawn] == screen.state.board.camp_position(0)
    screen.submit(cmd.MoveToken(
        pawn_id=pawn,
        x=screen.state.board.tile(first_tile).position[0],
        y=screen.state.board.tile(first_tile).position[1],
        tile_index=first_tile))
    settle(screen)

    assert screen.state.board.pawn_tiles.get(pawn) == first_tile
    assert view.visual[pawn] == screen.state.tokens[pawn].position


def test_every_client_redraws_after_an_authoritative_reset(table, library):
    """The same propagation, over the wire.

    The events reach a client through ``apply_authoritative`` and the same bus,
    so a fix that only worked on the machine that clicked would show here.
    """
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout

    host, clients = table.editing("Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    screens = []
    for service in [host, *clients]:
        app = App(Layout(), headless=True, size=(1920, 1080))
        screen = GameScreen(app, service.session, service=service)
        app.push(screen)
        screens.append(screen)

    room, by_seat, _, idle = seats(table, host, clients)
    pawn = room.state.library.pawns[0].id
    tile = room.state.board.tiles[7]
    by_seat[idle].session.submit(cmd.MoveToken(
        pawn_id=pawn, x=tile.position[0], y=tile.position[1],
        tile_index=tile.index))
    table.pump()
    settle(*screens)
    for screen in screens:
        assert screen.board_view.visual[pawn] == screen.state.tokens[pawn].position

    by_seat[idle].session.submit(cmd.ResetBoard())
    table.pump()

    camp = room.state.board.camp_position(0)
    for screen in screens:
        assert screen.state.board.pawn_tiles == {}
        assert screen.board_view.visual[pawn] == camp
    assert fingerprint_of(room.state.snapshot()) == fingerprint_of(
        host.state.snapshot())
    assert replay_fingerprint(room, library) == fingerprint_of(
        room.state.snapshot())


# ── stage 57: an online editing table does not move anybody's view ───────────
#
# Editing was built for ONE person at ONE keyboard, where the screen following
# the turn is not a convenience but the only way to reach the next seat.  Four
# friends testing online inherited that rule and it does the opposite of what it
# was for: somebody plays a card, the turn advances, and every screen in the
# room jumps to a seat its owner was not looking at.
#
# The distinction is entirely LOCAL and entirely about the VIEW.  The turn
# cursor, the authority, the command gates and the hidden information are the
# same on both kinds of table, and the tests below check that they still are —
# a fix that bought a stable view by loosening any of them would be a much
# worse bug than the one it cured.
#
#     local  + edit mode      view follows the turn      (unchanged)
#     local  + no edit mode   unchanged
#     online + no edit mode   unchanged
#     online + EDIT MODE      view stays put; only a click moves it
def _headless():
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def online_screens(table, library, *names, size=(1920, 1080), edit_mode=True):
    """An online match with a GameScreen per player, editing or not.

    Goes through ``table.editing``/``table.playing`` rather than building a
    state by hand, so the screens are looking at real replicas fed by the real
    server — which is the only way the "somebody else's command arrived" cases
    below mean anything.
    """
    _headless()
    from pedzacy_piotrek.ui.app import App
    from pedzacy_piotrek.ui.game_screen import GameScreen
    from pedzacy_piotrek.ui.layout import Layout

    opener = table.editing if edit_mode else table.playing
    host, clients = opener(*names)
    screens = []
    for service in [host, *clients]:
        app = App(Layout(), headless=True, size=size)
        screen = GameScreen(app, service.session, service=service)
        app.push(screen)
        screens.append(screen)
    return host, clients, screens


def a_movement_card(state, seat):
    from pedzacy_piotrek.config import settings

    player = state.player(seat)
    return next(c for c in player.hand
                if c.deck_id == settings.DECK_MOVEMENT and not c.locked)


def play_a_card(table, service, seat):
    """Whoever holds ``seat`` discards a movement card, which passes the turn."""
    card = a_movement_card(service.state, seat)
    service.session.submit(cmd.DiscardCard(player_index=seat,
                                           card_uid=card.uid))
    table.pump()


def click_tile(screen, seat):
    """Click a player tile through the REAL layout, at whatever size it is.

    The rect comes from ``Layout.player_tile_rect``; nothing here knows a
    coordinate, which is what lets the same test run at four resolutions.
    """
    import pygame

    frame_once(screen)
    rect = screen.app.layout.player_tile_rect(seat, len(screen.state.players))
    pos = rect.center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), pos)
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1), pos)


def frame_once(screen, dt=1 / 60.0, mouse=(0, 0)):
    app = screen.app
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(dt, mouse)
    screen.draw(app.canvas)


# ── 1. local editing is untouched ────────────────────────────────────────────
def test_a_local_editing_table_still_follows_the_turn(library):
    """THE BEHAVIOUR THAT MUST SURVIVE.  One keyboard, five seats, and the
    screen moving to the seat now in play is how the next card gets played."""
    screen = a_local_screen(library)
    assert screen.view_follows_the_game
    screen.state.apply(cmd.BeginMatch(), local=False)
    start = screen.state.active_player_index
    assert screen.view_seat == start

    card = a_movement_card(screen.state, start)
    screen.submit(cmd.DiscardCard(player_index=start, card_uid=card.uid))

    assert screen.state.active_player_index != start, "the turn passed"
    assert screen.view_seat == screen.state.active_player_index, \
        "the hot-seat screen stopped following the turn"


def test_a_local_table_without_editing_is_untouched(library):
    screen = a_local_screen(library, edit_mode=False)
    assert screen.view_follows_the_game
    assert screen.view_seat == screen.my_seat


# ── 2-3. an online editing table keeps its seat ──────────────────────────────
def test_an_online_editing_table_opens_on_your_own_seat(table, library):
    """Not on whoever happens to be starting: you own one seat here."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    for screen in screens:
        assert not screen.view_follows_the_game
        assert screen.view_seat == screen.my_seat


def test_playing_a_card_does_not_move_your_own_view(table, library):
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    active = host.state.active_player_index
    actor = next(s for s in screens if s.my_seat == active)
    watched = actor.view_seat

    play_a_card(table, by_seat[active], active)

    assert actor.state.active_player_index != active, "the turn still passed"
    assert actor.view_seat == watched, "the view jumped after playing a card"


def test_somebody_elses_card_does_not_move_your_view(table, library):
    """THE REPORT, in one test: A is watching A, B plays, A still watches A."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    active = host.state.active_player_index
    idle = next(s for s in screens if s.my_seat != active)
    watched = idle.view_seat

    play_a_card(table, by_seat[active], active)

    assert idle.view_seat == watched, \
        "a remote player's card moved this machine's view"
    assert idle.state.active_player_index != active, \
        "but the turn did advance on this replica"


def test_a_whole_round_of_cards_leaves_every_view_where_it_was(table, library):
    """Not one command: several, including the turn coming back round."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    watching = {screen.my_seat: screen.view_seat for screen in screens}

    for _ in range(len(by_seat) + 1):
        active = host.state.active_player_index
        play_a_card(table, by_seat[active], active)

    for screen in screens:
        assert screen.view_seat == watching[screen.my_seat]
        assert screen.view_seat == screen.my_seat


def test_an_explicit_turn_change_does_not_move_a_watching_client(table, library):
    """``SetActivePlayer`` is the bluntest turn transition there is.

    It is also still allowed: this stage does not take editing away, it stops
    the VIEW from chasing it.
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    active = host.state.active_player_index
    target = next(s for s in by_seat if s != active)
    watchers = {id(s): s.view_seat for s in screens}

    # The seat asks for itself: the server judges ``player_index`` against the
    # connection's own seat, so this is the only form a client can send.
    by_seat[target].session.submit(cmd.SetActivePlayer(player_index=target))
    table.pump()

    assert host.state.active_player_index == target, "the turn DID move"
    for screen in screens:
        assert screen.view_seat == watchers[id(screen)]


# ── 4. clicking still works, at every resolution ─────────────────────────────
@pytest.mark.parametrize("size", [(1280, 760), (1920, 1080),
                                  (2560, 1440), (3840, 2160)])
def test_clicking_a_player_still_switches_the_view(table, library, size):
    """Manual selection is the ONLY way to move now, so it had better work.

    Run at four resolutions because the tile it clicks is placed by ``Layout``
    and nothing in the test knows a coordinate — a rect that collapsed at
    1280x760 would take the last remaining way to change seats with it.
    """
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", size=size)
    screen = screens[0]
    other = next(i for i in range(len(screen.state.players))
                 if i != screen.my_seat)

    click_tile(screen, other)

    assert screen.view_seat == other, f"tile click did nothing at {size}"
    click_tile(screen, screen.my_seat)
    assert screen.view_seat == screen.my_seat, "and back again"


def test_a_click_is_still_honoured_after_the_turn_moves(table, library):
    """The view stays where it is put, not where it was put once."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    by_seat = table.by_seat(host, clients)
    screen = screens[0]
    other = next(i for i in range(len(screen.state.players))
                 if i != screen.my_seat)
    click_tile(screen, other)

    active = host.state.active_player_index
    play_a_card(table, by_seat[active], active)

    assert screen.view_seat == other, \
        "the chosen seat was overwritten by the turn"


def test_returning_to_your_own_seat_still_works(table, library):
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    screen = screens[0]
    other = next(i for i in range(len(screen.state.players))
                 if i != screen.my_seat)
    screen.focus_seat(other)
    assert screen.view_seat == other

    screen.return_to_my_seat()
    assert screen.view_seat == screen.my_seat


# ── 5. an ordinary online table is untouched ─────────────────────────────────
def test_an_ordinary_online_table_is_untouched(table, library):
    """Edit mode off: the rule that answers "no" never applies."""
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", edit_mode=False)
    by_seat = table.by_seat(host, clients)
    for screen in screens:
        assert screen.view_follows_the_game
        assert screen.view_seat == screen.my_seat

    active = host.state.active_player_index
    play_a_card(table, by_seat[active], active)

    for screen in screens:
        assert screen.view_seat == screen.my_seat, \
            "an ordinary table never shows another hand anyway"
        assert not screen.may_view(
            next(i for i in range(len(screen.state.players))
                 if i != screen.my_seat)), "hidden information is still hidden"


# ── 6-8. nothing about the AUTHORITY moved ───────────────────────────────────
def test_the_turn_cursor_advances_exactly_as_before(table, library):
    """The view is the only thing this stage touches.

    The server's own cursor, the clients' replicas and the fingerprint all have
    to agree afterwards, because "the screen stopped following the turn" must
    not become "the turn stopped".
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    by_seat = table.by_seat(host, clients)
    seen = [room.state.active_player_index]

    for _ in range(4):
        active = room.state.active_player_index
        play_a_card(table, by_seat[active], active)
        seen.append(room.state.active_player_index)

    assert len(set(seen)) > 1, "the turn never moved at all"
    assert all(a != b for a, b in zip(seen, seen[1:])), \
        "the turn stood still on a card"
    for service in [host, *clients]:
        assert service.state.active_player_index == \
            room.state.active_player_index
        assert fingerprint_of(service.state.snapshot()) == \
            fingerprint_of(room.state.snapshot())
    assert replay_fingerprint(room, library) == \
        fingerprint_of(room.state.snapshot())


def test_the_view_cannot_be_used_to_act_for_another_seat(table, library):
    """A stable view must not become a way round the gates.

    ``view_seat`` is a local picture and always was; the engine judges
    ``player_index`` against the seat the SERVER gave this connection.  Setting
    the view by hand — further than any click could — and acting through the
    ordinary path must still be refused on an ordinary table.
    """
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", edit_mode=False)
    room = table.room(host.room_code)
    screen = screens[0]
    victim = next(i for i in range(len(screen.state.players))
                  if i != screen.my_seat)
    screen.view_seat = victim          # by hand: no click can reach here

    assert not screen.state.may_control(victim)
    assert not screen.controls_view
    before = fingerprint_of(room.state.snapshot())
    card = a_movement_card(room.state, victim)
    screen.submit(cmd.DiscardCard(player_index=victim, card_uid=card.uid))
    table.pump()

    assert fingerprint_of(room.state.snapshot()) == before, \
        "a command for somebody else's seat was accepted"


def test_the_safety_net_still_reclaims_a_seat_it_may_not_watch(table, library):
    """``_context`` snaps back when the right to look is gone.

    Unchanged by this stage, and checked because it is the other thing that
    writes ``view_seat`` without being asked.
    """
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", edit_mode=False)
    screen = screens[0]
    screen.view_seat = next(i for i in range(len(screen.state.players))
                            if i != screen.my_seat)

    frame_once(screen)

    assert screen.view_seat == screen.my_seat


# ── 9. reconnection ──────────────────────────────────────────────────────────
def test_reconnecting_does_not_move_the_chosen_seat(table, library):
    """A dropped connection is not a request to look somewhere else.

    The seat this player chose is a local decision, and coming back through the
    grace period restores the MATCH, not the interface.
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    screen = screens[1]
    service = clients[0]
    other = next(i for i in range(len(screen.state.players))
                 if i != screen.my_seat)
    click_tile(screen, other)
    assert screen.view_seat == other

    service.client.transport.drop()
    table.pump()
    service.client.transport.restore()
    table.pump(20)

    assert service.disconnected is None, "the player came back"
    assert screen.view_seat == other, "reconnecting moved the view"


# ── the click itself: what it moves, and what it must not say ────────────────
def test_a_local_click_still_hands_the_turn_over(library):
    """Hot-seat editing: one click both looks AND takes the seat.

    The whole point of the mode at one keyboard, and untouched by stage 57.
    """
    screen = a_local_screen(library)
    screen.state.apply(cmd.BeginMatch(), local=False)
    other = next(i for i in range(len(screen.state.players))
                 if i != screen.state.active_player_index)

    click_tile(screen, other)

    assert screen.view_seat == other
    assert screen.state.active_player_index == other, \
        "the hot-seat click stopped handing the turn over"


def test_an_online_click_looks_without_taking_the_turn(table, library):
    """Looking is local; the turn is the server's, and it says no.

    ``authorise_remote`` has always refused a ``player_index`` that is not the
    connection's own seat, so this was never a way to move somebody else's
    turn.  What it used to do was ASK and be told off — one red line per look,
    on the interaction that is now the only way to change seats at all.
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    screen = screens[0]
    other = next(i for i in range(len(screen.state.players))
                 if i != screen.my_seat)
    before = room.state.active_player_index
    screen.status_bar.clear()               # a known-empty starting point

    click_tile(screen, other)
    table.pump()

    assert screen.view_seat == other, "the look did not happen"
    assert room.state.active_player_index == before, \
        "a client moved the authoritative turn"
    assert not screen.may_take_seat(other)
    said = screen.status_bar.message or ""
    assert "miejsce" not in said, f"looking was answered with a refusal: {said}"


def test_an_online_click_on_your_own_seat_still_asks_for_the_turn(table, library):
    """Editing is not taken away: your OWN seat is a request the server takes."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    idle = next(s for s in screens if s.my_seat != room.state.active_player_index)

    assert idle.may_take_seat(idle.my_seat)
    click_tile(idle, idle.my_seat)
    table.pump()

    assert room.state.active_player_index == idle.my_seat, \
        "an editing table stopped letting a seat take its own turn"
    assert idle.view_seat == idle.my_seat
