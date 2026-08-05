"""
Particles.

Small, deliberately cheap, and entirely cosmetic: particles live in *world*
space and are drawn through the camera, so they zoom and pan with the board
instead of floating over it.

Nothing here touches game state, which means particles can be switched off
wholesale (``settings.PARTICLES_ENABLED``) on a slow machine with no effect on
the rules.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pygame

from ..config import settings

Color = Tuple[int, int, int]
Point = Tuple[float, float]


@dataclass
class Particle:
    position: Point
    velocity: Point
    life: float
    max_life: float
    radius: float
    color: Color
    gravity: float = 0.0
    fade: bool = True
    shrink: bool = True
    spin: float = 0.0
    shape: str = "circle"

    @property
    def alive(self) -> bool:
        return self.life > 0.0

    @property
    def progress(self) -> float:
        return 1.0 - max(0.0, self.life / self.max_life)


class ParticleSystem:
    """A flat pool of particles with a few named emitters."""

    def __init__(self, rng: Optional[random.Random] = None, limit: int = 600) -> None:
        self.particles: List[Particle] = []
        self.rng = rng or random.Random()
        self.limit = limit
        self.enabled = settings.PARTICLES_ENABLED

    # ── emitters ─────────────────────────────────────────────────────────────
    def dust(self, position: Point, color: Color = (196, 178, 140), count: int = 14) -> None:
        """A pawn lands: a low ring of dust kicked outward."""
        if not self.enabled:
            return
        for _ in range(count):
            angle = self.rng.uniform(0, math.tau)
            speed = self.rng.uniform(28, 92)
            life = self.rng.uniform(0.35, 0.8)
            self.spawn(
                Particle(
                    position=position,
                    velocity=(math.cos(angle) * speed, math.sin(angle) * speed * 0.45 - 18),
                    life=life,
                    max_life=life,
                    radius=self.rng.uniform(2.0, 5.0),
                    color=color,
                    gravity=120.0,
                )
            )

    def sparkle(self, position: Point, color: Color = (255, 236, 150), count: int = 18) -> None:
        """A check, a reveal, a card landing in a slot."""
        if not self.enabled:
            return
        for _ in range(count):
            angle = self.rng.uniform(0, math.tau)
            speed = self.rng.uniform(40, 150)
            life = self.rng.uniform(0.4, 1.0)
            self.spawn(
                Particle(
                    position=position,
                    velocity=(math.cos(angle) * speed, math.sin(angle) * speed - 40),
                    life=life,
                    max_life=life,
                    radius=self.rng.uniform(1.5, 3.2),
                    color=color,
                    gravity=60.0,
                    shape="star",
                )
            )

    def trail(self, position: Point, color: Color, count: int = 2) -> None:
        """Emitted continuously while a token glides — a faint wake."""
        if not self.enabled:
            return
        for _ in range(count):
            life = self.rng.uniform(0.2, 0.45)
            self.spawn(
                Particle(
                    position=(
                        position[0] + self.rng.uniform(-4, 4),
                        position[1] + self.rng.uniform(-2, 6),
                    ),
                    velocity=(self.rng.uniform(-12, 12), self.rng.uniform(-16, -4)),
                    life=life,
                    max_life=life,
                    radius=self.rng.uniform(1.5, 3.5),
                    color=color,
                )
            )

    def leaves(self, position: Point, color: Color = (92, 150, 84), count: int = 6) -> None:
        """Ambient drift — used sparsely so the board is never busy."""
        if not self.enabled:
            return
        for _ in range(count):
            life = self.rng.uniform(1.4, 3.0)
            self.spawn(
                Particle(
                    position=position,
                    velocity=(self.rng.uniform(-18, 18), self.rng.uniform(10, 34)),
                    life=life,
                    max_life=life,
                    radius=self.rng.uniform(2.0, 4.0),
                    color=color,
                    gravity=6.0,
                    spin=self.rng.uniform(-3, 3),
                    shape="leaf",
                )
            )

    def spawn(self, particle: Particle) -> None:
        if len(self.particles) >= self.limit:
            self.particles.pop(0)
        self.particles.append(particle)

    # ── simulation ───────────────────────────────────────────────────────────
    def update(self, dt: float) -> None:
        if not self.particles:
            return
        alive: List[Particle] = []
        for p in self.particles:
            p.life -= dt
            if not p.alive:
                continue
            vx, vy = p.velocity
            vy += p.gravity * dt
            vx *= 1.0 - min(0.9, 1.4 * dt)
            p.velocity = (vx, vy)
            p.position = (p.position[0] + vx * dt, p.position[1] + vy * dt)
            alive.append(p)
        self.particles = alive

    def clear(self) -> None:
        self.particles.clear()

    # ── drawing ──────────────────────────────────────────────────────────────
    def draw(self, surface: pygame.Surface, camera) -> None:
        if not self.particles:
            return
        viewport = camera.viewport
        for p in self.particles:
            sx, sy = camera.world_to_screen(p.position)
            if not viewport.collidepoint(sx, sy):
                continue
            t = p.progress
            alpha = int(255 * (1.0 - t) ** 1.4) if p.fade else 255
            if alpha <= 3:
                continue
            radius = p.radius * camera.zoom * ((1.0 - t * 0.6) if p.shrink else 1.0)
            if radius < 0.6:
                continue
            size = int(radius * 2) + 2
            layer = pygame.Surface((size, size), pygame.SRCALPHA)
            if p.shape == "star":
                self._star(layer, size / 2, radius, (*p.color, alpha))
            elif p.shape == "leaf":
                pygame.draw.ellipse(
                    layer, (*p.color, alpha),
                    pygame.Rect(0, size * 0.25, size, size * 0.5),
                )
            else:
                pygame.draw.circle(layer, (*p.color, alpha), (size // 2, size // 2), int(radius))
            surface.blit(layer, (sx - size // 2, sy - size // 2))

    @staticmethod
    def _star(surface: pygame.Surface, centre: float, radius: float, color) -> None:
        points = []
        for i in range(8):
            angle = math.pi * i / 4
            r = radius if i % 2 == 0 else radius * 0.42
            points.append((centre + math.cos(angle) * r, centre + math.sin(angle) * r))
        pygame.draw.polygon(surface, color, points)
