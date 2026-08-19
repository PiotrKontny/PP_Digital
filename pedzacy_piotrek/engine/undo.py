"""
Rewinding one turn — the checkpoint behind undo and Liskowy Konkurs.

A checkpoint is taken just BEFORE a movement card resolves and describes
everything that card is allowed to change.  Restoring it makes the table
identical to the moment before the card was played, which is a stronger promise
than "put the pawn back" and is the only one worth making: a card can move
several pawns, restack a tower, discard itself, draw a replacement, grant a
status, arm a check and end the turn, and an undo that forgot any one of those
would leave the game quietly wrong.

TWO THINGS THIS DELIBERATELY DOES NOT DO.

It does NOT deep-copy the game state.  ``copy.deepcopy`` works and is fast, but
the pieces it produces are new objects, and the renderer caches the board while
the interface holds cards — swapping the objects out from under them leaves
half the screen drawing a board nobody is playing on.  So the checkpoint stores
POSITIONS AND MEMBERSHIP, by card uid and tile index, and puts the existing
objects back where they were.  Card identity survives a rewind, which is what
lets ``find_discarded(uid) is card`` keep meaning something.

It does NOT snapshot the deck by shuffling or re-seeding.  The draw pile is
restored to the exact order it had, so the card drawn at the end of the undone
turn goes back to the top and the SAME card is drawn again at the end of the
corrective turn.  That requirement is not implemented anywhere; it falls out of
restoring the order, which is the only reason it can be relied on.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TurnCheckpoint:
    """Everything one turn is allowed to change, and who may rewind it."""

    #: The seat whose turn produced this checkpoint — the only one that may
    #: undo it.  Authoritative: a client asking to undo somebody else's turn is
    #: refused by the engine, not by the interface hiding a button.
    seat: int
    #: The card that was played, so the interface can name what would come back.
    card_uid: int = -1
    #: Set once the WINDOW closes — the next player has played — after which
    #: neither undo nor Liskowy Konkurs may use it.
    spent: bool = False

    # ── the state itself, all by value ──────────────────────────────────────
    pawn_tiles: Dict[str, int] = field(default_factory=dict)
    stacks: Dict[int, List[str]] = field(default_factory=dict)
    hands: Dict[int, List[int]] = field(default_factory=dict)
    draw_piles: Dict[str, List[int]] = field(default_factory=dict)
    discard_piles: Dict[str, List[int]] = field(default_factory=dict)
    ability_uses: Dict[int, Tuple[Optional[int], Optional[int]]] = \
        field(default_factory=dict)
    statuses: List[Any] = field(default_factory=list)
    scalars: Dict[str, Any] = field(default_factory=dict)
    rng_state: Any = None
    #: Where the pawns that are NOT on the board were standing.  Membership is
    #: enough for a pawn on a field — ``_sync_token_positions`` recomputes the
    #: rest from the tile and the tower — but a pawn in the camp has a position
    #: and no tile, so nothing can recompute it and the rewind would leave it
    #: stranded wherever the undone card put it: in the camp by the rules, and
    #: drawn on field 1.  Stage 52.
    token_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)


#: State that is a plain value and can simply be put back.  Listed rather than
#: discovered so that a new field added to GameState is a deliberate decision
#: about whether a turn may change it, not an accident either way.
_SCALARS = (
    "active_player_index", "turn_counter", "turn_slot", "round_number",
    "phase", "victory", "identity_swap",
    "eliminated_pawns", "mod_slots", "armed_mods",
    "pending_movement", "pending_check", "pending_breakup",
    "pending_lead_check", "pending_pawn_check", "pending_mod_selection",
    "check_allowed", "check_needs_separation",
    "_pending_chest_choices", "_resolved_movement",
)


def _all_cards(state) -> Dict[int, Any]:
    """Every card object in the game, by uid.

    The checkpoint stores uids; this is how they become cards again.  Hands,
    both piles of every deck and the mod rack — a card is always in exactly one
    of them, and a uid that has since vanished is simply skipped on restore
    rather than crashing the table.
    """
    cards: Dict[int, Any] = {}
    for deck in state.decks.values():
        for card in [*deck.draw_pile, *deck.discard_pile]:
            cards[card.uid] = card
    for player in state.players:
        for card in player.hand:
            cards[card.uid] = card
        for held in (player.character, player.skill):
            if held is not None:
                cards[held.uid] = held
    for card in state.mod_slots:
        if card is not None:
            cards[card.uid] = card
    return cards


def capture(state, seat: int, card_uid: int = -1) -> TurnCheckpoint:
    """Photograph the table before a card resolves."""
    point = TurnCheckpoint(seat=int(seat), card_uid=int(card_uid))

    point.pawn_tiles = dict(state.board.pawn_tiles)
    point.stacks = {tile.index: list(tile.stack) for tile in state.board.tiles
                    if tile.stack}
    point.hands = {player.index: [card.uid for card in player.hand]
                   for player in state.players}
    point.draw_piles = {deck_id: [card.uid for card in deck.draw_pile]
                        for deck_id, deck in state.decks.items()}
    point.discard_piles = {deck_id: [card.uid for card in deck.discard_pile]
                           for deck_id, deck in state.decks.items()}
    # Ability charges are spent by a turn as surely as cards are.
    point.ability_uses = {
        player.index: (
            None if player.character is None else player.character.uses_left,
            None if player.skill is None else player.skill.uses_left,
        )
        for player in state.players
    }
    # Statuses are small dataclasses with plain payloads, so a copy is honest
    # and cheap.  They hold no reference to the board or to a card.
    point.statuses = [copy.deepcopy(status) for status in state.statuses.all()]
    point.scalars = {name: copy.deepcopy(getattr(state, name))
                     for name in _SCALARS if hasattr(state, name)}
    # THE DRAW ORDER IS NOT ENOUGH ON ITS OWN: an effect that rolls the dice
    # would otherwise roll differently after a rewind, so the generator goes
    # back too and a replayed turn is genuinely the same turn.
    point.rng_state = state.rng.getstate()
    point.token_positions = {
        token_id: tuple(token.position)
        for token_id, token in state.tokens.items()
        if token_id not in state.board.pawn_tiles
    }
    return point


def restore(state, point: TurnCheckpoint) -> None:
    """Put the table back, IN PLACE, without replacing any object.

    Every container is refilled rather than reassigned: the renderer holds the
    board, the interface holds cards, and a rewind that handed them new objects
    would leave them drawing a game that no longer exists.
    """
    cards = _all_cards(state)

    # ── the board ────────────────────────────────────────────────────────────
    for tile in state.board.tiles:
        tile.stack.clear()
    for index, stack in point.stacks.items():
        tile = state.board.tile(index)
        if tile is not None:
            tile.stack.extend(stack)
    state.board.pawn_tiles.clear()
    state.board.pawn_tiles.update(point.pawn_tiles)

    # ── cards: hands and both piles of every deck ───────────────────────────
    for player in state.players:
        player.hand.clear()
        for uid in point.hands.get(player.index, []):
            card = cards.get(uid)
            if card is not None:
                player.hand.append(card)
    for deck_id, deck in state.decks.items():
        deck.draw_pile.clear()
        deck.draw_pile.extend(
            card for card in (cards.get(uid) for uid in point.draw_piles.get(deck_id, []))
            if card is not None
        )
        deck.discard_pile.clear()
        deck.discard_pile.extend(
            card for card in (cards.get(uid) for uid in point.discard_piles.get(deck_id, []))
            if card is not None
        )
    # A card that came back out of the discard pile has to stop looking spent.
    for player in state.players:
        for card in player.hand:
            restore_card = getattr(card, "restore", None)
            if callable(restore_card):
                restore_card()
    for deck in state.decks.values():
        for card in deck.draw_pile:
            restore_card = getattr(card, "restore", None)
            if callable(restore_card):
                restore_card()

    for player in state.players:
        uses = point.ability_uses.get(player.index)
        if uses is None:
            continue
        character_uses, skill_uses = uses
        if player.character is not None and character_uses is not None:
            player.character.uses_left = character_uses
        if player.skill is not None and skill_uses is not None:
            player.skill.uses_left = skill_uses

    # ── statuses and the plain values ───────────────────────────────────────
    state.statuses.clear()
    for status in point.statuses:
        state.statuses.add(copy.deepcopy(status))
    for name, value in point.scalars.items():
        setattr(state, name, copy.deepcopy(value))

    if point.rng_state is not None:
        state.rng.setstate(point.rng_state)

    state._sync_token_positions()

    # A pawn back in the camp has no tile to be recomputed from, so its
    # position is put back by hand — AFTER the sync, which only speaks for the
    # pawns that are on the board.
    for token_id, position in point.token_positions.items():
        token = state.tokens.get(token_id)
        if token is not None and token_id not in state.board.pawn_tiles:
            token.position = tuple(position)
            token.tile_index = None
