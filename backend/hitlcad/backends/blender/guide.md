# Blender code contract (Blender {blender_version}, headless)

A design's code is `interfaces.py`, part files under `parts/`, `lib/` helpers and `assembly.py`.
hitlcad runs each in Blender's Python with `p` (parameter values) and `i` (interface values, as
attributes; see the hitlcad skill) and records what each reads. 1 Blender unit = 1 mm, Z up. Each
part file runs in a fresh, empty scene.

## Part files

`def build(p, i)` returns `{part name: (object or [objects], connectors)}` for exactly the parts
design.json assigns to the file, each part in its own frame (the way it is made; its lowest point at
Z=0). `connectors` maps each declared connector to a 4 × 4 `mathutils.Matrix` (or an empty, whose
world matrix is used), or a list of them for a pattern. A frame's origin sits where the mating
connector's origin will be, Z out of the part. Meshes are not B-rep: hitlcad checks connector frames
and placements, and reports hole and shaft sizes and fits as unverified.

```python
import bpy
from mathutils import Matrix


def build(p, i):
    grip = make_grip(p["length"], p["girth"])  # your bmesh code
    socket = Matrix.Translation((0, 0, 0)) @ Matrix.Rotation(3.14159, 4, "X")  # faces down
    return {"grip": (grip, {"socket": socket})}
```

## assembly.py

`def build(p, i)` returns `{part or component name: [Matrix, …]}`: one placement per instance.

Rules:
- Keep code self-contained: `bpy`, `bmesh`, `mathutils`, the standard library and numpy, plus `lib/`.
- Every dimension a human may want to tune must come from `p[...]` or `i.<value>`.
- Prefer building geometry with `bmesh` or `bpy.data` directly over `bpy.ops`, which depends on
  context. `bpy.ops.mesh.primitive_*_add` works in background mode and is fine for primitives.
- Modifiers are applied on export; keep them on the object (Subdivision, Solidify, Boolean,
  Bevel, Array, Mirror, Screw, Displace…). Use `object.modifiers.new(name, type)`.
- Aim for a closed (watertight) manifold mesh: use Solidify for shells, avoid zero-thickness faces,
  and remove doubles (`bmesh.ops.remove_doubles`).
- Seed any randomness from a parameter: `random.seed(p["seed"])`.
- Name every object for what it is (`obj.name = "ear_left"`): when the human clicks the model, you
  are told the object they hit.
- Keep triangle counts reasonable (< 500k).

Skeleton:

```python
import bmesh
import bpy
from mathutils import Matrix
from math import cos, pi, sin


def build(p, i):
    h, r, wall, twist = p["height"], p["radius"], p["wall"], p["twist_deg"]
    mesh = bpy.data.meshes.new("vase")
    bm = bmesh.new()
    rings, segs = 48, 64
    for ring in range(rings + 1):
        t = ring / rings
        radius = r * (0.8 + 0.3 * sin(t * pi))
        angle = twist * pi / 180 * t
        for j in range(segs):
            a = 2 * pi * j / segs + angle
            bm.verts.new((radius * cos(a), radius * sin(a), h * t))
    bm.verts.ensure_lookup_table()
    for ring in range(rings):
        for j in range(segs):
            a, b = ring * segs + j, ring * segs + (j + 1) % segs
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
    return {"vase": (obj, {"base": Matrix.Rotation(3.14159, 4, "X")})}
```

Useful API notes for this version:
- Link new objects with `bpy.context.scene.collection.objects.link(obj)`.
- Colours are materials' Principled BSDF Base Color, linear RGB as Blender's colour fields are (an
  sRGB value v is about v ** 2.2). Name each material for what it is:
  ```python
  mat = bpy.data.materials.new("collar")
  mat.use_nodes = True
  mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.85, 0.1, 0.15, 1)
  obj.data.materials.append(mat)
  ```
  An object may carry several (faces pick one by `material_index`). Colours carry into the 3D view
  (a part in several colours is drawn as one shape per colour), the 3MF files (one object per
  colour, for multi-colour printers) and `model.blend`.
- Curves: `bpy.data.curves.new(name, "CURVE")`, set `bevel_depth` for tubes; they are converted
  to meshes on export.
- Booleans: `m = obj.modifiers.new("cut", "BOOLEAN"); m.operation = "DIFFERENCE"; m.object = cutter;
  m.solver = "EXACT"`, then hide the cutter from the result by not returning it and setting
  `cutter.hide_render = True` and returning only the kept objects.

The build exports each part in its own frame (`build/parts/<part>.stl`, and `.glb` with the objects
by name and their materials), the placed assembly (`build/mesh.stl`), the 3D view, and
`build/model.blend`: the whole assembly as one Blender scene to open, tweak or render, each part's
objects in a collection of their own and each instance a collection instance at its placement
(nested for components), 1 unit = 1 mm, Z up.
