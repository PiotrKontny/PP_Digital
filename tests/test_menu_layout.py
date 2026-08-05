"""
Menu layout and text-entry tests.

Two things this stage set out to fix, both checked mechanically rather than by
eye — which matters, because "no overlapping text" is exactly the sort of
regression that creeps back in the next time a line is added.

The overlap check records every piece of text a screen draws (by wrapping the
renderer) and asserts that no two of them share pixels and that all of them stay
inside the window, at every resolution the game supports.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame
import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config.settings import RULES
from netkit import Table
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.menu import MenuScreen
from pedzacy_piotrek.ui.network_screens import (
    HostSetupScreen,
    JoinScreen,
    LobbyScreen,
    MainMenuScreen,
)
from pedzacy_piotrek.ui.widgets import TextInput

SIZES = [(1280, 760), (1600, 900), (1920, 1080), (2560, 1440)]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


class TextRecorder:
    """Wraps Renderer.text and remembers where every string landed."""

    def __init__(self, renderer) -> None:
        self.renderer = renderer
        self.drawn = []
        self._original = renderer.text

    def __enter__(self) -> "TextRecorder":
        def recording(text, font, colour, surface=None, **anchors):
            rect = self._original(text, font, colour, surface, **anchors)
            if str(text).strip():
                self.drawn.append((str(text), pygame.Rect(rect)))
            return rect

        self.renderer.text = recording
        return self

    def __exit__(self, *exc) -> None:
        self.renderer.text = self._original

    def overlaps(self):
        """Pairs of strings whose rectangles collide."""
        clashes = []
        for i, (text_a, rect_a) in enumerate(self.drawn):
            for text_b, rect_b in self.drawn[i + 1:]:
                # A one-pixel touch is not a collision; require real overlap.
                shared = rect_a.clip(rect_b)
                if shared.width > 1 and shared.height > 1:
                    clashes.append((text_a, text_b, tuple(shared)))
        return clashes

    def outside(self, width: int, height: int):
        window = pygame.Rect(0, 0, width, height)
        return [(text, tuple(rect)) for text, rect in self.drawn
                if not window.contains(rect)]


def render_once(app: App, screen) -> TextRecorder:
    with TextRecorder(app.renderer) as recorder:
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)
    return recorder


def check(app: App, screen) -> None:
    recorder = render_once(app, screen)
    assert recorder.drawn, "the screen drew no text at all"
    clashes = recorder.overlaps()
    assert not clashes, f"overlapping text: {clashes[:4]}"
    escaped = recorder.outside(app.layout.win_w, app.layout.win_h)
    assert not escaped, f"text outside the window: {escaped[:4]}"


# ── every menu, every resolution ─────────────────────────────────────────────
@pytest.mark.parametrize("size", SIZES)
def test_the_main_menu_has_no_overlapping_text(library, size):
    app = App(Layout(), headless=True, size=size)
    screen = MainMenuScreen(app, library)
    app.push(screen)
    screen.notify("Host zakończył grę")      # the longest thing it ever shows
    check(app, screen)


@pytest.mark.parametrize("size", SIZES)
def test_the_host_screen_has_no_overlapping_text(library, size):
    app = App(Layout(), headless=True, size=size)
    screen = HostSetupScreen(app, library)
    app.push(screen)
    screen.error = "Nie udało się połączyć z serwerem wss://piotrek.example.com"
    check(app, screen)


@pytest.mark.parametrize("size", SIZES)
def test_the_join_screen_has_no_overlapping_text(library, size):
    app = App(Layout(), headless=True, size=size)
    screen = JoinScreen(app, library)
    app.push(screen)
    screen.error = "Serwer wss://piotrek.example.com odrzucił połączenie"
    check(app, screen)


@pytest.mark.parametrize("size", SIZES)
def test_the_local_setup_screen_has_no_overlapping_text(library, size):
    app = App(Layout(), headless=True, size=size)
    screen = MenuScreen(app, library, lambda config: None)
    app.push(screen)
    check(app, screen)


@pytest.mark.parametrize("size", SIZES)
def test_the_lobby_has_no_overlapping_text_when_full(library, size):
    """The worst case: six seats, long nicknames, and the host's address."""
    app = App(Layout(), headless=True, size=size)
    table = Table(library)
    try:
        host, clients = table.seated("Bardzo Długi Nick",
                                     *[f"Gracz Numer {i}"
                                       for i in range(RULES.max_players - 1)])
        clients[0].set_character("Piotrek")
        table.pump()
        screen = LobbyScreen(app, library, host)
        app.push(screen)
        check(app, screen)
    finally:
        table.close()


@pytest.mark.parametrize("size", SIZES)
def test_the_lobby_has_no_overlapping_text_when_empty(library, size):
    app = App(Layout(), headless=True, size=size)
    table = Table(library)
    try:
        host = table.host("Kuba")
        screen = LobbyScreen(app, library, host)
        app.push(screen)
        check(app, screen)          # includes the "not enough players" warning
    finally:
        table.close()


def test_lobby_rows_do_not_reach_the_controls_below(library):
    """A full table must not push the seat list into the character picker."""
    for size in SIZES:
        app = App(Layout(), headless=True, size=size)
        table = Table(library)
        try:
            host, _ = table.seated("Kuba", *[f"Gracz {i}"
                                             for i in range(RULES.max_players - 1)])
            screen = LobbyScreen(app, library, host)
            app.push(screen)
            screen.update(1 / 60, (0, 0))
            last = screen.seat_rect(len(screen.lobby.seats) - 1)
            assert last.bottom <= screen.character.rect.top
            assert screen.character.rect.bottom <= screen.start.rect.top
            assert screen.start.rect.bottom <= screen.leave.rect.top
            assert screen.leave.rect.bottom <= app.layout.win_h
        finally:
            table.close()


# ── text entry ───────────────────────────────────────────────────────────────
def type_key(field: TextInput, character: str) -> None:
    """Exactly what SDL delivers for one keystroke: KEYDOWN *and* TEXTINPUT."""
    field.handle(pygame.event.Event(pygame.KEYDOWN, key=ord(character[0]),
                                    unicode=character))
    field.handle(pygame.event.Event(pygame.TEXTINPUT, text=character))


def press(field: TextInput, key: int) -> None:
    field.handle(pygame.event.Event(pygame.KEYDOWN, key=key, unicode=""))


def test_a_keystroke_inserts_exactly_one_character():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick")
    field.focus()
    for character in "Kuba":
        type_key(field, character)
    assert field.value == "Kuba"


def test_polish_letters_survive():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick")
    field.focus()
    for character in "Żółw":
        type_key(field, character)
    assert field.value == "Żółw"


def test_one_backspace_deletes_one_character():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick", "Kuba")
    field.focus()
    press(field, pygame.K_BACKSPACE)
    assert field.value == "Kub"
    press(field, pygame.K_BACKSPACE)
    assert field.value == "Ku"


def test_holding_backspace_keeps_deleting(monkeypatch):
    """Held keys repeat on the field's own timer, like a desktop text box."""
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick", "Kubasinski")
    field.focus()
    held = {pygame.K_BACKSPACE: True}
    monkeypatch.setattr(pygame.key, "get_pressed",
                        lambda: _Pressed(held))

    press(field, pygame.K_BACKSPACE)
    assert field.value == "Kubasinsk", "the first delete is immediate"

    # Still inside the initial delay: nothing more happens yet.
    field.update((0, 0), TextInput.REPEAT_DELAY * 0.5)
    assert field.value == "Kubasinsk"

    field.update((0, 0), TextInput.REPEAT_DELAY * 0.6 + TextInput.REPEAT_INTERVAL * 3)
    assert len(field.value) < 9, "then it repeats while held"

    remaining = len(field.value)
    held[pygame.K_BACKSPACE] = False
    field.update((0, 0), 1.0)
    assert len(field.value) == remaining, "and stops when released"


class _Pressed:
    """Stand-in for pygame.key.get_pressed(), which reports nothing headless."""

    def __init__(self, held) -> None:
        self.held = held

    def __getitem__(self, key: int) -> bool:
        return bool(self.held.get(key, False))


def test_releasing_the_key_stops_the_repeat():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick", "Kuba")
    field.focus()
    press(field, pygame.K_BACKSPACE)
    field.handle(pygame.event.Event(pygame.KEYUP, key=pygame.K_BACKSPACE))
    field.update((0, 0), 5.0)
    assert field.value == "Kub"


def test_a_numeric_field_ignores_letters():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Port", numeric=True)
    field.focus()
    for character in "4a7b6":
        type_key(field, character)
    assert field.value == "476"


def test_a_field_stops_at_its_maximum_length():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick", max_length=4)
    field.focus()
    for character in "Kubasinski":
        type_key(field, character)
    assert field.value == "Kuba"


def test_an_unfocused_field_ignores_typing():
    field = TextInput(pygame.Rect(0, 0, 200, 40), "Nick")
    type_key(field, "K")
    assert field.value == ""


def test_clicking_a_field_focuses_it_and_blurs_the_others(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = JoinScreen(app, library)
    app.push(screen)
    screen.update(1 / 60, (0, 0))

    centre = screen.nickname.rect.center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=centre, button=1), centre
    )
    assert screen.nickname.focused
    assert not screen.code.focused and not screen.server.focused

    for character in "Ola":
        type_key(screen.nickname, character)
    assert screen.nickname.value == "Ola"


def test_tab_moves_between_fields(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = JoinScreen(app, library)
    app.push(screen)
    assert screen.code.focused

    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB), (0, 0))
    assert screen.nickname.focused and not screen.code.focused
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB), (0, 0))
    assert screen.server.focused
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB), (0, 0))
    assert screen.code.focused, "and wraps around"


def test_typing_into_one_field_leaves_the_others_alone(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = JoinScreen(app, library)
    app.push(screen)
    for character in "K7M":
        type_key(screen.code, character)
    assert screen.code.value == "K7M"
    assert screen.server.value.startswith("ws")
    assert screen.nickname.value == ""


# ── the development option ───────────────────────────────────────────────────
def test_the_local_setup_screen_can_drop_to_two_players(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    captured = []
    screen = MenuScreen(app, library, captured.append)
    app.push(screen)
    screen.update(1 / 60, (0, 0))

    minus = screen.players_stepper.rects["minus1"].center
    for _ in range(6):
        screen.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=minus, button=1), minus
        )
    assert screen.num_players == RULES.min_players, "the floor without the option"

    centre = screen.debug_checkbox.rect.center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=centre, button=1), centre
    )
    assert screen.debug_version
    assert screen.minimum_players == RULES.debug_min_players
    for _ in range(3):
        screen.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=minus, button=1), minus
        )
    assert screen.num_players == 2

    start = screen.start_button.rect.center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1), start
    )
    assert captured and captured[0].num_players == 2
    assert captured[0].debug_version is True


def test_turning_the_option_off_restores_the_normal_minimum(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = MenuScreen(app, library, lambda config: None)
    app.push(screen)
    centre = screen.debug_checkbox.rect.center

    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=centre, button=1), centre
    )
    screen.num_players = 2
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=centre, button=1), centre
    )
    assert not screen.debug_version
    assert screen.num_players >= RULES.min_players


def test_a_two_player_game_actually_runs(library):
    """The option only relaxes the count; everything else behaves normally."""
    from pedzacy_piotrek.config.settings import SessionConfig
    from pedzacy_piotrek.engine.setup import create_game

    config = SessionConfig(num_players=2, board_cells=24, seed=3,
                           debug_version=True).normalised()
    assert config.num_players == 2
    state = create_game(config, library)
    assert len(state.players) == 2
    assert sum(1 for p in state.players if p.is_piotrek) == 1
    assert state.turn_order(), "the turn cadence copes with a single hunter"


def test_the_host_screen_offers_the_option(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = HostSetupScreen(app, library)
    app.push(screen)
    screen.update(1 / 60, (0, 0))
    assert not screen.debug_version

    centre = screen.debug_checkbox.rect.center
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=centre, button=1), centre
    )
    assert screen.debug_version
    # Ticking it must survive the trip to the server, because the minimum is a
    # rule the server enforces, not a hint the menu keeps to itself.
    table = Table(library)
    try:
        host, _ = table.seated("Kuba")
        host.set_settings(debug_version=True)
        table.pump()
        assert host.lobby_state.debug_version
        assert host.lobby_state.minimum_players == RULES.debug_min_players
    finally:
        table.close()


def test_a_two_player_lobby_can_start_only_with_the_option(library):
    table = Table(library)
    try:
        host, (ola,) = table.seated("Kuba", "Ola")
        assert not host.lobby_state.can_start
        assert str(RULES.min_players) in host.lobby_state.validate()

        host.set_settings(debug_version=True)
        table.pump()
        assert host.lobby_state.can_start
        assert host.lobby_state.to_config(seed=1).num_players == 2
    finally:
        table.close()


# ── desktop text-box behaviour (stage 8) ─────────────────────────────────────
def ctrl(field: TextInput, key: int) -> None:
    pygame.key.set_mods(pygame.KMOD_CTRL)
    try:
        field.handle(pygame.event.Event(pygame.KEYDOWN, key=key, unicode=""))
    finally:
        pygame.key.set_mods(0)


def shift(field: TextInput, key: int) -> None:
    pygame.key.set_mods(pygame.KMOD_SHIFT)
    try:
        field.handle(pygame.event.Event(pygame.KEYDOWN, key=key, unicode=""))
    finally:
        pygame.key.set_mods(0)


def focused_field(value: str = "", **kwargs) -> TextInput:
    field = TextInput(pygame.Rect(100, 100, 300, 40), "Nick", value, **kwargs)
    field.focus()
    return field


def test_select_all_then_typing_replaces_everything():
    field = focused_field("Kubasinski")
    ctrl(field, pygame.K_a)
    assert field.selected_text == "Kubasinski"
    type_key(field, "O")
    assert field.value == "O"
    assert field.selection is None


def test_copy_and_paste():
    from pedzacy_piotrek.ui import clipboard

    clipboard.reset()
    source = focused_field("Byd")
    ctrl(source, pygame.K_a)
    ctrl(source, pygame.K_c)
    assert source.value == "Byd", "copying does not remove anything"

    target = focused_field("")
    ctrl(target, pygame.K_v)
    assert target.value == "Byd"


def test_cut_removes_the_selection_and_keeps_it_for_pasting():
    from pedzacy_piotrek.ui import clipboard

    clipboard.reset()
    field = focused_field("Kuba")
    ctrl(field, pygame.K_a)
    ctrl(field, pygame.K_x)
    assert field.value == ""

    ctrl(field, pygame.K_v)
    assert field.value == "Kuba"


def test_backspace_deletes_a_selection_in_one_press():
    field = focused_field("192.168.0.12")
    ctrl(field, pygame.K_a)
    press(field, pygame.K_BACKSPACE)
    assert field.value == ""


def test_delete_also_clears_a_selection():
    field = focused_field("Kuba")
    ctrl(field, pygame.K_a)
    press(field, pygame.K_DELETE)
    assert field.value == ""


def test_the_caret_can_be_moved_and_edited_in_the_middle():
    field = focused_field("Kuba")
    press(field, pygame.K_LEFT)
    press(field, pygame.K_LEFT)
    assert field.caret == 2
    type_key(field, "X")
    assert field.value == "KuXba"
    press(field, pygame.K_BACKSPACE)
    assert field.value == "Kuba"


def test_home_and_end_jump_to_the_edges():
    field = focused_field("Kuba")
    press(field, pygame.K_HOME)
    assert field.caret == 0
    type_key(field, "!")
    assert field.value == "!Kuba"
    press(field, pygame.K_END)
    assert field.caret == len(field.value)


def test_shift_arrows_extend_a_selection():
    field = focused_field("Kuba")
    press(field, pygame.K_END)
    shift(field, pygame.K_LEFT)
    shift(field, pygame.K_LEFT)
    assert field.selected_text == "ba"
    press(field, pygame.K_BACKSPACE)
    assert field.value == "Ku"


def test_clicking_places_the_caret(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = JoinScreen(app, library)
    app.push(screen)
    screen.server.value = "ws://192.168.0.12"
    render_once(app, screen)          # gives the field its font

    field = screen.server
    # Click just after the third character.
    x = field.rect.left + field.PADDING + field._measure("ws:")
    position = (x, field.rect.centery)
    field.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=position, button=1))
    assert field.caret == 3
    assert field.selection is None, "a click is not a selection"

    type_key(field, "X")
    assert field.value == "ws:X//192.168.0.12"


def test_dragging_selects_text(library):
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = JoinScreen(app, library)
    app.push(screen)
    screen.server.value = "ws://192.168.0.12"
    render_once(app, screen)

    field = screen.server
    start = (field.rect.left + field.PADDING, field.rect.centery)
    end = (field.rect.left + field.PADDING + field._measure("ws:"),
           field.rect.centery)
    field.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1))
    field.handle(pygame.event.Event(pygame.MOUSEMOTION, pos=end, rel=(1, 0),
                                    buttons=(1, 0, 0)))
    field.handle(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=1))
    assert field.selected_text == "ws:"

    type_key(field, "X")
    assert field.value.startswith("X")
    assert not field.value.startswith("ws:")


def test_the_placeholder_is_not_the_value(library):
    field = TextInput(pygame.Rect(0, 0, 300, 40), "Adres", "",
                      placeholder="np. 192.168.0.12")
    assert field.value == ""
    field.focus()
    type_key(field, "1")
    assert field.value == "1", "typing does not append to the placeholder"


def test_the_placeholder_is_drawn_dimmer_than_real_text(library):
    """A hint must never look like something the player typed."""
    app = App(Layout(), headless=True, size=(1600, 900))
    screen = JoinScreen(app, library)
    app.push(screen)
    screen.code.value = ""
    screen.code.blur()

    with TextRecorder(app.renderer) as recorder:
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.draw(app.canvas)
    placeholder = next(rect for text, rect in recorder.drawn
                       if text == screen.code.placeholder)

    def brightest(rect):
        best = 0
        for x in range(rect.left, rect.right, 2):
            for y in range(rect.top, rect.bottom, 2):
                best = max(best, sum(app.canvas.get_at((x, y))[:3]))
        return best

    hint_brightness = brightest(placeholder)

    screen.code.value = "192.168.0.12"
    with TextRecorder(app.renderer) as recorder:
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.draw(app.canvas)
    typed = next(rect for text, rect in recorder.drawn if text == "192.168.0.12")
    assert brightest(typed) > hint_brightness + 40
