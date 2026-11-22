import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.app import create_app
from server.limits import Limits, Timing

FAST = Timing(turn_seconds=0.6, grace=0.1, reveal=0.05, start_delay=0.05, bot_think=(0.0, 0.01))


@pytest.fixture
def client():
    with TestClient(create_app(Limits(), FAST)) as c:
        yield c


def recv_until(ws, kind, limit=30):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg["t"] == kind:
            return msg
    raise AssertionError(f"never saw {kind}")


def send(ws, **msg):
    ws.send_text(json.dumps(msg))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_serves_the_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Bowman" in r.text


def test_bot_match_plays_a_shot(client):
    with client.websocket_connect("/ws") as ws:
        send(ws, t="bot", name="Tester", level=3)
        start = recv_until(ws, "start")
        assert start["bot"] and len(start["terrain"]) == 251 and start["names"][start["you"]] == "Tester"
        you = start["you"]
        turn = start["turn"]
        if turn != you:
            shot = recv_until(ws, "shot")  # the bot goes first
            assert shot["by"] == 1 - you
            turn = shot["next"]["turn"] if shot["next"] else turn
        assert turn == you
        send(ws, t="shoot", angle=40, power=0.7)
        shot = recv_until(ws, "shot")
        assert shot["by"] == you and shot["path"] and shot["hp"]


def test_two_players_pair_up_and_trade_shots(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        send(a, t="quick", name="Ann")
        assert recv_until(a, "queued")
        send(b, t="quick", name="Bob")
        sa, sb = recv_until(a, "start"), recv_until(b, "start")
        assert {sa["you"], sb["you"]} == {0, 1}
        assert sa["terrain"] == sb["terrain"] and sa["turn"] == sb["turn"]
        first, ws_first = (a, sa) if sa["turn"] == sa["you"] else (b, sb)
        other = b if first is a else a
        send(first, t="shoot", angle=35, power=0.8)
        s1, s2 = recv_until(first, "shot"), recv_until(other, "shot")
        assert s1 == s2 and s1["next"]["turn"] == 1 - ws_first["you"]


def test_shooting_out_of_turn_is_ignored(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        send(a, t="quick", name="A")
        send(b, t="quick", name="B")
        sa, sb = recv_until(a, "start"), recv_until(b, "start")
        wrong = b if sa["turn"] == sa["you"] else a
        send(wrong, t="shoot", angle=40, power=0.5)
        # nobody shot, so the right archer's clock runs out instead
        msg = recv_until(a, "timeout")
        assert msg["by"] == sa["turn"]


def test_private_room(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        send(a, t="create", name="Host")
        code = recv_until(a, "room")["code"]
        assert len(code) == 4
        send(b, t="join", name="Guest", code=code.lower())
        assert recv_until(a, "start")["names"] and recv_until(b, "start")
        send(b, t="join", name="Late", code=code)  # already started: the same socket re-joining is refused
        assert recv_until(b, "error")


def test_join_unknown_room(client):
    with client.websocket_connect("/ws") as ws:
        send(ws, t="join", name="X", code="ZZZZ")
        assert recv_until(ws, "error")


def test_leaving_forfeits_the_match(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        send(a, t="quick", name="A")
        send(b, t="quick", name="B")
        sa, _ = recv_until(a, "start"), recv_until(b, "start")
        send(b, t="leave")
        over = recv_until(a, "over")
        assert over["reason"] == "left" and over["winner"] == sa["you"]


def test_bot_match_finishes_and_rematch_restarts(client):
    with client.websocket_connect("/ws") as ws:
        send(ws, t="bot", name="T", level=3)
        start = recv_until(ws, "start")
        you, turn = start["you"], start["turn"]
        done = None
        if turn == you:
            send(ws, t="shoot", angle=35, power=0.75)
        for _ in range(200):
            msg = json.loads(ws.receive_text())
            if msg["t"] == "over":
                done = msg
                break
            if msg["t"] in ("shot", "timeout"):
                if msg.get("winner") is not None:
                    done = msg
                    break
                nxt = msg["next"]
                if nxt["turn"] == you:
                    send(ws, t="shoot", angle=35, power=0.75)
        assert done is not None, "a duel against the hard bot should end"
        send(ws, t="rematch")
        assert recv_until(ws, "start")["hp"] == [100, 100]


def test_idle_player_forfeits_after_three_timeouts(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        send(a, t="quick", name="A")
        send(b, t="quick", name="B")
        recv_until(a, "start"), recv_until(b, "start")
        over = recv_until(a, "over", limit=60)
        assert over["reason"] == "timeout"


def test_junk_is_dropped(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not json")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_origin_allowlist():
    app = create_app(Limits(allowed_origins=frozenset({"https://me.github.io"})), FAST)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
                pass
        with c.websocket_connect("/ws", headers={"origin": "https://me.github.io"}) as ws:
            send(ws, t="ping")
            assert recv_until(ws, "pong")


def test_connection_cap():
    app = create_app(Limits(max_connections=1), FAST)
    with TestClient(app) as c:
        with c.websocket_connect("/ws"):
            with c.websocket_connect("/ws") as second:
                assert json.loads(second.receive_text())["t"] == "full"
