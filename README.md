# Bowman 2 (clone)

A real-time two-player archery duel in the browser, inspired by the stickman game *Bowman 2*.
Drag back like a slingshot, let go, and put an arrow in your rival's head. Play a friend, a
random opponent, or a bot.

- **Client** (`client/`): plain HTML, CSS and JavaScript on a canvas. Hosted on **GitHub Pages**.
- **Server** (`server/`, `game/`): Python (Starlette and WebSockets). Hosted on **Render**.

This follows the same layout as the [agar.io clone](https://github.com/anantojha/agar-io-clone).

## How to play

| Do this | To do that |
|---|---|
| Drag back anywhere, then release | Shoot. Direction and strength come from the drag. |
| Watch the wind (top centre) | Aim into it. It changes every turn. |

- Head **50**, body **25**, legs **15**. Both archers start with **100** HP.
- Turns alternate. You get **25 s** per turn. Miss **3 turns in a row** and you forfeit.
- Menu: **Quick match** (random opponent), **Play the bot** (Easy, Normal, Hard), **Private room**
  (share a 4-letter code with a friend), **Join**.

## Run it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn server.app:app --port 8000
```

Open <http://localhost:8000>. The server also serves `client/`, so there is nothing else to run.
To try two players, open the page in two tabs and press **Quick match** in both.

```bash
.venv/bin/python -m pytest -q      # physics, rules, bot and websocket tests
```

## How it works

The server owns all the physics, so players cannot cheat and everyone sees the same shot.

1. A match starts with a random seed. The server builds the terrain and sends the height map.
2. On your turn the client sends `{"t":"shoot","angle":..,"power":..}`.
3. The server flies the arrow in 1/120 s steps (gravity, wind, terrain, head/body/legs hit boxes),
   and sends back the whole path (30 points per second) with the result.
4. Both browsers replay the path, then the next turn starts.

| File | What it does |
|---|---|
| `game/physics.py` | Terrain generator, arrow simulation, hit zones |
| `game/match.py` | One duel: turns, wind, damage, winner |
| `game/config.py` | Every tuning number (gravity, damage, turn time) |
| `server/game_server.py` | Matchmaking, private rooms, the per-room turn loop |
| `server/bot.py` | The bot: searches for the closest shot, then adds human-sized error |
| `server/app.py`, `limits.py` | HTTP and WebSocket endpoints, origin check, rate limits |
| `client/game.js` | Rendering, input, sound, message handling |

Messages (JSON): client sends `quick`, `bot`, `create`, `join`, `cancel`/`leave`, `shoot`,
`rematch`, `ping`. Server sends `queued`, `room`, `start`, `shot`, `timeout`, `over`, `left`,
`rematch_offer`, `error`, `full`, `pong`.

## Deploy

### 1. Server on Render

1. Push this repo to GitHub.
2. In Render, choose **New > Blueprint** and pick the repo. It reads `render.yaml` and builds the
   `Dockerfile`.
3. In `render.yaml` (or the Render dashboard) set `BOWMAN_ALLOWED_ORIGINS` to your Pages origin,
   for example `https://YOUR-GITHUB-USER.github.io`. No path, no trailing slash.
4. Note the service URL, for example `https://bowman-2-server.onrender.com`.

The free plan sleeps after about 15 minutes idle. The page pokes the server awake when it loads and
shows "Waking the server…" for up to a minute on the first visit.

### 2. Client on GitHub Pages

1. In the repo: **Settings > Pages > Source: GitHub Actions**.
2. **Settings > Secrets and variables > Actions > Variables**: add `BOWMAN_SERVER_URL` =
   `wss://bowman-2-server.onrender.com/ws` (your Render URL, `wss://`, ending in `/ws`).
3. Push to `main`. `.github/workflows/pages.yml` writes that address into `client/config.js` and
   publishes `client/`.

### Server settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `BOWMAN_ALLOWED_ORIGINS` | empty (any) | Comma-separated origins allowed to open a WebSocket |
| `BOWMAN_TRUST_PROXY` | off | `1` to read the client IP from `X-Forwarded-For` (set on Render) |
| `BOWMAN_MAX_CONNECTIONS` | 200 | Total open sockets |
| `BOWMAN_MAX_PER_IP` | 8 | Sockets per IP |
| `BOWMAN_MAX_ROOMS` | 100 | Concurrent duels |

The server keeps all rooms in memory, so run **one worker** and expect a restart to end live matches.

## Ideas to add

Shop and bow upgrades, special arrows (fire, triple shot), a ranked ladder, spectators, more maps.
