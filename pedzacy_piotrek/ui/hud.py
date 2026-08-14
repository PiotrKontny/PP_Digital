"""
HUD panels.

One class per region of the screen.  Each panel knows how to draw itself and
how to turn a click inside itself into commands; none of them touches game
state directly.

The regions changed in this stage — the board moved to the middle and grew, so
the decks and the mod rack moved into a left column, the character panel into a
right one, and the hand became a fan along the bottom (``hand_fan.py``).  What
did *not* change is every interaction the prototype had:

* click a draw pile → the active player draws;
* click a hand card → play it if the engine can resolve it, otherwise discard;
* right-click a hand card → stage it, then click the rack to push it in;
* right-click a filled mod slot → discard it;
* click the big character card → discard whatever is showing;
* click the character/skill draw piles → draw;
* click a pawn colour → cross it off the hunter's list;
* click a player tile → switch seat; click its pencil → rename;
* ± on the round counter → change round.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pygame

from ..cards.base_card import Card, CardDef
from ..config import settings
from ..config.theme import darken, lighten, mix
from ..engine import commands as cmd
from ..engine.animation import approach
from ..engine.game_state import GameState
from ..players.player import Player
from ..render.card_renderer import CardRenderer
from ..render.renderer import Renderer
from .layout import Layout
from .widgets import TextField

Color = Tuple[int, int, int]


@dataclass
class HudContext:
    """Everything a panel needs for one frame."""

    r: Renderer
    cards: CardRenderer
    layout: Layout
    state: GameState
    surface: pygame.Surface
    mouse: Tuple[int, int]
    pending_mod_uid: Optional[int] = None
    rename: Optional[TextField] = None
    dt: float = 0.0
    #: Seat whose hand, character and notepad are on screen.  This is NOT the
    #: active player: over a network every machine watches the same active
    #: player but shows its own cards, and conflating the two made every client
    #: believe it was playing the host's seat.
    view_index: int = 0
    #: Whether the viewer may act for the seat they are looking at.
    can_act: bool = True

    @property
    def player(self) -> Player:
        player = self.state.player(self.view_index)
        return player if player is not None else self.state.active_player

    @property
    def theme(self):
        return self.r.theme


class Panel:
    """Base panel: draws, and optionally answers clicks with commands."""

    def draw(self, ctx: HudContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def handle_click(self, ctx: HudContext, button: int, ui) -> List[cmd.Command]:
        return []


def _deck_section(
    ctx: HudContext, deck, label_y: int, draw_rect: pygame.Rect,
    disc_rect: pygame.Rect, color: Color, label_x: Optional[int] = None,
    hover: float = 0.0,
) -> None:
    """A deck as it appears everywhere: name, draw pile, discard pile.

    Shared by the left column and the character panel; they used to be two
    near-identical blocks of drawing code.

    ``hover`` is an animated 0..1 value: the pile's own colour brightens and it
    lifts a couple of pixels. That replaced a glow drawn *around* the pile,
    which read as a smudge rather than as a control responding to the cursor.
    """
    r, theme, surface = ctx.r, ctx.theme, ctx.surface
    size = (draw_rect.width, draw_rect.height)
    label_left = label_x if label_x is not None else draw_rect.x
    band = pygame.Rect(label_left, label_y, disc_rect.right - label_left,
                       ctx.layout.section_line_h)

    # Heading in the game's voice: upper case, letter-spaced, brass.  The two
    # counters ride the same row on the right so the words never cost the cards
    # a line of column.
    line_h = ctx.layout.section_line_h
    name_font = r.fonts.get(int(13 * ctx.layout.ui_scale), bold=True)
    count_font = r.fonts.get(int(13 * ctx.layout.ui_scale), bold=True)
    heading = lighten(theme.text_heading, 0.3 * hover) if hover else theme.text_heading
    # The counters own the right of the band, so the NAME is fitted to what is
    # left of it.  "UMIEJĘTNOŚCI PIOTRKA" is the longest deck name in the game
    # and it ran straight into its own "2 / 0" once stage 26 made the type
    # bigger — a heading that overlaps its own number is worse than a smaller
    # heading, and the counters cannot shrink because they are the data.
    counts_w = (count_font.size(f"{deck.draw_count}")[0]
                + count_font.size(f"/ {deck.discard_count}")[0]
                + int(18 * ctx.layout.ui_scale))
    name_font = r.fitted_font(deck.name.upper(),
                              max(30, band.width - counts_w),
                              int(13 * ctx.layout.ui_scale), bold=True, spacing=1)
    r.spaced_text(deck.name.upper(), name_font, heading, surface,
                  midleft=(band.left, band.centery), spacing=1)
    r.text(f"{deck.draw_count}", count_font,
           lighten(theme.text_light, 0.2 * hover) if hover else theme.text_light,
           surface, midright=(band.right - 26, band.centery))
    r.text(f"/ {deck.discard_count}", count_font, theme.text_dim, surface,
           midright=(band.right, band.centery))

    # Both piles sit in recessed wells, so a deck reads as resting in the panel
    # rather than floating on it.
    for rect in (draw_rect, disc_rect):
        r.inset_well(rect.inflate(6, 6), surface, radius=max(6, rect.height // 14))

    # ``deck_id`` is what picks the card back — every deck has its own picture,
    # configured in ``settings.CARD_BACKS``.  Passing the deck's identity rather
    # than naming an asset is what keeps this file out of the business of
    # knowing which file is which; ``color`` still dresses the discard pile and
    # the drawn fallback back.
    ctx.cards.draw_pile(draw_rect.x, draw_rect.y - int(3 * hover), deck.draw_count,
                        color, surface, size=size, brightness=hover,
                        deck_id=deck.id)

    top = deck.top_discard
    if top is not None:
        ctx.cards.draw_in(top, disc_rect, surface, border_color=color)
    else:
        ctx.cards.draw_empty(disc_rect.x, disc_rect.y, surface, size=size)


# ── turn bar ─────────────────────────────────────────────────────────────────
class RoundPanel(Panel):
    """Chest countdown, the turn-order map, and the round counter."""

    def draw(self, ctx: HudContext) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface

        bar = layout.turn_bar
        r.premium_panel(bar, surface)

        # Chest status: a small brass-edged plaque, label above value, so the
        # hierarchy reads at a glance instead of as two equal lines.
        info = layout.chest_box
        r.inset_well(info, surface, radius=8)
        state_text = "OTWARTA" if state.chest_is_open else f"OD RUNDY {state.chest_open_round}"
        r.spaced_text("SKRZYNIA", r.fonts.get(int(11 * layout.ui_scale), bold=True),
                      theme.text_heading, surface,
                      center=(info.centerx, info.top + info.height * 0.32), spacing=2)
        r.text(state_text, r.fonts.get(int(14 * layout.ui_scale), bold=True),
               theme.accent if state.chest_is_open else theme.text_light, surface,
               center=(info.centerx, info.bottom - info.height * 0.30))

        counter = layout.round_counter_rects()
        self._draw_turn_map(ctx)
        self._draw_counter(ctx, counter)

    def _draw_turn_map(self, ctx: HudContext) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface

        sequence = state.turn_order()
        circles = layout.turn_circle_rects(len(sequence))
        if not circles:
            return
        diameter = layout.turn_circle_actual_d
        chest_open = state.chest_is_open
        chest_awards = state.chest_awards_cards()
        chest_hunter = state.chest_recipient()
        chest_color = theme.deck_colors[settings.DECK_CHEST]
        arrow_color = theme.brass
        font = r.fonts.get(max(9, int(diameter * 0.20)))
        piotrek_marked = False

        for i, (slot, rect) in enumerate(zip(sequence, circles)):
            if i < len(circles) - 1:
                nxt = circles[i + 1]
                y = rect.centery
                r.arrow((rect.right + 3, y), (nxt.left - 5, y), arrow_color, 2, 7, surface)

            is_now = i == state.turn_slot
            face = mix(theme.panel_bg_light, theme.card_bg_shade, 0.22)
            if is_now:
                r.ring_glow(rect.center, diameter // 2, theme.accent, surface,
                            strength=0.75)
            r.aa_circle(rect.center, diameter // 2, face,
                        theme.accent if is_now else theme.panel_edge,
                        3 if is_now else 2, surface)
            r.aa_ring(rect.center, diameter // 2 - 3,
                      mix(face, theme.panel_highlight, 0.5), 1, surface)

            lines = r.wrap_lines(slot.name, font, diameter - 8)[:2]
            line_h = font.get_height()
            ty = rect.centery - (len(lines) * line_h) // 2 - 2
            for line in lines:
                r.text(line, font, theme.text_light if not is_now else theme.btn_active_text,
                       surface, midtop=(rect.centerx, ty))
                ty += line_h
            # The slot number on a little brass tab below the portrait.
            tab_r = max(7, diameter // 6)
            centre = (rect.centerx, rect.bottom - tab_r // 2)
            r.aa_circle(centre, tab_r, theme.panel_bg, theme.panel_edge, 1, surface)
            r.text(str(i + 1), r.fonts.get(max(9, int(tab_r * 1.1)), bold=True),
                   theme.text_heading, surface, center=centre)

            show_dot = False
            if slot.is_piotrek and not piotrek_marked:
                piotrek_marked = True
                show_dot = state.piotrek_name is not None
            elif (not slot.is_piotrek) and chest_hunter is not None and slot.name == chest_hunter:
                show_dot = True
            if show_dot:
                centre = (rect.centerx, rect.bottom + 6)
                # FILLED means "a card arrives this round", OUTLINED means
                # "not this one".  Before the chest opens nothing is dealt, and
                # on a small table every second eligible round deals nothing
                # either — both are the same statement to a player, so both
                # draw the same hollow marker.  The marker still MOVES on a
                # skipped round; only its fill changes.
                if chest_open and chest_awards:
                    r.aa_circle(centre, 5, chest_color, surface=surface)
                else:
                    r.aa_ring(centre, 5, chest_color, 2, surface)

    def _draw_counter(self, ctx: HudContext, counter: Dict[str, pygame.Rect]) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface

        minus_hover = counter["minus"].collidepoint(ctx.mouse)
        plus_hover = counter["plus"].collidepoint(ctx.mouse)

        # "RUNDA" above the number, minus and plus either side: the concept's
        # hierarchy, where the number is the thing you actually read.
        big = counter["big"]
        r.spaced_text("RUNDA", r.fonts.get(int(11 * layout.ui_scale), bold=True),
                      theme.text_heading, surface,
                      center=(big.centerx, big.top - 9), spacing=3)

        r.circle_button(counter["minus"].center, layout.counter_small_d // 2, surface,
                        fill=theme.counter_minus,
                        border=lighten(theme.counter_minus, 0.45),
                        hover=1.0 if minus_hover else 0.0)
        r.text("\u2212", r.fonts.get(int(layout.counter_small_d * 0.62), bold=True),
               theme.btn_active_text, surface, center=counter["minus"].center)

        r.aa_circle(big.center, layout.counter_big_d // 2 + 2,
                    darken(theme.background_deep, 0.7), surface=surface)
        r.aa_circle(big.center, layout.counter_big_d // 2,
                    mix(theme.panel_bg_light, theme.brass, 0.12),
                    theme.brass_light, 2, surface)
        r.text(str(state.round_number),
               r.fonts.get(max(16, int(layout.counter_big_d * 0.56)), bold=True),
               theme.brass_bright, surface, center=big.center, shadow=True)

        r.circle_button(counter["plus"].center, layout.counter_small_d // 2, surface,
                        fill=theme.counter_plus,
                        border=lighten(theme.counter_plus, 0.45),
                        hover=1.0 if plus_hover else 0.0)
        r.text("+", r.fonts.get(int(layout.counter_small_d * 0.62), bold=True),
               theme.btn_active_text, surface, center=counter["plus"].center)

    def handle_click(self, ctx: HudContext, button: int, ui) -> List[cmd.Command]:
        if button != 1:
            return []
        counter = ctx.layout.round_counter_rects()
        if counter["minus"].collidepoint(ctx.mouse):
            return [cmd.SetRound(round_number=ctx.state.round_number - 1)]
        if counter["plus"].collidepoint(ctx.mouse):
            return [cmd.SetRound(round_number=ctx.state.round_number + 1)]
        return []


# ── left column ──────────────────────────────────────────────────────────────
class DeckPanel(Panel):
    """The left column: its background plus the three table decks."""

    def __init__(self) -> None:
        #: Animated hover level per deck, 0..1.
        self.hover: Dict[str, float] = {}

    def draw(self, ctx: HudContext) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface

        r.premium_panel(layout.left_panel, surface)

        for column, deck_id in enumerate(settings.TABLE_DECKS):
            deck = state.deck(deck_id)
            draw_rect = layout.deck_draw_rect(column)
            wanted = (
                1.0 if draw_rect.collidepoint(ctx.mouse) and deck.draw_count else 0.0
            )
            level = approach(self.hover.get(deck_id, 0.0), wanted, 12.0, ctx.dt)
            self.hover[deck_id] = level
            _deck_section(
                ctx, deck, layout.deck_label_y(column),
                draw_rect, layout.deck_discard_rect(column),
                theme.deck_colors.get(deck_id) or theme.brass,
                label_x=layout.left_panel.left + layout.left_inner,
                hover=level,
            )

    def handle_click(self, ctx: HudContext, button: int, ui) -> List[cmd.Command]:
        if button != 1:
            return []
        for column, deck_id in enumerate(settings.TABLE_DECKS):
            if ctx.layout.deck_draw_rect(column).collidepoint(ctx.mouse):
                return [cmd.DrawCard(player_index=ctx.player.index, deck_id=deck_id)]
        return []


class ModPanel(Panel):
    """Two-slot 'Mody Patusa' rack shared by the whole table."""

    def draw(self, ctx: HudContext) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface
        placing = ctx.pending_mod_uid is not None
        size = layout.panel_card_size

        panel = layout.left_panel
        r.section_heading(
            "Aktywne Mody Patusa",
            r.fonts.get(int(13 * layout.ui_scale), bold=True),
            panel.centerx, layout.mod_label_y,
            panel.width - 2 * layout.left_inner, surface,
            color=theme.mod_select_ring if placing else theme.text_heading,
        )
        if placing:
            r.text("kliknij slot", r.fonts.label(), theme.mod_select_ring, surface,
                   midtop=(panel.centerx, layout.mod_label_y + layout.section_line_h))
        else:
            # The same line the "kliknij slot" hint uses, which is free the rest
            # of the time.  The table already knows when the chest opens; the
            # mods are the other thing worth counting down to, and a round that
            # pauses everything should not arrive as a surprise.
            if state.mod_selection_open:
                note, colour = "wybór trwa…", theme.accent
            else:
                due = state.next_mod_round()
                note = ("wybór w tej rundzie" if due == state.round_number
                        else f"następny wybór: runda {due}")
                colour = theme.text_dim
            r.text(note, r.fonts.label(), colour, surface,
                   midtop=(panel.centerx, layout.mod_label_y + layout.section_line_h))

        for slot in range(layout.mod_slot_count):
            rect = layout.mod_slot_rect(slot)
            hovered = rect.collidepoint(ctx.mouse)
            card = state.mod_slots[slot]
            well = rect.inflate(6, 6)
            if hovered:
                # The slot's own outline lights up — the same rim as everything
                # else, sized to the slot rather than a halo over the column.
                r.shape_glow(well, theme.mod_select_ring if placing else theme.accent,
                             surface, radius=max(6, rect.height // 14),
                             strength=0.5 if placing else 0.3)
            r.inset_well(well, surface,
                         radius=max(6, rect.height // 14),
                         border=theme.mod_select_ring if (placing and hovered) else None)
            if card is not None:
                colour = theme.deck_colors.get(card.deck_id)
                ctx.cards.draw_in(card, rect, surface,
                                  highlighted=(hovered and placing), border_color=colour)
                if hovered and not placing:
                    r.text("P-click: odrzuć", r.fonts.label(), theme.mod_instr, surface,
                           midtop=(rect.centerx, rect.bottom + 3))
            else:
                ctx.cards.draw_empty(rect.x, rect.y, surface, size=size,
                                     label="pusty" if rect.height > 90 else None)

    def handle_click(self, ctx: HudContext, button: int, ui) -> List[cmd.Command]:
        layout = ctx.layout
        hit_slot: Optional[int] = None
        for slot in range(layout.mod_slot_count):
            if layout.mod_slot_rect(slot).collidepoint(ctx.mouse):
                hit_slot = slot
                break

        if button == 1 and ctx.pending_mod_uid is not None:
            if hit_slot is not None:
                staged = ctx.pending_mod_uid
                ui.pending_mod_uid = None
                return [cmd.PlaceMod(player_index=ctx.player.index, card_uid=staged)]
            return []
        if button == 3 and hit_slot is not None and ctx.state.mod_slots[hit_slot] is not None:
            return [cmd.DiscardMod(slot=hit_slot)]
        return []


# ── right column ─────────────────────────────────────────────────────────────
class CharacterPanel(Panel):
    """'Twoja Postać' — character, ability, its decks, and the hunter notepad."""

    def __init__(self) -> None:
        self._ability_cache: Dict[Tuple[int, str], Card] = {}
        self.hover: Dict[str, float] = {}

    def _ability_card(self, player: Player) -> Optional[Card]:
        """A synthetic card showing a character's fixed ability.

        Cached, because building it fresh every frame would churn card uids.
        """
        if player.character is None:
            return None
        key = (player.index, player.character.title)
        cached = self._ability_cache.get(key)
        if cached is None:
            definition = CardDef(
                deck_id=settings.DECK_CHARACTERS,
                title=player.character.skill or "",
                text=player.character.text,
            )
            cached = Card(definition)
            self._ability_cache[key] = cached
        return cached

    def draw(self, ctx: HudContext) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface
        player = ctx.player

        panel = layout.right_panel
        r.premium_panel(panel, surface)

        show_skill = player.is_piotrek
        rects = layout.character_panel(show_skill)

        if show_skill:
            self._draw_identity_badge(ctx, rects["identity"])

        r.section_heading("Twoja Postać",
                          r.fonts.get(int(12 * layout.ui_scale), bold=True),
                          panel.centerx, rects["title_y"],
                          panel.width - 2 * layout.right_inner, surface)
        r.text(player.display_character,
               r.fonts.get(int(21 * layout.ui_scale), bold=True), theme.text_light,
               surface, midtop=(panel.centerx, rects["name_y"]), shadow=True)

        ability_color = theme.deck_colors[
            settings.DECK_SKILLS if show_skill else settings.DECK_CHARACTERS
        ]
        ability = player.skill if show_skill else self._ability_card(player)
        card_rect: pygame.Rect = rects["card"]  # type: ignore[assignment]

        r.inset_well(card_rect.inflate(8, 8), surface,
                     radius=max(6, card_rect.height // 14))
        if ability is not None:
            hovered = card_rect.collidepoint(ctx.mouse)
            ctx.cards.draw_in(ability, card_rect, surface, highlighted=hovered,
                              border_color=ability_color)
        else:
            ctx.cards.draw_empty(card_rect.x, card_rect.y, surface, label="brak",
                                 size=(card_rect.w, card_rect.h))

        self._draw_ability_button(ctx, show_skill, ability)

        if show_skill:
            _deck_section(
                ctx, state.deck(settings.DECK_SKILLS), rects["skill_label_y"],
                rects["skill_draw"], rects["skill_disc"],
                theme.deck_colors[settings.DECK_SKILLS],
                label_x=panel.left + layout.right_inner,
                hover=self._hover_level(ctx, settings.DECK_SKILLS, rects["skill_draw"]),
            )
        _deck_section(
            ctx, state.deck(settings.DECK_CHARACTERS), rects["char_label_y"],
            rects["char_draw"], rects["char_disc"],
            theme.deck_colors[settings.DECK_CHARACTERS],
            label_x=panel.left + layout.right_inner,
            hover=self._hover_level(ctx, settings.DECK_CHARACTERS, rects["char_draw"]),
        )

        if player.character is not None and not show_skill:
            self._draw_pawn_grid(ctx, layout.pawn_grid_top(show_skill))

    def _draw_identity_badge(self, ctx: HudContext, rect: pygame.Rect) -> None:
        """Piotrek's own colour, kept in front of him for the whole match.

        Drawn ONLY on a Piotrek panel, and a Piotrek panel is only ever on
        Piotrek's own screen — a hunter's client has ``show_skill`` false for
        its own seat and is not allowed to look at anybody else's
        (``GameScreen.may_view``).  On top of that, a hunter's copy of the state
        does not contain the colour to draw: ``secret_pawn`` is ``None`` there
        all match.  Two independent reasons, because this is the one thing on
        screen that must never leak.

        Before the colour is chosen the badge is an empty ring rather than
        nothing, so the row does not appear from nowhere and shift the panel.
        """
        r, theme, surface = ctx.r, ctx.theme, ctx.surface
        pawn_id = ctx.player.secret_pawn
        pawn = ctx.state.library.pawn(pawn_id) if pawn_id else None
        scale = ctx.layout.ui_scale

        radius = max(5, int(rect.height * 0.34))
        centre_y = rect.centery
        font = r.fonts.get(max(9, int(12 * scale)), bold=True)
        label = pawn.name.upper() if pawn is not None else "NIE WYBRANO"
        text = r.text_surface(label, font, theme.brass_light if pawn is not None
                              else theme.text_dim)
        total = radius * 2 + int(8 * scale) + text.get_width()
        left = rect.centerx - total // 2

        centre = (left + radius, centre_y)
        if pawn is not None:
            r.aa_circle((centre[0], centre[1] + max(1, int(2 * scale))), radius,
                        darken(theme.background_deep, 0.6), surface=surface)
            r.aa_circle(centre, radius, pawn.color, darken(pawn.color, 0.55), 2,
                        surface)
            r.soft_ellipse((centre[0] - radius * 0.3, centre[1] - radius * 0.35),
                           radius * 0.45, radius * 0.3, lighten(pawn.color, 0.75),
                           alpha=140, surface=surface)
        else:
            r.aa_ring(centre, radius, theme.text_dim, 2, surface=surface)
        surface.blit(text, (left + radius * 2 + int(8 * scale),
                            centre_y - text.get_height() // 2))

    def _draw_ability_button(self, ctx: HudContext, show_skill: bool,
                             ability: Optional[Card]) -> None:
        """Activate the character's ability, with its remaining uses.

        The count comes from the card's ``uses`` field, not from reading "2x"
        out of the description — the text stays human-readable and the rules
        stay structured.
        """
        r, layout, theme, surface = ctx.r, ctx.layout, ctx.theme, ctx.surface
        rect = layout.ability_button_rect(show_skill)
        source_card = ctx.player.skill if show_skill else ctx.player.character
        if source_card is None or not source_card.has_ability:
            return

        left = source_card.uses_left
        total = source_card.uses_total
        # Sesja na PG and the pawns-on-START rule grey the button exactly the
        # way spending the last use does — same ``enabled=False``, same styling
        # — but they are a DIFFERENT state: the charges are untouched and come
        # back when the block lifts, so the caption still shows how many are
        # left rather than "ZUŻYTE".  ONE QUESTION, asked of the engine, so the
        # button is grey exactly when the command would be refused: a button
        # that lights up for a rule the engine enforces is a lie the player
        # only discovers by clicking it.
        blocked = ctx.state.ability_refusal()
        locked = blocked is not None
        available = source_card.ability_available and not locked
        hovered = rect.collidepoint(ctx.mouse) and available

        style = r.emphasis(
            fill=theme.btn_primary_bg, border=theme.btn_primary_border,
            text=theme.btn_primary_text, hover=1.0 if hovered else 0.0,
            enabled=available, accent=theme.brass_light,
        )
        drawn = r.interactive_panel(rect, style, surface, radius=8)

        if locked and source_card.ability_available:
            label = ("SESJA NA PG" if ctx.state.abilities_locked
                     else "PIONKI NA STARCIE")
        elif total is None:
            label = "UŻYJ UMIEJĘTNOŚCI"
        elif available:
            label = f"UŻYJ UMIEJĘTNOŚCI ({left}/{total})"
        else:
            label = "ZUŻYTE"
        # The longest label here — "UŻYJ UMIEJĘTNOŚCI (2/2)" — is what used to
        # run out through both ends of this button in the narrow right-hand
        # column.  The box is as wide as the column allows and the type shrinks
        # to meet it, so the caption always fits whatever room is left.
        r.fit_spaced_text(label, drawn, style.text, surface, spacing=1,
                          padding=8, min_size=8, height_ratio=0.46)

    def _hover_level(self, ctx: HudContext, deck_id: str, rect) -> float:
        wanted = 1.0 if rect.collidepoint(ctx.mouse) else 0.0
        level = approach(self.hover.get(deck_id, 0.0), wanted, 12.0, ctx.dt)
        self.hover[deck_id] = level
        return level

    def _draw_pawn_grid(self, ctx: HudContext, top_y: int) -> None:
        """'Kolory Piotrka' — the hunters' elimination notepad.

        AUTOMATIC since stage 17.  A colour is crossed off when a tower was
        checked and turned out not to be Piotrek, and that is a fact the server
        establishes and broadcasts — so the panel reads
        ``state.eliminated_pawns`` and every hunter's notepad is identical.
        Clicking does nothing on purpose: a player crossing colours off by hand
        could contradict the only party that actually knows.
        """
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface
        pawns = state.library.pawns

        panel = layout.pawn_grid_panel(top_y)
        r.inset_well(panel, surface, radius=9)
        r.spaced_text("KOLORY PIOTRKA",
                      r.fonts.get(int(11 * layout.ui_scale), bold=True),
                      theme.text_heading, surface,
                      center=(panel.centerx, panel.top + layout.pk_title_h // 2 + 2),
                      spacing=2)

        for pawn, rect in zip(pawns, layout.pawn_grid_rects(top_y, len(pawns))):
            hovered = rect.collidepoint(ctx.mouse)
            radius = layout.pk_circle_d // 2
            eliminated = pawn.id in state.eliminated_pawns
            # A ruled-out colour is DRAINED as well as crossed: the difference
            # has to read at a glance from across the room, and a dark disc
            # under a red cross says "spent" before the cross is even resolved.
            face = darken(pawn.color, 0.35) if eliminated else pawn.color
            r.aa_circle((rect.centerx, rect.centery + 2), radius,
                        darken(theme.background_deep, 0.6), surface=surface)
            r.aa_circle(rect.center, radius, face,
                        theme.invalid if eliminated else
                        (theme.brass_light if hovered else darken(pawn.color, 0.55)),
                        3 if eliminated else 2, surface)
            if not eliminated:
                r.soft_ellipse(
                    (rect.centerx - radius * 0.3, rect.centery - radius * 0.35),
                    radius * 0.45, radius * 0.3, lighten(pawn.color, 0.75),
                    alpha=140, surface=surface)
            else:
                # Was a 4-pixel line at every resolution, which is a hairline
                # on a 1440p panel and was reported as hard to see.  Now the
                # cross is a share of the circle and therefore scales with the
                # window, and it is the same mark the board-side notice draws.
                r.heavy_cross(rect.center, int(radius * 1.85), theme.invalid,
                              darken(theme.ink, 0.4), surface,
                              scale=layout.ui_scale)

    def handle_click(self, ctx: HudContext, button: int, ui) -> List[cmd.Command]:
        if button != 1:
            return []
        player = ctx.player
        show_skill = player.is_piotrek
        rects = ctx.layout.character_panel(show_skill)

        card_rect: pygame.Rect = rects["card"]  # type: ignore[assignment]
        if card_rect.collidepoint(ctx.mouse) and player.character is not None:
            return [cmd.DiscardTopCharacterCard(player_index=player.index)]
        if show_skill and rects["skill_draw"].collidepoint(ctx.mouse):  # type: ignore[union-attr]
            return [cmd.DrawSkill(player_index=player.index)]
        if rects["char_draw"].collidepoint(ctx.mouse):  # type: ignore[union-attr]
            return [cmd.DrawCharacter(player_index=player.index)]

        # The notepad is not clickable any more — see :meth:`_draw_pawn_grid`.
        return []


# ── player list ──────────────────────────────────────────────────────────────
class PlayerTiles(Panel):
    @staticmethod
    def _crown(r, surface, position, colour) -> None:
        """The little crown the concept puts over the player in turn."""
        x, y = position
        points = [(x - 7, y + 8), (x - 7, y + 2), (x - 3, y + 5), (x, y),
                  (x + 3, y + 5), (x + 7, y + 2), (x + 7, y + 8)]
        pygame.draw.polygon(surface, colour, points)
        pygame.draw.polygon(surface, darken(colour, 0.6), points, 1)

    def draw(self, ctx: HudContext) -> None:
        r, layout, state, theme = ctx.r, ctx.layout, ctx.state, ctx.theme
        surface = ctx.surface
        count = len(state.players)
        rename = ctx.rename

        for i, player in enumerate(state.players):
            rect = layout.player_tile_rect(i, count)
            is_active = i == state.active_player_index
            is_viewed = i == ctx.view_index
            is_owned = i == state.local_seat
            is_renaming = rename is not None and rename.active and rename.target_index == i
            controllable = state.may_control(i)

            fill = theme.btn_active_bg if is_active else theme.btn_idle_bg
            if not controllable:
                fill = darken(fill, 0.8)
            border = (
                theme.btn_active_border if is_active
                else (theme.prompt if is_renaming else theme.panel_line)
            )
            # One call, one language: the seat in turn is "selected", the seat
            # being renamed is "focused", the one merely being looked at gets
            # the ordinary hover.  No tile invents its own look any more.
            style = r.emphasis(
                fill=fill, border=border, text=theme.text_light,
                selected=is_active, focused=is_renaming,
                hover=0.55 if (is_viewed and not is_active) else 0.0,
                accent=theme.prompt if is_renaming else theme.accent,
            )
            rect = r.interactive_panel(rect, style, surface, radius=9)

            # The player's colour as a bar down the left edge, like the concept.
            stripe = pygame.Rect(rect.left + 4, rect.top + 5, 5, rect.height - 10)
            pygame.draw.rect(surface, player.color, stripe, border_radius=3)
            pygame.draw.rect(surface, lighten(player.color, 0.45),
                             pygame.Rect(stripe.left, stripe.top, 5, 4),
                             border_radius=2)

            # ``display_text`` puts the caret where the caret actually is, so
            # arrow keys and Home/End are visible while renaming.  Appending it
            # was only ever correct while the caret could not move.
            name = rename.display_text() if is_renaming else player.name  # type: ignore[union-attr]
            if is_owned:
                name = f"{name} (Ty)"
            text_colour = (
                theme.btn_active_text if is_active
                else (theme.prompt_bright if is_renaming else theme.text_light)
            )
            if is_renaming and rename.showing_placeholder:  # type: ignore[union-attr]
                # A hint is not content.  Dimmer than anything the player could
                # have typed, so the two can never be confused — the same rule
                # the menu fields follow.
                text_colour = darken(theme.text_dim, 0.72)
            r.text(name, r.fonts.tile_name(), text_colour, surface,
                   center=(rect.centerx + 2, int(rect.top + rect.height * 0.40)),
                   shadow=is_active)
            r.text(f"kart: {len(player.hand)}",
                   r.fonts.get(int(14 * layout.ui_scale), bold=True),
                   theme.text_heading if is_active else theme.text_dim, surface,
                   midbottom=(rect.centerx + 2, rect.bottom - 7))

            if is_active:
                # A small crown over the seat whose turn it is.
                self._crown(r, surface, (rect.right - 16, rect.top + 3),
                            theme.brass_bright)
            if is_owned and not is_active:
                r.diamond((rect.left + 16, rect.top + 9), 3, theme.accent, surface)
            elif is_viewed and not is_owned:
                r.text("podgląd", r.fonts.label(), theme.text_dim, surface,
                       midtop=(rect.centerx, rect.top + 3))
            elif not controllable and not is_owned:
                r.diamond((rect.left + 16, rect.top + 9), 3,
                          darken(theme.text_dim, 0.7), surface)

            pencil = layout.rename_rect(rect)
            hovered = pencil.collidepoint(ctx.mouse)
            pencil_style = r.emphasis(
                fill=theme.panel_bg, border=theme.panel_line,
                text=theme.brass, hover=1.0 if hovered else 0.0,
                focused=is_renaming, quiet=True, accent=theme.prompt,
            )
            drawn = r.interactive_panel(pencil, pencil_style, surface, radius=4)
            pygame.draw.line(surface, lighten(pencil_style.text, 0.3 if hovered else 0.0),
                             (drawn.left + 4, drawn.bottom - 4),
                             (drawn.right - 4, drawn.top + 4), 2)

            if player.eliminated:
                # ONE LARGE X ACROSS THE WHOLE TILE, drawn last so it crosses
                # out the name, the card count and the pencil together — the
                # character is out of the game, not merely unavailable.  On the
                # existing seat tiles rather than in a second player list, so
                # the crossed-out seat keeps its place in the same row and
                # everybody can see whose turns are being skipped.
                self._elimination_cross(r, surface, rect, theme)

    @staticmethod
    def _elimination_cross(r, surface, rect, theme) -> None:
        """The X over a seat that is out of the game."""
        inset = 6
        box = rect.inflate(-inset * 2, -inset * 2)
        for width, colour in ((5, darken(theme.invalid, 0.55)),
                              (3, theme.invalid)):
            pygame.draw.line(surface, colour, box.topleft, box.bottomright, width)
            pygame.draw.line(surface, colour, box.bottomleft, box.topright, width)

    def handle_click(self, ctx: HudContext, button: int, ui) -> List[cmd.Command]:
        if button != 1:
            return []
        layout, state = ctx.layout, ctx.state
        count = len(state.players)
        for i in range(count):
            rect = layout.player_tile_rect(i, count)
            if not rect.collidepoint(ctx.mouse) and \
                    not layout.rename_rect(rect).collidepoint(ctx.mouse):
                continue
            if not state.may_control(i):
                # Looking is allowed while testing; acting is not, and the
                # engine has the final say either way.
                if ui.may_view(i):
                    ui.view_seat = i
                    return []
                return [cmd.SetActivePlayer(player_index=i)]  # engine refuses
            if layout.rename_rect(rect).collidepoint(ctx.mouse):
                ui.start_rename(i)
                return []
            ui.view_seat = i
            if state.edit_mode:
                return [cmd.SetActivePlayer(player_index=i)]
            return []
        return []


# ── recently played ──────────────────────────────────────────────────────────
class RecentlyPlayed(Panel):
    """The last few cards played, floating over the top-right of the board.

    Cards arrive with a short flight from the middle of the board, sit for long
    enough to be read, then fade. Hovering one lifts it out of the strip,
    enlarges it and draws it above the rest of the interface, so a card that has
    already shrunk into the corner can still be read without any hurry.

    It exists because a played card now *does* something — without this the pawn
    would move and nobody would see why. When the game goes online this panel is
    the shared log of what everyone did, which is why it is fed from
    ``CardPlayed`` events rather than from the click that caused them.
    """

    #: Seconds a card stays fully visible before it starts to fade.
    HOLD = 11.0
    FADE = 2.2
    #: How much a hovered card grows, and how fast it gets there.
    HOVER_SCALE = 2.15
    HOVER_SPEED = 13.0

    @dataclass
    class Entry:
        card: Card
        title: str
        description: str
        player_name: str
        colour: Color
        age: float = 0.0
        arrival: float = 0.0
        hover: float = 0.0

    def __init__(self, limit: int = 3) -> None:
        self.entries: List["RecentlyPlayed.Entry"] = []
        self.limit = limit
        self.hovered: Optional[int] = None
        self._rects: List[pygame.Rect] = []

    def push(self, card: Card, player_name: str, description: str, colour: Color) -> None:
        self.entries.insert(0, RecentlyPlayed.Entry(
            card=card, title=card.title, description=description,
            player_name=player_name, colour=colour,
        ))
        del self.entries[self.limit:]

    def update(self, dt: float, mouse: Optional[Tuple[int, int]] = None) -> None:
        self.hovered = None
        if mouse is not None:
            for index, rect in enumerate(self._rects):
                if index < len(self.entries) and rect.collidepoint(mouse):
                    self.hovered = index
                    break

        for index, entry in enumerate(self.entries):
            entry.age += dt
            entry.arrival = min(1.0, entry.arrival + dt * 3.2)
            wanted = 1.0 if index == self.hovered else 0.0
            entry.hover = approach(entry.hover, wanted, self.HOVER_SPEED, dt)
            if index == self.hovered:
                # Looking at a card keeps it alive; it would be irritating for a
                # card to fade out from under the cursor.
                entry.age = min(entry.age, self.HOLD * 0.8)

        self.entries = [e for e in self.entries if e.age < self.HOLD + self.FADE]
        del self.entries[self.limit:]

    def _alpha(self, entry: "RecentlyPlayed.Entry") -> int:
        if entry.age <= self.HOLD:
            return 255
        faded = 1.0 - (entry.age - self.HOLD) / self.FADE
        return int(255 * max(0.0, faded))

    def draw(self, ctx: HudContext) -> None:
        """The strip itself. The hovered card is left to :meth:`draw_overlay`."""
        layout = ctx.layout
        self._rects = [layout.recent_slot_rect(i) for i in range(self.limit)]
        if not self.entries:
            return

        r, theme, surface = ctx.r, ctx.theme, ctx.surface
        # A framed shelf rather than cards floating on the board.
        label_x, label_y = layout.recent_label_pos
        shelf = layout.recent_slot_rect(min(self.limit, len(self.entries)) - 1).union(
            layout.recent_slot_rect(0)
        ).inflate(20, 46)
        shelf.top = label_y - 6
        r.premium_panel(shelf, surface, radius=10, ornaments=False, shadow=12)
        r.spaced_text("OSTATNIO ZAGRANE",
                      r.fonts.get(int(11 * layout.ui_scale), bold=True),
                      theme.text_heading, surface,
                      center=(shelf.centerx, label_y + 6), spacing=2)

        for index, entry in enumerate(self.entries[: self.limit]):
            if entry.hover > 0.02:
                continue  # drawn on top of everything else instead
            self._draw_entry(ctx, index, entry)

    def draw_overlay(self, ctx: HudContext) -> None:
        """Draw the hovered card last, above every other panel."""
        for index, entry in enumerate(self.entries[: self.limit]):
            if entry.hover > 0.02:
                self._draw_entry(ctx, index, entry)

    def _draw_entry(self, ctx: HudContext, index: int, entry: "Entry") -> None:
        r, layout, theme = ctx.r, ctx.layout, ctx.theme
        surface = ctx.surface
        board = layout.board_viewport
        size = layout.recent_card_size()
        rect = layout.recent_slot_rect(index)

        # Fly in from the middle of the board, then settle into the strip.
        eased = 1 - (1 - entry.arrival) ** 3
        x = board.centerx + (rect.centerx - board.centerx) * eased
        y = board.centery + (rect.centery - board.centery) * eased
        scale = (0.7 + 0.3 * eased) + entry.hover * (self.HOVER_SCALE - 1.0)

        # An enlarged card grows towards the middle of the board so it never
        # spills off the right-hand edge of the screen.
        grown_w = size[0] * scale
        grown_h = size[1] * scale
        x -= entry.hover * max(0.0, (rect.centerx + grown_w / 2) - (board.right - 12))
        y += entry.hover * max(0.0, (board.top + 12) - (rect.centery - grown_h / 2))

        alpha = self._alpha(entry)
        # Repaint at the size it will be seen rather than zooming the small
        # face: an enlarged card is meant to be *read*, and a blown-up
        # rasterisation of 95×137 pixels is exactly what made it unreadable.
        paint_size = ctx.cards.quantised(size, scale)
        face = ctx.cards.face(entry.card, paint_size, border_color=entry.colour,
                              highlighted=entry.hover > 0.5)
        if alpha < 255:
            face = face.copy()
            face.set_alpha(alpha)
        blit_rect = face.get_rect(center=(int(x), int(y)))

        if entry.hover > 0.02:
            r.drop_shadow(blit_rect.inflate(-14, -14), radius=10,
                          spread=int(10 + 16 * entry.hover), alpha=150,
                          offset=(0, int(4 + 6 * entry.hover)), surface=surface)
        else:
            r.drop_shadow(blit_rect.inflate(-10, -10), radius=8, spread=8, alpha=90,
                          offset=(0, 3), surface=surface)
        surface.blit(face, blit_rect.topleft)

        if alpha > 120 and eased > 0.6:
            caption = entry.description or entry.player_name
            font = r.fonts.deck() if entry.hover > 0.5 else r.fonts.label()
            r.text(f"{entry.player_name}: {caption}", font, theme.text_light, surface,
                   midtop=(blit_rect.centerx, blit_rect.bottom + 4), shadow=True)


# ── status bar ───────────────────────────────────────────────────────────────
class StatusBar(Panel):
    def __init__(self) -> None:
        self.message: Optional[str] = None
        self.message_timer = 0.0

    def notify(self, message: str, duration: float = 3.5) -> None:
        self.message = message
        self.message_timer = duration

    def clear(self) -> None:
        self.message = None
        self.message_timer = 0.0

    def update(self, dt: float) -> None:
        if self.message_timer > 0:
            self.message_timer -= dt
            if self.message_timer <= 0:
                self.message = None

    def draw(self, ctx: HudContext) -> None:
        r, layout, theme = ctx.r, ctx.layout, ctx.theme
        surface = ctx.surface
        rect = layout.status_bar

        if self.message:
            text, colour = self.message, theme.prompt
        elif ctx.pending_mod_uid is not None:
            text = "UMIESZCZANIE — kliknij slot modów  ·  P-click anuluje"
            colour = theme.mod_select_ring
        else:
            text = (
                "Ctrl+kółko: zoom  ·  środkowy przycisk: przesuwanie  ·  "
                "S: przyciąganie  ·  F: dopasuj widok  ·  Tab: następny gracz"
            )
            colour = theme.status

        surface_text = r.text_surface(text, r.fonts.status(), colour)
        box = surface_text.get_rect(midleft=(rect.left + 12, rect.centery))
        backdrop = box.inflate(22, 10)
        r.premium_panel(backdrop, surface, radius=8, fill=theme.panel_inset,
                        ornaments=False, shadow=8)
        surface.blit(surface_text, box.topleft)
