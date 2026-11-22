"""The rules of one duel: whose turn it is, the wind, hit points, and who has won."""

from __future__ import annotations

import math
import random

from . import config as C
from .physics import launch_point, make_terrain, simulate


class Match:
    def __init__(self, seed: int | None = None, first: int | None = None):
        rng = random.Random(seed)
        self.seed = seed if seed is not None else rng.randrange(2**31)
        self.rng = random.Random(self.seed ^ 0x9E3779B9)
        self.terrain, self.feet = make_terrain(self.seed)
        self.hp = [C.MAX_HP, C.MAX_HP]
        self.turn = first if first is not None else self.rng.randrange(2)
        self.wind = self._roll_wind()
        self.winner: int | None = None
        self.over = False
        self.turn_no = 0  # counts turns, so a late or repeated shot can be told from a fresh one

    @staticmethod
    def facing(idx: int) -> int:
        return 1 if idx == 0 else -1

    def _roll_wind(self) -> float:
        return round(self.rng.uniform(-1, 1), 1)

    def start_message(self) -> dict:
        return {
            "world": [C.WORLD_W, C.WORLD_H],
            "step": C.TERRAIN_STEP,
            "terrain": self.terrain,
            "pos": [list(p) for p in self.feet],
            "hp": list(self.hp),
            "turn": self.turn,
            "wind": self.wind,
        }

    def _advance(self) -> dict:
        self.turn = 1 - self.turn
        self.turn_no += 1
        self.wind = self._roll_wind()
        return {"turn": self.turn, "wind": self.wind}

    def forfeit(self, loser: int) -> None:
        if not self.over:
            self.over = True
            self.winner = 1 - loser

    def shoot(self, idx: int, angle: float, power: float) -> dict:
        """Fire one arrow for archer `idx` and return the 'shot' message for both clients."""
        if self.over or idx != self.turn:
            raise ValueError("not your turn")
        if not (math.isfinite(angle) and math.isfinite(power)):
            raise ValueError("bad aim")
        angle = max(C.MIN_ANGLE, min(C.MAX_ANGLE, angle))
        power = max(C.MIN_POWER, min(1.0, power))
        facing = self.facing(idx)
        flight = simulate(
            self.terrain,
            launch_point(self.feet[idx], facing),
            angle,
            power,
            facing,
            self.wind,
            self.feet,
            idx,
        )
        hit = None
        if flight.hit:
            who, zone = flight.hit
            dmg = min(C.DAMAGE[zone], self.hp[who])
            self.hp[who] -= dmg
            hit = {"who": who, "zone": zone, "dmg": dmg}
            if self.hp[who] <= 0:
                self.over = True
                self.winner = 1 - who  # a lethal self-hit hands the win to the other archer
        msg = {
            "t": "shot",
            "by": idx,
            "angle": round(angle, 1),
            "power": round(power, 3),
            "wind": self.wind,
            "path": [[round(px, 1), round(py, 1)] for px, py in flight.path],
            "step": round(flight.sample_dt, 5),
            "flight": round(flight.duration, 3),
            "hit": hit,
            "hp": list(self.hp),
            "winner": self.winner,
            "next": None if self.over else self._advance(),
        }
        return msg

    def timeout(self, idx: int) -> dict:
        """The archer ran out of time: no arrow, the turn passes."""
        if self.over or idx != self.turn:
            raise ValueError("not your turn")
        return {"t": "timeout", "by": idx, "hp": list(self.hp), "next": self._advance()}
