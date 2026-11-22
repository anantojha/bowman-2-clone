"""The computer opponent: searches for the shot that passes closest to the target, then adds
human-sized error. Harder levels see the wind better and shake less."""

from __future__ import annotations

import random

from game import config as C
from game.match import Match
from game.physics import launch_point, simulate

LEVELS = {
    1: {"name": "Easy", "angle_sd": 5.0, "power_sd": 0.08, "wind_err": 0.8, "aim_h": 38.0},
    2: {"name": "Normal", "angle_sd": 2.2, "power_sd": 0.035, "wind_err": 0.4, "aim_h": 50.0},
    3: {"name": "Hard", "angle_sd": 0.7, "power_sd": 0.012, "wind_err": 0.1, "aim_h": C.HEAD_Y},
}
BOT_NAMES = ["Robin", "Legolas", "Artemis", "Hawkeye", "Katniss", "Merida", "Sagittarius"]


def _best(match: Match, idx: int, wind: float, target, angles, powers):
    origin = launch_point(match.feet[idx], match.facing(idx))
    best = (float("inf"), 45.0, 0.7)
    for ang in angles:
        for pw in powers:
            f = simulate(
                match.terrain, origin, ang, pw, match.facing(idx), wind, match.feet, idx,
                dt=1 / 60, record=False, track=target,
            )
            if f.closest < best[0]:
                best = (f.closest, ang, pw)
    return best


def choose_shot(match: Match, idx: int, level: int, rng: random.Random | None = None) -> tuple[float, float]:
    """Return (angle, power) for the archer `idx`. CPU heavy (a few hundred simulations)."""
    rng = rng or random.Random()
    cfg = LEVELS.get(level, LEVELS[2])
    foe = match.feet[1 - idx]
    target = (foe[0], foe[1] - cfg["aim_h"])
    wind = match.wind * (1 + rng.gauss(0, cfg["wind_err"]))

    coarse = _best(match, idx, wind, target, [a for a in range(5, 81, 3)], [p / 20 for p in range(6, 21)])
    _, ang0, pw0 = coarse
    fine = _best(
        match, idx, wind, target,
        [ang0 + k * 0.5 for k in range(-6, 7)],
        [min(1.0, max(C.MIN_POWER, pw0 + k * 0.01)) for k in range(-5, 6)],
    )
    _, ang, pw = fine
    ang += rng.gauss(0, cfg["angle_sd"])
    pw += rng.gauss(0, cfg["power_sd"])
    return (
        round(max(C.MIN_ANGLE, min(C.MAX_ANGLE, ang)), 1),
        round(max(C.MIN_POWER, min(1.0, pw)), 3),
    )
