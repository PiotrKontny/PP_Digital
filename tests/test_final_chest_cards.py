"""
The last four Karty Skrzyni: Gejtos, Alter Ego, Kingmaker and the two cards
that are still settled at the table.

Stage 28.  Three quite different things are being tested here.

Gejtos is the first card that moves pawns WITHOUT moving them along a route —
a tower is picked up whole and put down elsewhere — so nothing about distance,
direction or Mody Patusa applies to it and the tests say so.

Alter Ego is the only rule in the game that hands the hidden identity back.
Most of what matters about it is what does NOT happen: no machine may learn the
colour from the card itself, and for the length of the swap nobody knows it at
all — including the authority.

The rest is the smallest change in the stage and the one a player would notice
first: every Chest card can now be PLAYED, whether or not its rule exists yet.
"""

from __future__ import annotations

import pytest

from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import RULES, SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine import effects
from pedzacy_piotrek.engine import events as ev
from pedzacy_piotrek.engine import victory
from pedzacy_piotrek.engine.setup import create_game


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def make(library, players: int = 5, seed: int = 99, **kwargs):
    kwargs.setdefault("double_frequency", 0.0)
    kwargs.setdefault("mod_round_first", 10_000)
    kwargs.setdefault("chest_open_round", 10_000)
    return create_game(
        SessionConfig(num_players=players, board_cells=24, seed=seed,
                      piotrek_picks_pawn=False,
                      debug_version=players < RULES.min_players, **kwargs),
        library,
    )


def give(game, seat: int, title: str):
    deck = game.decks[settings.DECK_CHEST]
    card = next(c for c in deck.draw_pile if c.title == title)
    deck.draw_pile.remove(card)
    game.player(seat).add_card(card)
    return card


def play(game, seat: int, card, **choices):
    return game.apply(cmd.PlayCard(
        player_index=seat, card_uid=card.uid,
        choices={k: str(v) for k, v in choices.items()}))


def pawns(game):
    return [pawn.id for pawn in game.library.pawns]


def clear_board(game):
    for pawn_id in pawns(game):
        game.board.remove_pawn(pawn_id)
    game._sync_token_positions()


def place(game, pawn_id: str, position: int, half: int = 0) -> int:
    tile = game.board.position(position).tiles[half]
    game.board.place_pawn(pawn_id, tile.index)
    game._sync_token_positions()
    return tile.index


def stack_at(game, position: int, half: int = 0):
    """The tower on a field, BOTTOM FIRST."""
    return list(game.board.position(position).tiles[half].stack)


def choice_of(events):
    return next((e for e in events if isinstance(e, ev.ChoiceRequired)), None)


def rejection(events):
    return next((e for e in events if isinstance(e, ev.ActionRejected)), None)


# ── part 1: every Chest card is playable ─────────────────────────────────────
def test_every_chest_card_can_be_played(library):
    """The point of the stage, in one assertion.

    ``Card.is_playable`` reads ``definition.effect is not None``, so a card
    waiting on a ruling used to sit in the hand refusing to be clicked — and a
    player holding two of them was holding two dead cards against the chest
    limit.
    """
    game = make(library)
    for card_def in library.deck(settings.DECK_CHEST).cards:
        card = give(game, 0, card_def.title)
        assert card.is_playable, f"{card_def.title} cannot be played"


def test_an_undesigned_card_resolves_and_is_discarded(library):
    """Played, said, discarded.  Nothing pretends the rule was applied."""
    game = make(library)
    deck = game.decks[settings.DECK_CHEST]
    card = give(game, 0, "Nie masz Rosji")
    events = play(game, 0, card)

    assert game.player(0).card_by_uid(card.uid) is None, "it left the hand"
    assert deck.find_discarded(card.uid) is card, "and reached the discard pile"
    assert any(isinstance(e, ev.CardPlayed) for e in events)
    # The printed text is what the table needs in order to settle it by hand,
    # and it arrives as MoveFizzled rather than ActionRejected — the card was
    # PLAYED, not refused, and the difference is whether it stays in the hand.
    spoken = next((e for e in events if isinstance(e, ev.MoveFizzled)), None)
    assert spoken is not None and "ruch przeciwnika" in spoken.reason


def test_an_undesigned_card_changes_nothing(library):
    """A 'manual' card must not quietly move, freeze or draw anything."""
    game = make(library)
    clear_board(game)
    for offset, pawn_id in enumerate(pawns(game)):
        place(game, pawn_id, 3 + offset)
    before = game.board.to_dict()
    hands = {p.index: [c.uid for c in p.hand] for p in game.players}

    card = give(game, 0, "Shady")
    play(game, 0, card)

    assert game.board.to_dict() == before
    hands[0] = [uid for uid in hands[0] if uid != card.uid]
    hands[0].append(card.uid)
    assert [c.uid for c in game.player(0).hand] == \
        [uid for uid in hands[0] if uid != card.uid]


def test_kingmaker_is_playable_and_does_nothing(library):
    """The presentation and the title stay; no mechanic was invented."""
    game = make(library)
    hunter = next(p for p in game.players if not p.is_piotrek)
    deck = game.decks[settings.DECK_CHEST]
    card = next(c for c in deck.draw_pile if c.title == "Gamechanger")
    deck.draw_pile.remove(card)
    game._after_draw(hunter, card)
    hunter.add_card(card)

    assert card.title == "Kingmaker", "a hunter's Gamechanger is Kingmaker"
    assert card.is_playable
    before = game.snapshot()
    play(game, hunter.index, card)
    after = game.snapshot()

    assert not game.identity_swap, "Kingmaker must not touch the identity"
    assert after["eliminated"] == before["eliminated"]


# ── part 2: Gejtos ───────────────────────────────────────────────────────────
def test_gejtos_asks_for_the_option_before_the_pawn(library):
    """The option changes which fields are asked about, so it comes first."""
    game = make(library)
    card = give(game, 0, "Gejtos")
    question = choice_of(play(game, 0, card))
    assert question is not None and question.key == "option"
    assert {option for option, _ in question.options} == {"gather", "scatter"}

    question = choice_of(play(game, 0, card, option="gather"))
    assert question is not None and question.key == "pawn"


def test_mezczyzna_pulls_both_neighbours_onto_the_anchor(library):
    """The chosen pawn never moves; the neighbours land on its head."""
    game = make(library)
    clear_board(game)
    anchor, behind, ahead = "zielony", "niebieski", "różowy"
    place(game, anchor, 5)
    place(game, behind, 4)
    place(game, ahead, 6)

    card = give(game, 0, "Gejtos")
    play(game, 0, card, option="gather", pawn=anchor)

    assert game.board.position_of_pawn(anchor) == 5, "the anchor never moves"
    assert stack_at(game, 4) == [] and stack_at(game, 6) == []
    assert stack_at(game, 5)[0] == anchor, "and stays at the bottom"
    assert set(stack_at(game, 5)) == {anchor, behind, ahead}


def test_mezczyzna_moves_a_neighbouring_tower_whole_and_in_order(library):
    """A field is picked up as a block, not one pawn at a time."""
    game = make(library)
    clear_board(game)
    anchor = "zielony"
    low, mid, high = "niebieski", "różowy", "czerwony"
    place(game, anchor, 5)
    for pawn_id in (low, mid, high):
        place(game, pawn_id, 6)
    assert stack_at(game, 6) == [low, mid, high]

    card = give(game, 0, "Gejtos")
    play(game, 0, card, option="gather", pawn=anchor)

    assert stack_at(game, 5) == [anchor, low, mid, high]


def test_kobieta_pushes_both_neighbours_one_further_away(library):
    """The mirror image: in front goes forward, behind goes back."""
    game = make(library)
    clear_board(game)
    anchor, behind, ahead = "zielony", "niebieski", "różowy"
    place(game, anchor, 5)
    place(game, behind, 4)
    place(game, ahead, 6)

    card = give(game, 0, "Gejtos")
    play(game, 0, card, option="scatter", pawn=anchor)

    assert game.board.position_of_pawn(anchor) == 5
    assert game.board.position_of_pawn(behind) == 3
    assert game.board.position_of_pawn(ahead) == 7


def test_kobieta_refuses_to_push_anybody_before_field_one(library):
    """The card says it cannot be played, so it is not played.

    Every other backward move in the game CLAMPS at field one, which is right
    for a card that says "move back".  Here the card names the case, and a card
    that silently did three quarters of its rule would be worse than one that
    would not be played at all.
    """
    game = make(library)
    clear_board(game)
    anchor, behind = "zielony", "niebieski"
    place(game, anchor, 1)
    place(game, behind, 0)

    card = give(game, 0, "Gejtos")
    events = play(game, 0, card, option="scatter", pawn=anchor)

    assert rejection(events) is not None
    assert game.board.position_of_pawn(behind) == 0, "nobody moved"
    assert game.player(0).card_by_uid(card.uid) is not None, "the card stays"


def test_gejtos_ignores_an_empty_neighbour(library):
    game = make(library)
    clear_board(game)
    anchor, ahead = "zielony", "różowy"
    place(game, anchor, 5)
    place(game, ahead, 6)

    card = give(game, 0, "Gejtos")
    play(game, 0, card, option="gather", pawn=anchor)

    assert stack_at(game, 5) == [anchor, ahead]


def test_gejtos_with_no_neighbours_resolves_to_nothing(library):
    game = make(library)
    clear_board(game)
    anchor = "zielony"
    place(game, anchor, 5)
    far = "różowy"
    place(game, far, 9)

    card = give(game, 0, "Gejtos")
    events = play(game, 0, card, option="gather", pawn=anchor)

    assert any(isinstance(e, ev.MoveFizzled) for e in events)
    assert game.board.position_of_pawn(far) == 9
    assert game.player(0).card_by_uid(card.uid) is None, "but it was played"


def test_gejtos_asks_which_half_of_a_widened_neighbour(library):
    game = make(library, seed=7, double_frequency=1.0)
    doubled = [i for i in range(game.board.position_count)
               if game.board.position(i).is_doubled]
    assert len(doubled) >= 3
    clear_board(game)
    anchor, ahead = "zielony", "różowy"
    centre = doubled[1]
    place(game, anchor, centre)
    place(game, ahead, centre + 1, half=1)

    card = give(game, 0, "Gejtos")
    answers = {"option": "gather", "pawn": anchor}
    question = choice_of(play(game, 0, card, **answers))
    assert question is not None and question.kind == "tile"
    assert question.key.startswith("from"), \
        "the anchor's own field is a fact, not a question — only neighbours ask"


def test_gejtos_takes_only_the_half_it_was_pointed_at(library):
    game = make(library, seed=7, double_frequency=1.0)
    doubled = [i for i in range(game.board.position_count)
               if game.board.position(i).is_doubled]
    clear_board(game)
    anchor, near, far = "zielony", "różowy", "czerwony"
    centre = doubled[1]
    place(game, anchor, centre)
    place(game, near, centre + 1, half=0)
    place(game, far, centre + 1, half=1)

    card = give(game, 0, "Gejtos")
    answers = {"option": "gather", "pawn": anchor}
    for _ in range(4):
        question = choice_of(play(game, 0, card, **answers))
        if question is None:
            break
        answers[question.key] = str(question.tiles[0])

    assert near in (stack_at(game, centre, 0) + stack_at(game, centre, 1))
    assert game.board.position_of_pawn(far) == centre + 1, "the other half stayed"


def test_gejtos_is_untouched_by_the_movement_mods(library):
    """It is a Chest card, and it is not a route (N103)."""
    game = make(library)
    clear_board(game)
    anchor, ahead = "zielony", "różowy"
    place(game, anchor, 5)
    place(game, ahead, 6)
    for title, slot in (("Masa solna", 0), ("Halloween", 1)):
        deck = game.decks[settings.DECK_MODS]
        mod = next(c for c in deck.draw_pile if c.title == title)
        deck.draw_pile.remove(mod)
        game.mod_slots[slot] = mod
    game._sync_mod_states()

    card = give(game, 0, "Gejtos")
    play(game, 0, card, option="gather", pawn=anchor)

    assert stack_at(game, 5) == [anchor, ahead]


# ── part 3: Alter Ego ────────────────────────────────────────────────────────
def piotrek_of(game):
    return game.player(game.piotrek_seat)


def alter_ego(game):
    """Deal Gamechanger to Piotrek through the real transformation path."""
    player = piotrek_of(game)
    deck = game.decks[settings.DECK_CHEST]
    card = next(c for c in deck.draw_pile if c.title == "Gamechanger")
    deck.draw_pile.remove(card)
    game._after_draw(player, card)
    player.add_card(card)
    assert card.title == "Alter Ego"
    return card


def run_swap(game, new_colour: str):
    """Play Alter Ego and carry the swap through to the end, as a table would."""
    seat = game.piotrek_seat
    card = alter_ego(game)
    play(game, seat, card)
    for followed in victory.review(game):
        game.apply(followed, local=False)
    assert game.set_piotrek_pawn(new_colour)
    return game.apply(cmd.FinishIdentitySwap(), local=False)


def test_only_piotrek_may_change_identity(library):
    """A hunter can be dealt the card; the refusal is a real rule."""
    game = make(library)
    hunter = next(p for p in game.players if not p.is_piotrek)
    deck = game.decks[settings.DECK_CHEST]
    card = next(c for c in deck.draw_pile if c.title == "Gamechanger")
    deck.draw_pile.remove(card)
    # Forced past the presentation so a HUNTER holds the Piotrek face of it.
    card.transform(card.definition)
    from dataclasses import replace as _replace
    from pedzacy_piotrek.cards.base_card import EffectSpec
    card.definition = _replace(card.definition, title="Alter Ego",
                               effect=EffectSpec(type="swap_identity"),
                               presentation=None)
    hunter.add_card(card)

    events = play(game, hunter.index, card)
    assert rejection(events) is not None
    assert not game.identity_swap
    assert hunter.card_by_uid(card.uid) is not None, "a refusal keeps the card"


def test_playing_alter_ego_names_no_colour(library):
    """The card itself must never carry the secret (N72).

    This is the whole reason the reveal is a separate authority command: the
    handler runs on every replica, and most of them have never been told.
    """
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    card = alter_ego(game)

    events = play(game, seat, card)
    started = next(e for e in events if isinstance(e, ev.IdentitySwapStarted))

    assert started.piotrek_seat == seat
    assert "czerwony" not in repr(started)
    assert game.identity_swap == game.SWAP_REVEALING
    assert game.eliminated_pawns == [], "nothing is revealed yet"


def test_the_authority_publishes_the_old_colour(library):
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))

    followed = victory.review(game)
    assert followed == [cmd.RevealIdentity(pawn_id="czerwony")]


def test_a_replica_decides_nothing(library):
    """The safety net under the whole design: no colour, no verdict."""
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))
    # A replica has never been told the colour.
    piotrek_of(game).secret_pawn = None

    assert victory.review(game) == []


def test_the_notepad_resets_to_the_revealed_colour(library):
    """The brief's own example, and the reason the reset has to happen.

    Piotrek moves to a colour the hunters had already ruled out, which is only
    possible because ruling it out is void: the crossings were evidence about
    an identity that no longer exists.
    """
    game = make(library)
    piotrek_of(game).secret_pawn = "czerwony"
    game.eliminated_pawns = ["żółty", "różowy", "pomarańczowy", "zielony"]

    run_swap(game, "zielony")

    assert game.eliminated_pawns == ["czerwony"]
    assert piotrek_of(game).secret_pawn == "zielony"
    assert not game.identity_swap


def test_nobody_knows_the_colour_during_the_swap(library):
    """Between the reveal and the choice there is NO hidden identity at all.

    Which is what stops a tower being checked against nobody halfway through.
    """
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))
    for followed in victory.review(game):
        game.apply(followed, local=False)

    assert game.identity_swap == game.SWAP_CHOOSING
    assert game.piotrek_pawn is None
    assert victory.hidden_pawn(game) is None
    assert victory.review(game) == [], "and therefore nothing is judged"


def test_the_table_is_stopped_while_piotrek_chooses(library):
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))

    problem = game.authorise_remote(cmd.EndTurn(player_index=seat), seat)
    assert problem is not None and "tożsamość" in problem


def test_the_colour_just_revealed_cannot_be_chosen_again(library):
    """The card trades one identity for a DIFFERENT one."""
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))
    for followed in victory.review(game):
        game.apply(followed, local=False)

    assert game.swap_forbidden_pawn() == "czerwony"
    assert not game.set_piotrek_pawn("czerwony")
    assert game.set_piotrek_pawn("niebieski")


def test_victory_follows_the_new_colour_immediately(library):
    """Piotrek now wins with Green and no longer with Red."""
    game = make(library)
    clear_board(game)
    piotrek_of(game).secret_pawn = "czerwony"
    run_swap(game, "zielony")

    finish = game.board.last_position
    place(game, "czerwony", finish)
    assert victory.review(game) == [], "the old colour wins nothing"

    game.board.remove_pawn("czerwony")
    place(game, "zielony", finish)
    followed = victory.review(game)
    assert followed and isinstance(followed[0], cmd.DeclareVictory)
    assert followed[0].pawn_id == "zielony"


def test_the_hunters_now_search_for_the_new_colour(library):
    """A tower whose bottom is the NEW colour ends the match for the hunters."""
    game = make(library, players=5)
    clear_board(game)
    piotrek_of(game).secret_pawn = "czerwony"
    run_swap(game, "zielony")

    # Everybody onto one field, the new colour holding it up.
    place(game, "zielony", 6)
    for pawn_id in pawns(game):
        if pawn_id != "zielony":
            place(game, pawn_id, 6)

    followed = victory.review(game)
    assert followed and isinstance(followed[0], cmd.DeclareVictory)
    assert followed[0].outcome == "hunters"
    assert followed[0].pawn_id == "zielony"


def test_a_second_swap_is_refused_while_one_is_running(library):
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))

    # Only ONE Gamechanger is printed, so the second copy is built by hand.
    # What is under test is the engine's refusal, not the deck's contents.
    from dataclasses import replace as _replace
    from pedzacy_piotrek.cards.base_card import Card, EffectSpec
    original = piotrek_of(game).hand[-1]
    second = Card(uid=99999, definition=_replace(
        original.definition, title="Alter Ego",
        effect=EffectSpec(type="swap_identity"), presentation=None))
    piotrek_of(game).add_card(second)
    events = play(game, seat, second)
    assert rejection(events) is not None
    assert piotrek_of(game).card_by_uid(second.uid) is not None


def test_the_swap_flag_is_in_the_snapshot_and_names_no_colour(library):
    """Every machine must agree the table is stopped — and learn nothing else."""
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    play(game, seat, alter_ego(game))

    shot = game.snapshot()
    assert shot["identity_swap"] == game.SWAP_REVEALING
    assert "czerwony" not in repr(shot["identity_swap"])
    assert "secret" not in shot and "piotrek_pawn" not in shot


def test_a_pending_lead_check_does_not_survive_the_swap(library):
    """Squid Game armed a question about an identity that no longer exists."""
    game = make(library)
    seat = game.piotrek_seat
    piotrek_of(game).secret_pawn = "czerwony"
    game.pending_lead_check = "czerwony"
    play(game, seat, alter_ego(game))
    for followed in victory.review(game):
        game.apply(followed, local=False)

    assert game.pending_lead_check is None
