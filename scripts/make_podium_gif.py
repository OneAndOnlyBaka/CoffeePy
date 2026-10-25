"""Draws the kiosk ranking's award ceremony into static/kiosk/img/ranking/podium.gif.

    /tmp/gif/bin/python scripts/make_podium_gif.py      # venv with pillow, see make_wait_gifs.py

The scene is the victory podium of Accolade's Grand Prix Circuit (1987), retold in the
kiosk's colours: the winner lifts the trophy, and since there is no champagne at a coffee
machine, second and third hold out their cups and demonstratively pour them on the floor.
Two officials in suits stand by, scratching their heads, shrugging, shaking them, and a
janitor comes through and mops up - in front of a checkered sponsor wall of coffee beans.
Names and cup counts are not in the picture - the template puts them under it, so one GIF
serves every ranking and both languages.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

from make_wait_gifs import ACCENT, COFFEE, COFFEE_DARK, COFFEE_LIGHT, CREAM, CREAM_DARK, ease

# The scene is laid out on a 600x300 stage; the picture is wider than that by MARGIN of
# sponsor wall on either side, so it fills the kiosk's panel without stretching anyone.
# OUT_W x OUT_H is what the kiosk shows (kiosk.css .podium-gif).
W, H, SS = 600, 300, 3
MARGIN = 45
OUT_W, OUT_H = 790, 343
WHITE = (255, 251, 245)
GOLD = (222, 164, 52)
SKIN = (236, 196, 160)
SHIRT = WHITE
OVERALL = (47, 122, 61)  # the kiosk's --success green: the one who cleans up
JANITOR_CAP = (201, 138, 62)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "kiosk", "img", "ranking")

# Podium blocks: (place, left, top, right); all stand on the floor at FLOOR.
FLOOR = 282
BLOCKS = ((2, 128, 206, 246), (1, 241, 176, 359), (3, 354, 222, 472))


def s(*values):
    """Stage coordinates, as (x, y) pairs, to supersampled pixels of the wider picture."""
    return [round((v + (MARGIN if k % 2 == 0 else 0)) * SS) for k, v in enumerate(values)]


def font(size):
    for path in ("/usr/share/fonts/google-noto/NotoSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size * SS)
    return ImageFont.load_default(size * SS)


def backdrop(d):
    """The sponsor wall: a checkerboard, a coffee bean on every dark square."""
    tile = 50
    for row in range(H // tile + 1):
        for col in range(-(MARGIN // tile) - 1, (W + MARGIN) // tile + 1):
            x, y = col * tile, row * tile
            dark = (row + col) % 2 == 0
            d.rectangle(s(x, y, x + tile, y + tile), fill=CREAM_DARK if dark else CREAM)
            if dark:
                cx, cy = x + tile / 2, y + tile / 2
                d.ellipse(s(cx - 9, cy - 13, cx + 9, cy + 13), fill=COFFEE_LIGHT)
                d.arc(s(cx - 14, cy - 13, cx + 2, cy + 13), start=-70, end=70, fill=CREAM_DARK, width=2 * SS)
    d.rectangle(s(-MARGIN, FLOOR, W + MARGIN, H), fill=COFFEE_DARK)


def podium(d, numbers):
    for place, left, top, right in BLOCKS:
        d.rectangle(s(left, top, right, FLOOR), fill=WHITE, outline=COFFEE_LIGHT, width=SS)
        for x in range(left + 3, right - 3, 6):  # the dotted lip along the top edge
            d.rectangle(s(x, top + 3, x + 3, top + 6), fill=ACCENT)
        d.text(tuple(s((left + right) / 2, top + 10 + (FLOOR - top - 10) / 2 - (8 if place == 1 else 0))),
               str(place), font=numbers, fill=COFFEE, anchor="mm")
    # the checkered skirt under the winner's block, as in the original
    left, right = BLOCKS[1][1], BLOCKS[1][3]
    for k, x in enumerate(range(left, right, 9)):
        for j, y in enumerate((FLOOR - 18, FLOOR - 9)):
            if (k + j) % 2 == 0:
                d.rectangle(s(x, y, min(x + 9, right), y + 9), fill=COFFEE_DARK)


def limb(d, a, b, color, width):
    d.line(s(*a, *b), fill=color, width=width * SS)
    r = width / 2
    for x, y in (a, b):
        d.ellipse(s(x - r, y - r, x + r, y + r), fill=color)


def person(d, cx, feet, suit, hair, mood="happy", tie=None, hop=0.0, shake=0.0, shrug=0.0, stride=0.0):
    """Someone standing at (cx, feet), legs to head; returns the shoulders for the arms,
    which the caller draws because they are what each figure does, and the head's centre.
    `shake` moves the head sideways, `shrug` lifts the shoulders, `stride` spreads the
    feet for walking."""
    y = feet - hop
    hip, shoulder, neck = y - 30, y - 70, y - 74
    for side in (-1, 1):
        foot = cx + 8 * side + stride * side
        limb(d, (cx + 6 * side, hip), (foot, y - 4), suit, 10)
        d.rounded_rectangle(s(foot - 7, y - 6, foot + 7, y), radius=2 * SS, fill=COFFEE_DARK)
    d.rounded_rectangle(s(cx - 17, shoulder - shrug, cx + 17, hip + 4), radius=7 * SS, fill=suit)
    if tie:  # an official: white shirt in the V of the jacket, and a tie
        d.polygon([tuple(s(*p)) for p in ((cx - 8, shoulder), (cx + 8, shoulder), (cx, shoulder + 22))], fill=SHIRT)
        d.polygon([tuple(s(*p)) for p in ((cx - 2, shoulder + 2), (cx + 2, shoulder + 2), (cx + 3, shoulder + 20),
                                          (cx, shoulder + 24), (cx - 3, shoulder + 20))], fill=tie)
    else:  # a driver: the stripe down the racing suit
        d.rectangle(s(cx - 3, shoulder + 2, cx + 3, hip + 2), fill=WHITE)
    d.rectangle(s(cx - 4, neck, cx + 4, shoulder + 2), fill=SKIN)
    head = (cx + shake, neck - 13)
    face(d, head, hair, mood)
    return (cx - 15, shoulder + 5 - shrug), (cx + 15, shoulder + 5 - shrug), head


def face(d, head, hair, mood):
    d.ellipse(s(head[0] - 13, head[1] - 14, head[0] + 13, head[1] + 13), fill=SKIN)
    d.chord(s(head[0] - 14, head[1] - 16, head[0] + 14, head[1] + 6), start=180, end=360, fill=hair)
    ex, ey = head[0], head[1] + 1
    if mood == "smug":  # eyes half shut, one corner of the mouth up
        for side in (-1, 1):
            d.line(s(ex + 5 * side - 3, ey, ex + 5 * side + 3, ey), fill=COFFEE_DARK, width=2 * SS)
        d.line(s(ex - 4, ey + 7, ex + 4, ey + 5), fill=COFFEE_DARK, width=2 * SS)
    elif mood == "sad":  # eyes cast down, mouth turned down
        for side in (-1, 1):
            d.ellipse(s(ex + 5 * side - 2, ey + 1, ex + 5 * side + 2, ey + 4), fill=COFFEE_DARK)
        d.arc(s(ex - 5, ey + 8, ex + 5, ey + 14), start=200, end=340, fill=COFFEE_DARK, width=2 * SS)
    elif mood == "shout":  # brows down, mouth wide open
        for side in (-1, 1):
            d.ellipse(s(ex + 5 * side - 2, ey - 1, ex + 5 * side + 2, ey + 3), fill=COFFEE_DARK)
            d.line(s(ex + 2 * side, ey - 3, ex + 8 * side, ey - 6), fill=COFFEE_DARK, width=2 * SS)
        d.ellipse(s(ex - 3, ey + 5, ex + 3, ey + 11), fill=COFFEE_DARK)
    else:
        for side in (-1, 1):
            d.ellipse(s(ex + 5 * side - 2, ey - 2, ex + 5 * side + 2, ey + 2), fill=COFFEE_DARK)
        if mood == "happy":
            d.arc(s(ex - 6, ey + 1, ex + 6, ey + 9), start=10, end=170, fill=COFFEE_DARK, width=2 * SS)
        else:  # straight-faced official
            d.line(s(ex - 4, ey + 7, ex + 4, ey + 7), fill=COFFEE_DARK, width=2 * SS)
    if hair == JANITOR_CAP:  # a cap with a peak instead of hair
        d.rectangle(s(head[0] - 13, head[1] - 9, head[0] + 18, head[1] - 6), fill=hair)


def trophy(d, cx, bottom):
    d.rectangle(s(cx - 14, bottom - 6, cx + 14, bottom), fill=COFFEE_DARK)
    d.rectangle(s(cx - 4, bottom - 16, cx + 4, bottom - 6), fill=GOLD)
    d.chord(s(cx - 20, bottom - 58, cx + 20, bottom - 14), start=0, end=180, fill=GOLD)
    d.rectangle(s(cx - 20, bottom - 40, cx + 20, bottom - 36), fill=GOLD)
    for side in (-1, 1):
        d.arc(s(cx + side * 20 - 9, bottom - 38, cx + side * 20 + 9, bottom - 22),
              start=-90 if side > 0 else 90, end=90 if side > 0 else 270, fill=GOLD, width=3 * SS)
    star = [(cx + r * math.sin(a), bottom - 30 - r * math.cos(a))
            for k in range(10) for r, a in [((7 if k % 2 == 0 else 3), math.pi * k / 5)]]
    d.polygon([tuple(s(*p)) for p in star], fill=WHITE)


def tilt_at(t):
    """How far the cup is turned over (0 upright .. 1 upside down) at loop time t: turned
    over slowly, held there while it empties, turned back - and upright at the seam."""
    if t < 0.2:
        return ease(t / 0.2)
    if t < 0.7:
        return 1.0
    if t < 0.9:
        return 1.0 - ease((t - 0.7) / 0.2)
    return 0.0


def spilling_cup(d, hand, side, tilt, t):
    """A cup held out at `hand`, turned over towards `side` (-1 left, +1 right), pouring
    onto the floor while it is over far enough."""
    angle = side * math.radians(150) * tilt
    ca, sa = math.cos(angle), math.sin(angle)

    def at(x, y):  # cup coordinates (hand at 0,0; the cup's opening at y=-16) to the frame
        return hand[0] + x * ca - y * sa, hand[1] + x * sa + y * ca

    body = [at(-7 * side, -16), at(13 * side, -16), at(11 * side, 2), at(-5 * side, 2)]
    d.polygon([tuple(s(*p)) for p in body], fill=WHITE, outline=COFFEE_LIGHT)
    d.arc(s(hand[0] - 6, hand[1] - 12, hand[0] + 4, hand[1] - 2), 0, 360, fill=WHITE, width=2 * SS)
    if tilt > 0.55:
        rim = at(13 * side, -16)
        # the stream: wobbles a little, ends in splashes where it hits the floor
        points = []
        for k in range(16):
            u = k / 15
            points.append((rim[0] + 6 * side * u + 1.5 * math.sin(t * 40 + k), rim[1] + (FLOOR - rim[1]) * u))
        d.line([tuple(s(*p)) for p in points], fill=COFFEE, width=4 * SS, joint="curve")
        for k in range(6):
            age = (t * 6 + k / 6) % 1.0
            x = points[-1][0] + (k - 2.5) * 6 * age
            y = FLOOR - 2 - 14 * age + 18 * age * age
            r = 2.4 - age
            d.ellipse(s(x - r, y - r, x + r, y + r), fill=COFFEE)


def puddle(d, x, size):
    if size > 0.05:
        d.ellipse(s(x - 26 * size, FLOOR - 3 * size, x + 26 * size, FLOOR + 5 * size), fill=COFFEE)
        d.ellipse(s(x - 18 * size, FLOOR - 2 * size, x + 14 * size, FLOOR + 2 * size), fill=ACCENT)


def phase(t, start, end):
    """0..1 while t is between start and end, None outside - for the one-off scenes."""
    return (t - start) / (end - start) if start <= t < end else None


def official_arms(d, left, right, head, scratch, shrug, side_out):
    """Arms down; or one hand scratching the head; or both forearms out, palms up."""
    if scratch is not None:
        near, far = (right, left) if side_out > 0 else (left, right)
        hand = (head[0] + 11 * side_out, head[1] - 8 + 2 * math.sin(scratch * 40))
        elbow = (near[0] + 16 * side_out, near[1] + 6)
        limb(d, near, elbow, COFFEE_DARK, 8)
        limb(d, elbow, hand, COFFEE_DARK, 8)
        d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
        limb(d, far, (far[0] - 3 * side_out, far[1] + 34), COFFEE_DARK, 8)
    elif shrug is not None:
        for shoulder, side in ((left, -1), (right, 1)):
            elbow = (shoulder[0] + 2 * side, shoulder[1] + 20)
            hand = (shoulder[0] + 16 * side, shoulder[1] + 12)
            limb(d, shoulder, elbow, COFFEE_DARK, 8)
            limb(d, elbow, hand, COFFEE_DARK, 8)
            d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
    else:
        limb(d, left, (left[0] - 3, left[1] + 34), COFFEE_DARK, 8)
        limb(d, right, (right[0] + 3, right[1] + 34), COFFEE_DARK, 8)


def janitor(d, x, facing, walking, mopping, i):
    """Walks with the mop trailing, or stands and swings it over the puddle."""
    stride = 7 * math.sin(i * 1.3) if walking else 0.0
    left, right, head = person(d, x, FLOOR - abs(stride) * 0.2, OVERALL, JANITOR_CAP, mood="official",
                               stride=stride)
    swing = 10 * math.sin(i * 1.6) if mopping else 0.0
    mop = (x + 32 * facing + swing, FLOOR - 4)
    grip_high = (x + 8 * facing, left[1] + 4)
    grip_low = (x + 14 * facing + swing * 0.4, left[1] + 22)
    d.line(s(*grip_high, *mop), fill=COFFEE_LIGHT, width=3 * SS)
    d.rounded_rectangle(s(mop[0] - 12, mop[1] - 7, mop[0] + 12, mop[1] - 3), radius=SS, fill=COFFEE_LIGHT)
    for k in range(9):  # the mop's strands
        d.line(s(mop[0] - 12 + 3 * k, mop[1] - 4, mop[0] - 15 + 3.8 * k, mop[1] + 5), fill=WHITE, width=2 * SS)
    for shoulder, grip in ((left, grip_high), (right, grip_low)) if facing > 0 else ((right, grip_high), (left, grip_low)):
        limb(d, shoulder, grip, OVERALL, 8)
        d.ellipse(s(grip[0] - 4, grip[1] - 4, grip[0] + 4, grip[1] + 4), fill=SKIN)


# One loop of the whole scene, as fractions of it. The cups go over three times in that
# loop; everything else happens once.
SPILLS_PER_LOOP = 3
JANITOR_PATH = ((0.08, -90), (0.20, 80), (0.32, 80), (0.50, 466), (0.62, 466), (0.82, 690))
PUDDLES = ((106, 0.20, 0.32), (494, 0.50, 0.62))  # x, wiped from .. to


def janitor_at(t):
    """(x, walking, mopping) from JANITOR_PATH, None while off screen."""
    for (t0, x0), (t1, x1) in zip(JANITOR_PATH, JANITOR_PATH[1:]):
        if t0 <= t < t1:
            u = (t - t0) / (t1 - t0)
            return x0 + (x1 - x0) * u, x0 != x1, x0 == x1
    return None


def puddle_size(t, wipe_start, wipe_end):
    wiping = phase(t, wipe_start, wipe_end)
    if wiping is not None:
        return 1.0 - ease(wiping)
    return min(1.0, ((t - wipe_end) % 1.0) / 0.5)  # filled up again by the next spills


def frames(n=120):
    numbers = font(34)
    out = []
    for i in range(n):
        t = i / n
        img = Image.new("RGB", ((W + 2 * MARGIN) * SS, H * SS), CREAM)
        d = ImageDraw.Draw(img)
        backdrop(d)

        # the officials: scratch their heads, shrug, shake their heads at the mess
        for cx, hair, tie, side_out, scratches, shrugs, shakes in (
                (40, COFFEE_LIGHT, ACCENT, 1, ((0.02, 0.16),), ((0.40, 0.48),), ()),
                (560, (120, 120, 120), COFFEE_LIGHT, -1, ((0.86, 0.98),), ((0.70, 0.78),), ((0.24, 0.36),))):
            scratch = next((p for p in (phase(t, *w) for w in scratches) if p is not None), None)
            shrug = next((p for p in (phase(t, *w) for w in shrugs) if p is not None), None)
            shake = next((p for p in (phase(t, *w) for w in shakes) if p is not None), None)
            left, right, head = person(
                d, cx, FLOOR, COFFEE_DARK, hair, mood="official", tie=tie,
                shake=4 * math.sin(shake * 6 * math.pi) if shake is not None else 0.0,
                shrug=5 * math.sin(math.pi * shrug) if shrug is not None else 0.0)
            official_arms(d, left, right, head, scratch, shrug, side_out)

        for x, wipe_start, wipe_end in PUDDLES:
            puddle(d, x, puddle_size(t, wipe_start, wipe_end))

        podium(d, numbers)

        # 1st: lifts the trophy over their head, twice per loop, with a little jump
        lift = ease(abs(math.sin(2 * math.pi * t * SPILLS_PER_LOOP)))
        left, right, _ = person(d, 300, BLOCKS[1][2], COFFEE, COFFEE_DARK, hop=3 * lift)
        hands_y = left[1] - 66 - 16 * lift
        # hands wide apart on the handles, so the arms go up beside the head, not across it
        limb(d, left, (271, hands_y), COFFEE, 8)
        limb(d, right, (329, hands_y), COFFEE, 8)
        trophy(d, 300, hands_y + 30)  # held by the handles
        for x in (271, 329):
            d.ellipse(s(x - 4, hands_y - 4, x + 4, hands_y + 4), fill=SKIN)

        # 2nd and 3rd: the other hand on the hip, the cup held out and poured away
        for (place, left_x, top, right_x), side, suit, hair, delay in (
                (BLOCKS[0], -1, COFFEE_LIGHT, (40, 28, 20), 0.0),
                (BLOCKS[2], 1, ACCENT, (160, 90, 40), 0.08)):
            cx = (left_x + right_x) / 2
            left, right, _ = person(d, cx, top, suit, hair, mood="smug")
            out_shoulder, hip_shoulder = (left, right) if side < 0 else (right, left)
            hand = (out_shoulder[0] + 50 * side, out_shoulder[1] - 8)
            limb(d, out_shoulder, hand, suit, 8)
            elbow = (hip_shoulder[0] - 12 * side, hip_shoulder[1] + 14)
            limb(d, hip_shoulder, elbow, suit, 8)
            limb(d, elbow, (hip_shoulder[0] - 2 * side, hip_shoulder[1] + 26), suit, 8)
            spilling_cup(d, hand, side, tilt_at((t * SPILLS_PER_LOOP - delay) % 1.0), i / 3)
            d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)

        # the janitor walks in front of everything
        where = janitor_at(t)
        if where is not None:
            x, walking, mopping = where
            janitor(d, x, 1, walking, mopping, i)

        out.append(img.resize((OUT_W, OUT_H), Image.LANCZOS))
    return out


def save(name, frames, duration):
    os.makedirs(OUT, exist_ok=True)
    strip = Image.new("RGB", (OUT_W, OUT_H * len(frames)))
    for k, frame in enumerate(frames):
        strip.paste(frame, (0, OUT_H * k))
    palette = strip.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    path = os.path.join(OUT, name)
    quantized[0].save(path, save_all=True, append_images=quantized[1:], duration=duration, loop=0,
                      optimize=True, disposal=1)
    print(f"{path}: {len(frames)} frames, {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    save("podium.gif", frames(), 80)
