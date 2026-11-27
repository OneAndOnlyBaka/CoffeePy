import bpy, os, sys
from mathutils import Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import MODEL, WORK  # noqa: E402

S = str(WORK)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=str(MODEL),
                      forward_axis='Y', up_axis='Z', global_scale=0.001)
for o in bpy.context.scene.objects:
    if o.type != 'MESH':
        continue
    bb = [o.matrix_world @ Vector(c) for c in o.bound_box]
    lo = [min(v[i] for v in bb) * 1000 for i in range(3)]
    hi = [max(v[i] for v in bb) * 1000 for i in range(3)]
    if not o.name.startswith(("M3", "M2", "Scheibe", "Passstift", "Kabel")):
        print(f"{o.name:28s} {len(o.data.polygons):7d}  lo {lo[0]:7.1f} {lo[1]:7.1f} {lo[2]:7.1f}  hi {hi[0]:7.1f} {hi[1]:7.1f} {hi[2]:7.1f}")
os.makedirs(f"{S}/3d", exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=f"{S}/3d/model.blend")
