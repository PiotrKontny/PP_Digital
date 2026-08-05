"""
Stage 13: the handshake gate, friendly errors, and the shared text editor.

THE BUG THIS FILE EXISTS FOR.  ``HostSetupScreen.confirm`` opens a connection
and sends the table settings in the same function, before the game loop has
polled once.  ``HELLO`` is only sent from ``poll()``, but the transport queues
whatever it is handed and flushes it the instant the socket opens — so the
settings arrived first, the server answered "no handshake yet", and the host was
thrown out of a room they never managed to create.  Every time; not a race.

The existing multiplayer tests could not see it because ``netkit.Table.host()``
pumps the connection before touching any setting, which is precisely what the
real screens do not do.  So the tests here drive the client the way the *screens*
do: construct, act immediately, and only then poll.
"""

from __future__ import annotations

import time

import pygame
import pytest

from netkit import ROOT, Table, server_config

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config.settings import RULES
from pedzacy_piotrek.net import messages
from pedzacy_piotrek.net.config import NetworkConfig, ServerConfig
from pedzacy_piotrek.net.protocol import Message, MessageType
from pedzacy_piotrek.net.service import HostService
from pedzacy_piotrek.ui import clipboard
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.layout import Layout
from pedzacy_piotrek.ui.widgets import TextEditor, TextField, TextInput


@pytest.fixture(scope="module", autouse=True)
def _display():
    """The widget tests need a real pygame display.

    ``pygame.key.set_mods`` and ``pygame.key.start_text_input`` both raise
    "video system not initialized" without one, and every text field calls the
    latter when it takes focus.  A headless App is how the rest of the suite
    gets one.
    """
    app = App(Layout(), headless=True, size=(1280, 760))
    yield app
    pygame.quit()


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def table(library):
    board = Table(library)
    yield board
    board.close()


# ── the handshake gate ───────────────────────────────────────────────────────
def test_settings_sent_before_the_handshake_are_not_lost(table, library):
    """The exact sequence HostSetupScreen.confirm runs, and it must work.

    Note what is missing between the two calls: a poll.  That is the whole
    point — the screen has no opportunity to pump the connection between
    creating the service and configuring the table.
    """
    host = HostService("Gospodarz", config=table.config,
                       transport=table.server.transport(), library=library)
    table.services.append(host)

    host.set_settings(board_cells=42, chest_open_round=3,
                      double_percent=25, debug_version=True)

    table.pump()

    assert host.disconnected is None, host.disconnected
    assert host.room_code, "the host never got a room"
    assert host.lobby_state.board_cells == 42
    assert host.lobby_state.debug_version is True


def test_nothing_overtakes_hello(table, library):
    """Whatever order the screen acts in, HELLO is first on the wire."""
    host = HostService("Gospodarz", config=table.config,
                       transport=table.server.transport(), library=library)
    table.services.append(host)
    host.set_settings(board_cells=30)
    host.set_ready(True)
    table.pump()

    sent = table.server.received_from(host.client.transport)
    assert sent, "nothing was sent at all"
    assert sent[0] == MessageType.HELLO.value, sent


def test_the_player_never_reads_the_word_hello(table, library):
    """The protocol error that used to reach the screen, and must not."""
    host = HostService("Gospodarz", config=table.config,
                       transport=table.server.transport(), library=library)
    table.services.append(host)
    host.set_settings(debug_version=True)
    table.pump()

    for text in [host.error or "", host.disconnected or "", *host.notices]:
        assert "hello" not in text.lower()
        assert "przywitanie" not in text.lower()


def test_a_premature_message_no_longer_kills_the_session(table, library):
    """Belt and braces: an ungated client loses the message, not the match."""
    host = table.host("Gospodarz")
    code = host.room_code
    transport = table.server.transport()
    # Speak out of turn, exactly as an older build would.
    transport.send(Message(MessageType.SET_SETTINGS, {"board_cells": 30}))
    for _ in range(4):
        table.server.tick()
    replies = transport.poll()

    assert replies, "the server said nothing at all"
    assert all(not m.payload.get("fatal") for m in replies), \
        "a mis-ordered message must not be fatal"
    assert table.room(code) is not None, "the room should be untouched"


def test_actions_taken_while_reconnecting_are_replayed(table, library):
    """A click during a drop is held, not refused.

    The gate closes again on a new socket because the server has never heard of
    it; without the queue, everything the player did in those few seconds came
    back as "no handshake yet".
    """
    host, (ola,) = table.seated("Gospodarz", "Ola")
    client = ola.client

    # Pretend the socket dropped and came back: a new generation, ungreeted.
    client._welcomed_generation = 0
    ola.set_ready(False)
    assert client._pending, "the action should be waiting for the handshake"

    client._on_welcome(Message.welcome(client.peer_id, client.resume_token))
    table.pump()

    seat = host.lobby_state.seat_of(client.peer_id)
    assert seat is not None and seat.ready is False


# ── debug mode really means two players ──────────────────────────────────────
def test_a_two_player_debug_game_starts(table, library):
    """The whole chain: the setting reaches the server, and the game begins."""
    host = HostService("Gospodarz", config=table.config,
                       transport=table.server.transport(), library=library)
    table.services.append(host)
    host.set_settings(debug_version=True)
    table.pump()

    ola = table.join(host.room_code, "Ola")
    ola.set_ready(True)
    table.pump()

    assert host.lobby_state.minimum_players == RULES.debug_min_players == 2
    assert host.lobby_state.validate() == "", host.lobby_state.validate()

    host.start_game(library)
    table.pump()

    assert host.session is not None and ola.session is not None
    assert host.state.config.num_players == 2
    assert len(host.state.players) == 2
    assert host.state.snapshot() == ola.state.snapshot()


def test_without_debug_mode_two_players_are_still_refused(table, library):
    host, (ola,) = table.seated("Gospodarz", "Ola")
    assert host.lobby_state.minimum_players == RULES.min_players
    assert host.lobby_state.validate() != ""
    host.start_game(library)
    table.pump()
    assert host.session is None


# ── friendly errors ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    "Najpierw przywitanie (hello)",
    "ConnectionRefusedError(111, 'Connection refused')",
    "[Errno 111] Connection refused",
    "[Errno -2] Name or service not known",
    "received 1006 (abnormal closure); no close frame received",
    "WebSocketException: connection closed",
    "sent 1011 (internal error)",
    "timed out",
    "ws://piotrek.up.railway.app:51337",
    "{'type': 'error'}",
    "",
    None,
])
def test_no_technical_text_ever_reaches_the_player(raw):
    """Whatever the layers below produce, the player reads a sentence."""
    result = messages.friendly(raw)
    assert result
    assert "Error" not in result and "Errno" not in result
    assert "://" not in result
    assert "hello" not in result.lower()
    assert not result.startswith("{")
    # Polish prose, not a token dump.
    assert " " in result


@pytest.mark.parametrize("raw", [
    "Nie ma pokoju o kodzie ZZZZZZ",
    "Tylko host może rozpocząć grę",
    "Stół jest pełny (maksimum 6 graczy)",
    "Nie wszyscy są gotowi: Ola, Kuba",
    "Nikt nie wybrał postaci „Piotrek”",
])
def test_the_servers_own_polish_survives_untouched(raw):
    """It is more specific than any constant here could be — keep it.

    An earlier version of the translation table matched these too and replaced
    them with blander sentences, losing the room code and the player names.
    """
    assert messages.friendly(raw) == raw


def test_an_unknown_failure_says_something_true_and_useless():
    assert messages.friendly("qwertyuiopasdfgh") == messages.UNKNOWN


# ── one text editor, everywhere ──────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean_clipboard():
    clipboard.reset()
    yield
    clipboard.reset()


def _key(field, key, mods=0):
    pygame.key.set_mods(mods)
    event = pygame.event.Event(pygame.KEYDOWN, key=key, mod=mods)
    result = field.handle(event)
    pygame.key.set_mods(0)
    return result


def _type(field, text):
    field.handle(pygame.event.Event(pygame.TEXTINPUT, text=text))


@pytest.fixture
def rename() -> TextField:
    field = TextField(max_length=16)
    field.start(0, "Kuba")
    return field


def test_the_rename_box_can_select_all_and_be_replaced(rename):
    _key(rename, pygame.K_a, pygame.KMOD_CTRL)
    _type(rename, "Ola")
    assert rename.buffer == "Ola"


def test_the_rename_box_can_copy_and_paste(rename):
    _key(rename, pygame.K_a, pygame.KMOD_CTRL)
    _key(rename, pygame.K_c, pygame.KMOD_CTRL)
    _key(rename, pygame.K_END)
    _key(rename, pygame.K_v, pygame.KMOD_CTRL)
    assert rename.buffer == "KubaKuba"


def test_the_rename_box_can_cut(rename):
    _key(rename, pygame.K_a, pygame.KMOD_CTRL)
    _key(rename, pygame.K_x, pygame.KMOD_CTRL)
    assert rename.buffer == ""
    assert clipboard.paste() == "Kuba"


def test_the_rename_box_moves_its_caret(rename):
    _key(rename, pygame.K_HOME)
    assert rename.caret_index == 0
    _type(rename, "X")
    assert rename.buffer == "XKuba"


def test_the_rename_caret_is_drawn_where_it_is(rename):
    _key(rename, pygame.K_HOME)
    text = rename.display_text()
    # Either half of the blink is fine; when the bar is showing it must be at
    # the front, because that is where the caret is.
    assert text in ("|Kuba", "Kuba")


def test_the_rename_box_starts_selected_so_typing_replaces(rename):
    _type(rename, "Z")
    assert rename.buffer == "Z"


def test_the_rename_box_shows_its_placeholder_when_empty():
    field = TextField(max_length=16, placeholder="wpisz nazwę")
    field.start(0, "")
    assert "wpisz nazwę" in field.display_text()


def test_enter_still_confirms_and_escape_still_cancels(rename):
    assert _key(rename, pygame.K_RETURN) == "Kuba"
    assert rename.active
    _key(rename, pygame.K_ESCAPE)
    assert not rename.active


def test_both_text_widgets_share_one_editor():
    """The point of the refactor: a fix to one is a fix to the other."""
    assert isinstance(TextField(max_length=8).editor, TextEditor)
    assert isinstance(TextInput(pygame.Rect(0, 0, 10, 10)).editor, TextEditor)


@pytest.mark.parametrize("value, caret, expected", [
    ("Kuba Nowak", 10, 5),
    ("Kuba Nowak", 5, 0),
    ("Kuba  Nowak", 11, 6),
])
def test_ctrl_left_jumps_a_whole_word(value, caret, expected):
    editor = TextEditor(value)
    editor.caret = caret
    assert editor.word_left() == expected


def test_max_length_limits_typing_but_not_assignment():
    """The limit is on the player, not on the program.

    A field pre-filled with a long server address has to show all of it.
    """
    editor = TextEditor(max_length=4)
    editor.insert("abcdefgh")
    assert editor.value == "abcd"
    editor.set_value("a-much-longer-address")
    assert editor.value == "a-much-longer-address"


# ── the server really is a separate application ──────────────────────────────
def test_the_server_needs_neither_pygame_nor_the_client_packages():
    """The deployment story, asserted rather than documented.

    ``.railwayignore`` leaves ``ui/``, ``render/`` and ``assets/`` out of the
    build, and ``requirements-server.txt`` installs only ``websockets``.  Both
    claims are only true for as long as nothing on the server's import path
    reaches for a screen — so this walks that path with pygame made
    unimportable and the client packages made invisible.
    """
    import subprocess
    import sys
    import textwrap

    program = textwrap.dedent("""
        import sys

        class Blocker:
            FORBIDDEN = ("pygame", "pedzacy_piotrek.ui", "pedzacy_piotrek.render")

            def find_module(self, name, path=None):
                if any(name == f or name.startswith(f + ".")
                       for f in self.FORBIDDEN):
                    return self

            def load_module(self, name):
                raise ImportError(f"{name} is not available on the server")

        sys.meta_path.insert(0, Blocker())

        from pedzacy_piotrek.server.app import GameServer
        from pedzacy_piotrek.cards.loader import ContentLibrary

        server = GameServer(library=ContentLibrary.load())
        assert "serwer gry działa" in server.health_report()
        print("OK")
    """)
    result = subprocess.run([sys.executable, "-c", program],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_the_health_page_tells_a_human_the_server_is_alive(library):
    """Opening the URL in a browser is the only diagnosis tool a beginner has.

    Without this the address answers ``426 Upgrade Required``, which is correct
    and reads exactly like a broken deployment.
    """
    from pedzacy_piotrek.server.app import GameServer

    server = GameServer(NetworkConfig(), library)
    report = server.health_report()
    assert "działa" in report
    assert "wss://" in report          # tells them what to type in the game
    assert "Traceback" not in report
