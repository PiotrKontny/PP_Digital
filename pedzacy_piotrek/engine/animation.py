"""
Animation.

Pure maths, no pygame: a tween is a value that changes over time, and the
:class:`Animator` advances every live tween once per frame.  The renderer asks
the animator where a pawn *appears* to be, while the game state holds where it
actually *is* — which is exactly the separation that lets a networked move
("pawn red is now on field 12") play out as a two-second glide on every
client without the rules waiting for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

Number = float
Point = Tuple[float, float]


# ── easing ───────────────────────────────────────────────────────────────────
def linear(t: float) -> float:
    return t


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1.0 - ((-2 * t + 2) ** 3) / 2


def ease_out_back(t: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1.0 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_out_elastic(t: float) -> float:
    if t in (0.0, 1.0):
        return t
    c4 = (2 * math.pi) / 3
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * c4) + 1


def ease_out_bounce(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


EASINGS: Dict[str, Callable[[float], float]] = {
    "linear": linear,
    "cubic": ease_out_cubic,
    "in_out": ease_in_out_cubic,
    "back": ease_out_back,
    "elastic": ease_out_elastic,
    "bounce": ease_out_bounce,
}


@dataclass
class Tween:
    """Interpolates a scalar or a point from ``start`` to ``end``."""

    start: Sequence[float] | float
    end: Sequence[float] | float
    duration: float
    easing: Callable[[float], float] = ease_out_cubic
    delay: float = 0.0
    on_update: Optional[Callable[[object], None]] = None
    on_complete: Optional[Callable[[], None]] = None
    elapsed: float = 0.0
    done: bool = False

    def _lerp(self, t: float):
        if isinstance(self.start, (int, float)):
            return self.start + (float(self.end) - float(self.start)) * t  # type: ignore[arg-type]
        return tuple(
            a + (b - a) * t for a, b in zip(self.start, self.end)  # type: ignore[arg-type]
        )

    @property
    def value(self):
        if self.duration <= 0:
            return self._lerp(1.0)
        raw = max(0.0, min(1.0, (self.elapsed - self.delay) / self.duration))
        return self._lerp(self.easing(raw))

    def update(self, dt: float) -> None:
        if self.done:
            return
        self.elapsed += dt
        if self.on_update is not None:
            self.on_update(self.value)
        if self.elapsed >= self.delay + self.duration:
            self.done = True
            if self.on_update is not None:
                self.on_update(self._lerp(1.0))
            if self.on_complete is not None:
                self.on_complete()


class Animator:
    """Keeps named tweens alive and advances them.

    Names matter: starting a new tween for ``token:red`` replaces the old one,
    so a pawn that is moved twice in quick succession does not fight itself.
    """

    def __init__(self) -> None:
        self._tweens: Dict[str, Tween] = {}
        self._anonymous: List[Tween] = []

    def add(self, key: str, tween: Tween) -> Tween:
        self._tweens[key] = tween
        return tween

    def add_anonymous(self, tween: Tween) -> Tween:
        self._anonymous.append(tween)
        return tween

    def get(self, key: str) -> Optional[Tween]:
        return self._tweens.get(key)

    def value(self, key: str, default=None):
        tween = self._tweens.get(key)
        return tween.value if tween is not None else default

    def is_running(self, key: str) -> bool:
        tween = self._tweens.get(key)
        return tween is not None and not tween.done

    @property
    def busy(self) -> bool:
        return any(not t.done for t in self._tweens.values()) or bool(self._anonymous)

    def cancel(self, key: str) -> None:
        self._tweens.pop(key, None)

    def clear(self) -> None:
        self._tweens.clear()
        self._anonymous.clear()

    def update(self, dt: float) -> None:
        for key in list(self._tweens):
            tween = self._tweens[key]
            tween.update(dt)
            if tween.done:
                del self._tweens[key]
        for tween in list(self._anonymous):
            tween.update(dt)
            if tween.done:
                self._anonymous.remove(tween)


def approach(current: float, target: float, smoothing: float, dt: float) -> float:
    """Frame-rate independent exponential smoothing.

    Used for the camera, where a tween would be wrong (the target keeps moving)
    but a raw lerp would run faster on a 144 Hz screen than on a 60 Hz one.
    """
    if smoothing <= 0:
        return target
    factor = 1.0 - math.exp(-smoothing * dt)
    return current + (target - current) * factor


def approach_point(current: Point, target: Point, smoothing: float, dt: float) -> Point:
    return (
        approach(current[0], target[0], smoothing, dt),
        approach(current[1], target[1], smoothing, dt),
    )
