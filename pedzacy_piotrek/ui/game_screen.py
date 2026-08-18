"""
The game screen.

Successor to the prototype's ``run_game``, but where that function held state,
layout, rendering and input in one 640-line scope, this one only *routes*: it
offers an event to the hand fan, then the board, then each panel in priority
order, collects the commands they produce and hands them to the session.  It
contains no rules.

Input priority:

    renaming → THE MODAL STACK → keyboard → floating buttons → hand fan →
    board → round counter → right-click chain → left-click chain

Everything that can own input as a WINDOW lives in the modal stack
(ui/modals.py) rather than in a hand-written chain of ``if ... .active:``
tests.  One list decides both what is painted on top and what receives the
click, so the two can no longer disagree — which is the bug round 7 exposed,
where the Mod Patusa selection was drawn over the Chest limit and the Chest
limit answered the clicks.  Register a new window in ``_register_modals``;
do not add another branch here.
"""

from __future__ import annotations

import random
import time
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
from .ability_cards import AbilityCards
from .card_library import CardLibrary, draw_library_button
from .check_decision import BreakupChoice, CheckDecision
from .movement_decision import MovementDecision
from .hand_fan import HandFan
from .debug_panel import NetworkDebugPanel
from .match_overlays import (EliminationNotice, MatchStartOverlay,
                             VictoryOverlay)
from .card_preview import CardPreview
from .modals import Modal, ModalStack
from .overlays import (
    CardPicker, ChestChoice, ChestHoldingView, ChestReveal, ChoicePrompt,
    ModChoice, PauseMenu, RevealOverlay, RevealPhase,
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
        #: The enlarged hover preview shared by every panel that draws a card
        #: in a slot.  ONE of them for the whole screen: two slots cannot be
        #: under one cursor, and a preview per panel is a preview per panel to
        #: keep in step.  See ``ui/card_preview.py``.
        self.card_preview = CardPreview()
        self.status_bar = StatusBar()
        self.choice_prompt = ChoicePrompt()
        self.reveal = RevealOverlay()
        self.chest_choice = ChestChoice()
        #: The Mod Patusa selection that pauses a round.
        self.mod_choice = ModChoice()
        #: Somebody else's cards, laid out to take one from (Spy).
        self.card_picker = CardPicker()
        #: Paczka's read-only window: who holds which Chest cards.
        self.chest_reveal = ChestReveal()
        self.pause_menu = PauseMenu()
        #: The Card Library — every card in the game, and the controls that
        #: change how many of them there are.  Built with the SESSION's state
        #: and the screen's own ``submit``, so everything it does travels the
        #: road a played card travels.
        #: One character -> ability-card lookup for the whole screen (stage
        #: 50): the portrait, the turn-order map and the ability button all
        #: ask it, so they cannot disagree and cannot churn card uids.
        self.ability_cards = AbilityCards()
        self.card_library = CardLibrary(
            self.state.library, self.state, self.submit, app.renderer,
            seat=lambda: self.view_seat,
        )
        #: Before the first move and after the last one.  Both are drawn over
        #: the live table and both make the game unplayable while they are up —
        #: though the engine refuses everything anyway, so this is manners
        #: rather than enforcement.
        #: Nie masz Rosji: the two buttons under the played-card strip, and
        #: the confirmation in front of the block.  Reads the live state and
        #: answers with Commands; it owns no game state of its own.
        self.movement_decision = MovementDecision(
            self.state, seat=lambda: self.view_seat)
        #: Ice Block: the same two-button shape, in front of a CHECK instead of
        #: a movement.  The two can never be up at once — ``review`` decides
        #: nothing while a check is pending, and a paused movement is not a
        #: command that can arm one.
        self.check_decision = CheckDecision(
            self.state, seat=lambda: self.view_seat)
        #: Checking variant 2: Piotrek picking 2a or 2b for the last group of
        #: a broken tower.
        self.breakup_choice = BreakupChoice(
            self.state, seat=lambda: self.view_seat)
        self.match_start = MatchStartOverlay()
        self.victory = VictoryOverlay()
        #: "<colour> to nie Piotrek", as a card beside the board.  The status
        #: bar said it in the corner nobody watches while a tower is lifted.
        self.elimination_notice = EliminationNotice()
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

        #: Paczka's holdings, held back while a Mod selection is still running.
        #: The engine already defers the EVENT; this is the belt to that
        #: braces, for a replica that replayed an old log or resynced mid-pause.
        self._held_chest_reveal: Optional[List[ChestHoldingView]] = None
        #: Esc's random fallback.  DELIBERATELY NOT the session RNG (R4): this
        #: draw happens on ONE machine, at a moment nobody else knows about, and
        #: pulling from the shared stream would leave every other replica one
        #: number behind.  What travels is the COMMAND it produces, which is
        #: what every machine agrees on — exactly as a mouse click does.
        self._escape_rng = random.Random(self.state.config.seed ^ 0x5EC0)

        #: THE MODAL STACK.  One list behind both painting and input; see
        #: ui/modals.py for why there is only one.
        self.modals = ModalStack()
        self._register_modals()

        self.hand.notify = self.status_bar.notify

        self.bus.subscribe(ev.ActionRejected, self._on_rejected)
        self.bus.subscribe(ev.CardDrawn, self._on_card_drawn)
        self.bus.subscribe(ev.CardVariantChanged, self._on_variant_changed)
        self.bus.subscribe(ev.MovementDecisionOpened, self._on_decision_opened)
        self.bus.subscribe(ev.MovementAccepted, self._on_movement_accepted)
        self.bus.subscribe(ev.MovementBlocked, self._on_movement_blocked)
        self.bus.subscribe(ev.ModPlaced, self._on_mod_placed)
        self.bus.subscribe(ev.CardPlayed, self._on_card_played)
        self.bus.subscribe(ev.ActivePlayerChanged, self._on_player_changed)
        self.bus.subscribe(ev.ChoiceRequired, self._on_choice_required)
        self.bus.subscribe(ev.AbilityUsed, self._on_ability_used)
        self.bus.subscribe(ev.AbilityUnavailable, self._on_ability_unavailable)
        self.bus.subscribe(ev.CardTransformed, self._on_card_transformed)
        self.bus.subscribe(ev.CardRevealed, self._on_card_revealed)
        self.bus.subscribe(ev.ChestLimitReached, self._on_chest_limit)
        self.bus.subscribe(ev.ModSelectionStarted, self._on_mod_selection_started)
        self.bus.subscribe(ev.ModVoteCast, self._on_mod_vote_cast)
        self.bus.subscribe(ev.ModSelectionResolved, self._on_mod_selection_resolved)
        self.bus.subscribe(ev.ModSelectionFinished, self._on_mod_selection_finished)
        self.bus.subscribe(ev.CardSpotlighted, self._on_card_spotlighted)
        self.bus.subscribe(ev.TurnSkipped, self._on_turn_skipped)
        self.bus.subscribe(ev.CardStolen, self._on_card_stolen)
        self.bus.subscribe(ev.CardDrawEffect, self._on_card_draw_effect)
        self.bus.subscribe(ev.StatusGranted, self._on_status_granted)
        self.bus.subscribe(ev.MatchBegan, self._on_match_began)
        self.bus.subscribe(ev.PawnEliminated, self._on_pawn_eliminated)
        self.bus.subscribe(ev.PlayerEliminated, self._on_player_eliminated)
        self.bus.subscribe(ev.CheckDecisionOpened, self._on_check_decision_opened)
        self.bus.subscribe(ev.CheckAllowed, self._on_check_allowed)
        self.bus.subscribe(ev.CheckRefused, self._on_check_refused)
        self.bus.subscribe(ev.TowerBrokeUp, self._on_tower_broke_up)
        self.bus.subscribe(ev.MatchEnded, self._on_match_ended)
        self.bus.subscribe(ev.ChestCardsRevealed, self._on_chest_revealed)
        self.bus.subscribe(ev.LeadCheckAnnounced, self._on_lead_check)
        self.bus.subscribe(ev.PawnHidden, self._on_pawn_hidden)
        self.bus.subscribe(ev.PawnRestored, self._on_pawn_restored)
        if not self.state.phase.playable:
            # Joining a match that has not begun (the ordinary case online) or
            # one that is already over (a reconnection after the last move).
            self._sync_match_overlays()

    # ── the modal stack ──────────────────────────────────────────────────────
    def _register_modals(self) -> None:
        """THE ORDER OF THE WINDOWS.  Bottom first; the last one owns input.

        This list is the single source of truth for BOTH the paint order and
        the input order, so "visually on top" and "receives the click" are the
        same statement by construction rather than by two people remembering
        to keep two lists in step.  Read it top-down as the stack:

            pause menu              meta: leaving the match
            victory / match start   there is no table to play
            check / breakup / movement decisions   the table is stopped
            card library            a modal encyclopedia over a live game
            Paczka                  informational, dismissed with one click
            card picker             somebody else's hand
            MOD PATUSA SELECTION    ← above the chest limit, per stage 44
            chest limit             pending underneath while mods are chosen
            pending choice          which pawn / which field / how far
            reveal                  presentation only

        The Mod selection sitting ABOVE the chest limit is the stage 44 fix.
        It was always painted there; it now receives input there too, and the
        chest limit waits underneath instead of stealing the clicks.
        """
        layout = lambda: self.app.layout       # noqa: E731 — read every frame

        def covers(rect_of):
            def _covers(mouse):
                rect = rect_of()
                return rect is not None and rect.collidepoint(mouse)
            return _covers

        full_screen = lambda mouse: True       # noqa: E731

        self.modals.register(Modal(
            name="reveal",
            is_active=lambda: self.reveal.active,
            handle=self._modal_reveal,
            draw=lambda: self.reveal.draw(self.app.renderer, self.cards,
                                          self.app.layout, self.app.canvas),
            blocking=False, blocks_keyboard=False,
            covers=covers(lambda: self.reveal.card_rect(self.app.layout)),
            resolve=self._resolve_reveal_randomly,
        ))
        self.modals.register(Modal(
            name="pending_choice",
            # The card picker is the VIEW of a card-kind pending choice, so
            # this entry stands down while that one is up (it is above).
            is_active=lambda: self.pending_choice is not None,
            handle=self._modal_pending_choice,
            draw=lambda: self.choice_prompt.draw(
                self.app.renderer, self.app.layout, self.app.canvas,
                self.app.mouse()),
            blocking=False, blocks_keyboard=False,
            covers=lambda mouse: self.choice_prompt.option_at(mouse) is not None
            or self.choice_prompt.confirm_hit(mouse),
            resolve=self._resolve_pending_choice_randomly,
        ))
        self.modals.register(Modal(
            name="chest_choice",
            is_active=lambda: self.chest_choice.active,
            handle=self._modal_chest_choice,
            draw=lambda: self.chest_choice.draw(
                self.app.renderer, self.cards, self.app.layout,
                self.app.canvas, self.app.mouse()),
            blocking=True, blocks_keyboard=False,
            covers=covers(lambda: self.app.layout.chest_choice_panel(
                len(self.chest_choice.cards))),
            resolve=self._resolve_chest_choice_randomly,
        ))
        self.modals.register(Modal(
            name="mod_choice",
            is_active=lambda: self.mod_choice.active,
            handle=self._modal_mod_choice,
            draw=lambda: self.mod_choice.draw(
                self.app.renderer, self.cards, self.app.layout,
                self.app.canvas, self.app.mouse()),
            # Not blocking, for the reason it never was: a hunter waiting on
            # four other votes should still be able to pan and zoom.
            blocking=False, blocks_keyboard=False,
            covers=covers(lambda: self.app.layout.mod_choice_panel(
                len(self.mod_choice.cards)) if self.mod_choice.cards else None),
            resolve=self._resolve_mod_choice_randomly,
        ))
        self.modals.register(Modal(
            name="card_picker",
            is_active=lambda: self.card_picker.active,
            handle=self._modal_card_picker,
            draw=lambda: self.card_picker.draw(
                self.app.renderer, self.cards, self.app.layout,
                self.app.canvas, self.app.mouse()),
            blocking=True, blocks_keyboard=False,
            covers=covers(lambda: self.app.layout.card_picker_panel(
                len(self.card_picker.cards)) if self.card_picker.cards else None),
            resolve=self._resolve_pending_choice_randomly,
        ))
        self.modals.register(Modal(
            name="chest_reveal",
            is_active=lambda: self.chest_reveal.active,
            handle=self._modal_chest_reveal,
            draw=lambda: self.chest_reveal.draw(
                self.app.renderer, self.app.layout, self.app.canvas,
                self.app.mouse()),
            # Informational: the table underneath stays live so the other
            # players keep going while somebody reads it.  Keyboard IS taken,
            # so Esc and Enter close it rather than opening the pause menu.
            blocking=False, blocks_keyboard=True,
            covers=covers(lambda: self.app.layout.chest_reveal_panel(
                self.chest_reveal.lines)),
            resolve=self._resolve_chest_reveal_randomly,
        ))
        self.modals.register(Modal(
            name="card_library",
            is_active=lambda: self.card_library.active,
            handle=lambda event, mouse: self.card_library.handle_event(
                event, mouse, self.app.layout),
            draw=lambda: self.card_library.draw(
                self.app.renderer, self.cards, self.app.layout,
                self.app.canvas, self.app.mouse()),
            blocking=True, blocks_keyboard=True, covers=full_screen,
            # Esc already closes it, in its own handler.  There is nothing to
            # answer at random: it is an encyclopedia, not a question.
            resolve=None,
        ))
        self.modals.register(Modal(
            name="movement_decision",
            is_active=lambda: self.movement_decision.active,
            handle=self._modal_movement_decision,
            draw=self._draw_movement_decision,
            blocking=False, blocks_keyboard=False,
            covers=lambda mouse: self.movement_decision.consumes_click(
                mouse, self.app.layout),
            resolve=self._resolve_movement_decision_randomly,
        ))
        self.modals.register(Modal(
            name="breakup_choice",
            is_active=lambda: self.breakup_choice.active,
            handle=self._modal_breakup_choice,
            draw=lambda: self.breakup_choice.draw(
                self.app.renderer, self.app.layout, self.app.canvas,
                self.app.mouse()),
            blocking=False, blocks_keyboard=False,
            covers=lambda mouse: self.breakup_choice.consumes_click(
                mouse, self.app.layout),
            resolve=self._resolve_breakup_randomly,
        ))
        self.modals.register(Modal(
            name="check_decision",
            is_active=lambda: self.check_decision.active,
            handle=self._modal_check_decision,
            draw=self._draw_check_decision,
            blocking=False, blocks_keyboard=False,
            covers=lambda mouse: self.check_decision.consumes_click(
                mouse, self.app.layout),
            resolve=self._resolve_check_decision_randomly,
        ))
        self.modals.register(Modal(
            name="match_start",
            is_active=lambda: self.match_start.active,
            handle=self._modal_match_start,
            draw=lambda: self.match_start.draw(
                self.app.renderer, self.app.layout, self.app.canvas,
                self.app.mouse()),
            blocking=True, blocks_keyboard=True, covers=full_screen,
            resolve=self._resolve_match_start_randomly,
        ))
        self.modals.register(Modal(
            name="victory",
            is_active=lambda: self.victory.active,
            handle=self._modal_victory,
            draw=lambda: self.victory.draw(
                self.app.renderer, self.app.layout, self.app.canvas,
                self.app.mouse()),
            blocking=True, blocks_keyboard=True, covers=full_screen,
            # NO RANDOM RESOLUTION.  "Quit the application" is not a choice a
            # die gets to make, and there is no game state left to unblock.
            resolve=None,
        ))
        self.modals.register(Modal(
            name="pause_menu",
            is_active=lambda: self.pause_menu.active,
            handle=self._modal_pause_menu,
            draw=lambda: self.pause_menu.draw(
                self.app.renderer, self.app.layout, self.app.canvas),
            blocking=True, blocks_keyboard=True, covers=full_screen,
            # Navigation, not a game choice — same reasoning as the ending.
            resolve=None,
        ))

    # ── modal adapters: one event in, "did I take it" out ────────────────────
    def _modal_reveal(self, event: pygame.event.Event, mouse) -> bool:
        """Presentation only: a click dismisses it, and also does its job.

        Clicking PAST the reveal dismisses it *and* performs whatever was
        clicked, so an animation never costs the player an action.
        """
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.reveal.dismiss()
            return True
        if event.type != pygame.MOUSEBUTTONDOWN:
            return False
        on_card = self.reveal.hit(self.app.layout, mouse)
        self.reveal.dismiss()
        return bool(on_card)

    def _modal_pending_choice(self, event: pygame.event.Event, mouse) -> bool:
        if self.pending_choice is None:
            return False
        self._handle_choice_event(event, mouse)
        # ``_handle_choice_event`` already forwards navigation to the board
        # itself, so anything it saw is dealt with.  Only a KEYDOWN is left for
        # the rest of the screen, which is what keeps S/F/Tab working.
        return event.type != pygame.KEYDOWN

    def _modal_chest_choice(self, event: pygame.event.Event, mouse) -> bool:
        self._handle_chest_event(event, mouse)
        return event.type != pygame.KEYDOWN

    def _modal_mod_choice(self, event: pygame.event.Event, mouse) -> bool:
        return self._handle_mod_choice_event(event, mouse)

    def _modal_card_picker(self, event: pygame.event.Event, mouse) -> bool:
        self._handle_card_picker_event(event, mouse)
        return event.type != pygame.KEYDOWN

    def _modal_chest_reveal(self, event: pygame.event.Event, mouse) -> bool:
        return self._handle_chest_reveal_event(event, mouse)

    def _modal_movement_decision(self, event: pygame.event.Event, mouse) -> bool:
        decision = self.movement_decision.handle_event(event, mouse,
                                                       self.app.layout)
        if decision is not None:
            self.submit(decision)
            return True
        return False

    def _modal_breakup_choice(self, event: pygame.event.Event, mouse) -> bool:
        pick = self.breakup_choice.handle_event(event, mouse, self.app.layout)
        if pick is not None:
            self.submit(pick)
            return True
        return False

    def _modal_check_decision(self, event: pygame.event.Event, mouse) -> bool:
        answer = self.check_decision.handle_event(event, mouse, self.app.layout)
        if answer is not None:
            self.submit(answer)
            return True
        return False

    def _modal_match_start(self, event: pygame.event.Event, mouse) -> bool:
        self._handle_identity_event(event, mouse)
        return True

    def _modal_victory(self, event: pygame.event.Event, mouse) -> bool:
        self._handle_victory_event(event, mouse)
        return True

    def _modal_pause_menu(self, event: pygame.event.Event, mouse) -> bool:
        self._handle_pause_event(event, mouse)
        return True

    def _draw_movement_decision(self) -> None:
        mouse = self.app.mouse()
        self.movement_decision.draw(self.app.renderer, self.app.layout,
                                    self.app.canvas, mouse)
        self.movement_decision.draw_confirm(self.app.renderer, self.cards,
                                            self.app.layout, self.app.canvas,
                                            mouse)

    def _draw_check_decision(self) -> None:
        mouse = self.app.mouse()
        self.check_decision.draw(self.app.renderer, self.app.layout,
                                 self.app.canvas, mouse)
        self.check_decision.draw_confirm(self.app.renderer, self.app.layout,
                                         self.app.canvas, mouse)

    # ── Esc: resolve the active window with a VALID random answer ────────────
    def _pick(self, options):
        """One of ``options`` at random, or ``None`` if there are none.

        Every caller passes the SAME set the interface would have accepted a
        click on, which is the whole rule: Esc answers the question, it does
        not guess at objects and hope the engine agrees.
        """
        options = list(options)
        if not options:
            return None
        if len(options) == 1:
            return options[0]
        return self._escape_rng.choice(options)

    def _resolve_reveal_randomly(self) -> bool:
        if not self.reveal.active:
            return False
        self.reveal.dismiss()
        return True

    def _resolve_chest_reveal_randomly(self) -> bool:
        if not self.chest_reveal.active:
            return False
        self.chest_reveal.hide()
        return True

    def _resolve_pending_choice_randomly(self) -> bool:
        """Answer the engine's question with one of the options it offered.

        The valid set is the engine's own list — ``card_options`` when the
        question is about cards, ``options`` otherwise — so this can no more
        name an illegal pawn than a click on the prompt could.
        """
        choice = self.pending_choice
        if choice is None:
            return False
        if choice.kind == "card" and choice.card_options:
            ids = [str(uid) for uid in choice.card_options]
        else:
            ids = [str(option[0]) for option in choice.options]
        if not ids:
            # Nothing legal to pick.  Backing out costs nothing and is the
            # behaviour the interface already has for an unanswerable question.
            self._cancel_choice()
            return True
        if choice.count > 1:
            wanted = min(int(choice.count), len(ids))
            picked = self._escape_rng.sample(ids, wanted)
            answer = ",".join(picked)
            self.pending_choice = None
            self._clear_choice_ui()
            self.submit(choice.resubmit(self.pending_choice_seat, answer))
            return True
        self._resolve_choice(self._pick(ids))
        return True

    def _resolve_chest_choice_randomly(self) -> bool:
        """Keep a random legal handful, discard the rest."""
        if not self.chest_choice.active:
            return False
        uids = [card.uid for card in self.chest_choice.cards]
        limit = max(0, min(int(self.chest_choice.limit), len(uids)))
        keep = tuple(self._escape_rng.sample(uids, limit)) if limit else ()
        seat = self.chest_choice_seat
        self.chest_choice.hide()
        self.submit(cmd.KeepChestCards(player_index=seat, keep_uids=keep))
        # A dealing round feeds two seats, so the next queued prompt has to be
        # let through exactly as it is when the player answers by hand.
        self._open_next_chest_choice()
        return True

    def _resolve_mod_choice_randomly(self) -> bool:
        """Pick, or vote, at random among the three that were dealt.

        Resolves ONE step: this seat's pick, or the vote of the hunter whose
        turn it is at this keyboard.  The selection phase is several decisions
        and Esc answers the one in front of the player, which is what the
        stack's "resolve the active interaction" rule means.
        """
        if not self.mod_choice.interactive:
            # A seat with nothing to decide — the read-only "waiting" view.
            # There are ZERO valid options, so Esc means what it always meant.
            return False
        uid = self._pick([card.uid for card in self.mod_choice.cards])
        if uid is None:
            return False
        return self._answer_mod_choice(uid)

    def _resolve_movement_decision_randomly(self) -> bool:
        if not self.movement_decision.active:
            return False
        seat = self.movement_decision.seat()
        options = [cmd.AcceptMovement(player_index=seat)]
        # Blocking spends the card's one use, so it is only on the menu while
        # the card still has one — the same condition the engine enforces.
        card = self.movement_decision.card
        if card is None or int(getattr(card, "uses_left", 1) or 0) > 0:
            options.append(cmd.BlockMovement(player_index=seat))
        self.movement_decision.confirming = False
        self.submit(self._pick(options))
        return True

    def _resolve_check_decision_randomly(self) -> bool:
        if not self.check_decision.active:
            return False
        seat = self.check_decision.seat()
        options = [cmd.AllowCheck(player_index=seat)]
        if self.check_decision.uses_left > 0:
            options.append(cmd.RefuseCheck(player_index=seat))
        self.check_decision.confirming = False
        self.submit(self._pick(options))
        return True

    def _resolve_breakup_randomly(self) -> bool:
        tiles = self.breakup_choice.tiles()
        if not self.breakup_choice.active or len(tiles) < 2:
            return False
        tile = self._pick(tiles)
        self.submit(cmd.ChooseBreakupTile(player_index=self.breakup_choice.seat(),
                                          tile_index=tile.index))
        return True

    def _resolve_match_start_randomly(self) -> bool:
        """Piotrek's colour, from the list the authority offered him."""
        if not self.match_start.active or not self.match_start.choosing:
            return False
        pawn = self._pick([str(p.get("id", "")) for p in self.match_start.pawns
                           if p.get("id")])
        if not pawn:
            return False
        self._choose_identity(pawn)
        return True

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
        if self.card_library.active:
            # The status bar is behind the library.  A '-' that refuses because
            # the last copies are in people's hands has to say so where the
            # player is actually looking, or it reads as a dead button.
            self.card_library.notify(event.reason)
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
        if self.chest_choice.active:
            # Two seats can overflow on the same dealing round.  The first
            # prompt stays up and the queue in the state holds the rest;
            # ``_open_next_chest_choice`` brings them up in turn as each is
            # answered.  Replacing the panel here would lose the first answer.
            return
        cards = [c for c in player.hand if c.uid in set(event.card_uids)]
        self.chest_choice_seat = event.player_index
        self.view_seat = event.player_index
        self.chest_choice.show(cards, event.limit, event.new_card_uid)

    def _on_mod_selection_started(self, event: ev.ModSelectionStarted) -> None:
        """The round paused: both factions now choose a Mod Patusa."""
        self._sync_mod_overlay()
        self.status_bar.notify("Wybór Modów Patusa", duration=4.0)

    def _mod_side_for_this_machine(self) -> Tuple[str, List[int]]:
        """Which half of the selection this seat should be looking at.

        A machine is shown whatever it can still act on: Piotrek's three cards
        while he has not picked, then the hunters' three while a seat it owns
        has not voted.  Hot-seat play controls every seat, so it walks through
        both halves in turn — which is the only way one person at one keyboard
        can answer for a whole table.

        Which side a machine sees is decided by the seat it owns and nothing
        else, so Piotrek's three cards are laid out on his machine alone,
        exactly as the chest limit only opens where the hand overflowed.
        """
        state = self.state
        selection = state.pending_mod_selection
        if selection is None:
            return ("", [])
        piotrek = selection.piotrek_seat
        if (not selection.piotrek_done and piotrek is not None
                and state.may_control(piotrek) and selection.piotrek_cards):
            return ("piotrek", [c.uid for c in selection.piotrek_cards])
        if not selection.hunters_done and selection.hunter_cards:
            mine = any(state.may_control(seat) for seat in selection.hunter_seats)
            # A seat with nothing left to decide still watches the vote, so a
            # paused table never looks like a frozen one.
            return ("hunters" if mine else "waiting",
                    [c.uid for c in selection.hunter_cards])
        return ("waiting", [c.uid for c in selection.hunter_cards])

    def _sync_mod_overlay(self) -> None:
        """Put the overlay on the right side of the selection, and refresh it.

        Driven by the state rather than by the event that got us here, so a
        client replaying several commands in one frame — or reconnecting
        mid-selection — ends up looking at the right thing.
        """
        selection = self.state.pending_mod_selection
        if selection is None:
            self.mod_choice.hide()
            return
        mode, uids = self._mod_side_for_this_machine()
        if not uids:
            self.mod_choice.hide()
            return

        pool = (selection.piotrek_cards if mode == "piotrek"
                else selection.hunter_cards)
        cards = [card for card in pool if card.uid in set(uids)]
        if not cards:
            self.mod_choice.hide()
            return

        # Rebuilt only when the side actually changes: ``show`` resets the
        # animations, and doing that on every vote would make the counters
        # jump instead of counting.
        if not self.mod_choice.active or self.mod_choice.mode != mode:
            titles = {
                "piotrek": "Twój Mod Patusa",
                "hunters": "Mod Patusa Oprawców",
                "waiting": "Mody Patusa",
            }
            captions = {
                "piotrek": "wybierasz jeden — pozostałe odpadają",
                "hunters": "głosujcie — wygrywa Mod z największą liczbą głosów",
                "waiting": "Oprawcy głosują nad swoim Modem",
            }
            self.mod_choice.show(cards, mode, titles.get(mode, "Mody Patusa"),
                                 captions.get(mode, ""))

        self.mod_choice.set_tally(selection.tally(), len(selection.votes),
                                  len(selection.hunter_seats))
        self.mod_choice.settled = (mode == "waiting")
        self._sync_my_vote()

    def _sync_my_vote(self) -> None:
        """Show the tick against the vote belonging to whoever votes next.

        Hot-seat play controls every seat, so "my vote" has to mean the seat
        the NEXT click will speak for, not simply the first controlled seat
        that has voted — otherwise the green tick would stay stuck on the first
        hunter's choice while the person at the keyboard voted for the rest.
        """
        selection = self.state.pending_mod_selection
        if selection is None or self.mod_choice.mode != "hunters":
            return
        seat = self._voting_seat()
        self.mod_choice.my_vote = (None if seat is None
                                   else selection.votes.get(seat))

    def _on_mod_vote_cast(self, event: ev.ModVoteCast) -> None:
        """A hunter voted.  Everybody's counters move, including watchers'."""
        if not self.mod_choice.active:
            return
        self.mod_choice.set_tally(dict(event.tally), event.voted, event.voters)
        self._sync_my_vote()

    def _on_mod_selection_resolved(self, event: ev.ModSelectionResolved) -> None:
        """One faction settled.  Say which card won, then move to the other."""
        side = "Piotrek" if event.faction == "piotrek" else "Oprawcy"
        because = " (remis — wygrywa pierwszy z lewej)" if event.tie_broken else ""
        self.status_bar.notify(f"{side}: {event.title}{because}", duration=5.0)
        # Left up rather than closed: the other side may still be choosing, and
        # a panel that vanished mid-selection would look like a crash.  In
        # hot-seat this is what hands the same player the hunters' vote.
        self._sync_mod_overlay()

    def _on_mod_selection_finished(self, event: ev.ModSelectionFinished) -> None:
        self.mod_choice.hide()
        self.status_bar.notify("Mody Patusa aktywne — gramy dalej", duration=4.0)
        # Anything a chosen Mod queued may open now: the phase it depended on
        # is over.  The engine emits its own deferred events after this one, so
        # this only releases a window held by the replica-side net above.
        self._release_held_chest_reveal()

    # ── the Mody Patusa that announce themselves ─────────────────────────────
    def _on_chest_revealed(self, event: ev.ChestCardsRevealed) -> None:
        """Paczka arrived: show every machine the same list.

        Not modal and not synchronised — it changes nothing, so each player
        dismisses their own copy and the table carries on underneath.
        """
        holdings = [
            ChestHoldingView(name=holding.player_name, titles=list(holding.titles))
            for holding in event.holdings
        ]
        if self.state.pending_mod_selection is not None:
            # The engine already defers this while a selection is running, so
            # reaching here means a replica replaying an old log or resyncing
            # mid-pause (L19).  Held rather than shown: a window in front of
            # four people who still have to vote is the bug, whatever produced
            # the event.
            self._held_chest_reveal = holdings
            return
        self.chest_reveal.show(holdings)

    def _release_held_chest_reveal(self) -> None:
        """Show a Paczka window that waited for the Mod selection to finish."""
        holdings, self._held_chest_reveal = self._held_chest_reveal, None
        if holdings:
            self.chest_reveal.show(holdings)

    def _on_lead_check(self, event: ev.LeadCheckAnnounced) -> None:
        """Say what the automatic check did, including when it did nothing.

        A round where two pawns are level looks exactly like a broken mod
        otherwise, so the skip is reported as plainly as the check.
        """
        if event.skipped and not event.pawn_id:
            self.status_bar.notify(
                "Squid Game: remis na czele — w tej rundzie nikt nie jest sprawdzany")
        elif event.skipped:
            self.status_bar.notify(
                f"Squid Game: {self._pawn_name(event.pawn_id)} już sprawdzony")
        else:
            self.status_bar.notify(
                f"Squid Game: sprawdzany pionek {self._pawn_name(event.pawn_id)}")

    def _on_pawn_hidden(self, event: ev.PawnHidden) -> None:
        self.status_bar.notify(
            f"Shady: pionek {self._pawn_name(event.pawn_id)} znika z mapy na rundę")
        self.board_view.forget_pawn(event.pawn_id)

    def _on_pawn_restored(self, event: ev.PawnRestored) -> None:
        where = (f" na pionek {self._pawn_name(event.onto)}" if event.onto else "")
        self.status_bar.notify(
            f"Shady: pionek {self._pawn_name(event.pawn_id)} wraca na mapę{where}")
        self.board_view.forget_pawn(event.pawn_id)

    def _pawn_name(self, pawn_id: str) -> str:
        pawn = self.state.library.pawn(pawn_id)
        return pawn.name if pawn is not None else pawn_id

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

    def _on_card_drawn(self, event: ev.CardDrawn) -> None:
        """Confirm a draw, but ONLY while the library is covering the hand.

        Every other draw in this game announces itself by the card appearing in
        the fan, and a status line for something the player can already see is
        noise.  'Dobierz kartę' is the one draw whose result is hidden behind
        the window that asked for it, so it is the one that needs saying.
        """
        if not self.card_library.active or event.player_index != self.view_seat:
            return
        # Deliberately not naming the card.  A drawn card ACTS on the way in,
        # and Troll's action is to draw a replacement — so the last CardDrawn
        # of the chain is some other card entirely, and a message naming it
        # would confidently report the wrong one.  "A card arrived" is the part
        # this message can always be sure of.
        self.card_library.notify("Dodano kartę do ręki", ok=True)

    def _on_variant_changed(self, event: ev.CardVariantChanged) -> None:
        """Say that a card is now being played the other way.

        On the STATUS BAR as well as in the library, because this changes the
        rules for the whole table and the player who did it is the only one
        with the book open.  The count of cancelled ability effects is said
        only when there were any: it is the one consequence of the switch that
        is not written on the card itself.
        """
        label = event.label or event.variant
        text = f"{event.title} — {label}"
        if event.cancelled:
            text += f" (anulowane efekty umiejętności: {event.cancelled})"
        if self.card_library.active:
            self.card_library.notify(text, ok=True)
        self.status_bar.notify(text)

    # ── Nie masz Rosji ───────────────────────────────────────────────────────
    def _on_decision_opened(self, event: ev.MovementDecisionOpened) -> None:
        """A movement is waiting.  Start DRAWING a countdown for it.

        The number on screen is a picture: the authority owns the deadline and
        sends the command that closes the window.  Every seat is told, because
        everybody at the table should see that the game has stopped and why.
        """
        self.movement_decision.on_opened(event.seconds, event.card_uid)
        owner = self.state.player(event.player_index)
        name = owner.name if owner is not None else "Gracz"
        if self.view_seat in event.blockers:
            self.status_bar.notify(
                f"„{event.title}” — możesz zablokować ten ruch")
        else:
            self.status_bar.notify(f"{name}: „{event.title}” — czekamy na decyzję")

    def _on_check_decision_opened(self, event: ev.CheckDecisionOpened) -> None:
        """A check is waiting on Piotrek.  Start DRAWING a countdown.

        The number on screen is a picture; the authority owns the deadline.
        Everybody is told, because the table has visibly stopped and the reason
        is not a secret — WHICH pawn is being checked has always been public.
        """
        self.check_decision.on_opened(event.seconds, event.pawn_id)
        pawn = self.state.library.pawn(event.pawn_id)
        name = pawn.name if pawn is not None else event.pawn_id
        if self.view_seat == event.seat:
            self.status_bar.notify(
                f"Sprawdzają {name} — możesz odmówić (Ice Block)")
        else:
            self.status_bar.notify(f"Sprawdzenie: {name} — czekamy na decyzję")

    def _on_check_allowed(self, event: ev.CheckAllowed) -> None:
        self.check_decision.on_closed()
        if event.timed_out:
            self.status_bar.notify("Czas minął — sprawdzenie dozwolone")

    def _on_check_refused(self, event: ev.CheckRefused) -> None:
        """Ice Block cancelled it.  NOTHING about the pawn is said here.

        The event carries no answer because none was computed, so there is no
        way for this to leak one even by accident.
        """
        self.check_decision.on_closed()
        pawn = self.state.library.pawn(event.pawn_id)
        name = pawn.name if pawn is not None else event.pawn_id
        self.status_bar.notify(
            f"Ice Block — sprawdzenie {name} odwołane "
            f"(pozostało: {event.uses_left})")

    def _on_tower_broke_up(self, event: ev.TowerBrokeUp) -> None:
        self.status_bar.notify("Wieża się rozpadła")

    def _on_movement_accepted(self, event: ev.MovementAccepted) -> None:
        self.movement_decision.on_closed()
        if event.timeout:
            self.status_bar.notify("Czas minął — ruch dozwolony")

    def _on_movement_blocked(self, event: ev.MovementBlocked) -> None:
        self.movement_decision.on_closed()
        blocker = self.state.player(event.blocker_index)
        name = blocker.name if blocker is not None else "Przeciwnik"
        automatic = "  (ostatnia szansa)" if event.automatic else ""
        self.status_bar.notify(
            f"{name} zablokował ruch: „{event.title}”{automatic}")

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

    def _on_player_eliminated(self, event: ev.PlayerEliminated) -> None:
        """A seat is out of the game.

        Nothing is drawn from here: the seat tiles read ``player.eliminated``
        directly, so a client that reconnected sees the same X without having
        heard the event.  This is the announcement, not the state.
        """
        player = self.state.player(event.player_index)
        if player is None:
            return
        character = player.character
        who = character.title if character is not None else player.name
        self.status_bar.notify(f"{who} odpada z gry — {event.reason}"
                               if event.reason else f"{who} odpada z gry")

    def _on_pawn_eliminated(self, event: ev.PawnEliminated) -> None:
        """A check failed.  Every notepad crosses the colour off by itself.

        Nothing is drawn from here: the panel reads
        ``state.eliminated_pawns`` directly, so a player who joined late or
        reconnected sees the same crossings without having heard the event.
        """
        pawn = self.state.library.pawn(event.pawn_id)
        name = pawn.name if pawn is not None else event.pawn_id
        colour = pawn.color if pawn is not None else self.app.renderer.theme.invalid
        self.elimination_notice.show(name, colour)
        # Kept as well as the card, not instead of it: the status bar is the
        # log of what just happened and a player who looked away still has it.
        self.status_bar.notify(f"Sprawdzono wieżę: {name} to nie Piotrek", 5.0)

    def _on_match_ended(self, event: ev.MatchEnded) -> None:
        self._clear_choice_ui()
        self.chest_choice.hide()
        self.mod_choice.hide()
        self.reveal.dismiss()
        self.elimination_notice.hide()
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
        if not state.phase.playable or state.awaiting_identity:
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

        ALTER EGO REUSES BOTH, which is why the colour just revealed is filtered
        out here rather than at the source: the online list arrives already
        shortened by the server, the hot-seat list is built below, and this is
        the one place both pass through.  Nothing shows as already-chosen during
        a swap either — the old colour is gone and the new one is not picked.
        """
        state = self.state
        forbidden = state.swap_forbidden_pawn()
        if self.service is not None:
            pawns = list(getattr(self.service, "identity_request", []) or [])
            chosen = getattr(self.service, "identity_pawn", "") or ""
        else:
            pawns = [{"id": p.id, "name": p.name, "color": list(p.color)}
                     for p in state.library.pawns]
            chosen = state.piotrek_pawn or ""
        if forbidden:
            pawns = [p for p in pawns if p.get("id") != forbidden]
            chosen = "" if chosen == forbidden else chosen
        return pawns, chosen

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
        self._choose_identity(pawn_id)

    def _choose_identity(self, pawn_id: str) -> None:
        """Commit Piotrek's colour, online or at one keyboard.

        Split out of the click handler so Esc's random fallback takes exactly
        the same road — including the Alter Ego resume branch below.
        """
        if self.service is not None:
            self.service.choose_identity(pawn_id)
            # Shown as chosen at once.  The server confirms within a frame or
            # two and the overlay is rebuilt from its answer either way.
            self.match_start.show(self.match_start.pawns, pawn_id)
            return
        # Hot-seat: this machine is its own authority, so the colour is stored
        # here and the match begins — or RESUMES — through the ordinary command
        # path.  Which of the two it is depends on whether a swap is running,
        # because Alter Ego pauses a match that has already begun and
        # ``BeginMatch`` would be refused by a phase that is already PLAYING.
        swapping = self.state.awaiting_identity
        if self.state.set_piotrek_pawn(pawn_id):
            self.submit(cmd.FinishIdentitySwap() if swapping else cmd.BeginMatch())

    def handle_event(self, event: pygame.event.Event, mouse: Tuple[int, int]) -> None:
        # 1. Renaming captures all input until confirmed or cancelled.  It is
        #    not a window — it is an editor living inside a player tile — so it
        #    sits outside the stack and above it.
        if self.rename.active:
            if event.type == pygame.MOUSEBUTTONDOWN:
                self._commit_rename()
                return
            result = self.rename.handle(event)
            if result is not None:
                self._commit_rename(result)
            return

        # 2. THE MODAL STACK.  Exactly one window is offered the event — the
        #    topmost active one — and everything below it stays pending.  This
        #    is where the round 7 conflict is resolved: the Mod Patusa
        #    selection is registered above the chest limit, so it now owns the
        #    click it was already drawn over.
        if self.modals.handle_event(event, mouse):
            return

        # 3. Keyboard, for whatever the top window did not want.  A dialog that
        #    declares ``blocks_keyboard`` never gets here, which is what stops
        #    Esc opening the pause menu on top of the Card Library.
        if event.type == pygame.KEYDOWN:
            self._handle_key(event)
            return

        # These two float over the board, so they have to be checked before the
        # board claims the click as a map drag.
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.app.layout.card_library_button.collidepoint(mouse)):
            self.card_library.open()
            return
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.app.layout.end_turn_button.collidepoint(mouse)):
            self._end_turn_click(mouse)
            return
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.view_seat != self.my_seat
                and self.app.layout.return_seat_button.collidepoint(mouse)):
            self.return_to_my_seat()
            return
        # 'Cofnij ruch' sits INSIDE the board viewport, so it belongs in this
        # group and not in the ordinary chain below: the board claims any click
        # it is offered as a map drag, and a button drawn on top of the map has
        # to be asked first or it can never be pressed.  Only while the offer
        # stands — otherwise this would swallow drags over an empty corner.
        if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.can_undo
                and self.app.layout.undo_button_rect().collidepoint(mouse)):
            self._undo_click(mouse)
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

    def _handle_chest_reveal_event(self, event: pygame.event.Event,
                                   mouse: Tuple[int, int]) -> bool:
        """Dismiss Paczka's window.  Returns whether the event was consumed."""
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE,
                                                          pygame.K_RETURN):
            self.chest_reveal.hide()
            return True
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if self.chest_reveal.ok_hit(self.app.layout, mouse):
            self.chest_reveal.hide()
            return True
        return False

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

    def _handle_mod_choice_event(self, event: pygame.event.Event,
                                 mouse: Tuple[int, int]) -> bool:
        """A click on a Mod card: Piotrek's pick, or one hunter's vote.

        Returns whether the event was consumed.  Anything that is not a click on
        a card falls through so the board can still be panned and zoomed while
        the table waits for the last vote.
        """
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if not self.mod_choice.interactive:
            return False
        uid = self.mod_choice.card_at(self.app.layout, mouse)
        if uid is None:
            return False
        return self._answer_mod_choice(uid)

    def _answer_mod_choice(self, uid: int) -> bool:
        """Turn a chosen uid into Piotrek's pick or one hunter's vote.

        Shared by the click and by Esc's random fallback, so the two cannot
        drift apart about which seat a choice speaks for.
        """
        if self.mod_choice.mode == "piotrek":
            seat = self.state.piotrek_seat
            if seat is None:
                return False
            self.submit(cmd.ChooseMod(player_index=seat, card_uid=uid))
            return True

        seat = self._voting_seat()
        if seat is None:
            self.status_bar.notify("Nie głosujesz w tym wyborze")
            return True
        # Shown immediately rather than waiting for the event: over a network
        # the tick would otherwise lag a round trip behind the click.  The
        # engine's answer replaces it a moment later either way, and in
        # hot-seat that answer moves the tick on to the next hunter.
        self.mod_choice.my_vote = uid
        self.submit(cmd.VoteMod(player_index=seat, card_uid=uid))
        self._sync_my_vote()
        return True

    def _voting_seat(self) -> Optional[int]:
        """Which hunter seat this machine votes with.

        Hot-seat play controls every seat, so the vote goes to whichever hunter
        has not voted yet — that is what lets one person work through all the
        votes at one keyboard without a seat picker.
        """
        selection = self.state.pending_mod_selection
        if selection is None:
            return None
        mine = [seat for seat in selection.hunter_seats
                if self.state.may_control(seat)]
        if not mine:
            return None
        return next((seat for seat in mine if seat not in selection.votes),
                    mine[0])

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
            # A dealing round feeds two seats, so two of them can go over the
            # limit at once.  Answering one has to let the next through, or the
            # second prompt would never be shown and that player would sit over
            # the limit with a card nobody can put back.
            self._open_next_chest_choice()
            return
        uid = self.chest_choice.card_at(layout, mouse)
        if uid is not None:
            self.chest_choice.toggle(uid)

    def _open_next_chest_choice(self) -> None:
        """Show the next queued chest limit this machine is entitled to answer.

        Read from the state rather than from the events that filled it, so a
        client replaying several commands in one frame — or reconnecting with
        prompts already queued — opens the right one.
        """
        pending = self.state.pending_chest_choice
        if pending is None:
            return
        seat, uids = pending
        if not self.state.may_control(seat):
            player = self.state.player(seat)
            if player is not None:
                self.status_bar.notify(f"{player.name} wybiera Kartę Skrzyni…")
            return
        player = self.state.player(seat)
        if player is None:
            return
        cards = [c for c in player.hand if c.uid in set(uids)]
        if not cards:
            return
        self.chest_choice_seat = seat
        self.view_seat = seat
        self.chest_choice.show(cards, self.state.chest_limit(player), None)

    @property
    def can_end_turn(self) -> bool:
        """Whether the button should be live for whoever is at this machine."""
        seat = self.state.active_player_index
        return (self.state.may_control(seat)
                and self.chest_choice.active is False
                and self.card_picker.active is False
                and self.state.pending_mod_selection is None
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

    # ── the turn window: undo ────────────────────────────────────────────────
    @property
    def undo_seat(self) -> Optional[int]:
        """The seat this machine may rewind for, or ``None``.

        NOT ``view_seat``.  The view follows the active player, so on a
        hot-seat table it moves to the NEXT player the instant a card is
        played — which is the exact moment the previous player is offered the
        undo.  Keying the button to the view made it vanish precisely when it
        should have appeared.

        The offer belongs to the window's owner, and this machine may take it
        if it may play that seat at all: one client online, everybody round one
        table hot-seat.  ``may_control`` is the same question the rest of the
        screen asks, so the two cannot drift.
        """
        window = getattr(self.state, "turn_window", None)
        if window is None:
            return None
        seat = int(window.seat)
        if not self.state.may_control(seat) or not self.state.can_undo(seat):
            return None
        return seat

    @property
    def can_undo(self) -> bool:
        """Whether the undo button should be on screen at all."""
        return self.undo_seat is not None

    def _undo_click(self, mouse: Tuple[int, int]) -> bool:
        if not self.app.layout.undo_button_rect().collidepoint(mouse):
            return False
        seat = self.undo_seat
        if seat is None:
            # Only reachable if a frame drew the button and the window closed
            # before the click landed.  The engine would refuse it anyway; this
            # says why instead of sending a command that is going to bounce.
            self.status_bar.notify("Nie można już cofnąć ruchu")
            return True
        self.submit(cmd.UndoMove(player_index=seat))
        return True

    def _draw_undo_button(self, ctx: HudContext) -> None:
        """Drawn ONLY while the offer stands, so its absence is the rule."""
        if not self.can_undo:
            return
        r, surface = ctx.r, ctx.surface
        rect = ctx.layout.undo_button_rect()
        hovered = rect.collidepoint(ctx.mouse)
        theme = ctx.theme
        style = r.emphasis(fill=theme.btn_idle_bg, border=theme.btn_idle_border,
                           text=theme.btn_text,
                           hover=1.0 if hovered else 0.0, enabled=True,
                           accent=theme.prompt)
        drawn = r.interactive_panel(rect, style, surface, radius=9)
        r.fit_spaced_text("COFNIJ RUCH", drawn, style.text, surface,
                          base_size=int(12 * ctx.layout.ui_scale), spacing=1,
                          padding=10, shadow=True)

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
        blocked = self.state.ability_refusal()
        if blocked is not None:
            # Sesja na PG, or a pawn still on START.  The engine refuses this
            # too — asking it for the reason means the interface cannot drift
            # out of step with the rule, and the player is told which of the
            # two it is rather than getting a bare rejection.
            self.status_bar.notify(blocked)
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
        # Esc on an OPEN WINDOW never reaches here: the modal stack answers it
        # first, with a valid random choice (stage 44).  What is left is Esc
        # with nothing on screen to resolve — a half-made gesture, or the way
        # out of the match.  Cancelling a pending choice is now the RIGHT
        # BUTTON, which is where it always also was.
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
            preview=self.card_preview,
            abilities=self.ability_cards,
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
            # A hot-seat game has no server, so this session is the authority
            # and owes the table the same periodic work a room does: closing a
            # Nie masz Rosji window whose time is up.  Real time, passed in, so
            # nothing here depends on the frame rate and nothing sleeps.
            self.session.tick(time.monotonic())
        self.movement_decision.update(dt, self.app.layout, mouse)
        self.check_decision.update(dt, self.app.layout, mouse)
        self.breakup_choice.update(dt, self.app.layout, mouse)
        self._sync_match_overlays()
        # Holding Backspace in the rename box only deletes continuously while
        # this is called; the field has no clock of its own.
        self.rename.update(dt)
        self.pause_menu.update(dt, mouse)
        self.card_library.update(dt, self.app.layout, mouse)
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
        self.mod_choice.update(dt, self.app.layout, mouse)
        self.card_picker.update(dt, self.app.layout, mouse)
        self.chest_reveal.update(dt)
        self.elimination_notice.update(dt)
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

    def _draw_library_button(self, ctx: HudContext) -> None:
        """The book that opens the Card Library.

        Always on screen, unlike 'Wróć do', because it is a reference the
        player reaches for at any moment rather than a state they are in.
        """
        draw_library_button(ctx.r, ctx.layout, ctx.surface, ctx.mouse,
                            open_now=self.card_library.active)

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
        # Immediately after the board, so it is an OVERLAY on it rather than a
        # control that happens to sit nearby — and before the panels, so a
        # panel edge is never painted underneath it.
        self._draw_undo_button(ctx)
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
        # The enlarged hover preview any panel asked for while drawing above:
        # above every panel, so it is never half-covered by the column it grew
        # out of, and below the dialogs and banners below, because a window the
        # player has to answer outranks something they are only reading.  This
        # call CONSUMES the request, so a frame on which nothing asked draws
        # nothing and there is no stale preview to clear.
        self.card_preview.draw(ctx)
        self._draw_turn_banner(ctx)
        self._draw_return_button(ctx)
        self._draw_end_turn_button(ctx)
        self._draw_library_button(ctx)
        # Above the board and below every dialog: it is an announcement, not a
        # question, so nothing it covers is anything the player must click.
        self.elimination_notice.draw(self.app.renderer, self.app.layout, surface)
        # EVERY interactive window, in the ONE order that also decides who
        # receives the click.  Adding a draw call here instead of registering
        # a modal is how the two orders drifted apart in the first place.
        self.modals.draw()
        # Last, and deliberately above the dialogs: "connection lost" is the
        # one message that must never be covered by a window the player can no
        # longer resolve.
        self._draw_connection_banner(ctx)
        self.debug_panel.draw(
            self.app.renderer, self.app.layout, surface,
            session=self.session, service=self.service, state=self.state,
        )
