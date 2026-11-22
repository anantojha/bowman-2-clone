"""Terrain generation and the arrow simulation. Pure functions, no I/O, deterministic."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from . import config as C


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def make_terrain(seed: int) -> tuple[list[float], list[tuple[float, float]]]:
    """Return (heights, positions). heights[i] is the ground's y at x = i * TERRAIN_STEP.

    positions holds the feet of the two archers: left (slot 0) and right (slot 1), each on a
    flat ledge so nobody starts on a slope.
    """
    rng = random.Random(seed)
    n = C.WORLD_W // C.TERRAIN_STEP + 1
    base = rng.uniform(340, 390)
    waves = [  # (amplitude, wavelength, phase)
        (rng.uniform(45, 70), rng.uniform(350, 700), rng.uniform(0, math.tau)),
        (rng.uniform(18, 30), rng.uniform(170, 300), rng.uniform(0, math.tau)),
        (rng.uniform(6, 12), rng.uniform(80, 140), rng.uniform(0, math.tau)),
    ]
    ys = []
    for i in range(n):
        x = i * C.TERRAIN_STEP
        y = base + sum(a * math.sin(math.tau * x / wl + ph) for a, wl, ph in waves)
        ys.append(max(260.0, min(470.0, y)))

    spots = [rng.uniform(80, 150), rng.uniform(850, 920)]
    for px in spots:
        py = ys[round(px / C.TERRAIN_STEP)]
        for i in range(n):
            d = abs(i * C.TERRAIN_STEP - px)
            if d <= 32:
                ys[i] = py
            elif d <= 80:
                k = _smoothstep((d - 32) / 48)
                ys[i] = py * (1 - k) + ys[i] * k
    ys = [round(y, 1) for y in ys]
    positions = [(round(px, 1), ys[round(px / C.TERRAIN_STEP)]) for px in spots]
    return ys, positions


def ground_y(terrain: list[float], x: float) -> float:
    """Ground height at x, by linear interpolation. Outside the world it keeps the edge value."""
    f = x / C.TERRAIN_STEP
    if f <= 0:
        return terrain[0]
    if f >= len(terrain) - 1:
        return terrain[-1]
    i = int(f)
    k = f - i
    return terrain[i] * (1 - k) + terrain[i + 1] * k


def launch_point(foot: tuple[float, float], facing: int) -> tuple[float, float]:
    """Where an arrow starts: just in front of the archer's shoulder."""
    return foot[0] + facing * C.NOCK_REACH * 0.8, foot[1] - C.SHOULDER_Y - C.NOCK_REACH * 0.4


def zone_at(foot: tuple[float, float], x: float, y: float) -> str | None:
    """Which body part of an archer standing at `foot` contains the point (x, y)."""
    fx, fy = foot
    dx, h = x - fx, fy - y  # h is the height above the feet
    if dx * dx + (h - C.HEAD_Y) ** 2 <= C.HEAD_R**2:
        return "head"
    if abs(dx) <= C.TORSO_HALF_W and C.LEGS_TOP <= h <= C.TORSO_TOP:
        return "torso"
    if abs(dx) <= C.LEGS_HALF_W and 0 <= h < C.LEGS_TOP:
        return "legs"
    return None


@dataclass
class Flight:
    path: list[tuple[float, float]]  # sampled every SAMPLE_EVERY steps, plus the final point
    sample_dt: float
    duration: float
    hit: tuple[int, str] | None  # (archer index, zone)
    end: tuple[float, float]
    closest: float  # nearest the arrow came to `track` (only when a track point was given)


def simulate(
    terrain: list[float],
    origin: tuple[float, float],
    angle: float,
    power: float,
    facing: int,
    wind: float,
    feet: list[tuple[float, float]],
    shooter: int,
    dt: float = C.SIM_DT,
    record: bool = True,
    track: tuple[float, float] | None = None,
) -> Flight:
    """Fly one arrow. `angle` is degrees above the horizontal, towards the facing side."""
    a = math.radians(angle)
    speed = C.MAX_SPEED * power
    vx = facing * math.cos(a) * speed
    vy = -math.sin(a) * speed
    ax = wind * C.WIND_ACCEL
    x, y = origin
    t = 0.0
    step = 0
    path = [(x, y)] if record else []
    hit = None
    end = (x, y)
    closest = math.inf
    tx, ty = track if track else (0.0, 0.0)
    lo, hi = -C.OUT_MARGIN, C.WORLD_W + C.OUT_MARGIN

    while t < C.MAX_FLIGHT:
        vx += ax * dt
        vy += C.GRAVITY * dt
        nx = x + vx * dt
        ny = y + vy * dt
        t += dt
        step += 1

        if nx < lo or nx > hi or ny > C.WORLD_H + 200:
            end = (nx, ny)
            break
        g = ground_y(terrain, nx)
        if ny >= g:
            # land on the surface itself, so the stuck arrow sits where it touched
            end = (nx, g)
            break

        # Test the middle of the step too, so a fast arrow cannot slip through a head.
        found = None
        for px, py in (((x + nx) / 2, (y + ny) / 2), (nx, ny)):
            for i, foot in enumerate(feet):
                if i == shooter and t < C.SELF_HIT_GRACE:
                    continue
                zone = zone_at(foot, px, py)
                if zone:
                    found = (i, zone, px, py)
                    break
            if found:
                break
        if found:
            hit = (found[0], found[1])
            end = (found[2], found[3])
            x, y = end
            break

        x, y = nx, ny
        end = (x, y)
        if track:
            d = math.hypot(x - tx, y - ty)
            if d < closest:
                closest = d
        if record and step % C.SAMPLE_EVERY == 0:
            path.append((x, y))

    if record:
        path.append(end)
    if track:
        closest = min(closest, math.hypot(end[0] - tx, end[1] - ty))
    return Flight(path, dt * C.SAMPLE_EVERY, t, hit, end, closest)
