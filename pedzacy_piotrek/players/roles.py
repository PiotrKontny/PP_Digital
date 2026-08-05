"""
Roles.

The prototype expressed "is this player Piotrek?" by comparing the character
card's title to the string ``"Piotrek"`` in eight different places.  The role
is now derived once, from the ``role`` field in ``characters.json``, so
renaming the character in the data file cannot break the rules.
"""

from __future__ import annotations

from enum import Enum


class Role(Enum):
    """Which side of the hidden-identity game a player is on."""

    #: The hunted one.  Knows which pawn is his; wins by reaching the finish.
    PIOTREK = "piotrek"
    #: Everyone else.  Wins by stacking every pawn on Piotrek's pawn and
    #: correctly naming the one at the bottom of the tower.
    HUNTER = "hunter"

    @property
    def display_name(self) -> str:
        return "Piotrek" if self is Role.PIOTREK else "Hunter"

    @property
    def is_piotrek(self) -> bool:
        return self is Role.PIOTREK
