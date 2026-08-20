"""
Menus for playing together.

Four screens, all thin: they read a :class:`NetworkService` and tell it what the
player did.  None of them knows a socket exists, and none contains a game rule —
the lobby's validation belongs to the server, and the screens show whatever it
says.

    MainMenuScreen ──► HostSetupScreen ──► LobbyScreen ──► GameScreen
                   └─► JoinScreen      ──► LobbyScreen ──► GameScreen
                   └─► (local hot-seat) ─► MenuScreen  ──► GameScreen

WHAT CHANGED IN STAGE 11, and why the screens look different.  There is no
"host" address to hand out any more, because no player's machine listens for
anything.  Everyone connects outward to the same server and finds each other
with a **room code** — six characters, read aloud over a voice chat, typed by
somebody who is not looking at it.  That is the only part of the new
architecture the player ever sees, and it is the part that makes a friend in
another country able to join without touching a router.

Connecting never blocks.  A service is created and the lobby is entered
immediately; the connection finishes in the background and the lobby draws its
progress.  The old screens called a blocking ``connect()`` and froze the whole
application for five seconds whenever somebody mistyped an address.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pygame

from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import RULES
from ..config.theme import mix
from ..net.config import current as network_config
from ..net.config import remember_server_url
from ..net.lobby import DEFAULT_NICKNAME, clean_room_code
from ..net.messages import friendly
from ..net.service import ClientService, HostService, NetworkService
from ..net.transport import ConnectionState, TransportError
from . import clipboard
from .app import App, Screen
from .headings import BLOCK_GAP, content_top, draw_title
from .settings_panel import GameSettingsPanel
from .widgets import Button, Checkbox, Dropdown, Stepper, TextInput, fit_buttons

_title = draw_title

#: What the character dropdown calls "deal me one".
RANDOM_LABEL = "Losowa postać"

#: Shown greyed out when the server field is empty.  A concrete example
#: rather than "adres serwera": the shape of the answer is the hard part, and
#: the wss:// prefix is the thing people leave out.
SERVER_PLACEHOLDER = "wss://twoj-serwer.up.railway.app"

#: How long a "✓ skopiowano" confirmation stays on screen, in seconds.
COPY_NOTICE_SECONDS = 2.0


class CopyNotice:
    """A short confirmation that something reached the clipboard.

    Its own tiny class rather than a string plus a float on three screens,
    because "show this for two seconds" is the same behaviour every time and
    the alternative is three timers that drift apart.  Screens call
    :meth:`show` when they copy and :meth:`update` once a frame.
    """

    def __init__(self, seconds: float = COPY_NOTICE_SECONDS) -> None:
        self.seconds = seconds
        self.text = ""
        self._left = 0.0

    @property
    def visible(self) -> bool:
        return self._left > 0.0 and bool(self.text)

    def show(self, text: str) -> None:
        self.text = text
        self._left = self.seconds

    def update(self, dt: float) -> None:
        if self._left > 0.0:
            self._left = max(0.0, self._left - dt)

    def fade(self) -> float:
        """1.0 for most of its life, falling away over the last third.

        Vanishing between one frame and the next reads as a glitch; a short
        fade reads as a message that is finished.
        """
        if not self.visible:
            return 0.0
        tail = self.seconds / 3.0
        return 1.0 if self._left > tail else max(0.0, self._left / tail)


class MainMenuScreen(Screen):
    """Create a room, join one, play on this machine, or leave."""

    def __init__(self, app: App, library: ContentLibrary) -> None:
        super().__init__(app)
        self.library = library
        self.message = ""
        self.buttons: List[Tuple[str, Button]] = []
        self._build()

    def _build(self) -> None:
        r, layout = self.app.renderer, self.app.layout
        centre = layout.win_w // 2
        gap = 16
        entries = [
            ("host", "Załóż grę online"),
            ("join", "Dołącz do gry"),
            ("local", "Gra lokalna (hot-seat)"),
            ("quit", "Wyjście"),
        ]
        top_limit = content_top(r, layout)
        message_h = r.fonts.get(17, bold=True).get_height() + 20
        server_h = r.fonts.get(13).get_height() + 12

        # Measure first, place second.  "Gra lokalna (hot-seat)" is half as long
        # again as "Wyjście"; one hard-coded width for both is what clipped it.
        self.buttons = [
            (key, Button(pygame.Rect(0, 0, 0, 0), label, radius=12, accent=None,
                         primary=(key == "host")))
            for key, label in entries
        ]
        width, height = fit_buttons(
            r, [button for _, button in self.buttons],
            min_width=320, min_height=int(52 * r.fonts.scale),
            max_width=max(240, layout.win_w - 64),
        )

        block = len(entries) * height + (len(entries) - 1) * gap
        free = layout.win_h - top_limit - message_h - server_h - 20
        top = top_limit + max(0, (free - block) // 2)
        for index, (_, button) in enumerate(self.buttons):
            button.rect.topleft = (centre - width // 2, top + index * (height + gap))

        self.server_y = self.buttons[-1][1].rect.bottom + 16
        self.message_y = self.server_y + r.fonts.get(13).get_height() + 10

    def notify(self, message: str) -> None:
        self.message = message

    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.quit()
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        for key, button in self.buttons:
            if button.hit(mouse):
                self._choose(key)
                return

    def _choose(self, key: str) -> None:
        if key == "quit":
            self.app.quit()
        elif key == "host":
            self.app.push(HostSetupScreen(self.app, self.library))
        elif key == "join":
            self.app.push(JoinScreen(self.app, self.library))
        elif key == "local":
            from .menu import MenuScreen

            def start(config):
                from ..engine.setup import create_game
                from ..net.session import LocalSession
                from .game_screen import GameScreen

                state = create_game(config, self.library)
                self.app.replace(GameScreen(self.app, LocalSession(state),
                                            library=self.library))

            self.app.push(MenuScreen(self.app, self.library, start))

    def on_resize(self) -> None:
        self._build()

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        for _, button in self.buttons:
            button.update(mouse, dt)

    def draw(self, surface: pygame.Surface) -> None:
        r, layout = self.app.renderer, self.app.layout
        _title(r, layout, surface, "gra dla znajomych — kod pokoju i gracie")
        for _, button in self.buttons:
            button.draw(r, surface)
        r.text(f"Serwer gry: {network_config().describe_target()}", r.fonts.get(13),
               r.theme.text_dim, surface,
               midtop=(layout.win_w // 2, self.server_y))
        if self.message:
            r.text(self.message, r.fonts.get(17, bold=True), r.theme.invalid,
                   surface, midtop=(layout.win_w // 2, self.message_y),
                   shadow=True)


class _FormScreen(Screen):
    """Shared plumbing for the two screens that are mostly text fields."""

    #: How far the "Kopiuj" button sits from the field it belongs to.
    COPY_GAP = 8

    def __init__(self, app: App, library: ContentLibrary) -> None:
        super().__init__(app)
        self.library = library
        self.inputs: List[TextInput] = []
        self.error = ""
        #: Sits beside the server field so the address can be pasted into a
        #: chat window and sent to whoever is joining.
        self.copy_server = Button(pygame.Rect(0, 0, 92, 0), "Kopiuj",
                                  radius=8, text_size=13)
        self.copied = CopyNotice()

    def _place_copy_button(self, field: TextInput) -> None:
        """Put the copy button beside a field, vertically centred on it.

        Measured from the field rather than given a position, so it stays put
        when the elastic row spacing moves the form around on a short window.
        """
        r = self.app.renderer
        self.copy_server.rect.height = max(28, field.rect.height - 12)
        self.copy_server.fit(r, min_width=92,
                             min_height=self.copy_server.rect.height)
        self.copy_server.rect.midleft = (field.rect.right + self.COPY_GAP,
                                         field.rect.centery)

    def _copy_server_address(self, field: TextInput) -> None:
        address = (field.value or "").strip()
        if not address:
            return
        clipboard.copy(address)
        self.copied.show("✓ Skopiowano adres")

    def _draw_copy_button(self, surface: pygame.Surface, field: TextInput) -> None:
        r, theme = self.app.renderer, self.app.renderer.theme
        self.copy_server.draw(r, surface)
        if self.copied.visible:
            # Under the button, not beside it: to the right is the window edge
            # on a 1280-wide screen.
            r.text(self.copied.text, r.fonts.get(13, bold=True),
                   mix(theme.background, theme.valid, self.copied.fade()),
                   surface, midtop=(self.copy_server.rect.centerx,
                                    self.copy_server.rect.bottom + 4))

    def _focus(self, field: Optional[TextInput]) -> None:
        for other in self.inputs:
            if other is not field:
                other.blur()
        if field is not None:
            field.focus()

    def _cycle_focus(self) -> None:
        if not self.inputs:
            return
        focused = next((i for i, f in enumerate(self.inputs) if f.focused), -1)
        self._focus(self.inputs[(focused + 1) % len(self.inputs)])

    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            hit = next((f for f in self.inputs if f.rect.collidepoint(mouse)), None)
            # Blur the others first so only one field owns the keyboard, then
            # let the field itself place the caret from the click.
            for field in self.inputs:
                if field is not hit:
                    field.blur()
        # Motion and release go to every field: a drag that starts inside one
        # may finish outside it, and the selection should still follow.
        for field in self.inputs:
            if field.handle(event):
                return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.pop()
                return
            if event.key == pygame.K_TAB:
                self._cycle_focus()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.confirm()
                return
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and getattr(self, "server", None) is not None
                and self.copy_server.hit(mouse)):
            self._copy_server_address(self.server)
            return
        self.handle_click(event, mouse)

    def handle_click(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        pass

    def confirm(self) -> None:
        pass

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        # Text fields need the frame time: that is what drives the repeat when
        # backspace is held down.
        for field in self.inputs:
            field.update(mouse, dt)
        self.copied.update(dt)
        self.copy_server.enabled = bool(getattr(self, "server", None)
                                        and self.server.value.strip())
        self.copy_server.update(mouse, dt)
        self.update_widgets(dt, mouse)

    def update_widgets(self, dt: float, mouse: Tuple[int, int]) -> None:
        pass

    # ── shared: opening a lobby without freezing ─────────────────────────────
    def _enter_lobby(self, service: NetworkService, embedded=None) -> None:
        """The one place a connection enters the game, so the one place it is
        handed to the application.

        The lobby passes it to the game screen and the game screen passes it
        nowhere, but closing the window pops neither of them; the application
        is the only thing that outlives every screen and can therefore be
        trusted to close it.  Registered in build order — server first, then
        the connection to it — because :meth:`App.close_owned` unwinds in
        reverse.
        """
        self._focus(None)
        self.app.own(embedded)
        self.app.own(service)
        self.app.replace(LobbyScreen(self.app, self.library, service,
                                     embedded=embedded))


class HostSetupScreen(_FormScreen):
    """Nickname, table settings, then ask the server for a room."""

    FIELD_W = 360
    FIELD_H = 44
    LABEL_GAP = 24
    ROW_GAP = 18
    #: Floor for the elastic gaps.  Three rather than five since stage 21: the
    #: Mod Patusa row is a fifth setting on a screen that already only just fit
    #: a 1280×760 window, and two pixels a row buys it back without anything
    #: touching.
    MIN_ROW_GAP = 3
    #: How far the two Mod Patusa steppers sit either side of the centre line.
    MOD_SPLIT = 150
    #: Named once because ``on_resize`` MEASURES the first one to decide where
    #: the second checkbox goes; a caption that drifted between the measuring
    #: and the drawing would put the two switches on top of each other at some
    #: window size and not at others.
    DEBUG_LABEL = "Wersja testowa — gra od 2 graczy"
    EDIT_LABEL = "Edycja — odkryte ręce"

    def __init__(self, app: App, library: ContentLibrary) -> None:
        super().__init__(app, library)
        self.board_cells = RULES.board_cells_default
        self.chest_round = RULES.chest_open_default
        #: When the table first pauses to choose Mody Patusa, and the gap
        #: between pauses after that.
        self.mod_first = RULES.mod_round_first_default
        self.mod_interval = RULES.mod_round_interval_default
        self.double_percent = RULES.double_frequency_default
        self.debug_version = False
        #: An editing table.  The same switch as ``debug_version`` and laid out
        #: beside it because it is the same KIND of switch: a decision about
        #: what this table is for, made once, by the host, before anybody sits
        #: down.  See commands.EDITOR_ONLY.
        self.edit_mode = False
        self.run_local_server = False

        self.nickname = TextInput(pygame.Rect(0, 0, self.FIELD_W, self.FIELD_H),
                                  "Twój nick", "", placeholder=DEFAULT_NICKNAME,
                                  max_length=RULES.max_name_length)
        self.server = TextInput(pygame.Rect(0, 0, self.FIELD_W, self.FIELD_H),
                                "Serwer gry", network_config().server_url,
                                placeholder=SERVER_PLACEHOLDER, max_length=80)
        self.inputs = [self.nickname, self.server]

        self.debug_checkbox = Checkbox(pygame.Rect(0, 0, 20, 20))
        self.edit_checkbox = Checkbox(pygame.Rect(0, 0, 20, 20))
        self.local_checkbox = Checkbox(pygame.Rect(0, 0, 20, 20))
        # The same panel the hot-seat menu uses: one deck, one place to say
        # what is in it.  See ui/settings_panel.py.
        self.settings_panel = GameSettingsPanel(app, library)
        self.mod_deck_button = Button(pygame.Rect(0, 0, 150, 30),
                                      "Skład talii", radius=8)
        self.create = Button(pygame.Rect(0, 0, 280, 52), "Utwórz pokój",
                             radius=12, primary=True)
        self.back = Button(pygame.Rect(0, 0, 280, 40), "Wróć", radius=10)
        self._lay_out()
        self._focus(self.nickname)

    def _lay_out(self) -> None:
        """One downward pass, with elastic gaps.

        The block is measured first and the gaps shrink until it fits.  Fixed
        spacing is what used to push the error line off the bottom of a
        1280×760 window.
        """
        r, layout = self.app.renderer, self.app.layout
        centre = layout.win_w // 2
        self.centre = centre
        label_h = r.fonts.get(16).get_height()
        hint_h = r.fonts.get(13).get_height()
        error_h = r.fonts.get(17, bold=True).get_height()
        top = content_top(r, layout)

        # Measure the two blocks whose height is not a constant any more: the
        # stepper rows size themselves to their ± labels, and the buttons to
        # their captions.  Guessing 40 and 52 here is what left the error line
        # hanging off the bottom once the type grew.
        probe = Stepper(centre, 0, big_steps=True, r=r)
        stepper_h = probe.height
        button_h = max(self.create.natural_size(r)[1], int(52 * r.fonts.scale))
        back_h = max(self.back.natural_size(r)[1], int(40 * r.fonts.scale))

        fixed = (
            len(self.inputs) * (self.LABEL_GAP + self.FIELD_H)
            + 4 * (label_h + 4 + stepper_h)   # four stepper rows
            + 2 * (20 + hint_h)               # two option rows with hints
            + button_h + 12 + back_h          # create + gap + back
            + 14 + error_h                    # room for a message
        )
        gaps = len(self.inputs) + 4 + 3
        spare = layout.win_h - top - fixed - 16
        self.ROW_GAP = max(self.MIN_ROW_GAP, min(18, spare // max(1, gaps)))

        y = top
        for field in self.inputs:
            field.rect.size = (self.FIELD_W, self.FIELD_H)
            field.rect.topleft = (centre - self.FIELD_W // 2, y + self.LABEL_GAP)
            y = field.rect.bottom + self.ROW_GAP
        self._place_copy_button(self.server)

        y += 4
        self.stepper_rows = []
        for name in ("cells_stepper", "chest_stepper", "doubles_stepper"):
            stepper = Stepper(centre, y + label_h + 4,
                              big_steps=(name != "chest_stepper"), r=r)
            setattr(self, name, stepper)
            self.stepper_rows.append((y, stepper))
            y = stepper.rects["value"].bottom + self.ROW_GAP

        # The two Mod Patusa numbers read as one setting — "od rundy 3, co 2
        # rundy" — so they share a row either side of the centre line.  Two
        # full-width rows here pushed the error line off a 1280x760 window.
        self.mod_row_y = y
        self.mod_first_stepper = Stepper(centre - self.MOD_SPLIT,
                                         y + label_h + 4, value_w=64, r=r)
        self.mod_interval_stepper = Stepper(centre + self.MOD_SPLIT,
                                            y + label_h + 4, value_w=64, r=r)
        y = self.mod_first_stepper.rects["value"].bottom + self.ROW_GAP

        # Beside the row, never under it: the vertical gaps here shrink to fit
        # too, and a button placed below lands on the next label.
        self.mod_deck_button.fit(r, min_width=int(150 * r.fonts.scale),
                                 min_height=int(28 * r.fonts.scale))
        self.mod_deck_button.rect.midleft = (
            max(rect.right for rect in self.mod_interval_stepper.rects.values())
            + int(20 * r.fonts.scale),
            self.mod_interval_stepper.y + self.mod_interval_stepper.height // 2,
        )
        self.settings_panel.on_resize()

        self.local_row_y = y + 2
        self.local_checkbox.rect.topleft = (centre - 190, self.local_row_y)
        y = self.local_row_y + 20 + hint_h + self.ROW_GAP

        self.debug_row_y = y
        self.debug_checkbox.rect.topleft = (centre - 190, self.debug_row_y)
        y = self.debug_row_y + 20 + hint_h + self.ROW_GAP

        # SHARES THE DEBUG ROW rather than taking one of its own.  The form
        # already reaches the bottom of the shortest window the layout tests
        # cover (1280x760): a row of its own pushed the error line off the
        # screen even stripped to a bare checkbox with no hint and no gap, so
        # there is no version of "one more row" that fits.  The two belong
        # together anyway — both say what this table is FOR rather than how it
        # plays — and the debug hint below still reads as that row's hint.
        # MEASURED, not a magic offset: the fonts grow with the window, so a
        # fixed gap that clears the debug label at 1280 is swallowed by it at
        # 2560.  Asking the font how wide the caption actually is keeps the two
        # switches apart at every size the layout tests cover.
        after_debug = (self.debug_checkbox.rect.right + 10
                       + r.fonts.get(16).size(self.DEBUG_LABEL)[0])
        self.edit_checkbox.rect.topleft = (
            after_debug + int(28 * r.fonts.scale), self.debug_row_y)

        # Both buttons are sized to their own captions, then given the wider of
        # the two widths so the bottom of the screen reads as one block.
        fit_buttons(r, [self.create, self.back], min_width=280,
                    min_height=button_h,
                    max_width=max(220, layout.win_w - 64))
        self.create.rect.midtop = (centre, y)
        self.back.rect.midtop = (centre, self.create.rect.bottom + 12)
        self.error_y = self.back.rect.bottom + 14

    def on_resize(self) -> None:
        self._lay_out()

    def handle_click(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        # Modal while open: it covers the steppers underneath.
        if self.settings_panel.handle_event(event, mouse):
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        delta = self.cells_stepper.hit(mouse)
        if delta:
            self.board_cells = max(RULES.board_cells_min, self.board_cells + delta)
            return
        delta = self.chest_stepper.hit(mouse)
        if delta:
            self.chest_round = max(RULES.chest_open_min, self.chest_round + delta)
            return
        if self.mod_deck_button.hit(mouse):
            self.settings_panel.open()
            return
        delta = self.mod_first_stepper.hit(mouse)
        if delta:
            self.mod_first = max(RULES.mod_round_first_min, self.mod_first + delta)
            return
        delta = self.mod_interval_stepper.hit(mouse)
        if delta:
            self.mod_interval = max(RULES.mod_round_interval_min,
                                    self.mod_interval + delta)
            return
        delta = self.doubles_stepper.hit(mouse)
        if delta:
            step = 10 if abs(delta) > 1 else 5
            self.double_percent = max(0, min(
                100, self.double_percent + step * (1 if delta > 0 else -1)))
            return
        if self.local_checkbox.hit(mouse):
            self.run_local_server = not self.run_local_server
            if self.run_local_server:
                self.server.value = network_config().local_server_url
            return
        if self.debug_checkbox.hit(mouse):
            self.debug_version = not self.debug_version
            return
        if self.edit_checkbox.hit(mouse):
            self.edit_mode = not self.edit_mode
            return
        if self.create.hit(mouse):
            self.confirm()
        elif self.back.hit(mouse):
            self.app.pop()

    def confirm(self) -> None:
        self.error = ""
        embedded = None
        url = self.server.value.strip() or network_config().server_url

        if self.run_local_server:
            from ..server.embedded import EmbeddedServer

            embedded = EmbeddedServer(network_config(), self.library)
            if not embedded.start():
                self.error = friendly(embedded.error,
                                     default="Nie udało się uruchomić serwera")
                return
            url = embedded.url

        try:
            service = HostService(self.nickname.value or DEFAULT_NICKNAME,
                                  config=network_config(), library=self.library, url=url)
        except TransportError as failure:
            if embedded is not None:
                embedded.stop()
            self.error = friendly(str(failure))
            return

        service.set_settings(board_cells=self.board_cells,
                             chest_open_round=self.chest_round,
                             mod_round_first=self.mod_first,
                             mod_round_interval=self.mod_interval,
                             mod_counts=self.settings_panel.mod_counts,
                             movement_counts=self.settings_panel.movement_counts,
                             chest_counts=self.settings_panel.chest_counts,
                             ability_uses=self.settings_panel.ability_uses,
                             card_variants=self.settings_panel.card_variants,
                             block_decision_seconds=(
                                 self.settings_panel.block_decision_seconds),
                             check_decision_seconds=(
                                 self.settings_panel.check_decision_seconds),
                             check_variant=self.settings_panel.check_variant,
                             victory_variant=(
                                 self.settings_panel.victory_variant),
                             copy_consumes_use=(
                                 self.settings_panel.copy_consumes_use),
                             double_percent=self.double_percent,
                             debug_version=self.debug_version,
                             edit_mode=self.edit_mode)
        self._enter_lobby(service, embedded=embedded)

    def update_widgets(self, dt: float, mouse: Tuple[int, int]) -> None:
        self.debug_checkbox.checked = self.debug_version
        self.edit_checkbox.checked = self.edit_mode
        self.local_checkbox.checked = self.run_local_server
        self.create.update(mouse, dt)
        self.back.update(mouse, dt)
        self.mod_deck_button.update(mouse, dt)
        self.settings_panel.update(dt, mouse)
        self.debug_checkbox.update(mouse, dt)
        self.edit_checkbox.update(mouse, dt)
        self.local_checkbox.update(mouse, dt)

    def draw(self, surface: pygame.Surface) -> None:
        r, layout = self.app.renderer, self.app.layout
        centre, mouse = self.centre, self.app.mouse()
        _title(r, layout, surface, "Załóż grę")
        for field in self.inputs:
            field.draw(r, surface)
        self._draw_copy_button(surface, self.server)

        labels = ("Liczba pól planszy", "Skrzynia otwiera się w rundzie",
                  "Pola podwójne (12a / 12b)")
        values = (str(self.board_cells), str(self.chest_round),
                  f"{self.double_percent}%")
        for label, value, (label_y, stepper) in zip(labels, values,
                                                    self.stepper_rows):
            r.text(label, r.fonts.get(16), r.theme.text_light, surface,
                   midtop=(centre, label_y))
            stepper.draw(r, value, mouse, surface)

        r.text("Wybór Modów Patusa — od rundy / co ile rund", r.fonts.get(16),
               r.theme.text_light, surface, midtop=(centre, self.mod_row_y))
        self.mod_first_stepper.draw(r, str(self.mod_first), mouse, surface)
        self.mod_interval_stepper.draw(r, str(self.mod_interval), mouse, surface)
        self.mod_deck_button.draw(r, surface)

        self._option(surface, self.local_checkbox,
                     "Uruchom serwer na tym komputerze",
                     "tylko dla graczy w tej samej sieci — przez internet "
                     "użyj serwera z konfiguracji")
        self._option(surface, self.debug_checkbox, self.DEBUG_LABEL,
                     f"tylko do testów; normalnie potrzeba {RULES.min_players} graczy")
        # Label only, on the debug row — see ``on_resize``.  "Odkryte ręce"
        # earns its two words: a switch that turns every hand face up should
        # say so where it is flipped rather than in a manual.
        self.edit_checkbox.draw(r, surface)
        r.text(self.EDIT_LABEL, r.fonts.get(16), r.theme.text_light,
               surface, midleft=(self.edit_checkbox.rect.right + 10,
                                 self.edit_checkbox.rect.centery))

        self.create.draw(r, surface)
        self.back.draw(r, surface)
        if self.error:
            r.text(self.error, r.fonts.get(17, bold=True), r.theme.invalid,
                   surface, midtop=(centre, self.error_y), shadow=True)
        # Last, so it sits above the whole form.
        self.settings_panel.draw(surface)

    def _option(self, surface, checkbox, label: str, hint: str) -> None:
        r = self.app.renderer
        checkbox.draw(r, surface)
        r.text(label, r.fonts.get(16), r.theme.text_light, surface,
               midleft=(checkbox.rect.right + 10, checkbox.rect.centery))
        r.text(hint, r.fonts.get(13), r.theme.text_dim, surface,
               midtop=(self.centre, checkbox.rect.bottom + 4))


class JoinScreen(_FormScreen):
    """Room code, nickname, server, connect."""

    FIELD_W = 360
    FIELD_H = 46
    LABEL_GAP = 24
    ROW_GAP = 20

    def __init__(self, app: App, library: ContentLibrary) -> None:
        super().__init__(app, library)
        self.code = TextInput(pygame.Rect(0, 0, self.FIELD_W, self.FIELD_H),
                              "Kod pokoju", "", placeholder="np. K7M2QD",
                              max_length=8)
        self.nickname = TextInput(pygame.Rect(0, 0, self.FIELD_W, self.FIELD_H),
                                  "Twój nick", "", placeholder=DEFAULT_NICKNAME,
                                  max_length=RULES.max_name_length)
        self.server = TextInput(pygame.Rect(0, 0, self.FIELD_W, self.FIELD_H),
                                "Serwer gry", network_config().server_url,
                                placeholder=SERVER_PLACEHOLDER, max_length=80)
        self.inputs = [self.code, self.nickname, self.server]
        self.join = Button(pygame.Rect(0, 0, 280, 52), "Dołącz", radius=12,
                           primary=True)
        self.back = Button(pygame.Rect(0, 0, 280, 40), "Wróć", radius=10)
        self._lay_out()
        self._focus(self.code)

    def _lay_out(self) -> None:
        r, layout = self.app.renderer, self.app.layout
        centre = layout.win_w // 2
        self.centre = centre
        y = content_top(r, layout)
        for field in self.inputs:
            field.rect.size = (self.FIELD_W, self.FIELD_H)
            field.rect.topleft = (centre - self.FIELD_W // 2, y + self.LABEL_GAP)
            y = field.rect.bottom + self.ROW_GAP
        self._place_copy_button(self.server)

        self.join.rect.midtop = (centre, y + 8)
        fit_buttons(r, [self.join, self.back], min_width=280,
                    min_height=int(52 * r.fonts.scale),
                    max_width=max(220, layout.win_w - 64))
        self.join.rect.midtop = (centre, y + 8)
        self.back.rect.midtop = (centre, self.join.rect.bottom + 12)
        self.hint_y = self.back.rect.bottom + 14
        self.error_y = self.hint_y + r.fonts.get(13).get_height() + 8

    def on_resize(self) -> None:
        self._lay_out()

    def handle_click(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        if self.join.hit(mouse):
            self.confirm()
        elif self.back.hit(mouse):
            self.app.pop()

    def confirm(self) -> None:
        self.error = ""
        code = clean_room_code(self.code.value)
        if not code:
            self.error = "Podaj kod pokoju, który dostałeś od znajomego"
            return
        try:
            service = ClientService(code,
                                    self.nickname.value.strip() or DEFAULT_NICKNAME,
                                    config=network_config(), library=self.library,
                                    url=self.server.value.strip())
        except TransportError as failure:
            self.error = friendly(str(failure))
            return
        self._enter_lobby(service)

    def update_widgets(self, dt: float, mouse: Tuple[int, int]) -> None:
        self.join.update(mouse, dt)
        self.back.update(mouse, dt)

    def draw(self, surface: pygame.Surface) -> None:
        r, layout = self.app.renderer, self.app.layout
        _title(r, layout, surface, "Dołącz do gry")
        for field in self.inputs:
            field.draw(r, surface)
        self._draw_copy_button(surface, self.server)
        self.join.draw(r, surface)
        self.back.draw(r, surface)
        r.text("Kod pokoju podaje osoba, która ją założyła  ·  Tab przełącza pola",
               r.fonts.get(13), r.theme.text_dim, surface,
               midtop=(self.centre, self.hint_y))
        if self.error:
            r.text(self.error, r.fonts.get(17, bold=True), r.theme.invalid,
                   surface, midtop=(self.centre, self.error_y), shadow=True)


class LobbyScreen(Screen):
    """Who is here, what they are playing, and — for the host — Start.

    The layout is computed top-down in :meth:`_lay_out` rather than each piece
    picking its own fraction of the window: with six seats and a small window
    the connection details, the seat list and the character dropdown used to
    land on top of one another.
    """

    ROW_H = 38
    ROW_GAP = 6

    def __init__(self, app: App, library: ContentLibrary,
                 service: NetworkService, embedded=None) -> None:
        super().__init__(app)
        self.library = library
        self.service = service
        #: A server started inside this process, when the player asked for one.
        #: Kept for display and for what this screen wants to know about it;
        #: SHUTTING IT DOWN belongs to the application, which owns it, because
        #: this screen is replaced by the game screen the moment the match
        #: begins and a listener whose owner has gone outlives the session.
        self.embedded = embedded
        self.message = ""
        self.error = ""

        self.start = Button(pygame.Rect(0, 0, 280, 52), "Rozpocznij grę",
                            radius=12, primary=True)
        self.ready = Button(pygame.Rect(0, 0, 280, 52), "Jestem gotowy",
                            radius=12, primary=True)
        self.leave = Button(pygame.Rect(0, 0, 280, 40), "Opuść pokój", radius=10)
        self.character = Dropdown(pygame.Rect(0, 0, 260, 34),
                                  [RANDOM_LABEL, *library.character_titles()])
        self.character.value = RANDOM_LABEL
        #: Reading a six-character code aloud over a voice chat works, but
        #: pasting it into a chat window is what people actually do.
        self.copy_code = Button(pygame.Rect(0, 0, 200, 32), "Kopiuj kod pokoju",
                                radius=8, text_size=14)
        #: Reopen the table settings after the room exists.  THE SAME PANEL the
        #: host screen and the hot-seat menu use — one deck, one place to say
        #: what is in it — so a setting added there appears here with no code.
        #: Beside the room code rather than in the button column at the bottom:
        #: that column is measured against the shortest window the layout tests
        #: cover and the changelog records an extra row pushing it off the
        #: screen.  This row has spare width and none.
        self.settings_button = Button(pygame.Rect(0, 0, 200, 32),
                                      "Ustawienia gry", radius=8, text_size=14)
        self.settings_panel = GameSettingsPanel(app, library)
        #: The panel has no "apply" of its own — it is a modal that closes.
        #: Watched so that closing it is what sends the settings, once.
        self._settings_open = False
        self.copied = CopyNotice()
        #: Remembered once, when the room really exists — see :meth:`update`.
        self._remembered = False
        self._lay_out()

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def lobby(self):
        return self.service.lobby_state

    @property
    def is_host(self) -> bool:
        return self.service.is_host

    @property
    def my_peer_id(self) -> str:
        return self.service.peer_id

    @property
    def my_seat(self):
        return self.lobby.seat_of(self.my_peer_id)

    @property
    def connecting(self) -> bool:
        """No room yet: either still dialling, or waiting for the answer."""
        return not self.lobby.code

    def _taken(self) -> set:
        return set(self.lobby.taken_characters(except_peer=self.my_peer_id))

    # ── layout ───────────────────────────────────────────────────────────────
    def _lay_out(self) -> None:
        """Place everything in one downward pass, so nothing can collide."""
        r, layout = self.app.renderer, self.app.layout
        centre = layout.win_w // 2
        self.centre = centre
        self.row_width = min(580, layout.win_w - 80)

        # Bottom-up: the buttons are anchored to the bottom margin.
        margin = max(18, int(layout.win_h * 0.03))
        self.message_y = layout.win_h - margin - r.fonts.get(15).get_height()
        # "Jestem gotowy" and "Czekam na hosta" swap into the same button, so
        # it is measured against the longer of the two rather than whichever
        # one happens to be showing when the screen is laid out.
        widest = max(("Jestem gotowy", "Czekam na hosta"),
                     key=lambda text: r.spaced_width(
                         text.upper(), r.fonts.get(self.ready.text_size, bold=True), 2))
        self.ready.label = widest
        fit_buttons(r, [self.start, self.ready], min_width=280,
                    min_height=int(52 * r.fonts.scale),
                    max_width=max(220, layout.win_w - 64))
        self.leave.fit(r, min_width=280, min_height=int(40 * r.fonts.scale),
                       max_width=max(220, layout.win_w - 64))
        self.leave.rect.midbottom = (centre, self.message_y - 10)
        self.problem_y = (self.leave.rect.top - 8
                          - r.fonts.get(16, bold=True).get_height())
        self.start.rect.midbottom = (centre, self.problem_y - 6)
        self.ready.rect.midbottom = self.start.rect.midbottom

        # Top-down: heading, room code, copy button, seat list, character picker.
        y = content_top(r, layout)
        self.info_y = y
        y += r.fonts.get(30, bold=True).get_height() + 4
        y += r.fonts.get(14).get_height() + 8
        self.copy_code.fit(r, min_width=200,
                           min_height=int(32 * r.fonts.scale),
                           max_width=max(180, layout.win_w - 64))
        self.copy_code.rect.midtop = (centre, y)
        # The confirmation sits beside the button rather than under it, so the
        # seat list below never moves when it appears and disappears.
        self.copied_pos = (self.copy_code.rect.right + 12,
                           self.copy_code.rect.centery)
        # LEFT of the copy button, because the confirmation notice lives on its
        # right and the two would land on each other.  Measured off the button
        # it sits beside, so it follows it at every window size.
        self.settings_button.fit(r, min_width=200,
                                 min_height=int(32 * r.fonts.scale),
                                 max_width=max(180, layout.win_w - 64))
        self.settings_button.rect.midright = (
            self.copy_code.rect.left - int(12 * r.fonts.scale),
            self.copy_code.rect.centery)
        self.settings_panel.on_resize()
        y = self.copy_code.rect.bottom + BLOCK_GAP

        self.character_row_y = self.start.rect.top - BLOCK_GAP - 34
        self.character.rect.size = (260, 34)
        self.character.rect.topleft = (centre + 30, self.character_row_y)
        self.character.max_bottom = layout.win_h - 10

        self.seats_top = y
        available = self.character_row_y - BLOCK_GAP - y
        # The lobby's OWN minimum, not RULES.min_players: with the debug
        # version on, the table needs two seats and reserving space for three
        # left a permanent empty row under a game that was ready to start.
        rows = max(self.lobby.minimum_players, len(self.lobby.seats), 1)
        row_h = self.ROW_H + self.ROW_GAP
        if rows * row_h > available:
            # Shrink the rows rather than letting the list grow into the
            # controls below it.
            row_h = max(24, available // rows)
        self.row_h = row_h

    def seat_rect(self, index: int) -> pygame.Rect:
        return pygame.Rect(self.centre - self.row_width // 2,
                           self.seats_top + index * self.row_h,
                           self.row_width, max(20, self.row_h - self.ROW_GAP))

    def on_resize(self) -> None:
        self._lay_out()

    # ── input ────────────────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        # Modal while open, and FIRST: it covers the seat list underneath, so a
        # click that fell through would pick a character through the panel.
        if self.settings_panel.handle_event(event, mouse):
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.character.open:
                self.character.open = False
            else:
                self._leave()
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        if self.character.open:
            option = self.character.option_at(mouse)
            self.character.open = False
            if option is not None:
                self.character.value = option
                self.service.set_character(
                    "" if option == RANDOM_LABEL else option)
            return
        if self.character.hit(mouse):
            self.character.disabled_options = self._taken()
            self.character.open = True
            return
        if self.lobby.code and self.copy_code.hit(mouse):
            self._copy_code()
            return
        if self.may_edit_settings and self.settings_button.hit(mouse):
            self._open_settings()
            return
        if self.is_host and self.start.hit(mouse):
            self._start()
            return
        if not self.is_host and self.ready.hit(mouse):
            seat = self.my_seat
            self.service.set_ready(not (seat.ready if seat else False))
            return
        if self.leave.hit(mouse):
            self._leave()

    @property
    def may_edit_settings(self) -> bool:
        """Host only, and only while the room is between matches.

        THE SAME TWO CONDITIONS THE SERVER APPLIES — ``_apply_settings``
        refuses a non-host and refuses a started room — asked here so the
        button is absent rather than present and rejected.  The server is still
        the one that decides; this only declines to offer a question whose
        answer is already known.
        """
        return self.is_host and not self.lobby.started

    def _open_settings(self) -> None:
        """Reopen the table settings, seeded from the ROOM's current ones.

        Not from this screen's defaults: the lobby state is the single source
        of truth and it is replicated, so what the host edits is what the room
        actually has — including anything a previous host set before handing
        over.
        """
        lobby = self.lobby
        self.settings_panel.open(
            counts=dict(lobby.mod_counts),
            movement_counts=dict(lobby.movement_counts),
            chest_counts=dict(lobby.chest_counts),
            ability_uses=dict(lobby.ability_uses),
            card_variants=dict(lobby.card_variants),
            block_decision_seconds=lobby.block_decision_seconds,
        )
        self._settings_open = True

    def _apply_settings(self) -> None:
        """Send what the panel now holds.  One message, the existing one.

        ``set_settings`` is what the host screen sends before the room exists
        and what the server merges and clamps; nothing here validates anything,
        because the server settles what the settings ARE.  The reply is a
        lobby broadcast every client already mirrors, which is how the other
        players see the new table without a second protocol.
        """
        panel = self.settings_panel
        self.service.set_settings(
            mod_counts=panel.mod_counts,
            movement_counts=panel.movement_counts,
            chest_counts=panel.chest_counts,
            ability_uses=panel.ability_uses,
            card_variants=panel.card_variants,
            block_decision_seconds=panel.block_decision_seconds,
            check_decision_seconds=panel.check_decision_seconds,
            check_variant=panel.check_variant,
            victory_variant=panel.victory_variant,
            copy_consumes_use=panel.copy_consumes_use,
        )

    def _copy_code(self) -> None:
        """Put ONLY the code on the clipboard.

        Not the sentence around it: the person on the other end is going to
        paste it straight into the join field, and "KOD POKOJU: K7M2QD" does
        not join anything.
        """
        code = self.lobby.code
        if not code:
            return
        clipboard.copy(code)
        self.copied.show("✓ Skopiowano kod pokoju")

    def _start(self) -> None:
        """Ask the server to begin.  The answer arrives as a broadcast.

        Nothing happens on screen here: the server builds the game, tells
        everybody — including this machine — and :meth:`update` notices the
        session appearing.  Host and client therefore take exactly the same
        path into the match, which is why there is no way for one of them to
        enter a game the other did not.
        """
        self.error = ""
        if self.service.start_game(self.library) is None and self.service.error:
            self.error = friendly(self.service.error)

    def _enter_game(self) -> None:
        from .game_screen import GameScreen

        self.app.replace(GameScreen(self.app, self.service.session,
                                    service=self.service, library=self.library))

    def _leave(self) -> None:
        """"Opuść pokój": the player chose to go, so the room is told so.

        ``leave_room`` rather than merely closing the socket — the difference
        is whether the server frees this seat now or holds it open through a
        grace period for somebody who is already looking at the main menu.
        """
        self.service.leave_room()
        self.app.close_owned()
        self.app.replace(MainMenuScreen(self.app, self.library))

    def _go_home(self, reason: str) -> None:
        """Sent home by something that happened TO us, not by a choice.

        No ``leave_room`` here: the connection is already gone, and there is
        nothing to tell a server that has stopped listening.  The resources are
        still released.
        """
        self.app.close_owned()
        menu = MainMenuScreen(self.app, self.library)
        menu.notify(reason)
        self.app.replace(menu)

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        self.service.poll(self.library)
        if self.service.disconnected:
            self._go_home(self.service.disconnected)
            return
        if self.service.session is not None:
            self._enter_game()
            return

        self.copied.update(dt)
        if self.lobby.code and not self._remembered:
            # A room exists, so this address demonstrably works.  Remembering
            # it on connection instead would helpfully re-fill the field with
            # whatever typo failed.
            self._remembered = True
            remember_server_url(self.service.server_url)

        notices = self.service.drain_notices()
        if notices:
            self.message = notices[-1]
        self._lay_out()
        self.copy_code.enabled = bool(self.lobby.code)
        self.copy_code.update(mouse, dt)
        self.start.enabled = self.lobby.can_start
        self.start.update(mouse, dt)
        self.ready.update(mouse, dt)
        self.leave.update(mouse, dt)
        self.character.update(mouse, dt)
        self.settings_button.update(mouse, dt)
        self.settings_panel.update(dt, mouse)
        # CLOSING IS APPLYING.  The panel is a modal with a done button and no
        # notion of a server, so the moment it stops being open is the moment
        # the room is told.  Watched rather than hooked so the Esc key, the
        # button and a click outside all send exactly the same one message.
        if self._settings_open and not self.settings_panel.active:
            self._settings_open = False
            self._apply_settings()

    def draw(self, surface: pygame.Surface) -> None:
        r, layout = self.app.renderer, self.app.layout
        theme, centre = r.theme, self.centre
        _title(r, layout, surface, "Poczekalnia")

        self._draw_code(surface)
        if self.lobby.code:
            self.copy_code.draw(r, surface)
            if self.copied.visible:
                r.text(self.copied.text, r.fonts.get(14, bold=True),
                       mix(theme.background, theme.valid, self.copied.fade()),
                       surface, midleft=self.copied_pos)
            if self.may_edit_settings:
                self.settings_button.draw(r, surface)
        self._draw_seats(surface)

        r.text("Twoja postać:", r.fonts.get(17), theme.text_light, surface,
               midright=(self.character.rect.left - 14,
                         self.character.rect.centery))
        self.character.draw(r, surface)

        if self.is_host:
            self.start.draw(r, surface)
            problem = self.error or self.lobby.validate()
            if problem and not self.connecting:
                r.text(problem, r.fonts.get(16, bold=True), theme.invalid,
                       surface, midtop=(centre, self.problem_y), shadow=True)
        else:
            seat = self.my_seat
            self.ready.label = ("Czekam na hosta" if seat and seat.ready
                                else "Jestem gotowy")
            self.ready.draw(r, surface)
            if not self.connecting:
                r.text("Grę rozpoczyna osoba, która założyła pokój",
                       r.fonts.get(16), theme.text_dim, surface,
                       midtop=(centre, self.problem_y))

        self.leave.draw(r, surface)
        if self.message:
            r.text(self.message, r.fonts.get(15), theme.text_dim, surface,
                   midtop=(centre, self.message_y))
        if self.character.open:
            self.character.draw_overlay(r, self.app.mouse(), surface)
        # LAST, above the dropdown overlay as well: it is modal and consumes
        # every click while it is open, so anything drawn over it would be a
        # control the player can see and cannot press.
        self.settings_panel.draw(surface)

    def _draw_code(self, surface: pygame.Surface) -> None:
        """The room code, big, because it is the one thing to read aloud."""
        r, theme, centre = self.app.renderer, self.app.renderer.theme, self.centre
        font = r.fonts.get(30, bold=True)
        y = self.info_y
        if self.connecting:
            state = self.service.connection_state
            text = ("ŁĄCZĘ PONOWNIE…" if state is ConnectionState.RECONNECTING
                    else "ŁĄCZĘ Z SERWEREM…")
            r.spaced_text(text, font, theme.text_dim, surface,
                          center=(centre, y + font.get_height() // 2), spacing=4)
            r.text(self.service.server_url, r.fonts.get(14), theme.text_dim,
                   surface, midtop=(centre, y + font.get_height() + 4))
            return

        r.spaced_text(f"KOD POKOJU: {self.lobby.code}", font, theme.prompt,
                      surface, center=(centre, y + font.get_height() // 2),
                      spacing=6, shadow=True)
        r.text("Podaj go znajomym — wpisują go w „Dołącz do gry”",
               r.fonts.get(14), theme.text_dim, surface,
               midtop=(centre, y + font.get_height() + 4))

    def _draw_seats(self, surface: pygame.Surface) -> None:
        r, theme = self.app.renderer, self.app.renderer.theme
        seats = self.lobby.seats
        for index, seat in enumerate(seats):
            rect = self.seat_rect(index)
            mine = seat.peer_id == self.my_peer_id
            settled = seat.ready or seat.is_host
            style = r.emphasis(
                fill=theme.btn_active_bg if mine else theme.btn_idle_bg,
                border=theme.accent if mine else theme.panel_line,
                text=theme.text_light, selected=mine, quiet=not mine,
            )
            rect = r.interactive_panel(rect, style, surface, radius=9)
            small = r.fonts.get(min(15, max(11, rect.height - 22)))
            font = r.fonts.get(min(18, max(12, rect.height - 20)), bold=True)

            # Right to left: the status, then the character, then whatever room
            # is left goes to the nickname.  Placing the character at a fixed
            # offset from the right edge is what let a long name run into it on
            # a 1280-wide window — the row has three things in it now, so the
            # positions have to be measured rather than guessed.
            if not seat.connected:
                mark, colour = "rozłączony", theme.invalid
            elif settled:
                mark, colour = "gotowy", theme.valid
            else:
                mark, colour = "czeka", theme.text_dim
            mark_left = rect.right - 14 - small.size(mark)[0]
            r.text(mark, small, colour, surface,
                   midright=(rect.right - 14, rect.centery))

            character = seat.character or "losowa postać"
            character_right = mark_left - 16
            r.text(character, small,
                   theme.text_light if seat.character else theme.text_dim,
                   surface, midright=(character_right, rect.centery))

            label = seat.nickname + ("  (ty)" if mine else "")
            if seat.is_host:
                label += "  ·  zakłada"
            room = character_right - small.size(character)[0] - 16 - (rect.left + 14)
            r.text(_elided(label, font, room), font, theme.text_light, surface,
                   midleft=(rect.left + 14, rect.centery))

        minimum = self.lobby.minimum_players
        for index in range(len(seats), minimum):
            rect = self.seat_rect(index)
            r.inset_well(rect, surface, radius=9)
            r.spaced_text("CZEKAMY NA GRACZA", r.fonts.get(13, bold=True),
                          theme.text_dim, surface, center=rect.center, spacing=2)


def _elided(text: str, font, width: int) -> str:
    """Shorten ``text`` with an ellipsis until it fits ``width``.

    Six long nicknames on a 1280-wide window is the worst case the lobby has,
    and a name that does not fit must lose its own tail rather than run over
    whatever is drawn beside it.
    """
    if width <= 0:
        return ""
    if font.size(text)[0] <= width:
        return text
    for length in range(len(text) - 1, 0, -1):
        candidate = text[:length].rstrip() + "…"
        if font.size(candidate)[0] <= width:
            return candidate
    return "…"
