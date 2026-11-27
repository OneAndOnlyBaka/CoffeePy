"""Cuts the product video: 3D shots (render/<shot>/), screen recordings (clips/) and title cards.

    python3 compose.py segments      every segment as a lossless clip in edit/
    python3 compose.py only NAME     one segment
    python3 compose.py final         crossfades them into out/coffeepy.mp4 (+ the AV1 one)
    python3 compose.py youtube       out/coffeepy-youtube.mov from the same cut - ProRes, 1440p, PCM
"""
import os
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import REPO, WORK  # noqa: E402

S = str(WORK)
W, H, FPS = 1920, 1080, 30
FADE = 0.5
FONT = "/usr/share/fonts/google-noto/NotoSans-{}.ttf"
CREAM, CARAMEL, BROWN = (245, 237, 228), (214, 156, 86), (102, 51, 0)
LOGO = str(REPO / "docs" / "pictures" / "coffeepy.png")
# CC0 - see music/MUSIC.txt. From the top: its quiet first 12s carry title and hero, the beat comes in with the
# explode shot; faded out with the end card.
MUSIC, MUSIC_START = f"{S}/music/roof-tops.mp3", 0.0
os.makedirs(f"{S}/edit", exist_ok=True)
os.makedirs(f"{S}/out", exist_ok=True)


def font(weight, size):
    return ImageFont.truetype(FONT.format(weight), size)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    return float(out)


def caption(name, title, sub=None, align="left", box=True):
    """A lower third on a soft dark band - transparent PNG, faded in and out by ffmpeg."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if box:
        band = Image.new("L", (W, H), 0)
        ImageDraw.Draw(band).rectangle((0, H - 320, W, H), fill=225)  # dark enough over the white housing
        band = band.filter(ImageFilter.GaussianBlur(90))
        shade = Image.new("RGBA", (W, H), (20, 11, 6, 255))
        shade.putalpha(band)
        img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    ft, fs = font("SemiBold", 58), font("Regular", 34)
    y = H - (190 if sub else 140)
    x = 120
    if align == "center":
        x = (W - d.textlength(title, font=ft)) / 2
    d.text((x, y), title, font=ft, fill=CREAM)
    if sub:
        xs = 120 if align == "left" else (W - d.textlength(sub, font=fs)) / 2
        d.text((xs, y + 80), sub, font=fs, fill=CARAMEL)
    path = f"{S}/edit/cap_{name}.png"
    img.save(path)
    return path


def backdrop():
    """The 2D segments' background: the 3D shots' dark coffee, a warm glow in the middle."""
    img = Image.new("RGB", (W, H), (22, 13, 8))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.15, -H * 0.2, W * 0.85, H * 0.9), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(260))
    img.paste(Image.new("RGB", (W, H), (78, 50, 32)), mask=glow)
    # A little grain: an 8-bit gradient this dark bands, and the encoder turns the bands into blocks.
    noise = Image.merge("RGB", [Image.effect_noise((W, H), 1.6)] * 3)
    return ImageChops.add(img, noise, offset=-128)


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, r, fill=fill)


def shadow(img, box, r, blur=40, alpha=150):
    m = Image.new("L", img.size, 0)
    x0, y0, x1, y1 = box
    ImageDraw.Draw(m).rounded_rectangle((x0, y0 + 18, x1, y1 + 18), r, fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    img.paste(Image.new("RGB", img.size, (0, 0, 0)), mask=m)


def segment_3d(name, shot, title, sub, then=None):
    """then: (seconds, title, sub) - a second caption taking over at that point (what the screen shows changed)."""
    frames = len(os.listdir(f"{S}/render/{shot}"))
    t = frames / FPS
    caps = [(0.7, then[0] - 0.4 if then else t - 1.3, caption(name, title, sub))]
    if then:
        caps.append((then[0], t - 1.3, caption(f"{name}2", then[1], then[2])))
    inputs, graph, last = [], [], "[0]"
    for i, (start, end, path) in enumerate(caps, 1):
        inputs += ["-loop", "1", "-t", f"{t}", "-i", path]
        graph.append(f"[{i}]format=rgba,fade=in:st={start}:d=0.6:alpha=1,fade=out:st={end}:d=0.6:alpha=1[c{i}];"
                     f"{last}[c{i}]overlay[o{i}]")
        last = f"[o{i}]"
    graph.append(f"{last}format=yuv444p[v]")
    ffmpeg("-framerate", str(FPS), "-i", f"{S}/render/{shot}/%04d.png", *inputs, "-filter_complex", ";".join(graph),
           "-map", "[v]", "-c:v", "ffv1", "-r", str(FPS), f"{S}/edit/{name}.mkv")


def segment_2d(name, clip, layout, title, sub, trim=None):
    """layout: (background RGB image, x, y, w, h) - the clip goes into that box."""
    bg, x, y, w, h = layout
    bgp = f"{S}/edit/bg_{name}.png"
    bg.save(bgp)
    cap = caption(name, title, sub, align="center", box=False)
    src = f"{S}/clips/{clip}.mkv"
    t = trim or duration(src)
    ffmpeg("-loop", "1", "-t", f"{t}", "-i", bgp, "-t", f"{t}", "-i", src, "-loop", "1", "-t", f"{t}", "-i", cap,
           "-filter_complex",
           f"[1]scale={w}:{h}:flags=lanczos,tpad=stop_mode=clone:stop_duration=5[s];[0][s]overlay={x}:{y}:shortest=1[a];"
           f"[2]format=rgba,fade=in:st=0.5:d=0.6:alpha=1,fade=out:st={t - 1.2}:d=0.6:alpha=1[c];[a][c]overlay,format=yuv444p[v]",
           "-map", "[v]", "-c:v", "ffv1", "-r", str(FPS), "-t", f"{t}", f"{S}/edit/{name}.mkv")


def device_layout():
    """The kiosk screen in a white bezel like the housing's, top centre; the caption below it."""
    img = backdrop()
    w, h = 1152, 675
    x, y, b = (W - w) // 2, 70, 34
    shadow(img, (x - b, y - b, x + w + b, y + h + b), 38)
    rounded(ImageDraw.Draw(img), (x - b, y - b, x + w + b, y + h + b), 38, (232, 230, 225))
    ImageDraw.Draw(img).rectangle((x - 4, y - 4, x + w + 4, y + h + 4), fill=(12, 12, 14))
    return img, x, y, w, h


def browser_layout():
    img = backdrop()
    w, h = 1120, 700
    x, y, bar = (W - w) // 2, 92, 40
    shadow(img, (x, y - bar, x + w, y + h), 14)
    d = ImageDraw.Draw(img)
    rounded(d, (x, y - bar, x + w, y + h + 14), 14, (238, 234, 228))
    for i, c in enumerate([(236, 106, 94), (244, 190, 79), (98, 197, 84)]):
        d.ellipse((x + 18 + i * 24, y - bar + 13, x + 32 + i * 24, y - bar + 27), fill=c)
    rounded(d, (x + 110, y - bar + 8, x + w - 110, y - 8), 8, (255, 255, 255))
    d.text((x + 130, y - bar + 10), "https://coffeepy.local/dashboard/", font=font("Regular", 17), fill=(120, 110, 100))
    return img, x, y, w, h


def phone_layout():
    """A phone on the left, the caption beside it - the portal is a phone page."""
    img = backdrop()
    h = 880
    w = round(h * 390 / 844 / 2) * 2
    x, y, b = 360, 100, 18
    shadow(img, (x - b, y - b, x + w + b, y + h + b), 64)
    d = ImageDraw.Draw(img)
    rounded(d, (x - b, y - b, x + w + b, y + h + b), 64, (18, 18, 20))
    tx = x + w + 220
    d.text((tx, 380), "Your account on your phone", font=font("SemiBold", 64), fill=CREAM)
    d.text((tx, 485), "Balance, statistics and payments", font=font("Regular", 38), fill=CARAMEL)
    d.text((tx, 540), "Linked by one QR code at the kiosk - no app, no password", font=font("Regular", 30),
           fill=(200, 190, 178))
    return img, x, y, w, h


def title_card(name, t, lines, logo_y=60):
    img = Image.new("RGB", (W, H), BROWN)
    logo = Image.open(LOGO).convert("RGB")
    logo = logo.resize((1400, 820), Image.LANCZOS)
    img.paste(logo, ((W - 1400) // 2, logo_y))
    d = ImageDraw.Draw(img)
    y = 900
    for text, f, color in lines:
        d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=color)
        y += 64
    p = f"{S}/edit/card_{name}.png"
    img.save(p)
    ffmpeg("-loop", "1", "-t", f"{t}", "-i", p, "-vf", "format=yuv444p", "-c:v", "ffv1", "-r", str(FPS), f"{S}/edit/{name}.mkv")


def end_card(t):
    """The last 3D shot, its device on the right - name and promise on the left."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((110, 250), "CoffeePy", font=font("Bold", 112), fill=CREAM)
    d.text((116, 400), "The coffee tally that runs itself.", font=font("Regular", 36), fill=CARAMEL)
    d.text((116, 456), "Open source · runs offline on a Raspberry Pi", font=font("Regular", 27), fill=(200, 190, 178))
    p = f"{S}/edit/cap_end.png"
    img.save(p)
    frames = len(os.listdir(f"{S}/render/end"))
    t = frames / FPS
    ffmpeg("-framerate", str(FPS), "-i", f"{S}/render/end/%04d.png", "-loop", "1", "-t", f"{t}", "-i", p,
           "-filter_complex", "[1]format=rgba,fade=in:st=0.5:d=0.8:alpha=1[c];[0][c]overlay,format=yuv444p,fade=out:st="
           f"{t - 0.8}:d=0.8[v]", "-map", "[v]", "-c:v", "ffv1", "-r", str(FPS), f"{S}/edit/end.mkv")


SEGMENTS = {
    "title": lambda: title_card("title", 4.0, [("The coffee tally for the office kitchen", font("Regular", 46), (225, 210, 190))], logo_y=40),
    "hero": lambda: segment_3d("hero", "hero", "Meet the new CoffeePy", "A 3D-printed kiosk for the coffee machine"),
    "explode": lambda: segment_3d("explode", "explode", "Everything inside",
                                  "Raspberry Pi 3 · 7″ touchscreen · NFC reader · real-time clock"),
    "order": lambda: segment_3d("order", "order", "Tap your card. Pick a coffee. Done.",
                                "Your favourite is preselected - and booked on its own after 20 seconds"),
    "pay": lambda: segment_2d("pay", "kiosk_pay", device_layout(), "Settle up right at the machine",
                              "Cash, PayPal or a bank transfer by QR code", trim=8.5),
    "inventory": lambda: segment_2d("inventory", "kiosk_inventory", device_layout(), "Bought coffee beans? Book them at the kiosk",
                                    "Purchases are credited to your balance - and stock-takes happen right there too", trim=12.5),
    "ranking": lambda: segment_3d("ranking", "ranking", "Rankings, if your team wants them",
                                  "All-time and this month - switched on by the admin",
                                  then=(9.0, "And a pillory for open tabs", "Whoever owes money, until they settle up - optional as well")),
    "portal": lambda: segment_2d("portal", "portal", phone_layout(), "", None, trim=12.0),
    "dashboard": lambda: segment_2d("dashboard", "dashboard", browser_layout(), "A dashboard for whoever runs the coffee fund",
                                    "Members, statistics, bookings, backups and updates", trim=10.0),
    "inventory_dash": lambda: segment_2d("inventory_dash", "dashboard_inventory", browser_layout(), "What a cup really costs",
                                         "Stock, spending per month and price suggestions from real purchases", trim=8.5),
    "end": lambda: end_card(5),
}
ORDER = list(SEGMENTS)


def final():
    parts = [f"{S}/edit/{n}.mkv" for n in ORDER]
    lens = [duration(p) for p in parts]
    inputs = sum((["-i", p] for p in parts), [])
    chain, last, offset = [], "[0]", 0.0
    for i in range(1, len(parts)):
        offset += lens[i - 1] - FADE
        out = f"[x{i}]"
        chain.append(f"{last}[{i}]xfade=transition=fade:duration={FADE}:offset={offset:.3f}{out}")
        last = out
    chain.append(f"{last}format=yuv420p[v]")
    ffmpeg(*inputs, "-filter_complex", ";".join(chain), "-map", "[v]", "-c:v", "ffv1", f"{S}/edit/_all.mkv")
    total = duration(f"{S}/edit/_all.mkv")
    audio = ["-ss", f"{MUSIC_START}", "-t", f"{total}", "-i", MUSIC]
    mix = ["-af", f"afade=in:d=0.4,afade=out:st={total - 3.0}:d=3.0,loudnorm=I=-16:TP=-1.5:LRA=11",
           "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    # H.264 for anything that plays it. Fedora's ffmpeg has no x264 and Mesa no H.264 VAAPI encoder, so OpenH264
    out = f"{S}/out/coffeepy.mp4"
    ffmpeg("-i", f"{S}/edit/_all.mkv", *audio, "-c:v", "libopenh264", "-rc_mode", "bitrate", "-b:v", "12M",
           "-maxrate", "18M", "-pix_fmt", "yuv420p", *mix, "-movflags", "+faststart", out)
    # OpenH264 ignores the bitrate asked for (~2 Mbit/s here) - the AV1 one is the quality copy, for uploads
    ffmpeg("-i", f"{S}/edit/_all.mkv", *audio, "-c:v", "libsvtav1", "-crf", "20", "-preset", "5",
           "-pix_fmt", "yuv420p10le", *mix, "-movflags", "+faststart", f"{S}/out/coffeepy-av1.mp4")
    subprocess.run(["cp", f"{S}/music/MUSIC.txt", f"{S}/out/"])
    print(out, f"{duration(out):.1f}s")


def youtube():
    """The upload for YouTube. It re-encodes whatever it gets, so it gets as much as possible: ProRes 422 HQ (in its
    list of formats; no x264 here, and OpenH264 stays at ~2 Mbit/s), uncompressed audio, and 1440p - from 1440p up
    YouTube serves VP9/AV1, also to 1080p viewers, where a 1080p upload mostly ends up as its weaker H.264."""
    total = duration(f"{S}/edit/_all.mkv")
    out = f"{S}/out/coffeepy-youtube.mov"  # ~4 GB
    ffmpeg("-i", f"{S}/edit/_all.mkv", "-ss", f"{MUSIC_START}", "-t", f"{total}", "-i", MUSIC,
           # tagged on the frames: prores_ks ignores -color_primaries & co., and an untagged file leaves YouTube guessing
           "-vf", "scale=2560:1440:flags=lanczos:out_color_matrix=bt709,format=yuv422p10le,"
                  "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
           "-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0",
           "-af", f"afade=in:d=0.4,afade=out:st={total - 3.0}:d=3.0,loudnorm=I=-14:TP=-1.0:LRA=11",
           "-map", "0:v", "-map", "1:a", "-c:a", "pcm_s24le", "-ar", "48000", "-shortest", out)
    print(out, f"{duration(out):.1f}s")


if __name__ == "__main__":
    if sys.argv[1] == "segments":
        for n in ORDER:
            SEGMENTS[n]()
            print("segment", n)
    elif sys.argv[1] == "only":
        SEGMENTS[sys.argv[2]]()
    elif sys.argv[1] == "youtube":
        youtube()
    else:
        final()
