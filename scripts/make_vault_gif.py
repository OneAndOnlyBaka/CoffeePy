"""Draws the kiosk ranking's month view into static/kiosk/img/ranking/vault.gif.

    /tmp/gif/bin/python scripts/make_vault_gif.py      # venv with pillow, see make_wait_gifs.py

The month isn't over, so nobody has won it yet: instead of the podium, the vault from
Konami's Hyper Sports (1984). The month's top three stand by while the next athlete takes a
long run-up, takes off from the springboard, leapfrogs over a chair, wobbles, sticks the
landing, gets themselves a coffee from the machine behind it and drinks it - and only then,
after putting their heads together, do the judges hold up their scores. Same stage, wall,
floor and people as the podium (make_podium_gif.py) - the two GIFs sit in the same frame on
the kiosk.
"""
import math

from PIL import Image, ImageDraw

from make_podium_gif import (ACCENT, COFFEE, COFFEE_DARK, COFFEE_LIGHT, CREAM, FLOOR, H, MARGIN, OUT_H, OUT_W,
                             OVERALL, SKIN, SS, W, WHITE, backdrop, face, font, limb, person, s, save)
from make_wait_gifs import ease

# Along the floor: where the run-up starts (off the picture), the springboard, the chair, the
# landing mat, the coffee machine, where the jumper stands at it, and the judges' table.
START_X, BOARD_X, CHAIR_X, LANDING_X, MACHINE_X, AT_MACHINE_X, TABLE_X = (
    -MARGIN - 60, 230, 300, 370, 446, 404, 506)
DESK_TOP = FLOOR - 36  # the little table the machine stands on
MACHINE_TOP = DESK_TOP - 58
CHAIR_TOP = FLOOR - 80  # the top of its backrest, which the hands push off
WOOD, WOOD_EDGE = COFFEE_LIGHT, COFFEE

# The loop, in seconds. Everything below is timed in seconds and converted per frame.
LOOP, FRAME_MS = 16.0, 80
RUN_END, BOARD_END = 4.0, 4.25
FLIGHT = (4.25, 5.6)  # in the air (motion lines, the top three craning their necks)
POURING = (7.8, 8.6)  # the machine runs
CUP_TAKEN = 8.9  # from here on the cup is in the jumper's hand
CONFER = (10.4, 11.8)  # the judges put their heads together
SCORES = (11.8, 13.8)  # the judges' cards are up
WALK_OFF = 13.8


def machine(d, steam_phase, t):
    """The espresso machine on its table behind the landing: steaming, with a cup under it
    until the jumper takes it, coffee running into that cup while it's on."""
    x0, x1 = MACHINE_X - 30, MACHINE_X + 30
    d.rectangle(s(MACHINE_X - 38, DESK_TOP, MACHINE_X + 38, DESK_TOP + 6), fill=WOOD, outline=WOOD_EDGE, width=SS)
    for x in (MACHINE_X - 34, MACHINE_X + 29):
        d.rectangle(s(x, DESK_TOP + 6, x + 5, FLOOR), fill=WOOD, outline=WOOD_EDGE, width=SS)
    d.rounded_rectangle(s(x0, MACHINE_TOP, x1, DESK_TOP), radius=6 * SS, fill=COFFEE_DARK)
    d.rectangle(s(x0 + 6, MACHINE_TOP + 5, x1 - 6, MACHINE_TOP + 12), fill=COFFEE_LIGHT)  # the cup warmer
    d.ellipse(s(MACHINE_X + 12, MACHINE_TOP + 17, MACHINE_X + 24, MACHINE_TOP + 29), fill=ACCENT)  # the button
    d.rectangle(s(MACHINE_X - 12, MACHINE_TOP + 20, MACHINE_X + 2, MACHINE_TOP + 28), fill=COFFEE_LIGHT)  # group
    d.line(s(MACHINE_X - 22, MACHINE_TOP + 30, MACHINE_X + 4, MACHINE_TOP + 30), fill=COFFEE_LIGHT, width=4 * SS)
    if POURING[0] <= t < POURING[1]:
        d.line(s(MACHINE_X - 5, MACHINE_TOP + 30, MACHINE_X - 5, DESK_TOP - 14), fill=COFFEE, width=2 * SS)
    if t < CUP_TAKEN:
        cup(d, (MACHINE_X - 5, DESK_TOP - 7))
    for k in range(3):
        points = [(x0 + 12 + 10 * k + 3 * math.sin(steam_phase + k + i / 3), MACHINE_TOP - 2 - i * 1.6)
                  for i in range(12)]
        d.line([tuple(s(*p)) for p in points], fill=COFFEE_LIGHT, width=2 * SS, joint="curve")


def cup(d, centre, steam_phase=None):
    x, y = centre
    d.rounded_rectangle(s(x - 7, y - 7, x + 7, y + 7), radius=2 * SS, fill=WHITE, outline=COFFEE_LIGHT)
    d.arc(s(x + 4, y - 4, x + 11, y + 3), start=-90, end=90, fill=WHITE, width=2 * SS)
    if steam_phase is not None:
        points = [(x + 2 * math.sin(steam_phase + k / 2), y - 9 - k * 1.3) for k in range(9)]
        d.line([tuple(s(*p)) for p in points], fill=COFFEE_LIGHT, width=2 * SS, joint="curve")


UPHOLSTERY, UPHOLSTERY_EDGE = (156, 44, 36), (96, 26, 22)
METAL = (64, 64, 70)
SPIN = (4.95, 6.6)  # the push off the backrest sets the chair turning


def chair(d, t):
    """An office swivel chair - five-star base on castors, gas lift, seat, backrest - in
    colours of its own, so it stands out from the wall. The backrest faces the run-up (the
    hands push off its top); after the push the chair turns twice and settles back."""
    seat = FLOOR - 40
    spin = 0.0
    if SPIN[0] <= t < SPIN[1]:
        spin = 4 * math.pi * ease((t - SPIN[0]) / (SPIN[1] - SPIN[0]))
    d.line(s(CHAIR_X - 24, FLOOR - 7, CHAIR_X + 24, FLOOR - 7), fill=METAL, width=4 * SS)  # the star, side-on
    for x in (CHAIR_X - 22, CHAIR_X, CHAIR_X + 22):
        d.ellipse(s(x - 3.5, FLOOR - 7, x + 3.5, FLOOR), fill=COFFEE_DARK)
    d.rectangle(s(CHAIR_X - 3, seat, CHAIR_X + 3, FLOOR - 7), fill=METAL)  # the gas lift
    # the backrest goes round the seat as it turns: behind it on the far side, in front on ours
    back_x = CHAIR_X - 22 * math.cos(spin)
    back_w = 4 + 3 * abs(math.cos(spin))
    behind = math.sin(spin) > 0

    def backrest():
        d.rectangle(s(back_x - 2, seat - 12, back_x + 2, seat - 4), fill=METAL)
        d.rounded_rectangle(s(back_x - back_w, CHAIR_TOP, back_x + back_w, seat - 10), radius=3 * SS,
                            fill=UPHOLSTERY, outline=UPHOLSTERY_EDGE, width=SS)

    if behind:
        backrest()
    d.rounded_rectangle(s(CHAIR_X - 24, seat - 6, CHAIR_X + 24, seat + 2), radius=3 * SS,
                        fill=UPHOLSTERY, outline=UPHOLSTERY_EDGE, width=SS)
    if not behind:
        backrest()


def springboard(d, squash):
    """A vaulting board: a sloped top in the accent colour with a darker edge, the side
    showing its thickness, stripes across where the feet go."""
    rise = 16 - 6 * squash
    back, front = BOARD_X - 34, BOARD_X + 20
    d.polygon([tuple(s(*p)) for p in ((back, FLOOR), (front, FLOOR - rise), (front, FLOOR))], fill=WOOD_EDGE)
    d.polygon([tuple(s(*p)) for p in ((back, FLOOR - 1), (front, FLOOR - rise - 1), (front, FLOOR - rise + 4),
                                      (back + 6, FLOOR - 1))], fill=ACCENT)
    for k in range(3):  # grip stripes
        x = front - 6 - 8 * k
        y = FLOOR - rise * (x - back) / (front - back)
        d.line(s(x, y - 1, x - 2, y + 3), fill=WHITE, width=2 * SS)
    d.rectangle(s(front - 3, FLOOR - rise - 1, front, FLOOR), fill=COFFEE_DARK)


def landing_mat(d):
    d.rounded_rectangle(s(LANDING_X - 32, FLOOR - 7, LANDING_X + 32, FLOOR), radius=3 * SS, fill=COFFEE_LIGHT)


def dust(d, x, t):
    """Little clouds kicked up behind the runner, more of them the faster they go."""
    for k in range(4):
        age = (t * 3 + k / 4) % 1.0
        r = 2 + 4 * age
        px = x - 16 - 22 * age - 8 * k
        d.ellipse(s(px - r, FLOOR - 4 - r - 6 * age, px + r, FLOOR - 4 + r - 6 * age), fill=CREAM_DUST)


CREAM_DUST = (214, 190, 166)


def motion_lines(d, hip):
    for k, dy in enumerate((-30, -12, 6)):
        x = hip[0] - 34 - 6 * k
        d.line(s(x - 18, hip[1] + dy, x, hip[1] + dy), fill=COFFEE_LIGHT, width=2 * SS)


def body_points(hip, lean):
    """Shoulder (where the arms hang) and head centre for a body tipped forward by `lean` -
    the same proportions as an upright person() on the podium."""
    up = (math.sin(lean), -math.cos(lean))
    return (hip[0] + 35 * up[0], hip[1] + 35 * up[1]), (hip[0] + 57 * up[0], hip[1] + 57 * up[1])


def hands_for(hip, lean, arms):
    """Arm angles (from straight down, positive forward) to hand positions; points pass through."""
    shoulder, _ = body_points(hip, lean)
    return [a if isinstance(a, tuple) else (shoulder[0] + 34 * math.sin(a), shoulder[1] + 34 * math.cos(a))
            for a in arms]


def athlete(d, hip, lean, legs, hands, holding=False, steam_phase=0.0):
    """The jumper in any pose: legs as angles from straight down (positive forward), hands as
    points. Built like person() - jacket, stripe, neck, head - only tipped by `lean`; the
    head goes on after the arms, so no arm ever crosses the face, and the cup after the head,
    so it can reach the mouth."""
    up = (math.sin(lean), -math.cos(lean))
    across = (-up[1], up[0])

    def at(along, side):
        return tuple(s(hip[0] + along * up[0] + side * across[0], hip[1] + along * up[1] + side * across[1]))

    for angle in legs:
        foot = (hip[0] + 32 * math.sin(angle), hip[1] + 32 * math.cos(angle))
        limb(d, hip, foot, OVERALL, 10)
        d.ellipse(s(foot[0] - 7, foot[1] - 3, foot[0] + 7, foot[1] + 3), fill=COFFEE_DARK)
    d.polygon([at(-4, -14), at(-4, 14), at(36, 15), at(40, 11), at(40, -11), at(36, -15)], fill=OVERALL)
    d.polygon([at(-2, -3), at(-2, 3), at(38, 3), at(38, -3)], fill=WHITE)  # the stripe
    d.polygon([at(38, -4), at(38, 4), at(46, 4), at(46, -4)], fill=SKIN)  # the neck
    shoulder, head = body_points(hip, lean)
    for hand in hands:
        limb(d, shoulder, hand, OVERALL, 8)
    face(d, head, COFFEE_DARK, "happy")
    for k, hand in enumerate(hands):
        if holding and k == 0:
            cup(d, (hand[0] + 2, hand[1] - 6), steam_phase)
        d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)


# Everything after the board, keyframe by keyframe: (seconds, hip, lean, legs, arms). Arms
# are angles, or the points the hands are on - flat on the backrest while they push off it,
# on the machine's button, at the cup. WALK marks the stretches walked rather than flown,
# which get striding legs instead of the keyframes' ones.
ON_CHAIR = ((CHAIR_X - 26, CHAIR_TOP - 2), (CHAIR_X - 18, CHAIR_TOP - 2))
STAND = (AT_MACHINE_X, FLOOR - 30)
BUTTON = (MACHINE_X + 18, MACHINE_TOP + 23)
CUP_UNDER_MACHINE = (MACHINE_X - 7, DESK_TOP - 1)
SIP = (AT_MACHINE_X + 9, FLOOR - 74)  # the cup, drawn above the hand, then sits at the mouth
WALK = ((6.8, 7.4),)
TIMELINE = (
    (BOARD_END, (BOARD_X + 4, FLOOR - 34), 0.35, (-0.2, -0.45), (1.6, 1.4)),  # off the board, arms forward
    (4.65, (CHAIR_X - 49, CHAIR_TOP - 4), 0.75, (-0.6, -0.3), ON_CHAIR),  # hands down on the backrest
    (4.95, (CHAIR_X - 24, CHAIR_TOP - 10), 0.5, (1.1, 1.5), ON_CHAIR),  # legs spread forward past it
    (5.3, (CHAIR_X + 34, FLOOR - 86), 0.1, (0.5, 0.2), (2.3, 2.1)),  # pushed off, upright, arms up
    (5.6, (LANDING_X, FLOOR - 22), -0.05, (0.5, -0.4), (1.7, 1.5)),  # touch-down, knees giving
    (5.75, (LANDING_X - 2, FLOOR - 28), -0.18, (0.4, -0.3), (2.8, -1.9)),  # wobbling, arms going round
    (5.9, (LANDING_X + 2, FLOOR - 28), 0.14, (0.3, -0.4), (0.9, 2.9)),
    (6.05, (LANDING_X, FLOOR - 29), -0.07, (0.3, -0.3), (2.5, -1.5)),
    (6.2, (LANDING_X, FLOOR - 30), 0.0, (0.3, -0.3), (2.3, -2.3)),  # stuck it, arms up
    (6.6, (LANDING_X, FLOOR - 30), 0.0, (0.3, -0.3), (2.3, -2.3)),
    (6.8, (LANDING_X, FLOOR - 30), 0.0, (0.0, 0.0), (0.2, -0.2)),  # and off to the machine
    (7.4, STAND, 0.0, (0.1, -0.1), (0.2, -0.2)),
    (7.7, STAND, 0.12, (0.1, -0.1), (BUTTON, -0.2)),  # presses the button
    (7.9, STAND, 0.0, (0.1, -0.1), (0.4, -0.2)),  # waits for it, tapping a foot
    (8.6, STAND, 0.0, (0.1, -0.1), (0.4, -0.2)),
    (8.9, STAND, 0.15, (0.1, -0.1), (CUP_UNDER_MACHINE, -0.3)),  # takes the cup
    (9.2, STAND, 0.0, (0.1, -0.1), (SIP, -0.2)),  # drinks
    (10.0, STAND, -0.1, (0.1, -0.1), (SIP, -0.2)),
    (10.2, (AT_MACHINE_X, FLOOR - 36), -0.2, (0.15, -0.15), (0.9, 1.2)),  # aaah - a little hop
    (10.4, STAND, 0.0, (0.1, -0.1), (0.9, -0.2)),
    (11.8, STAND, 0.0, (0.1, -0.1), (0.9, -0.2)),  # waits for the judges, sipping now and then
    (12.1, STAND, 0.0, (0.1, -0.1), (2.7, -0.2)),  # raises the cup to them
    (WALK_OFF, STAND, 0.0, (0.1, -0.1), (2.7, -0.2)),
)


def lerp(a, b, u):
    return a + (b - a) * u


def pose_at(t, i):
    """(hip, lean, legs, hands, squash of the board) at t seconds into the loop."""
    run = 1.4 * i  # stride phase
    if t < RUN_END:  # the run-up, picking up speed
        u = t / RUN_END
        x = lerp(START_X, BOARD_X - 14, u * u * (1.6 - 0.6 * u))
        swing = (0.45 + 0.4 * u) * math.sin(run)
        hip, lean = (x, FLOOR - 32 - 4 * u * abs(math.sin(run))), 0.15 + 0.2 * u
        return hip, lean, (swing, -swing), hands_for(hip, lean, (-swing * 1.2, swing * 1.2)), 0.0
    if t < BOARD_END:  # two feet down on the board, arms swung back
        u = (t - RUN_END) / (BOARD_END - RUN_END)
        hip, lean = (BOARD_X - 2, FLOOR - 24), 0.35
        return hip, lean, (0.3, -0.1), hands_for(hip, lean, (-0.8, -0.8)), 1.0 - abs(2 * u - 1)
    if t >= WALK_OFF:  # walking off past the judges, cup in hand
        x = lerp(AT_MACHINE_X, W + MARGIN + 50, (t - WALK_OFF) / (LOOP - WALK_OFF))
        swing = 0.45 * math.sin(run)
        hip = (x, FLOOR - 30 - abs(math.sin(run)))
        return hip, 0.05, (swing, -swing), hands_for(hip, 0.05, (0.9, -swing)), 0.0
    for (t0, hip0, lean0, legs0, arms0), (t1, hip1, lean1, legs1, arms1) in zip(TIMELINE, TIMELINE[1:]):
        if t0 <= t < t1:
            u = ease((t - t0) / (t1 - t0))
            hip = (lerp(hip0[0], hip1[0], u), lerp(hip0[1], hip1[1], u))
            lean = lerp(lean0, lean1, u)
            legs = [lerp(a, b, u) for a, b in zip(legs0, legs1)]
            if any(w0 <= t < w1 for w0, w1 in WALK):
                swing = 0.45 * math.sin(run)
                legs, hip = (swing, -swing), (hip[0], hip[1] - abs(math.sin(run)))
            if 7.9 <= t < 8.6:  # impatient foot
                legs = (legs[0] + 0.25 * max(0.0, math.sin(i * 1.6)), legs[1])
            hands = [(lerp(a[0], b[0], u), lerp(a[1], b[1], u))
                     for a, b in zip(hands_for(hip0, lean0, arms0), hands_for(hip1, lean1, arms1))]
            if 10.4 <= t < 11.8 and 0.3 < (t - 10.4) % 0.7 < 0.6:  # another sip while waiting
                hands[0] = SIP
            return hip, lean, legs, hands, 0.0


def judges(d, t, i):
    """Three officials behind a table: they confer once the jumper has had their coffee -
    heads together, the middle one unsure - and then the cards go up."""
    cards = (ease(min(1.0, max(0.0, (t - SCORES[0]) / 0.4)))
             * (1 - ease(min(1.0, max(0.0, (t - SCORES[1]) / 0.4)))))
    conferring = ease(min(1.0, max(0.0, (t - CONFER[0]) / 0.3))) * (1 - ease(min(1.0, max(0.0, (t - CONFER[1] + 0.3) / 0.3))))
    numbers = font(15)
    for k, (cx, score) in enumerate(((TABLE_X + 20, "9"), (TABLE_X + 60, "10"), (TABLE_X + 100, "9"))):
        lean_in = (6, 0, -6)[k] * conferring
        if k == 1:
            lean_in = 3 * math.sin(i * 0.9) * conferring  # shaking their head
        left, right, head = person(d, cx, FLOOR - 6, COFFEE_DARK, (120, 120, 120) if k == 1 else COFFEE_LIGHT,
                                   mood="official", tie=ACCENT if k != 1 else COFFEE_LIGHT, shake=lean_in)
        if cards > 0.05:
            hand = (right[0] + 6, right[1] - 10 - 26 * cards)
            limb(d, right, hand, COFFEE_DARK, 8)
            d.rectangle(s(hand[0] - 11, hand[1] - 26, hand[0] + 11, hand[1] - 4), fill=WHITE, outline=COFFEE_LIGHT)
            d.text(tuple(s(hand[0], hand[1] - 15)), score, font=numbers, fill=COFFEE, anchor="mm")
            d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
    d.rectangle(s(TABLE_X - 20, FLOOR - 40, W + MARGIN + 10, FLOOR), fill=WHITE, outline=COFFEE_LIGHT, width=SS)
    for x in range(TABLE_X - 16, W + MARGIN + 6, 6):
        d.rectangle(s(x, FLOOR - 37, x + 3, FLOOR - 34), fill=ACCENT)


def leaders(d, t, i):
    """The month's top three, in the podium's colours, by the run-up. They crane their necks
    while the jumper is in the air; when the scores go up one claps, one shakes their head
    with arms still folded, one scratches theirs."""
    reacting = SCORES[0] <= t < SCORES[1]
    watching = FLIGHT[0] - 0.3 <= t < FLIGHT[1] + 0.3
    for place, cx, suit, hair in ((1, 22, COFFEE, COFFEE_DARK), (2, 66, COFFEE_LIGHT, (40, 28, 20)),
                                  (3, 110, ACCENT, (160, 90, 40))):
        mood = "smug" if place == 2 else "happy" if place == 1 else "official"
        shake = 5 if watching else 0.0
        if place == 2 and reacting:
            shake = 3 * math.sin(i * 1.1)
        hop = 3 * abs(math.sin(i * 1.2 + place)) if watching else 0.0
        left, right, head = person(d, cx, FLOOR, suit, hair, mood=mood, shake=shake, hop=hop)
        if place == 1 and reacting:
            gap = 3 + 5 * abs(math.sin(i * 1.4))
            for shoulder, side in ((left, -1), (right, 1)):
                hand = (cx + gap * side, shoulder[1] + 4)
                limb(d, shoulder, hand, suit, 8)
                d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
        elif place == 2:  # arms folded across the stripe
            for shoulder, side in ((left, -1), (right, 1)):
                elbow = (shoulder[0] + 2 * side, shoulder[1] + 18)
                limb(d, shoulder, elbow, suit, 8)
                limb(d, elbow, (cx - 8 * side, shoulder[1] + 14), suit, 8)
        elif place == 3 and reacting:  # that score is worrying
            hand = (head[0] + 11, head[1] - 8 + 2 * math.sin(i * 2.2))
            elbow = (right[0] + 14, right[1] + 6)
            limb(d, right, elbow, suit, 8)
            limb(d, elbow, hand, suit, 8)
            d.ellipse(s(hand[0] - 4, hand[1] - 4, hand[0] + 4, hand[1] + 4), fill=SKIN)
            limb(d, left, (left[0] - 3, left[1] + 34), suit, 8)
        else:
            limb(d, left, (left[0] - 3, left[1] + 34), suit, 8)
            limb(d, right, (right[0] + 3, right[1] + 34), suit, 8)


def frames():
    out = []
    n = round(LOOP * 1000 / FRAME_MS)
    for i in range(n):
        t = LOOP * i / n
        img = Image.new("RGB", ((W + 2 * MARGIN) * SS, H * SS), CREAM)
        d = ImageDraw.Draw(img)
        backdrop(d)
        leaders(d, t, i)
        judges(d, t, i)
        landing_mat(d)
        machine(d, 2 * math.pi * t, t)
        chair(d, t)
        hip, lean, legs, hands, squash = pose_at(t, i)
        springboard(d, squash)
        if t < RUN_END:
            dust(d, hip[0], t)
        if FLIGHT[0] <= t < FLIGHT[0] + 0.9:
            motion_lines(d, hip)
        athlete(d, hip, lean, legs, hands, holding=t >= CUP_TAKEN, steam_phase=2 * math.pi * t)
        out.append(img.resize((OUT_W, OUT_H), Image.LANCZOS))
    return out


if __name__ == "__main__":
    save("vault.gif", frames(), FRAME_MS)
