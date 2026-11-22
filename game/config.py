"""Tuning constants shared by the physics, the match rules and the bot.

The browser draws the stickmen with the same numbers (see HEAD_R and friends in client/game.js),
so change both sides together.
"""

# World, in "pixels". x grows to the right, y grows downward (like a canvas).
WORLD_W = 1000
WORLD_H = 520
TERRAIN_STEP = 4  # one height sample every 4 px
OUT_MARGIN = 150  # arrows that fly this far outside the world are gone

# Arrow physics
GRAVITY = 700.0  # px/s^2
MAX_SPEED = 900.0  # px/s at power 1.0
WIND_ACCEL = 70.0  # px/s^2 per wind unit; wind is in [-1, 1]
SIM_DT = 1 / 120
SAMPLE_EVERY = 4  # send one path point every 4 steps (30 Hz)
MAX_FLIGHT = 8.0  # seconds

# What the player may ask for
MIN_ANGLE = -20.0
MAX_ANGLE = 85.0
MIN_POWER = 0.15

# Stickman geometry, measured from the feet (px, py) upward
HEAD_R = 12.0
HEAD_Y = 62.0  # head centre height above the feet
TORSO_HALF_W = 10.0
TORSO_TOP = 52.0
LEGS_TOP = 24.0  # legs run from 0 to here, the torso from here to TORSO_TOP
LEGS_HALF_W = 9.0
SHOULDER_Y = 46.0  # where the arrow leaves from
NOCK_REACH = 20.0  # distance from the shoulder along the aim
SELF_HIT_GRACE = 0.35  # seconds before an arrow can hit its own archer

# Rules
MAX_HP = 100
DAMAGE = {"head": 50, "torso": 25, "legs": 15}
TURN_SECONDS = 25.0
TURN_GRACE = 3.0  # extra time the server waits for network lag
REVEAL_SECONDS = 1.2  # pause after an arrow lands before the next turn starts
MAX_TIMEOUTS = 3  # a human who lets this many turns in a row run out forfeits
