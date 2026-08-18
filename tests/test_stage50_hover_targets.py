"""
Stage 50 — one preview, four hover targets, and text that fits.

Stage 49 gave the portrait a hover that enlarged the PORTRAIT, and left the
ability card sitting underneath it to be hovered separately.  That answered the
question the player did not have ("what does this character look like") twice
over, and answered the one they did have ("what does my ability do") in a card
142 px tall.  Stage 50 turns the portrait into a hover TARGET that resolves to
a CARD, removes the permanent ability card from the column, and points three
more targets at the same preview.

The shape being tested is:

    hover target  ->  resolve card  ->  the one CardPreview  ->  full card

    a character portrait
    a circle in the turn-order map
    a mod in the active rack
    a card in a slot                (unchanged, and must stay unchanged)

Most of this file is deliberately NON-VISUAL, per the brief: the resolution is
where the bugs live, and a test that only checks a rectangle would pass with
the wrong card in it.  The pixel tests that remain are the ones whose claim is
genuinely about pixels — that a description is not clipped, and that a long
title stays inside its card.
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

from pedzacy_piotrek.cards.base_card import Card, CardDef
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine import commands as cmd
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.render.card_renderer import BODY_FRACTION, TITLE_MAX_LINES
from pedzacy_piotrek.ui.ability_cards import AbilityCards
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

WINDOW = (1920, 1080)
REFERENCE_WINDOWS = [(1280, 760), (1600, 900), (1920, 1080), (2560, 1440)]

#: A description long enough to overflow a card that does not shrink its type.
LONG_TEXT = ("Sprawdza dowolnego pionka. Jeżeli nie trafi, odpada z gry. "
             "Co najmniej 3 pionki muszą być na planszy, a gracz musi "
             "zadeklarować kolor przed sprawdzeniem.")
LONG_TITLE = "Przerwanie Systemowe Poziomu Drugiego"


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


def build(library, size=WINDOW) -> GameScreen:
    app = App(Layout(), headless=True, size=size)
    state = create_game(
        SessionConfig(num_players=5, board_cells=24, chest_open_round=3, seed=77),
        library,
    )
    screen = GameScreen(app, LocalSession(state))
    app.push(screen)
    return screen


@pytest.fixture
def screen(library) -> GameScreen:
    return build(library)


def frame(screen: GameScreen, mouse=(0, 0), dt: float = 1 / 60) -> None:
    app = screen.app
    app.mouse = lambda position=tuple(mouse): position
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(dt, tuple(mouse))
    screen.draw(app.canvas)


def view_hunter(screen: GameScreen) -> int:
    for index, player in enumerate(screen.state.players):
        if not player.is_piotrek and screen.may_view(index):
            screen.view_seat = index
            return index
    raise AssertionError("no hunter seat to look at")


def view_piotrek(screen: GameScreen) -> int:
    for index, player in enumerate(screen.state.players):
        if player.is_piotrek and screen.may_view(index):
            screen.view_seat = index
            return index
    raise AssertionError("no Piotrek seat")


def portrait_rect(screen: GameScreen) -> pygame.Rect:
    player = screen.state.player(screen.view_seat)
    return screen.app.layout.character_panel(player.is_piotrek)["portrait"]


def put_mod(screen: GameScreen, slot: int = 0, art: bool = True) -> Card:
    deck = screen.state.deck(settings.DECK_MODS)
    card = next(c for c in deck.draw_pile if screen.cards.has_art(c) is art)
    deck.draw_pile.remove(card)
    screen.state.mod_slots[slot] = card
    return card


# ═════════════════════════════════════════════════════════════════════════════
# 1. The shared resolver: character -> ability card
# ═════════════════════════════════════════════════════════════════════════════
def test_a_character_resolves_to_its_own_ability_card(screen):
    abilities = AbilityCards()
    card = abilities.for_character(screen.state, "Big D Randy")

    assert card is not None
    assert card.title == "Granny Costume", \
        "the resolver returned the character's name instead of the ability's"


def test_the_resolver_preserves_the_ability_face_separation(screen):
    """Stage 49's rule, still holding through the new indirection."""
    abilities = AbilityCards()
    for owner, ability in [("Big D Randy", "Granny Costume"),
                           ("Lubin", "Jazdy"),
                           ("Ondrej", "Radar"),
                           ("Dziubdziuch", "Przerwanie Systemowe")]:
        card = abilities.for_character(screen.state, owner)
        assert card is not None and card.title == ability, owner


def test_the_resolver_returns_nothing_for_an_unknown_character(screen):
    assert AbilityCards().for_character(screen.state, "Nobody") is None
    assert AbilityCards().for_character(screen.state, None) is None


def test_the_resolver_returns_nothing_for_a_character_without_an_ability(screen):
    """Piotrek's own character card carries no fixed ability.

    This is also the hidden-information answer for the turn-order map: his
    circle resolves to ``None``, so his hand of skills stays private.
    """
    assert AbilityCards().for_character(screen.state, "Piotrek") is None


def test_the_resolver_caches_instead_of_churning_uids(screen):
    """A fresh Card per frame would churn the uid the drag code keys on."""
    abilities = AbilityCards()
    first = abilities.for_character(screen.state, "Big D Randy")
    second = abilities.for_character(screen.state, "Big D Randy")
    assert first is second
    assert first.uid == second.uid


def test_a_hunter_seat_resolves_through_its_character_card(screen):
    seat = view_hunter(screen)
    player = screen.state.player(seat)
    card = AbilityCards().for_player(screen.state, player)

    assert card is not None
    assert card.title == player.character.definition.ability_face.title


def test_piotreks_seat_resolves_to_the_skill_in_his_hand(screen):
    """Not a name lookup: the skill is a different card every time he draws."""
    seat = view_piotrek(screen)
    player = screen.state.player(seat)
    card = AbilityCards().for_player(screen.state, player)

    assert card is player.skill, \
        "Piotrek's panel must preview the skill he is actually holding"


def test_a_variant_is_resolved_before_the_ability_face(screen):
    """Order matters, and the live card is what makes it come free.

    ``GameState`` pushes a variant onto every live copy when it changes, so a
    resolver that starts from the live card is already reading the variant this
    match is playing — and takes ``ability_face`` afterwards, exactly as the
    Card Library does.
    """
    state = screen.state
    definition = next(d for d in state.library.deck(settings.DECK_CHARACTERS).cards
                      if d.has_variants)
    wanted = next(v.id for v in definition.variants
                  if v.id != state.card_variant(settings.DECK_CHARACTERS,
                                                definition.title))
    abilities = AbilityCards()
    before = abilities.for_character(state, definition.title)

    state.apply(cmd.SetCardVariant(deck_id=settings.DECK_CHARACTERS,
                                   title=definition.title, variant=wanted))
    after = abilities.for_character(state, definition.title)

    assert after is not None
    assert after.title == definition.ability_face.title, \
        "switching a variant lost the ability's own name"
    assert after.text != before.text, \
        "the resolver served a stale variant out of its cache"


# ═════════════════════════════════════════════════════════════════════════════
# 2. The character portrait previews the ABILITY
# ═════════════════════════════════════════════════════════════════════════════
def test_the_column_no_longer_draws_a_permanent_ability_card(screen):
    for size in REFERENCE_WINDOWS:
        layout = Layout(*size)
        for show_skill in (False, True):
            assert "card" not in layout.character_panel(show_skill), \
                f"the ability card slot is still in the column at {size}"


def test_hovering_a_hunter_portrait_previews_its_ability(screen):
    view_hunter(screen)
    expected = screen.ability_cards.for_player(
        screen.state, screen.state.player(screen.view_seat))

    frame(screen, mouse=portrait_rect(screen).center)
    assert screen.card_preview.rect is not None
    assert screen.card_preview.previewed is expected, \
        "the portrait previewed something other than this seat's ability"


def test_hovering_piotreks_portrait_previews_his_skill(screen):
    seat = view_piotrek(screen)
    frame(screen, mouse=portrait_rect(screen).center)

    assert screen.card_preview.previewed is screen.state.player(seat).skill


def test_the_portrait_preview_is_a_card_not_a_picture(screen):
    view_hunter(screen)
    frame(screen, mouse=portrait_rect(screen).center)
    preview = screen.card_preview.rect
    assert abs(preview.height / preview.width - (200 / 140)) < 0.08, \
        "the preview is not card-shaped, so it is still enlarging the portrait"


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_portrait_preview_stays_on_screen(size, library):
    local = build(library, size=size)
    view_hunter(local)
    rect = portrait_rect(local)
    frame(local, mouse=rect.center)

    preview = local.card_preview.rect
    assert preview is not None, f"no preview at {size}"
    assert not preview.colliderect(rect), f"the preview covers the portrait at {size}"
    assert local.app.layout.hover_preview_bounds.contains(preview), \
        f"the preview left the content area at {size}"


# ═════════════════════════════════════════════════════════════════════════════
# 3. Active Mody Patusa
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("art", [True, False])
def test_every_active_mod_previews(screen, art):
    card = put_mod(screen, 0, art=art)
    rect = screen.app.layout.mod_slot_rect(0)

    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None

    frame(screen, mouse=rect.center)
    assert screen.card_preview.previewed is card, \
        f"an active mod with art={art} did not preview its own card"


def test_the_mod_preview_follows_the_card_in_the_slot(screen):
    """It must stay correct when the rack changes mid-game."""
    first = put_mod(screen, 0, art=False)
    rect = screen.app.layout.mod_slot_rect(0)
    frame(screen, mouse=rect.center)
    assert screen.card_preview.previewed is first

    second = put_mod(screen, 0, art=True)
    assert second is not first
    frame(screen, mouse=rect.center)
    assert screen.card_preview.previewed is second, \
        "the preview served the mod that used to be in the slot"


def test_an_empty_mod_slot_previews_nothing(screen):
    screen.state.mod_slots[0] = None
    frame(screen, mouse=screen.app.layout.mod_slot_rect(0).center)
    assert screen.card_preview.rect is None


def test_the_mod_hover_uses_the_layout_rect(screen):
    """No hard-coded hover box: the target IS the slot the layout hands out."""
    put_mod(screen, 0, art=False)
    rect = screen.app.layout.mod_slot_rect(0)

    frame(screen, mouse=(rect.left - 30, rect.centery))
    assert screen.card_preview.rect is None, "the hover target is wider than the slot"
    frame(screen, mouse=rect.center)
    assert screen.card_preview.rect is not None


# ═════════════════════════════════════════════════════════════════════════════
# 4. The turn-order map
# ═════════════════════════════════════════════════════════════════════════════
def order_circles(screen: GameScreen):
    sequence = screen.state.turn_order()
    return list(zip(sequence, screen.app.layout.turn_circle_rects(len(sequence))))


def test_hovering_an_order_circle_previews_that_characters_ability(screen):
    hits = 0
    for slot, rect in order_circles(screen):
        if slot.is_piotrek:
            continue
        frame(screen, mouse=rect.center)
        expected = screen.ability_cards.for_character(screen.state, slot.name)
        assert expected is not None, slot.name
        assert screen.card_preview.previewed is expected, \
            f"{slot.name}'s circle previewed the wrong card"
        hits += 1
    assert hits >= 2, "the seeding should put several hunters in the order"


def test_the_order_map_is_generic_across_characters(screen):
    """Not hard-coded for one name: every hunter in the order resolves."""
    names = {slot.name for slot, _ in order_circles(screen) if not slot.is_piotrek}
    assert len(names) >= 3
    for name in names:
        assert screen.ability_cards.for_character(screen.state, name) is not None


def test_piotreks_order_circle_reveals_nothing(screen):
    """His skills are private; his character card has no fixed ability."""
    piotrek = [(s, r) for s, r in order_circles(screen) if s.is_piotrek]
    assert piotrek, "the seeding should place Piotrek in the order"
    for _, rect in piotrek:
        frame(screen, mouse=rect.center)
        assert screen.card_preview.rect is None, \
            "a Piotrek circle previewed a card"


def test_the_order_hover_is_a_circle_not_a_box(screen):
    """The circles sit close together; square targets would steal each other's."""
    slot, rect = next((s, r) for s, r in order_circles(screen) if not s.is_piotrek)
    frame(screen, mouse=(rect.left + 1, rect.top + 1))  # inside the rect corner
    assert screen.card_preview.rect is None, \
        "the corner of the bounding box counted as a hover"
    frame(screen, mouse=rect.center)
    assert screen.card_preview.rect is not None


def test_the_order_preview_and_the_portrait_agree(screen):
    """Two targets, one lookup — a character cannot read two ways."""
    seat = view_hunter(screen)
    name = screen.state.player(seat).display_character
    slot_rect = next(r for s, r in order_circles(screen) if s.name == name)

    frame(screen, mouse=portrait_rect(screen).center)
    from_portrait = screen.card_preview.previewed
    frame(screen, mouse=slot_rect.center)
    from_order = screen.card_preview.previewed

    assert from_portrait is from_order


# ═════════════════════════════════════════════════════════════════════════════
# 5. Hovering is presentation: no gameplay may happen
# ═════════════════════════════════════════════════════════════════════════════
def test_hovering_nothing_changes_the_game(screen):
    put_mod(screen, 0, art=True)
    state = screen.state
    before = (
        state.round_number, state.turn_slot,
        [len(p.hand) for p in state.players],
        [c.uses_left if c is not None else None
         for c in (p.character for p in state.players)],
        [s.uid if s is not None else None for s in state.mod_slots],
    )

    targets = [portrait_rect(screen).center,
               screen.app.layout.mod_slot_rect(0).center]
    targets += [r.center for _, r in order_circles(screen)]
    for target in targets:
        frame(screen, mouse=target)

    after = (
        state.round_number, state.turn_slot,
        [len(p.hand) for p in state.players],
        [c.uses_left if c is not None else None
         for c in (p.character for p in state.players)],
        [s.uid if s is not None else None for s in state.mod_slots],
    )
    assert before == after, "hovering mutated the game state"


def test_no_hover_target_emits_a_command(screen):
    """Hovering must never reach the Command path at all."""
    sent = []
    screen.submit = lambda command: sent.append(command)
    put_mod(screen, 0, art=False)
    for target in [portrait_rect(screen).center,
                   screen.app.layout.mod_slot_rect(0).center,
                   order_circles(screen)[1][1].center]:
        frame(screen, mouse=target)
    assert sent == []


# ═════════════════════════════════════════════════════════════════════════════
# 6. Text that fits: no artwork, and very long titles
# ═════════════════════════════════════════════════════════════════════════════
def rendered_lines(screen, card: Card, size):
    """How many description lines the fitter decided on for ``card``."""
    room_w = size[0] - 2 * (max(3, int(size[1] * 0.028)) + max(4, int(size[0] * 0.05)))
    font, lines = screen.cards._description_font(
        size, card.text, (room_w, size[1]), fraction=BODY_FRACTION,
    )
    return font, lines


def make_card(title: str, text: str) -> Card:
    return Card(CardDef(deck_id=settings.DECK_CHARACTERS, title=title, text=text))


def dark_rows(surface: pygame.Surface, threshold: int = 130):
    """Rows carrying INK, on a parchment card.

    Luminance rather than colour matching: the title and body inks are dark
    browns, and the parchment they sit on is close enough to them in RGB that a
    tolerance wide enough to catch the type also catches the card.  Dark-on-
    light is the actual distinction.

    The frame is cropped away first — the border is the darkest thing on the
    card and would mark every row.
    """
    inset = surface.get_rect().inflate(-int(surface.get_width() * 0.16),
                                       -int(surface.get_height() * 0.10))
    rows = set()
    for y in range(inset.top, inset.bottom):
        for x in range(inset.left, inset.right):
            pixel = surface.get_at((x, y))
            if (0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]) < threshold:
                rows.add(y)
                break
    return rows


@pytest.mark.parametrize("size", [(140, 200), (197, 282), (235, 336), (349, 498)])
def test_a_long_description_is_not_clipped_on_a_card_without_artwork(screen, size):
    """The bug: the end of the rules text was silently cut off.

    Every wrapped line the fitter produces must FIT — if the fitter has to trim
    even one, that trimming is exactly what the player never sees.
    """
    card = make_card("Radar", LONG_TEXT)
    assert not screen.cards.has_art(card), "this card must have no artwork"

    font, lines = rendered_lines(screen, card, size)
    whole = screen.app.renderer.wrap_lines(
        card.text, font,
        size[0] - 2 * (max(3, int(size[1] * 0.028)) + max(4, int(size[0] * 0.05))))
    assert lines == whole, f"the description was trimmed at {size}"


def test_the_description_ink_reaches_the_bottom_of_a_long_card(screen):
    """Measured on the painted face, not just on the fitter's arithmetic."""
    card = make_card("Radar", LONG_TEXT)
    face = screen.cards.face(card, (235, 336))

    rows = dark_rows(face)
    assert rows, "no description was painted at all"
    # The last line must be painted well down the card — a clipped description
    # stops early and leaves the lower third blank.
    assert max(rows) > face.get_height() * 0.78, \
        "the description stops short, which is what clipping looks like"


@pytest.mark.parametrize("title", [
    "Radar", "Zamiana Miejscami", LONG_TITLE,
    "Przerwanie Systemowe Poziomu Drugiego Awaryjnego",
])
def test_a_title_never_overflows_its_card(screen, title):
    """Short, medium and very long, on the same generic path.

    PARCHMENT ONLY.  ``dark_rows`` reads ink as "darker than the card", which
    is the right question on parchment and a meaningless one on a photograph —
    a Signature face is dark edge to edge, so it would mark every row and fail
    for a reason that has nothing to do with the title.  The guard is here
    because that is exactly how this test was wrong when first written: it
    parametrised "Granny Costume", which has had artwork since it was drawn.
    Titles above must stay ones with no file in ``assets/card_art``.
    """
    size = (235, 336)
    # NO BODY TEXT, so every mark on the card below the divider would be the
    # title having escaped its band.
    card = make_card(title, "")
    assert not screen.cards.has_art(card), (
        f"{title!r} has gained artwork; this test measures parchment faces, "
        "so pick another title or assert against the Signature title band")
    face = screen.cards.face(card, size)

    rows = dark_rows(face)
    assert rows, f"no title was painted for {title!r}"
    assert max(rows) < size[1] * 0.5, \
        f"the title {title!r} spilled out of its band"


def test_a_long_title_wraps_instead_of_being_truncated(screen):
    """It used to keep two lines and drop the rest, silently."""
    size = (235, 336)
    card = make_card(LONG_TITLE, "Krótki opis.")
    pad = max(3, int(size[1] * 0.028)) + max(4, int(size[0] * 0.05))
    font = screen.cards._title_font(size, LONG_TITLE, size[0] - 2 * pad)
    lines = screen.app.renderer.wrap_lines(LONG_TITLE, font, size[0] - 2 * pad)

    assert len(lines) <= TITLE_MAX_LINES, "the title still needs more lines than it has"
    assert " ".join(lines).split() == LONG_TITLE.split(), \
        "part of the title was dropped"


def test_a_title_that_already_fits_is_not_shrunk(screen):
    """No regression for the ordinary case: the loops exit at full size."""
    size = (235, 336)
    pad = max(3, int(size[1] * 0.028)) + max(4, int(size[0] * 0.05))
    plain = screen.cards._title_font(size, "Radar", size[0] - 2 * pad)
    reference = screen.cards._font(size, 0.068, bold=True)
    assert plain.get_height() == reference.get_height()


def test_a_short_description_is_not_shrunk(screen):
    """The body fitter must be a no-op when there was never a problem."""
    size = (235, 336)
    card = make_card("Radar", "Krótki opis.")
    font, _ = rendered_lines(screen, card, size)
    reference = screen.cards._font(size, 0.054)
    assert font.get_height() == reference.get_height()


def test_the_ability_preview_shows_a_complete_description_on_screen(screen):
    """End to end: hover a hunter's portrait, read the whole ability."""
    view_hunter(screen)
    card = screen.ability_cards.for_player(
        screen.state, screen.state.player(screen.view_seat))
    assert card is not None and not screen.cards.has_art(card), \
        "this seeding should give the hunter an unillustrated ability"

    frame(screen, mouse=portrait_rect(screen).center)
    preview = screen.card_preview.rect
    region = screen.app.canvas.subsurface(preview).copy()
    rows = dark_rows(region)

    assert rows, "the preview painted no description"
    assert max(rows) > preview.height * 0.55, \
        "the description stops in the upper half — it is being clipped"


# ═════════════════════════════════════════════════════════════════════════════
# 7. Regression
# ═════════════════════════════════════════════════════════════════════════════
def test_the_card_library_still_titles_abilities_correctly(screen):
    tab = next(t for t in screen.card_library.tabs if t.abilities)
    entry = next(e for e in tab.entries if e.owner == "Big D Randy")
    assert entry.card.title == "Granny Costume"
    assert entry.title == "Big D Randy"


def test_the_ability_button_still_reflects_the_engine(screen):
    """The button reads the live card, which the preview did not replace."""
    seat = view_hunter(screen)
    player = screen.state.player(seat)
    frame(screen)
    rect = screen.app.layout.ability_button_rect(False)

    assert screen.app.layout.right_panel.contains(rect)
    assert player.character.uses_total is not None


def test_one_preview_at_a_time(screen):
    """Four targets, one cursor, one request — nothing to unwind."""
    put_mod(screen, 0, art=False)
    view_hunter(screen)
    frame(screen, mouse=screen.app.layout.mod_slot_rect(0).center)
    first = screen.card_preview.previewed
    frame(screen, mouse=portrait_rect(screen).center)
    second = screen.card_preview.previewed
    assert first is not second
    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None


def test_the_interface_still_resolves_abilities_in_one_place():
    """No panel may grow its own copy of the character -> ability rule."""
    ui_dir = Path(__file__).resolve().parent.parent / "pedzacy_piotrek" / "ui"
    offenders = []
    for source in ui_dir.glob("*.py"):
        if source.name in {"ability_cards.py", "card_library.py"}:
            continue
        if ".ability_face" in source.read_text(encoding="utf-8"):
            offenders.append(source.name)
    assert not offenders, f"ability_face resolved outside the shared lookup: {offenders}"
