"""
Game setup.

Turns a :class:`SessionConfig` (what the menu, and later the lobby, produced)
into a ready-to-play :class:`GameState`.  Keeping this separate from the state
itself means the future host can build a game from a config message without
any UI involved, and tests can spin one up in two lines.

Rules preserved from the prototype:

* explicit character picks are honoured first;
* if nobody explicitly took Piotrek, one of the remaining random players is
  guaranteed to get him;
* everyone else is filled in randomly;
* whoever holds Piotrek starts with 5 movement cards, everyone else with 3.
"""

from __future__ import annotations

import random
from dataclasses import replace
import time
from typing import Dict, List, Optional

from ..board.board import BoardModel, BoardTheme
from ..cards.base_card import Card
from ..cards.deck import Deck
from ..cards.loader import ContentLibrary
from ..config import settings
from ..config.settings import RULES, SessionConfig
from ..players.player import Player
from .game_state import GameState


def new_seed() -> int:
    return int(time.time() * 1000) & 0x7FFFFFFF


def build_decks(library: ContentLibrary, rng: random.Random) -> Dict[str, Deck]:
    """One deck per definition, each with its own RNG derived from the seed.

    Per-deck RNGs mean that drawing from the chest cannot shift the shuffle of
    the movement deck — useful for reproducible balance runs, and necessary if
    a client ever needs to re-simulate only part of a game.

    Card uids are renumbered deterministically here.  They default to a
    process-global counter, which is fine for one game but not for a networked
    one: a command says *which card* by uid, so the host and every client have
    to agree on the numbering.  Deck position gives that for free, and it is
    stable regardless of how many games the process has already built.
    """
    decks: Dict[str, Deck] = {}
    for ordinal, deck_id in enumerate(library.deck_order):
        definition = library.deck(deck_id)
        deck_rng = random.Random(rng.getrandbits(64))
        deck = Deck(definition, deck_rng)
        base = (ordinal + 1) * 10_000
        for index, card in enumerate(deck.draw_pile):
            card.uid = base + index
        decks[deck_id] = deck
    return decks


def assign_characters(
    players: List[Player],
    decks: Dict[str, Deck],
    choices: Optional[List[Optional[str]]],
    rng: random.Random,
) -> None:
    deck = decks[settings.DECK_CHARACTERS]
    if choices is None:
        choices = [None] * len(players)

    # 1) honour explicit picks
    for player, choice in zip(players, choices):
        if choice is None:
            continue
        card = deck.take_titled(choice)
        if card is not None:
            player.character = card

    # 2) guarantee a Piotrek among the still-unassigned players
    if not any(p.is_piotrek for p in players):
        piotrek = next((c for c in deck.draw_pile if c.is_piotrek), None)
        unassigned = [p for p in players if p.character is None]
        if piotrek is not None and unassigned:
            deck.draw_pile.remove(piotrek)
            rng.choice(unassigned).character = piotrek

    # 3) fill everyone else randomly
    deck.shuffle_draw_pile()
    for player in players:
        if player.character is not None:
            continue
        card = deck.take_card()
        if card is not None:
            player.character = card


def assign_piotrek_skill(
    players: List[Player], decks: Dict[str, Deck], rng: random.Random
) -> Optional[Card]:
    """Piotrek draws one of his three skills at the start of the game.

    The design document says he has one from the outset, and the skills change
    how many cards he starts with, so this has to happen before the deal.
    """
    piotrek = next((p for p in players if p.is_piotrek), None)
    if piotrek is None:
        return None
    deck = decks.get(settings.DECK_SKILLS)
    if deck is None:
        return None
    card = deck.take_card()
    if card is not None:
        piotrek.skill = card
    return card


def starting_hand_size(player: Player) -> int:
    """How many movement cards a player begins with.

    Piotrek starts with more than a hunter, and a skill may trade that back —
    ChatGPT costs him two cards for its range bonus.  The adjustment is a
    ``passive`` declared in characters.json, so a future skill that changes the
    opening hand needs no code here.
    """
    count = RULES.start_hand_piotrek if player.is_piotrek else RULES.start_hand_default
    for card in (player.skill, player.character):
        if card is not None:
            count += int(card.passive.get("movement_cards_delta", 0))
    return max(0, count)


def deal_starting_hands(players: List[Player], decks: Dict[str, Deck]) -> None:
    movement = decks[settings.DECK_MOVEMENT]
    for player in players:
        for _ in range(starting_hand_size(player)):
            card = movement.take_card()
            if card is None or not player.add_card(card):
                break


def assign_secret_pawn(players: List[Player], library: ContentLibrary, rng: random.Random) -> None:
    """Piotrek secretly gets one of the pawn colours.

    The design document leaves open whether Piotrek picks his pawn or draws it
    at random; this does the random draw, and the field is on the Player so a
    'choose your pawn' step can set it instead without touching anything else.
    """
    piotrek = next((p for p in players if p.is_piotrek), None)
    if piotrek is None or not library.pawns:
        return
    piotrek.secret_pawn = rng.choice(library.pawns).id


def create_game(
    config: SessionConfig,
    library: Optional[ContentLibrary] = None,
    board_theme: Optional[BoardTheme] = None,
) -> GameState:
    config = config.normalised()
    library = library or ContentLibrary.load()
    seed = config.seed or new_seed()
    # `replace` rather than rebuilding field by field: the old version listed
    # every field explicitly and quietly dropped any that were added later,
    # which is how `double_frequency` went missing between the menu and the
    # board generator.
    config = replace(config, seed=seed)
    rng = random.Random(seed)

    decks = build_decks(library, rng)

    players: List[Player] = []
    for i in range(config.num_players):
        pawn = library.pawns[i % len(library.pawns)]
        players.append(
            Player(index=i, name=f"Player {i + 1}", color=pawn.color, color_name=pawn.name)
        )

    assign_characters(players, decks, config.character_choices, rng)
    assign_piotrek_skill(players, decks, rng)
    deal_starting_hands(players, decks)
    assign_secret_pawn(players, library, rng)

    board = BoardModel.generate(
        cell_count=config.board_cells,
        seed=seed,
        theme=board_theme,
        pawn_count=len(library.pawns),
        double_frequency=config.double_frequency,
    )

    state = GameState(
        library=library,
        config=config,
        board=board,
        players=players,
        decks=decks,
        rng=rng,
    )

    # The design document opens every round with Piotrek, so the game starts on
    # whichever seat the cadence puts first rather than always on seat 0.
    order = state.seat_order(1)
    if order:
        state.active_player_index = order[0]
    return state
