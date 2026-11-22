import math
import random

import pytest

from game import Match, config as C, ground_y, make_terrain, simulate, zone_at
from game.physics import launch_point
from server.bot import choose_shot


def test_terrain_is_deterministic_and_in_bounds():
    a, feet_a = make_terrain(7)
    b, feet_b = make_terrain(7)
    assert a == b and feet_a == feet_b
    assert len(a) == C.WORLD_W // C.TERRAIN_STEP + 1
    assert all(250 <= y <= 480 for y in a)
    assert make_terrain(8)[0] != a


def test_archers_stand_on_flat_ledges_on_their_own_side():
    for seed in range(50):
        terrain, feet = make_terrain(seed)
        (lx, ly), (rx, ry) = feet
        assert lx < 200 and rx > 800
        for x, y in feet:
            assert ground_y(terrain, x) == pytest.approx(y, abs=0.1)
            assert ground_y(terrain, x - 20) == pytest.approx(y, abs=0.1)
            assert ground_y(terrain, x + 20) == pytest.approx(y, abs=0.1)


def test_zones():
    foot = (100.0, 300.0)
    assert zone_at(foot, 100, 300 - C.HEAD_Y) == "head"
    assert zone_at(foot, 100, 300 - 40) == "torso"
    assert zone_at(foot, 100, 300 - 10) == "legs"
    assert zone_at(foot, 130, 300 - 40) is None
    assert zone_at(foot, 100, 300 - 100) is None


def test_arrow_with_no_wind_follows_a_parabola():
    terrain = [1000.0] * (C.WORLD_W // C.TERRAIN_STEP + 1)  # a floor far below
    origin = (500.0, 100.0)
    f = simulate(terrain, origin, 45, 0.5, 1, 0.0, [(-999, -999), (-999, -999)], 0, record=True)
    # range of a level-ground shot: v^2 sin(2a) / g, here measured down to y = 1000
    t = f.duration
    v = C.MAX_SPEED * 0.5
    expected_x = origin[0] + v * math.cos(math.radians(45)) * t
    assert f.end[0] == pytest.approx(expected_x, rel=0.02)
    assert f.hit is None


def test_wind_pushes_the_arrow():
    terrain = [1000.0] * (C.WORLD_W // C.TERRAIN_STEP + 1)
    feet = [(-999, -999), (-999, -999)]
    calm = simulate(terrain, (300, 300), 40, 0.6, 1, 0.0, feet, 0)
    tail = simulate(terrain, (300, 300), 40, 0.6, 1, 1.0, feet, 0)
    head = simulate(terrain, (300, 300), 40, 0.6, 1, -1.0, feet, 0)
    assert head.end[0] < calm.end[0] < tail.end[0]


def test_arrow_stops_on_the_ground():
    terrain, feet = make_terrain(3)
    f = simulate(terrain, launch_point(feet[0], 1), 10, 0.3, 1, 0.0, feet, 0)
    assert f.hit is None
    assert f.end[1] == pytest.approx(ground_y(terrain, f.end[0]), abs=0.5)


def test_flat_shot_at_the_head_hits():
    terrain = [1000.0] * (C.WORLD_W // C.TERRAIN_STEP + 1)
    feet = [(100.0, 400.0), (400.0, 400.0)]
    # Gravity drops a full-power arrow about 40 px over 300 px, so some small upward angle must hit.
    origin = (100.0, 400.0 - C.HEAD_Y)
    zones = {
        simulate(terrain, origin, a / 2, 1.0, 1, 0.0, feet, 0).hit for a in range(0, 30)
    }
    assert (1, "head") in zones and (1, "torso") in zones


def test_own_archer_is_not_hit_at_launch():
    terrain, feet = make_terrain(5)
    f = simulate(terrain, launch_point(feet[0], 1), 80, 0.2, 1, 0.0, feet, 0)
    assert f.hit is None or f.hit[0] != 0 or f.duration > C.SELF_HIT_GRACE


def test_match_turns_and_damage():
    m = Match(seed=1, first=0)
    with pytest.raises(ValueError):
        m.shoot(1, 40, 0.5)
    msg = m.shoot(0, 40, 0.5)
    assert msg["by"] == 0 and msg["next"]["turn"] == 1 and m.turn == 1
    assert msg["path"][0][0] > m.feet[0][0]


def test_match_kills_end_the_game():
    m = Match(seed=2, first=0)
    m.hp = [100, 25]
    m.feet[1] = (m.feet[1][0], m.feet[1][1])
    for _ in range(400):
        if m.over:
            break
        idx = m.turn
        angle, power = choose_shot(m, idx, 3, random.Random(idx))
        m.shoot(idx, angle, power)
    assert m.over and m.winner in (0, 1)
    assert m.hp[1 - m.winner] == 0


def test_timeout_passes_the_turn():
    m = Match(seed=1, first=1)
    msg = m.timeout(1)
    assert msg["next"]["turn"] == 0


def test_shot_values_are_clamped():
    m = Match(seed=1, first=0)
    msg = m.shoot(0, 500, 9)
    assert msg["angle"] == C.MAX_ANGLE and msg["power"] == 1.0
    with pytest.raises(ValueError):
        Match(seed=1, first=0).shoot(0, float("nan"), 0.5)


def test_hard_bot_lands_hits_easy_bot_does_not():
    def hits(level):
        total = 0
        for seed in range(12):
            m = Match(seed=seed, first=0)
            rng = random.Random(seed)
            angle, power = choose_shot(m, 0, level, rng)
            if m.shoot(0, angle, power)["hit"]:
                total += 1
        return total

    assert hits(3) >= 6
    assert hits(3) > hits(1)
