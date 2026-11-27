"""Draws static/machines/<family>.svg - one plain front view per JURA product line, shown wherever the
coffee machine is (dashboard, kiosk, portal: coffee/machine_image.py).

Our own drawings, not JURA's product photos: those are theirs, and the device is offline anyway. Each is
built from the same parts (body, bean lid, display, rotary selector, outlet, drip tray), so they look like
one set and a line is told apart by its proportions and what it has - a GIGA two hoppers and two outlets,
a Z its aluminium front and rotary selector, an X its tall professional build. No text in them: they show
in both languages. An admin's own photo replaces the drawing (dashboard -> Coffee machine).

    python3 scripts/make_machine_images.py      # rewrites every file; the output is committed
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "machines"
W, H, FLOOR = 200, 240, 226

BODY, EDGE, SILVER, CHROME = "#34373c", "#1f2125", "#c9cdd3", "#a3a9b1"
SCREEN, GLOW, RECESS, TRAY = "#0f1113", "#7cc8ff", "#1b1d20", "#26282c"

# width, body height, corner radius, front ("dark" or "silver"), hoppers, display (w, h), rotary,
# outlets, milk spout, display row from the top (share of the height)
FAMILIES = {
    "generic": dict(w=130, h=170, r=12, front="dark", hoppers=1, display=(44, 18), rotary=False, outlets=1, milk=False),
    "ena": dict(w=104, h=180, r=22, front="dark", hoppers=1, display=(36, 16), rotary=False, outlets=1, milk=False, dome=True),
    "c": dict(w=132, h=166, r=10, front="silver", hoppers=1, display=(40, 16), rotary=True, outlets=1, milk=False),
    "d": dict(w=122, h=160, r=10, front="dark", hoppers=1, display=(30, 14), rotary=True, outlets=1, milk=False),
    "e": dict(w=136, h=172, r=10, front="dark", hoppers=1, display=(52, 18), rotary=False, outlets=1, milk=True),
    "we": dict(w=136, h=178, r=6, front="dark", hoppers=1, display=(56, 20), rotary=False, outlets=1, milk=True, lock=True),
    "j": dict(w=146, h=186, r=26, front="silver", hoppers=1, display=(46, 18), rotary=True, outlets=1, milk=True),
    "s": dict(w=144, h=180, r=30, front="dark", hoppers=1, display=(60, 30), rotary=False, outlets=1, milk=True),
    "z": dict(w=160, h=192, r=12, front="silver", hoppers=1, display=(64, 26), rotary=True, outlets=1, milk=True),
    "giga": dict(w=184, h=190, r=12, front="silver", hoppers=2, display=(70, 26), rotary=True, outlets=2, milk=True),
    "giga_x": dict(w=184, h=204, r=8, front="dark", hoppers=2, display=(78, 34), rotary=False, outlets=2, milk=True,
                   lock=True),
    "w": dict(w=140, h=196, r=6, front="dark", hoppers=1, display=(60, 30), rotary=False, outlets=1, milk=True, lock=True),
    "x": dict(w=158, h=210, r=6, front="dark", hoppers=2, display=(74, 40), rotary=False, outlets=1, milk=True,
              lock=True),
}


def rect(x, y, w, h, fill, rx=0, extra=""):
    return f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx:g}" fill="{fill}"{extra}/>'


def draw(w, h, r, front, hoppers, display, rotary, outlets, milk, dome=False, lock=False):
    x0, y0 = (W - w) / 2, FLOOR - h
    parts = [f'<ellipse cx="{W / 2:g}" cy="{FLOOR + 4:g}" rx="{w / 2 + 12:g}" ry="6" fill="#000" opacity=".18"/>']
    # bean hoppers: a lid on top, or a dome (ENA)
    lid_w = (w - 24) / hoppers - (6 if hoppers > 1 else 0)
    for i in range(hoppers):
        lx = x0 + 12 + i * (lid_w + 12)
        if dome:
            parts.append(f'<ellipse cx="{lx + lid_w / 2:g}" cy="{y0 + 2:g}" rx="{lid_w / 2:g}" ry="12" fill="{EDGE}"/>')
            parts.append(f'<ellipse cx="{lx + lid_w / 2:g}" cy="{y0:g}" rx="{lid_w / 2 - 6:g}" ry="7" fill="{CHROME}" opacity=".5"/>')
        else:
            parts.append(rect(lx, y0 - 9, lid_w, 14, EDGE, 6))
            parts.append(rect(lx + 6, y0 - 6, lid_w - 12, 4, CHROME, 2, ' opacity=".45"'))
            if lock:
                parts.append(f'<circle cx="{lx + lid_w - 10:g}" cy="{y0 - 2:g}" r="2.5" fill="{CHROME}"/>')
    parts.append(rect(x0, y0, w, h, BODY, r, f' stroke="{EDGE}" stroke-width="2"'))
    parts.append(rect(x0 + 5, y0 + r / 2 + 4, 5, h - r - 30, "#fff", 2.5, ' opacity=".10"'))  # gloss
    # the upper front: aluminium or the body's own colour, with the display and the controls
    top_h = h * 0.40
    if front == "silver":
        parts.append(rect(x0 + 10, y0 + 10, w - 20, top_h, SILVER, max(4, r / 2)))
    dw, dh = display
    dx, dy = W / 2 - dw / 2 - (14 if rotary else 0), y0 + 10 + top_h / 2 - dh / 2
    parts.append(rect(dx, dy, dw, dh, SCREEN, 3, f' stroke="{EDGE}" stroke-width="1.5"'))
    for row in range(min(3, int(dh // 9))):  # a few lines of light on the screen
        parts.append(rect(dx + 6, dy + 5 + row * 8, dw * (0.6 if row else 0.75) - 6, 2.5, GLOW, 1.2,
                          f' opacity="{0.9 - row * 0.25:.2f}"'))
    if rotary:
        cx, cy = dx + dw + 20, dy + dh / 2
        parts.append(f'<circle cx="{cx:g}" cy="{cy:g}" r="11" fill="{CHROME}" stroke="{EDGE}" stroke-width="1.5"/>')
        parts.append(f'<circle cx="{cx:g}" cy="{cy:g}" r="6" fill="{BODY}" opacity=".35"/>')
    else:  # a row of keys under the screen
        for k in range(4):
            parts.append(f'<circle cx="{W / 2 - 18 + k * 12:g}" cy="{dy + dh + 8:g}" r="2.6" fill="{CHROME}" opacity=".8"/>')
    # the space for the cup, with the outlet(s) at its top and the drip tray at its bottom
    ry, rh = y0 + 16 + top_h, h - top_h - 38
    parts.append(rect(x0 + 14, ry, w - 28, rh, RECESS, 8))
    span = (w - 28) / outlets
    for i in range(outlets):
        ox = x0 + 14 + span * (i + 0.5) - (8 if milk and outlets == 1 else 0)
        parts.append(rect(ox - 16, ry, 32, 13, CHROME, 3))
        parts.append(rect(ox - 9, ry + 13, 5, 6, CHROME, 1.5))
        parts.append(rect(ox + 4, ry + 13, 5, 6, CHROME, 1.5))
        if milk and (outlets == 1 or i == outlets - 1):
            parts.append(rect(ox + 19, ry + 2, 4, 20, CHROME, 2))
    ty = FLOOR - 24
    parts.append(rect(x0 + 10, ty, w - 20, 16, TRAY, 4, f' stroke="{EDGE}" stroke-width="1.5"'))
    slots = int((w - 36) // 9)
    for s in range(slots):
        parts.append(rect(x0 + 18 + s * 9, ty + 5, 5, 6, "#3d4147", 1))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            + "".join(parts) + "</svg>\n")


def main():
    OUT.mkdir(exist_ok=True)
    for name, params in FAMILIES.items():
        (OUT / f"{name}.svg").write_text(draw(**params))
    print(f"{len(FAMILIES)} drawings in {OUT}")


if __name__ == "__main__":
    main()
