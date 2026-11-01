"""Draws the kiosk's "apply your card" animations, one per mode, into static/kiosk/img/wait/.

    python3 -m venv /tmp/gif && /tmp/gif/bin/pip install pillow
    /tmp/gif/bin/python scripts/make_wait_gifs.py

Dev tool only - Pillow is not an app dependency, the GIFs it writes are committed. Same
format as the thank-you GIFs (220x220, kiosk palette, flat shapes); every frame is drawn
at 4x and scaled down, which is what keeps the curves smooth in a 256-colour GIF.
"""
import math
import os

from PIL import Image, ImageDraw

SIZE, SS = 220, 4  # output size, supersampling factor
CREAM = (247, 239, 230)
CREAM_DARK = (230, 212, 193)
COFFEE = (107, 59, 0)
COFFEE_DARK = (59, 35, 20)
COFFEE_LIGHT = (169, 116, 79)
ACCENT = (201, 138, 61)
SUCCESS = (47, 122, 61)
MILK = (255, 255, 255)
MILK_BLUE = (52, 106, 180)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "kiosk", "img", "wait")


def canvas():
    img = Image.new("RGB", (SIZE * SS, SIZE * SS), CREAM)
    return img, ImageDraw.Draw(img)


def s(*values):
    """Design coordinates (0-220) to supersampled pixels."""
    return [round(v * SS) for v in values]


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def finish(img):
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def save(name, frames, duration):
    os.makedirs(OUT, exist_ok=True)
    # One palette for the whole animation, built from every frame: taken from the first
    # alone, colours that only appear later (the coins, the bars) had no entry of their own.
    strip = Image.new("RGB", (SIZE, SIZE * len(frames)))
    for k, frame in enumerate(frames):
        strip.paste(frame, (0, SIZE * k))
    palette = strip.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    # The background has to come out as the page's exact --cream, or the GIF sits on the
    # kiosk as a slightly darker square. Fixing the palette before the remap is not enough:
    # quantize(palette=...) matches at reduced precision and lands the background on a
    # neighbouring entry. So every entry that is cream for all practical purposes becomes
    # cream, after the fact.
    entries = quantized[0].getpalette()
    for k in range(len(entries) // 3):
        if sum((entries[3 * k + c] - CREAM[c]) ** 2 for c in range(3)) <= 27:
            entries[3 * k:3 * k + 3] = CREAM
    for frame in quantized:
        frame.putpalette(entries)
    path = os.path.join(OUT, name)
    quantized[0].save(path, save_all=True, append_images=quantized[1:], duration=duration, loop=0,
                      optimize=True, disposal=1)
    print(f"{path}: {len(frames)} frames, {os.path.getsize(path) // 1024} KB")


def steam(d, x, top, phase, alpha_t):
    """A wavy wisp of steam; alpha_t 0..1 fades it in by shortening it."""
    length = 46 * alpha_t
    points = []
    for i in range(24):
        y = top + 46 - length * i / 23
        points.append((x + 6 * math.sin(phase + i / 4), y))
    if len(points) > 1 and length > 2:
        d.line([tuple(s(px, py)) for px, py in points], fill=ACCENT, width=4 * SS, joint="curve")


def order_frames(n=36):
    """A cup filling from the machine's spout, then steaming."""
    frames = []
    for i in range(n):
        img, d = canvas()
        t = i / n
        fill = ease(t / 0.55)  # pouring for the first 55% of the loop
        # saucer + shadow
        d.ellipse(s(48, 172, 172, 188), fill=CREAM_DARK)
        # spout of the machine
        d.rounded_rectangle(s(92, 14, 128, 34), radius=4 * SS, fill=COFFEE_DARK)
        d.rectangle(s(104, 34, 116, 42), fill=COFFEE_DARK)
        # stream while pouring
        if t < 0.55:
            d.rectangle(s(107, 42, 113, 150 - 66 * fill), fill=COFFEE)
        # cup body (drawn as the inside, then the coffee level, then the rim)
        d.rounded_rectangle(s(62, 92, 158, 172), radius=16 * SS, fill=COFFEE)
        d.rounded_rectangle(s(70, 98, 150, 164), radius=12 * SS, fill=CREAM)
        level = 164 - 60 * fill
        if fill > 0.02:
            d.rounded_rectangle(s(70, level, 150, 164), radius=12 * SS, fill=COFFEE_DARK)
        d.arc(s(146, 108, 182, 146), start=-90, end=90, fill=COFFEE, width=9 * SS)
        # steam once full
        if t >= 0.55:
            k = (t - 0.55) / 0.45
            for j, x in enumerate((92, 110, 128)):
                steam(d, x, 40, 2 * math.pi * (k * 1.5) + j, min(1.0, k * 3 - j * 0.3))
        frames.append(finish(img))
    return frames


def info_frames(n=36):
    """Coins dropping onto a stack next to a bar chart that grows with them."""
    frames = []
    coin_times = (0.05, 0.25, 0.45)
    for i in range(n):
        img, d = canvas()
        t = i / n
        d.line(s(24, 182, 196, 182), fill=CREAM_DARK, width=4 * SS)
        # bar chart
        for j, (x, h) in enumerate(((118, 50), (146, 80), (174, 112))):
            grow = ease((t - 0.1 - j * 0.15) / 0.3)
            if grow > 0:
                top = 180 - h * grow
                d.rounded_rectangle(s(x, top, x + 20, 180), radius=4 * SS, fill=SUCCESS if j == 2 else COFFEE_LIGHT)
        # coin stack: each coin falls, then rests
        for j, start in enumerate(coin_times):
            rest_y = 166 - j * 14
            fall = (t - start) / 0.16
            if fall < 0:
                continue
            y = rest_y if fall >= 1 else 20 + (rest_y - 20) * fall * fall
            # a falling coin spins (narrow ellipse), a resting one lies flat
            width = 30 if fall >= 1 else 30 * abs(math.cos(fall * math.pi * 2)) + 4
            d.ellipse(s(62 - width, y - 9, 62 + width, y + 9), fill=ACCENT, outline=COFFEE, width=2 * SS)
            if fall >= 1:
                d.ellipse(s(44, y - 5, 80, y + 5), outline=COFFEE_LIGHT, width=SS)
        frames.append(finish(img))
    return frames


def gear(d, cx, cy, radius, teeth, angle, color):
    tooth_depth = radius * 0.22  # keep in step with `depth` in settings_frames
    points = []
    steps = teeth * 4
    for k in range(steps):
        a = angle + 2 * math.pi * k / steps
        r = radius if (k % 4) in (0, 1) else radius - tooth_depth
        points.append(tuple(s(cx + r * math.cos(a), cy + r * math.sin(a))))
    d.polygon(points, fill=color)
    hole = radius * 0.32
    d.ellipse(s(cx - hole, cy - hole, cx + hole, cy + hole), fill=CREAM)


def settings_frames(n=30):
    """Two meshing gears. Over one loop the big one turns exactly one tooth and the small
    one the matching angle the other way, so the loop has no visible seam. Distance and
    starting phase are computed so a tooth of one sits in a gap of the other."""
    frames = []
    big_teeth, small_teeth = 12, 8
    big_r, small_r = 60, 40
    depth = 0.22
    distance = big_r + small_r - (big_r + small_r) * depth / 2
    line = -math.pi / 4  # small gear up and to the right of the big one
    big_c = (93, 128)  # puts the pair as a whole in the middle of the frame
    small_c = (big_c[0] + distance * math.cos(line), big_c[1] + distance * math.sin(line))
    big_step, small_step = 2 * math.pi / (big_teeth * 4), 2 * math.pi / (small_teeth * 4)
    # gear() draws a tooth over its first two points of every four: its centre sits half a
    # step in, a gap's centre two and a half steps in.
    big_start = line - 0.5 * big_step
    small_start = line + math.pi - 2.5 * small_step
    for i in range(n):
        img, d = canvas()
        t = i / n
        gear(d, *big_c, big_r, big_teeth, big_start + 2 * math.pi / big_teeth * t, COFFEE)
        gear(d, *small_c, small_r, small_teeth, small_start - 2 * math.pi / small_teeth * t, ACCENT)
        frames.append(finish(img))
    return frames


def bean(d, cx, cy, rx, ry, angle):
    """A coffee bean: a tilted oval with the S-shaped crease that tells it from any other dot."""
    a = math.radians(angle)

    def at(u, v):
        return tuple(s(cx + u * math.cos(a) - v * math.sin(a), cy + u * math.sin(a) + v * math.cos(a)))
    d.polygon([at(rx * math.cos(k / 20 * math.pi), ry * math.sin(k / 20 * math.pi)) for k in range(40)],
              fill=COFFEE_DARK)
    crease = [at(0.25 * rx * math.sin(math.pi * (k / 15 - 0.5) * 2), ry * 0.8 * (k / 15 * 2 - 1)) for k in range(16)]
    d.line(crease, fill=ACCENT, width=2 * SS, joint="curve")


def purchase_frames(n=36):
    """A bag of coffee beans, a carton of milk and a cookie dropping into a shopping bag, which
    then gets a tick - bought for everyone. Each sticks out far enough to be told apart: the
    bean on the kraft bag, the gable and the blue band with a drop on the carton."""
    frames = []
    drops = ((0.0, 50), (0.2, 36), (0.4, 92))  # (start, top where it comes to rest)
    for i in range(n):
        img, d = canvas()
        t = i / n
        d.line(s(24, 190, 196, 190), fill=CREAM_DARK, width=4 * SS)
        # what drops in, drawn behind the bag's front so it disappears into it
        for j, (start, rest) in enumerate(drops):
            if t < start:
                continue
            y = -90 + (rest + 90) * ease((t - start) / 0.22)
            if j == 0:  # kraft bag of coffee: crimped top, a big bean on the front
                d.rounded_rectangle(s(58, y + 8, 102, y + 100), radius=5 * SS, fill=ACCENT)
                d.rectangle(s(58, y, 102, y + 10), fill=COFFEE_LIGHT)
                for x in range(58, 98, 6):
                    d.polygon([tuple(s(x, y)), tuple(s(x + 3, y - 4)), tuple(s(x + 6, y))], fill=COFFEE_LIGHT)
                bean(d, 80, y + 38, 11, 15, 25)
            elif j == 1:  # milk carton, a bit from the side: gable roof, ridge fin, blue band with a drop
                line = {"fill": MILK, "outline": COFFEE_DARK, "width": 2 * SS}
                d.polygon([tuple(s(130, y + 24)), tuple(s(142, y + 16)), tuple(s(142, y + 104)), tuple(s(130, y + 110))],
                          **{**line, "fill": CREAM_DARK})
                d.polygon([tuple(s(130, y + 24)), tuple(s(142, y + 16)), tuple(s(136, y + 6))], **{**line, "fill": CREAM_DARK})
                d.polygon([tuple(s(104, y + 24)), tuple(s(130, y + 24)), tuple(s(136, y + 6)), tuple(s(110, y + 6))], **line)
                d.rectangle(s(110, y, 136, y + 6), **line)
                d.rectangle(s(104, y + 24, 130, y + 110), **line)
                d.rectangle(s(106, y + 36, 128, y + 66), fill=MILK_BLUE)
                d.polygon([tuple(s(117, y + 40)), tuple(s(111, y + 53)), tuple(s(123, y + 53))], fill=MILK)
                d.ellipse(s(111, y + 47, 123, y + 59), fill=MILK)
            else:  # a cookie, leaning on the carton
                d.ellipse(s(140, y, 170, y + 30), fill=ACCENT, outline=COFFEE, width=2 * SS)
                for cx, cy in ((149, 9), (159, 14), (151, 21), (161, 23)):
                    d.ellipse(s(cx - 2.5, y + cy - 2.5, cx + 2.5, y + cy + 2.5), fill=COFFEE_DARK)
        # the bag, in front of what dropped in
        d.polygon([tuple(s(50, 122)), tuple(s(170, 122)), tuple(s(160, 188)), tuple(s(60, 188))], fill=COFFEE)
        # the tick once everything is in
        k = ease((t - 0.7) / 0.15)
        if k > 0:
            d.ellipse(s(86, 130, 134, 178), fill=SUCCESS)
            d.line([tuple(s(99, 154)), tuple(s(108, 163)), tuple(s(99 + 24 * k, 163 - 20 * k))],
                   fill=CREAM, width=6 * SS, joint="curve")
        frames.append(finish(img))
    return frames


if __name__ == "__main__":
    save("wait_order.gif", order_frames(), 80)
    save("wait_info.gif", info_frames(), 80)
    save("wait_settings.gif", settings_frames(), 60)
    save("wait_purchase.gif", purchase_frames(), 80)
