"""
The Card Library (stage 32).

Three layers, and the split matters: the ENGINE tests below never open a
window, because deck composition and ability charges are rules and a rule that
only works when somebody is looking at it is not a rule.  The UI tests drive
the real screen with synthetic events against SDL's dummy driver, the way
``test_ui.py`` does, so a control that stops being where the hit test looks
fails here rather than in somebody's game.
"""

from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame
import pytest

from pedzacy_piotrek.cards.base_card import Card
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

WINDOW = (1920, 1080)
#: The displays the project promises to work at (LLM_Instructions, "HOW SCALING
#: WORKS").  Every geometry test below walks all five.
REFERENCE_WINDOWS = [(1280, 760), (1920, 1080), (1920, 1200),
                     (2560, 1440), (3840, 2160)]


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def game(library):
    return create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3, seed=77),
        library,
    )


@pytest.fixture
def screen(library) -> GameScreen:
    app = App(Layout(), headless=True, size=WINDOW)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3, seed=77),
        library,
    )
    game_screen = GameScreen(app, LocalSession(state))
    app.push(game_screen)
    return game_screen


def draw(screen: GameScreen, size=WINDOW) -> pygame.Surface:
    """One frame, so the interface has laid itself out before it is clicked."""
    surface = pygame.Surface(size)
    screen.app.renderer.begin(surface)
    screen.update(0.016, (0, 0))
    screen.draw(surface)
    return surface


def paint(screen: GameScreen, size=WINDOW) -> pygame.Surface:
    """A frame with NO ``update`` — the picture only.

    The table underneath keeps animating (the board's particles, the status
    bar, a fading notice), and the library's shade is translucent, so two
    frames a tick apart differ by a unit or two everywhere.  Comparing
    pictures means comparing them at the same instant.
    """
    surface = pygame.Surface(size)
    screen.app.renderer.begin(surface)
    screen.draw(surface)
    return surface


def click(screen: GameScreen, position, button: int = 1) -> None:
    position = (int(position[0]), int(position[1]))
    screen.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=position),
        position,
    )
    screen.session.poll()


def resized(library, size) -> GameScreen:
    app = App(Layout(), headless=True, size=size)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=5), library
    )
    game_screen = GameScreen(app, LocalSession(state))
    app.push(game_screen)
    return game_screen


# ══ the engine: deck composition during a match ══════════════════════════════
def test_the_library_count_is_what_the_lobby_configured(game, library):
    """The number under a card is the number the deck was built with.

    Not the draw pile: five cards of a title with two of them dealt into hands
    is still a deck of five, and a library that said three the moment somebody
    drew would be measuring something the lobby has no word for.
    """
    for deck_id in settings.TABLE_DECKS:
        printed = {card.title: card.count for card in library.deck(deck_id).cards}
        assert game.deck_composition(deck_id) == printed


def test_the_count_survives_the_cards_being_dealt_and_played(game):
    """Moving a card about does not change how many of it there are."""
    deck = game.decks[settings.DECK_MOVEMENT]
    title = deck.draw_pile[-1].title
    before = game.deck_card_count(settings.DECK_MOVEMENT, title)
    card = deck.take_card()
    game.players[0].hand.append(card)
    assert game.deck_card_count(settings.DECK_MOVEMENT, title) == before
    game.players[0].hand.remove(card)
    deck.return_card(card)
    assert game.deck_card_count(settings.DECK_MOVEMENT, title) == before


def test_plus_adds_a_copy_to_the_draw_pile(game):
    title = library_title(game, settings.DECK_MOVEMENT)
    before = game.deck_card_count(settings.DECK_MOVEMENT, title)
    draw_before = game.decks[settings.DECK_MOVEMENT].draw_count

    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title=title, delta=1))

    assert isinstance(events[0], ev.DeckCountChanged)
    assert events[0].count == before + 1
    assert game.deck_card_count(settings.DECK_MOVEMENT, title) == before + 1
    assert game.decks[settings.DECK_MOVEMENT].draw_count == draw_before + 1


def test_an_added_copy_is_never_on_top_of_the_pile(library):
    """A card conjured onto the top of the deck is a card handed to the next
    player.  It goes somewhere in the middle, and 'somewhere' is the seeded
    RNG, so every machine puts it in the same place.
    """
    tops = set()
    for seed in range(12):
        game = create_game(SessionConfig(num_players=5, board_cells=24,
                                         seed=seed), library)
        title = library_title(game, settings.DECK_MOVEMENT)
        game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                       title=title, delta=1))
        tops.add(game.decks[settings.DECK_MOVEMENT].draw_pile[-1].title)
    # If insertion were "on top", every one of these would be the same title.
    assert len(tops) > 1


def test_an_added_copy_gets_a_uid_nothing_else_has(game):
    title = library_title(game, settings.DECK_MOVEMENT)
    game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                   title=title, delta=1))
    uids = [card.uid for card in game.cards_of_deck(settings.DECK_MOVEMENT)]
    assert len(uids) == len(set(uids))


def test_minus_takes_a_copy_off_a_pile(game):
    title = library_title(game, settings.DECK_MOVEMENT)
    before = game.deck_card_count(settings.DECK_MOVEMENT, title)

    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title=title, delta=-1))

    assert isinstance(events[0], ev.DeckCountChanged)
    assert game.deck_card_count(settings.DECK_MOVEMENT, title) == before - 1


def test_minus_never_takes_a_card_out_of_a_hand(game):
    """THE safety rule of active-game editing.

    Every copy of the title is put into a hand; the only honest answer is to
    refuse, because the alternative is deleting a card somebody is holding.
    """
    deck = game.decks[settings.DECK_MOVEMENT]
    title = deck.draw_pile[-1].title
    for card in list(deck.draw_pile) + list(deck.discard_pile):
        if card.title == title:
            if card in deck.draw_pile:
                deck.draw_pile.remove(card)
            else:
                deck.discard_pile.remove(card)
            game.players[0].hand.append(card)
    held = len(game.players[0].hand)

    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title=title, delta=-1))

    assert isinstance(events[0], ev.ActionRejected)
    assert len(game.players[0].hand) == held
    assert game.deck_card_count(settings.DECK_MOVEMENT, title) > 0


def test_minus_falls_back_to_the_discard_pile(game):
    """A pile is a pile.  With the draw pile empty of a title, its discarded
    copies are still the deck's own cards and may be taken back.
    """
    deck = game.decks[settings.DECK_MOVEMENT]
    title = deck.draw_pile[-1].title
    moved = [c for c in list(deck.draw_pile) if c.title == title]
    for card in moved:
        deck.draw_pile.remove(card)
        deck.discard_pile.append(card)
    discarded = deck.discard_count

    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title=title, delta=-1))

    assert isinstance(events[0], ev.DeckCountChanged)
    assert events[0].where == "discard"
    assert deck.discard_count == discarded - 1


def test_the_lobby_bounds_still_apply(game):
    """The library is not a way round the validation the lobby enforces."""
    title = library_title(game, settings.DECK_CHEST)
    for _ in range(RULES.card_count_max + 4):
        game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_CHEST,
                                       title=title, delta=1))
    assert (game.deck_card_count(settings.DECK_CHEST, title)
            <= RULES.card_count_max)

    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_CHEST,
                                            title=title, delta=1))
    assert isinstance(events[0], ev.ActionRejected)


def test_the_mod_deck_keeps_its_own_bound(game):
    title = library_title(game, settings.DECK_MODS)
    for _ in range(RULES.mod_count_max + 4):
        game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MODS,
                                       title=title, delta=1))
    assert game.deck_card_count(settings.DECK_MODS, title) <= RULES.mod_count_max


def test_only_the_three_table_decks_may_be_edited(game):
    """The character deck is not a deck whose composition anybody sets."""
    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_CHARACTERS,
                                            title="Lubin", delta=1))
    assert isinstance(events[0], ev.ActionRejected)


def test_an_unknown_title_changes_nothing(game):
    events = game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                            title="Nie ma takiej karty",
                                            delta=1))
    assert isinstance(events[0], ev.ActionRejected)


def test_editing_a_deck_leaves_the_discard_pile_alone(game):
    """Cards already played stay played."""
    deck = game.decks[settings.DECK_MOVEMENT]
    for _ in range(4):
        deck.discard_pile.append(deck.take_card())
    discarded = [card.uid for card in deck.discard_pile]
    title = library_title(game, settings.DECK_MOVEMENT)

    game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT,
                                   title=title, delta=1))

    assert [card.uid for card in deck.discard_pile] == discarded


def library_title(game, deck_id: str) -> str:
    """A title with copies in the draw pile, whichever deck is asked about."""
    deck = game.decks[deck_id]
    return deck.draw_pile[-1].title


# ══ the engine: ability defaults and what is left of them ════════════════════
def ability_titles(library) -> list:
    return [definition.title
            for deck_id in (settings.DECK_CHARACTERS, settings.DECK_SKILLS)
            for definition in library.deck(deck_id).cards
            if definition.ability is not None and definition.uses is not None]


def test_every_ability_is_findable_and_knows_its_default(game, library):
    for title in ability_titles(library):
        card = game.ability_card(title)
        assert card is not None, title
        assert game.ability_default_uses(title) == card.definition.uses


def test_the_default_comes_from_the_lobby_not_from_the_json(library):
    """``ability_uses`` from the settings panel IS the default the library
    restores to, because ``with_uses`` rewrote the definition before the deck
    was built.  Nothing in the library keeps a second copy of it.
    """
    game = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=3,
                      ability_uses={"ChatGPT": 7}),
        library,
    )
    assert game.ability_default_uses("ChatGPT") == 7


def test_minus_cannot_go_below_zero(game, library):
    title = ability_titles(library)[0]
    for _ in range(12):
        game.apply(cmd.AdjustAbilityUses(title=title, delta=-1))
    assert game.ability_card(title).uses_left == 0

    events = game.apply(cmd.AdjustAbilityUses(title=title, delta=-1))
    assert isinstance(events[0], ev.ActionRejected)
    assert game.ability_card(title).uses_left == 0


def test_plus_may_exceed_the_default(game, library):
    """Deliberately unbounded: the table may hand an ability more charges than
    it was printed with, and that is the whole reason ``+`` has no ceiling.
    """
    title = ability_titles(library)[0]
    default = game.ability_default_uses(title)
    for _ in range(5):
        game.apply(cmd.AdjustAbilityUses(title=title, delta=1))
    assert game.ability_card(title).uses_left == default + 5


def test_changing_the_current_uses_never_moves_the_default(game, library):
    title = ability_titles(library)[0]
    default = game.ability_default_uses(title)
    for _ in range(3):
        game.apply(cmd.AdjustAbilityUses(title=title, delta=1))
    assert game.ability_card(title).definition.uses == default
    assert game.ability_default_uses(title) == default


@pytest.mark.parametrize("spent", [0, 1, 99])
def test_restore_puts_the_current_uses_back_to_the_default(game, library, spent):
    """From partial use, from zero, and from more than the default."""
    title = ability_titles(library)[0]
    default = game.ability_default_uses(title)
    card = game.ability_card(title)
    card.uses_left = max(0, default + 3) if spent == 99 else max(0, default - spent)
    if card.uses_left == default:
        card.uses_left = default + 2

    events = game.apply(cmd.RestoreAbilityUses(title=title))

    assert isinstance(events[0], ev.AbilityUsesChanged)
    assert events[0].restored is True
    assert card.uses_left == default
    assert card.definition.uses == default


def test_restore_does_not_permanently_change_the_default(game, library):
    """Three above the default, restored, is the default — not three."""
    title = ability_titles(library)[0]
    default = game.ability_default_uses(title)
    for _ in range(3):
        game.apply(cmd.AdjustAbilityUses(title=title, delta=1))
    game.apply(cmd.RestoreAbilityUses(title=title))
    assert game.ability_card(title).uses_left == default
    assert game.ability_default_uses(title) == default


def test_restore_works_on_an_ability_nobody_is_playing(game, library):
    """The counter belongs to the card, and most cards are still in the deck."""
    dealt = {p.character.title for p in game.players if p.character}
    dealt |= {p.skill.title for p in game.players if p.skill}
    undealt = [t for t in ability_titles(library) if t not in dealt]
    assert undealt, "the fixture is meant to leave abilities undealt"
    title = undealt[0]
    game.apply(cmd.AdjustAbilityUses(title=title, delta=-1))
    game.apply(cmd.RestoreAbilityUses(title=title))
    assert game.ability_card(title).uses_left == game.ability_default_uses(title)


def test_spending_an_ability_in_play_is_visible_to_the_library(game, library):
    """The library reads the same counter the ability button spends."""
    player = next(p for p in game.players if p.character is not None
                  and p.character.has_ability)
    card = player.character
    before = card.uses_left
    card.spend_use()
    assert game.ability_card(card.title).uses_left == before - 1
    game.apply(cmd.RestoreAbilityUses(title=card.title))
    assert card.uses_left == before


def test_an_unknown_ability_changes_nothing(game):
    events = game.apply(cmd.RestoreAbilityUses(title="Nie ma takiej"))
    assert isinstance(events[0], ev.ActionRejected)


# ══ commands, authority and the snapshot ═════════════════════════════════════
def test_the_new_commands_survive_the_wire(game):
    for command in (
        cmd.AdjustDeckCount(deck_id=settings.DECK_MODS, title="AKO", delta=1),
        cmd.AdjustAbilityUses(title="Lubin", delta=-1),
        cmd.RestoreAbilityUses(title="Lubin"),
    ):
        assert cmd.Command.from_dict(command.to_dict()) == command


def test_the_library_actions_are_not_bound_to_a_turn(library):
    """Any player, at any time.  Restoring somebody else's ability is somebody
    reaching over and resetting a counter, not that character acting.
    """
    game = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=77,
                      edit_mode=False, local_seat=0),
        library,
    )
    game.active_player_index = 1
    title = ability_titles(library)[0]

    events = game.apply(cmd.AdjustAbilityUses(title=title, delta=1))
    assert not any(isinstance(e, ev.ActionRejected) for e in events)

    events = game.apply(cmd.RestoreAbilityUses(title=title))
    assert not any(isinstance(e, ev.ActionRejected) for e in events)


def test_a_remote_player_may_restore_another_characters_ability(library):
    """What the server runs for an incoming command.  It must not care which
    seat sent it, or the ability could only ever be restored by its owner.
    """
    game = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=77, edit_mode=False),
        library,
    )
    title = ability_titles(library)[0]
    for seat in range(len(game.players)):
        assert game.authorise_remote(cmd.RestoreAbilityUses(title=title),
                                     seat) is None
        assert game.authorise_remote(
            cmd.AdjustAbilityUses(title=title, delta=1), seat) is None
        assert game.authorise_remote(
            cmd.AdjustDeckCount(deck_id=settings.DECK_MODS, title="AKO",
                                delta=1), seat) is None


def test_both_numbers_are_in_the_fingerprint(game, library):
    """A change nothing in the snapshot notices is a desync nobody detects."""
    before = game.snapshot()
    game.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_MODS,
                                   title=library_title(game, settings.DECK_MODS),
                                   delta=1))
    assert game.snapshot() != before

    before = game.snapshot()
    game.apply(cmd.AdjustAbilityUses(title=ability_titles(library)[0], delta=1))
    assert game.snapshot() != before


def test_two_machines_applying_the_same_commands_agree(library):
    """The replication contract: same seed, same commands, same table.

    Both the inserted card's position and its uid have to come out the same on
    every machine, and this is what says so.
    """
    config = SessionConfig(num_players=5, board_cells=24, seed=4242)
    one, two = create_game(config, library), create_game(config, library)
    title = library_title(one, settings.DECK_MOVEMENT)
    batch = [
        cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT, title=title, delta=1),
        cmd.AdjustDeckCount(deck_id=settings.DECK_MOVEMENT, title=title, delta=1),
        cmd.AdjustAbilityUses(title=ability_titles(library)[0], delta=1),
        cmd.RestoreAbilityUses(title=ability_titles(library)[1]),
    ]
    for command in batch:
        one.apply(command)
        two.apply(command)

    assert one.snapshot() == two.snapshot()
    assert [c.uid for c in one.decks[settings.DECK_MOVEMENT].draw_pile] == \
           [c.uid for c in two.decks[settings.DECK_MOVEMENT].draw_pile]


# ══ the library overlay: opening, closing, categories ════════════════════════
def test_the_book_button_opens_the_library(screen):
    draw(screen)
    assert not screen.card_library.active
    click(screen, screen.app.layout.card_library_button.center)
    assert screen.card_library.active


def test_the_book_button_is_in_the_bottom_right_and_clear_of_the_others(screen):
    layout = screen.app.layout
    button = layout.card_library_button
    board = layout.board_viewport
    assert button.centerx > board.centerx
    assert button.centery > board.centery
    assert not button.colliderect(layout.end_turn_button)
    assert not button.colliderect(layout.status_bar)


def test_all_four_categories_are_there(screen):
    ids = [tab.id for tab in screen.card_library.tabs]
    assert ids == [settings.DECK_MOVEMENT, settings.DECK_MODS,
                   settings.DECK_CHEST, "abilities"]


def test_each_category_holds_the_right_cards(screen, library):
    shelf = screen.card_library
    for deck_id in settings.TABLE_DECKS:
        shelf.select_tab(deck_id)
        assert [e.title for e in shelf.entries] == \
               [c.title for c in library.deck(deck_id).cards]
    shelf.select_tab("abilities")
    assert [e.title for e in shelf.entries] == ability_titles(library)


def test_the_order_is_the_data_files_order_and_does_not_wander(screen):
    shelf = screen.card_library
    shelf.open()
    first = [e.title for e in shelf.entries]
    shelf.select_tab("abilities")
    shelf.select_tab(settings.DECK_MOVEMENT)
    shelf.close()
    shelf.open()
    assert [e.title for e in shelf.entries] == first


def test_clicking_a_tab_selects_it(screen):
    draw(screen)
    click(screen, screen.app.layout.card_library_button.center)
    draw(screen)
    shelf = screen.card_library
    click(screen, shelf.tab_rects[3].center)
    assert shelf.tab.id == "abilities"


def test_the_library_closes(screen):
    draw(screen)
    click(screen, screen.app.layout.card_library_button.center)
    draw(screen)
    click(screen, screen.card_library.close_button.rect.center)
    assert not screen.card_library.active


def test_escape_closes_the_library_rather_than_opening_the_pause_menu(screen):
    draw(screen)
    screen.card_library.open()
    screen.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE,
                                           mod=0, unicode=""), (0, 0))
    assert not screen.card_library.active
    assert not screen.pause_menu.active


def test_the_library_blocks_the_game_underneath(screen):
    """Modal in the sense the settings panel is: everything is consumed,
    including clicks outside the panel, so nothing reaches a live table.
    """
    draw(screen)
    screen.card_library.open()
    draw(screen)
    hand_before = [c.uid for c in screen.state.player(screen.view_seat).hand]
    seat_before = screen.state.active_player_index
    for target in (screen.app.layout.hand_area.center,
                   screen.app.layout.board_viewport.center,
                   screen.app.layout.end_turn_button.center):
        click(screen, target)
    assert [c.uid for c in screen.state.player(screen.view_seat).hand] == hand_before
    assert screen.state.active_player_index == seat_before


def test_a_click_outside_the_panel_closes_it_and_does_nothing_else(screen):
    draw(screen)
    screen.card_library.open()
    draw(screen)
    before = screen.state.snapshot()
    click(screen, (4, 4))
    assert not screen.card_library.active
    assert screen.state.snapshot() == before


# ══ the library overlay: the cards are the game's cards ══════════════════════
def test_the_library_uses_the_existing_card_renderer(screen, monkeypatch):
    """Not "looks like it does" — the actual calls are counted.

    A second renderer written for the library is the failure this is here to
    catch: it would drift from the real card face at the first change to
    either.
    """
    draw(screen)
    screen.card_library.open()
    seen = []
    original = screen.cards.draw_in

    def spy(card, rect, surface=None, **kwargs):
        seen.append((card, kwargs))
        return original(card, rect, surface, **kwargs)

    monkeypatch.setattr(screen.cards, "draw_in", spy)
    draw(screen)

    shown = {id(entry.card) for entry in screen.card_library.entries}
    library_calls = [kwargs for card, kwargs in seen if id(card) in shown]
    assert library_calls, "the library drew no cards through CardRenderer.draw_in"
    # ``reveal`` is what makes a Signature card open on hover; a library that
    # painted its own faces would have no reason to pass it.
    assert all("reveal" in kwargs for kwargs in library_calls)


def test_a_signature_card_is_drawn_from_its_artwork(screen, library):
    """Whatever the owner has illustrated, the library shows illustrated.

    Built rather than looked up: the card_art folder is filled in by hand over
    months and a test that needs a particular file is a test that breaks when
    somebody renames one.
    """
    shelf = screen.card_library
    shelf.open()
    art = screen.cards.art
    illustrated = [e for e in shelf.entries
                   if art.surface(e.card.definition) is not None]
    if not illustrated:
        pytest.skip("no card artwork installed in this checkout")
    entry = illustrated[0]
    resting = screen.cards.face(entry.card, (140, 200), reveal=0.0)
    opened = screen.cards.face(entry.card, (140, 200), reveal=1.0)
    # The reveal is the hover behaviour stage 30 built; the two states differ.
    assert resting.get_buffer().raw != opened.get_buffer().raw


def test_hovering_a_card_reveals_it_on_the_existing_curve(screen):
    """The hover is a FLOAT that climbs, exactly as the hand fan's does, so a
    Signature card glides rather than snapping between two faces.
    """
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    draw(screen)
    layout = screen.app.layout
    entry = shelf.entries[0]
    centre = shelf.card_rect(0, layout).center
    levels = []
    for _ in range(6):
        screen.update(0.016, centre)
        levels.append(shelf.hover_of(entry))
    assert levels[-1] > levels[0] > 0.0
    assert levels[-1] <= 1.0
    # ...and falls again when the cursor leaves.
    for _ in range(20):
        screen.update(0.016, (0, 0))
    assert shelf.hover_of(entry) < 0.05


def test_nothing_is_drawn_over_a_card_face(screen):
    """Stage 31's rule, held to in a new screen.

    The library's own furniture — the count, the steppers, the owner's name and
    the restore button — is all OUTSIDE the card's rectangle.  This is the
    check that a later "just a little quantity badge in the corner" fails.
    """
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    draw(screen)
    layout = screen.app.layout
    for tab in shelf.tabs:
        shelf.select_tab(tab.id)
        draw(screen)
        for index in shelf._visible_indices(layout):
            card = shelf.card_rect(index, layout)
            for name, box in shelf._rows_under_card(index, layout).items():
                assert not card.colliderect(box), (tab.id, name, index)


# ══ the grid: columns, scrolling, clipping ═══════════════════════════════════
@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_grid_is_four_cards_wide(library, size):
    """Four across at every reference resolution (stage 33).

    Was three-or-four; the owner wanted more of the collection on screen at
    once, so the card shrank until four fit everywhere.
    """
    assert Layout(*size).card_library_columns == 4


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_a_library_card_is_a_readable_size(library, size):
    """Bigger than a hand card at the same window, or the library is a
    thumbnail gallery rather than the place cards are read.
    """
    layout = Layout(*size)
    width, height = layout.card_library_card_size
    assert width >= layout.hand_card_size[0] * 0.9
    assert height > width


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_nothing_overlaps_and_nothing_escapes_at_any_resolution(library, size):
    screen = resized(library, size)
    surface = pygame.Surface(size)
    screen.app.renderer.begin(surface)
    layout = screen.app.layout
    shelf = screen.card_library
    shelf.open()
    window = surface.get_rect()
    panel = layout.card_library_panel
    content = layout.card_library_content

    assert window.contains(panel)
    assert panel.contains(content)
    assert panel.contains(layout.card_library_close_rect())

    for tab in shelf.tabs:
        shelf.select_tab(tab.id)
        for scroll in (0, shelf.max_scroll(layout) // 2,
                       shelf.max_scroll(layout)):
            shelf.scroll = scroll
            screen.update(0.016, (0, 0))
            screen.draw(surface)
            for rect in shelf.tab_rects:
                assert panel.contains(rect)
                assert rect.bottom <= content.top
            cells = [shelf.cell_rect(i, layout)
                     for i in range(len(shelf.entries))]
            for first in range(len(cells)):
                for second in range(first + 1, len(cells)):
                    assert not cells[first].colliderect(cells[second])
            for index in shelf._visible_indices(layout):
                cell = cells[index]
                assert cell.contains(shelf.card_rect(index, layout))
                for name, box in shelf._rows_under_card(index, layout).items():
                    assert cell.left - 1 <= box.left and box.right <= cell.right + 1
                    assert cell.top - 1 <= box.top and box.bottom <= cell.bottom + 1
                    assert content.left <= box.left and box.right <= content.right
                stepper = shelf._stepper(
                    shelf._rows_under_card(index, layout)["stepper"], layout)
                band = shelf._rows_under_card(index, layout)["stepper"]
                for rect in stepper.rects.values():
                    assert content.left <= rect.left and rect.right <= content.right
                    assert band.top - 1 <= rect.top
                    assert rect.bottom <= band.bottom + 1


def test_the_content_scrolls_and_stops_at_both_ends(screen):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_MOVEMENT)
    draw(screen)
    layout = screen.app.layout
    assert shelf.max_scroll(layout) > 0, "thirty cards should not fit at once"

    for _ in range(40):
        screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1),
                            layout.card_library_content.center)
    assert shelf.scroll == shelf.max_scroll(layout)

    for _ in range(80):
        screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1),
                            layout.card_library_content.center)
    assert shelf.scroll == 0


def test_a_short_category_does_not_scroll(screen):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_MODS)
    draw(screen)
    layout = screen.app.layout
    if shelf.max_scroll(layout) == 0:
        screen.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3),
                            layout.card_library_content.center)
        assert shelf.scroll == 0


def test_the_grid_paints_nothing_outside_its_viewport(screen, monkeypatch):
    """Clipping, checked in PIXELS rather than in arithmetic.

    The frame is drawn twice — once whole, once with the grid suppressed — and
    every pixel that differs has to be inside the content rect.  A row scrolled
    half in is the case that matters: it is always half outside the viewport,
    and without a clip it paints over the tab strip.
    """
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_MOVEMENT)
    layout = screen.app.layout
    draw(screen)
    shelf.scroll = max(1, shelf.max_scroll(layout) // 3)

    # The opening fade is an animation; pin it, and paint both frames at the
    # same instant, or they differ by a step of everything else on the table.
    shelf.appear = 1.0
    whole = paint(screen)
    monkeypatch.setattr(shelf, "_draw_grid",
                        lambda *args, **kwargs: None)
    bare = paint(screen)

    content = layout.card_library_content
    for x in range(0, screen.app.layout.win_w, 9):
        for y in range(0, screen.app.layout.win_h, 9):
            if content.collidepoint((x, y)):
                continue
            assert whole.get_at((x, y)) == bare.get_at((x, y)), (x, y)


def test_a_click_below_the_viewport_reaches_nothing(screen):
    """A card scrolled out of sight is out of reach, not merely invisible."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    draw(screen)
    layout = screen.app.layout
    before = screen.state.snapshot()
    click(screen, (layout.card_library_content.centerx,
                   layout.card_library_content.bottom + 6))
    assert screen.state.snapshot() == before


# ══ the controls, clicked for real ═══════════════════════════════════════════
def stepper_of(screen, index: int):
    layout = screen.app.layout
    boxes = screen.card_library._rows_under_card(index, layout)
    return screen.card_library._stepper(boxes["stepper"], layout), boxes


@pytest.mark.parametrize("deck_id", list(settings.TABLE_DECKS))
def test_the_quantity_controls_change_the_real_deck(screen, deck_id):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(deck_id)
    draw(screen)
    entry = shelf.entries[0]
    before = shelf.count_of(entry)

    stepper, _ = stepper_of(screen, 0)
    click(screen, stepper.rects["plus1"].center)
    assert shelf.count_of(entry) == before + 1
    assert screen.state.deck_card_count(deck_id, entry.title) == before + 1

    click(screen, stepper.rects["minus1"].center)
    assert shelf.count_of(entry) == before


def test_the_displayed_quantity_is_the_games_own_number(screen):
    """One source of truth: the library keeps no count of its own, so a change
    made anywhere else shows up in it without being told.
    """
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_CHEST)
    entry = shelf.entries[0]
    before = shelf.count_of(entry)
    screen.state.apply(cmd.AdjustDeckCount(deck_id=settings.DECK_CHEST,
                                           title=entry.title, delta=1))
    assert shelf.count_of(entry) == before + 1


def test_the_quantity_controls_go_through_a_command(screen, monkeypatch):
    """R2 and R6: the interface builds a Command, it does not touch state."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    draw(screen)
    sent = []
    monkeypatch.setattr(screen, "submit", sent.append)
    shelf.submit = screen.submit
    stepper, _ = stepper_of(screen, 0)
    click(screen, stepper.rects["plus1"].center)
    assert len(sent) == 1
    assert isinstance(sent[0], cmd.AdjustDeckCount)
    assert sent[0].delta == 1


def test_the_library_shows_the_character_that_owns_each_ability(screen, library):
    """Read from the data, not from a table in the interface."""
    shelf = screen.card_library
    shelf.select_tab("abilities")
    piotrek = next(c.title for c in library.deck(settings.DECK_CHARACTERS).cards
                   if c.is_piotrek)
    characters = {c.title for c in library.deck(settings.DECK_CHARACTERS).cards}
    skills = {c.title for c in library.deck(settings.DECK_SKILLS).cards}
    for entry in shelf.entries:
        assert entry.owner
        if entry.title in characters:
            assert entry.owner == entry.title
        elif entry.title in skills:
            assert entry.owner == piotrek


def test_the_ability_entries_carry_the_printed_ability_name(screen, library):
    shelf = screen.card_library
    shelf.select_tab("abilities")
    lookup = {c.title: c for deck in (settings.DECK_CHARACTERS,
                                      settings.DECK_SKILLS)
              for c in library.deck(deck).cards}
    for entry in shelf.entries:
        definition = lookup[entry.title]
        assert entry.ability == (definition.skill or definition.title)


def test_the_ability_controls_move_the_current_uses_only(screen, library):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab("abilities")
    draw(screen)
    entry = shelf.entries[0]
    left, default = shelf.uses_of(entry)

    stepper, _ = stepper_of(screen, 0)
    click(screen, stepper.rects["plus1"].center)
    assert shelf.uses_of(entry) == (left + 1, default)

    click(screen, stepper.rects["minus1"].center)
    assert shelf.uses_of(entry) == (left, default)


def test_the_restore_button_restores_and_leaves_the_default(screen):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab("abilities")
    draw(screen)
    entry = shelf.entries[0]
    _, default = shelf.uses_of(entry)

    stepper, boxes = stepper_of(screen, 0)
    for _ in range(3):
        click(screen, stepper.rects["plus1"].center)
    assert shelf.uses_of(entry)[0] == default + 3

    click(screen, boxes["restore"].center)
    assert shelf.uses_of(entry) == (default, default)


def test_the_ability_minus_stops_at_zero_in_the_interface(screen):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab("abilities")
    draw(screen)
    entry = shelf.entries[0]
    stepper, _ = stepper_of(screen, 0)
    for _ in range(12):
        click(screen, stepper.rects["minus1"].center)
    assert shelf.uses_of(entry)[0] == 0


def test_a_refusal_is_shown_where_the_player_is_looking(screen):
    """The status bar is behind the overlay; a dead-looking button is worse
    than a refusal nobody reads.
    """
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab("abilities")
    draw(screen)
    stepper, _ = stepper_of(screen, 0)
    for _ in range(12):
        click(screen, stepper.rects["minus1"].center)
    assert shelf.notice
    assert shelf.notice_left > 0.0


def test_the_abilities_tab_has_no_deck_quantity_controls(screen):
    """An ability is not a deck.  ``count_of`` would be meaningless here, and
    the tab that would call it is the one that does not.
    """
    shelf = screen.card_library
    shelf.select_tab("abilities")
    assert shelf.tab.abilities is True
    assert shelf.tab.deck_id == ""


def test_the_deck_tabs_have_no_restore_button(screen):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    layout = screen.app.layout
    for deck_id in settings.TABLE_DECKS:
        shelf.select_tab(deck_id)
        draw(screen)
        for index in shelf._visible_indices(layout):
            assert "restore" not in shelf._rows_under_card(index, layout)


# ══ stage 33: four columns, and 'Dobierz kartę' ══════════════════════════════
@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_cards_are_smaller_than_the_three_column_layout_was(library, size):
    """The point of the retune: more of the collection visible at once.

    Stage 32 sized the card at ~0.6 of the viewport's height, which is what
    left room for only three.  The check is against that share rather than
    against remembered pixel values, so it still means something if the panel's
    chrome changes.
    """
    layout = Layout(*size)
    _, card_h = layout.card_library_card_size
    assert card_h < layout.card_library_content_h * 0.55
    # ...and a row still costs less than the viewport, or nothing would scroll
    # into view properly.
    _, cell_h = layout.card_library_cell_size(False)
    assert cell_h < layout.card_library_content.height


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_four_cards_and_their_controls_fit_across(library, size):
    """Four columns is only four columns if four actually fit side by side."""
    layout = Layout(*size)
    cell_w, _ = layout.card_library_cell_size(False)
    assert 4 * cell_w <= layout.card_library_content.width + 1


def test_a_narrow_window_gets_fewer_columns_rather_than_overlap(library):
    """"Do not blindly force four": the count is still measured.

    A window too narrow for four takes three — it does not squeeze four in on
    top of one another.
    """
    layout = Layout(560, 900)
    columns = layout.card_library_columns
    cell_w, _ = layout.card_library_cell_size(False)
    assert 1 <= columns <= 4
    assert columns * cell_w <= layout.card_library_allowance + layout.card_library_gap


@pytest.mark.parametrize("deck_id", list(settings.TABLE_DECKS))
def test_every_deck_card_has_a_draw_button(screen, deck_id):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(deck_id)
    draw(screen)
    layout = screen.app.layout
    visible = shelf._visible_indices(layout)
    assert visible
    for index in visible:
        assert "draw" in shelf._rows_under_card(index, layout)


def test_abilities_have_no_draw_button(screen):
    """An ability is not drawn from a deck, so it gets no button that says so."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab("abilities")
    draw(screen)
    layout = screen.app.layout
    for index in shelf._visible_indices(layout):
        boxes = shelf._rows_under_card(index, layout)
        assert "draw" not in boxes
        # ...and everything it should still have is still there.
        assert {"owner", "restore", "label", "stepper"} <= set(boxes)


def test_the_draw_button_is_outside_the_card_face(screen):
    """Stage 31 again: the new control goes UNDER the card, never over it."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    layout = screen.app.layout
    for deck_id in settings.TABLE_DECKS:
        shelf.select_tab(deck_id)
        draw(screen)
        for index in shelf._visible_indices(layout):
            card = shelf.card_rect(index, layout)
            box = shelf._rows_under_card(index, layout)["draw"]
            assert not card.colliderect(box)
            assert box.top >= card.bottom


def test_one_cards_draw_button_never_touches_another_cards(screen):
    """Four columns puts the neighbours close; the button must stay in its cell."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    layout = screen.app.layout
    for deck_id in settings.TABLE_DECKS:
        shelf.select_tab(deck_id)
        draw(screen)
        visible = shelf._visible_indices(layout)
        boxes = {i: shelf._rows_under_card(i, layout)["draw"] for i in visible}
        for i in visible:
            assert shelf.cell_rect(i, layout).contains(boxes[i])
            for j in visible:
                if i != j:
                    assert not boxes[i].colliderect(boxes[j])


@pytest.mark.parametrize("deck_id", list(settings.TABLE_DECKS))
def test_clicking_dobierz_karte_puts_that_card_in_the_hand(screen, deck_id):
    """The whole feature, clicked for real, once per deck."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(deck_id)
    draw(screen)
    layout = screen.app.layout
    seat = screen.view_seat
    index = 1
    entry = shelf.entries[index]
    before = [c.uid for c in screen.state.player(seat).hand]
    deck_before = screen.state.decks[deck_id].draw_count

    click(screen, shelf._rows_under_card(index, layout)["draw"].center)

    arrived = [c for c in screen.state.player(seat).hand if c.uid not in before]
    assert [c.title for c in arrived] == [entry.title]
    assert arrived[0].deck_id == deck_id
    assert screen.state.decks[deck_id].draw_count == deck_before - 1


def test_the_draw_button_sends_a_command_rather_than_touching_the_hand(screen,
                                                                      monkeypatch):
    """R2 and R6 again: the interface asks, the engine decides."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    draw(screen)
    sent = []
    monkeypatch.setattr(screen, "submit", sent.append)
    shelf.submit = screen.submit
    layout = screen.app.layout
    entry = shelf.entries[0]

    click(screen, shelf._rows_under_card(0, layout)["draw"].center)

    assert len(sent) == 1
    assert isinstance(sent[0], cmd.DrawTitledCard)
    assert sent[0].title == entry.title
    assert sent[0].deck_id == shelf.tab.deck_id
    assert sent[0].player_index == screen.view_seat


def test_the_button_still_works_when_the_grid_is_scrolled(screen):
    """The button moves with its card, and the hit test moves with both."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_MOVEMENT)
    draw(screen)
    layout = screen.app.layout
    shelf.scroll = max(1, shelf.max_scroll(layout) // 3)
    draw(screen)

    index = next(i for i in shelf._visible_indices(layout)
                 if layout.card_library_content.contains(
                     shelf._rows_under_card(i, layout)["draw"]))
    entry = shelf.entries[index]
    seat = screen.view_seat
    before = [c.uid for c in screen.state.player(seat).hand]

    click(screen, shelf._rows_under_card(index, layout)["draw"].center)

    arrived = [c for c in screen.state.player(seat).hand if c.uid not in before]
    assert [c.title for c in arrived] == [entry.title]


def test_hovering_stays_up_while_the_cursor_walks_to_the_button(screen):
    """No flicker on the way down.

    The reveal follows the CELL, so a description opened by hovering the card
    is still open when the cursor reaches the button under it — which is where
    the player is going next.
    """
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    draw(screen)
    layout = screen.app.layout
    entry = shelf.entries[0]
    for _ in range(20):
        screen.update(0.016, shelf.card_rect(0, layout).center)
    opened = shelf.hover_of(entry)
    assert opened > 0.8

    button = shelf._rows_under_card(0, layout)["draw"].center
    for _ in range(20):
        screen.update(0.016, button)
    assert shelf.hover_of(entry) >= opened * 0.95


# ── the engine side of 'Dobierz kartę' ───────────────────────────────────────
def test_a_titled_draw_takes_the_named_card_from_the_middle_of_the_pile(game):
    """Not the top card: the point is not having to draw thirty others."""
    deck = game.decks[settings.DECK_MOVEMENT]
    wanted = deck.draw_pile[len(deck.draw_pile) // 2]
    assert wanted is not deck.draw_pile[-1], "meant to be buried"
    others = [c.uid for c in deck.draw_pile if c.uid != wanted.uid]

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_MOVEMENT,
                                           title=wanted.title))

    assert any(isinstance(e, ev.CardDrawn) for e in events)
    assert wanted.title in [c.title for c in game.players[0].hand]
    # every other card is still where it was
    assert [c.uid for c in deck.draw_pile] == [u for u in others
                                               if u in {c.uid for c in deck.draw_pile}]
    assert len(deck.draw_pile) == len(others)


def test_a_titled_draw_searches_only_the_deck_it_was_asked_about(game):
    """Card identity here is (deck_id, title), and the tab decides the deck.

    A title that exists only in the Chest deck must not be fetched out of it by
    a request naming the Movement deck — otherwise a future card printed in two
    decks would be drawn from whichever one happened to be searched first.
    """
    chest_only = game.decks[settings.DECK_CHEST].draw_pile[0].title
    assert not any(c.title == chest_only
                   for c in game.decks[settings.DECK_MOVEMENT].draw_pile)
    chest_before = game.decks[settings.DECK_CHEST].draw_count

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_MOVEMENT,
                                           title=chest_only))

    assert isinstance(events[0], ev.ActionRejected)
    assert game.decks[settings.DECK_CHEST].draw_count == chest_before
    assert chest_only not in [c.title for c in game.players[0].hand]


def test_the_card_that_arrives_belongs_to_the_deck_it_was_asked_of(game):
    for deck_id in settings.TABLE_DECKS:
        deck = game.decks[deck_id]
        player = game.players[0]
        player.hand.clear()
        title = deck.draw_pile[-1].title
        game.apply(cmd.DrawTitledCard(player_index=0, deck_id=deck_id,
                                      title=title))
        arrived = [c for c in player.hand if c.title == title]
        assert arrived, deck_id
        assert all(c.deck_id == deck_id for c in arrived)


def test_a_titled_draw_can_reach_the_discard_pile(game):
    """The discard pile is part of the deck — ``take_card`` reshuffles it back
    in the moment the draw pile runs dry, so refusing a card sitting there
    would be an off-by-one-pile lie.
    """
    deck = game.decks[settings.DECK_CHEST]
    card = deck.draw_pile[-1]
    # EVERY copy into the discard pile, not just one: the deck holds several of
    # some titles, and leaving one in the draw pile would let the draw pile
    # answer the request and prove nothing.
    copies = [c for c in deck.draw_pile if c.title == card.title]
    for copy in copies:
        deck.draw_pile.remove(copy)
        deck.discard_pile.append(copy)

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_CHEST,
                                           title=card.title))

    assert any(isinstance(e, ev.CardDrawn) for e in events)
    drawn = [c for c in game.players[0].hand if c.title == card.title]
    assert drawn, "the copy in the discard pile should have answered"
    assert drawn[0].uid not in [c.uid for c in deck.discard_pile]
    assert len(deck.discard_pile) == len(copies) - 1


def test_a_missing_card_is_refused_and_changes_nothing(game):
    """No fake card, no touched hand, no corrupted deck."""
    deck = game.decks[settings.DECK_CHEST]
    hand = [c.uid for c in game.players[0].hand]
    draw_pile = [c.uid for c in deck.draw_pile]
    discard = [c.uid for c in deck.discard_pile]

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_CHEST,
                                           title="Nie ma takiej karty"))

    assert isinstance(events[0], ev.ActionRejected)
    assert "Brak karty" in events[0].reason
    assert [c.uid for c in game.players[0].hand] == hand
    assert [c.uid for c in deck.draw_pile] == draw_pile
    assert [c.uid for c in deck.discard_pile] == discard


def test_a_card_whose_every_copy_is_already_out_is_refused(game):
    """Exists in the definition, exists in nobody's deck."""
    deck = game.decks[settings.DECK_MOVEMENT]
    title = deck.draw_pile[-1].title
    deck.draw_pile = [c for c in deck.draw_pile if c.title != title]
    deck.discard_pile = [c for c in deck.discard_pile if c.title != title]

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_MOVEMENT,
                                           title=title))
    assert isinstance(events[0], ev.ActionRejected)


def test_a_titled_draw_will_not_raid_the_character_deck(game):
    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_CHARACTERS,
                                           title="Lubin"))
    assert isinstance(events[0], ev.ActionRejected)


def test_a_full_hand_refuses_a_titled_draw(game):
    """It obeys the ordinary rules; it is a shortcut, not a cheat."""
    deck = game.decks[settings.DECK_MOVEMENT]
    player = game.players[0]
    while not player.hand_is_full:
        player.hand.append(deck.take_card())
    title = deck.draw_pile[-1].title
    held = len(player.hand)

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_MOVEMENT,
                                           title=title))

    assert isinstance(events[0], ev.ActionRejected)
    assert len(player.hand) == held


def test_a_titled_draw_lets_the_card_act_on_the_way_in(game):
    """It goes through the same arrival as any other draw.

    Troll replaces itself when drawn; a titled draw that skipped
    ``_after_draw`` would quietly disable that, and the two draw routes would
    drift apart (N104's lesson, on a different pair of paths).
    """
    deck = game.decks[settings.DECK_MOVEMENT]
    if not any(c.title == "Troll" for c in deck.draw_pile):
        pytest.skip("no Troll in this content")
    before = deck.draw_count

    game.apply(cmd.DrawTitledCard(player_index=0,
                                 deck_id=settings.DECK_MOVEMENT,
                                 title="Troll"))

    # Troll plus the replacement it drew: two cards left the pile, not one.
    assert deck.draw_count == before - 2


def test_the_titled_draw_does_not_touch_the_configured_quantity(game):
    """The two numbers are different numbers.

    'Dobierz kartę' moves a copy from the deck to a hand; the library's count
    is copies IN THE MATCH, and a card in a hand is still in the match.  So the
    quantity must NOT move — if it does, somebody has wired the button to the
    stepper.
    """
    deck = game.decks[settings.DECK_MOVEMENT]
    title = deck.draw_pile[-1].title
    before = game.deck_card_count(settings.DECK_MOVEMENT, title)

    game.apply(cmd.DrawTitledCard(player_index=0,
                                  deck_id=settings.DECK_MOVEMENT, title=title))

    assert game.deck_card_count(settings.DECK_MOVEMENT, title) == before


def test_the_titled_draw_command_survives_the_wire(game):
    command = cmd.DrawTitledCard(player_index=2, deck_id=settings.DECK_MODS,
                                 title="AKO")
    assert cmd.Command.from_dict(command.to_dict()) == command


def test_a_titled_draw_is_not_bound_to_a_turn(library):
    """A tester fetching a card is not taking a move."""
    game = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=77,
                      edit_mode=False, local_seat=0),
        library,
    )
    game.active_player_index = 3
    title = game.decks[settings.DECK_MODS].draw_pile[-1].title

    events = game.apply(cmd.DrawTitledCard(player_index=0,
                                           deck_id=settings.DECK_MODS,
                                           title=title))
    assert not any(isinstance(e, ev.ActionRejected) for e in events)


def test_a_titled_draw_is_still_owned_by_its_seat(library):
    """You may fetch a card for YOURSELF.  The server checks that."""
    game = create_game(
        SessionConfig(num_players=5, board_cells=24, seed=77, edit_mode=False),
        library,
    )
    title = game.decks[settings.DECK_MODS].draw_pile[-1].title
    command = cmd.DrawTitledCard(player_index=1, deck_id=settings.DECK_MODS,
                                 title=title)
    assert game.authorise_remote(command, 1) is None
    assert game.authorise_remote(command, 2) is not None


# ── the feedback the library shows ───────────────────────────────────────────
def test_a_refused_draw_is_reported_in_red_in_the_library(screen):
    """The hand is behind the window, so the refusal has to be in front of it."""
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_CHEST)
    draw(screen)
    layout = screen.app.layout
    entry = shelf.entries[0]
    deck = screen.state.decks[settings.DECK_CHEST]
    deck.draw_pile = [c for c in deck.draw_pile if c.title != entry.title]
    deck.discard_pile = [c for c in deck.discard_pile if c.title != entry.title]
    hand = [c.uid for c in screen.state.player(screen.view_seat).hand]

    click(screen, shelf._rows_under_card(0, layout)["draw"].center)

    assert "Brak karty" in shelf.notice
    assert shelf.notice_ok is False
    assert [c.uid for c in screen.state.player(screen.view_seat).hand] == hand
    # ...and red is what it is actually painted.
    assert shelf.notice_colour(screen.app.renderer.theme) == \
        screen.app.renderer.theme.warning


def test_a_successful_draw_is_confirmed_in_green(screen):
    draw(screen)
    shelf = screen.card_library
    shelf.open()
    shelf.select_tab(settings.DECK_MODS)
    draw(screen)
    layout = screen.app.layout

    click(screen, shelf._rows_under_card(1, layout)["draw"].center)

    assert shelf.notice == "Dodano kartę do ręki"
    assert shelf.notice_ok is True
    assert shelf.notice_colour(screen.app.renderer.theme) == \
        screen.app.renderer.theme.valid


def test_the_confirmation_stays_quiet_when_the_library_is_shut(screen):
    """Every other draw announces itself by appearing in the fan."""
    draw(screen)
    assert not screen.card_library.active
    seat = screen.view_seat
    screen.submit(cmd.DrawTitledCard(
        player_index=seat, deck_id=settings.DECK_MODS,
        title=screen.state.decks[settings.DECK_MODS].draw_pile[-1].title))
    screen.session.poll()
    assert screen.card_library.notice == ""
