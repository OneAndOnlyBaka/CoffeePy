"""Writes the kiosk's nav bar icons as SVG into static/kiosk/img/nav/.

    python3 scripts/make_nav_icons.py

Stdlib only. Same motifs and palette as the "apply your card" animations
(make_wait_gifs.py), so the bar and the screens next to it read as one design: flat cream
shapes with ochre details, legible on the bar's dark brown and on the lighter brown of
the active button alike. The SVGs are committed; this only has to run again to change them.
"""
import math
import os

CREAM = "#f7efe6"
ACCENT = "#c98a3d"
LIGHT = "#a9744f"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "kiosk", "img", "nav")


def svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
            f"{body}\n</svg>\n")


def gear_path(cx, cy, radius, teeth, angle=0.0, hole=0.34):
    """Outline with a hole (evenodd), so whatever is behind the button shows through -
    the bar's brown, or the active button's lighter one."""
    depth = radius * 0.24
    steps = teeth * 4
    points = []
    for k in range(steps):
        a = angle + 2 * math.pi * k / steps
        r = radius if k % 4 in (0, 1) else radius - depth
        points.append(f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}")
    h = radius * hole
    return (f'M{" L".join(points)} Z '
            f"M{cx + h:.2f},{cy:.2f} A{h:.2f},{h:.2f} 0 1,0 {cx - h:.2f},{cy:.2f} "
            f"A{h:.2f},{h:.2f} 0 1,0 {cx + h:.2f},{cy:.2f} Z")


def order():
    steam = "".join(
        f'<path d="M{x},36 c-5,-5 5,-9 0,-14 c-5,-5 5,-9 0,-14" fill="none" stroke="{ACCENT}" '
        f'stroke-width="4" stroke-linecap="round"/>'
        for x in (34, 46, 58)
    )
    return svg(
        f'  <ellipse cx="46" cy="88" rx="36" ry="5" fill="{LIGHT}"/>\n'
        f'  <path d="M68,52 a11,11 0 0 1 0,22" fill="none" stroke="{CREAM}" stroke-width="7"/>\n'
        f'  <rect x="20" y="42" width="52" height="42" rx="10" fill="{CREAM}"/>\n'
        f'  <rect x="26" y="47" width="40" height="8" rx="4" fill="{ACCENT}"/>\n'
        f"  {steam}"
    )


def info():
    coins = "".join(
        f'<ellipse cx="28" cy="{y}" rx="20" ry="7" fill="{ACCENT}" stroke="{CREAM}" stroke-width="2.5"/>'
        for y in (82, 71, 60)
    )
    bars = "".join(
        f'<rect x="{x}" y="{86 - h}" width="11" height="{h}" rx="3" fill="{color}"/>'
        for x, h, color in ((56, 28, CREAM), (70, 44, CREAM), (84, 62, ACCENT))
    )
    return svg(
        f'  <rect x="6" y="88" width="90" height="4" rx="2" fill="{LIGHT}"/>\n'
        f"  {bars}\n  {coins}"
    )


def settings():
    # Meshing pair, like the settings animation: distance and phase put a tooth of each
    # into a gap of the other.
    big_r, small_r, depth = 30, 19, 0.24
    distance = big_r + small_r - (big_r + small_r) * depth / 2
    line = -math.pi / 4
    big_c = (40, 60)
    small_c = (big_c[0] + distance * math.cos(line), big_c[1] + distance * math.sin(line))
    big_step, small_step = 2 * math.pi / 48, 2 * math.pi / 32
    return svg(
        f'  <path fill-rule="evenodd" fill="{CREAM}" d="{gear_path(*big_c, big_r, 12, line - 0.5 * big_step)}"/>\n'
        f'  <path fill-rule="evenodd" fill="{ACCENT}" '
        f'd="{gear_path(*small_c, small_r, 8, line + math.pi - 2.5 * small_step)}"/>'
    )


def ranking():
    star = []
    for k in range(10):
        r = 9 if k % 2 == 0 else 4
        a = -math.pi / 2 + k * math.pi / 5
        star.append(f"{50 + r * math.cos(a):.2f},{35 + r * math.sin(a):.2f}")
    return svg(
        f'  <path d="M28,22 a13,13 0 0 0 4,24 M72,22 a13,13 0 0 1 -4,24" fill="none" stroke="{CREAM}" '
        f'stroke-width="6" stroke-linecap="round"/>\n'
        f'  <path d="M28,12 H72 V34 a22,22 0 0 1 -44,0 Z" fill="{CREAM}"/>\n'
        f'  <polygon points="{" ".join(star)}" fill="{ACCENT}"/>\n'
        f'  <rect x="45" y="55" width="10" height="14" fill="{CREAM}"/>\n'
        f'  <rect x="32" y="68" width="36" height="9" rx="2" fill="{ACCENT}"/>\n'
        f'  <rect x="26" y="77" width="48" height="10" rx="3" fill="{CREAM}"/>'
    )


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, make in (("order", order), ("info", info), ("settings", settings), ("ranking", ranking)):
        path = os.path.join(OUT, f"{name}.svg")
        with open(path, "w") as f:
            f.write(make())
        print(f"{path}: {os.path.getsize(path)} bytes")
