"""Render orthographic preview views of an STL with Blender Workbench (headless, no GPU needed).

Usage: blender -b --factory-startup -P blender_entry.py -- <mesh.stl> <size>   (cwd = output dir)
Writes view_<name>.png for each view and views.json describing them.
"""

import json
import os
import sys

import bpy
from mathutils import Vector

VIEWS = {
    # name: (camera direction from target, up vector)
    "iso": (Vector((1.0, -1.0, 0.8)), Vector((0, 0, 1))),
    "front": (Vector((0.0, -1.0, 0.0)), Vector((0, 0, 1))),
    "right": (Vector((1.0, 0.0, 0.0)), Vector((0, 0, 1))),
    "top": (Vector((0.0, 0.0, 1.0)), Vector((0, 1, 0))),
}


def look_at(cam, target, direction, up):
    forward = -direction.normalized()
    right = forward.cross(up).normalized()
    true_up = right.cross(forward)
    from mathutils import Matrix

    rot = Matrix((right, true_up, -forward)).transposed()
    cam.matrix_world = Matrix.Translation(target + direction) @ rot.to_4x4()


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :]
    stl, size = argv[0], int(argv[1])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.stl_import(filepath=stl)
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    corners = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    hi = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    center, extent = (lo + hi) / 2, hi - lo
    radius = max(extent.length / 2, 1e-3)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "SINGLE"
    shading.single_color = (0.72, 0.74, 0.78)
    shading.show_cavity = True
    shading.cavity_type = "BOTH"
    shading.show_object_outline = True
    shading.show_specular_highlight = True
    scene.display.render_aa = "8"
    world = bpy.data.worlds.new("bg")
    world.color = (0.96, 0.96, 0.95)
    scene.world = world

    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.clip_start, cam_data.clip_end = radius * 0.01, radius * 20
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    meta = {"center": list(center), "extent": list(extent), "views": []}
    for name, (direction, up) in VIEWS.items():
        look_at(cam, center, direction.normalized() * radius * 4, up)
        cam_data.ortho_scale = radius * 2.3
        scene.render.filepath = os.path.join(os.getcwd(), f"view_{name}.png")
        bpy.ops.render.render(write_still=True)
        meta["views"].append({"name": name, "file": f"view_{name}.png"})
    with open("views.json", "w") as f:
        json.dump(meta, f)


main()
