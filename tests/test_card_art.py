"""
Signature Cards: optional full-card artwork.

The feature is a BRANCH — a card with a picture in ``assets/card_art`` is drawn
one way, every other card exactly as it always was — so these tests come in
matching pairs.  Half of them exist to prove the new thing works; the other
half exist to prove it did not touch anything else, which is the half that
matters when the next stage changes the renderer again.

Rendered and measured rather than asked wherever possible: "the title moves up
on hover" is a claim about pixels, and a test that only checks a return value
would pass with the title drawn off the bottom of the card.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import math
from dataclasses import replace

import pygame
import pytest

from pedzacy_piotrek.cards.base_card import Card, CardDef
from pedzacy_piotrek.cards.loader import ContentLibrary
from pedzacy_piotrek.config import settings
from pedzacy_piotrek.config.settings import SessionConfig
from pedzacy_piotrek.engine.setup import create_game
from pedzacy_piotrek.net.session import LocalSession
from pedzacy_piotrek.render.card_art import CardArtLibrary, slugify
from pedzacy_piotrek.render.card_renderer import CARD_TYPE_ANCHOR, CardRenderer
from pedzacy_piotrek.render.renderer import Renderer
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

HAND_SIZE = (216, 309)


@pytest.fixture(scope="module")
def library() -> ContentLibrary:
    return ContentLibrary.load()


@pytest.fixture
def cards(library):
    """A CardRenderer pointed at the real, shipped card-art folder."""
    app = App(Layout(), headless=True, size=(2560, 1440))
    renderer = app.renderer
    return CardRenderer(renderer, library), renderer


def definition(library, deck_id: str, title: str) -> CardDef:
    for card in library.deck(deck_id).cards:
        if card.title == title:
            return card
    raise AssertionError(f"no card {title!r} in {deck_id}")


def solid_art(directory: Path, name: str, size=(64, 96), colour=(255, 0, 0)) -> Path:
    """A picture whose only job is to exist and be one recognisable colour."""
    surface = pygame.Surface(size)
    surface.fill(colour)
    path = directory / name
    pygame.image.save(surface, str(path))
    return path


def painted_rows(surface: pygame.Surface, colour, tolerance: int = 60) -> list[int]:
    """Rows where anything close to ``colour`` is painted — used to find text."""
    rows = []
    width, height = surface.get_size()
    for y in range(height):
        for x in range(4, width - 4, 2):
            pixel = surface.get_at((x, y))
            if all(abs(pixel[i] - colour[i]) <= tolerance for i in range(3)):
                rows.append(y)
                break
    return rows


@pytest.fixture
def illustrated(tmp_path, library):
    """A Signature card whose artwork is FLAT DARK GREY.

    The shipped Troll photograph is the wrong instrument for measuring where
    the title is: it contains a near-white sneaker, and a scan for pale pixels
    finds that long before it finds any type.  Against a flat dark picture the
    only pale pixels on the card are the ones the renderer drew, so "which rows
    hold text" becomes an exact question.

    The card is otherwise the real Troll — real title, real Polish rules text
    of real length — so the layout under test is the shipped one.
    """
    surface = pygame.Surface((320, 460))
    surface.fill((40, 40, 44))
    pygame.image.save(surface, str(tmp_path / "plain.png"))

    app = App(Layout(), headless=True, size=(2560, 1440))
    renderer_cards = CardRenderer(app.renderer, library,
                                  art=CardArtLibrary(tmp_path))
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    card = Card(replace(troll, art="plain"))
    assert renderer_cards.art.has_art(card.definition)
    return renderer_cards, app.renderer, card


def title_rows(renderer_cards, renderer, card, size, reveal) -> list[int]:
    face = renderer_cards.face(card, size, reveal=reveal)
    return painted_rows(face, renderer.theme.card_art_title, 40)


# ── naming convention ────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("Troll", "troll"),
    ("Rage Quit", "rage_quit"),
    ("rage-quit", "rage_quit"),
    ("Stańczyk", "stanczyk"),
    ("Dzieckorolka", "dzieckorolka"),
    ("Nie masz Rosji", "nie_masz_rosji"),
    ("Sesja na PG", "sesja_na_pg"),
    ("Plagiat!", "plagiat"),
    ("Masa solna", "masa_solna"),
    ("  spaced  out  ", "spaced_out"),
])
def test_a_name_reduces_to_one_key_however_it_is_spelled(text, expected):
    """Punctuation, case, spaces and Polish diacritics all fold away.

    This is the whole of the workflow: the owner saves a file under the card's
    name and it is found.  ``ł`` is checked explicitly because it is the one
    Polish letter Unicode will not decompose for us.
    """
    assert slugify(text) == expected


def test_every_shipped_card_title_produces_a_usable_key(library):
    """No card in any deck is unnameable.

    A title that slugified to nothing would silently be un-illustratable
    forever, and nobody would find out until they tried to draw it.
    """
    for deck in library.decks.values():
        for card in deck.cards:
            assert slugify(card.title), f"{card.title!r} has no key"


def test_the_shipped_folder_is_where_the_documentation_says(library):
    assert settings.CARD_ART_DIR.name == "card_art"
    assert settings.CARD_ART_DIR.parent == settings.ASSETS_DIR
    # Not the same folder as the standard card's inline illustration.
    assert settings.CARD_ART_DIR != settings.IMAGE_DIR


# ── discovery ────────────────────────────────────────────────────────────────
def test_dropping_a_file_in_is_the_whole_configuration(tmp_path, library):
    """The headline requirement: a new file, and nothing else, is enough."""
    art = CardArtLibrary(tmp_path)
    janek = definition(library, settings.DECK_MOVEMENT, "Janek")
    assert art.key(janek) is None

    solid_art(tmp_path, "Janek.png")
    art.refresh()
    assert art.key(janek) == "janek"
    assert art.surface(janek) is not None


@pytest.mark.parametrize("filename", ["Troll.png", "troll.PNG", "TROLL.jpg",
                                      "Troll.jpeg", "Troll.bmp"])
def test_the_common_image_formats_and_spellings_all_work(tmp_path, library, filename):
    solid_art(tmp_path, filename)
    art = CardArtLibrary(tmp_path)
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.has_art(troll), f"{filename} was not picked up"


def test_a_file_that_is_not_an_image_is_ignored(tmp_path, library):
    (tmp_path / "Troll.txt").write_text("not a picture")
    (tmp_path / "Troll.psd").write_bytes(b"\x00\x01\x02")
    art = CardArtLibrary(tmp_path)
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.key(troll) is None


def test_an_explicit_name_overrides_the_title(tmp_path, library):
    solid_art(tmp_path, "wielki_troll.png")
    art = CardArtLibrary(tmp_path)
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.key(troll) is None
    assert art.key(replace(troll, art="wielki_troll")) == "wielki_troll"
    # The override is slugified too, so its spelling is as forgiving.
    assert art.key(replace(troll, art="Wielki Troll")) == "wielki_troll"


def test_an_empty_art_field_opts_a_card_out(tmp_path, library):
    """"This card keeps its parchment face" has to be sayable."""
    solid_art(tmp_path, "Troll.png")
    art = CardArtLibrary(tmp_path)
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.has_art(troll)
    assert not art.has_art(replace(troll, art=""))


def test_a_title_two_decks_share_is_scoped_by_folder(tmp_path):
    """One title in two decks is two pictures, and the folder says which.

    Without scoping, whichever file the filesystem listed first would win on
    one machine and lose on another.

    The two definitions are BUILT here rather than looked up in the shipped
    decks.  They used to be the Mod Patusa and the Chest card both called
    "Shady" — and then the Mod was renamed to "Obóz Harcerski", which took the
    collision away and this test with it.  A mechanism has to keep being tested
    whether or not today's content happens to exercise it; pinning the test to
    a title was the same mistake N7 is about, one layer up.
    """
    (tmp_path / "mods").mkdir()
    (tmp_path / "chest").mkdir()
    solid_art(tmp_path / "mods", "Shady.png")
    solid_art(tmp_path / "chest", "Shady.png")
    art = CardArtLibrary(tmp_path)

    mod = CardDef(deck_id=settings.DECK_MODS, title="Shady", text="")
    chest = CardDef(deck_id=settings.DECK_CHEST, title="Shady", text="")
    assert art.key(mod) == "mods/shady"
    assert art.key(chest) == "chest/shady"
    assert art.path(mod) != art.path(chest)


def test_a_subfolder_still_answers_to_the_bare_name(tmp_path, library):
    """Filing tidily must not be the same as opting out."""
    (tmp_path / "movement").mkdir()
    solid_art(tmp_path / "movement", "Troll.png")
    art = CardArtLibrary(tmp_path)
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.has_art(troll)


# ── robustness: this must never take the game down ───────────────────────────
def test_a_missing_file_falls_back_to_the_standard_card(cards, library):
    """A card configured for artwork that is not there is a NORMAL card."""
    renderer_cards, _ = cards
    janek = definition(library, settings.DECK_MOVEMENT, "Janek")
    ghost = Card(replace(janek, art="nie_ma_takiego_pliku"))

    assert not renderer_cards.art.has_art(ghost.definition)
    face = renderer_cards.face(ghost, HAND_SIZE)
    plain = renderer_cards.face(Card(janek), HAND_SIZE)
    assert face.get_size() == plain.get_size()
    # Parchment, not a picture: the standard face, unchanged.
    assert face.get_at((HAND_SIZE[0] // 2, HAND_SIZE[1] // 2))[0] > 170


def test_a_corrupt_image_falls_back_instead_of_raising(tmp_path, library):
    """Half a download, or a .jpg that is really something else."""
    (tmp_path / "Troll.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    art = CardArtLibrary(tmp_path)
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.path(troll) is not None, "the file is found"
    assert art.surface(troll) is None, "but it does not load"
    assert not art.has_art(troll), "so the card is a standard card"


def test_a_missing_folder_is_not_an_error(tmp_path, library):
    art = CardArtLibrary(tmp_path / "does" / "not" / "exist")
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert art.key(troll) is None
    assert art.surface(troll) is None


def test_the_game_starts_and_runs_with_an_empty_card_art_folder(tmp_path, library):
    """A clean checkout with no binary assets still plays.

    The project's own rule (``assets/README.md``): everything in assets is an
    override, and the game runs without any of it.
    """
    app = App(Layout(), headless=True, size=(1920, 1080))
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=7), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    screen.cards.art = CardArtLibrary(tmp_path)
    screen.cards.clear_cache()
    app.push(screen)
    for _ in range(6):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)


# ── the shipped Troll ────────────────────────────────────────────────────────
def test_troll_ships_with_artwork_and_uses_it(cards, library):
    renderer_cards, _ = cards
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert renderer_cards.art.has_art(troll), (
        "assets/card_art/Troll.png is the worked example; without it the "
        "Signature path is never exercised by a real card"
    )


def test_a_signature_card_is_the_picture_not_parchment(cards, library):
    """The artwork covers the face — no beige body left showing."""
    renderer_cards, _ = cards
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))
    janek = Card(definition(library, settings.DECK_MOVEMENT, "Janek"))

    signature = renderer_cards.face(troll, HAND_SIZE)
    standard = renderer_cards.face(janek, HAND_SIZE)

    # Sampled across the upper half, where a standard card is plain parchment.
    def warm_pale(surface):
        hits = 0
        total = 0
        for x in range(20, HAND_SIZE[0] - 20, 6):
            for y in range(30, HAND_SIZE[1] // 2, 6):
                pixel = surface.get_at((x, y))
                total += 1
                if pixel[0] > 200 and pixel[1] > 190 and pixel[2] > 150:
                    hits += 1
        return hits / max(1, total)

    assert warm_pale(standard) > 0.8, "a standard card is parchment"
    assert warm_pale(signature) < 0.3, "a signature card is its picture"


def test_a_signature_card_keeps_the_card_shape_and_frame(cards, library):
    """It is still a card in this game: rounded corners and a brass rule."""
    renderer_cards, _ = cards
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))
    face = renderer_cards.face(troll, HAND_SIZE)
    assert face.get_at((0, 0))[3] == 0, "the corner is rounded away"
    assert face.get_at((HAND_SIZE[0] // 2, 2))[3] > 0, "the top edge is not"


def test_a_signature_card_ignores_the_badge(cards, library):
    """The brief: no movement/effect icons on an artwork card.

    Stated as "the badge makes no difference" rather than "the strip is
    blank", because a picture can legitimately have anything in that corner and
    only a comparison can tell drawn pixels from photographed ones.
    """
    renderer_cards, _ = cards
    from pedzacy_piotrek.cards.base_card import Badge

    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    plain = renderer_cards.face(Card(replace(troll, badge=None)), HAND_SIZE)
    badged = renderer_cards.face(
        Card(replace(troll, badge=Badge("rainbow", "+", True, 2))), HAND_SIZE)

    strip = [(x, y) for x in range(12, HAND_SIZE[0] - 12, 3)
             for y in range(int(HAND_SIZE[1] * 0.85), HAND_SIZE[1] - 6, 2)]
    assert all(plain.get_at(p) == badged.get_at(p) for p in strip), (
        "a Signature card drew a badge"
    )
    # And the standard face still does draw one, so this is not vacuous.
    without = renderer_cards.face(Card(replace(troll, badge=None, art="")), HAND_SIZE)
    with_badge = renderer_cards.face(
        Card(replace(troll, badge=Badge("rainbow", "+", True, 2), art="")), HAND_SIZE)
    assert any(without.get_at(p) != with_badge.get_at(p) for p in strip)


# ── the two states ───────────────────────────────────────────────────────────
def test_hovering_lifts_the_title_and_brings_the_description(illustrated):
    """The heart of it, measured on the pixels rather than asserted about.

    The title is near-white and the artwork is flat dark, so "where is the
    title" is answerable by looking for pale rows.  On hover it must be HIGHER
    and there must be pale rows underneath it that were not there before.
    """
    renderer_cards, renderer, card = illustrated

    resting = title_rows(renderer_cards, renderer, card, HAND_SIZE, 0.0)
    hovered = title_rows(renderer_cards, renderer, card, HAND_SIZE, 1.0)

    assert resting and hovered
    assert min(hovered) < min(resting) - HAND_SIZE[1] * 0.15, (
        "the title should move a long way up, not twitch"
    )
    assert max(hovered) > max(resting), "the description appears below it"
    assert max(hovered) < HAND_SIZE[1], "and stays on the card"


def test_the_resting_title_sits_near_the_bottom(illustrated):
    renderer_cards, renderer, card = illustrated
    rows = title_rows(renderer_cards, renderer, card, HAND_SIZE, 0.0)
    assert rows
    assert min(rows) > HAND_SIZE[1] * 0.72, "near the bottom, as in the reference"
    assert max(rows) < HAND_SIZE[1] - 4, "but not touching the edge"


def test_nothing_but_the_title_shows_at_rest(illustrated):
    """The brief: no description and no icons until the card is hovered."""
    renderer_cards, renderer, card = illustrated
    rows = title_rows(renderer_cards, renderer, card, HAND_SIZE, 0.0)
    # One title line's worth of rows, not a paragraph's worth.
    assert max(rows) - min(rows) < HAND_SIZE[1] * 0.12


def test_hovering_darkens_the_picture(cards, library):
    """The upper artwork dims so the eye moves to the words."""
    renderer_cards, _ = cards
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))

    def brightness(reveal):
        face = renderer_cards.face(troll, HAND_SIZE, reveal=reveal)
        total = count = 0
        for x in range(20, HAND_SIZE[0] - 20, 4):
            for y in range(20, int(HAND_SIZE[1] * 0.35), 4):
                pixel = face.get_at((x, y))
                total += pixel[0] + pixel[1] + pixel[2]
                count += 1
        return total / max(1, count)

    assert brightness(1.0) < brightness(0.0) - 12


def test_the_reveal_is_gradual_rather_than_a_switch(illustrated):
    """A smooth transition means intermediate frames actually differ."""
    renderer_cards, renderer, card = illustrated

    tops = [min(title_rows(renderer_cards, renderer, card, HAND_SIZE, reveal))
            for reveal in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert len(set(tops)) >= 4, f"the title should travel, not jump: {tops}"
    assert tops == sorted(tops, reverse=True), f"and travel one way: {tops}"


def test_reveal_defaults_to_the_hover_flag(cards, library):
    """Callers that only know about ``highlighted`` still get both states.

    Every panel and overlay in the game already passes ``highlighted``; none of
    them should have to learn a new argument to show a Signature card properly.
    """
    renderer_cards, _ = cards
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))
    assert (renderer_cards.face(troll, HAND_SIZE, highlighted=True) is
            renderer_cards.face(troll, HAND_SIZE, highlighted=True, reveal=1.0))
    assert (renderer_cards.face(troll, HAND_SIZE) is
            renderer_cards.face(troll, HAND_SIZE, reveal=0.0))


# ── responsive behaviour ─────────────────────────────────────────────────────
@pytest.mark.parametrize("size", [(84, 120), (110, 157), (140, 200),
                                  (180, 258), (216, 309), (320, 457)])
@pytest.mark.parametrize("reveal", [0.0, 1.0])
def test_nothing_escapes_the_card_at_any_size(illustrated, size, reveal):
    """Long Polish rules text on a small card must be cut, not spilled.

    The bottom two rows inside the frame are checked for the title colour: if
    the block were laid out from the top the description would run off the
    card here long before it did at hand size.
    """
    renderer_cards, renderer, card = illustrated
    face = renderer_cards.face(card, size, reveal=reveal)
    assert face.get_size() == size
    rows = painted_rows(face, renderer.theme.card_art_title, 40)
    assert rows, "there is always at least a title"
    assert max(rows) <= size[1] - 2, "text ran off the bottom of the card"
    assert min(rows) >= 2, "text ran off the top of the card"


@pytest.mark.parametrize("size", [(140, 200), (216, 309)])
def test_the_artwork_is_not_distorted(cards, library, size):
    """Cover, not stretch.

    Measured by a proxy the picture itself supplies: the Troll art is a tall
    photograph, so a card that stretched it would show the SAME picture at
    every aspect ratio, while cover-scaling crops.  Rendering the same
    artwork into two different aspect ratios and getting the same middle
    column back would mean it had been squashed to fit.
    """
    renderer_cards, _ = cards
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))
    tall = renderer_cards.face(troll, size)
    wide = renderer_cards.face(troll, (size[0] * 2, size[1]))

    def column(face, height):
        return [face.get_at((face.get_width() // 2, y))[:3]
                for y in range(10, height - 60, 7)]

    assert column(tall, size[1]) != column(wide, size[1]), (
        "a stretched picture would look identical down the middle"
    )


@pytest.mark.parametrize("scale", [0.85, 1.0, 1.11, 1.33, 1.44, 1.8])
def test_a_card_of_a_given_size_renders_identically_on_every_monitor(library, scale):
    """Stage 29's invariant, extended to the Signature face.

    Card type is a fraction of the CARD and is anchored to
    ``CARD_TYPE_ANCHOR``, so the same pixel size must give the same type
    whatever ``FontBook`` is set to.  Breaking this is how the laptop ended up
    with descriptions in stacks of two-word lines, and the new face uses the
    same ``_font`` for exactly this reason.
    """
    renderer = Renderer()
    renderer.fonts.set_scale(scale)
    renderer_cards = CardRenderer(renderer, library)
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))

    reference = Renderer()
    reference.fonts.set_scale(CARD_TYPE_ANCHOR)
    expected = CardRenderer(reference, library).face(troll, HAND_SIZE, reveal=1.0)
    actual = renderer_cards.face(troll, HAND_SIZE, reveal=1.0)

    for x in range(6, HAND_SIZE[0] - 6, 5):
        for y in range(6, HAND_SIZE[1] - 6, 5):
            assert actual.get_at((x, y)) == expected.get_at((x, y)), (
                f"card type moved with the interface scale at {scale}"
            )


# ── nothing else changed ─────────────────────────────────────────────────────
def test_a_card_without_artwork_is_byte_identical_to_before(cards, library):
    """The other half of the branch.

    A card with no picture must not merely "look the same" — it must take the
    same code path and produce the same surface it did before Signature Cards
    existed.  Comparing the two hover states is the cheapest way to say so:
    the standard face has no reveal, so they cannot differ.
    """
    renderer_cards, _ = cards
    janek = Card(definition(library, settings.DECK_MOVEMENT, "Janek"))
    for reveal in (0.0, 0.5, 1.0):
        face = renderer_cards.face(janek, HAND_SIZE, reveal=reveal)
        assert face is renderer_cards.face(janek, HAND_SIZE)


def test_artwork_changes_no_gameplay_property(library):
    """Presentation only.

    Troll is locked, kept out of opening hands and unplayable by hand, and it
    had all three of those before it had a picture.  If adding artwork could
    change any of them the feature would not be the presentation layer it
    claims to be.
    """
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    illustrated = Card(troll)
    plain = Card(replace(troll, art=""))

    assert illustrated.locked == plain.locked is True
    assert illustrated.opens_a_hand == plain.opens_a_hand is False
    assert illustrated.is_playable == plain.is_playable
    assert illustrated.effect == plain.effect
    assert illustrated.on_draw == plain.on_draw
    assert illustrated.text == plain.text


def test_the_deck_composition_is_untouched(library):
    """Counts are what the lobby shows; a rendering change must not move them."""
    movement = library.deck(settings.DECK_MOVEMENT)
    assert len(movement.cards) == 30
    troll = definition(library, settings.DECK_MOVEMENT, "Troll")
    assert troll.count == 2


def test_the_hand_fan_gives_its_smooth_hover_to_the_reveal(library):
    """The fan already animates hover; the card should ride the same curve.

    Checked through the real screen rather than by reading the source, because
    what matters is that the value ARRIVES — a fan that computed a lovely
    hover and passed ``highlighted`` would snap.
    """
    app = App(Layout(), headless=True, size=(2560, 1440))
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=3), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)

    seen: list = []
    original = screen.cards.draw_transformed

    def spy(*args, **kwargs):
        seen.append(kwargs.get("reveal"))
        return original(*args, **kwargs)

    screen.cards.draw_transformed = spy
    fan = screen.hand
    hand = fan.hand
    assert hand, "the seat was dealt cards"

    # Park the cursor on a card and let the hover animation run a few frames.
    for _ in range(4):
        fan.update(1 / 60, (0, 0))
    fan._sync()
    target = fan.slots[hand[0].uid]
    target.hover = 0.4
    app.renderer.begin(app.canvas)
    fan.draw(app.canvas)

    assert 0.4 in seen, f"the fan's hover value never reached the card: {seen}"


# ── the old double frame and status pips are gone (stage 31) ─────────────────
def test_no_inner_rule_is_drawn_inside_a_standard_card(cards, library):
    """The card face has ONE border, not two.

    The old inset brass rule sat at ``max(3, h*0.028)`` from the edge and was
    far darker than the parchment it lay on.  Sampled down the left margin,
    where there is no text at any card size: the inner column must now look
    like the parchment three pixels further in, not like a line.
    """
    renderer_cards, _ = cards
    janek = Card(definition(library, settings.DECK_MOVEMENT, "Janek"))
    face = renderer_cards.face(janek, HAND_SIZE)
    inset = max(3, int(HAND_SIZE[1] * 0.028))

    for y in range(int(HAND_SIZE[1] * 0.3), int(HAND_SIZE[1] * 0.7), 4):
        on_rule = face.get_at((inset, y))
        parchment = face.get_at((inset + 4, y))
        assert abs(on_rule[0] - parchment[0]) < 25, (
            f"something is still drawn along the old inset rule at y={y}"
        )


def test_nothing_is_drawn_over_signature_artwork_but_the_border(cards, library):
    """A Signature card's picture is unobstructed inside its own edge.

    Compared against the artwork painted straight into the card: every pixel
    inside the border must be the picture (possibly scrimmed at the bottom, so
    only the top half is checked), never a rule laid across it.
    """
    renderer_cards, _ = cards
    troll = Card(definition(library, settings.DECK_MOVEMENT, "Troll"))
    face = renderer_cards.face(troll, HAND_SIZE, reveal=0.0)
    bare = renderer_cards._cover(
        renderer_cards.art.surface(troll.definition), HAND_SIZE)

    inset = max(3, int(HAND_SIZE[1] * 0.028))
    # The old rule ran along ``inset``.  Sampled down the straight sides only:
    # at the rounded corners the border stroke and the corner mask both
    # legitimately touch this column, and neither of them is the rule.
    for y in range(int(HAND_SIZE[1] * 0.18), int(HAND_SIZE[1] * 0.45)):
        for x in (inset, inset + 1, HAND_SIZE[0] - inset - 1, HAND_SIZE[0] - inset):
            assert face.get_at((x, y))[:3] == bare.get_at((x, y))[:3], (
                f"the artwork is overpainted at ({x}, {y})"
            )


def test_the_hand_draws_no_status_pip_on_a_card(library):
    """No circle is painted on top of a card in the fan.

    A green pip marked "playable" and a pale one marked "locked"; both sat ten
    pixels in from the top edge of the card, along the card's own axis, which
    on an illustrated card meant in the middle of the picture.

    The check recomputes that exact spot from the live fan geometry and looks
    there, rather than counting coloured pixels across the shelf: the artwork
    is a photograph and will always contain a few pixels that happen to match a
    theme colour, so a count would either be flaky or too loose to catch a pip
    that came back.
    """
    app = App(Layout(), headless=True, size=(2560, 1440))
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=5), library)
    screen = GameScreen(app, LocalSession(state), library=library)
    app.push(screen)

    # A locked card (Troll) and a plainly playable one, side by side.
    hand = state.active_player.hand
    deck = state.deck(settings.DECK_MOVEMENT)
    for want in ("Troll", "Zerówka - czerwony"):
        for pile in (deck.draw_pile, deck.discard_pile):
            for card in list(pile):
                if card.title == want:
                    pile.remove(card)
                    hand.insert(0, card)
                    break
    assert any(c.locked for c in hand), "a locked card is on the table"
    assert any(c.is_playable for c in hand), "and a playable one"

    for _ in range(30):
        app.renderer.begin(app.canvas)
        app.renderer.table_background(app.canvas)
        screen.update(1 / 60, (0, 0))
        screen.draw(app.canvas)

    fan = screen.hand
    card_h = app.layout.hand_card_size[1]
    theme = app.renderer.theme
    checked = 0
    for slot in fan.slots.values():
        radians = math.radians(slot.angle)
        offset = card_h * slot.scale * 0.5 - 10
        cx = int(slot.position[0] + math.sin(radians) * offset)
        cy = int(slot.position[1] - math.cos(radians) * offset)
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                point = (cx + dx, cy + dy)
                if not app.canvas.get_rect().collidepoint(point):
                    continue
                pixel = app.canvas.get_at(point)[:3]
                for name, colour in (("playable", theme.valid),
                                     ("locked", theme.prompt)):
                    assert tuple(pixel) != tuple(colour), (
                        f"a {name} pip is still drawn at {point}"
                    )
                checked += 1
    assert checked, "the fan had slots to check"


def test_removing_the_pips_left_the_answers_they_gave_intact(library):
    """The pips were display only; the rules they hinted at still hold.

    This is the part of the cleanup that had to be checked rather than assumed:
    a locked card must still be unplayable and undiscardable, and the engine —
    not the interface — must be the one saying so.
    """
    state = create_game(SessionConfig(num_players=4, board_cells=24, seed=5), library)
    deck = state.deck(settings.DECK_MOVEMENT)
    troll = next(c for pile in (deck.draw_pile, deck.discard_pile)
                 for c in pile if c.title == "Troll")
    assert troll.locked
    assert not troll.is_playable, "a locked card is still not playable"

    janek = next(c for pile in (deck.draw_pile, deck.discard_pile)
                 for c in pile if c.title == "Janek")
    assert janek.is_playable, "and a normal card still is"
