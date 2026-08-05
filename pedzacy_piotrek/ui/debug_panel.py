"""
Network debug panel.

Off by default, toggled with F3.  It exists to answer the questions that come
up when a client and the server disagree: which room am I in, is the connection
up, how long is the round trip, how many actions are in flight, and — the one
that matters — does my state fingerprint match the one the server stamped on
the last accepted command.

Everything it shows comes from :class:`NetworkStats` and the session, so the
panel reads and never writes.  It is safe to leave in a release build.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pygame

from ..render.renderer import Renderer
from .layout import Layout


class NetworkDebugPanel:
    """A corner readout of the networking state."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        return self.enabled

    def _lines(self, session, service, state) -> List[Tuple[str, str]]:
        stats = getattr(service or session, "stats", None)
        lines: List[Tuple[str, str]] = [("tryb", getattr(stats, "mode", "local"))]

        if service is not None:
            lines.append(("serwer", str(getattr(service, "server_url", "—"))))
            lines.append(("pokój", str(getattr(service, "room_code", "") or "—")))

        state_name = getattr(getattr(stats, "state", None), "value", "—")
        dropped = (getattr(service, "disconnected", None)
                   or getattr(session, "disconnected", None))
        lines.append(("połączenie", dropped or state_name))
        lines.append(("gracze", str(getattr(stats, "players", 0))))

        ping = getattr(stats, "ping_ms", None)
        lines.append(("ping", "—" if ping is None else f"{ping:.0f} ms"))
        lines.append(("wysłane", f"{getattr(stats, 'sent', 0)} · "
                                 f"{getattr(stats, 'last_sent', '—')}"))
        lines.append(("odebrane", f"{getattr(stats, 'received', 0)} · "
                                  f"{getattr(stats, 'last_received', '—')}"))
        lines.append(("w locie", str(getattr(stats, "pending", 0))))
        lines.append(("nr komendy", str(getattr(stats, "sequence", 0))))
        lines.append(("powroty / sync", f"{getattr(stats, 'reconnects', 0)} / "
                                        f"{getattr(stats, 'resyncs', 0)}"))

        if state is not None:
            lines.append(("tura / runda",
                          f"{state.turn_counter} / {state.round_number}"))
            lines.append(("aktywny", str(state.active_player_index)))
            lines.append(("moje miejsce", str(state.local_seat)))
            lines.append(("suma stanu", _fingerprint(state)))
        return lines

    def draw(self, r: Renderer, layout: Layout, surface: pygame.Surface,
             session=None, service=None, state=None) -> None:
        if not self.enabled:
            return
        lines = self._lines(session, service, state)
        font = r.fonts.get(14)
        label_font = r.fonts.get(14, bold=True)
        line_h = font.get_height() + 3
        width = 330
        panel = pygame.Rect(layout.win_w - width - 14, 14, width,
                            len(lines) * line_h + 34)
        r.premium_panel(panel, surface, radius=8, fill=r.theme.panel_inset,
                        border=r.theme.panel_edge, ornaments=False, shadow=10)
        r.text("SIEĆ — F3", label_font, r.theme.accent, surface,
               topleft=(panel.left + 10, panel.top + 8))

        y = panel.top + 28
        for key, value in lines:
            r.text(key, font, r.theme.text_dim, surface, topleft=(panel.left + 10, y))
            r.text(str(value), font, r.theme.text_light, surface,
                   topright=(panel.right - 10, y))
            y += line_h


def _fingerprint(state) -> str:
    """A short hash of the snapshot: two peers showing the same one are in sync.

    Cheap enough to compute every frame at this scale, and the fastest way to
    spot a desync — the numbers stop matching the moment one machine diverges.
    """
    try:
        import hashlib
        import json

        raw = json.dumps(state.snapshot(), sort_keys=True, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    except Exception:  # pragma: no cover - never break the game for a debug view
        return "—"
