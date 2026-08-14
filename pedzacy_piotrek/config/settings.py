"""
Central configuration.

Every tunable that used to be a module-level constant scattered through
game.py now lives here.  Nothing in this module imports pygame, so it can be
imported by the headless engine, by a future dedicated server, or by tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Tuple

# ── paths ────────────────────────────────────────────────────────────────────
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PACKAGE_ROOT / "data"
ASSETS_DIR = PACKAGE_ROOT / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
IMAGE_DIR = ASSETS_DIR / "images"
SOUND_DIR = ASSETS_DIR / "sounds"
#: Full-card artwork for Signature Cards.  Separate from ``IMAGE_DIR`` on
#: purpose: ``images/cards/`` holds the small illustration a STANDARD card
#: shows inside its parchment body, while a file here REPLACES the card face
#: entirely.  One folder, one meaning — see ``render/card_art.py``.
CARD_ART_DIR = ASSETS_DIR / "card_art"
#: Extensions the card-art scanner accepts.  Lower case; the scan folds case.
CARD_ART_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

CARDS_FILE = DATA_DIR / "cards.json"
CHARACTERS_FILE = DATA_DIR / "characters.json"
BOARD_FILE = DATA_DIR / "board.json"

# ── application ──────────────────────────────────────────────────────────────
APP_TITLE = "Pędzący Piotrek"
FPS = 60

# ── deck identity ────────────────────────────────────────────────────────────
# The engine addresses decks by string id; these five ids replace the old
# integer indices 0..4 (DECK_CARD_SETS).  The order is still meaningful: the
# first three are the ones shown in the central deck panel.
DECK_MOVEMENT = "movement"
DECK_MODS = "mods"
DECK_CHEST = "chest"
DECK_CHARACTERS = "characters"
DECK_SKILLS = "piotrek_skills"

TABLE_DECKS = (DECK_MOVEMENT, DECK_MODS, DECK_CHEST)
PIOTREK_TITLE = "Piotrek"


@dataclass(frozen=True)
class Rules:
    """Pure gameplay numbers.  Identical to the prototype's values."""

    min_players: int = 3
    max_players: int = 6
    max_hand: int = 8
    start_hand_piotrek: int = 5
    start_hand_default: int = 3
    mod_slots: int = 2

    #: How many Karty Skrzyni a player may hold.  Piotrek carries more; his
    #: ChatGPT skill trades that back down (declared in characters.json).
    chest_limit_default: int = 1
    chest_limit_piotrek: int = 2

    #: Playing a movement card refills the hand and passes the turn on its own.
    #: A switch rather than a hard-coded rule so a future variant (or a test)
    #: can drive the turn by hand.
    auto_turn_flow: bool = True

    board_cells_min: int = 10
    board_cells_default: int = 24

    chest_open_min: int = 1
    #: The chest opens on round SIX (stage 26; it was 3).  Opening it that
    #: early made the special cards the opening move rather than a turn of the
    #: game, which is a balance decision and therefore a number, not a rule.
    chest_open_default: int = 6

    #: Above this many players the chest is handed out every eligible round.
    #: At or below it the rota comes round often enough that a card a round is
    #: too much, so only every ``chest_sparse_interval``-th eligible round
    #: actually awards one.  Five and six players are unaffected.
    chest_sparse_max_players: int = 4
    chest_sparse_interval: int = 2

    #: The round the first Mod Patusa selection happens on, and how many rounds
    #: apart the following ones are.  The physical game pauses every second
    #: round from the third, and both numbers are set in the lobby because
    #: balancing them is the whole reason they are numbers and not constants.
    mod_round_first_default: int = 3
    mod_round_first_min: int = 1
    mod_round_interval_default: int = 2
    mod_round_interval_min: int = 1
    #: How many Mod Patusa cards each faction is offered to choose from.
    mod_choices: int = 3

    #: Bounds on how many copies of one Mod Patusa the lobby may put in the
    #: deck.  Zero is allowed — leaving a mod out of the game is a legitimate
    #: balance decision, and the DEFAULTS live in cards.json rather than here
    #: because the composition of the deck is content, not code.
    mod_count_min: int = 0
    mod_count_max: int = 9

    #: The same, for the Movement and Chest decks, which stage 26 made
    #: configurable too.  A wider ceiling because those decks are printed with
    #: up to six copies of a title where the mods have two.
    card_count_min: int = 0
    card_count_max: int = 12

    #: Bounds on a character ability's charges.  Zero is allowed and means the
    #: ability is in the game but unusable, which is a legitimate way to try a
    #: character without its power rather than a broken state.
    ability_uses_min: int = 0
    ability_uses_max: int = 9

    #: Piotrek acts on every Nth turn slot of a round (slot 0, 3, 6, ...).
    piotrek_turn_period: int = 3

    #: How long an opponent has to decide whether to block a movement (Nie
    #: masz Rosji), in seconds.  A NUMBER and not a constant in the gameplay
    #: code, because the table has to be able to try 3 seconds and 12 — which
    #: is the whole reason the lobby can set it.
    block_decision_default: int = 7
    block_decision_min: int = 1
    block_decision_max: int = 30
    #: Ice Block's window.  A separate default from the movement one because
    #: the brief asks for ten seconds and because refusing a check is a bigger
    #: decision than letting a pawn move; the LIMITS are shared, so one clamp
    #: and one lobby control cover both.
    check_decision_default: int = 10
    #: How long a failed check under variant 2 waits before the tower comes
    #: apart.  A deadline on the authority's clock, never a sleep.
    tower_breakup_seconds: float = 2.0

    max_name_length: int = 16

    #: Default share of board rows widened into a doubled position, in percent.
    double_frequency_default: int = 30

    #: Smallest table the "Wersja testowa" option allows.  Testing multiplayer
    #: otherwise needs three people at three machines, which makes fixing a
    #: networking bug a scheduling problem.  Development only — the real game
    #: needs ``min_players``.
    debug_min_players: int = 2


RULES = Rules()


@dataclass(frozen=True)
class BoardLayout:
    """Geometry of the generated winding board.

    Two coordinate names are used throughout the generator:

    * **along** — the direction of travel, start to finish;
    * **across** — perpendicular to it, the direction the road weaves in.

    The board is laid out horizontally (along = +x), because the interface puts
    it in a wide centre column; making the axis explicit means switching back to
    a vertical board is one value, not a rewrite.
    """

    #: "horizontal" (start on the left) or "vertical" (start at the bottom).
    orientation: str = "horizontal"

    #: Distance in world pixels between two consecutive path rows.
    tile_spacing: float = 96.0
    #: Radius of a playable field.
    tile_radius: float = 32.0
    #: Sideways offset of the two fields of a "double" row, in world pixels.
    lane_offset: float = 44.0
    #: Width of the road ribbon drawn under the fields.
    road_width: float = 126.0
    #: Extent of the canvas across the direction of travel.
    canvas_across: int = 1080
    #: Margin the road keeps from the canvas edges (across).
    side_margin: float = 190.0
    #: Space reserved before field 1 for the starting camp.
    start_band: float = 230.0
    #: Space reserved after the final field.
    finish_band: float = 170.0
    #: How far apart pawns sit while waiting in the camp.
    camp_spacing: float = 64.0
    #: Lift applied per pawn when several stack on one field.
    stack_lift: float = 14.0
    #: A dropped pawn snaps to a field within this world-space radius.
    snap_radius: float = 66.0

    # ── spacing guarantees (see BoardModel._validate) ────────────────────────
    #: Minimum clear gap between the rims of any two fields.  The generator
    #: refuses to emit a board that violates this, which is what stopped the
    #: "squashed corner" boards the previous version could produce.
    min_tile_gap: float = 14.0
    #: Minimum clear gap between two separate passes of the road, measured
    #: between their outer edges.  Keeps a hairpin from touching itself.
    min_road_gap: float = 46.0

    @property
    def min_tile_distance(self) -> float:
        """Centre-to-centre distance two fields must never come closer than."""
        return 2.0 * self.tile_radius + self.min_tile_gap

    @property
    def min_turn_radius(self) -> float:
        """Tightest curve the road may bend into.

        On a curve of radius R the inner lane is compressed by ``(R - offset)/R``.
        Requiring the compressed spacing to stay above ``min_tile_distance``
        gives this bound directly, so the value is derived rather than guessed.
        """
        ratio = self.min_tile_distance / self.tile_spacing
        if ratio >= 1.0:
            # Fields are already as close as they may be on a straight road;
            # only a straight road can satisfy that, so demand a huge radius.
            return 1.0e6
        # The 1.10 is headroom: the bound is exact for a perfect circular arc,
        # and a real road is a spline whose curvature wobbles a little.
        return self.lane_offset / (1.0 - ratio) * 1.10 + self.tile_radius

    @property
    def road_half_width(self) -> float:
        return max(self.road_width / 2.0, self.lane_offset + self.tile_radius)


BOARD = BoardLayout()

#: Network debug panel, toggled in game with F3.  Off unless asked for.
NETWORK_DEBUG = False

# ── window ───────────────────────────────────────────────────────────────────
#: Smallest window the layout is designed to stay usable in.  Below this the
#: panels would start clipping, so the window is not allowed to shrink further.
MIN_WINDOW = (1280, 760)
#: Window opened on first run when the desktop is larger than this.
PREFERRED_WINDOW = (1920, 1080)

# ── camera ───────────────────────────────────────────────────────────────────
ZOOM_MIN = 0.35
ZOOM_MAX = 1.60
ZOOM_DEFAULT = 0.62
#: Exponential smoothing factor for camera moves (per second, 0 = instant).
CAMERA_SMOOTHING = 14.0
SCROLL_STEP = 64

# ── feature switches ─────────────────────────────────────────────────────────
#: Pawns released near a field snap onto it and stack like the turtles in the
#: original board game.  Set to False to restore the prototype's completely
#: free drag-anywhere placement.
SNAP_TOKENS_TO_TILES = True
#: Particle effects (dust puffs when a pawn lands, sparkles on a check).
PARTICLES_ENABLED = True
#: Draw the numbered label on every field.
SHOW_TILE_NUMBERS = True

# ── networking ───────────────────────────────────────────────────────────────
# Every networking value lives in ``net/config.py`` and ``data/network.json``
# now: server address, port, timeouts, heartbeat, reconnection and TLS.  Nothing
# about the wire belongs in this file, because a game setting and a deployment
# setting are changed by different people at different times.
NETWORK_FILE = DATA_DIR / "network.json"


# ── match variants ───────────────────────────────────────────────────────────
# Two lobby choices that change a RULE rather than a number.  Declared as
# id -> label so the lobby, the settings panel and the config all read the same
# list: adding a third answer is one entry here, not a new control.
#
# THE FIRST ENTRY OF EACH IS THE GAME AS IT SHIPPED, and is what an older
# client, a saved match or a test that never opened the panel gets.
# Each entry is ``id -> (short name, description)``.  TWO STRINGS, NOT ONE, and
# the split is the whole point: the stepper's well is a small box between -1 and
# +1 and only ever holds the NAME, exactly as a card variant's well holds
# "Wariant 1".  The description belongs in the row's help line under the title,
# where every other tab already puts it.  Folding them into one string is what
# put a sentence inside the well and pushed it over both buttons.
CHECK_VARIANTS: dict[str, tuple[str, str]] = {
    "continue": ("Wariant 1", "nieudany check nic nie zmienia"),
    "break_tower": ("Wariant 2", "nieudany check rozbija wieżę"),
}

VICTORY_VARIANTS: dict[str, tuple[str, str]] = {
    "own_pawn": ("Wariant 1", "wygrywa pionek Piotrka"),
    "any_pawn": ("Wariant 2", "wygrywa dowolny pionek"),
}


@dataclass
class SessionConfig:
    """Everything chosen in the pre-game menu / future lobby.

    This is the payload the host will eventually broadcast to clients so every
    machine builds an identical :class:`~pedzacy_piotrek.engine.game_state.GameState`.
    """

    num_players: int = RULES.max_players
    board_cells: int = RULES.board_cells_default
    chest_open_round: int = RULES.chest_open_default
    #: First round that pauses for a Mod Patusa selection, and the gap between
    #: selections after it.  Defaults reproduce the physical game: round 3,
    #: then every second round.
    mod_round_first: int = RULES.mod_round_first_default
    mod_round_interval: int = RULES.mod_round_interval_default
    #: How many copies of each Mod Patusa go into the deck, by card title.
    #: EMPTY MEANS "as printed" — the counts in cards.json.  Only titles the
    #: lobby actually changed need to be in here, so a match built by an older
    #: client, or by a test that never opened the panel, still gets the real
    #: deck instead of an empty one.
    mod_counts: dict[str, int] = field(default_factory=dict)
    #: The same, for the Movement and Chest decks (stage 26).  Separate fields
    #: rather than one nested mapping because ``mod_counts`` is already on the
    #: wire, in the lobby snapshot and in a dozen tests: a client on an older
    #: build sends the old field and still gets the deck it asked for.
    movement_counts: dict[str, int] = field(default_factory=dict)
    chest_counts: dict[str, int] = field(default_factory=dict)
    #: Character title → how many times its ability may be used.  EMPTY MEANS
    #: "as printed", exactly as the count mappings do, so a table that never
    #: opened the panel gets the values in characters.json.
    ability_uses: dict[str, int] = field(default_factory=dict)
    #: Card title → the id of the variant this match plays it under.  EMPTY
    #: MEANS "as printed", exactly as the four mappings above do, so a match
    #: built by an older client or by a test that never opened the panel plays
    #: every card's FIRST variant — which is the behaviour that shipped before
    #: variants existed.  A title with no variants, or an id the card does not
    #: declare, is ignored rather than applied.
    card_variants: dict[str, str] = field(default_factory=dict)
    #: Seconds an eligible opponent gets to answer a movement they may block.
    #: Configured in the lobby; the ENGINE holds the number and the interface
    #: only draws the countdown, so a client with a fast clock cannot give
    #: itself longer to think.
    block_decision_seconds: int = RULES.block_decision_default
    character_choices: list[str | None] = field(default_factory=list)
    #: Probability that a row of the board is widened into a doubled position
    #: (12a / 12b).  ``None`` keeps the board theme's fixed pattern.
    double_frequency: float | None = None
    #: Hot-seat editing: when true anybody at the keyboard may act as any
    #: player, which is how the prototype worked and how a single-machine game
    #: has to work.  When false the interface only controls ``local_seat`` —
    #: the way it will behave once the game is played over a network.
    edit_mode: bool = True
    #: Which seat belongs to this machine.  Meaningless in edit mode; it
    #: becomes the player's own seat when the lobby assigns one.
    local_seat: int = 0
    #: Development option: allow a two-player table so multiplayer can be tested
    #: without rounding up a third person.  Nothing else about the game changes.
    debug_version: bool = False
    #: Online match: Piotrek picks his own pawn colour before the first move,
    #: privately, and the server is the only one told.  A hot-seat game leaves
    #: this off and the colour is dealt from the seed as it always was — with
    #: everybody at one keyboard there is no secret to keep and nobody to ask.
    piotrek_picks_pawn: bool = False
    #: Seconds Piotrek gets to answer a check he may refuse with Ice Block.
    #: Same shape and the same clamp as ``block_decision_seconds``: the ENGINE
    #: holds the number, the interface only draws the countdown.
    check_decision_seconds: int = RULES.check_decision_default
    #: What a FAILED check does to the tower.
    #:
    #: ``"continue"`` is the game as it has always been — the colour is crossed
    #: off and play goes on.  ``"break_tower"`` adds the breakup on top of
    #: that; it does not replace the crossing-off.  A string rather than a bool
    #: because a third answer is likelier here than in most places, and an
    #: unknown value falls back to the default rather than to a crash.
    check_variant: str = "continue"
    #: What it takes for Piotrek to win by escaping.
    #:
    #: ``"own_pawn"`` is the game as it has always been — the colour he is
    #: actually hiding behind has to reach the finish.  ``"any_pawn"`` lets any
    #: pawn do it.  THIS CHANGES THE VICTORY CONDITION AND NOTHING ELSE: the
    #: hidden colour is still hidden, still checked the same way, and still the
    #: thing a check is asking about.
    victory_variant: str = "own_pawn"
    #: Skills whose ORIGINAL owner also loses a use when Herold copies them.
    #:
    #: A per-match setting, never a rule in the code and never a change to the
    #: printed ability.  The default names Glockboy, because borrowing a
    #: one-shot guess that can end the match or knock its owner out should cost
    #: the owner something — but the table decides, and nothing assumes
    #: Glockboy is the only one.
    copy_consumes_use: Tuple[str, ...] = ("Where are you Marcus?",)

    @property
    def min_players(self) -> int:
        return RULES.debug_min_players if self.debug_version else RULES.min_players
    #: Shared RNG seed — the single source of randomness for deck shuffles,
    #: character dealing and board decoration.  Sending it over the wire is
    #: what will make host and clients agree without syncing every card.
    seed: int = 0

    def normalised(self) -> "SessionConfig":
        """Clamp the settings into their legal ranges.

        ``replace`` rather than a rebuild: listing the fields explicitly meant
        every new setting had to be remembered here too, and twice now one was
        not — it reached the menu and then vanished on the way to the game.
        """
        choices = list(self.character_choices)
        if len(choices) < self.num_players:
            choices += [None] * (self.num_players - len(choices))
        frequency = self.double_frequency
        if frequency is not None:
            frequency = max(0.0, min(1.0, frequency))
        players = max(self.min_players, min(RULES.max_players, self.num_players))
        return replace(
            self,
            num_players=players,
            board_cells=max(RULES.board_cells_min, self.board_cells),
            chest_open_round=max(RULES.chest_open_min, self.chest_open_round),
            mod_round_first=max(RULES.mod_round_first_min, self.mod_round_first),
            mod_round_interval=max(RULES.mod_round_interval_min,
                                   self.mod_round_interval),
            character_choices=choices[:players],
            double_frequency=frequency,
            mod_counts=clamp_mod_counts(self.mod_counts),
            movement_counts=clamp_card_counts(self.movement_counts),
            chest_counts=clamp_card_counts(self.chest_counts),
            ability_uses=clamp_ability_uses(self.ability_uses),
            card_variants=clean_card_variants(self.card_variants),
            block_decision_seconds=max(
                RULES.block_decision_min,
                min(RULES.block_decision_max, int(self.block_decision_seconds))),
            check_decision_seconds=max(
                RULES.block_decision_min,
                min(RULES.block_decision_max, int(self.check_decision_seconds))),
            check_variant=(self.check_variant
                           if self.check_variant in CHECK_VARIANTS
                           else "continue"),
            copy_consumes_use=tuple(str(name) for name in
                                    (self.copy_consumes_use or ())),
            victory_variant=(self.victory_variant
                             if self.victory_variant in VICTORY_VARIANTS
                             else "own_pawn"),
            local_seat=max(0, min(players - 1, self.local_seat)),
        )


def clamp_title_map(counts: "Mapping[str, int] | None",
                    low: int, high: int) -> dict[str, int]:
    """Force a title → number mapping into a legal range, dropping junk.

    Shared by the config, the lobby and the server so a hand-written message
    cannot seat a deck with -3 copies of a card in it, and so all three agree
    about what a given payload means.  Sorted by title on the way out: this
    ends up in the lobby snapshot every client compares, and two dictionaries
    with the same pairs in a different order are the same settings.

    One implementation for all four mappings stage 26 ended up with.  Four
    copies of this differing only in their bounds is how one of them quietly
    stops sorting and a whole table starts resyncing.
    """
    if not counts:
        return {}
    clean: dict[str, int] = {}
    for title, value in counts.items():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        clean[str(title)] = max(low, min(high, number))
    return {title: clean[title] for title in sorted(clean)}


def clamp_mod_counts(counts: "Mapping[str, int] | None") -> dict[str, int]:
    """Legal copies of each Mod Patusa."""
    return clamp_title_map(counts, RULES.mod_count_min, RULES.mod_count_max)


def clamp_card_counts(counts: "Mapping[str, int] | None") -> dict[str, int]:
    """Legal copies of each Movement or Chest card."""
    return clamp_title_map(counts, RULES.card_count_min, RULES.card_count_max)


def clamp_ability_uses(uses: "Mapping[str, int] | None") -> dict[str, int]:
    """Legal charges for a character ability."""
    return clamp_title_map(uses, RULES.ability_uses_min, RULES.ability_uses_max)


def clean_card_variants(variants: "Mapping[str, str] | None") -> dict[str, str]:
    """Tidy a title → variant-id mapping the way the count maps are clamped.

    A variant id is a NAME, not a number, so there is no range to clamp it
    into: this drops junk, stringifies both sides and sorts by title for the
    same reason :func:`clamp_title_map` does — this mapping goes into the lobby
    snapshot every client compares against the host's, and two dictionaries
    with the same pairs in a different order are the same settings.

    Whether an id is one the CARD actually declares is settled where the cards
    are, in ``DeckDef.with_variants`` and ``GameState``: an id no card knows is
    ignored there, so a stale entry left by a renamed variant costs that one
    card its setting rather than the match.
    """
    if not variants:
        return {}
    clean: dict[str, str] = {}
    for title, value in variants.items():
        if value is None:
            continue
        clean[str(title)] = str(value)
    return {title: clean[title] for title in sorted(clean)}
