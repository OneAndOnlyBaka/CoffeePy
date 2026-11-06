"""Animates scene.blend for one shot and renders it.

    blender -b scene.blend -P shots.py -- SHOT stills F1,F2,...   half size, few samples, to look at
    blender -b scene.blend -P shots.py -- SHOT render             every frame, full size
"""
import bpy, math, sys, os
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import WORK  # noqa: E402

S = str(WORK)
args = sys.argv[sys.argv.index("--") + 1:]
SHOT, MODE = args[0], args[1]
sc = bpy.context.scene
TILT = math.radians(13.339)
N = Vector((0, -math.sin(TILT), math.cos(TILT)))
O = lambda n: bpy.data.objects[n]
CENTER = Vector((0.13, 0.045, 0.03))
SCREEN = O("Screen").location.copy()


def orbit(az, r, h, target=CENTER):
    a = math.radians(az)
    return target + Vector((r * math.cos(a), r * math.sin(a), h))


def key(obj, frame, loc=None, rot=None):
    if loc is not None:
        obj.location = loc
        obj.keyframe_insert("location", frame=frame)
    if rot is not None:
        obj.rotation_euler = rot
        obj.keyframe_insert("rotation_euler", frame=frame)


def camera(frames):
    """frames: [(frame, cam position, aim position)] - eased between."""
    for f, c, a in frames:
        key(O("Camera"), f, loc=c)
        key(O("Aim"), f, loc=a)


def screen(clip, offset=0, cyclic=True):
    folder = f"{S}/seq/{clip}"
    count = len(os.listdir(folder))
    # Blender adds the offset after wrapping a cyclic sequence: past the last file the screen renders magenta
    if cyclic and offset + length > count:
        raise SystemExit(f"{clip}: offset {offset} + {length} frames runs past its {count} images")
    img = bpy.data.images.load(f"{folder}/0001.png")
    img.source = 'SEQUENCE'
    tex = O("Screen").active_material.node_tree.nodes["ScreenTex"]
    tex.image = img
    u = tex.image_user
    u.frame_duration = count
    u.frame_start = 1
    u.frame_offset = offset
    u.use_cyclic = cyclic
    u.use_auto_refresh = True


def explode(f_out, f_hold, f_back, f_end):
    # Top down as it is assembled: frame, shell (the lid), display and NFC board under it, the Pi, the bottom.
    up = Vector((0, 0, 1))
    moves = {"top": N * 0.135, "shell": up * 0.052, "nfc": up * -0.018, "display": up * -0.018, "pi": up * -0.064,
             "bottom": up * -0.105, "all": up * 0.105}
    for g, d in moves.items():
        e = O(f"rig_{g}")
        for f, v in ((f_out, Vector()), (f_hold, d), (f_back, d), (f_end, Vector())):
            key(e, f, loc=v)


def badge():
    o = O("NFC_Badge")
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return sum(pts, Vector()) / 8, max(p.dot(N) for p in pts)


if SHOT == "hero":
    length = 240
    camera([(1, orbit(-128, 0.62, 0.30), CENTER), (length, orbit(-58, 0.60, 0.26), CENTER)])
    screen("kiosk_wait")
elif SHOT == "explode":
    length = 270
    camera([(1, CENTER + Vector((0.62, -0.95, 0.36)), CENTER + Vector((0, 0, 0.10))),
            (length, CENTER + Vector((0.38, -1.00, 0.28)), CENTER + Vector((0, 0, 0.11)))])
    explode(20, 85, 185, 250)
    screen("kiosk_wait", offset=90)
elif SHOT == "order":
    length = 330
    aim = (SCREEN + O("NFC_Badge").location) / 2
    c, top = badge()
    aim = Vector((0.165, 0.055, 0.035))
    camera([(1, aim + Vector((0.25, -0.56, 0.34)), aim), (length, aim + Vector((0.19, -0.47, 0.29)), aim)])
    card = O("Card")
    rest = c + N * (top - c.dot(N) + 0.004)
    rot = (TILT, 0, 0)
    away = rest + N * 0.14 + Vector((0.05, -0.10, 0.0))
    key(card, 1, loc=away + Vector((0.10, -0.05, 0)), rot=(TILT + 0.5, 0.2, -0.3))
    key(card, 40, loc=away, rot=(TILT + 0.35, 0.1, -0.15))
    key(card, 74, loc=rest, rot=rot)
    key(card, 104, loc=rest, rot=rot)
    key(card, 140, loc=away + Vector((0.12, -0.05, 0.02)), rot=(TILT + 0.4, 0.15, -0.25))
    screen("kiosk_order", cyclic=False)
elif SHOT == "ranking":
    length = 390  # all-time, this month and the pillory - the clip's tabs at 0.5s, 5.5s and 9.5s
    d = Vector((0.0, -0.62, 0.78)).normalized()
    camera([(1, SCREEN + d * 0.42 + Vector((-0.03, 0, 0)), SCREEN), (length, SCREEN + d * 0.30, SCREEN)])
    screen("kiosk_ranking", offset=20, cyclic=False)
elif SHOT == "end":
    length = 150
    # aimed left of the device, so it sits in the right half and the end card's text has the left
    left = CENTER + Vector((-0.09, 0.0, 0))
    camera([(1, orbit(-74, 0.84, 0.30), left), (length, orbit(-82, 0.80, 0.27), left)])
    screen("kiosk_wait", offset=40)

# the card only plays in the order shot - parked anywhere else, a wide camera finds it
for name in ("Card", "CardLabel"):
    O(name).hide_render = SHOT != "order"
for o in bpy.data.objects:
    if o.animation_data and o.animation_data.action:
        for fc in o.animation_data.action.fcurves:
            for k in fc.keyframe_points:
                k.interpolation = 'BEZIER'
                k.easing = 'AUTO'
sc.frame_start, sc.frame_end = 1, length
out = f"{S}/render/{SHOT}"
os.makedirs(out, exist_ok=True)
if MODE == "stills":
    sc.render.resolution_percentage = 50
    sc.eevee.taa_render_samples = 16
    for f in [int(x) for x in args[2].split(",")]:
        sc.frame_set(f)
        sc.render.filepath = f"{S}/render/still_{SHOT}_{f:04d}.png"
        bpy.ops.render.render(write_still=True)
else:
    sc.render.filepath = f"{out}/"
    sc.render.use_overwrite = False  # resumes where an interrupted run stopped
    bpy.ops.render.render(animation=True)
