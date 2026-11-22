"""Matchmaking, private rooms and the per-room turn loop.

Every duel is a Room with its own asyncio task. That task is the only code that changes the
room's Match, so there are no locks: websocket handlers just drop shots into the room's inbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import Counter

from game import Match
from game import config as C

from .bot import BOT_NAMES, LEVELS, choose_shot
from .limits import Limits, Timing

log = logging.getLogger("bowman")

CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O
CODE_LENGTH = 4


def _dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


def clean_name(value) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isprintable()).strip()
    return text[:14] or "Archer"


class Client:
    def __init__(self, ip: str, now: float):
        self.ip = ip
        self.name = "Archer"
        self.out: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self.overflow = False  # the client stopped reading; the socket pump closes it
        self.room: Room | None = None
        self.slot = -1
        self.last_seen = now
        self.tokens = 30.0
        self.stamp = now

    def send(self, msg: dict) -> None:
        try:
            self.out.put_nowait(_dumps(msg))
        except asyncio.QueueFull:
            self.overflow = True


class Room:
    def __init__(self, server: "GameServer", code: str, bot_level: int | None = None):
        self.server = server
        self.code = code
        self.bot_level = bot_level
        self.bot_slot = -1
        self.players: list[Client | None] = [None, None]
        self.names = ["", ""]
        self.match: Match | None = None
        self.task: asyncio.Task | None = None
        self.inbox: asyncio.Queue = asyncio.Queue()  # (slot, turn_no, angle, power), or None to wake the loop
        self.rematch: set[int] = set()
        self.rematch_event = asyncio.Event()
        self.timeouts = [0, 0]
        self.rounds = 0
        self.closed = False

    # ---- helpers -------------------------------------------------------------------------------
    @property
    def timing(self) -> Timing:
        return self.server.timing

    def humans(self) -> list[tuple[int, Client]]:
        return [(i, c) for i, c in enumerate(self.players) if c is not None]

    def broadcast(self, msg: dict) -> None:
        for _, c in self.humans():
            c.send(msg)

    def seat(self, client: Client, slot: int) -> None:
        self.players[slot] = client
        client.room = self
        client.slot = slot

    def waiting_for_guest(self) -> bool:
        return self.task is None and self.bot_level is None and None in self.players

    def begin(self) -> None:
        for i, c in enumerate(self.players):
            self.names[i] = c.name if c else self._bot_name()
        self.task = asyncio.create_task(self.run())
        self.task.add_done_callback(self._finished)

    def _bot_name(self) -> str:
        return f"{random.choice(BOT_NAMES)} ({LEVELS[self.bot_level]['name']})"

    def _finished(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("room %s crashed", self.code, exc_info=task.exception())
            self.close()

    # ---- the loop ------------------------------------------------------------------------------
    async def run(self) -> None:
        while not self.closed:
            self.start_match()
            await asyncio.sleep(self.timing.start_delay)
            while not self.match.over:
                await self.play_turn()
            await self.wait_rematch()

    def start_match(self) -> None:
        self.rounds += 1
        self.timeouts = [0, 0]
        self.rematch.clear()
        self.rematch_event.clear()
        while not self.inbox.empty():
            self.inbox.get_nowait()
        self.match = Match()
        base = self.match.start_message()
        for slot, c in self.humans():
            c.send({
                "t": "start", "you": slot, "names": self.names, "bot": self.bot_level is not None,
                "turn_ms": int(self.timing.turn_seconds * 1000),
                "delay_ms": int(self.timing.start_delay * 1000),
                **base,
            })

    def _next_info(self, msg: dict, delay: float) -> None:
        if msg.get("next"):
            msg["next"]["turn_ms"] = int(self.timing.turn_seconds * 1000)
            msg["next"]["delay_ms"] = int(delay * 1000)

    async def play_turn(self) -> None:
        m, t = self.match, self.timing
        idx = m.turn
        if idx == self.bot_slot:
            await asyncio.sleep(random.uniform(*t.bot_think))
            angle, power = await asyncio.to_thread(choose_shot, m, idx, self.bot_level)
            if m.over or m.turn != idx:
                return
            await self.emit(m.shoot(idx, angle, power))
            return

        try:
            while True:
                item = await asyncio.wait_for(self.inbox.get(), t.turn_seconds + t.grace)
                if item is None:  # the other archer left; the match is already over
                    return
                slot, turn_no, angle, power = item
                if slot != idx or turn_no != m.turn_no:
                    continue  # a repeat or a shot meant for an earlier turn
                try:
                    msg = m.shoot(idx, angle, power)
                except ValueError:
                    continue
                break
        except asyncio.TimeoutError:
            self.timeouts[idx] += 1
            if self.timeouts[idx] >= C.MAX_TIMEOUTS:
                m.forfeit(idx)
                self.broadcast({"t": "over", "winner": m.winner, "reason": "timeout"})
                return
            msg = m.timeout(idx)
            self._next_info(msg, 0.6)
            self.broadcast(msg)
            await asyncio.sleep(0.6)
            return
        self.timeouts[idx] = 0
        await self.emit(msg)

    async def emit(self, msg: dict) -> None:
        self._next_info(msg, self.timing.reveal)
        self.broadcast(msg)
        # Let the clients watch the arrow fly and land before the next turn's clock starts.
        await asyncio.sleep(msg["flight"] + self.timing.reveal)

    async def wait_rematch(self) -> None:
        # Not cleared here: a quick player may have asked for a rematch while the last arrow was
        # still being replayed. start_match() resets both.
        await self.rematch_event.wait()

    # ---- messages from clients -----------------------------------------------------------------
    def on_shoot(self, client: Client, angle: float, power: float) -> None:
        m = self.match
        if m and not m.over and m.turn == client.slot:
            self.inbox.put_nowait((client.slot, m.turn_no, angle, power))

    def on_rematch(self, client: Client) -> None:
        m = self.match
        if not m or not m.over or self.closed:
            return
        humans = self.humans()
        if self.bot_level is None and len(humans) < 2:
            return
        self.rematch.add(client.slot)
        if len(self.rematch) >= len(humans):
            self.rematch_event.set()
        else:
            for slot, c in humans:
                if slot not in self.rematch:
                    c.send({"t": "rematch_offer"})

    def on_leave(self, client: Client) -> None:
        slot = client.slot
        if self.players[slot] is client:
            self.players[slot] = None
        client.room = None
        client.slot = -1
        remaining = self.humans()
        if not remaining:
            self.close()
            return
        m = self.match
        if m and not m.over:
            m.forfeit(slot)
            self.broadcast({"t": "over", "winner": m.winner, "reason": "left"})
            self.inbox.put_nowait(None)  # wake the loop if it is waiting on this archer
        else:
            self.broadcast({"t": "left"})

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.server.rooms.pop(self.code, None)
        for _, c in self.humans():
            c.room = None
            c.slot = -1
        self.players = [None, None]
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()


class GameServer:
    def __init__(self, limits: Limits | None = None, timing: Timing | None = None, clock=time.monotonic):
        self.limits = limits or Limits()
        self.timing = timing or Timing()
        self.clock = clock
        self.clients: set[Client] = set()
        self.per_ip: Counter[str] = Counter()
        self.rooms: dict[str, Room] = {}
        self.waiting: Client | None = None
        self.handlers = {
            "ping": self._ping, "quick": self._quick, "bot": self._bot, "create": self._create,
            "join": self._join, "cancel": self._leave_msg, "leave": self._leave_msg,
            "shoot": self._shoot, "rematch": self._rematch,
        }

    # ---- connections ---------------------------------------------------------------------------
    def refusal(self, ip: str) -> str | None:
        if len(self.clients) >= self.limits.max_connections:
            return "The server is full. Try again in a minute."
        if self.per_ip[ip] >= self.limits.max_per_ip:
            return "Too many connections from your network."
        return None

    def connect(self, ip: str) -> Client:
        client = Client(ip, self.clock())
        self.clients.add(client)
        self.per_ip[ip] += 1
        return client

    def disconnect(self, client: Client) -> None:
        self._leave(client)
        if client in self.clients:
            self.clients.discard(client)
            self.per_ip[client.ip] -= 1
            if self.per_ip[client.ip] <= 0:
                del self.per_ip[client.ip]

    def stats(self) -> dict:
        return {
            "connections": len(self.clients),
            "rooms": len(self.rooms),
            "queued": 1 if self.waiting else 0,
        }

    # ---- messages ------------------------------------------------------------------------------
    def handle_message(self, client: Client, text: str) -> bool:
        """Return False to drop the connection (junk, flooding)."""
        now = self.clock()
        client.last_seen = now
        if len(text) > self.limits.max_message:
            return False
        client.tokens = min(self.limits.rate_burst, client.tokens + (now - client.stamp) * self.limits.rate_per_sec)
        client.stamp = now
        if client.tokens < 1:
            return False
        client.tokens -= 1
        try:
            msg = json.loads(text)
        except ValueError:
            return False
        if not isinstance(msg, dict):
            return False
        handler = self.handlers.get(msg.get("t"))
        if handler:
            handler(client, msg)
        return True

    def _leave(self, client: Client) -> None:
        if self.waiting is client:
            self.waiting = None
        if client.room:
            client.room.on_leave(client)

    def _ping(self, client: Client, msg: dict) -> None:
        client.send({"t": "pong"})

    def _leave_msg(self, client: Client, msg: dict) -> None:
        self._leave(client)

    def _new_room(self, bot_level: int | None = None) -> Room | None:
        if len(self.rooms) >= self.limits.max_rooms:
            return None
        while True:
            code = "".join(random.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))
            if code not in self.rooms:
                break
        room = Room(self, code, bot_level)
        self.rooms[code] = room
        return room

    def _busy(self, client: Client) -> None:
        client.send({"t": "error", "msg": "The server is busy right now. Try again soon."})

    def _quick(self, client: Client, msg: dict) -> None:
        self._leave(client)
        client.name = clean_name(msg.get("name"))
        other = self.waiting
        if other is None or other is client:
            self.waiting = client
            client.send({"t": "queued"})
            return
        room = self._new_room()
        if room is None:
            return self._busy(client)
        self.waiting = None
        a, b = random.sample([other, client], 2)
        room.seat(a, 0)
        room.seat(b, 1)
        room.begin()

    def _bot(self, client: Client, msg: dict) -> None:
        self._leave(client)
        client.name = clean_name(msg.get("name"))
        try:
            level = int(msg.get("level", 2))
        except (TypeError, ValueError):
            level = 2
        room = self._new_room(bot_level=max(1, min(3, level)))
        if room is None:
            return self._busy(client)
        slot = random.randrange(2)
        room.bot_slot = 1 - slot
        room.seat(client, slot)
        room.begin()

    def _create(self, client: Client, msg: dict) -> None:
        self._leave(client)
        client.name = clean_name(msg.get("name"))
        room = self._new_room()
        if room is None:
            return self._busy(client)
        room.seat(client, random.randrange(2))
        client.send({"t": "room", "code": room.code})

    def _join(self, client: Client, msg: dict) -> None:
        self._leave(client)
        client.name = clean_name(msg.get("name"))
        code = str(msg.get("code", "")).strip().upper()[:CODE_LENGTH]
        room = self.rooms.get(code)
        if room is None or not room.waiting_for_guest():
            client.send({"t": "error", "msg": "No open room with that code."})
            return
        room.seat(client, 0 if room.players[0] is None else 1)
        room.begin()

    def _shoot(self, client: Client, msg: dict) -> None:
        try:
            angle, power = float(msg.get("angle")), float(msg.get("power"))
        except (TypeError, ValueError):
            return
        if client.room:
            client.room.on_shoot(client, angle, power)

    def _rematch(self, client: Client, msg: dict) -> None:
        if client.room:
            client.room.on_rematch(client)
