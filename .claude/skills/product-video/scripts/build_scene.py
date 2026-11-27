"""model.blend (the plain OBJ import) -> scene.blend: materials, studio, screen, NFC card, explode rigs."""
import bpy, math, sys, os
from mathutils import Vector, Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import WORK  # noqa: E402

S = str(WORK)
mm = 0.001
TILT = math.radians(13.339)
N = Vector((0, -math.sin(TILT), math.cos(TILT)))  # display normal
V = Vector((0, math.cos(TILT), math.sin(TILT)))   # display "up" in the image
sc = bpy.context.scene


def principled(name, color, rough=0.4, metal=0.0, coat=0.0, emission=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    b.inputs["Coat Weight"].default_value = coat
    return m


abs_white = principled("ABS", (0.80, 0.79, 0.76), rough=0.38)
black = principled("Black", (0.012, 0.011, 0.010), rough=0.55)
steel = principled("Steel", (0.75, 0.75, 0.77), rough=0.28, metal=1.0)
nylon = principled("Nylon", (0.82, 0.80, 0.75), rough=0.5)
glass_black = principled("DisplayBody", (0.01, 0.01, 0.012), rough=0.2)


def assign(o, mat):
    o.data.materials.clear()
    o.data.materials.append(mat)


for o in list(sc.objects):
    n = o.name
    if o.type != 'MESH':
        continue
    if n.startswith(("LCD_Schale", "Card_Schale", "LCD_Rand", "Card_Rand", "LCD_Boden", "Card_Boden")):
        assign(o, abs_white)
    elif n.startswith(("Logo", "NFC_Badge")):
        assign(o, black)
    elif n.startswith(("M3", "Scheibe_M3", "Passstift")):
        assign(o, steel)
    elif n.startswith(("M2.5", "Scheibe_M2.5")):
        assign(o, nylon)
    elif n == "1118":
        assign(o, glass_black)
    else:
        for slot in o.material_slots:
            if slot.material and slot.material.use_nodes:
                b = slot.material.node_tree.nodes.get("Principled BSDF")
                if b:
                    b.inputs["Roughness"].default_value = 0.45
    for p in o.data.polygons:
        p.use_smooth = False
    if n.startswith(("Kabel", "NFC_Kabel", "M3", "M2.5", "Passstift", "Scheibe")):
        for p in o.data.polygons:
            p.use_smooth = True

# --- the screen: an emissive plane on the display glass, its image set per shot
center = 95.6 * mm * Vector((1, 0, 0)) + 68.0 * mm * V + 27.08 * mm * N
bpy.ops.mesh.primitive_plane_add(size=1, location=center, rotation=(TILT, 0, 0))
screen = bpy.context.active_object
screen.name = "Screen"
screen.scale = (154.2 * mm, 86.0 * mm, 1)
sm = bpy.data.materials.new("ScreenMat")
sm.use_nodes = True
nt = sm.node_tree
b = nt.nodes["Principled BSDF"]
b.inputs["Base Color"].default_value = (0, 0, 0, 1)
b.inputs["Roughness"].default_value = 0.04
b.inputs["Emission Strength"].default_value = 1.0
tex = nt.nodes.new("ShaderNodeTexImage")
tex.name = "ScreenTex"
tex.interpolation = 'Cubic'
nt.links.new(tex.outputs["Color"], b.inputs["Emission Color"])
assign(screen, sm)

# --- NFC card (ISO ID-1), coffee brown with the name on it
bpy.ops.mesh.primitive_cube_add(size=1)
card = bpy.context.active_object
card.name = "Card"
card.scale = (85.6 * mm, 54 * mm, 0.76 * mm)
bpy.ops.object.transform_apply(scale=True)
bev = card.modifiers.new("bevel", 'BEVEL')
bev.width = 3.2 * mm; bev.segments = 8; bev.limit_method = 'ANGLE'
assign(card, principled("CardMat", (0.16, 0.07, 0.025), rough=0.25, coat=0.6))
bpy.ops.object.text_add(location=(-36 * mm, -18 * mm, 0.40 * mm))
label = bpy.context.active_object
label.name = "CardLabel"
label.data.body = "CoffeePy"
label.data.size = 9 * mm
label.data.extrude = 0.05 * mm
assign(label, principled("CardText", (0.93, 0.85, 0.72), rough=0.4))
label.parent = card
card.location = (0.6, -0.6, 0.3)  # parked out of view; shots move it

# --- explode rigs: an empty per group, parts parented to it
groups = {
    "shell": lambda n: n.startswith(("LCD_Schale", "Card_Schale", "Passstift")),
    "top": lambda n: n.startswith(("LCD_Rand", "Card_Rand", "Logo", "NFC_Badge")),
    "nfc": lambda n: (n.startswith("NFC_") and n != "NFC_Badge") or "_NFC" in n,
    "display": lambda n: n in ("1118", "Screen") or "_Display" in n,
    "pi": lambda n: n.startswith(("Raspberry", "RTC_", "Dupont", "Kabel_", "USB_C", "M2.5x6_Nylonschraube", "M2.5_Nylonmutter")),
    "bottom": lambda n: n.startswith(("LCD_Boden", "Card_Boden", "M3x8_Senkschraube", "M3_Mutter")),
}
for g, test in groups.items():
    e = bpy.data.objects.new(f"rig_{g}", None)
    sc.collection.objects.link(e)
    for o in sc.objects:
        if o.type == 'MESH' and o.parent is None and test(o.name):
            o.parent = e
            o.matrix_parent_inverse = Matrix()

# Everything rides on rig_all, which lifts the device off the floor while it is apart.
lift = bpy.data.objects.new("rig_all", None)
sc.collection.objects.link(lift)
for o in sc.objects:
    if o.parent is None and (o.type == 'MESH' or o.name.startswith("rig_")) and o is not lift and o.name != "Card":
        o.parent = lift
        o.matrix_parent_inverse = Matrix()

# --- studio: dark coffee floor, softboxes, studio HDRI for reflections
bpy.ops.mesh.primitive_plane_add(size=12, location=(0.13, 0.05, -0.0002))
floor = bpy.context.active_object
floor.name = "Floor"
assign(floor, principled("FloorMat", (0.045, 0.028, 0.018), rough=0.32))

world = bpy.data.worlds.new("World")
sc.world = world
world.use_nodes = True
wn = world.node_tree
env = wn.nodes.new("ShaderNodeTexEnvironment")
hdri = os.path.join(os.path.dirname(bpy.app.binary_path), "4.5", "datafiles", "studiolights", "world", "studio.exr")
env.image = bpy.data.images.load(hdri)
wn.links.new(env.outputs["Color"], wn.nodes["Background"].inputs["Color"])
wn.nodes["Background"].inputs["Strength"].default_value = 0.06
# The camera sees a dark coffee backdrop past the floor's edge, not the HDRI's softboxes - reflections keep them.
dark = wn.nodes.new("ShaderNodeBackground")
dark.inputs["Color"].default_value = (0.012, 0.007, 0.004, 1)
path = wn.nodes.new("ShaderNodeLightPath")
mix = wn.nodes.new("ShaderNodeMixShader")
wn.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
wn.links.new(wn.nodes["Background"].outputs["Background"], mix.inputs[1])
wn.links.new(dark.outputs["Background"], mix.inputs[2])
wn.links.new(mix.outputs["Shader"], wn.nodes["World Output"].inputs["Surface"])


def area(name, loc, size, energy, color=(1, 1, 1), target=(0.13, 0.045, 0.03)):
    L = bpy.data.lights.new(name, 'AREA')
    L.shape = 'RECTANGLE'; L.size = size[0]; L.size_y = size[1]
    L.energy = energy; L.color = color
    o = bpy.data.objects.new(name, L)
    sc.collection.objects.link(o)
    o.location = loc
    d = Vector(target) - Vector(loc)
    o.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    return o


area("Key", (-0.45, -0.55, 0.75), (0.9, 0.6), 22, (1.0, 0.95, 0.88))
area("Fill", (0.85, -0.45, 0.35), (0.6, 0.6), 8, (0.85, 0.9, 1.0))
area("Rim", (0.2, 0.9, 0.55), (1.2, 0.3), 25, (1.0, 0.85, 0.7))

cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.lens = 50
cam.data.sensor_width = 36
aim = bpy.data.objects.new("Aim", None)
sc.collection.objects.link(aim)
t = cam.constraints.new('TRACK_TO')
t.target = aim; t.track_axis = 'TRACK_NEGATIVE_Z'; t.up_axis = 'UP_Y'

sc.render.engine = 'BLENDER_EEVEE_NEXT'
ee = sc.eevee
ee.taa_render_samples = 48
ee.use_raytracing = True
ee.ray_tracing_options.resolution_scale = '1'
ee.use_shadows = True
ee.fast_gi_method = 'GLOBAL_ILLUMINATION'
# Standard, not AgX: the screen has to show the kiosk's own colours, not a filmic version of them.
sc.view_settings.view_transform = 'Standard'
sc.view_settings.look = 'Medium High Contrast'
sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
sc.render.fps = 30
sc.render.image_settings.file_format = 'PNG'
# saved beside, then moved: a render may have the old one open
bpy.ops.wm.save_as_mainfile(filepath=f"{S}/3d/scene.new.blend")
os.replace(f"{S}/3d/scene.new.blend", f"{S}/3d/scene.blend")
print("saved scene")
