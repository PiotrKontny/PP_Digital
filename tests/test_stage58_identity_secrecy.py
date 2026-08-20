"""
Stage 58 — only Piotrek may see Piotrek's colour.

THE RULE, and it is worth stating as a rule because everything below is one
consequence of it:

    the secret colour is visible when the person AT THE SCREEN is the seat that
    owns it, and at no other time, on no other surface, in no other mode.

The leak was not in the data.  A hunter's replica holds ``secret_pawn`` as
``None`` for the whole match, the public snapshot never carries it and neither
does the command log — that half was already right and is re-checked here so it
stays right.  The leak was one line of PRESENTATION: the identity badge was
drawn because the SEAT being displayed was Piotrek's, without asking whose eyes
were on it.  In a hot-seat game those are the same question.  In online edit
mode, where anybody may open anybody's panel, they are not.

So the badge now has three states, and a hunter only ever sees the third:

    chosen      the pawn's colour and name    Piotrek, on his own screen
    not chosen  an empty ring, "NIE WYBRANO"  likewise
    not yours   a locked ring, "UKRYTA TOŻSAMOŚĆ"

The third is CONSTANT — the same before and after Piotrek chooses, the same
whichever colour he chose — because a badge that changed when he picked would
announce that he had, and a row that disappeared would announce that the panel
had something to hide.

MEASURED IN PIXELS wherever the claim is about pixels, which is the philosophy
the portrait and card-art tests already use here.  A test asserting
``draw_badge is False`` would pass against a build that drew the swatch through
a different code path; a test that reads the colour off the canvas would not.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame
import pytest

from netkit import Table
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

WINDOW = (1920, 1080)

#: Every resolution the project promises. A panel verified at one is verified
#: at none: the badge is laid out by ``Layout`` and its radius scales.
REFERENCE_WINDOWS = [(1280, 760), (1920, 1080), (2560, 1440), (3840, 2160)]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library) -> Table:
    made = Table(library)
    yield made
    made.close()


# ── harness ──────────────────────────────────────────────────────────────────
def frame(screen) -> None:
    app = screen.app
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(1 / 60.0, (0, 0))
    screen.draw(app.canvas)


def online_screens(table, library, *names, size=WINDOW, edit_mode=True):
    """An online match with a screen per player, editing unless told otherwise."""
    opener = table.editing if edit_mode else table.playing
    host, clients = opener(*names)
    screens = []
    for service in [host, *clients]:
        app = App(Layout(), headless=True, size=size)
        screen = GameScreen(app, service.session, service=service)
        app.push(screen)
        screens.append(screen)
    return host, clients, screens


def piotrek_seat_of(state) -> int:
    seat = state.piotrek_seat
    assert seat is not None, "no Piotrek at this table"
    return seat


def identity_rect(screen) -> pygame.Rect:
    """Where the badge lives, asked of the LAYOUT rather than measured.

    ``character_panel(True)`` is the Piotrek layout — the one that has an
    identity row at all — so this is the band the badge occupies on whichever
    screen is being inspected, at whatever size it is.
    """
    return screen.app.layout.character_panel(True)["identity"]


def pixels_of(surface: pygame.Surface, rect: pygame.Rect):
    clipped = rect.clip(surface.get_rect())
    return [tuple(surface.get_at((x, y))[:3])
            for x in range(clipped.left, clipped.right)
            for y in range(clipped.top, clipped.bottom)]


def distance(a, b) -> float:
    return sum(abs(int(x) - int(y)) for x, y in zip(a, b))


def shows_colour(surface, rect, colour, tolerance: int = 90) -> bool:
    """Whether anything in ``rect`` is recognisably that pawn's colour.

    A sum-of-channels distance rather than an exact match, because the badge
    darkens its border and lightens a highlight — a test demanding the exact
    RGB would miss a swatch drawn one shade off and call the leak fixed.
    """
    return any(distance(pixel, colour) <= tolerance
               for pixel in pixels_of(surface, rect))


def drawn_strings(screen) -> list:
    """Every string the renderer painted during one frame.

    Text goes to the canvas as pixels, so a leak in a LABEL is invisible to a
    colour probe. This records the strings themselves, which is the only way to
    catch "CZERWONY" written in the theme's brass.
    """
    from pedzacy_piotrek.render.renderer import Renderer

    seen: list = []
    original_text = Renderer.text
    original_surface = Renderer.text_surface

    def text(self, value, *args, **kwargs):
        seen.append(str(value))
        return original_text(self, value, *args, **kwargs)

    def text_surface(self, value, *args, **kwargs):
        seen.append(str(value))
        return original_surface(self, value, *args, **kwargs)

    Renderer.text = text
    Renderer.text_surface = text_surface
    try:
        frame(screen)
    finally:
        Renderer.text = original_text
        Renderer.text_surface = original_surface
    return seen


def a_local_piotrek_screen(library):
    """Hot-seat, where the colour is dealt from the seed and IS the viewer's."""
    config = SessionConfig(num_players=3, edit_mode=True, seed=7,
                           piotrek_picks_pawn=False)
    state = create_game(config, library)
    state.apply(cmd.BeginMatch(), local=False)
    app = App(Layout(), headless=True, size=WINDOW)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)
    return screen


# ── the data layer, which was already right ──────────────────────────────────
def test_the_public_snapshot_says_nothing_about_the_secret(table, library):
    """Re-checked, not assumed. The presentation fix is the SECOND line.

    Not "the word czerwony is absent" — every pawn is on the board and every
    pawn's position is public, so that string is in there for honest reasons
    and a test looking for it would fail against a correct build. The real
    claim is INFORMATIONAL: change which pawn is the secret and the published
    state does not move a byte. A snapshot that is identical for all six
    possible secrets cannot be carrying any of them.
    """
    host, clients, _ = online_screens(table, library, "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    assert room.state.piotrek_pawn, "the table never chose a colour"

    published = set()
    for pawn in room.state.library.pawns:
        room.state.player(seat).secret_pawn = pawn.id
        published.add(str(room.state.snapshot()))

    assert len(published) == 1, "the snapshot changes with the secret"
    assert "secret_pawn" not in published.pop()


def test_the_command_log_carries_no_secret(table, library):
    """The colour is set through ``set_piotrek_pawn``, never through a command.

    The log is what a reconnecting client replays, so anything in it is
    something every client is told. Checked by name AND by the same
    informational argument: no entry mentions the chosen colour.
    """
    host, clients, _ = online_screens(table, library, "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    secret = room.state.piotrek_pawn
    logged = str(room.command_log)

    assert "secret_pawn" not in logged
    assert "set_piotrek_pawn" not in logged
    assert f"'{secret}'" not in logged, "the colour appears as a logged value"


def test_a_hunter_client_never_holds_the_secret(table, library):
    """The strongest form of the guarantee: it is not there to leak."""
    host, clients, _ = online_screens(table, library, "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)

    for service in [host, *clients]:
        if service.state.local_seat == seat:
            continue
        assert service.state.piotrek_pawn is None
        assert all(p.secret_pawn is None for p in service.state.players)


def test_piotreks_own_client_does_hold_his_secret(table, library):
    """The other half — a fix that hid it from everybody would pass the rest."""
    host, clients, _ = online_screens(table, library, "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    mine = next(s for s in [host, *clients] if s.state.local_seat == seat)

    assert mine.state.piotrek_pawn == room.state.piotrek_pawn


# ── the pixels ───────────────────────────────────────────────────────────────
def test_piotrek_sees_his_own_colour(table, library):
    """Both the swatch and the name, on his own screen."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat == seat)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)

    said = drawn_strings(screen)

    assert screen.entitled_to_secrets
    assert pawn.name.upper() in said, "Piotrek cannot see his own colour"
    assert shows_colour(screen.app.canvas, identity_rect(screen), pawn.color), \
        "the swatch was not painted in the pawn's colour"


@pytest.mark.parametrize("which", [0, 1])
def test_a_hunter_inspecting_piotrek_sees_no_colour(table, library, which):
    """THE REPORT. Both hunters, because one of them is the host."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)
    hunters = [s for s in screens if s.my_seat != seat]
    screen = hunters[which]

    screen.focus_seat(seat)
    said = drawn_strings(screen)

    assert screen.view_seat == seat, "the hunter really is looking at Piotrek"
    assert not screen.entitled_to_secrets
    assert pawn.name.upper() not in said, "the colour was named"
    assert "UKRYTA TOŻSAMOŚĆ" in said, "the row said nothing at all"
    assert not shows_colour(screen.app.canvas, identity_rect(screen),
                            pawn.color), "the swatch leaked the colour"


def test_the_masked_badge_names_no_pawn_at_all(table, library):
    """Not merely "not the right colour": no pawn name, and no pawn's colour.

    A badge that showed SOME colour would narrow the field, and one that named
    a pawn would be worse than useless. Checked against every pawn in the
    library rather than against the one that happens to be the secret.
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat != seat)
    screen.focus_seat(seat)

    said = drawn_strings(screen)
    rect = identity_rect(screen)

    for pawn in room.state.library.pawns:
        assert pawn.name.upper() not in said, f"{pawn.name} was named"
        assert not shows_colour(screen.app.canvas, rect, pawn.color), \
            f"{pawn.name} was painted in the badge"


def test_the_badge_refuses_even_when_the_secret_is_planted_in_the_replica(
        library):
    """BELT AND BRACES: the UI refuses on its own, not because the data is thin.

    A hunter's replica holds ``None`` today, so every other test in this file
    would still pass against a panel that drew whatever it was given. This one
    writes the real colour into the hunter's OWN copy of the state — the exact
    thing a future data-flow regression would do — and then asks the panel to
    draw it. The badge must still refuse, because it asks who is LOOKING and
    never gets as far as reading the field.

    It also pins the constancy: masked before the planted colour and masked
    after, pixel for pixel. A badge that changed would be a clock telling the
    table when Piotrek made up his mind.
    """
    table = Table(library)
    try:
        host, clients, screens = online_screens(table, library,
                                                "Kuba", "Ola", "Norbert")
        room = table.room(host.room_code)
        seat = piotrek_seat_of(room.state)
        pawn = room.state.library.pawn(room.state.piotrek_pawn)
        screen = next(s for s in screens if s.my_seat != seat)
        screen.focus_seat(seat)
        rect = identity_rect(screen)

        frame(screen)
        empty_handed = pixels_of(screen.app.canvas, rect)

        # The regression, simulated: this client now knows the secret.
        screen.state.player(seat).secret_pawn = pawn.id
        assert screen.state.piotrek_pawn == pawn.id, "the plant took"
        said = drawn_strings(screen)
        planted = pixels_of(screen.app.canvas, rect)

        assert pawn.name.upper() not in said, "the planted colour was named"
        assert not shows_colour(screen.app.canvas, rect, pawn.color), \
            "the panel drew a colour it had been handed"
        assert planted == empty_handed, \
            "the badge changed once the colour was available"
    finally:
        table.close()


# ── switching seats leaves nothing behind ────────────────────────────────────
def test_switching_from_piotrek_to_a_hunter_leaves_no_stale_colour(table,
                                                                   library):
    """Piotrek looks at somebody else: his own colour must not stay on screen."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat == seat)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)
    other = next(i for i in range(len(screen.state.players)) if i != seat)

    frame(screen)                                   # his own panel first
    assert shows_colour(screen.app.canvas, identity_rect(screen), pawn.color)

    screen.focus_seat(other)
    said = drawn_strings(screen)

    assert pawn.name.upper() not in said, "his colour followed him"
    assert not shows_colour(screen.app.canvas, identity_rect(screen),
                            pawn.color), "stale pixels from the previous panel"


def test_a_hunter_switching_to_piotrek_inherits_nothing(table, library):
    """The reverse: their own panel, then Piotrek's, in one screen's lifetime."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)
    screen = next(s for s in screens if s.my_seat != seat)

    frame(screen)
    screen.focus_seat(seat)
    said = drawn_strings(screen)

    assert pawn.name.upper() not in said
    assert not shows_colour(screen.app.canvas, identity_rect(screen),
                            pawn.color)


def test_piotrek_viewing_a_hunter_shows_no_secret_for_that_hunter(table,
                                                                  library):
    """A hunter has no secret colour, and the panel must not invent one."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat == seat)
    other = next(i for i in range(len(screen.state.players)) if i != seat)

    screen.focus_seat(other)
    said = drawn_strings(screen)

    assert not screen.state.player(other).is_piotrek
    for pawn in room.state.library.pawns:
        assert pawn.name.upper() not in said


# ── hot-seat keeps what it had ───────────────────────────────────────────────
def test_hot_seat_still_shows_the_colour(library):
    """One keyboard, one person, every seat theirs — nobody to hide it from.

    This is the case the badge was written for and it must not be collateral
    damage: a fix that hid the colour from Piotrek himself would satisfy every
    secrecy test in this file.
    """
    screen = a_local_piotrek_screen(library)
    seat = piotrek_seat_of(screen.state)
    screen.focus_seat(seat)
    pawn = screen.state.library.pawn(screen.state.piotrek_pawn)

    said = drawn_strings(screen)

    assert screen.entitled_to_secrets, "hot-seat is entitled to everything"
    assert pawn.name.upper() in said
    assert shows_colour(screen.app.canvas, identity_rect(screen), pawn.color)


# ── the awkward cases ────────────────────────────────────────────────────────
def test_reconnecting_does_not_change_who_may_see_it(table, library):
    """Coming back through the grace period restores the MATCH, not a right."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)
    hunter = next(s for s in [host, *clients] if s.state.local_seat != seat)
    screen = next(s for s in screens if s.my_seat != seat)
    screen.focus_seat(seat)

    hunter.client.transport.drop()
    table.pump()
    hunter.client.transport.restore()
    table.pump(20)

    assert hunter.disconnected is None, "the hunter came back"
    assert hunter.state.piotrek_pawn is None, "the resync handed over the secret"
    said = drawn_strings(screen)
    assert pawn.name.upper() not in said
    assert not shows_colour(screen.app.canvas, identity_rect(screen),
                            pawn.color)


def test_an_ordinary_online_table_cannot_reach_piotreks_panel_at_all(table,
                                                                     library):
    """Edit mode off: the older guard is still there and still first.

    ``may_view`` refuses the seat outright, so the badge question never arises.
    Checked because stage 58 must not have replaced that guard with itself.
    """
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", edit_mode=False)
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat != seat)

    assert not screen.may_view(seat)
    screen.focus_seat(seat)
    assert screen.view_seat != seat, "may_view let a hunter in"
    said = drawn_strings(screen)
    for pawn in room.state.library.pawns:
        assert pawn.name.upper() not in said


def test_edit_mode_manual_inspection_cannot_bypass_it(table, library):
    """The bypass the report used, spelled out: set the view by hand.

    Further than any click can reach — no tile, no key, straight at the field —
    and the badge still refuses, because it asks who is LOOKING.
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)
    screen = next(s for s in screens if s.my_seat != seat)

    screen.view_seat = seat
    said = drawn_strings(screen)

    assert screen.state.may_control(seat), "editing does allow control"
    assert not screen.entitled_to_secrets, "but control is not entitlement"
    assert pawn.name.upper() not in said
    assert not shows_colour(screen.app.canvas, identity_rect(screen),
                            pawn.color)


def test_a_new_match_shows_no_colour_from_the_old_one(table, library):
    """Nothing carries over: a fresh table, freshly asked."""
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    old_pawn = room.state.library.pawn(room.state.piotrek_pawn)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat != seat)
    screen.focus_seat(seat)
    frame(screen)

    room.state.player(seat).secret_pawn = None
    for player in room.state.players:
        player.secret_pawn = None
    said = drawn_strings(screen)

    assert old_pawn.name.upper() not in said
    assert not shows_colour(screen.app.canvas, identity_rect(screen),
                            old_pawn.color)


def test_the_entitlement_follows_the_seat_not_the_character(table, library):
    """Role changes: the rule is about SEATS, and reads the role each frame.

    Nothing caches which seat is Piotrek's — the badge asks the state every
    time it draws — so a role that moves takes the entitlement with it. Written
    against a moved role rather than a moved view, because a cached
    ``piotrek_seat`` is exactly the shortcut this must not grow.
    """
    host, clients, screens = online_screens(table, library,
                                            "Kuba", "Ola", "Norbert")
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    mine = next(s for s in screens if s.my_seat == seat)

    assert mine.entitled_to_secrets, "his own seat, his own secret"
    other = next(i for i in range(len(mine.state.players)) if i != seat)
    mine.focus_seat(other)
    assert not mine.entitled_to_secrets, \
        "Piotrek looking elsewhere is not entitled there"
    mine.return_to_my_seat()
    assert mine.entitled_to_secrets, "and back again"


# ── every resolution ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_badge_keeps_its_secret_at_every_resolution(table, library, size):
    """The badge is laid out by ``Layout`` and its radius scales with the
    window, so a swatch that fitted at 1280 might paint at 3840."""
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", size=size)
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    pawn = room.state.library.pawn(room.state.piotrek_pawn)

    mine = next(s for s in screens if s.my_seat == seat)
    said = drawn_strings(mine)
    assert pawn.name.upper() in said, f"Piotrek lost his colour at {size}"
    assert shows_colour(mine.app.canvas, identity_rect(mine), pawn.color)

    theirs = next(s for s in screens if s.my_seat != seat)
    theirs.focus_seat(seat)
    said = drawn_strings(theirs)
    assert pawn.name.upper() not in said, f"the colour leaked at {size}"
    assert "UKRYTA TOŻSAMOŚĆ" in said
    assert not shows_colour(theirs.app.canvas, identity_rect(theirs),
                            pawn.color), f"the swatch leaked at {size}"


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_masked_badge_stays_inside_its_band(table, library, size):
    """It paints, and it paints where the badge has always been.

    A masked row that painted nothing would pass every secrecy test in this
    file by leaving a hole in the panel; one that overflowed would push the
    portrait around.
    """
    host, clients, screens = online_screens(table, library, "Kuba", "Ola",
                                            "Norbert", size=size)
    room = table.room(host.room_code)
    seat = piotrek_seat_of(room.state)
    screen = next(s for s in screens if s.my_seat != seat)
    screen.focus_seat(seat)
    frame(screen)

    rects = screen.app.layout.character_panel(True)
    band = rects["identity"]
    assert band.bottom <= rects["portrait"].top, "the badge moved"
    assert screen.app.layout.right_panel.contains(band)

    pixels = pixels_of(screen.app.canvas, band)
    background = screen.app.renderer.theme.background
    assert any(distance(p, background) > 30 for p in pixels), \
        f"the masked badge painted nothing at {size}"
