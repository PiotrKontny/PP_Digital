"""
Stage 14: text editing everywhere, copy buttons, and a remembered server.

Nothing here is about networking behaviour — the wire is unchanged. It is about
whether a person can get their hands on the game: paste a nickname, copy a room
code into a chat window, and not retype a server address every session.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pygame
import pytest

from netkit import Table

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.net import config as netconfig
from pedzacy_piotrek.net.lobby import DEFAULT_NICKNAME
from pedzacy_piotrek.ui import clipboard
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.network_screens import (COPY_NOTICE_SECONDS, CopyNotice,
                                                HostSetupScreen, JoinScreen,
                                                LobbyScreen)
from pedzacy_piotrek.ui.widgets import TextEditor, TextField, TextInput

RESOLUTIONS = [(1280, 760), (1920, 1080), (2560, 1440), (3840, 2160)]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture(scope="module", autouse=True)
def _display():
    app = App(Layout(), headless=True, size=(1280, 760))
    yield app
    pygame.quit()


@pytest.fixture(autouse=True)
def _clean_clipboard():
    clipboard.reset()
    yield
    clipboard.reset()


def make_app(size=(1280, 760)) -> App:
    return App(Layout(), headless=True, size=size)


def _key(field, key, mods=0):
    pygame.key.set_mods(mods)
    result = field.handle(pygame.event.Event(pygame.KEYDOWN, key=key, mod=mods))
    pygame.key.set_mods(0)
    return result


def _type(field, text):
    field.handle(pygame.event.Event(pygame.TEXTINPUT, text=text))


def _click(field, x, y):
    field.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x, y)))


# ── 1. every field is a real text box ────────────────────────────────────────
def _all_fields():
    """One of every editable surface in the application.

    If a new kind of text entry is added and not listed here, the shared-editor
    tests below stop covering the application — which is the failure mode this
    helper exists to make obvious.
    """
    box = TextInput(pygame.Rect(100, 100, 300, 40), "Nick", "Kuba")
    box.focus()
    rename = TextField(max_length=16)
    rename.start(0, "Kuba")
    return [("TextInput", box), ("TextField", rename)]


@pytest.mark.parametrize("name, field", _all_fields())
def test_every_field_supports_select_all_and_replace(name, field):
    _key(field, pygame.K_a, pygame.KMOD_CTRL)
    _type(field, "Ola")
    assert field.value if name == "TextInput" else field.buffer == "Ola"


@pytest.mark.parametrize("name, field", _all_fields())
def test_every_field_copies_cuts_and_pastes(name, field):
    text = lambda: field.value if name == "TextInput" else field.buffer
    _key(field, pygame.K_a, pygame.KMOD_CTRL)
    _key(field, pygame.K_c, pygame.KMOD_CTRL)
    assert clipboard.paste() == "Kuba"
    _key(field, pygame.K_END)
    _key(field, pygame.K_v, pygame.KMOD_CTRL)
    assert text() == "KubaKuba"
    _key(field, pygame.K_a, pygame.KMOD_CTRL)
    _key(field, pygame.K_x, pygame.KMOD_CTRL)
    assert text() == ""


@pytest.mark.parametrize("name, field", _all_fields())
def test_every_field_moves_its_caret_with_home_and_end(name, field):
    editor = field.editor
    _key(field, pygame.K_HOME)
    assert editor.caret == 0
    _key(field, pygame.K_END)
    assert editor.caret == 4


@pytest.mark.parametrize("name, field", _all_fields())
def test_every_field_extends_a_selection_with_shift(name, field):
    _key(field, pygame.K_HOME)
    _key(field, pygame.K_RIGHT, pygame.KMOD_SHIFT)
    _key(field, pygame.K_RIGHT, pygame.KMOD_SHIFT)
    assert field.editor.selected_text == "Ku"


# ── holding backspace ────────────────────────────────────────────────────────
class _Pressed:
    """Stand-in for ``pygame.key.get_pressed``, which reports nothing headless.

    ``TextEditor.tick`` asks the keyboard whether the key is still down, so
    without this the repeat correctly stops on the first frame and the test
    proves nothing.
    """

    def __init__(self, held) -> None:
        self.held = held

    def __getitem__(self, key: int) -> bool:
        return bool(self.held.get(key, False))


@pytest.fixture
def keyboard(monkeypatch):
    """A dictionary of keys that are 'down'."""
    held: dict = {}
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: _Pressed(held))
    return held


def _hold(editor, key, seconds, steps=60):
    editor.handle_key(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0))
    for _ in range(steps):
        editor.tick(seconds / steps)


def test_holding_backspace_keeps_deleting(keyboard):
    keyboard[pygame.K_BACKSPACE] = True
    editor = TextEditor("Bardzo długa nazwa gracza")
    before = len(editor.value)
    _hold(editor, pygame.K_BACKSPACE, TextEditor.REPEAT_DELAY + 0.35)
    assert len(editor.value) < before - 3, editor.value


def test_a_tap_of_backspace_deletes_exactly_one(keyboard):
    """The repeat must not start before the delay, or typing becomes lossy."""
    keyboard[pygame.K_BACKSPACE] = True
    editor = TextEditor("Kuba")
    editor.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE))
    editor.tick(TextEditor.REPEAT_DELAY * 0.5)
    assert editor.value == "Kub"


def test_releasing_the_key_stops_the_repeat(keyboard):
    keyboard[pygame.K_BACKSPACE] = True
    editor = TextEditor("Kuba Nowak")
    editor.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE))
    editor.release()
    editor.tick(5.0)
    assert editor.value == "Kuba Nowa"


def test_a_key_the_keyboard_says_is_up_stops_repeating(keyboard):
    """KEYUP can be eaten by a focus change; the keyboard is the truth."""
    keyboard[pygame.K_BACKSPACE] = True
    editor = TextEditor("Kuba Nowak")
    editor.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE))
    keyboard[pygame.K_BACKSPACE] = False
    editor.tick(5.0)
    assert editor.value == "Kuba Nowa"


def test_the_rename_box_repeats_too(keyboard):
    """This is the field that did not, before this stage."""
    keyboard[pygame.K_BACKSPACE] = True
    rename = TextField(max_length=32)
    rename.start(0, "Bardzo długa nazwa")
    _key(rename, pygame.K_END)
    _key(rename, pygame.K_BACKSPACE)
    for _ in range(60):
        rename.update((TextEditor.REPEAT_DELAY + 0.35) / 60)
    assert len(rename.buffer) < len("Bardzo długa nazwa") - 3


# ── placeholders ─────────────────────────────────────────────────────────────
def test_a_placeholder_is_dimmer_than_typed_text(library):
    """Measured, not asserted by eye: the hint must be darker than real text."""
    app = make_app()
    screen = JoinScreen(app, library)
    app.push(screen)
    field = screen.code

    def brightest(value: str, text: str) -> int:
        field.value = value
        field.blur()
        app.renderer.begin(app.canvas)
        app.canvas.fill(app.renderer.theme.background)
        screen.draw(app.canvas)
        best = 0
        for x in range(field.rect.left + 4, field.rect.right - 4, 2):
            for y in range(field.rect.top + 4, field.rect.bottom - 4, 2):
                best = max(best, sum(app.canvas.get_at((x, y))[:3]))
        return best

    hint = brightest("", field.placeholder)
    typed = brightest("K7M2QD", "K7M2QD")
    assert typed > hint + 40, (typed, hint)


def test_the_rename_box_reports_when_it_is_showing_a_hint():
    """The HUD needs to know, because it picks the colour."""
    rename = TextField(max_length=16, placeholder="wpisz nazwę")
    rename.start(0, "")
    assert rename.showing_placeholder
    rename.editor.insert("K")
    assert not rename.showing_placeholder


@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_every_field_on_a_form_has_a_hint_or_a_value(screen_class, library):
    """An empty box with no label inside it tells the player nothing."""
    app = make_app()
    screen = screen_class(app, library)
    for field in screen.inputs:
        assert field.placeholder or field.value, field.label


def test_the_two_forms_agree_about_the_nickname_field(library):
    """Host used to PRE-FILL it, so the player had to delete before typing."""
    app = make_app()
    host = HostSetupScreen(app, library)
    join = JoinScreen(app, library)
    assert host.nickname.value == join.nickname.value == ""
    assert host.nickname.placeholder == join.nickname.placeholder == DEFAULT_NICKNAME


# ── 2 & 3. the copy buttons ──────────────────────────────────────────────────
def test_the_copy_notice_lasts_about_two_seconds():
    notice = CopyNotice()
    notice.show("✓ Skopiowano")
    assert notice.visible
    notice.update(COPY_NOTICE_SECONDS * 0.5)
    assert notice.visible
    notice.update(COPY_NOTICE_SECONDS)
    assert not notice.visible


def test_the_copy_notice_fades_rather_than_vanishing():
    notice = CopyNotice()
    notice.show("✓")
    full = notice.fade()
    notice.update(COPY_NOTICE_SECONDS * 0.9)
    assert 0.0 < notice.fade() < full


def test_the_lobby_copies_only_the_room_code(library):
    table = Table(library)
    try:
        host = table.host("Kuba")
        app = make_app()
        screen = LobbyScreen(app, library, host)
        code = host.room_code
        assert code

        screen._copy_code()

        assert clipboard.paste() == code, "only the code belongs on the clipboard"
        assert "KOD" not in clipboard.paste()
        assert screen.copied.visible
    finally:
        table.close()


def test_the_copy_code_button_is_dead_until_there_is_a_code(library):
    table = Table(library)
    try:
        host = table.host("Kuba")
        app = make_app()
        screen = LobbyScreen(app, library, host)
        screen.service.client.lobby_state.code = ""
        screen.update(0.016, (0, 0))
        assert not screen.copy_code.enabled
        screen._copy_code()
        assert not screen.copied.visible
    finally:
        table.close()


@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_the_copy_button_copies_the_server_address(screen_class, library):
    app = make_app()
    screen = screen_class(app, library)
    screen.server.value = "wss://piotrek.up.railway.app"
    screen._copy_server_address(screen.server)
    assert clipboard.paste() == "wss://piotrek.up.railway.app"
    assert screen.copied.visible


@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_an_empty_server_field_copies_nothing(screen_class, library):
    app = make_app()
    screen = screen_class(app, library)
    screen.server.value = "   "
    screen._copy_server_address(screen.server)
    assert clipboard.paste() == ""
    assert not screen.copied.visible


# ── 5. polish: nothing overlaps, nothing escapes ─────────────────────────────
@pytest.mark.parametrize("size", RESOLUTIONS)
@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_the_copy_button_sits_beside_its_field_and_stays_on_screen(
        screen_class, size, library):
    app = make_app(size)
    screen = screen_class(app, library)
    button, field = screen.copy_server, screen.server

    assert not button.rect.colliderect(field.rect), "the button covers the field"
    assert button.rect.left >= field.rect.right, "the button is on the wrong side"
    assert button.rect.right <= app.layout.win_w, "the button runs off the window"
    assert button.rect.top >= 0 and button.rect.bottom <= app.layout.win_h
    # Aligned with the field it belongs to, not merely near it.
    assert abs(button.rect.centery - field.rect.centery) <= 1


@pytest.mark.parametrize("size", RESOLUTIONS)
@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_no_two_controls_on_a_form_overlap(screen_class, size, library):
    app = make_app(size)
    screen = screen_class(app, library)
    named = [(f"pole {f.label}", f.rect) for f in screen.inputs]
    named.append(("kopiuj", screen.copy_server.rect))
    for i, (name_a, a) in enumerate(named):
        for name_b, b in named[i + 1:]:
            assert not a.colliderect(b), f"{name_a} overlaps {name_b} at {size}"


@pytest.mark.parametrize("size", RESOLUTIONS)
def test_the_lobby_copy_button_stays_inside_the_window(size, library):
    table = Table(library)
    try:
        host = table.host("Kuba")
        app = make_app(size)
        screen = LobbyScreen(app, library, host)
        rect = screen.copy_code.rect
        assert rect.left >= 0 and rect.right <= app.layout.win_w
        assert rect.bottom <= app.layout.win_h
        # It must not land on the seat list underneath it.
        assert rect.bottom <= screen.seats_top
    finally:
        table.close()


@pytest.mark.parametrize("size", RESOLUTIONS)
@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_the_forms_still_draw_without_exploding(screen_class, size, library):
    app = make_app(size)
    screen = screen_class(app, library)
    app.push(screen)
    screen.update(0.016, (0, 0))
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.draw(app.canvas)


# ── 4. remembering the server ────────────────────────────────────────────────
@pytest.fixture
def prefs(tmp_path, monkeypatch):
    """Point the preferences file at a temporary directory."""
    monkeypatch.setattr(netconfig, "user_config_dir", lambda: tmp_path)
    return tmp_path / "preferences.json"


def test_a_working_address_is_remembered(prefs):
    assert netconfig.remember_server_url("wss://piotrek.up.railway.app")
    assert netconfig.remembered_server_url() == "wss://piotrek.up.railway.app"
    assert json.loads(prefs.read_text(encoding="utf-8"))["server_url"]


def test_the_remembered_address_survives_a_restart(prefs):
    netconfig.remember_server_url("wss://piotrek.up.railway.app")
    # A fresh load is what starting the game again does.
    config = netconfig.NetworkConfig.load(env={})
    assert config.server_url == "wss://piotrek.up.railway.app"


def test_without_a_remembered_address_the_shipped_default_is_used(prefs):
    config = netconfig.NetworkConfig.load(env={})
    assert config.server_url.startswith("ws://127.0.0.1")


def test_the_environment_still_beats_the_remembered_address(prefs):
    """Automation and --server must keep winning, or a deployment cannot
    override what one player's machine happens to have saved."""
    netconfig.remember_server_url("wss://stary.up.railway.app")
    config = netconfig.NetworkConfig.load(
        env={"PIOTREK_SERVER_URL": "wss://nowy.up.railway.app"})
    assert config.server_url == "wss://nowy.up.railway.app"


def test_an_empty_address_is_not_remembered(prefs):
    netconfig.remember_server_url("wss://dobry.up.railway.app")
    assert not netconfig.remember_server_url("   ")
    assert netconfig.remembered_server_url() == "wss://dobry.up.railway.app"


def test_an_unwritable_preferences_file_is_not_fatal(monkeypatch, tmp_path):
    """A locked profile or a full disk must not stop the game."""
    target = tmp_path / "nope"
    target.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(netconfig, "user_config_dir", lambda: target)
    assert netconfig.remember_server_url("wss://x.example.com") is False
    assert netconfig.remembered_server_url() == ""


def test_a_corrupt_preferences_file_is_ignored(prefs):
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text("{ this is not json", encoding="utf-8")
    assert netconfig.remembered_server_url() == ""
    assert netconfig.NetworkConfig.load(env={}).server_url


def test_the_lobby_remembers_the_address_once_a_room_exists(library, prefs,
                                                            monkeypatch):
    table = Table(library)
    try:
        host = table.host("Kuba")
        # The in-process transport calls itself "in-process:c3", which is not
        # an address anything could reconnect to; a real one reports its URL.
        monkeypatch.setattr(type(host), "server_url",
                            property(lambda self: "wss://piotrek.up.railway.app"))
        app = make_app()
        screen = LobbyScreen(app, library, host)
        assert host.room_code
        screen.update(0.016, (0, 0))
        assert netconfig.remembered_server_url() == "wss://piotrek.up.railway.app"
    finally:
        table.close()


def test_an_address_nothing_could_reconnect_to_is_not_remembered(library, prefs):
    """The in-process transport's name is the live example of one.

    Saved unchecked it produced ``ws://in-process:c3:51337``, whose port is not
    a number, and every later urlparse of it raised — including the one drawing
    the main menu.
    """
    table = Table(library)
    try:
        host = table.host("Kuba")
        app = make_app()
        screen = LobbyScreen(app, library, host)
        screen.update(0.016, (0, 0))
        assert netconfig.remembered_server_url() == ""
        # And the config still describes itself without raising.
        assert netconfig.NetworkConfig.load(env={}).describe_target()
    finally:
        table.close()


def test_nothing_is_remembered_before_a_room_exists(library, prefs):
    """Otherwise the field is helpfully re-filled with the typo that failed."""
    table = Table(library)
    try:
        host = table.host("Kuba")
        app = make_app()
        screen = LobbyScreen(app, library, host)
        screen.service.client.lobby_state.code = ""
        screen.update(0.016, (0, 0))
        assert netconfig.remembered_server_url() == ""
    finally:
        table.close()


# ── the clipboard itself ─────────────────────────────────────────────────────
def test_a_probe_before_the_window_exists_is_not_cached(monkeypatch):
    """Otherwise the system clipboard is off for the whole session.

    Any call made before the display is up used to pin availability to False
    permanently, silently demoting every copy and paste in the game to the
    internal buffer with nothing in a log to show for it.
    """
    clipboard.reset()
    monkeypatch.setattr(pygame.display, "get_init", lambda: False)
    assert clipboard._ensure_ready() is False
    assert clipboard._checked is False, "a missing display is not an answer"
    monkeypatch.undo()
    clipboard.reset()
    assert clipboard._ensure_ready() is True


def test_copying_survives_a_platform_with_no_clipboard(monkeypatch):
    """Copy and paste must still work inside the game on such a machine."""
    clipboard.reset()
    monkeypatch.setattr(pygame.display, "get_init", lambda: True)
    monkeypatch.setattr(pygame.scrap, "init",
                        lambda: (_ for _ in ()).throw(pygame.error("nope")))
    clipboard.copy("K7M2QD")
    assert clipboard.paste() == "K7M2QD"


# ── the clipboard is the OPERATING SYSTEM's clipboard ────────────────────────
# Every test above this line goes through ui/clipboard, so all of them passed
# happily while the module was talking to nothing but its own buffer: copying
# from the game into Discord did not work, and neither did pasting back. These
# reach around the module and touch pygame.scrap directly, which is the only
# way to tell the two apart.
def _outside_world_puts(text: str) -> None:
    """Stand in for Discord, a browser or Notepad putting text on the clipboard."""
    pygame.scrap.init()
    for text_type in clipboard._TEXT_TYPES:
        try:
            pygame.scrap.put(text_type, text.encode("utf-8"))
            return
        except Exception:
            continue
    pytest.skip("no usable system clipboard type on this platform")


def _outside_world_reads() -> str:
    """What another application would find on the clipboard right now."""
    pygame.scrap.init()
    for text_type in clipboard._TEXT_TYPES:
        try:
            raw = pygame.scrap.get(text_type)
        except Exception:
            continue
        if raw:
            return bytes(raw).rstrip(b"\x00").decode("utf-8")
    return ""


def test_copying_reaches_the_system_clipboard():
    """Game → Discord. The module used to keep this to itself entirely."""
    clipboard.copy("K7M2QD")
    assert _outside_world_reads() == "K7M2QD"


def test_pasting_reads_the_system_clipboard():
    """Discord → Game, with nothing ever copied inside the game."""
    _outside_world_puts("wss://piotrek.up.railway.app")
    assert clipboard.paste() == "wss://piotrek.up.railway.app"


def test_the_outside_world_beats_the_internal_buffer():
    """The fallback is a fallback. It must never shadow a working clipboard."""
    clipboard.copy("kopia w grze")
    _outside_world_puts("skopiowane w przeglądarce")
    assert clipboard.paste() == "skopiowane w przeglądarce"


def test_an_emptied_system_clipboard_is_not_papered_over():
    """Stale internal text pasted after the user cleared the clipboard is a lie."""
    clipboard.copy("K7M2QD")
    _outside_world_puts("")
    assert clipboard.paste() == ""


def test_polish_letters_survive_the_round_trip():
    clipboard.copy("Zażółć gęślą jaźń")
    assert _outside_world_reads() == "Zażółć gęślą jaźń"
    _outside_world_puts("Łódź")
    assert clipboard.paste() == "Łódź"


def test_a_pasted_paragraph_becomes_one_line():
    """These are single-line fields; a chat window's line break is not an error."""
    _outside_world_puts("wiersz\r\ndrugi")
    assert clipboard.paste() == "wiersz drugi"


def test_the_copy_code_button_reaches_the_system_clipboard(library):
    """Copy Room Code → the friend's chat window."""
    table = Table(library)
    try:
        host = table.host("Kuba")
        screen = LobbyScreen(make_app(), library, host)
        screen._copy_code()
        assert _outside_world_reads() == host.room_code
    finally:
        table.close()


@pytest.mark.parametrize("screen_class", [HostSetupScreen, JoinScreen])
def test_the_copy_address_button_reaches_the_system_clipboard(screen_class, library):
    screen = screen_class(make_app(), library)
    screen.server.value = "wss://piotrek.up.railway.app"
    screen._copy_server_address(screen.server)
    assert _outside_world_reads() == "wss://piotrek.up.railway.app"


def test_a_field_copies_into_the_system_clipboard(library):
    """Ctrl+C in a menu box, then Ctrl+V in any other application."""
    box = TextInput(pygame.Rect(100, 100, 300, 40), "Nick", "Kuba")
    box.focus()
    _key(box, pygame.K_a, pygame.KMOD_CTRL)
    _key(box, pygame.K_c, pygame.KMOD_CTRL)
    assert _outside_world_reads() == "Kuba"


def test_a_field_pastes_what_another_application_copied():
    box = TextInput(pygame.Rect(100, 100, 300, 40), "Nick", "")
    box.focus()
    _outside_world_puts("Kubasinski")
    _key(box, pygame.K_v, pygame.KMOD_CTRL)
    assert box.value == "Kubasinski"


# ── the two shapes of pygame.scrap ───────────────────────────────────────────
class _CeScrap:
    """pygame-ce: put_text/get_text and nothing else worth having.

    Upstream pygame 2.6 has neither, which is exactly what this module used to
    call — every AttributeError swallowed by an `except Exception: pass`.
    """

    def __init__(self) -> None:
        self.text = ""

    def init(self) -> None:
        pass

    def get_init(self) -> bool:
        return True

    def set_mode(self, mode) -> None:
        pass

    def put_text(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text


def test_a_pygame_ce_style_clipboard_is_used(monkeypatch):
    scrap = _CeScrap()
    monkeypatch.setattr(pygame, "scrap", scrap)
    clipboard.reset()
    clipboard.copy("K7M2QD")
    assert scrap.text == "K7M2QD", "put_text is there; it should have been used"
    scrap.text = "z Discorda"
    assert clipboard.paste() == "z Discorda"


class _UpstreamScrap:
    """Upstream pygame 2.6: put/get only, and only one type string is accepted.

    ``pygame.SCRAP_TEXT`` is ``"text/plain"``, which this backend rejects — the
    other half of what made every copy fall through to the internal buffer.
    """

    TYPE = "text/plain;charset=utf-8"

    def __init__(self) -> None:
        self.data = {}

    def init(self) -> None:
        pass

    def get_init(self) -> bool:
        return True

    def set_mode(self, mode) -> None:
        pass

    def put(self, text_type, data):
        if text_type != self.TYPE:
            raise pygame.error("content could not be placed in clipboard.")
        self.data[text_type] = data

    def get(self, text_type):
        return self.data.get(text_type)


def test_a_clipboard_without_put_text_is_still_the_system_clipboard(monkeypatch):
    """Guessing one call style is the whole of what Stage 15 had to undo."""
    scrap = _UpstreamScrap()
    assert not hasattr(scrap, "put_text"), "this stand-in is upstream pygame"
    monkeypatch.setattr(pygame, "scrap", scrap)
    clipboard.reset()
    clipboard.copy("K7M2QD")
    assert scrap.data.get(_UpstreamScrap.TYPE) == b"K7M2QD"
    assert clipboard.paste() == "K7M2QD"


def test_an_unreadable_clipboard_falls_back_but_a_readable_empty_one_does_not(monkeypatch):
    """The fallback exists for machines that cannot read, not for empty ones."""
    class _Broken(_CeScrap):
        def get_text(self):
            raise pygame.error("no clipboard here")

        def get(self, text_type):
            raise pygame.error("no clipboard here")

        def put(self, text_type, data):
            raise pygame.error("no clipboard here")

    monkeypatch.setattr(pygame, "scrap", _Broken())
    clipboard.reset()
    clipboard.copy("K7M2QD")
    assert clipboard.paste() == "K7M2QD", "the internal buffer has to save this one"
