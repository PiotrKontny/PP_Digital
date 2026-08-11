"""
Overlays.

Three things now interrupt the ordinary screen, and all three are driven by
engine events rather than by the click that caused them — which is what will
let a remote player's decision appear on everybody's table later.

* :class:`ChoicePrompt` — the engine needs an answer (which pawn? which half of
  a doubled field? one space or two?).  One panel handles every question,
  because the engine describes what it is asking rather than the interface
  knowing about particular cards.
* :class:`RevealOverlay` — a card announcing itself in the middle of the
  screen: Gamechanger turning into Alter Ego or Kingmaker, and the random card
  Seks z pedałami turns up.
* :class:`ChestChoice` — the chest hand limit, laying the candidates out side
  by side so the player can pick what to keep.
* :class:`CardPicker` — a row of somebody else's cards to choose one from
  (Spy).  Deliberately generic: it is handed a list of cards and a title and
  knows nothing about which card opened it.
* :class:`ModChoice` — the Mod Patusa selection that pauses a round.  One
  overlay serves both factions: Piotrek clicks to keep, hunters click to vote
  and watch the count build up.
* :class:`ChestReveal` — Paczka's window: who is holding which Chest cards.
  Purely informational, shown on every machine at once and dismissed by each
  player independently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pygame

from ..cards.base_card import Card
from ..config.theme import darken, lighten
from ..engine.animation import approach, ease_out_cubic
from ..render.card_renderer import CardRenderer
from ..render.renderer import Renderer
from .layout import Layout

Color = Tuple[int, int, int]


def _dim(surface: pygame.Surface, rect: pygame.Rect, alpha: int = 150) -> None:
    """Darken everything behind an overlay so the eye goes to the front."""
    shade = pygame.Surface(rect.size, pygame.SRCALPHA)
    shade.fill((6, 10, 8, alpha))
    surface.blit(shade, rect.topleft)


# ── the engine is asking something ───────────────────────────────────────────
@dataclass
class ChoiceOptionView:
    id: str
    label: str
    rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    color: Optional[Color] = None
    hover: float = 0.0


class ChoicePrompt:
    """A question from the engine, answered by clicking.

    Pawn questions also accept a click on the pawn itself out on the board, and
    field questions expect one — the buttons are the reliable path, the board is
    the natural one.
    """

    def __init__(self) -> None:
        self.active = False
        self.prompt = ""
        self.description = ""
        self.kind = "option"
        self.options: List[ChoiceOptionView] = []
        self.appear = 0.0
        #: How many answers the engine wants, and whether their order matters.
        self.count = 1
        self.ordered = False
        #: The picks so far, in the order they were made.  Empty for the
        #: ordinary one-answer question, where picking IS answering.
        self.selected: List[str] = []
        self.confirm_rect = pygame.Rect(0, 0, 0, 0)

    def show(self, prompt: str, kind: str, options: Sequence[Tuple[str, str]],
             description: str = "", colors: Optional[Dict[str, Color]] = None,
             count: int = 1, ordered: bool = False) -> None:
        self.active = True
        self.prompt = prompt
        self.kind = kind
        self.description = description
        self.appear = 0.0
        self.count = max(1, count)
        self.ordered = ordered
        self.selected = []
        self.options = [
            ChoiceOptionView(id=oid, label=label, color=(colors or {}).get(oid))
            for oid, label in options
        ]

    def hide(self) -> None:
        self.active = False
        self.options = []
        self.selected = []

    @property
    def is_pawn_choice(self) -> bool:
        return self.kind == "pawn"

    # ── multi-select ─────────────────────────────────────────────────────────
    @property
    def is_multi(self) -> bool:
        return self.count > 1

    @property
    def ready(self) -> bool:
        """Exactly the number asked for — not at least, not at most."""
        return len(self.selected) == self.count

    def toggle(self, option_id: str) -> None:
        """Add a pick, or take it back.

        Clicking something already chosen removes it and everything after it
        keeps its relative order, so the numbers close up: picking green then
        pink then unpicking green leaves pink as number one.  That is what the
        numbers promise, and a list that renumbered by rewriting history would
        break the promise.
        """
        if option_id in self.selected:
            self.selected.remove(option_id)
            return
        if len(self.selected) >= self.count:
            # Full: the oldest pick makes way, which is friendlier than a click
            # that does nothing and a player wondering why.
            self.selected.pop(0)
        self.selected.append(option_id)

    def order_of(self, option_id: str) -> Optional[int]:
        """1-based position in the selection, or None when it is not picked."""
        if option_id not in self.selected:
            return None
        return self.selected.index(option_id) + 1

    def confirm_hit(self, position: Tuple[int, int]) -> bool:
        return self.ready and self.confirm_rect.collidepoint(position)

    # ── geometry ─────────────────────────────────────────────────────────────
    def _lay_out(self, layout: Layout) -> pygame.Rect:
        panel = layout.choice_prompt
        if not self.options:
            return panel
        gap = 14 if self.is_pawn_choice else 10
        count = len(self.options)

        if self.is_pawn_choice:
            # Pawn choices are drawn as the pawns themselves, so the hit area is
            # a square around the token plus room for its name underneath.
            size = min(74, max(40, (panel.width - (count + 1) * gap) // count))
            size = min(size, panel.height - 62)
            width = height = size
        else:
            width = min(190, max(90, (panel.width - (count + 1) * gap) // count))
            height = min(52, panel.height - 56)

        total = count * width + (count - 1) * gap
        x = panel.centerx - total // 2
        y = panel.bottom - height - (26 if self.is_pawn_choice else 14)
        for option in self.options:
            option.rect = pygame.Rect(x, y, width, height)
            x += width + gap
        self.confirm_rect = (layout.choice_confirm_rect() if self.is_multi
                             else pygame.Rect(0, 0, 0, 0))
        return panel

    def option_at(self, position: Tuple[int, int]) -> Optional[str]:
        for option in self.options:
            if option.rect.collidepoint(position):
                return option.id
        return None

    # ── frame ────────────────────────────────────────────────────────────────
    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 14.0, dt)
        for option in self.options:
            wanted = 1.0 if option.rect.collidepoint(mouse) else 0.0
            option.hover = approach(option.hover, wanted, 16.0, dt)

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        panel = self._lay_out(layout)
        eased = ease_out_cubic(min(1.0, self.appear))
        slide = int((1.0 - eased) * 26)
        panel = panel.move(0, slide)

        r.premium_panel(panel, surface, radius=14, border=r.theme.brass_light,
                        glow=r.theme.brass, glow_strength=0.35, shadow=18)
        r.spaced_text(self.prompt.upper(), r.fonts.get(19, bold=True), theme.prompt, surface,
              center=(panel.centerx, panel.top + 22), spacing=2, shadow=True)
        if self.description:
            r.text(self.description, r.fonts.deck(), theme.text_dim, surface,
                   midtop=(panel.centerx, panel.top + 36))

        for option in self.options:
            rect = option.rect.move(0, slide - int(option.hover * 4))
            picked = self.order_of(option.id)
            if picked is not None:
                # A chosen option sits proud of the row, so the selection reads
                # at a glance and not only from the little number on it.
                rect = rect.move(0, -6)
            if self.is_pawn_choice:
                self._draw_pawn_option(r, option, rect, surface)
            else:
                self._draw_button_option(r, option, rect, surface)
            if picked is not None:
                self._draw_pick_number(r, rect, picked, surface)

        if self.is_multi:
            self._draw_confirm(r, surface, slide, mouse)
            hint = f"wybrano {len(self.selected)}/{self.count}  ·  Esc anuluje"
        else:
            hint = "Esc anuluje"
        r.text(hint, r.fonts.label(), theme.text_dim, surface,
               midbottom=(panel.centerx, panel.bottom - 2))

    def _draw_pick_number(self, r: Renderer, rect: pygame.Rect, order: int,
                          surface: pygame.Surface) -> None:
        """The ① ② badge saying when this one moves.

        Drawn as a number rather than as circled digits from the font, because
        those only go up to twenty and are missing from plenty of the fonts a
        player might have installed.
        """
        theme = r.theme
        radius = max(9, rect.width // 7)
        centre = (rect.right - radius // 2, rect.top + radius // 2)
        r.aa_circle(centre, radius, theme.prompt_bright, darken(theme.prompt, 0.6),
                    2, surface)
        r.text(str(order), r.fonts.get(max(12, radius + 2), bold=True),
               theme.ink, surface, center=centre)

    def _draw_confirm(self, r: Renderer, surface: pygame.Surface, slide: int,
                      mouse: Tuple[int, int]) -> None:
        """Only live once EXACTLY the requested number has been picked."""
        theme = r.theme
        rect = self.confirm_rect.move(0, slide)
        enabled = self.ready
        hovered = enabled and rect.collidepoint(mouse)
        style = r.emphasis(fill=theme.btn_primary_bg,
                           border=theme.btn_primary_border,
                           text=theme.btn_primary_text,
                           hover=1.0 if hovered else 0.0, enabled=enabled,
                           accent=theme.brass_light)
        drawn = r.interactive_panel(rect, style, surface, radius=9)
        label = "ZATWIERDŹ" if enabled else f"WYBIERZ {self.count}"
        r.fit_spaced_text(label, drawn, style.text, surface,
                          base_size=15, spacing=2, padding=12)


    def _draw_pawn_option(self, r: Renderer, option: ChoiceOptionView,
                          rect: pygame.Rect, surface: pygame.Surface) -> None:
        """A pawn choice is drawn as the pawn, not as a button with its name.

        The player is picking a token on the board; showing them a row of
        labelled rectangles would make them read where they should just look.
        """
        colour = option.color or r.theme.brass
        radius = rect.width // 2 - 2
        centre = (rect.centerx, rect.centery)

        # A lit rim around the token, not a lamp behind it: the pawn is round,
        # so its highlight is a ring that reaches a fixed fraction past its own
        # edge rather than a disc two and a half times its size.
        r.ring_glow(centre, radius, lighten(colour, 0.3), surface,
                    strength=0.45 + 0.55 * option.hover)
        r.drop_shadow(pygame.Rect(0, 0, radius * 2, radius * 2).move(
            centre[0] - radius, centre[1] - radius + 4),
            radius=radius, spread=6, alpha=120, offset=(0, 3), surface=surface)

        r.aa_circle(centre, radius, colour, darken(colour, 0.45),
                    3 if option.hover > 0.4 else 2, surface)
        # A highlight blob, so the token reads as a physical piece.
        r.soft_ellipse((centre[0] - radius * 0.28, centre[1] - radius * 0.34),
                       radius * 0.42, radius * 0.3, lighten(colour, 0.75),
                       alpha=150, surface=surface)
        if option.hover > 0.05:
            r.aa_ring(centre, radius + 3 + int(option.hover * 3),
                      r.theme.prompt_bright, 2, surface)

        r.text(option.label, r.fonts.label(),
               r.theme.prompt_bright if option.hover > 0.4 else r.theme.text_dim, surface,
               midtop=(rect.centerx, rect.bottom + 3), shadow=True)

    def _draw_button_option(self, r: Renderer, option: ChoiceOptionView,
                            rect: pygame.Rect, surface: pygame.Surface) -> None:
        """An option in a decision dialog — a button like any other button.

        These used to grow a halo of their own; a dialog that asks a question
        should look like the rest of the interface, not announce itself.
        """
        base = option.color or r.theme.btn_idle_bg
        text = r.theme.text_light if sum(base) < 420 else r.theme.ink
        style = r.emphasis(fill=base, border=darken(base, 0.7), text=text,
                           hover=option.hover, accent=r.theme.prompt)
        drawn = r.interactive_panel(rect, style, surface, radius=9)
        r.fit_text(option.label, drawn, style.text, surface, base_size=17,
                   padding=10)


# ── a card announcing itself ─────────────────────────────────────────────────
@dataclass
class RevealPhase:
    """One card, held up for a few seconds.

    ``halo`` is what makes a spotlight different from an announcement: a card
    the game chose FOR the player gets a pulsing ring, because they are about
    to lose a turn to it and "look at this" has to carry further than "here is
    what you drew".
    """

    title: str
    text: str
    seconds: float
    color: Optional[Color] = None
    subtitle: str = ""
    halo: bool = False


class RevealOverlay:
    """A big card in the middle of the screen, in one or two phases.

    Gamechanger uses two: it shows itself, then turns into Alter Ego or
    Kingmaker with a flip.  A random reveal uses two as well: the card that
    caused it, then the card it found.
    """

    FLIP_SECONDS = 0.45

    def __init__(self) -> None:
        self.phases: List[RevealPhase] = []
        self.index = 0
        self.elapsed = 0.0
        self.flip = 0.0
        self.card: Optional[Card] = None

    @property
    def active(self) -> bool:
        return self.index < len(self.phases)

    def show(self, phases: Sequence[RevealPhase], card: Optional[Card] = None) -> None:
        self.phases = list(phases)
        self.index = 0
        self.elapsed = 0.0
        self.flip = 0.0
        self.card = card

    def card_rect(self, layout: Layout) -> pygame.Rect:
        size = layout.reveal_card_size
        rect = pygame.Rect(0, 0, size[0], size[1])
        rect.center = layout.reveal_centre
        return rect

    def hit(self, layout: Layout, position: Tuple[int, int]) -> bool:
        return self.active and self.card_rect(layout).collidepoint(position)

    def dismiss(self) -> None:
        self.phases = []
        self.index = 0
        self.card = None

    def update(self, dt: float) -> None:
        if not self.active:
            return
        self.elapsed += dt
        phase = self.phases[self.index]
        if self.elapsed >= phase.seconds:
            self.elapsed = 0.0
            self.index += 1
            self.flip = 1.0 if self.index < len(self.phases) else 0.0
        if self.flip > 0:
            self.flip = max(0.0, self.flip - dt / self.FLIP_SECONDS)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface) -> None:
        if not self.active:
            return
        theme = r.theme
        phase = self.phases[self.index]
        size = layout.reveal_card_size
        centre = layout.reveal_centre
        _dim(surface, layout.board_viewport, 130)

        # The flip: squeeze the card horizontally as one face becomes the other.
        squeeze = abs(math.cos(math.pi * (1.0 - self.flip))) if self.flip > 0 else 1.0
        squeeze = max(0.06, squeeze)
        entrance = min(1.0, self.elapsed / 0.28)
        scale = 0.86 + 0.14 * ease_out_cubic(entrance)

        card_w = max(8, int(size[0] * squeeze * scale))
        card_h = int(size[1] * scale)
        rect = pygame.Rect(0, 0, card_w, card_h)
        rect.center = centre

        colour = phase.color or theme.brass_light
        r.drop_shadow(rect, radius=16, spread=26, alpha=170, offset=(0, 10),
                      surface=surface)
        r.rounded_gradient(surface, rect, theme.card_bg, theme.card_bg_shade, 16)
        # One border here too (stage 31).  This overlay paints its own card
        # rather than calling ``CardRenderer.face``, so it carried a second
        # copy of the old inset rule; leaving it would have made the reveal the
        # only place in the game where the double frame survived.
        pygame.draw.rect(surface, colour, rect, 4, border_radius=16)

        if squeeze > 0.45:
            pad = int(card_w * 0.09)
            title_font = r.fonts.get(max(16, int(card_h * 0.075)), bold=True)
            for line in r.wrap_lines(phase.title, title_font, card_w - 2 * pad)[:2]:
                r.text(line, title_font, darken(colour, 0.6), surface,
                       midtop=(rect.centerx, rect.top + int(card_h * 0.08)))
                break
            body_font = r.fonts.get(max(12, int(card_h * 0.048)))
            r.draw_wrapped(
                phase.text, body_font, theme.card_text,
                pygame.Rect(rect.left + pad, rect.top + int(card_h * 0.26),
                            card_w - 2 * pad, card_h - int(card_h * 0.34)),
                surface,
            )
            if phase.subtitle:
                r.text(phase.subtitle, r.fonts.deck(), theme.prompt_bright, surface,
                       midtop=(rect.centerx, rect.bottom + 12), shadow=True)

        if phase.halo:
            # The card's OWN rectangle lights up (N44): no disc behind it, and
            # nothing that reaches past its corners.  The pulse is a sine of
            # elapsed time, so it breathes at the same rate whatever the frame
            # rate is doing.
            pulse = 0.5 + 0.5 * math.sin(self.elapsed * 5.0)
            r.shape_glow(rect, theme.prompt_bright, surface, radius=16,
                         strength=0.55 + 0.45 * pulse)
            pygame.draw.rect(surface, theme.prompt_bright, rect.inflate(10, 10),
                             3, border_radius=18)


# ── the chest hand limit ─────────────────────────────────────────────────────
class ChestChoice:
    """Pick which chest cards to keep when a draw goes over the limit."""

    def __init__(self) -> None:
        self.active = False
        self.cards: List[Card] = []
        self.new_uid: Optional[int] = None
        self.limit = 1
        self.keep: List[int] = []
        self.appear = 0.0
        self.hover: Dict[int, float] = {}

    def show(self, cards: Sequence[Card], limit: int, new_uid: Optional[int]) -> None:
        self.active = True
        self.cards = list(cards)
        self.limit = limit
        self.new_uid = new_uid
        # Start with the newest card selected: it is the one the player just
        # chose to draw, so it is the likeliest thing they want.
        self.keep = [new_uid] if new_uid is not None and limit > 0 else []
        self.appear = 0.0
        self.hover = {}

    def hide(self) -> None:
        self.active = False
        self.cards = []
        self.keep = []

    @property
    def ready(self) -> bool:
        return len(self.keep) == self.limit

    def toggle(self, uid: int) -> None:
        if uid in self.keep:
            self.keep.remove(uid)
        elif len(self.keep) < self.limit:
            self.keep.append(uid)
        else:
            # Replacing the oldest selection is friendlier than refusing the
            # click and making the player deselect something first.
            self.keep.pop(0)
            self.keep.append(uid)

    def card_at(self, layout: Layout, position: Tuple[int, int]) -> Optional[int]:
        for index, card in enumerate(self.cards):
            if layout.chest_choice_card_rect(index, len(self.cards)).collidepoint(position):
                return card.uid
        return None

    def confirm_hit(self, layout: Layout, position: Tuple[int, int]) -> bool:
        return (
            self.ready
            and layout.chest_confirm_rect(len(self.cards)).collidepoint(position)
        )

    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 12.0, dt)
        for index, card in enumerate(self.cards):
            rect = layout.chest_choice_card_rect(index, len(self.cards))
            wanted = 1.0 if rect.collidepoint(mouse) else 0.0
            self.hover[card.uid] = approach(self.hover.get(card.uid, 0.0), wanted,
                                            16.0, dt)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        count = len(self.cards)
        panel = layout.chest_choice_panel(count)
        _dim(surface, surface.get_rect(), int(150 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.4, shadow=22)

        r.spaced_text("LIMIT KART SKRZYNI", r.fonts.get(22, bold=True),
                      theme.brass_bright, surface,
                      center=(panel.centerx, panel.top + 24), spacing=3, shadow=True)
        r.text(
            f"Zatrzymaj {self.limit} — resztę odrzucisz "
            f"(wybrano {len(self.keep)}/{self.limit})",
            r.fonts.deck(), theme.text_dim, surface,
            midtop=(panel.centerx, panel.top + 38),
        )

        size = layout.chest_choice_card_size(count)
        for index, card in enumerate(self.cards):
            rect = layout.chest_choice_card_rect(index, count)
            hover = self.hover.get(card.uid, 0.0)
            chosen = card.uid in self.keep
            lift = int(10 * hover + (10 if chosen else 0))
            draw_rect = rect.move(0, -lift)
            border = theme.valid if chosen else theme.deck_colors.get(card.deck_id)
            if chosen or hover > 0.02:
                # Was a circle drawn around a rectangular card, which reached a
                # long way past both of its corners.  Now the card's own outline
                # lights up.
                r.shape_glow(draw_rect, theme.valid if chosen else theme.prompt,
                             surface, radius=10,
                             strength=0.7 if chosen else 0.35 * hover)
            cards.draw_in(card, draw_rect, surface, highlighted=chosen or hover > 0.5,
                          border_color=border)
            if chosen:
                pygame.draw.rect(surface, theme.valid, draw_rect.inflate(6, 6), 3,
                                 border_radius=10)
                r.text("ZATRZYMUJESZ", r.fonts.label(), theme.valid, surface,
                       midtop=(draw_rect.centerx, draw_rect.bottom + 8), shadow=True)
            elif card.uid == self.new_uid:
                r.text("nowa karta", r.fonts.label(), theme.prompt, surface,
                       midtop=(draw_rect.centerx, draw_rect.bottom + 4), shadow=True)

        confirm = layout.chest_confirm_rect(count)
        enabled = self.ready
        hovered = enabled and confirm.collidepoint(mouse)
        style = r.emphasis(fill=theme.btn_primary_bg,
                           border=theme.btn_primary_border,
                           text=theme.btn_primary_text,
                           hover=1.0 if hovered else 0.0, enabled=enabled,
                           accent=theme.brass_light)
        drawn = r.interactive_panel(confirm, style, surface, radius=10)
        r.fit_spaced_text("ZATWIERDŹ" if enabled else f"WYBIERZ {self.limit}",
                          drawn, style.text, surface, base_size=17, spacing=2,
                          padding=14)


# ── choosing the Mods Patusa ─────────────────────────────────────────────────
class ModChoice:
    """Three Mods Patusa to choose from — by clicking, or by voting.

    ONE overlay for both factions, because they are the same picture with a
    different rule underneath.  Piotrek clicks a card and it is his; a hunter
    clicks a card and that is a vote, which shows up on every hunter's screen
    with a running count.  Splitting them into two overlays would have meant
    maintaining the same card lineup twice.

    Like every other overlay here it is told nothing about *why* it is open: it
    is handed cards, a mode and a tally, and it answers with a uid.  Whether
    that uid becomes a ``ChooseMod`` or a ``VoteMod`` is the screen's problem.
    """

    def __init__(self) -> None:
        self.active = False
        self.cards: List[Card] = []
        #: "piotrek" — click to keep; "hunters" — click to vote; "waiting" —
        #: this seat has nothing to decide and is watching the other side.
        self.mode = "piotrek"
        self.title = ""
        self.caption = ""
        #: uid → votes, straight from the engine.  Never counted here: the
        #: engine owns the tally, and a screen that recomputed it would be a
        #: second opinion about who won.
        self.tally: Dict[int, int] = {}
        #: The uid this seat voted for, so its own tick can be picked out.
        self.my_vote: Optional[int] = None
        self.voted = 0
        self.voters = 0
        #: Set once this seat has committed, which greys the panel out without
        #: closing it — the vote is still running and the counts still move.
        self.settled = False
        self.appear = 0.0
        self.elapsed = 0.0
        self.hover: Dict[int, float] = {}
        #: Per-card animation of the vote badge, so a tick grows in rather than
        #: appearing between two frames.
        self.badge: Dict[int, float] = {}
        #: Cards that just gained or lost a vote, pulsed for a moment.
        self.pulse: Dict[int, float] = {}

    def show(self, cards: Sequence[Card], mode: str, title: str,
             caption: str = "") -> None:
        self.active = True
        self.cards = list(cards)
        self.mode = mode
        self.title = title
        self.caption = caption
        self.tally = {card.uid: 0 for card in self.cards}
        self.my_vote = None
        self.voted = 0
        self.voters = 0
        self.settled = False
        self.appear = 0.0
        self.elapsed = 0.0
        self.hover = {}
        self.badge = {}
        self.pulse = {}

    def hide(self) -> None:
        self.active = False
        self.cards = []
        self.hover = {}
        self.badge = {}
        self.pulse = {}

    def set_tally(self, tally: Dict[int, int], voted: int, voters: int) -> None:
        """Adopt the engine's counts, pulsing whatever changed."""
        for uid, count in tally.items():
            if count != self.tally.get(uid, 0):
                self.pulse[uid] = 1.0
        self.tally = dict(tally)
        self.voted = voted
        self.voters = voters

    @property
    def interactive(self) -> bool:
        """Whether a click on a card means anything for this seat."""
        return self.active and not self.settled and self.mode in ("piotrek", "hunters")

    def card_at(self, layout: Layout, position: Tuple[int, int]) -> Optional[int]:
        count = len(self.cards)
        for index, card in enumerate(self.cards):
            if layout.mod_choice_card_rect(index, count).collidepoint(position):
                return card.uid
        return None

    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 12.0, dt)
        self.elapsed += dt
        count = len(self.cards)
        live = self.interactive
        for index, card in enumerate(self.cards):
            rect = layout.mod_choice_card_rect(index, count)
            wanted = 1.0 if (live and rect.collidepoint(mouse)) else 0.0
            self.hover[card.uid] = approach(self.hover.get(card.uid, 0.0),
                                            wanted, 16.0, dt)
            # A badge is only shown for a card with votes on it; easing the
            # target rather than the presence is what makes it grow and shrink
            # instead of blinking.
            target = 1.0 if self.tally.get(card.uid, 0) > 0 else 0.0
            self.badge[card.uid] = approach(self.badge.get(card.uid, 0.0),
                                            target, 12.0, dt)
            self.pulse[card.uid] = approach(self.pulse.get(card.uid, 0.0),
                                            0.0, 3.2, dt)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        count = len(self.cards)
        if count == 0:
            return
        panel = layout.mod_choice_panel(count)
        _dim(surface, surface.get_rect(), int(165 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.4, shadow=22)

        r.spaced_text(self.title.upper(), r.fonts.get(22, bold=True),
                      theme.brass_bright, surface,
                      center=(panel.centerx, panel.top + 26), spacing=3,
                      shadow=True)
        if self.caption:
            r.text(self.caption, r.fonts.deck(), theme.text_dim, surface,
                   midtop=(panel.centerx, panel.top + 42))

        for index, card in enumerate(self.cards):
            self._draw_card(r, cards, layout, surface, index, card, count)

        self._draw_footer(r, layout, surface, panel)

    def _draw_card(self, r: Renderer, cards: CardRenderer, layout: Layout,
                   surface: pygame.Surface, index: int, card: Card,
                   count: int) -> None:
        theme = r.theme
        rect = layout.mod_choice_card_rect(index, count)
        hover = self.hover.get(card.uid, 0.0)
        votes = self.tally.get(card.uid, 0)
        mine = self.my_vote == card.uid
        lift = int(12 * hover + (10 if mine else 0))
        draw_rect = rect.move(0, -lift)

        if mine or hover > 0.02:
            r.shape_glow(draw_rect, theme.valid if mine else theme.prompt,
                         surface, radius=10,
                         strength=0.7 if mine else 0.45 * hover)
        cards.draw_in(card, draw_rect, surface,
                      highlighted=mine or hover > 0.5,
                      border_color=theme.deck_colors.get(card.deck_id))
        if mine:
            pygame.draw.rect(surface, theme.valid, draw_rect.inflate(6, 6), 3,
                             border_radius=10)

        self._draw_badge(r, layout, surface, index, count, votes, mine, lift)
        if self.mode != "piotrek":
            self._draw_tally(r, surface, draw_rect, votes)

    def _draw_badge(self, r: Renderer, layout: Layout, surface: pygame.Surface,
                    index: int, count: int, votes: int, mine: bool,
                    lift: int) -> None:
        """The tick in the corner: present when anybody voted, green when it is yours.

        Piotrek's side has no votes to show, so it gets no badge at all rather
        than a badge that always reads zero.
        """
        if self.mode == "piotrek":
            return
        grown = self.badge.get(self.cards[index].uid, 0.0)
        if grown <= 0.02 and not mine:
            return
        theme = r.theme
        badge = layout.mod_vote_badge_rect(index, count).move(0, -lift)
        pulse = self.pulse.get(self.cards[index].uid, 0.0)
        scale = 0.7 + 0.3 * min(1.0, grown) + 0.12 * pulse
        size = max(6, int(badge.width * scale))
        badge = pygame.Rect(0, 0, size, size)
        badge.center = layout.mod_vote_badge_rect(index, count).move(0, -lift).center

        colour = theme.valid if mine else theme.brass_light
        pygame.draw.circle(surface, darken(colour, 0.45), badge.center,
                           badge.width // 2)
        pygame.draw.circle(surface, colour, badge.center, badge.width // 2, 2)
        # A tick rather than a number: the count lives under the card, and two
        # numbers on one card is one too many.
        left = (badge.left + badge.width * 0.26, badge.centery)
        mid = (badge.centerx - badge.width * 0.02, badge.bottom - badge.height * 0.3)
        right = (badge.right - badge.width * 0.24, badge.top + badge.height * 0.3)
        pygame.draw.lines(surface, colour, False, [left, mid, right],
                          max(2, badge.width // 9))

    def _draw_tally(self, r: Renderer, surface: pygame.Surface,
                    rect: pygame.Rect, votes: int) -> None:
        """The running count under a card, in words a table can read at a glance."""
        theme = r.theme
        colour = theme.valid if votes else theme.text_dim
        label = "1 głos" if votes == 1 else f"{votes} głosy" if 2 <= votes <= 4 \
            else f"{votes} głosów"
        r.text(label, r.fonts.get(17, bold=True), colour, surface,
               midtop=(rect.centerx, rect.bottom + 8), shadow=votes > 0)

    def _draw_footer(self, r: Renderer, layout: Layout, surface: pygame.Surface,
                     panel: pygame.Rect) -> None:
        theme = r.theme
        if self.mode == "piotrek":
            note = ("kliknij Mod, który wchodzi do gry"
                    if not self.settled else "wybrano — czekamy na Oprawców")
        elif self.mode == "hunters":
            if self.settled:
                note = "głosowanie zakończone"
            else:
                note = (f"kliknij Mod, na który głosujesz  ·  "
                        f"zagłosowało {self.voted}/{self.voters}"
                        "  ·  możesz zmienić głos")
        else:
            note = "czekamy na wybór Modów Patusa…"
        r.text(note, r.fonts.label(), theme.text_dim, surface,
               midbottom=(panel.centerx, panel.bottom - 16))


# ── pause / leave ────────────────────────────────────────────────────────────
class PauseMenu:
    """Esc menu: leave the match or quit the game.

    Deliberately short.  There is no "return to lobby" because a match cannot be
    un-started: the host would have to tear the session down and rebuild the
    lobby, and pretending otherwise would leave clients staring at a table that
    no longer exists.  Leaving sends everyone back to the main menu, which is
    the honest version of the same thing.
    """

    def __init__(self) -> None:
        self.active = False
        self.entries: List[Tuple[str, str]] = []
        self.rects: List[pygame.Rect] = []
        self.appear = 0.0
        self.hover: Optional[int] = None

    def open(self, entries: Sequence[Tuple[str, str]]) -> None:
        self.active = True
        self.entries = list(entries)
        self.appear = 0.0

    def close(self) -> None:
        self.active = False

    def _lay_out(self, layout: Layout, r: Optional[Renderer] = None) -> pygame.Rect:
        """Measured from the captions, like every other button block."""
        width, height, gap = 320, 52, 12
        if r is not None:
            from .widgets import BUTTON_PAD_X, BUTTON_SPACING, BUTTON_TEXT_SIZE

            font = r.fonts.get(BUTTON_TEXT_SIZE, bold=True)
            widest = max(
                (r.spaced_width(label.upper(), font, BUTTON_SPACING)
                 for _, label in self.entries), default=0)
            width = max(width, int(widest + 2 * BUTTON_PAD_X))
            height = max(height, font.get_height() + 30)
            width = min(width, max(200, layout.win_w - 80))
        total = len(self.entries) * height + (len(self.entries) - 1) * gap
        panel = pygame.Rect(0, 0, width + 60, total + 110)
        panel.center = (layout.win_w // 2, layout.win_h // 2)
        self.rects = [
            pygame.Rect(panel.centerx - width // 2,
                        panel.top + 76 + index * (height + gap), width, height)
            for index in range(len(self.entries))
        ]
        return panel

    def entry_at(self, position: Tuple[int, int]) -> Optional[str]:
        for (key, _), rect in zip(self.entries, self.rects):
            if rect.collidepoint(position):
                return key
        return None

    def update(self, dt: float, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 14.0, dt)
        self.hover = next(
            (i for i, rect in enumerate(self.rects) if rect.collidepoint(mouse)), None
        )

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface) -> None:
        if not self.active:
            return
        panel = self._lay_out(layout, r)
        _dim(surface, surface.get_rect(), int(170 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=14, border=r.theme.brass_light,
                        glow=r.theme.brass, glow_strength=0.3, shadow=22)
        r.spaced_text("MENU", r.fonts.get(26, bold=True), r.theme.brass_bright,
                      surface, center=(panel.centerx, panel.top + 34), spacing=4,
                      shadow=True)

        for index, ((key, label), rect) in enumerate(zip(self.entries, self.rects)):
            hovered = index == self.hover
            accent = (r.theme.warning_bg if key in ("leave", "quit")
                      else r.theme.btn_active_bg)
            style = r.emphasis(fill=accent, border=lighten(accent, 0.45),
                               text=r.theme.btn_active_text,
                               hover=1.0 if hovered else 0.0,
                               accent=r.theme.brass_light)
            drawn = r.interactive_panel(rect, style, surface, radius=10)
            r.fit_spaced_text(label.upper(), drawn, style.text, surface,
                              base_size=17, spacing=2, padding=16)


# ── somebody else's cards, to take one from ──────────────────────────────────
class CardPicker:
    """A row of cards the player must pick exactly one of (Spy).

    Deliberately told nothing about why it is open.  It is handed a title, a
    caption and a list of cards, and it answers with a uid — so the next card
    that needs "choose one of these" reuses it by passing a different list,
    which is the same bargain :class:`ChoicePrompt` makes for pawns and fields.

    It is only ever opened on the machine of the player who asked.  The engine
    sends ``ChoiceRequired`` to the asker alone (N40), so the cards it lays out
    never reach anybody who is not entitled to see them.
    """

    def __init__(self) -> None:
        self.active = False
        self.cards: List[Card] = []
        self.title = ""
        self.caption = ""
        self.appear = 0.0
        self.hover: Dict[int, float] = {}

    def show(self, cards: Sequence[Card], title: str, caption: str = "") -> None:
        self.active = True
        self.cards = list(cards)
        self.title = title
        self.caption = caption
        self.appear = 0.0
        self.hover = {}

    def hide(self) -> None:
        self.active = False
        self.cards = []
        self.hover = {}

    def card_at(self, layout: Layout, position: Tuple[int, int]) -> Optional[int]:
        count = len(self.cards)
        for index, card in enumerate(self.cards):
            if layout.card_picker_card_rect(index, count).collidepoint(position):
                return card.uid
        return None

    def update(self, dt: float, layout: Layout, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        self.appear = approach(self.appear, 1.0, 12.0, dt)
        count = len(self.cards)
        for index, card in enumerate(self.cards):
            rect = layout.card_picker_card_rect(index, count)
            wanted = 1.0 if rect.collidepoint(mouse) else 0.0
            self.hover[card.uid] = approach(self.hover.get(card.uid, 0.0),
                                            wanted, 16.0, dt)

    def draw(self, r: Renderer, cards: CardRenderer, layout: Layout,
             surface: pygame.Surface, mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        count = len(self.cards)
        if count == 0:
            return
        panel = layout.card_picker_panel(count)
        _dim(surface, surface.get_rect(), int(160 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.4, shadow=22)

        r.spaced_text(self.title.upper(), r.fonts.get(22, bold=True),
                      theme.brass_bright, surface,
                      center=(panel.centerx, panel.top + 24), spacing=3,
                      shadow=True)
        if self.caption:
            r.text(self.caption, r.fonts.deck(), theme.text_dim, surface,
                   midtop=(panel.centerx, panel.top + 38))

        for index, card in enumerate(self.cards):
            rect = layout.card_picker_card_rect(index, count)
            hover = self.hover.get(card.uid, 0.0)
            draw_rect = rect.move(0, -int(12 * hover))
            border = theme.deck_colors.get(card.deck_id)
            if hover > 0.02:
                r.shape_glow(draw_rect, theme.prompt, surface, radius=10,
                             strength=0.55 * hover)
            cards.draw_in(card, draw_rect, surface, highlighted=hover > 0.5,
                          border_color=border)

        r.text("kliknij kartę, którą zabierasz  ·  Esc anuluje",
               r.fonts.label(), theme.text_dim, surface,
               midbottom=(panel.centerx, panel.bottom - 14))


# ── Paczka: the Chest is public ──────────────────────────────────────────────
@dataclass
class ChestHoldingView:
    """One player's Chest cards, as the window lists them."""

    name: str
    titles: List[str] = field(default_factory=list)


class ChestReveal:
    """Who is holding which Chest cards (Paczka).

    Informational and nothing else: it changes no state, blocks no command and
    is dismissed by each player on their own machine, so one person reading it
    slowly does not hold the table up.  Every machine builds the same list from
    its own replica, which is why there is nothing to synchronise.

    A LIST rather than a row of card faces, because six players holding two
    cards each is twelve faces and no lineup fits them at 1280×760.  The
    furniture is the card picker\'s — the dimmed table, the panel, the brass
    heading, one button — so it reads as the same family of window as the chest
    limit and the mod selection rather than as a fourth kind of dialog.
    """

    def __init__(self) -> None:
        self.active = False
        self.holdings: List[ChestHoldingView] = []
        self.appear = 0.0

    def show(self, holdings: Sequence[ChestHoldingView]) -> None:
        self.active = True
        self.holdings = list(holdings)
        self.appear = 0.0

    def hide(self) -> None:
        self.active = False
        self.holdings = []

    @property
    def lines(self) -> int:
        """Rows of text: a name plus one per card, or the empty-table message."""
        if not self.holdings:
            return 1
        return sum(1 + max(1, len(h.titles)) for h in self.holdings)

    def ok_hit(self, layout: Layout, position: Tuple[int, int]) -> bool:
        return layout.chest_reveal_ok_rect(self.lines).collidepoint(position)

    def update(self, dt: float) -> None:
        if self.active:
            self.appear = approach(self.appear, 1.0, 12.0, dt)

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             mouse: Tuple[int, int]) -> None:
        if not self.active:
            return
        theme = r.theme
        lines = self.lines
        panel = layout.chest_reveal_panel(lines)
        _dim(surface, surface.get_rect(), int(150 * min(1.0, self.appear)))
        r.premium_panel(panel, surface, radius=16, border=theme.brass_light,
                        glow=theme.brass, glow_strength=0.4, shadow=22)

        r.spaced_text("PACZKA", r.fonts.get(22, bold=True), theme.brass_bright,
                      surface, center=(panel.centerx, panel.top + int(26 * layout.ui_scale)),
                      spacing=3, shadow=True)
        r.text("Wszystkie karty ze Skrzyni są odkryte",
               r.fonts.deck(), theme.text_dim, surface,
               midtop=(panel.centerx, panel.top + int(42 * layout.ui_scale)))

        step = layout.chest_reveal_line
        y = panel.top + layout.chest_reveal_header
        left = panel.left + int(28 * layout.ui_scale)
        if not self.holdings:
            r.text("Nikt nie posiada obecnie kart Skrzyni.",
                   r.fonts.deck(), theme.text_light, surface, topleft=(left, y))
        else:
            for holding in self.holdings:
                r.text(holding.name, r.fonts.get(16, bold=True), theme.brass_bright,
                       surface, topleft=(left, y))
                y += step
                for title in holding.titles:
                    r.text(f"– {title}", r.fonts.deck(), theme.text_light, surface,
                           topleft=(left + int(16 * layout.ui_scale), y))
                    y += step

        ok = layout.chest_reveal_ok_rect(lines)
        hovered = ok.collidepoint(mouse)
        style = r.emphasis(fill=theme.btn_primary_bg,
                           border=theme.btn_primary_border,
                           text=theme.btn_primary_text,
                           hover=1.0 if hovered else 0.0, enabled=True,
                           accent=theme.brass_light)
        drawn = r.interactive_panel(ok, style, surface, radius=10)
        r.fit_spaced_text("OK", drawn, style.text, surface, base_size=17,
                          spacing=2, padding=14)
