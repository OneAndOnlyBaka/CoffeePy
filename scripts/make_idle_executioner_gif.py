"""Draws the kiosk ranking's pillory view for when nobody owes anything into
static/kiosk/img/ranking/pillory_empty.gif.

    /tmp/gif/bin/python scripts/make_idle_executioner_gif.py      # venv with pillow, see make_wait_gifs.py

The executioner has nothing to do: hooded, axe in hand on an empty scaffold, the stocks
beside it standing open. He looks left and right, scratches his head, shrugs, taps the
block with the axe out of boredom, and a tumbleweed rolls past. Same stage, wall and floor
as the podium (make_podium_gif.py) and the walk of shame (make_pillory_gif.py) - but no
crowd and no rope: there is nothing to watch.
"""
import math

from PIL import Image, ImageDraw

from make_podium_gif import (COFFEE, COFFEE_DARK, COFFEE_LIGHT, CREAM, FLOOR, H, MARGIN, OUT_H, OUT_W, SKIN, SS,
                             W, backdrop, limb, phase, s, save)

LOOP, FRAME_MS = 14.0, 100
HOOD = (44, 32, 28)
METAL, METAL_DARK = (178, 178, 184), (120, 120, 128)
WOOD, WOOD_DARK = (150, 98, 52), (104, 64, 32)
TUMBLEWEED = (176, 140, 84)

DECK = FLOOR - 64  # the scaffold's floor boards
SCAFFOLD = (180, 420)  # left and right edge of the deck
STAIRS = 4
EXEC_X = 270
BLOCK_X = 352
STOCKS_X = 520

# What he does when (seconds into the loop); anything not listed is standing about.
LOOKS = ((0.4, 2.4), (10.0, 11.6))  # head turns left and right
SCRATCHES = ((2.8, 4.8),)
SHRUGS = ((5.2, 6.6), (12.0, 13.4))
TAPS = ((7.2, 9.6),)  # the axe on the block, bored
TUMBLE = (7.6, 13.6)  # the tumbleweed's way across


def scaffold(d):
    left, right = SCAFFOLD
    for x in range(left + 10, right, 46):  # the legs, braced
        d.rectangle(s(x - 4, DECK, x + 4, FLOOR), fill=WOOD_DARK)
    for x in range(left + 10, right - 46, 46):
        d.line(s(x, DECK + 8, x + 46, FLOOR - 4), fill=WOOD_DARK, width=3 * SS)
    d.rectangle(s(left, DECK - 8, right, DECK + 4), fill=WOOD)
    for x in range(left + 24, right, 24):
        d.line(s(x, DECK - 8, x, DECK + 4), fill=WOOD_DARK, width=SS)
    step = (FLOOR - DECK) / STAIRS
    for k in range(STAIRS):  # stairs up the left side
        y = DECK + step * k
        d.rectangle(s(left - 18 * (k + 1), y, left, y + 5), fill=WOOD)
    d.line(s(left - 18 * STAIRS, FLOOR, left, DECK - 4), fill=WOOD_DARK, width=3 * SS)


def block(d):
    top = DECK - 30
    d.rectangle(s(BLOCK_X - 22, top, BLOCK_X + 22, DECK - 8), fill=WOOD)
    d.ellipse(s(BLOCK_X - 22, top - 6, BLOCK_X + 22, top + 6), fill=COFFEE_LIGHT)
    d.ellipse(s(BLOCK_X - 12, top - 3, BLOCK_X + 12, top + 3), outline=WOOD, width=SS)  # the rings
    d.chord(s(BLOCK_X - 7, top - 6, BLOCK_X + 7, top + 6), start=0, end=180, fill=WOOD_DARK)  # the notch


def stocks(d):
    """The pillory itself, standing open - the top board swung up."""
    d.rectangle(s(STOCKS_X - 4, FLOOR - 110, STOCKS_X + 4, FLOOR), fill=WOOD_DARK)
    board = FLOOR - 92
    d.rectangle(s(STOCKS_X - 38, board, STOCKS_X + 38, board + 12), fill=WOOD)
    for hx in (-24, 0, 24):  # neck and wrists, empty
        d.chord(s(STOCKS_X + hx - 6, board - 6, STOCKS_X + hx + 6, board + 6), start=0, end=180, fill=COFFEE_DARK)
    # the upper board, hinged on the left and open
    hinge = (STOCKS_X - 38, board - 2)
    d.polygon([tuple(s(*p)) for p in (hinge, (hinge[0] + 70, board - 52), (hinge[0] + 77, board - 42),
                                      (hinge[0] + 7, board + 8))], fill=WOOD)
    d.ellipse(s(hinge[0] - 3, hinge[1] - 3, hinge[0] + 5, hinge[1] + 5), fill=METAL_DARK)


def axe(d, hand, angle):
    """The axe held at `hand`, the handle pointing `angle` (radians, 0 = straight up)."""
    up = (math.sin(angle), -math.cos(angle))
    bottom = (hand[0] - up[0] * 22, hand[1] - up[1] * 22)
    top = (hand[0] + up[0] * 34, hand[1] + up[1] * 34)
    d.line(s(*bottom, *top), fill=WOOD, width=4 * SS)
    side = (-up[1], up[0])  # the blade sticks out to this side
    c = (top[0] - up[0] * 6, top[1] - up[1] * 6)
    blade = [
        (c[0] + up[0] * 9, c[1] + up[1] * 9),
        (c[0] + side[0] * 22 + up[0] * 14, c[1] + side[1] * 22 + up[1] * 14),
        (c[0] + side[0] * 24 - up[0] * 12, c[1] + side[1] * 24 - up[1] * 12),
        (c[0] - up[0] * 8, c[1] - up[1] * 8),
    ]
    d.polygon([tuple(s(*p)) for p in blade], fill=METAL)
    d.line(s(*blade[1], *blade[2]), fill=WHITE_EDGE, width=2 * SS)
    d.ellipse(s(c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4), fill=METAL_DARK)


WHITE_EDGE = (236, 236, 240)


def executioner(d, t):
    """Black hood with eye holes, bare arms, a broad belt - and the axe in his right hand."""
    first = lambda spans: next((p for p in (phase(t, *w) for w in spans) if p is not None), None)  # noqa: E731
    look, scratch, shrug, tap = first(LOOKS), first(SCRATCHES), first(SHRUGS), first(TAPS)
    lift = 6 * math.sin(math.pi * shrug) if shrug is not None else 0.0
    turn = 6 * math.sin(look * 4 * math.pi) if look is not None else 0.0
    if shrug is not None:
        turn = 3 * math.sin(math.pi * shrug)  # head tilted, "what can you do"

    x, feet = EXEC_X, DECK - 8
    hip, shoulder, neck = feet - 32, feet - 76 - lift, feet - 80
    for side in (-1, 1):  # boots, set wide
        foot = x + 11 * side
        limb(d, (x + 7 * side, hip), (foot, feet - 4), HOOD, 12)
        d.rounded_rectangle(s(foot - 8, feet - 7, foot + 8, feet), radius=2 * SS, fill=COFFEE_DARK)
    d.rounded_rectangle(s(x - 21, shoulder, x + 21, hip + 4), radius=8 * SS, fill=HOOD)  # a broad chest
    d.rectangle(s(x - 21, hip - 6, x + 21, hip), fill=WOOD_DARK)  # the belt
    d.rectangle(s(x - 4, hip - 6, x + 4, hip), fill=METAL)

    head = (x + turn, neck - 14)
    d.ellipse(s(head[0] - 15, head[1] - 15, head[0] + 15, head[1] + 15), fill=HOOD)
    d.polygon([tuple(s(*p)) for p in ((head[0] - 12, head[1] - 8), (head[0] + 12, head[1] - 8),
                                      (head[0] + 2, head[1] - 30))], fill=HOOD)  # the pointed hood
    gaze = 2 * math.copysign(1, turn) if abs(turn) > 2 else 0
    for side in (-1, 1):
        ex, ey = head[0] + 6 * side, head[1] - 1
        d.ellipse(s(ex - 5, ey - 3, ex + 5, ey + 3), fill=SKIN)  # the eye holes
        d.ellipse(s(ex - 2 + gaze, ey - 2, ex + 2 + gaze, ey + 2), fill=COFFEE_DARK)
        if scratch is not None or shrug is not None:  # puzzled brows
            d.line(s(ex - 4, ey - 6 - (side + 1), ex + 4, ey - 6 + (side - 1)), fill=SKIN, width=2 * SS)

    left, right = (x - 19, shoulder + 6), (x + 19, shoulder + 6)
    # right arm: the axe - resting on the deck, raised and tapped on the block, or held out in a shrug
    if tap is not None:
        beat = abs(math.sin(tap * 5 * math.pi))
        hand = (right[0] + 24, right[1] + 22 - 8 * beat)
        limb(d, right, hand, SKIN, 9)
        axe(d, hand, math.radians(100 - 34 * beat))  # the blade comes down on the block
    elif shrug is not None:
        elbow = (right[0] + 3, right[1] + 22)
        hand = (right[0] + 20, right[1] + 12)
        limb(d, right, elbow, SKIN, 9)
        limb(d, elbow, hand, SKIN, 9)
        axe(d, hand, math.radians(28))
    else:
        hand = (right[0] + 8, right[1] + 30)
        limb(d, right, hand, SKIN, 9)
        axe(d, hand, math.radians(4))
    d.ellipse(s(hand[0] - 5, hand[1] - 5, hand[0] + 5, hand[1] + 5), fill=SKIN)

    # left arm: hangs, scratches the hood, or goes out palm up
    if scratch is not None:
        hand = (head[0] - 12, head[1] - 12 + 2 * math.sin(scratch * 50))
        elbow = (left[0] - 18, left[1] + 6)
        limb(d, left, elbow, SKIN, 9)
        limb(d, elbow, hand, SKIN, 9)
    elif shrug is not None:
        elbow = (left[0] - 3, left[1] + 22)
        hand = (left[0] - 20, left[1] + 12)
        limb(d, left, elbow, SKIN, 9)
        limb(d, elbow, hand, SKIN, 9)
    else:
        hand = (left[0] - 4, left[1] + 36)
        limb(d, left, hand, SKIN, 9)
    d.ellipse(s(hand[0] - 5, hand[1] - 5, hand[0] + 5, hand[1] + 5), fill=SKIN)


def tumbleweed(d, t):
    p = phase(t, *TUMBLE)
    if p is None:
        return
    x = -MARGIN - 20 + (W + 2 * MARGIN + 40) * p
    y = FLOOR - 20 - 12 * abs(math.sin(p * 9 * math.pi))
    spin = p * 14 * math.pi
    d.ellipse(s(x - 20, y - 20, x + 20, y + 20), outline=TUMBLEWEED, width=2 * SS)
    for k in range(5):
        a = spin + k * math.pi / 5
        d.line(s(x - 19 * math.cos(a), y - 19 * math.sin(a), x + 19 * math.cos(a), y + 19 * math.sin(a)),
               fill=TUMBLEWEED, width=SS)
    for k in range(3):
        a = spin * 0.7 + k * 2.1
        d.arc(s(x - 13, y - 13, x + 13, y + 13), start=math.degrees(a), end=math.degrees(a) + 100, fill=COFFEE,
              width=SS)


def frames():
    out = []
    n = round(LOOP * 1000 / FRAME_MS)
    for i in range(n):
        t = LOOP * i / n
        img = Image.new("RGB", ((W + 2 * MARGIN) * SS, H * SS), CREAM)
        d = ImageDraw.Draw(img)
        backdrop(d)
        stocks(d)
        scaffold(d)
        block(d)
        executioner(d, t)
        tumbleweed(d, t)
        out.append(img.resize((OUT_W, OUT_H), Image.LANCZOS))
    return out


if __name__ == "__main__":
    save("pillory_empty.gif", frames(), FRAME_MS)
