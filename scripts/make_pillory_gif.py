"""Draws the kiosk ranking's pillory view into static/kiosk/img/ranking/pillory.gif.

    /tmp/gif/bin/python scripts/make_pillory_gif.py      # venv with pillow, see make_wait_gifs.py

The walk of shame from Game of Thrones, at the coffee machine: someone who still owes for
their coffee walks across, head down, while a septa follows ringing her bell and calling
"SHAME!" - the crowd behind the rope shakes its fists, points, and now and then a coffee bean
flies. Same stage, wall, floor and people as the podium (make_podium_gif.py). Who actually
owes is not in the picture - the template lists them under it.
"""
import math

from PIL import Image, ImageDraw

from make_podium_gif import (ACCENT, COFFEE, COFFEE_DARK, COFFEE_LIGHT, CREAM, FLOOR, GOLD, H, JANITOR_CAP,
                             MARGIN, OUT_H, OUT_W, OVERALL, SKIN, SS, W, WHITE, backdrop, face, font, limb, person,
                             s, save)

LOOP, FRAME_MS = 16.0, 100
WALK = (-MARGIN - 60, W + MARGIN + 130)  # the debtor's way across, off the picture at both ends
SEPTA_BEHIND = 70  # she follows this far behind
SHAME_EVERY = 2.0  # seconds between two "SHAME!"s - and the bell's rings
ROBE = (120, 120, 126)
SACK = (240, 232, 214)  # the debtor's shift - the lightest thing on the stage
DEBT_RED = (178, 40, 32)
ROPE, POST = (156, 44, 36), GOLD
CROWD_FEET = FLOOR - 24  # the crowd stands back, behind the rope
# The crowd behind the rope: x, suit, hair, tie (officials) - and what they do while the
# debtor passes: shake a fist, point, or stand with their arms folded and shout.
CROWD = (
    (0, COFFEE_DARK, COFFEE_LIGHT, ACCENT, "fist"),
    (96, ACCENT, (160, 90, 40), None, "point"),
    (192, OVERALL, JANITOR_CAP, None, "fist"),  # the janitor from the podium
    (288, COFFEE_LIGHT, (40, 28, 20), None, "folded"),
    (384, COFFEE_DARK, (120, 120, 120), COFFEE_LIGHT, "point"),
    (480, COFFEE, COFFEE_DARK, None, "fist"),
    (576, ACCENT, COFFEE_DARK, None, "folded"),
)


def debtor_x(t):
    return WALK[0] + (WALK[1] - WALK[0]) * t / LOOP


def rope(d):
    for x in range(-MARGIN + 20, W + MARGIN, 120):
        d.rectangle(s(x - 2, FLOOR - 46, x + 2, FLOOR), fill=POST)
        d.ellipse(s(x - 5, FLOOR - 51, x + 5, FLOOR - 41), fill=POST)
        d.rectangle(s(x - 7, FLOOR - 3, x + 7, FLOOR), fill=POST)
    for x in range(-MARGIN + 20, W + MARGIN - 120, 120):  # the rope sags between two posts
        points = [(x + 120 * k / 12, FLOOR - 44 + 10 * math.sin(math.pi * k / 12)) for k in range(13)]
        d.line([tuple(s(*p)) for p in points], fill=ROPE, width=4 * SS, joint="curve")


def crowd(d, t, i):
    near = debtor_x(t)
    for k, (cx, suit, hair, tie, gesture) in enumerate(CROWD):
        close = abs(near - cx) < 110
        left, right, head = person(d, cx, CROWD_FEET, suit, hair, mood="shout" if close else "official", tie=tie)
        toward = 1 if near > cx else -1
        if close and gesture == "fist":
            near_shoulder = right if toward > 0 else left
            hand = (near_shoulder[0] + 8 * toward, near_shoulder[1] - 30 - 6 * math.sin(i * 1.5 + k))
            limb(d, near_shoulder, hand, suit, 8)
            d.ellipse(s(hand[0] - 5, hand[1] - 5, hand[0] + 5, hand[1] + 5), fill=SKIN)
            other = left if toward > 0 else right
            limb(d, other, (other[0] - 3 * toward, other[1] + 34), suit, 8)
        elif close and gesture == "point":
            near_shoulder = right if toward > 0 else left
            hand = (near_shoulder[0] + 32 * toward, near_shoulder[1] + 4)
            limb(d, near_shoulder, hand, suit, 8)
            d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
            d.line(s(hand[0], hand[1], hand[0] + 7 * toward, hand[1]), fill=SKIN, width=3 * SS)  # the finger
            other = left if toward > 0 else right
            limb(d, other, (other[0] - 3 * toward, other[1] + 34), suit, 8)
        elif gesture == "folded":
            for shoulder, side in ((left, -1), (right, 1)):
                elbow = (shoulder[0] + 2 * side, shoulder[1] + 18)
                limb(d, shoulder, elbow, suit, 8)
                limb(d, elbow, (cx - 8 * side, shoulder[1] + 14), suit, 8)
        else:
            limb(d, left, (left[0] - 3, left[1] + 34), suit, 8)
            limb(d, right, (right[0] + 3, right[1] + 34), suit, 8)


def debtor(d, x, i):
    """Barefoot in a plain shift, head down, shuffling - hands clasped in front, and a sign
    round the neck saying what this is about."""
    stride = 5 * math.sin(i * 0.9)
    left, right, head = person(d, x, FLOOR, SACK, (120, 90, 60), mood="sad", stride=stride, shake=3,
                               hop=0.5 * abs(math.sin(i * 0.9)))
    for shoulder in (left, right):
        limb(d, shoulder, (x + 2, shoulder[1] + 26), SACK, 8)
    d.ellipse(s(x - 3, left[1] + 22, x + 7, left[1] + 30), fill=SKIN)
    sign_top = left[1] - 1
    for side in (-1, 1):  # the string round the neck
        d.line(s(x + 3 + 4 * side, head[1] + 12, x + 3 + 11 * side, sign_top), fill=COFFEE_DARK, width=SS)
    d.rectangle(s(x - 11, sign_top, x + 17, sign_top + 18), fill=WHITE, outline=COFFEE_DARK, width=SS)
    d.text(tuple(s(x + 3, sign_top + 9)), "-€", font=font(13), fill=DEBT_RED, anchor="mm")


def septa(d, x, t, i):
    """The septa: grey robe to the floor, white wimple, a bell in her raised hand that
    swings when she rings it - and "SHAME!" every SHAME_EVERY seconds."""
    phase = (t % SHAME_EVERY) / SHAME_EVERY
    shouting = phase < 0.55
    top = FLOOR - 74
    step = 2 * math.sin(i * 0.9)
    d.polygon([tuple(s(*p)) for p in ((x - 15, top), (x + 15, top), (x + 22 + step, FLOOR),
                                      (x - 22 - step, FLOOR))], fill=ROBE)
    d.line(s(x - 6, top + 4, x - 6, FLOOR - 4), fill=(100, 100, 106), width=2 * SS)  # a fold in the robe
    head = (x, top - 13)
    d.ellipse(s(head[0] - 18, head[1] - 18, head[0] + 18, head[1] + 20), fill=ROBE)  # the hood
    d.ellipse(s(head[0] - 15, head[1] - 15, head[0] + 15, head[1] + 17), fill=WHITE)  # the wimple
    face(d, head, WHITE, "shout" if shouting else "official")
    # bell arm, raised forward; the bell swings while she rings it
    shoulder = (x + 12, top + 6)
    swing = 0.6 * math.sin(phase * 2 * math.pi * 3) * (1 - phase) if phase < 0.5 else 0.0
    hand = (x + 30, top - 8)
    limb(d, shoulder, hand, ROBE, 8)
    d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
    bx, by = hand[0] + 14 * math.sin(swing), hand[1] + 14 * math.cos(swing)
    d.line(s(*hand, bx, by), fill=COFFEE_DARK, width=2 * SS)
    d.chord(s(bx - 8, by - 4, bx + 8, by + 14), start=180, end=360, fill=GOLD)
    d.rectangle(s(bx - 8, by + 4, bx + 8, by + 8), fill=GOLD)
    if shouting:
        bubble(d, (x - 6, top - 50), "SHAME!")
        for k in (-1, 1):  # the ring
            d.arc(s(bx - 14, by - 6, bx + 14, by + 18), start=(-30 if k > 0 else 150), end=(30 if k > 0 else 210),
                  fill=COFFEE_DARK, width=SS)
    other = (x - 12, top + 6)
    limb(d, other, (x - 16, top + 36), ROBE, 8)


def bubble(d, at, text):
    x, y = at
    big = font(15)
    w = d.textlength(text, font=big) / SS + 14
    d.rounded_rectangle(s(x - w / 2, y - 12, x + w / 2, y + 12), radius=6 * SS, fill=WHITE,
                        outline=COFFEE_DARK, width=SS)
    d.polygon([tuple(s(*p)) for p in ((x - 4, y + 11), (x + 6, y + 11), (x + 2, y + 22))], fill=WHITE)
    d.line(s(x - 4, y + 12, x + 2, y + 22, x + 6, y + 12), fill=COFFEE_DARK, width=SS)
    d.text(tuple(s(x, y)), text, font=big, fill=COFFEE, anchor="mm")


def beans(d, t):
    """Every so often one of the crowd near the debtor throws a coffee bean at them."""
    x = debtor_x(t)
    for k, offset in enumerate((0.3, 1.1, 1.9)):
        throw = ((t + offset) % 1.6) / 0.7
        if throw >= 1:
            continue
        start_t = t - throw * 0.7  # where the debtor was when it was thrown
        target = debtor_x(t)
        thrower = min(CROWD, key=lambda c: abs(c[0] - debtor_x(start_t) + (60 if k % 2 else -60)))[0]
        bx = thrower + (target - thrower) * throw
        by = CROWD_FEET - 80 + (FLOOR - 70 - (CROWD_FEET - 80)) * throw - 40 * math.sin(math.pi * throw)
        d.ellipse(s(bx - 4, by - 3, bx + 4, by + 3), fill=COFFEE)
        d.line(s(bx - 2, by, bx + 2, by), fill=COFFEE_LIGHT, width=SS)


def frames():
    out = []
    n = round(LOOP * 1000 / FRAME_MS)
    for i in range(n):
        t = LOOP * i / n
        img = Image.new("RGB", ((W + 2 * MARGIN) * SS, H * SS), CREAM)
        d = ImageDraw.Draw(img)
        backdrop(d)
        crowd(d, t, i)
        rope(d)
        x = debtor_x(t)
        septa(d, x - SEPTA_BEHIND, t, i)
        debtor(d, x, i)
        beans(d, t)
        out.append(img.resize((OUT_W, OUT_H), Image.LANCZOS))
    return out


if __name__ == "__main__":
    save("pillory.gif", frames(), FRAME_MS)
