import asyncio
import contextlib
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from .game_server import Client, GameServer, _dumps
from .limits import Limits, Timing

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"

# WebSocket close codes
CLOSE_GOING_AWAY = 1001
CLOSE_POLICY = 1008
CLOSE_TRY_AGAIN_LATER = 1013


async def _watch(ws: WebSocket, client: Client, game: GameServer) -> None:
    """Close the connection when nothing has arrived for idle_timeout seconds."""
    idle = game.limits.idle_timeout
    try:
        while True:
            await asyncio.sleep(min(idle / 3, 5.0))
            if game.clock() - client.last_seen > idle:
                await ws.close(code=CLOSE_GOING_AWAY)
                return
    except Exception:
        pass


async def _pump(ws: WebSocket, client: Client) -> None:
    """Send whatever the game has queued for this client. Ends quietly when the socket closes."""
    try:
        while True:
            await ws.send_text(await client.out.get())
            if client.overflow:  # it is not reading; do not let the queue grow
                await ws.close(code=CLOSE_POLICY)
                return
    except Exception:
        pass


def _client_ip(ws: WebSocket, limits: Limits) -> str:
    if limits.trust_proxy:
        forwarded = ws.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return ws.client.host if ws.client else "unknown"


def _origin_ok(ws: WebSocket, limits: Limits) -> bool:
    if not limits.allowed_origins:
        return True
    origin = ws.headers.get("origin", "").rstrip("/")
    if origin in limits.allowed_origins:
        return True
    # The copy of the page served by this very server is always welcome.
    return bool(origin) and urlsplit(origin).netloc == ws.headers.get("host", "")


def create_app(limits: Limits | None = None, timing: Timing | None = None) -> Starlette:
    game = GameServer(limits or Limits.from_env(), timing)
    limits = game.limits

    async def health(request):
        return JSONResponse({"status": "ok", **game.stats()})

    async def play(ws: WebSocket):
        if not _origin_ok(ws, limits):
            await ws.close(code=CLOSE_POLICY)  # refused before the handshake completes
            return
        await ws.accept()
        reason = game.refusal(_client_ip(ws, limits))
        if reason:
            await ws.send_text(_dumps({"t": "full", "reason": reason}))
            await ws.close(code=CLOSE_TRY_AGAIN_LATER)
            return

        client = game.connect(_client_ip(ws, limits))
        sender = asyncio.create_task(_pump(ws, client))
        watchdog = asyncio.create_task(_watch(ws, client, game))
        try:
            while True:
                event = await ws.receive()
                if event["type"] == "websocket.disconnect":
                    break
                text = event.get("text")
                if text is not None and not game.handle_message(client, text):
                    await ws.close(code=CLOSE_POLICY)
                    break
                # Binary frames from clients are not part of the protocol; ignore them.
        finally:
            sender.cancel()
            watchdog.cancel()
            game.disconnect(client)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        for room in list(game.rooms.values()):
            room.close()

    app = Starlette(
        routes=[
            Route("/health", health),
            WebSocketRoute("/ws", play),
            Mount("/", app=StaticFiles(directory=CLIENT_DIR, html=True), name="client"),
        ],
        lifespan=lifespan,
    )
    app.state.game = game
    return app


app = create_app()
