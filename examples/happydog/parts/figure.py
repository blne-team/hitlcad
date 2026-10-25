"""The puppy, sculpted as one piece and split into the three parts it prints as: body, head and tail.

The body is one metaball blob (lib/sculpt.py's elements), converted to a mesh. Coloured details (eyes,
nose, tongue, ears, spots, collar, ...) are placed on the blob by ray-casting against it, so they follow
the surface when parameters change. Everything is modelled in dog units, then scaled into millimetres
(i.scale). The details are merged into the blob, each keeping its colour, so every part is one solid
(overlapping shells would print, but read as paper-thin walls to a wall-thickness check). The figure is
then split at the neck joint, through the middle of the collar, and the tail, standing on a flat root,
plugs into a socket in the rump. Each joint leaves a glue line.
"""

from math import cos, pi, radians, sin

import bmesh
import numpy as np
from lib import sculpt
from lib.meshes import (
    Blob,
    X,
    Y,
    Z,
    bake,
    band,
    copy,
    cut,
    cylinder,
    disc,
    ellipsoid,
    frame,
    half_ellipsoid,
    halves,
    inflated,
    material,
    mesh_object,
    remove,
    tube,
    union,
)
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

DEEP = 2.8  # how far the band round the neck reaches into it, dog units
PATCH = 0.45  # how far the coloured patches (eye patch, spots, blush) stand off the surface, dog units


def build(p, i):
    hs = p["head_scale"]
    es = p["eye_size"]
    happy_eyes = p["eye_style"] >= 0.5
    H, rh = sculpt.head_map(p)
    Rh = Matrix([list(r) for r in rh])

    def Hv(v):
        return Vector(H(v))

    def Hd(d):
        return (Rh @ Vector(d)).normalized()

    hx = Rh @ X  # head's sideways axis

    blob = bake(Blob(p["detail"], sculpt.elements(p)).obj)
    smooth = copy(blob, "smooth_body")  # before its cuts: what the coloured patches follow

    # open smiling mouth: a D-shaped cut (flat top) under the nose; the cut faces turn dark red.
    # Cut before placing the details, so the smile lines can find the real mouth outline.
    mouth = Rh @ Matrix.Rotation(radians(-8), 3, "X")  # the cut's frame: X across, -Y out of the mouth, Z up
    centre = Hv((0, -31, 57.0))
    cutter = half_ellipsoid("mouth_cut", centre, (8.5 * hs, 7.0 * hs, 4.6 * hs * p["mouth_open"]), mouth, "mouth")
    blob = cut(blob, cutter, transfer=True)
    # flatten the bottom a little so it stands on the bed
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0,
                          matrix=Matrix.Translation((0, 0, sculpt.FLOOR - 50)) @ Matrix.Diagonal((200, 200, 100, 1)))  # fmt: skip
    floor = mesh_object("floor_cut", bm, "fur")
    blob = cut(blob, floor)
    remove(floor)
    blob.name = "body"

    pivot = Vector(sculpt.PIVOT)

    def to_head(w):
        return pivot + (Rh.transposed() @ (w - pivot)) / hs

    bm = bmesh.new()
    bm.from_mesh(blob.data)
    bvh = BVHTree.FromBMesh(bm)
    # mouth outline: edges between the dark red cut faces and the fur
    mi = [mat.name for mat in blob.data.materials].index("mouth")
    lip = [v.co.copy() for e in bm.edges if len(e.link_faces) == 2
           and (e.link_faces[0].material_index == mi) != (e.link_faces[1].material_index == mi)
           for v in e.verts]  # fmt: skip
    bm.free()

    def cast(target, direction, dist=80):
        """Surface point and normal hit by a ray coming from outside along `direction` toward `target`."""
        d = Vector(direction).normalized()
        o = Vector(target) + d * dist
        for _ in range(8):
            loc, nrm, _, _ = bvh.ray_cast(o, -d)
            if loc is None:
                return Vector(target), d
            if nrm.dot(d) > 0:  # entering the surface: this is the one we want
                return loc, nrm
            o = loc - d * 1e-3  # the ray started inside another limb: skip its exit
        return Vector(target), d

    head, body, tail = [], [], []  # detail shells of each part
    curves = {}  # curve objects -> the part list they join once baked
    patches = {}  # colour -> [(part, name, cylinder outline)]: raised patches that follow the surface

    fm = i.feature_min  # the narrowest detail that prints, in dog units

    def raised(name, pts, r, color, part, closed=False):
        """A raised line, at least the printable minimum wide; left out if designed under half of it."""
        if 2 * r >= fm / 2:
            curves[tube(name, pts, max(r, fm / 2), color, closed=closed)] = part

    def bump(*radii):
        return tuple(max(r, fm / 2) for r in radii)

    def patch_on(name, target, direction, radii, color, part, xhint=X):
        """A patch of colour raised PATCH above the surface, following it, with an elliptical outline: a
        cylinder that cuts it out of the surface lifted by PATCH (below), so its edge stands square."""
        loc, n = cast(target, direction)
        a, b = 0.85 * radii[0], 0.85 * radii[1]  # the outline the original sunk lens showed
        depth = 0.8 * min(a, b)
        outline = Matrix.Translation(loc) @ frame(n, xhint).to_4x4() @ Matrix.Translation((0, 0, -depth))
        patches.setdefault(color, []).append(
            (part, cylinder(name, outline @ Matrix.Diagonal((a, b, 1, 1)), 1.0, depth + PATCH + 0.5, color)))
        return loc, n

    # ---------------------------------------------------------------- face
    head_c = Hv((0, -6, 67))
    sides = ((-1, "r"), (1, "l"))

    patch = 0.0  # eye patch first (eyes sit on it)
    if p["spots"] >= 0.5:
        patch_on("eye_patch", head_c, Hd((0.40, -0.82, 0.36)), (6.8 * hs, 6.2 * hs), "fur_dark", head, hx)
        patch = PATCH

    for s, tag in sides:
        loc, n = cast(head_c, Hd((s * 0.40, -0.82, 0.30)))
        F = frame(n, hx)
        lift = patch if s == 1 else 0.0
        if happy_eyes:
            a = 3.4 * es * hs
            pts = []
            for k in range(7):
                t = -1 + 2 * k / 6
                g = loc + F @ Vector((t * a, 0.55 * a * (1 - t * t) - 0.2 * a, 0))
                q, qn = cast(g, n)
                pts.append(q + qn * (0.25 + lift))
            raised(f"eye_{tag}", pts, 0.8 * es * hs, "black", head)
        else:
            rx, ry, rz = 3.2 * es * hs, 3.6 * es * hs, 2.4 * es * hs
            head.append(ellipsoid(f"eye_{tag}", loc + n * (lift - 0.7), (rx, ry, rz), F, "black"))
            u, v = -0.38, 0.42  # highlight: upper left as seen from the front
            w = (1 - u * u - v * v) ** 0.5
            c = loc + n * (lift - 0.7) + F @ Vector((u * rx, v * ry, w * rz))
            head.append(ellipsoid(f"eye_shine_{tag}", c, bump(*(0.95 * es * hs,) * 3), None, "white", 16, 8))
        b = 3.0 * hs  # raised eyebrow arc
        pts = []
        for k in range(5):
            t = -1 + 2 * k / 4
            g = loc + F @ Vector((t * b, 5.6 * es * hs + 1.0 * hs * (1 - t * t), 0))
            q, qn = cast(g, n)
            pts.append(q + qn * 0.2)  # on the fur: the eye patch may not reach this far
        raised(f"brow_{tag}", pts, 0.6 * hs, "fur_dark", head)

    # nose
    loc, n = cast(Hv((0, -20, 59.5)), Hd((0, -0.78, 0.62)))
    F = frame(n, hx)
    head.append(ellipsoid("nose", loc + n * 0.6, (4.6 * hs, 3.2 * hs, 2.8 * hs), F, "black"))
    head.append(ellipsoid("nose_shine", loc + n * (0.6 + 2.2 * hs) + F @ Vector((-1.4 * hs, 1.0 * hs, 0)),
                          bump(1.1 * hs, 0.7 * hs, 0.6 * hs), F, "white", 16, 8))  # fmt: skip
    nose_bottom = loc + n * 0.6 - F @ Vector((0, 2.6 * hs, 0))

    if p["blush"] >= 0.5:  # cheek blush
        for s, tag in sides:
            patch_on(f"blush_{tag}", Hv((s * 7.5, -16, 61)), Hd((s * 0.65, -0.72, -0.05)), (3.4 * hs, 2.4 * hs),
                     "blush", head, hx)

    # philtrum: from under the nose down to the middle of the mouth's top edge
    top = max((q for q in lip if abs(to_head(q).x) < 1.5), key=lambda q: to_head(q).z)
    pts = []
    for k in range(3):
        q, qn = cast(nose_bottom.lerp(top, k / 3), Hd((0, -1, 0.1)))
        pts.append(q + qn * 0.1)
    pts.append(top)
    raised("philtrum", pts, 0.5 * hs, "black", head)

    # smile lines: from each mouth corner (the outline's outermost point), curling up and out into the cheek
    for s, tag in sides:
        corner = max(lip, key=lambda q: s * to_head(q).x)
        n = Hd((s * 0.55, -0.83, 0.1))  # the cheek's facing (the cut edge's own normal is unreliable)
        F = frame(n, hx)
        pts = [corner]
        for u, v in ((0.9, 0.3), (1.8, 1.0), (2.4, 2.0), (2.6, 3.0)):
            q, qn = cast(corner + F @ Vector((s * u * hs, v * hs, 0)), n)
            pts.append(q + qn * 0.1)
        raised(f"smile_{tag}", pts, 0.5 * hs, "black", head)

    # lower lip: the cut's rising floor meets the receding chin at an acute angle, a feather edge; a round bead
    # along it (a little onto the lip, off the rim's own vertices) gives the lip a rounded edge that prints
    depth = 4.6 * hs * p["mouth_open"]
    rim = {}
    for q in lip:
        u = mouth.transposed() @ (q - centre)
        if u.z < -0.15 * depth:  # the lower lip, short of the corners
            rim[round(u.x, 3)] = q + mouth @ Vector((0, 0, -1)) * 0.2 * fm
    rim = [rim[x] for x in sorted(rim)]
    rim = rim[:: max(1, len(rim) // 16)]
    for _ in range(3):  # smooth out the mesh's steps, so the bead does not fold over itself
        rim = [rim[0]] + [(rim[k - 1] + 2 * rim[k] + rim[k + 1]) / 4 for k in range(1, len(rim) - 1)] + [rim[-1]]
    raised("lower_lip", rim, 0.6 * fm, "mouth", head)

    # tongue, hanging out over the lower lip, with a groove down the middle
    tl = p["tongue_length"]
    ang = radians(60)
    root = Hv((0, -26, 56))
    d = Hd((0, -cos(ang), -sin(ang)))
    ry, rz = 6.5 * tl * hs, 1.9 * hs
    F = frame(Hd((0, -sin(ang), cos(ang))), hx)
    tc = root + d * ry * 0.8
    head.append(ellipsoid("tongue", tc, bump(4.3 * hs, ry, rz), F, "tongue"))
    pts = [tc + F @ Vector((0, v * ry, rz * (1 - v * v) ** 0.5 * 0.97)) for v in (0.7, 0.35, 0.0, -0.35, -0.6)]
    raised("tongue_groove", pts, 0.35 * hs, "mouth", head)

    # ---------------------------------------------------------------- ears
    def mesh_cast(target, direction):
        return tuple(np.array(v) for v in cast(Vector(target), Vector(direction)))

    for tag, centre, radii, rot in sculpt.ears(p, mesh_cast, fm):
        head.append(ellipsoid(f"ear_{tag}", Vector(centre), radii, Matrix(rot.tolist()), "fur_dark", 48, 24))

    # ---------------------------------------------------------------- collar and tag, or a fold of fur
    # The neck joint runs through the middle of a band round the neck: the band's outer wall stands square
    # to the joint all round, where the bare neck would meet it at a grazing angle and leave a feather edge.
    c0 = Vector(sculpt.COLLAR_CENTRE)
    ct = radians(sculpt.COLLAR_TILT_DEG)
    axis = Vector((0, -sin(ct), cos(ct)))
    e2 = axis.cross(X)
    pts, nrms = [], []
    N = 72
    for k in range(N):
        th = 2 * pi * k / N - pi / 2
        loc, n = cast(c0, X * cos(th) + e2 * sin(th), 60)
        pts.append(loc)
        nrms.append(n)
    for _ in range(3):  # smooth the loop
        pts = [(pts[k - 1] + 2 * pts[k] + pts[(k + 1) % N]) / 4 for k in range(N)]
        nrms = [(nrms[k - 1] + 2 * nrms[k] + nrms[(k + 1) % N]).normalized() for k in range(N)]
    # Each half of the band, above and below the joint, is at least 1.25 printable minimums tall (its rounded
    # edge still prints), and the band reaches DEEP into the neck, so it also fills the shallow notch where
    # the throat meets the chest.
    width, thick = max(sculpt.COLLAR_WIDTH, 2.5 * fm), sculpt.COLLAR_THICK
    if p["collar"] < 0.5:  # no collar: a soft, rounded fold of fur in its place
        head.append(band("neck_fold", pts, nrms, axis, width, 0.6 + DEEP, DEEP, "fur", roundness=1.0))
    else:
        head.append(band("collar", pts, nrms, axis, width, thick - 0.8 + DEEP, DEEP, "collar"))
        front = 0 if (pts[0] - c0).dot(Y) < 0 else N // 2
        pf, nf = pts[front], nrms[front]
        downv = (-Z - nf * (-Z).dot(nf)).normalized()
        centre = pf + nf * (thick + 0.2) + downv * (width / 2 + 2.0)
        # the tag hangs just below the collar, against the chest: on the body, held by a stub behind it

        def height(v):  # above the neck joint, through the middle of the collar
            return (v - c0).dot(axis)

        tag_t = max(1.2, fm)
        na = abs(nf.dot(axis))
        reach = 3.0 * (1 - na * na) ** 0.5 + tag_t / 2 * na  # from its centre to its rim's highest point, along the axis
        clear = (-0.4 - reach - height(pf + nf * (thick + 0.2))) / downv.dot(axis)  # all of it below the joint
        drop = max(width / 2 + 2.0, clear)
        centre = pf + nf * (thick + 0.2) + downv * drop
        body.append(disc("tag", centre, 3.0, tag_t, nf, "gold"))
        for tilt in range(0, 90, 5):  # back into the chest, below the joint
            back = (-nf).lerp(downv, tilt / 90).normalized()
            loc, _, _, _ = bvh.ray_cast(centre, back)
            if loc is not None and height(loc) < -1.3:
                break
        body.append(cylinder("tag_stub", Matrix.Translation(centre) @ frame(loc - centre, X).to_4x4(), max(0.8, fm / 2),
                             (loc - centre).length + 0.5, "gold"))  # fmt: skip

    # ---------------------------------------------------------------- body details
    if p["spots"] >= 0.5:
        patch_on("back_spot", Vector((0, 4, 32)), (0.75, 0.45, 0.45), (9, 7), "fur_dark", body)
        patch_on("hip_spot", Vector((-12, 12, 14)), (-0.95, 0.25, 0.2), (6, 5), "fur_dark", body, Y)

    paws = [(s * 8, -15, 4.5) for s in (-1, 1)] + [(s * 15, -6, 4.5) for s in (-1, 1)]
    for k, pc in enumerate(paws):  # toe lines on all four paws
        for dx in (-2.2, 2.2):
            pts = []
            for j in range(5):
                phi = radians(35 + 13 * j)  # from the top of the paw round to its front
                c = Vector(pc) + Vector((dx, 0, 0))
                q, qn = cast(c, (0, -sin(phi), cos(phi)), 14)
                if (q - c).length < 1:  # missed the paw
                    break
                pts.append(q + qn * 0.1)
            else:
                raised(f"toe_{k}_{'ab'[dx > 0]}", pts, 0.5, "fur_dark", body)

    # ---------------------------------------------------------------- tail
    line = [Vector(v) for v in sculpt.tail_curve(p)]
    curves[tube("tail", line, sculpt.TAIL_RADIUS, "fur", [1.0 - 0.4 * k / 8 for k in range(9)])] = tail
    tail.append(ellipsoid("tail_tip", line[-1], (3.4, 3.4, 3.4), None, "cream", 24, 12))

    for c, part in curves.items():
        part.append(bake(c))

    for color, outlined in patches.items():  # the smooth surface lifted by PATCH, cut out by each outline
        for part, outline in outlined:
            lifted = inflated(smooth, PATCH, outline.name)
            lifted.data.materials[0] = material(color)
            part.append(cut(cut(lifted, outline, "INTERSECT"), cutter))  # and kept out of the mouth
            remove(outline)
    remove(smooth, cutter)

    # ---------------------------------------------------------------- millimetres, joints, parts
    to_mm = Matrix.Scale(i.scale, 4) @ Matrix.Translation((0, 0, -sculpt.FLOOR))
    for o in [blob, *head, *body, *tail]:
        o.data.transform(to_mm)
        o.data.update()

    # the tail plugs into a socket in the rump, on its flat root face
    root = Matrix(i.tail_root)
    tube_obj = next(o for o in tail if o.name == "tail")
    tail.remove(tube_obj)
    tube_obj = halves(tube_obj, root, 0)[1]
    tail = union(tube_obj, tail, "tail")
    socket = inflated(tail, i.glue_gap, "tail_socket")
    blob = cut(blob, socket)
    remove(socket)

    joint = Matrix(i.neck_joint)
    # one solid of many colours: the details' faces keep their materials
    figure = union(blob, [*head, *body], "figure")
    below, above = halves(figure, joint, i.glue_gap)
    peg = cylinder("peg", joint @ Matrix.Translation((0, 0, -i.peg_len)), i.peg_d / 2,
                   2 * i.peg_len - i.glue_gap / 2, "fur", 96)  # fmt: skip
    below = union(below, [peg], "body")
    socket = cylinder("socket_cut", joint @ Matrix.Translation((0, 0, -1)), i.socket_d / 2, i.socket_depth + 1,
                      "fur", 96)  # fmt: skip
    above = cut(above, socket)
    remove(socket)
    for o, name in ((below, "body"), (above, "head"), (tail, "tail")):
        o.name = o.data.name = name

    above.data.transform(joint.inverted())
    tail.data.transform(root.inverted())
    flip = Matrix.Rotation(pi, 4, "X")  # a connector facing the one it mates with
    return {
        "body": (below, {"neck": joint, "tail": root}),
        "head": (above, {"neck": flip}),
        "tail": (tail, {"root": flip}),
    }
