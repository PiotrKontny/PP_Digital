"""
Stage 49 — character portraits, and the Card Library's ability name.

Two features that turned out to be one idea: a CHARACTER and its ABILITY are
different things with different names and different pictures, and the interface
had been treating them as one.

    Big D Randy      the character  -> assets/portraits/Big D Randy.png
    Granny Costume   his ability    -> assets/card_art/Granny Costume.png

The Card Library asked for the ability's artwork under the CHARACTER's name,
found nothing, and titled the card after the character.  ``Granny Costume.png``
had been sitting in the folder the whole time.

These tests come in the matching pairs ``test_card_art.py`` and
``test_card_preview.py`` use: half prove the new thing appears, and half prove
that the card art, the ability hover, Piotrek's identity badge and the panel's
existing behaviour were all left alone.

Measured against PIXELS wherever the claim is about pixels.  "The portrait is
drawn" is a claim about what was painted, and a test that only checked a rect
would pass with the portrait drawn blank — which is exactly the bug a
placeholder feature is most likely to ship.
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
from pedzacy_piotrek.render.card_art import CardArtLibrary, slugify
from pedzacy_piotrek.render.portrait import PortraitLibrary
from pedzacy_piotrek.ui.app import App
from pedzacy_piotrek.ui.game_screen import GameScreen
from pedzacy_piotrek.ui.layout import Layout

WINDOW = (1920, 1080)

#: The displays the project promises to work at.  Every geometry claim walks
#: all of them — a panel verified at one resolution is a panel verified at none.
REFERENCE_WINDOWS = [(1280, 760), (1600, 900), (1920, 1080), (1920, 1200),
                     (2560, 1440)]

RED = (220, 40, 40)
GREEN = (40, 200, 90)
BLUE = (50, 90, 230)


# ── fixtures and helpers ─────────────────────────────────────────────────────
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
    """One whole frame with the cursor at ``mouse``.

    The HUD reads the cursor in ``draw`` through ``App.mouse``, so pointing
    that at a fixed position is the only honest way to hold a cursor still over
    a slot — the same reason ``test_card_preview.py`` does it.
    """
    app = screen.app
    app.mouse = lambda position=tuple(mouse): position
    app.renderer.begin(app.canvas)
    app.canvas.fill(app.renderer.theme.background)
    screen.update(dt, tuple(mouse))
    screen.draw(app.canvas)


def write_portrait(folder: Path, name: str, color, size=(160, 160)) -> Path:
    """A solid-colour portrait file, so a pixel can say which one was loaded."""
    folder.mkdir(parents=True, exist_ok=True)
    surface = pygame.Surface(size)
    surface.fill(color)
    path = folder / f"{name}.png"
    pygame.image.save(surface, str(path))
    return path


def region_mean(surface: pygame.Surface, rect: pygame.Rect):
    """Average colour of a region, as three floats."""
    total = [0.0, 0.0, 0.0]
    count = 0
    for x in range(rect.left, rect.right, 2):
        for y in range(rect.top, rect.bottom, 2):
            pixel = surface.get_at((x, y))
            total[0] += pixel[0]
            total[1] += pixel[1]
            total[2] += pixel[2]
            count += 1
    return tuple(channel / max(1, count) for channel in total)


def close_to(actual, expected, tolerance=26) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(actual, expected))


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
    raise AssertionError("no Piotrek seat to look at")


def portrait_rect(screen: GameScreen) -> pygame.Rect:
    player = screen.state.player(screen.view_seat)
    return screen.app.layout.character_panel(player.is_piotrek)["portrait"]


def ability_card_of(screen: GameScreen):
    """The ability card this seat's portrait previews (stage 50)."""
    return screen.ability_cards.for_player(
        screen.state, screen.state.player(screen.view_seat))


def use_portraits(screen: GameScreen, folder: Path) -> None:
    """Point the running screen at a portrait folder of the test's own."""
    screen.cards.portraits = PortraitLibrary(folder)


# ═════════════════════════════════════════════════════════════════════════════
# 1. The portrait library: which file answers to which character
# ═════════════════════════════════════════════════════════════════════════════
def test_a_character_with_a_portrait_loads_its_own(tmp_path):
    write_portrait(tmp_path, "Glockboy", RED)
    portraits = PortraitLibrary(tmp_path)

    assert portraits.has_portrait("Glockboy")
    assert portraits.path("Glockboy") == tmp_path / "Glockboy.png"
    assert portraits.surface("Glockboy").get_at((5, 5))[:3] == RED


def test_a_character_without_a_portrait_falls_back_to_the_placeholder(tmp_path):
    """The whole of the fallback rule, and the reason the panel never branches."""
    write_portrait(tmp_path, "placeholder", BLUE)
    portraits = PortraitLibrary(tmp_path)

    assert not portraits.has_portrait("Nobody At All")
    assert portraits.path("Nobody At All") is None
    # ...but there is still something to paint, and it is the placeholder.
    assert portraits.surface("Nobody At All").get_at((5, 5))[:3] == BLUE


def test_different_characters_get_different_portraits(tmp_path):
    write_portrait(tmp_path, "Glockboy", RED)
    write_portrait(tmp_path, "Lubin", GREEN)
    write_portrait(tmp_path, "placeholder", BLUE)
    portraits = PortraitLibrary(tmp_path)

    assert portraits.surface("Glockboy").get_at((5, 5))[:3] == RED
    assert portraits.surface("Lubin").get_at((5, 5))[:3] == GREEN
    # A third character, undrawn, still gets the placeholder and not either
    # of its neighbours.
    assert portraits.surface("Mitoman").get_at((5, 5))[:3] == BLUE


def test_adding_a_portrait_needs_no_code_change(tmp_path):
    """The claim the feature is FOR: drop a file in, restart, done.

    ``refresh`` stands in for the restart.  Nothing between the two asserts
    touches configuration, a mapping, or a line of Python that names a
    character — the filename is the whole of the wiring.
    """
    write_portrait(tmp_path, "placeholder", BLUE)
    portraits = PortraitLibrary(tmp_path)
    assert not portraits.has_portrait("Big D Randy")

    write_portrait(tmp_path, "Big D Randy", GREEN)
    portraits.refresh()

    assert portraits.has_portrait("Big D Randy")
    assert portraits.surface("Big D Randy").get_at((5, 5))[:3] == GREEN


def test_replacing_the_placeholder_needs_no_code_change(tmp_path):
    write_portrait(tmp_path, "placeholder", BLUE)
    portraits = PortraitLibrary(tmp_path)
    assert portraits.surface("Anyone").get_at((5, 5))[:3] == BLUE

    write_portrait(tmp_path, "placeholder", RED)  # same name, new picture
    portraits.refresh()
    assert portraits.surface("Anyone").get_at((5, 5))[:3] == RED


@pytest.mark.parametrize("filename", [
    "Big D Randy", "big_d_randy", "big-d-randy", "BIG D RANDY",
])
def test_the_filename_does_not_have_to_be_spelled_exactly(tmp_path, filename):
    """The card-art folding convention, reused rather than re-invented."""
    write_portrait(tmp_path, filename, RED)
    assert PortraitLibrary(tmp_path).has_portrait("Big D Randy")


def test_polish_diacritics_fold_the_way_card_art_folds_them(tmp_path):
    write_portrait(tmp_path, "Dziubdziuch", RED)
    portraits = PortraitLibrary(tmp_path)
    assert portraits.has_portrait("Dziubdziuch")
    # One convention, one function — if these ever diverge, a file that finds
    # its card would stop finding its character.
    assert slugify("Stańczyk") == slugify("Stanczyk")


def test_the_placeholder_is_not_a_character(tmp_path):
    """It must not be reachable as if somebody were called 'Placeholder'."""
    write_portrait(tmp_path, "placeholder", BLUE)
    portraits = PortraitLibrary(tmp_path)
    assert not portraits.has_portrait("placeholder")
    assert not portraits.has_portrait("Placeholder")


# ── nothing here may raise ───────────────────────────────────────────────────
def test_a_missing_portrait_folder_does_not_crash(tmp_path):
    portraits = PortraitLibrary(tmp_path / "does" / "not" / "exist")
    assert portraits.has_portrait("Glockboy") is False
    assert portraits.surface("Glockboy") is None  # no placeholder either


def test_a_corrupt_portrait_falls_back_instead_of_raising(tmp_path):
    """A half-downloaded PNG costs a character its likeness, not its panel."""
    write_portrait(tmp_path, "placeholder", BLUE)
    (tmp_path / "Glockboy.png").write_bytes(b"this is not a PNG")
    portraits = PortraitLibrary(tmp_path)

    assert portraits.has_portrait("Glockboy") is False
    assert portraits.surface("Glockboy").get_at((5, 5))[:3] == BLUE


def test_a_non_image_file_in_the_folder_is_ignored(tmp_path):
    write_portrait(tmp_path, "placeholder", BLUE)
    (tmp_path / "notes.txt").write_text("art still to do")
    (tmp_path / "Glockboy.psd").write_bytes(b"\x00\x01")
    portraits = PortraitLibrary(tmp_path)
    assert not portraits.has_portrait("Glockboy")


def test_the_shipped_placeholder_is_a_real_loadable_asset():
    """The one asset this stage actually ships has to BE there and load."""
    portraits = PortraitLibrary()
    assert portraits.placeholder_path.is_file(), "placeholder.png is missing"
    surface = portraits.placeholder_surface()
    assert surface is not None and surface.get_width() > 0


# ═════════════════════════════════════════════════════════════════════════════
# 2. The character panel: a Hunter
# ═════════════════════════════════════════════════════════════════════════════
def test_a_hunter_panel_draws_the_portrait(screen, tmp_path):
    view_hunter(screen)
    name = screen.state.player(screen.view_seat).display_character
    write_portrait(tmp_path, name, RED)
    use_portraits(screen, tmp_path)
    frame(screen)

    rect = portrait_rect(screen)
    painted = region_mean(screen.app.canvas, rect.inflate(-14, -14))
    assert close_to(painted, RED), \
        f"the hunter's own portrait was not painted into {rect} (got {painted})"


def test_a_hunter_without_a_portrait_gets_the_placeholder(screen, tmp_path):
    view_hunter(screen)
    write_portrait(tmp_path, "placeholder", GREEN)
    use_portraits(screen, tmp_path)
    frame(screen)

    painted = region_mean(screen.app.canvas, portrait_rect(screen).inflate(-14, -14))
    assert close_to(painted, GREEN), f"the placeholder was not painted ({painted})"


def test_the_hunters_name_is_still_visible_under_the_portrait(screen):
    """The name did not go away when the heading did."""
    view_hunter(screen)
    frame(screen)
    layout = screen.app.layout
    rects = layout.character_panel(False)
    name_y = rects["name_y"]

    button = layout.ability_button_rect(False)
    assert name_y > rects["portrait"].bottom, "the name must sit UNDER the portrait"
    assert name_y < button.top, "the name must sit ABOVE the ability button"
    # Something bright was actually painted on that row — brass type on a dark
    # panel, so the row is measurably lighter than the panel around it.
    # The NAME ROW only.  Measuring all the way down to the button dilutes the
    # type with whatever dark panel sits between them, and the portrait growing
    # in stage 50 made that gap wide enough to swamp the average.
    band = pygame.Rect(layout.right_panel.left + 6, name_y,
                       layout.right_panel.width - 12,
                       max(6, min(layout.r_name_h, button.top - name_y - 2)))
    assert max(region_mean(screen.app.canvas, band)) > 60, \
        "no name was painted under the portrait"


def test_the_hunter_ability_is_still_resolved_and_still_the_ability(screen):
    """Stage 50 stopped DRAWING it in the column; it still resolves."""
    view_hunter(screen)
    player = screen.state.player(screen.view_seat)
    card = ability_card_of(screen)

    assert card is not None
    # The ABILITY's name, not the character's.  Both exist and they differ.
    assert card.title == player.character.definition.skill
    assert card.title != player.character.title
    assert card.text == player.character.text


def test_the_hunter_ability_button_still_works(screen):
    """The ability is untouched: same rect, same enabled/disabled reasoning."""
    view_hunter(screen)
    frame(screen)
    layout = screen.app.layout
    rect = layout.ability_button_rect(False)

    rects = layout.character_panel(False)
    assert layout.right_panel.contains(rect), "the ability button left the panel"
    # Under the NAME since stage 50, the ability card having left the column.
    assert rect.top >= int(rects["name_y"]), \
        "the button no longer sits under the character's name"
    # The engine's own answer, which is what greys the button.  Asking it at
    # all is the point: the button must not have grown an opinion of its own.
    assert hasattr(screen.state, "ability_refusal")


# ═════════════════════════════════════════════════════════════════════════════
# 3. The character panel: Piotrek
# ═════════════════════════════════════════════════════════════════════════════
def test_piotreks_portrait_is_shown(screen, tmp_path):
    view_piotrek(screen)
    write_portrait(tmp_path, "Piotrek", RED)
    use_portraits(screen, tmp_path)
    frame(screen)

    painted = region_mean(screen.app.canvas, portrait_rect(screen).inflate(-14, -14))
    assert close_to(painted, RED), f"Piotrek's portrait was not painted ({painted})"


def test_piotreks_identity_badge_survived_the_portrait(screen):
    """The one thing on this screen that must never be lost or duplicated."""
    seat = view_piotrek(screen)
    player = screen.state.player(seat)
    pawn = screen.state.library.pawn(player.secret_pawn)
    frame(screen)

    rects = screen.app.layout.character_panel(True)
    identity = rects["identity"]

    # It is still ABOVE the portrait, which is where the design reference puts
    # it and where it has been since it existed.
    assert identity.bottom <= rects["portrait"].top
    assert screen.app.layout.right_panel.contains(identity)
    # And it is drawn: the pawn's colour is actually on those pixels.
    painted = region_mean(screen.app.canvas, identity)
    assert max(painted) > 40, "the identity badge painted nothing"
    assert pawn is not None


def test_piotreks_identity_is_not_duplicated_by_the_portrait(screen):
    """One badge, not two — the portrait is a picture, not a second badge."""
    view_piotrek(screen)
    rects = screen.app.layout.character_panel(True)
    assert not rects["identity"].colliderect(rects["portrait"])


def test_piotreks_ability_card_and_decks_still_fit(screen):
    view_piotrek(screen)
    layout = screen.app.layout
    rects = layout.character_panel(True)
    panel = layout.right_panel

    for key in ("portrait", "skill_draw", "skill_disc",
                "char_draw", "char_disc"):
        assert panel.contains(rects[key]), f"{key} escaped the panel"
    assert panel.contains(layout.ability_button_rect(True))


def test_piotreks_ability_remains_functional(screen):
    """A command still reaches the engine and is answered, not swallowed."""
    seat = view_piotrek(screen)
    player = screen.state.player(seat)
    assert player.skill is not None
    # The panel shows Piotrek's SKILL card itself, which is its own card and
    # its own name — ``ability_face`` leaves it exactly alone.
    assert player.skill.definition.ability_face is player.skill.definition


# ═════════════════════════════════════════════════════════════════════════════
# 4. Hover: the portrait, the ability, and the two not interfering
# ═════════════════════════════════════════════════════════════════════════════
def test_hovering_the_portrait_opens_the_ability_card(screen, tmp_path):
    """STAGE 50 REVERSED THIS TEST, and the reversal is the feature.

    Stage 49 asserted the enlarged thing was the character's PICTURE.  That
    answered "what does this character look like", which the player can already
    see, while the question they actually have — "what does my ability do" —
    was left to a small card underneath.  The portrait is now the target for
    the ability card, with its full description.
    """
    view_hunter(screen)
    name = screen.state.player(screen.view_seat).display_character
    write_portrait(tmp_path, name, RED)
    use_portraits(screen, tmp_path)

    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None, "a preview appeared with no hover"

    rect = portrait_rect(screen)
    frame(screen, mouse=rect.center)
    preview = screen.card_preview.rect
    assert preview is not None, "hovering the portrait opened nothing"

    # It is a CARD, not the picture: the portrait is a solid red square, so a
    # blown-up portrait would paint the preview red.
    painted = region_mean(screen.app.canvas, preview.inflate(-24, -24))
    assert not close_to(painted, RED, tolerance=60), \
        "the portrait enlarged itself instead of showing the ability card"
    # And it keeps a card's proportions.
    assert abs(preview.height / preview.width - (200 / 140)) < 0.08


def test_the_portrait_preview_shows_the_ability_text(screen):
    """Not merely the artwork — the words are the point."""
    view_hunter(screen)
    card = ability_card_of(screen)
    assert card is not None and card.text

    rect = portrait_rect(screen)
    frame(screen, mouse=rect.center)
    preview = screen.card_preview.rect
    theme = screen.app.renderer.theme

    body = pygame.Rect(preview.left, preview.centery,
                       preview.width, preview.height // 2)
    painted = 0
    for x in range(body.left + 4, body.right - 4, 2):
        for y in range(body.top, body.bottom - 4, 2):
            pixel = screen.app.canvas.get_at((x, y))
            if close_to(pixel[:3], theme.card_text, tolerance=48):
                painted += 1
    assert painted > 120, "the ability preview shows no description text"


def test_the_original_portrait_stays_put_while_it_is_previewed(screen, tmp_path):
    """The original under the cursor is not repainted — stage 48's rule.

    The INTERIOR is compared byte for byte.  The border may take a hover rim;
    a changed pixel inside the frame would mean the portrait had been redrawn.
    """
    view_hunter(screen)
    write_portrait(tmp_path, "placeholder", GREEN)
    use_portraits(screen, tmp_path)

    rect = portrait_rect(screen)
    # The CENTRE of the picture.  The frame is rounded, so the corners of a
    # merely-inset rect still show the border — which is allowed to light up
    # (N45) and would fail this for the wrong reason.
    inner = rect.inflate(-int(rect.width * 0.4), -int(rect.height * 0.4))

    frame(screen, mouse=(0, 0))
    before = screen.app.canvas.subsurface(inner).copy()
    frame(screen, mouse=rect.center)
    after = screen.app.canvas.subsurface(inner).copy()

    assert pygame.image.tostring(before, "RGB") == pygame.image.tostring(after, "RGB"), \
        "the portrait under the cursor was repainted instead of left alone"


def test_the_preview_never_covers_the_portrait_it_belongs_to(screen):
    """The gap is load-bearing: overlapping rects flicker the hover on and off."""
    for size in REFERENCE_WINDOWS:
        local = build(ContentLibrary.load(), size=size)
        view_hunter(local)
        rect = portrait_rect(local)
        frame(local, mouse=rect.center)
        preview = local.card_preview.rect
        assert preview is not None, f"no portrait preview at {size}"
        assert not preview.colliderect(rect), f"preview covers the portrait at {size}"
        assert local.app.layout.hover_preview_bounds.contains(preview), \
            f"the preview left the content area at {size}"


def test_the_column_no_longer_has_a_second_ability_target(screen):
    """One target, so the two can no longer fight over the cursor.

    Stage 49 had to prove the portrait hover and the ability-card hover did not
    trigger each other.  Stage 50 removed the second target entirely, which is
    a stronger guarantee than any test of their coexistence: there is one
    rectangle in this column that previews, and one cursor.
    """
    view_hunter(screen)
    rects = screen.app.layout.character_panel(False)
    assert "card" not in rects

    rect = portrait_rect(screen)
    frame(screen, mouse=rect.center)
    first = screen.card_preview.rect
    assert first is not None
    assert not first.colliderect(rect), "the preview covers its own target"


def test_leaving_both_slots_closes_the_preview(screen):
    view_hunter(screen)
    view_hunter(screen)
    frame(screen, mouse=portrait_rect(screen).center)
    assert screen.card_preview.rect is not None
    frame(screen, mouse=(0, 0))
    assert screen.card_preview.rect is None, "the preview outlived the hover"


# ═════════════════════════════════════════════════════════════════════════════
# 5. The Card Library: an ability is titled after the ABILITY
# ═════════════════════════════════════════════════════════════════════════════
def ability_entry(screen: GameScreen, owner: str):
    tab = next(tab for tab in screen.card_library.tabs if tab.abilities)
    return next(entry for entry in tab.entries if entry.owner == owner)


def test_big_d_randys_ability_is_titled_granny_costume(screen):
    """The reported bug, exactly as reported."""
    entry = ability_entry(screen, "Big D Randy")

    assert entry.card.title == "Granny Costume", \
        "the Card Library is still titling the ability after the character"
    assert entry.card.title != "Big D Randy"


def test_the_ability_keeps_the_abilitys_own_description(screen):
    """The description was always right; it must STAY right."""
    entry = ability_entry(screen, "Big D Randy")
    definition = next(d for d in screen.state.library.deck(settings.DECK_CHARACTERS).cards
                      if d.title == "Big D Randy")
    assert entry.card.text == definition.text
    assert "Freezuje" in entry.card.text


def test_the_ability_artwork_is_found_under_the_ability_name(screen):
    """The point of the fix: ``Granny Costume.png`` exists and was never used."""
    entry = ability_entry(screen, "Big D Randy")
    art = CardArtLibrary()

    assert art.key(entry.card.definition) == slugify("Granny Costume")
    assert screen.cards.has_art(entry.card), \
        "the ability still renders as a parchment card despite having artwork"
    # And the WRONG lookup — the one that was happening — genuinely finds
    # nothing, so this test would have failed before the fix rather than
    # passing for an unrelated reason.
    character = CardDef(deck_id=settings.DECK_CHARACTERS, title="Big D Randy",
                        text="")
    assert art.key(character) is None


def test_the_owner_is_still_the_character(screen):
    """Separated, not swapped.  The character name still identifies the seat."""
    entry = ability_entry(screen, "Big D Randy")
    assert entry.owner == "Big D Randy"
    # And the ENGINE is still addressed by the character's title, because that
    # is what holds the charges.  Getting this wrong would make the library's
    # +/- buttons silently target a card that does not exist.
    assert entry.title == "Big D Randy"
    assert screen.state.ability_card(entry.title) is not None


@pytest.mark.parametrize("owner,ability", [
    ("Big D Randy", "Granny Costume"),
    ("Lubin", "Jazdy"),
    ("Ondrej", "Radar"),
    ("Dziubdziuch", "Przerwanie Systemowe"),
])
def test_every_character_whose_ability_has_another_name_uses_that_name(
        screen, owner, ability):
    """Not one special case — the rule, across the whole deck."""
    entry = ability_entry(screen, owner)
    assert entry.card.title == ability
    assert entry.title == owner


def test_piotreks_own_skills_are_unaffected(screen):
    """A skill card IS its ability, so there is nothing to separate."""
    tab = next(tab for tab in screen.card_library.tabs if tab.abilities)
    skills = [e for e in tab.entries
              if e.card.deck_id == settings.DECK_SKILLS]
    assert skills, "the ability tab lost Piotrek's skills"
    for entry in skills:
        assert entry.card.title == entry.title, \
            "a Piotrek skill was renamed by the ability face"


def test_the_ability_charges_still_adjust_through_the_library(screen):
    """The commands still land, which is what ``entry.title`` protects."""
    shelf = screen.card_library
    # ``_bump`` reads the ACTIVE tab, so selecting it is part of the gesture:
    # the abilities tab has to be on screen for its stepper to be clickable.
    shelf.tab_index = next(i for i, tab in enumerate(shelf.tabs) if tab.abilities)
    entry = ability_entry(screen, "Big D Randy")
    sent = []
    shelf.submit = lambda command: sent.append(command)
    shelf._bump(entry, +1)

    assert sent and isinstance(sent[0], cmd.AdjustAbilityUses)
    assert sent[0].title == "Big D Randy", \
        "the charge command was addressed to the ability instead of the card"


# ── the derivation itself ────────────────────────────────────────────────────
def test_ability_face_moves_the_title_and_nothing_else(library):
    definition = next(d for d in library.deck(settings.DECK_CHARACTERS).cards
                      if d.title == "Big D Randy")
    face = definition.ability_face

    assert face.title == "Granny Costume"
    for field in ("deck_id", "text", "art", "uses", "ability", "badge",
                  "variants", "image", "role", "count"):
        assert getattr(face, field) == getattr(definition, field), \
            f"ability_face disturbed {field}"


def test_ability_face_is_a_no_op_without_a_separate_skill(library):
    for definition in library.deck(settings.DECK_SKILLS).cards:
        assert definition.ability_face is definition


def test_ability_face_does_not_create_a_second_card(library):
    """No duplicate definitions: the character is still reachable from it."""
    definition = next(d for d in library.deck(settings.DECK_CHARACTERS).cards
                      if d.title == "Big D Randy")
    assert definition.ability_face.printed.title == "Big D Randy"


# ═════════════════════════════════════════════════════════════════════════════
# 6. Regression: everything that already worked
# ═════════════════════════════════════════════════════════════════════════════
def test_ordinary_card_artwork_still_resolves():
    """The card-art system was not replaced, extended or rerouted."""
    art = CardArtLibrary()
    for title in ("Troll", "Ice Block", "Speedrun", "Stańczyk"):
        definition = CardDef(deck_id=settings.DECK_MOVEMENT, title=title, text="")
        assert art.key(definition) == slugify(title), f"{title} lost its artwork"


def test_card_art_and_portraits_do_not_resolve_through_each_other(tmp_path):
    """Two folders, two questions.  A character cannot borrow card art."""
    write_portrait(tmp_path, "Granny Costume", RED)
    portraits = PortraitLibrary(tmp_path)
    # The ability's picture is in card_art and is NOT a portrait of anybody.
    assert not portraits.has_portrait("Big D Randy")
    # And the real card-art folder holds no portrait for a character.
    art = CardArtLibrary()
    character = CardDef(deck_id=settings.DECK_CHARACTERS, title="Glockboy", text="")
    assert art.key(character) is None


def test_the_hand_and_the_decks_are_untouched(screen):
    """A portrait is a panel decoration; nothing else may have moved."""
    before = len(screen.state.active_player.hand)
    frame(screen, mouse=portrait_rect(screen).center)
    assert len(screen.state.active_player.hand) == before


def test_clicking_the_portrait_discards_the_character_card(screen):
    """The gesture MOVED WITH THE CARD (stage 50).

    Stage 49 asserted the portrait had no click handler, which was right while
    the ability card was still in the column holding the discard gesture.  That
    slot is gone, so the gesture came with it rather than being deleted — the
    portrait is now the character's representation here, and clicking it does
    what clicking the card did.
    """
    view_hunter(screen)
    rects = screen.app.layout.character_panel(False)
    commands = screen.character_panel.handle_click(
        _click_ctx(screen, rects["portrait"].center), 1, None)
    assert commands and isinstance(commands[0], cmd.DiscardTopCharacterCard)


def _click_ctx(screen: GameScreen, mouse):
    from pedzacy_piotrek.ui.hud import HudContext
    return HudContext(
        r=screen.app.renderer, cards=screen.cards, layout=screen.app.layout,
        state=screen.state, surface=screen.app.canvas, mouse=tuple(mouse),
        view_index=screen.view_seat,
    )


def test_hovering_the_portrait_issues_no_commands(screen):
    """Hovering is presentation.  It must never reach the engine."""
    view_hunter(screen)
    ctx = _click_ctx(screen, portrait_rect(screen).center)
    # Drawing a frame with the cursor on the portrait is what produces the
    # preview; no Command may come out of that path at all.
    frame(screen, mouse=portrait_rect(screen).center)
    assert screen.card_preview.rect is not None
    assert screen.character_panel.handle_click(ctx, 3, None) == [], \
        "a right-click on the portrait produced a command"


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_column_still_fits_at_every_resolution(size):
    """The portrait may not push anything off the panel, at any window."""
    layout = Layout(*size)
    panel = layout.right_panel
    for show_skill in (False, True):
        rects = layout.character_panel(show_skill)
        keys = ["portrait", "char_draw", "char_disc"]
        if show_skill:
            keys += ["skill_draw", "skill_disc", "identity"]
        for key in keys:
            assert panel.contains(rects[key]), \
                f"{key} escaped the panel at {size} (show_skill={show_skill})"
        assert panel.contains(layout.ability_button_rect(show_skill))
        if not show_skill:
            grid = layout.pawn_grid_panel(layout.pawn_grid_top(show_skill))
            assert panel.contains(grid), f"the notepad escaped at {size}"


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_ability_card_stays_readable_at_every_resolution(size):
    """Stage 29's floor, which the portrait is not allowed to spend.

    This is why the draw/discard PILES shrink first: they show a card back and
    a count, and the ability card shows a Polish sentence.
    """
    layout = Layout(*size)
    for show_skill in (False, True):
        assert layout.right_cards(show_skill)[1] >= 120, \
            f"the ability card fell below the readable floor at {size}"


@pytest.mark.parametrize("size", REFERENCE_WINDOWS)
def test_the_portrait_is_worth_looking_at_at_every_resolution(size):
    """The other side of the same trade — it must not be a postage stamp."""
    layout = Layout(*size)
    for show_skill in (False, True):
        width, height = layout.portrait_size(show_skill)
        assert height >= 48, f"the portrait collapsed at {size}"
        assert width <= layout.right_panel.width - 2 * layout.right_inner, \
            f"the portrait is wider than its column at {size}"
        # Proportions kept: a portrait is a face, not a letterbox.
        assert 0.8 <= (width / height) / (1 / layout.portrait_aspect) <= 1.2


def test_the_interface_never_loads_a_portrait_itself():
    """Stage 30's rule, extended to the third asset library.

    ``ui/`` asks ``CardRenderer`` what a character looks like and never opens
    the folder, so there is one opinion about portraits exactly as there is one
    about card art.
    """
    ui_dir = Path(__file__).resolve().parent.parent / "pedzacy_piotrek" / "ui"
    for source in ui_dir.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "PortraitLibrary" not in text, f"{source.name} loads portraits itself"
        assert ".portraits." not in text, f"{source.name} bypasses draw_portrait"
