"""Blender helpers for the sculpt: coloured primitives, tubes, the collar band, the metaball blob, and turning
any of them into a plain mesh."""

from math import copysign, cos, pi, sin

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from lib import sculpt

COLORS = {
    "fur": (0.86, 0.58, 0.28),
    "fur_dark": (0.42, 0.23, 0.11),
    "cream": (0.98, 0.91, 0.78),
    "black": (0.04, 0.03, 0.03),
    "white": (1.0, 1.0, 1.0),
    "mouth": (0.42, 0.06, 0.09),
    "tongue": (0.96, 0.42, 0.52),
    "blush": (1.0, 0.62, 0.66),
    "collar": (0.85, 0.10, 0.15),
    "gold": (0.96, 0.76, 0.20),
}

X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))


def material(name):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.diffuse_color = (*COLORS[name], 1)
    return m


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def frame(z, xhint):
    """3 × 3 rotation whose Z column is `z` and X column is as close to `xhint` as possible."""
    z = Vector(z).normalized()
    y = z.cross(Vector(xhint)).normalized()
    x = y.cross(z)
    return Matrix((x, y, z)).transposed()


def mesh_object(name, bm, color):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(material(color))
    me.shade_smooth()
    return link(bpy.data.objects.new(name, me))


def ellipsoid(name, center, radii, rot=None, color="fur", segs=32, rings=16):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    m = Matrix.Translation(center) @ (rot or Matrix.Identity(3)).to_4x4() @ Matrix.Diagonal((*radii, 1))
    bmesh.ops.transform(bm, matrix=m, verts=bm.verts)
    return mesh_object(name, bm, color)


def half_ellipsoid(name, center, radii, rot=None, color="mouth"):
    """Lower half (local -Z) of an ellipsoid, closed by a flat top."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, 0), plane_no=(0, 0, 1), clear_outer=True)
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
    m = Matrix.Translation(center) @ (rot or Matrix.Identity(3)).to_4x4() @ Matrix.Diagonal((*radii, 1))
    bmesh.ops.transform(bm, matrix=m, verts=bm.verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm, color)


def cylinder(name, matrix, radius, length, color):
    """A round rod along the frame's Z, from Z=0 to Z=length."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=radius, radius2=radius, depth=length)
    bmesh.ops.transform(bm, matrix=matrix @ Matrix.Translation((0, 0, length / 2)), verts=bm.verts)
    return mesh_object(name, bm, color)


def disc(name, center, radius, thick, axis, color):
    m = Matrix.Translation(center) @ frame(axis, X if abs(Vector(axis).dot(X)) < 0.9 else Y).to_4x4()
    return cylinder(name, m @ Matrix.Translation((0, 0, -thick / 2)), radius, thick, color)


def tube(name, pts, r, color, taper=None):
    """A round tube through the points (smooth Bezier), turned into a mesh by bake(). `taper` optionally
    scales the radius at each point."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = r
    cu.bevel_resolution = 3
    cu.resolution_u = 8
    cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(pts) - 1)
    for k, (bp, pt) in enumerate(zip(sp.bezier_points, pts)):
        bp.co = pt
        bp.handle_left_type = bp.handle_right_type = "AUTO"
        bp.radius = taper[k] if taper else 1.0
    cu.materials.append(material(color))
    return link(bpy.data.objects.new(name, cu))


def band(name, pts, normals, axis, width, thick, sink, color, prof=16):
    """Closed rounded-rectangle band (a collar) following a loop of surface points."""
    bm = bmesh.new()
    rings = []
    for q, n in zip(pts, normals):
        n = (n - axis * n.dot(axis)).normalized()
        ring = []
        for k in range(prof):
            a = 2 * pi * k / prof
            u = copysign(abs(cos(a)) ** 0.5, cos(a))
            w = copysign(abs(sin(a)) ** 0.5, sin(a))
            ring.append(bm.verts.new(q + n * (thick / 2 - sink + u * thick / 2) + axis * (w * width / 2)))
        rings.append(ring)
    for k in range(len(rings)):
        a, b = rings[k], rings[(k + 1) % len(rings)]
        for j in range(prof):
            bm.faces.new((a[j], a[(j + 1) % prof], b[(j + 1) % prof], b[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm, color)


def bake(obj):
    """Replace obj by a plain mesh with its modifiers / curve bevel / metaball field applied."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    bm = bmesh.new()
    bm.from_mesh(me)
    # boolean cuts can leave slivers whose vertices merge on export into pinched edges
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)
    bmesh.ops.dissolve_degenerate(bm, dist=0.01, edges=bm.edges)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
    bm.to_mesh(me)
    bm.free()
    me.shade_smooth()
    name = obj.name
    bpy.data.objects.remove(obj)
    return link(bpy.data.objects.new(name, me))


def cut(obj, cutter, operation="DIFFERENCE", transfer=False):
    """Apply a boolean with `cutter` to obj (EXACT solver); returns the new plain mesh object."""
    m = obj.modifiers.new(cutter.name, "BOOLEAN")
    m.operation = operation
    m.object = cutter
    m.solver = "EXACT"
    if transfer:
        m.material_mode = "TRANSFER"
    return bake(obj)


def copy(obj, name):
    return link(bpy.data.objects.new(name, obj.data.copy()))


def remove(*objs):
    for o in objs:
        bpy.data.objects.remove(o)


def halves(obj, matrix, gap):
    """Split a closed mesh at the plane Z=0 of `matrix`, leaving `gap` between the halves: (below, above),
    each closed by a flat face."""
    co, no = matrix.translation, matrix.col[2].xyz.normalized()
    out = []
    for side in (-1, 1):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=co + no * side * gap / 2,
                               plane_no=no, clear_outer=side < 0, clear_inner=side > 0)
        bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        me = obj.data.copy()
        bm.to_mesh(me)
        bm.free()
        out.append(link(bpy.data.objects.new(obj.name, me)))
    remove(obj)
    return out


def inflated(obj, by, name):
    """A copy of a closed smooth mesh with every vertex pushed out along its normal: a cutter that leaves a gap."""
    out = copy(obj, name)
    me = out.data
    co, normals = np.empty(len(me.vertices) * 3), np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    me.vertices.foreach_get("normal", normals)  # all at once: moving a vertex invalidates every normal
    me.vertices.foreach_set("co", co + normals * by)
    me.update()
    return out


class Blob:
    """The metaball body from lib/sculpt.py's elements."""

    def __init__(self, res, elements):
        self.mb = bpy.data.metaballs.new("dog")
        self.mb.resolution = self.mb.render_resolution = res
        self.mb.threshold = sculpt.THRESHOLD
        self.mb.materials.append(material("fur"))
        self.obj = link(bpy.data.objects.new("body", self.mb))
        for el in elements:
            getattr(self, el[0])(*el[1:])

    def _el(self, kind, co, radius, rot=None):
        el = self.mb.elements.new(type=kind)
        el.co = tuple(co)
        el.radius = radius * sculpt.K
        el.stiffness = sculpt.STIFFNESS
        if rot is not None:
            el.rotation = rot.to_quaternion()
        return el

    def ball(self, co, r):
        self._el("BALL", co, r)

    def ellipsoid(self, co, radii, rot):
        rmax = max(radii)
        el = self._el("ELLIPSOID", co, rmax, Matrix([list(row) for row in rot]))
        el.size_x, el.size_y, el.size_z = (r / rmax for r in radii)

    def capsule(self, a, b, r):
        a, b = Vector(a), Vector(b)
        seg = b - a
        el = self._el("CAPSULE", (a + b) / 2, r, X.rotation_difference(seg).to_matrix())
        el.size_x = seg.length / 2
