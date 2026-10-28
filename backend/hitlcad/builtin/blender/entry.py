"""Blender's build worker. Runs inside Blender's bundled Python, which cannot import hitlcad: it loads the
model runtime from hitlcad's execution folder, and serves jobs (`runtime.serve`) with the adapter below.

Usage: blender -b --factory-startup -P entry.py   (it reads its jobs' paths on stdin)

Scene convention: 1 Blender unit = 1 mm, Z up. Each part file runs in a fresh, empty scene; the assembly
(`model.blend`) is put together in one more, from the built parts' files.
"""

import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "execution"))
import runtime  # noqa: E402
from runtime import ModelError  # noqa: E402


def _load_sdk():
    """`hitlcad.sdk` for model code, as build123d code imports it. It imports nothing, so it loads on its own
    here; hitlcad's other modules (and its site-packages, built for another Python) stay out of Blender's."""
    import importlib.util
    import types

    package = types.ModuleType("hitlcad")
    package.__path__ = []
    sys.modules["hitlcad"] = package
    path = Path(__file__).resolve().parents[2] / "sdk" / "__init__.py"
    spec = importlib.util.spec_from_file_location("hitlcad.sdk", path)
    sys.modules["hitlcad.sdk"] = package.sdk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package.sdk)


_load_sdk()

GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def _reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _millimetres()


def _millimetres():
    units = bpy.context.scene.unit_settings
    units.system = "METRIC"
    units.scale_length = 0.001
    units.length_unit = "MILLIMETERS"


def matrix(value):
    """A placement or connector frame as a 4 × 4 matrix: a mathutils.Matrix, an empty (its world matrix),
    or a 4 × 4 nested list."""
    if isinstance(value, bpy.types.Object):
        value = value.matrix_world
    if isinstance(value, mathutils.Matrix):
        if len(value.row) != 4 or len(value.col) != 4:
            raise ModelError(runtime.FRAME, "a placement or connector frame is a 4 × 4 Matrix")
        return [[float(c) for c in row] for row in value]
    rows = [list(map(float, row)) for row in value] if isinstance(value, list | tuple) else None
    if rows is None or len(rows) != 4 or any(len(r) != 4 for r in rows):
        raise ModelError(
            runtime.FRAME, f"a placement or connector frame is a mathutils.Matrix, got {type(value).__name__}"
        )
    return rows


def _objects(value, what):
    objs = list(value) if isinstance(value, list | tuple) else [value]
    if not objs or not all(isinstance(o, bpy.types.Object) and o.type in GEOMETRY_TYPES for o in objs):
        raise ModelError(runtime.SHAPE, f"{what}: a part is a mesh or curve Object, or a list of them")
    return objs


def _select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _export_objects(objs, path):
    """The part's objects as built, under their names, so a click can say which object it hit, and with their
    materials, whose base colours the view, 3MF and model.blend show; in glTF's metres. Copies are exported,
    their modifiers applied and their placement scaled into the mesh: scaling the objects themselves would
    also scale what their modifiers see (a boolean's other object stays in millimetres)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    names = [o.name for o in objs]
    copies = []
    try:
        for o, name in zip(objs, names, strict=True):
            mesh = bpy.data.meshes.new_from_object(o.evaluated_get(depsgraph), preserve_all_data_layers=True,
                                                   depsgraph=depsgraph)  # fmt: skip
            mesh.transform(mathutils.Matrix.Scale(0.001, 4) @ o.matrix_world)
            for i, slot in enumerate(o.material_slots):  # the materials it shows, its object's own ones included
                mesh.materials[i] = slot.material
            o.name = f"{name} (built)"
            copies.append(bpy.data.objects.new(name, mesh))
            bpy.context.scene.collection.objects.link(copies[-1])
        _select_only(copies)
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=False)
    finally:
        for copy in copies:
            mesh = copy.data
            bpy.data.objects.remove(copy)
            bpy.data.meshes.remove(mesh)
        for o, name in zip(objs, names, strict=True):
            o.name = name


class Blender:
    """Parts are mesh or curve objects, exported as an STL and a glTF of their objects; hitlcad tessellates their
    view from the glTF (`BlenderBackend.complete_parts`). Nothing is reused by content: a part's objects are
    rebuilt in an empty scene by every file that makes them."""

    def __init__(self):
        self.modes = {"assembly": assembly}

    def placement(self, value):
        return matrix(value)

    def begin(self):
        _reset_scene()

    def unpack(self, what, value):
        if not (isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict)):
            raise ModelError(
                runtime.RETURN, f"{what}: return (object or [objects], {{connector: Matrix or [Matrices]}})"
            )
        return _objects(value[0], what), value[1], None

    def digest(self, objs):
        return None

    def export(self, objs, name, out, tolerance):
        stl, glb = f"parts/{name}.stl", f"parts/{name}.glb"
        _select_only(objs)
        bpy.ops.wm.stl_export(filepath=str(out / stl), export_selected_objects=True, apply_modifiers=True)
        _export_objects(objs, str(out / glb))
        return {"mesh": stl, "objects": glb}


def _collection(name, parent):
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def _import_part(path, collection):
    """A built part's objects into `collection`, in the part's frame in millimetres, under their names and with
    their materials. Its glTF is metres, as glTF is, whichever backend wrote it; empties that only group the
    objects (a build123d compound's) are dissolved."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path, import_scene_as_collection=False)
    new = [o for o in bpy.data.objects if o not in before]
    for top in [o for o in new if o.parent is None]:
        top.matrix_world = mathutils.Matrix.Scale(1000, 4) @ top.matrix_world
    bpy.context.view_layer.update()
    for empty in [o for o in new if o.type == "EMPTY" and o.children]:
        for child in empty.children:
            world = child.matrix_world.copy()
            child.parent = None
            child.matrix_world = world
        new.remove(empty)
        bpy.data.objects.remove(empty)
    for o in new:
        for c in o.users_collection:
            c.objects.unlink(o)
        collection.objects.link(o)


def _merge_materials():
    """One material per name and colour: each part's glTF brings its own copy of a material that parts share,
    which Blender would call `fur.001`."""
    for material in list(bpy.data.materials):
        stem, _, n = material.name.rpartition(".")
        first = bpy.data.materials.get(stem) if n.isdigit() else None
        if first is not None and tuple(first.diffuse_color) == tuple(material.diffuse_color):
            material.user_remap(first)
            bpy.data.materials.remove(material)


def assembly(job):
    """The built assembly as one scene (`model.blend`). Each part's objects sit in a collection of their own
    (`lid`, `box/lid` in component box) under `<design> parts`, which the view layer excludes; each instance
    is a collection instance at its placement named by its path from the design (`<design>/box[1]`), so it
    never takes an object's name, under `<design> assembly`. A component is a collection of its own
    instances (`<design>/box/lid`), which the component's instances show."""
    bpy.ops.wm.read_factory_settings(use_empty=True)  # the importer scales glTF's metres to the scene's units
    tree = job["tree"]
    library = _collection(f"{tree['name']} parts", bpy.context.scene.collection)

    def design(node, path, placed):
        sources = {}
        for name, objects in node["parts"].items():
            sources[name] = _collection(path + name, library)
            _import_part(objects, sources[name])
        for name, child in node["components"].items():
            sources[name] = design(child, f"{path}{name}/", _collection(path + name, library))
        for instance in node["instances"]:
            empty = bpy.data.objects.new(f"{tree['name']}/{path}{instance['name']}", None)
            empty.instance_type = "COLLECTION"
            empty.instance_collection = sources[instance["of"]]
            empty.matrix_basis = mathutils.Matrix(instance["transform"])
            placed.objects.link(empty)
        return placed

    design(tree, "", _collection(f"{tree['name']} assembly", bpy.context.scene.collection))
    _merge_materials()
    bpy.context.view_layer.layer_collection.children[library.name].exclude = True
    _millimetres()
    bpy.ops.wm.save_as_mainfile(filepath=job["target"])
    return {}


runtime.serve(Blender())
