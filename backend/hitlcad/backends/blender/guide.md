# Blender authoring contract (Blender {blender_version}, headless)

Write one Python module that defines `build(p)`. `p` is a dict of parameter values keyed by
parameter name. The scene starts empty with 1 Blender unit = 1 mm, Z up. Create objects, then
return them (an Object or list of Objects), or return `None` to export every mesh/curve object
in the scene. Cameras and lights are discarded. Rest the model on the XY plane (lowest point at
Z=0) unless the brief says otherwise.

Rules:
- Keep `model.py` self-contained: `bpy`, `bmesh`, `mathutils`, the standard library and numpy.
  It runs in a fresh Blender process with the output folder as its working directory.
- Every dimension a human may want to tune must come from `p[...]`.
- Prefer building geometry with `bmesh` or `bpy.data` directly over `bpy.ops`, which depends on
  context. `bpy.ops.mesh.primitive_*_add` works in background mode and is fine for primitives.
- Modifiers are applied on export; keep them on the object (Subdivision, Solidify, Boolean,
  Bevel, Array, Mirror, Screw, Displace…). Use `object.modifiers.new(name, type)`.
- Aim for a closed (watertight) manifold mesh if the object could be 3D printed: use Solidify
  for shells, avoid zero-thickness faces, and remove doubles (`bmesh.ops.remove_doubles`).
- Seed any randomness from a parameter: `random.seed(p["seed"])`.
- Name every object for what it is (`obj.name = "ear_left"`): when the human clicks or pins the
  model, you are told the name of the object they hit.
- Keep triangle counts reasonable (< 500k).

Skeleton:

```python
import bmesh
import bpy
from math import cos, pi, sin


def build(p):
    h, r, wall, twist = p["height"], p["radius"], p["wall"], p["twist_deg"]
    mesh = bpy.data.meshes.new("vase")
    bm = bmesh.new()
    rings, segs = 48, 64
    for i in range(rings + 1):
        t = i / rings
        radius = r * (0.8 + 0.3 * sin(t * pi))
        angle = twist * pi / 180 * t
        for j in range(segs):
            a = 2 * pi * j / segs + angle
            bm.verts.new((radius * cos(a), radius * sin(a), h * t))
    bm.verts.ensure_lookup_table()
    for i in range(rings):
        for j in range(segs):
            a, b = i * segs + j, i * segs + (j + 1) % segs
            bm.faces.new([bm.verts[a], bm.verts[b], bm.verts[b + segs], bm.verts[a + segs]])
    bottom = bm.faces.new([bm.verts[j] for j in reversed(range(segs))])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("vase", mesh)
    bpy.context.scene.collection.objects.link(obj)
    solid = obj.modifiers.new("wall", "SOLIDIFY")
    solid.thickness = wall
    solid.offset = -1
    return obj
```

Useful API notes for this version:
- Link new objects with `bpy.context.scene.collection.objects.link(obj)`.
- Materials: `mat = bpy.data.materials.new("m"); mat.diffuse_color = (r, g, b, 1)`;
  `obj.data.materials.append(mat)`. Colours carry into the viewer.
- Curves: `bpy.data.curves.new(name, "CURVE")`, set `bevel_depth` for tubes; they are converted
  to meshes on export.
- Booleans: `m = obj.modifiers.new("cut", "BOOLEAN"); m.operation = "DIFFERENCE"; m.object = cutter;
  m.solver = "EXACT"`, then hide the cutter from the result by not returning it and setting
  `cutter.hide_render = True` and returning only the kept objects.

- Assemblies: when the design is several parts, return a dict `{"body": body, "lid": [lid, knob]}`
  (a part is one object or a list of objects), each placed where it sits in the assembly. Names
  use letters, digits, `.`, `_` or `-`. Every part gets its own STL and overlaps between parts are
  reported. Without a dict, all returned objects are one part.

The build produces `mesh.stl` (mm), `model.blend` and `objects.glb` (the objects by name) (plus
`part-<name>.stl` per part). You receive the
measured bounding box, volume, watertightness and body count after each build, plus preview
renders when your model supports images.
