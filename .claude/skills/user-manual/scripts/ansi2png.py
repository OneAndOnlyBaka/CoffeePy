"""Renders a `tmux capture-pane -e -p` dump (24-bit colour escapes) to a PNG - how the
provisioning tool's screens get into the manual. Needs Pillow (see capture_tui.sh).

    python3 ansi2png.py pane.txt out.png [columns]
"""
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont


def font(style):
    path = subprocess.run(["fc-match", "-f", "%{file}", f"monospace:{style}"], capture_output=True, text=True).stdout
    return ImageFont.truetype(path, 16)


def render(src, out, columns=100):
    lines = open(src, encoding="utf-8").read().rstrip("\n").split("\n")
    cw, ch = 10, 22
    fonts = {False: font("regular"), True: font("bold")}
    img = Image.new("RGB", (columns * cw, len(lines) * ch))
    draw = ImageDraw.Draw(img)
    default_fg, default_bg = (220, 220, 220), (20, 20, 20)
    fg, bg, bold = default_fg, default_bg, False
    for y, line in enumerate(lines):
        x = 0
        for token in re.split(r"(\x1b\[[0-9;]*m)", line):
            if token.startswith("\x1b["):
                codes = [int(c) if c else 0 for c in token[2:-1].split(";")]
                i = 0
                while i < len(codes):
                    c = codes[i]
                    if c == 0:
                        fg, bg, bold = default_fg, default_bg, False
                    elif c == 1:
                        bold = True
                    elif c == 22:
                        bold = False
                    elif c in (38, 48) and i + 4 < len(codes) and codes[i + 1] == 2:
                        colour = tuple(codes[i + 2:i + 5])
                        i += 4
                        if c == 38:
                            fg = colour
                        else:
                            bg = colour
                    elif c == 39:
                        fg = default_fg
                    elif c == 49:
                        bg = default_bg
                    i += 1
                continue
            for char in token:
                draw.rectangle([x * cw, y * ch, x * cw + cw, y * ch + ch], fill=bg)
                draw.text((x * cw, y * ch + 2), char, font=fonts[bold], fill=fg)
                x += 1
        # tmux leaves out trailing cells in the default colour - fill the row with the last one
        draw.rectangle([x * cw, y * ch, columns * cw, y * ch + ch], fill=bg)
    img.save(out)


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 100)
