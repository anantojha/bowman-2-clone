"""Abuse limits and timing, read from environment variables so Render can tune them."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from game import config as C


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Limits:
    allowed_origins: frozenset[str] = frozenset()  # empty = any origin (local development)
    trust_proxy: bool = False  # read the client IP from X-Forwarded-For (behind Render's proxy)
    max_connections: int = 200
    max_per_ip: int = 8
    max_rooms: int = 100
    max_message: int = 600  # characters in one client message
    rate_per_sec: float = 15.0
    rate_burst: float = 30.0
    idle_timeout: float = 90.0  # seconds without any message (the page pings every 20 s)

    @classmethod
    def from_env(cls) -> "Limits":
        origins = os.environ.get("BOWMAN_ALLOWED_ORIGINS", "")
        return cls(
            allowed_origins=frozenset(o.strip().rstrip("/") for o in origins.split(",") if o.strip()),
            trust_proxy=os.environ.get("BOWMAN_TRUST_PROXY", "") == "1",
            max_connections=_int("BOWMAN_MAX_CONNECTIONS", 200),
            max_per_ip=_int("BOWMAN_MAX_PER_IP", 8),
            max_rooms=_int("BOWMAN_MAX_ROOMS", 100),
        )


@dataclass
class Timing:
    """Pauses in the room loop. Tests shrink them so a whole duel runs in a moment."""

    turn_seconds: float = C.TURN_SECONDS
    grace: float = C.TURN_GRACE
    reveal: float = C.REVEAL_SECONDS
    start_delay: float = 1.5  # before the first turn of a match
    bot_think: tuple[float, float] = field(default=(1.0, 2.2))
