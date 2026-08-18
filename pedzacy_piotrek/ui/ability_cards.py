"""
CHARACTER -> ABILITY CARD, in one place (stage 50).

Three different things in the interface now answer the same question, and they
must not answer it three different ways:

    the character portrait in the right-hand panel
    a character's circle in the turn-order map
    (and, already, the Card Library's ability tab)

all mean "show me what THIS character's ability does".  Before this module the
character panel resolved it privately in ``CharacterPanel._ability_card`` and
nothing else could reach that, so a second hover target would have had to
either import a panel or grow its own copy of the rule — and a second copy of
this particular rule is exactly what stage 49 was cleaning up.

WHAT THE RULE IS
    character name
        -> ``GameState.ability_card``   the ONE live copy, wherever it is
        -> ``CardDef.ability_face``     read under the ABILITY's name
        -> a display ``Card``

    "Big D Randy" -> the live character card -> "Granny Costume"

VARIANTS COME FOR FREE, and that is the reason this goes through the live card
rather than the content library.  ``GameState`` pushes a variant onto every
live copy when it changes (``_reread_copies``), so the definition held by a
dealt card is ALREADY the reading this match is playing.  The Card Library has
to call ``variant_definition`` explicitly only because it builds its cells from
the printed content library, which knows nothing about this match.  Anything
starting from a live card must not repeat that step: ``ability_face`` is taken
last, after the variant, exactly as the library takes it.

WHY IT IS CACHED
    A ``Card`` carries a uid.  Building one per frame would churn uids sixty
    times a second under the cursor, and uid is what the drag code and the
    animation code identify a card by.  The key includes the VARIANT, so a
    match that switches Ondrej's Radar mid-game gets the reading it is actually
    playing rather than whichever one was cached first.

NO GAMEPLAY HAPPENS HERE.  Every method reads and returns; nothing mutates
``GameState``, nothing emits a Command.  Hovering is presentation.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..cards.base_card import Card
from ..engine.game_state import GameState
from ..players.player import Player


class AbilityCards:
    """Resolves and caches the ability card to PREVIEW for a character.

    Owned by the screen, not by a panel, because more than one panel asks.
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], Card] = {}

    def clear(self) -> None:
        self._cache.clear()

    # ── the lookup ───────────────────────────────────────────────────────────
    def for_character(self, state: GameState,
                      name: Optional[str]) -> Optional[Card]:
        """The ability card belonging to the character called ``name``.

        ``None`` when there is no such character, or when it has no ability to
        show — Piotrek's own character card carries no fixed ability, so his
        circle in the turn map correctly previews nothing.  That is also the
        hidden-information answer: his SKILLS are private, and this returns a
        character's PUBLIC fixed ability or nothing at all.
        """
        if not name:
            return None
        live = state.ability_card(name)
        if live is None:
            return None
        definition = live.definition
        if definition.ability is None:
            return None
        return self._display(definition)

    def for_player(self, state: GameState,
                   player: Optional[Player]) -> Optional[Card]:
        """The ability card to show for a SEAT the viewer is looking at.

        Piotrek is the exception and it is a rule, not a special case: his
        panel shows the SKILL CARD IN HIS HAND, which is a different card every
        time he draws one and is private to him.  A hunter's ability is printed
        on the character card everybody can see, so it resolves by name like
        any other.
        """
        if player is None:
            return None
        if player.is_piotrek:
            # Already the live card, already variant-correct, and its title is
            # its own ability's name — ``ability_face`` is a no-op on a skill
            # card, so there is nothing to derive.
            return player.skill
        if player.character is None:
            return None
        return self._display(player.character.definition)

    # ── internals ────────────────────────────────────────────────────────────
    def _display(self, definition) -> Card:
        face = definition.ability_face
        key = (definition.title, definition.selected_variant or "")
        cached = self._cache.get(key)
        if cached is None or cached.definition != face:
            cached = Card(face)
            self._cache[key] = cached
        return cached
