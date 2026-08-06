"""
The game screen.

Successor to the prototype's ``run_game``, but where that function held state,
layout, rendering and input in one 640-line scope, this one only *routes*: it
offers an event to the hand fan, then the board, then each panel in priority
order, collects the commands they produce and hands them to the session.  It
contains no rules.

Input priority (unchanged from the prototype except for the fan, which is new
and sits first because it is the topmost thing on screen):

    renaming → hand fan → board → round counter → right-click chain → left-click chain
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pygame

from ..config import settings
from ..engine import commands as cmd
from ..engine import effects
from ..engine import events as ev
from ..render.card_renderer import CardRenderer
from .app import App, Screen
from .board_view import BoardView
from .hand_fan import HandFan
from .debug_panel import NetworkDebugPanel
from .match_overlays import MatchStartOverlay, VictoryOverlay
from .overlays import (
    CardPicker, ChestChoice, ChoicePrompt, PauseMenu, RevealOverlay,
    RevealPhase,
)
from .hud import (
    CharacterPanel,
    DeckPanel,
    HudContext,
    ModPanel,
    PlayerTiles,
    RecentlyPlayed,
    RoundPanel,
    StatusBar,
)
from .widgets import TextField


@dataclass
class PendingChoice:
    """An action waiting for a decision from the player.

    One shape for every question the engine can ask — which pawn, which half of
    a doubled field, how far to move — because the engine describes the
    question rather than the interface knowing about particular cards.
    ``answers`` accumulates as the engine asks for one thing after another.
    """

    key: str
    kind: str
    prompt: str
    options: List[tuple]
    tiles: List[int]
    pawns: List[str]
    answers: Dict[str, str]
    description: str = ""
    #: Cards on offer, when the question is about cards (Spy).
    card_options: List[int] = field(default_factory=list)
    #: How many answers the engine wants and whether their order matters.  More
    #: than one turns the prompt into a multi-select and the answer into the
    #: picked ids joined by commas — which is why no command needed a new field.
    count: int = 1
    ordered: bool = False
    owner: Optional[int] = None
    card_uid: Optional[int] = None
    ability_source: Optional[str] = None

    def resubmit(self, player_index: int, option_id: str) -> cmd.Command:
        """The same action again, with this answer added."""
        choices = dict(self.answers)
        choices[self.key] = str(option_id)
        if self.card_uid is not None:
            return cmd.PlayCard(player_index=player_index,
                                card_uid=self.card_uid, choices=choices)
        return cmd.UseAbility(player_index=player_index,
                              source=self.ability_source or "character",
                              choices=choices)


class GameScreen(Screen):
    def __init__(self, app: App, session, service=None, library=None) -> None:
        super().__init__(app)
        self.session = session
        #: The network service, when this is a multiplayer match.  ``None`` for
        #: a game on one machine — everything below treats the two the same,
        #: which is the point of routing every action through a session.
        self.service = service
        self.library = library
        self.state = session.state
        self.bus = session.bus

        self.cards = CardRenderer(app.renderer, self.state.library)
        self.board_view = BoardView(
            app.renderer, app.layout, self.state, self.submit, self.bus
        )
        #: Seat currently on screen.  A hot-seat game starts on whoever is
        #: playing; a networked one starts on the seat this machine owns, and
        #: ``my_seat`` always brings it back.
        self.view_seat = (self.state.active_player_index if self.state.edit_mode
                          else self.state.local_seat)
        self.hand = HandFan(
            app.renderer, self.cards, app.layout, self.state, self.submit,
            seat=lambda: self.view_seat, can_act=lambda: self.controls_view,
        )

        self.round_panel = RoundPanel()
        self.deck_panel = DeckPanel()
        self.mod_panel = ModPanel()
        self.character_panel = CharacterPanel()
        self.player_tiles = PlayerTiles()
        self.recently_played = RecentlyPlayed()
        self.status_bar = StatusBar()
        self.choice_prompt = ChoicePrompt()
        self.reveal = RevealOverlay()
        self.chest_choice = ChestChoice()
        #: Somebody else's cards, laid out to take one from (Spy).
        self.card_picker = CardPicker()
        self.pause_menu = PauseMenu()
        #: Before the first move and after the last one.  Both are drawn over
        #: the live table and both make the game unplayable while they are up —
        #: though the engine refuses everything anyway, so this is manners
        #: rather than enforcement.
        self.match_start = MatchStartOverlay()
        self.victory = VictoryOverlay()
        self.chest_choice_seat: int = 0
        self.debug_panel = NetworkDebugPanel(enabled=settings.NETWORK_DEBUG)

        #: uid of the hand card staged for the mod rack (right-click, then slot)
        self.pending_mod_uid: Optional[int] = None
        #: Set while the table is waiting for a decision, and the seat it
        #: belongs to — a client must not answer somebody else's question.
        self.pending_choice: Optional[PendingChoice] = None
        self.pending_choice_seat: int = 0
        self.rename = TextField(max_length=settings.RULES.max_name_length,
                                placeholder="wpisz nazwę")
        #: Frame time, kept so drawing can animate hovers without a second clock.
        self.dt = 0.0
        #: Title/text of the card currently being played, used as the opening
        #: phase of a random reveal ("Seks z pedałami" before what it turns up).
        self._playing_title: Optional[str] = None
        self._playing_text: Optional[str] = None
        #: Seconds left of a card being held up by the game rather than by the
        #: player.  While it runs the board holds new walks back, so the pawn
        #: moves after the card has been seen and not underneath it.
        self._spotlight_left = 0.0

        self.hand.notify = self.status_bar.notify

        self.bus.subscribe(ev.ActionRejected, self._on_rejected)
        self.bus.subscribe(ev.ModPlaced, self._on_mod_placed)
        self.bus.subscribe(ev.CardPlayed, self._on_card_played)
        self.bus.subscribe(ev.ActivePlayerChanged, self._on_player_changed)
        self.bus.subscribe(ev.ChoiceRequired, self._on_choice_required)
        self.bus.subscribe(ev.AbilityUsed, self._on_ability_used)
        self.bus.subscribe(ev.AbilityUnavailable, self._on_ability_unavailable)
        self.bus.subscribe(ev.CardTransformed, self._on_card_transformed)
        self.bus.subscribe(ev.CardRevealed, self._on_card_revealed)
        self.bus.subscribe(ev.ChestLimitReached, self._on_chest_limit)
        self.bus.subscribe(ev.CardSpotlighted, self._on_card_spotlighted)
        self.bus.subscribe(ev.TurnSkipped, self._on_turn_skipped)
        self.bus.subscribe(ev.CardStolen, self._on_card_stolen)
        self.bus.subscribe(ev.CardDrawEffect, self._on_card_draw_effect)
        self.bus.subscribe(ev.StatusGranted, self._on_status_granted)
        self.bus.subscribe(ev.MatchBegan, self._on_match_began)
        self.bus.subscribe(ev.PawnEliminated, self._on_pawn_eliminated)
        self.bus.subscribe(ev.MatchEnded, self._on_match_ended)
        if not self.state.phase.playable:
            # Joining a match that has not begun (the ordinary case online) or
            # one that is already over (a reconnection after the last move).
            self._sync_match_overlays()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def on_enter(self) -> None:
        pygame.display.set_caption(
            f"{settings.APP_TITLE} — {self.state.config.board_cells} pól, "
            f"{len(self.state.players)} graczy"
        )
        self.board_view.build()

    def on_resize(self) -> None:
        """The window changed size; drop cached art drawn at the old scale."""
        self.cards.clear_cache()

    # ── which seat is on screen ──────────────────────────────────────────────
    @property
    def my_seat(self) -> int:
        """The seat this machine owns.  Never changes during a match."""
        return self.state.local_seat

    @property
    def controls_view(self) -> bool:
        """Whether the seat being shown is one we may actually play."""
        return self.state.may_control(self.view_seat)

    def may_view(self, seat: int) -> bool:
        """Looking at another player's cards is a testing aid, not a feature.

        Allowed in a hot-seat game (everyone is at the same table anyway) and
        with the development option on.  Never in an ordinary network match —
        that is the whole point of hidden information.
        """
        if seat == self.my_seat:
            return True
        return self.state.edit_mode or self.state.config.debug_version

    def focus_seat(self, seat: int) -> None:
        if self.may_view(seat):
            self.view_seat = seat

    def return_to_my_seat(self) -> None:
        self.view_seat = self.my_seat
        self.status_bar.notify("Wróciłeś do swojego gracza")

    def submit(self, command: cmd.Command) -> None:
        """The single funnel through which the UI changes anything."""
        if isinstance(command, cmd.PlayCard):
            # Remember what is being played, so a card that reveals another one
            # can open its animation with the card the player actually chose.
            player = self.state.player(command.player_index)
            played = player.card_by_uid(command.card_uid) if player else None
            if played is not None:
                self._playing_title = played.title
                self._playing_text = played.text
        self.session.submit(command)

    # ── event reactions ──────────────────────────────────────────────────────
    def _on_rejected(self, event: ev.ActionRejected) -> None:
        # A pending choice already explains itself in the board overlay; the
        # engine's refusal would only repeat it as an error.
        if self.pending_choice is not None:
            return
        self.status_bar.notify(event.reason)

    def _on_choice_required(self, event: ev.ChoiceRequired) -> None:
        """The engine says the action is legal but needs a decision."""
        self.pending_choice_seat = event.player_index
        self.pending_choice = PendingChoice(
            key=event.key,
            kind=event.kind,
            prompt=event.prompt,
            options=[tuple(option) for option in event.options],
            tiles=list(event.tiles),
            pawns=list(event.pawns),
            answers=dict(event.answered),
            description=event.description,
            card_options=list(event.card_options),
            count=event.count,
            ordered=event.ordered,
            owner=event.owner,
            card_uid=event.card_uid,
            ability_source=event.ability_source,
        )
        self.hand.cancel_drag()
        self.board_view.choice_tiles = list(event.tiles)
        self.board_view.choice_pawns = list(event.pawns)
        self.board_view.choice_selected = []
        if event.tiles:
            self.board_view.focus_on_tiles(event.tiles)

        if event.kind == "card":
            # A hand belonging to somebody else.  This event reached this
            # machine and no other (N40), so laying the cards out here does not
            # show them to anybody who should not see them.
            self._show_card_picker(event)
            return

        colours = {
            pawn.id: pawn.color for pawn in self.state.library.pawns
        } if event.kind == "pawn" else None
        self.choice_prompt.show(
            event.prompt, event.kind, [tuple(o) for o in event.options],
            event.description, colours, count=event.count, ordered=event.ordered,
        )

    def _show_card_picker(self, event: ev.ChoiceRequired) -> None:
        """Lay out the cards the engine is offering, in the order it gave them."""
        cards = []
        for uid in event.card_options:
            card = self.state.find_card(uid)
            if card is not None:
                cards.append(card)
        if not cards:
            # The hand emptied between the question and the answer, which can
            # only happen to a client whose replica is behind.  Say so rather
            # than opening an empty panel nothing can be clicked in.
            self.pending_choice = None
            self.status_bar.notify("Nie ma już czego przeglądać")
            return
        self.card_picker.show(cards, event.prompt, event.description)

    def _resolve_multi_choice(self) -> None:
        """Answer a multi-select with the picks, in the order they were made."""
        choice = self.pending_choice
        if choice is None or not self.choice_prompt.ready:
            return
        answer = ",".join(self.choice_prompt.selected)
        self.pending_choice = None
        self._clear_choice_ui()
        self.submit(choice.resubmit(self.pending_choice_seat, answer))

    def _resolve_choice(self, option_id: str) -> None:
        choice, self.pending_choice = self.pending_choice, None
        self._clear_choice_ui()
        if choice is not None:
            self.submit(choice.resubmit(self.pending_choice_seat, option_id))

    def _cancel_choice(self) -> None:
        self.pending_choice = None
        self._clear_choice_ui()
        self.status_bar.notify("Anulowano — nic się nie stało")

    def _clear_choice_ui(self) -> None:
        self.board_view.choice_tiles = []
        self.board_view.choice_pawns = []
        self.board_view.choice_selected = []
        self.choice_prompt.hide()
        self.card_picker.hide()
        self.status_bar.clear()

    # ── abilities, reveals and the chest limit ───────────────────────────────
    def _on_ability_used(self, event: ev.AbilityUsed) -> None:
        left = "" if event.uses_left is None else f"  ({event.uses_left} zostało)"
        self.status_bar.notify(f"{event.title}: {event.description}{left}")

    def _on_ability_unavailable(self, event: ev.AbilityUnavailable) -> None:
        self.status_bar.notify(event.reason, duration=6.0)

    def _on_status_granted(self, event: ev.StatusGranted) -> None:
        if event.source:
            self.status_bar.notify(f"{event.label} — {event.source}")

    def _on_card_transformed(self, event: ev.CardTransformed) -> None:
        """Gamechanger: show what was drawn, then what it became."""
        colour = self.app.renderer.theme.deck_colors.get(settings.DECK_CHEST)
        self.reveal.show([
            RevealPhase(event.from_title, event.intro_text, event.delay, colour),
            RevealPhase(event.to_title, event.to_text, 3.0,
                        self.app.renderer.theme.brass_light,
                        subtitle="trafia na twoją rękę"),
        ])

    def _on_card_revealed(self, event: ev.CardRevealed) -> None:
        """Seks z pedałami: dwell on the card played, then on the one it found."""
        card = self.state.deck(event.deck_id).find_discarded(event.card_uid)
        colour = self.app.renderer.theme.deck_colors.get(event.deck_id)
        self.reveal.show([
            RevealPhase(self._playing_title or "Losowanie", self._playing_text or "",
                        event.announce_seconds, colour),
            RevealPhase(event.title, event.text, 2.5, colour,
                        subtitle="wylosowana karta"),
        ], card)

    def _on_chest_limit(self, event: ev.ChestLimitReached) -> None:
        player = self.state.player(event.player_index)
        if player is None:
            return
        if not self.state.may_control(event.player_index):
            # Somebody else's overflowing hand: their machine answers it.
            self.status_bar.notify(f"{player.name} wybiera Kartę Skrzyni…")
            return
        cards = [c for c in player.hand if c.uid in set(event.card_uids)]
        self.chest_choice_seat = event.player_index
        self.view_seat = event.player_index
        self.chest_choice.show(cards, event.limit, event.new_card_uid)

    def _on_card_spotlighted(self, event: ev.CardSpotlighted) -> None:
        """A card the player did not choose, held up before it takes effect.

        The state has already changed — Troll has already played the card and
        the pawn is already where it will be (N36).  What this buys is the
        picture arriving in a sensible order: the card is shown with a ring
        round it, the board holds its walks back for the same few seconds, and
        only then does anything appear to move.
        """
        card = self.state.find_card(event.card_uid)
        if card is None:
            card = self.state.deck(event.deck_id).find_discarded(event.card_uid)
        theme = self.app.renderer.theme
        colour = theme.deck_colors.get(event.deck_id) or theme.brass
        subtitle = event.caption or ("zagrywasz tę kartę" if event.forced else "")
        self.reveal.show(
            [RevealPhase(event.title, event.text, event.seconds, colour,
                         subtitle=subtitle, halo=True)],
            card,
        )
        self._spotlight_left = max(self._spotlight_left, event.seconds)
        self.board_view.walk_delay = self._spotlight_left
        player = self.state.player(event.player_index)
        if player is not None:
            self.status_bar.notify(f"{player.name}: {event.title}",
                                   duration=max(3.0, event.seconds))

    def _on_turn_skipped(self, event: ev.TurnSkipped) -> None:
        player = self.state.player(event.player_index)
        if player is None:
            return
        because = f" — {event.source}" if event.source else ""
        self.status_bar.notify(f"{player.name}: tura pominięta{because}",
                               duration=4.0)

    def _on_card_stolen(self, event: ev.CardStolen) -> None:
        """Say that a card changed hands, without saying which one.

        The event carries no title on purpose, and this must not go looking for
        one: everybody sees this message, and only the two players involved are
        entitled to know what moved.
        """
        victim = self.state.player(event.from_player)
        thief = self.state.player(event.to_player)
        if victim is None or thief is None:
            return
        self.status_bar.notify(
            f"{thief.name} zabiera kartę ruchu graczowi {victim.name}",
            duration=4.5,
        )

    def _on_card_draw_effect(self, event: ev.CardDrawEffect) -> None:
        """A card that did something the moment it was drawn."""
        if not self.state.may_control(event.player_index):
            return
        self.status_bar.notify(f"{event.title}: {event.description}",
                               duration=5.0)

    def _on_mod_placed(self, event: ev.ModPlaced) -> None:
        self.status_bar.notify("Mod Patusa aktywny")

    def _on_card_played(self, event: ev.CardPlayed) -> None:
        card = self.state.deck(event.deck_id).find_discarded(event.card_uid)
        player = self.state.player(event.player_index)
        if card is None or player is None:
            return
        theme = self.app.renderer.theme
        colour = theme.deck_colors.get(event.deck_id) or theme.brass
        self.recently_played.push(card, player.name, event.description, colour)
        self.status_bar.notify(f"{player.name}: {event.title} — {event.description}")

    def _on_player_changed(self, event: ev.ActivePlayerChanged) -> None:
        self.hand.cancel_drag()
        self.pending_mod_uid = None
        if self.state.edit_mode:
            # Hot-seat: the screen follows the turn, because the person at the
            # keyboard is now playing that seat.
            self.view_seat = event.player_index
        if self.pending_choice is not None:
            self._cancel_choice()

    # ── input ────────────────────────────────────────────────────────────────
    # ── the match beginning and ending ───────────────────────────────────────
    def _on_match_began(self, event: ev.MatchBegan) -> None:
        self.match_start.hide()
        self.status_bar.notify("Gra się rozpoczęła!")

    def _on_pawn_eliminated(self, event: ev.PawnEliminated) -> None:
        """A check failed.  Every notepad crosses the colour off by itself.

        Nothing is drawn from here: the panel reads
        ``state.eliminated_pawns`` directly, so a player who joined late or
        reconnected sees the same crossings without having heard the event.
        """
        pawn = self.state.library.pawn(event.pawn_id)
        name = pawn.name if pawn is not None else event.pawn_id
        self.status_bar.notify(f"Sprawdzono wieżę: {name} to nie Piotrek", 5.0)

    def _on_match_ended(self, event: ev.MatchEnded) -> None:
        self._clear_choice_ui()
        self.chest_choice.hide()
        self.reveal.dismiss()
        self._sync_match_overlays()

    def _sync_match_overlays(self) -> None:
        """Put the overlays where the state says they should be.

        Driven by the state rather than by the events that got it there, so a
        reconnecting player who replays twenty commands in one frame ends up
        looking at the right thing.
        """
        state = self.state
        if state.victory is not None:
            self.match_start.hide()
            if not self.victory.active:
                pawn = state.library.pawn(state.victory.pawn_id)
                self.victory.show(
                    state.victory,
                    pawn.color if pawn is not None else (200, 200, 200),
                    pawn.name if pawn is not None else state.victory.pawn_id,
                    can_return=self.service is not None,
                )
            return
        if not state.phase.playable:
            self.match_start.show(*self._identity_question())
        else:
            self.match_start.hide()

    def _identity_question(self):
        """(colours to offer, colour already chosen) for the start overlay.

        Online the SERVER decides whether this machine is asked at all, and it
        asks exactly one; the answer is simply mirrored here.  In a hot-seat
        game there is nobody to ask and nobody to hide from — everyone is at the
        one keyboard — so the same overlay is shown to whoever is sitting there
        and the choice is applied locally.  One flow, two sources of the
        question.
        """
        state = self.state
        if self.service is not None:
            return (list(getattr(self.service, "identity_request", []) or []),
                    getattr(self.service, "identity_pawn", "") or "")
        pawns = [{"id": p.id, "name": p.name, "color": list(p.color)}
                 for p in state.library.pawns]
        return pawns, state.piotrek_pawn or ""

    def _handle_victory_event(self, event: pygame.event.Event,
                              mouse: Tuple[int, int]) -> None:
        """Two buttons, and nothing else works.  The match is over."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        choice = self.victory.button_at(mouse)
        if choice == VictoryOverlay.QUIT:
            self._leave_match(quit_app=True)
        elif choice == VictoryOverlay.MENU:
            # Leave the room properly on the way out: the seat is freed for
            # whoever is left rather than held open by a grace period nobody is
            # waiting through.
            self._leave_match()
        elif choice == VictoryOverlay.RETURN:
            self._return_to_lobby()

    def _return_to_lobby(self) -> None:
        """Ask the server to put the room back, and wait to be told it did.

        Nothing happens on screen here for the same reason starting a match
        shows nothing: the answer is a broadcast, and every player leaves the
        table on the same message rather than each on their own click.
        """
        if self.service is None:
            self._leave_match()
            return
        self.service.return_to_lobby()

    def _handle_identity_event(self, event: pygame.event.Event,
                               mouse: Tuple[int, int]) -> None:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        pawn_id = self.match_start.pawn_at(mouse)
        if not pawn_id:
            return
        if self.service is not None:
            self.service.choose_identity(pawn_id)
            # Shown as chosen at once.  The server confirms within a frame or
            # two and the overlay is rebuilt from its answer either way.
            self.match_start.show(self.match_start.pawns, pawn_id)
            return
        # Hot-seat: this machine is its own authority, so the colour is stored
        # here and the match begins through the ordinary command path.
        if self.state.set_piotrek_pawn(pawn_id):
            self.submit(cmd.BeginMatch())

    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        # 1. Renaming captures all input until confirmed or cancelled.
        if self.rename.active:
            if event.type == pygame.MOUSEBUTTONDOWN:
                self._commit_rename()
                return
            result = self.rename.handle(event)
            if result is not None:
                self._commit_rename(result)
            return

        if self.pause_menu.active:
            self._handle_pause_event(event, mouse)
            return

        # 1a. The ending is absolutely modal: there is no game left to play, and
        #     Esc must not offer to "leave" a match that has already finished.
        if self.victory.active:
            self._handle_victory_event(event, mouse)
            return

        # 1b. Before the first move: Piotrek picks a colour, everybody else
        #     waits.  Nothing on the table responds to anything.
        if self.match_start.active:
            self._handle_identity_event(event, mouse)
            return

        if event.type == pygame.KEYDOWN:
            self._handle_key(event)
            return

        # 2. The chest limit is fully modal: nothing else may happen until the
        #    player says which cards they keep.
        if self.chest_choice.active:
            self._handle_chest_event(event, mouse)
            return

        # 2a. Somebody else's hand is fully modal too: it is showing hidden
        #     information, so nothing else may happen behind it.
        if self.card_picker.active:
            self._handle_card_picker_event(event, mouse)
            return

        # 3. A pending decision blocks everything except answering it and
        #    looking around the board.
        if self.pending_choice is not None:
            self._handle_choice_event(event, mouse)
            return

        # A reveal is only presentation.  Clicking it dismisses it; clicking
        # past it dismisses it *and* does whatever was clicked, so the animation
        # never costs the player an action.
        if self.reveal.active and event.type == pygame.MOUSEBUTTONDOWN:
            on_card = self.reveal.hit(self.app.layout, mouse)
            self.reveal.dismiss()
            if on_card:
                return

        # These two float over the board, so they have to be checked before the
        # board claims the click as a map drag.
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.app.layout.end_turn_button.collidepoint(mouse)):
            self._end_turn_click(mouse)
            return
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.view_seat != self.my_seat
                and self.app.layout.return_seat_button.collidepoint(mouse)):
            self.return_to_my_seat()
            return

        # 4. The hand fan is the topmost layer on screen.
        if self.hand.handle_event(event, mouse):
            return

        # 5. The board owns navigation and pawn dragging.
        if self.board_view.handle_event(event, mouse):
            return

        if event.type != pygame.MOUSEBUTTONDOWN:
            return

        ctx = self._context(mouse)

        # 5. Round counter.
        commands = self.round_panel.handle_click(ctx, event.button, self)
        if commands:
            self._submit_all(commands)
            return

        # 6. Right-click chain: cancel staging, stage a hand card, discard a mod.
        if event.button == 3:
            if self.pending_mod_uid is not None:
                self.pending_mod_uid = None
                return
            uid = self.hand._card_at(mouse)
            if uid is not None:
                self.pending_mod_uid = uid
                return
            self._route(ctx, 3, (self.mod_panel,))
            return

        if event.button != 1:
            return

        # 7. Left-click chain, in the prototype's order.
        if self.pending_mod_uid is not None:
            self._route(ctx, 1, (self.mod_panel,))
            self.pending_mod_uid = None
            return

        if self._end_turn_click(mouse):
            return
        if self._ability_click(mouse):
            return
        self._route(
            ctx, 1, (self.player_tiles, self.deck_panel, self.character_panel)
        )

    def _handle_choice_event(self, event: pygame.event.Event,
                             mouse: Tuple[int, int]) -> None:
        """While choosing: clicks answer, the wheel and middle drag still look.

        Three ways to answer, all reaching the same place: the buttons in the
        prompt, the highlighted field on the board, or the pawn itself.
        """
        choice = self.pending_choice
        if choice is None:
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if choice.count > 1:
                self._handle_multi_click(choice, mouse)
                return
            option = self.choice_prompt.option_at(mouse)
            if option is not None:
                self._resolve_choice(option)
                return
            if choice.kind == "tile":
                tile = self.board_view.choice_tile_at(mouse)
                if tile is not None:
                    self._resolve_choice(str(tile))
                return
            if choice.kind == "pawn":
                pawn_id = self.board_view.token_at(mouse)
                if pawn_id is not None and pawn_id in {o[0] for o in choice.options}:
                    self._resolve_choice(pawn_id)
                return
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            self._cancel_choice()
            return
        if event.type in (pygame.MOUSEWHEEL, pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
            self.board_view.handle_event(event, mouse)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 2:
            self.board_view.handle_event(event, mouse)

    def _handle_multi_click(self, choice: PendingChoice,
                            mouse: Tuple[int, int]) -> None:
        """A click while several things are being picked: add, remove, confirm.

        Picking is not answering here — the engine wants a list, so the click
        toggles and the Confirm button sends.  Clicking a pawn that is already
        picked takes it back out and the numbers close up behind it.
        """
        if self.choice_prompt.confirm_hit(mouse):
            self._resolve_multi_choice()
            return
        option = self.choice_prompt.option_at(mouse)
        if option is None and choice.kind == "pawn":
            # The pawn out on the board is the other way to answer, and for a
            # question about pawns it is the natural one.
            pawn_id = self.board_view.token_at(mouse)
            if pawn_id is not None and pawn_id in {o[0] for o in choice.options}:
                option = pawn_id
        if option is None:
            return
        self.choice_prompt.toggle(option)
        self.board_view.choice_selected = list(self.choice_prompt.selected)

    def _handle_card_picker_event(self, event: pygame.event.Event,
                                  mouse: Tuple[int, int]) -> None:
        """Clicking one of somebody else's cards takes it; right-click backs out."""
        if event.type != pygame.MOUSEBUTTONDOWN:
            return
        if event.button == 3:
            self._cancel_choice()
            return
        if event.button != 1:
            return
        uid = self.card_picker.card_at(self.app.layout, mouse)
        if uid is not None:
            self._resolve_choice(str(uid))

    def _handle_chest_event(self, event: pygame.event.Event,
                            mouse: Tuple[int, int]) -> None:
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        layout = self.app.layout
        if self.chest_choice.confirm_hit(layout, mouse):
            keep = tuple(self.chest_choice.keep)
            self.chest_choice.hide()
            self.submit(cmd.KeepChestCards(
                player_index=self.chest_choice_seat, keep_uids=keep))
            return
        uid = self.chest_choice.card_at(layout, mouse)
        if uid is not None:
            self.chest_choice.toggle(uid)

    @property
    def can_end_turn(self) -> bool:
        """Whether the button should be live for whoever is at this machine."""
        seat = self.state.active_player_index
        return (self.state.may_control(seat)
                and self.chest_choice.active is False
                and self.card_picker.active is False
                and self.pending_choice is None)

    def _end_turn_click(self, mouse: Tuple[int, int]) -> bool:
        if not self.app.layout.end_turn_button.collidepoint(mouse):
            return False
        if not self.can_end_turn:
            self.status_bar.notify("Teraz nie możesz zakończyć tury")
            return True
        self.submit(cmd.EndTurn(player_index=self.state.active_player_index))
        return True

    def _draw_end_turn_button(self, ctx: HudContext) -> None:
        """Always visible on your turn; greyed out when it is not yours."""
        r, surface = ctx.r, ctx.surface
        rect = ctx.layout.end_turn_button
        live = self.can_end_turn
        hovered = live and rect.collidepoint(ctx.mouse)
        theme = ctx.theme
        style = r.emphasis(fill=theme.btn_primary_bg,
                           border=theme.btn_primary_border,
                           text=theme.btn_primary_text,
                           hover=1.0 if hovered else 0.0, enabled=live,
                           accent=theme.accent)
        drawn = r.interactive_panel(rect, style, surface, radius=10)
        r.fit_spaced_text("ZAKOŃCZ TURĘ", drawn, style.text, surface,
                          base_size=int(15 * ctx.layout.ui_scale), spacing=2,
                          padding=14, shadow=live)

    def _ability_click(self, mouse: Tuple[int, int]) -> bool:
        """The 'use ability' button under the character card."""
        player = self.state.player(self.view_seat) or self.state.active_player
        if not self.controls_view:
            rect = self.app.layout.ability_button_rect(player.is_piotrek)
            if rect.collidepoint(mouse):
                self.status_bar.notify("To nie jest twoja postać")
                return True
            return False
        source = "skill" if player.is_piotrek else "character"
        card = player.skill if source == "skill" else player.character
        rect = self.app.layout.ability_button_rect(player.is_piotrek)
        if card is None or not card.has_ability or not rect.collidepoint(mouse):
            return False
        if not card.ability_available:
            self.status_bar.notify("Ta umiejętność została już zużyta")
            return True
        self.submit(cmd.UseAbility(player_index=player.index, source=source))
        return True

    def _route(self, ctx: HudContext, button: int, panels) -> bool:
        for panel in panels:
            commands = panel.handle_click(ctx, button, self)
            if commands:
                self._submit_all(commands)
                return True
        return False

    def _submit_all(self, commands: List[cmd.Command]) -> None:
        for command in commands:
            self.submit(command)

    def _handle_pause_event(self, event: pygame.event.Event,
                            mouse: Tuple[int, int]) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.pause_menu.close()
            return
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return
        choice = self.pause_menu.entry_at(mouse)
        if choice == "resume":
            self.pause_menu.close()
        elif choice == "leave":
            self._leave_match()
        elif choice == "quit":
            self._leave_match(quit_app=True)

    def _pause_entries(self):
        entries = [("resume", "Wróć do gry")]
        if self.service is not None:
            # Leaving no longer ends anybody else's match: the game lives on the
            # server, so the others carry on and this seat is simply empty.
            entries.append(("leave", "Opuść rozgrywkę"))
        else:
            entries.append(("leave", "Wróć do menu głównego"))
        entries.append(("quit", "Wyjdź z gry"))
        return entries

    def _back_to_lobby_screen(self) -> None:
        """The room is a lobby again.  Go and sit in it."""
        from .network_screens import LobbyScreen

        self.app.replace(LobbyScreen(self.app, self.library or self.state.library,
                                     self.service))

    def _leave_match(self, quit_app: bool = False, message: str = "") -> None:
        """Close the connection and go back where the player came from."""
        if self.service is not None:
            self.service.close()
        if quit_app:
            self.app.quit()
            return
        from .network_screens import MainMenuScreen

        menu = MainMenuScreen(self.app, self.library or self.state.library)
        if message:
            menu.notify(message)
        self.app.replace(menu)

    def _handle_key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_F3:
            self.debug_panel.toggle()
            return
        if event.key == pygame.K_ESCAPE and (self.pending_choice is not None
                                             or self.card_picker.active):
            self._cancel_choice()
            return
        if event.key == pygame.K_ESCAPE and self.reveal.active:
            self.reveal.dismiss()
            return
        if event.key == pygame.K_ESCAPE:
            if self.hand.dragging is not None or self.hand.pressed is not None:
                self.hand.cancel_drag()
                return
            if self.pending_mod_uid is not None:
                self.pending_mod_uid = None
                return
            # Nothing to cancel: Esc is the way out of the match.
            self.pause_menu.open(self._pause_entries())
        elif event.key == pygame.K_s:
            settings.SNAP_TOKENS_TO_TILES = not settings.SNAP_TOKENS_TO_TILES
            self.status_bar.notify(
                "Przyciąganie pionków do pól: "
                + ("włączone" if settings.SNAP_TOKENS_TO_TILES else "wyłączone")
            )
        elif event.key == pygame.K_f:
            camera = self.board_view.camera
            camera.set_zoom(camera.min_zoom)
            camera.move_to((self.state.board.width / 2, self.state.board.height / 2))
            self.status_bar.notify("Widok dopasowany do planszy")
        elif event.key == pygame.K_HOME:
            self.return_to_my_seat()
        elif event.key == pygame.K_TAB:
            if not self.state.edit_mode:
                self.status_bar.notify(
                    "Tryb edycji wyłączony — sterujesz tylko swoim miejscem")
                return
            nxt = (self.state.active_player_index + 1) % len(self.state.players)
            self.submit(cmd.SetActivePlayer(player_index=nxt))

    # ── rename ───────────────────────────────────────────────────────────────
    def start_rename(self, player_index: int) -> None:
        self.rename.start(player_index, "")

    def _commit_rename(self, text: Optional[str] = None) -> None:
        index = self.rename.target_index
        value = (text if text is not None else self.rename.buffer).strip()
        self.rename.stop()
        if index is not None and value:
            self.submit(cmd.RenamePlayer(player_index=index, name=value))

    # ── frame ────────────────────────────────────────────────────────────────
    def _context(self, mouse: Tuple[int, int], dt: float = 0.0) -> HudContext:
        if not self.may_view(self.view_seat):
            # Safety net: a seat we lost the right to watch (the game left edit
            # mode, say) snaps back to our own rather than leaking a hand.
            self.view_seat = self.my_seat
        return HudContext(
            r=self.app.renderer,
            cards=self.cards,
            layout=self.app.layout,
            state=self.state,
            surface=self.app.canvas,
            mouse=mouse,
            pending_mod_uid=self.pending_mod_uid,
            rename=self.rename,
            dt=dt,
            view_index=self.view_seat,
            can_act=self.controls_view,
        )

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        self.dt = dt
        if self.service is not None:
            # One call for both roles now.  The host is a client too, so there
            # is no longer a second code path that only one machine takes —
            # which is where several of the old ownership bugs lived.
            self.service.poll(self.library)
            for notice in self.service.drain_notices():
                self.status_bar.notify(notice)
            dropped = getattr(self.service, "disconnected", None)
            if dropped:
                self._leave_match(message=dropped)
                return
            if self.service.session is None:
                # The room went back to being a lobby, which only happens after
                # a finished match.  Everybody makes this trip on the server's
                # message, not on their own click.
                self._back_to_lobby_screen()
                return
        else:
            self.session.poll()
        self._sync_match_overlays()
        # Holding Backspace in the rename box only deletes continuously while
        # this is called; the field has no clock of its own.
        self.rename.update(dt)
        self.pause_menu.update(dt, mouse)
        self.hand.drop_zone = self.app.layout.board_viewport
        self.hand.update(dt, mouse)
        self.board_view.update(dt, mouse)
        self.recently_played.update(dt, mouse)
        self.status_bar.update(dt)
        self.choice_prompt.update(dt, mouse)
        self.match_start.update(dt, mouse)
        self.victory.update(dt, mouse)
        self.reveal.update(dt)
        self.chest_choice.update(dt, self.app.layout, mouse)
        self.card_picker.update(dt, self.app.layout, mouse)
        if self._spotlight_left > 0.0:
            # Counts down in real time and only affects the picture: the walk
            # it is holding back has already happened as far as the rules are
            # concerned, so a slow machine sees it late, never differently.
            self._spotlight_left = max(0.0, self._spotlight_left - dt)
            self.board_view.walk_delay = self._spotlight_left
        self._sync_drag_preview()

    def _draw_return_button(self, ctx: HudContext) -> None:
        """A way back to your own seat that is always on screen when you need it.

        Only drawn while looking somewhere else, so it never becomes furniture.
        """
        if self.view_seat == self.my_seat:
            return
        r, surface = ctx.r, ctx.surface
        rect = ctx.layout.return_seat_button
        hovered = rect.collidepoint(ctx.mouse)
        owner = self.state.player(self.my_seat)
        name = owner.name if owner is not None else "swojego gracza"
        theme = ctx.theme
        style = r.emphasis(border=theme.panel_edge, text=theme.text_light,
                           hover=1.0 if hovered else 0.0)
        drawn = r.interactive_panel(rect, style, surface, radius=9)
        r.fit_text(f"Wróć do: {name}  (Home)", drawn, style.text, surface,
                   base_size=int(14 * ctx.layout.ui_scale), padding=12)

    def _draw_turn_banner(self, ctx: HudContext) -> None:
        """Whose turn it is, and — when it is not yours — that you are waiting.

        Only shown when the turn actually restricts anything: in hot-seat games
        everybody is playing everybody, and a banner saying so would be noise.
        """
        if self.state.edit_mode:
            return
        r, layout, theme = ctx.r, ctx.layout, ctx.theme
        surface = ctx.surface
        active = self.state.active_player
        mine = self.state.may_act(self.state.local_seat)

        watching = self.view_seat != self.my_seat
        if watching:
            viewed = self.state.player(self.view_seat)
            text = f"Podgląd: {viewed.name if viewed else '?'}"
        else:
            text = ("Twoja tura" if mine else f"Tura: {active.name} — czekasz")
        colour = (theme.brass_light if watching
                  else (theme.valid if mine else theme.prompt))
        font = r.fonts.get(20, bold=True)
        rendered = r.text_surface(text, font, colour)
        box = rendered.get_rect(midtop=(layout.board_viewport.centerx,
                                        layout.board_viewport.top + 10))
        backdrop = box.inflate(46, 16)
        # A pill floating over the top of the board, as in the concept.
        r.premium_panel(backdrop, surface, radius=backdrop.height // 2,
                        fill=theme.panel_bg, border=colour, ornaments=False,
                        shadow=14)
        surface.blit(rendered, box.topleft)
        if not mine:
            r.text("obserwujesz — poczekaj na swoją kolej", r.fonts.label(),
                   theme.text_dim, surface,
                   midtop=(backdrop.centerx, backdrop.bottom + 2))

    def _sync_drag_preview(self) -> None:
        """Show, on the board, what the card being dragged would do."""
        card = self.hand.card_by_uid(self.hand.dragging)
        if card is None:
            self.board_view.preview_route = []
            return
        plan = self.hand.drag_preview or effects.preview(self.state, card)
        tiles = effects.preview_tiles(self.state, plan)
        self.board_view.preview_route = list(tiles)
        self.board_view.preview_valid = bool(tiles)

    def _draw_connection_banner(self, ctx: HudContext) -> None:
        """Say so while the connection is being re-established.

        The board stays on screen and the interface stays alive: a four-second
        WiFi hiccup should cost four seconds, not the match.  The banner is the
        only thing that changes, because the transport is already reconnecting
        and the state sync that follows will put everything right.
        """
        service = self.service
        if service is None or not getattr(service, "reconnecting", False):
            return
        r, surface, theme = ctx.r, ctx.surface, ctx.theme
        font = r.fonts.get(19, bold=True)
        text = "ŁĄCZĘ PONOWNIE Z SERWEREM…"
        width = font.size(text)[0] + 64
        rect = pygame.Rect(0, 0, width, font.get_height() + 26)
        rect.midtop = (ctx.layout.win_w // 2, int(18 * ctx.layout.ui_scale))
        r.premium_panel(rect, surface, radius=10, fill=theme.warning_bg,
                        border=theme.panel_edge, ornaments=False, shadow=12)
        r.spaced_text(text, font, theme.prompt, surface, center=rect.center,
                      spacing=2, shadow=True)

    def draw(self, surface: pygame.Surface) -> None:
        ctx = self._context(self.app.mouse(), self.dt)
        ctx.surface = surface

        self.round_panel.draw(ctx)
        self.board_view.draw(surface)
        self.recently_played.draw(ctx)
        self.deck_panel.draw(ctx)
        self.mod_panel.draw(ctx)
        self.character_panel.draw(ctx)
        self.player_tiles.draw(ctx)
        self.status_bar.draw(ctx)
        self.hand.draw(surface)
        # Last of all: a played card the player is inspecting sits above
        # everything, including the hand fan.
        self.recently_played.draw_overlay(ctx)
        self._draw_turn_banner(ctx)
        self._draw_return_button(ctx)
        self._draw_end_turn_button(ctx)
        self.choice_prompt.draw(self.app.renderer, self.app.layout, surface, ctx.mouse)
        self.reveal.draw(self.app.renderer, self.cards, self.app.layout, surface)
        self.chest_choice.draw(self.app.renderer, self.cards, self.app.layout,
                               surface, ctx.mouse)
        self.card_picker.draw(self.app.renderer, self.cards, self.app.layout,
                              surface, ctx.mouse)
        self._draw_connection_banner(ctx)
        # Above everything except the pause menu: these two ARE the screen
        # while they are up.
        self.match_start.draw(self.app.renderer, self.app.layout, surface,
                              ctx.mouse)
        self.victory.draw(self.app.renderer, self.app.layout, surface, ctx.mouse)
        self.pause_menu.draw(self.app.renderer, self.app.layout, surface)
        self.debug_panel.draw(
            self.app.renderer, self.app.layout, surface,
            session=self.session, service=self.service, state=self.state,
        )
