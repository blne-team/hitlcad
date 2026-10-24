"""The puppy, sculpted as one piece and split into the three parts it prints as: body, head (with the collar
and tag) and tail.

The body is one metaball blob (lib/sculpt.py's elements), converted to a mesh. Coloured details (eyes,
nose, tongue, ears, spots, collar, ...) are separate overlapping shells placed on the blob by ray-casting
against it, so they follow the surface when parameters change. Everything is modelled in dog units, then
scaled into millimetres (i.scale) and split: the head comes off at the neck joint along the collar's
lower edge, the tail is cut back to the rump's surface. Each part leaves a glue line to the next.
"""

from math import cos, pi, radians, sin

import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from lib import sculpt
from lib.meshes import (
    X,
    Y,
    Z,
    Blob,
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
    mesh_object,
    remove,
    tube,
)


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
    raw = copy(blob, "smooth_body")  # before its cuts: what the tail is trimmed against

    # open smiling mouth: a D-shaped cut (flat top) under the nose; the cut faces turn dark red.
    # Cut before placing the details, so the smile lines can find the real mouth outline.
    cutter = half_ellipsoid("mouth_cut", Hv((0, -31, 57.0)), (8.5 * hs, 7.0 * hs, 4.6 * hs * p["mouth_open"]),
                            Rh @ Matrix.Rotation(radians(-8), 3, "X"), "mouth")  # fmt: skip
    blob = cut(blob, cutter, transfer=True)
    # flatten the bottom a little so it stands on the bed
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0,
                          matrix=Matrix.Translation((0, 0, sculpt.FLOOR - 50)) @ Matrix.Diagonal((200, 200, 100, 1)))  # fmt: skip
    floor = mesh_object("floor_cut", bm, "fur")
    blob = cut(blob, floor)
    remove(cutter, floor)
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

    def on_surface(name, target, direction, radii, sink, color, xhint=X):
        loc, n = cast(target, direction)
        return ellipsoid(name, loc - n * sink, radii, frame(n, xhint), color)

    head, body, tail = [], [], []  # detail shells of each part
    curves = {}  # curve objects -> the part list they join once baked

    # ---------------------------------------------------------------- face
    head_c = Hv((0, -6, 67))
    sides = ((-1, "r"), (1, "l"))

    patch = 0.0  # eye patch first (eyes sit on it)
    if p["spots"] >= 0.5:
        head.append(on_surface("eye_patch", head_c, Hd((0.40, -0.82, 0.36)), (6.8 * hs, 6.2 * hs, 1.2), 0.75,
                               "fur_dark", hx))  # fmt: skip
        patch = 0.45

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
            curves[tube(f"eye_{tag}", pts, 0.8 * es * hs, "black")] = head
        else:
            rx, ry, rz = 3.2 * es * hs, 3.6 * es * hs, 2.4 * es * hs
            head.append(ellipsoid(f"eye_{tag}", loc + n * (lift - 0.7), (rx, ry, rz), F, "black"))
            u, v = -0.38, 0.42  # highlight: upper left as seen from the front
            w = (1 - u * u - v * v) ** 0.5
            c = loc + n * (lift - 0.7) + F @ Vector((u * rx, v * ry, w * rz))
            head.append(ellipsoid(f"eye_shine_{tag}", c, (0.95 * es * hs,) * 3, None, "white", 16, 8))
        b = 3.0 * hs  # raised eyebrow arc
        pts = []
        for k in range(5):
            t = -1 + 2 * k / 4
            g = loc + F @ Vector((t * b, 5.6 * es * hs + 1.0 * hs * (1 - t * t), 0))
            q, qn = cast(g, n)
            pts.append(q + qn * (0.2 + lift))
        curves[tube(f"brow_{tag}", pts, 0.6 * hs, "fur_dark")] = head

    # nose
    loc, n = cast(Hv((0, -20, 59.5)), Hd((0, -0.78, 0.62)))
    F = frame(n, hx)
    head.append(ellipsoid("nose", loc + n * 0.6, (4.6 * hs, 3.2 * hs, 2.8 * hs), F, "black"))
    head.append(ellipsoid("nose_shine", loc + n * (0.6 + 2.2 * hs) + F @ Vector((-1.4 * hs, 1.0 * hs, 0)),
                          (1.1 * hs, 0.7 * hs, 0.6 * hs), F, "white", 16, 8))  # fmt: skip
    nose_bottom = loc + n * 0.6 - F @ Vector((0, 2.6 * hs, 0))

    if p["blush"] >= 0.5:  # cheek blush
        for s, tag in sides:
            loc, n = cast(Hv((s * 7.5, -16, 61)), Hd((s * 0.65, -0.72, -0.05)))
            head.append(ellipsoid(f"blush_{tag}", loc - n * 0.6, (3.4 * hs, 2.4 * hs, 0.9), frame(n, hx), "blush"))

    # philtrum: from under the nose down to the middle of the mouth's top edge
    top = max((q for q in lip if abs(to_head(q).x) < 1.5), key=lambda q: to_head(q).z)
    pts = []
    for k in range(3):
        q, qn = cast(nose_bottom.lerp(top, k / 3), Hd((0, -1, 0.1)))
        pts.append(q + qn * 0.1)
    pts.append(top)
    curves[tube("philtrum", pts, 0.5 * hs, "black")] = head

    # smile lines: from each mouth corner (the outline's outermost point), curling up and out into the cheek
    for s, tag in sides:
        corner = max(lip, key=lambda q: s * to_head(q).x)
        n = Hd((s * 0.55, -0.83, 0.1))  # the cheek's facing (the cut edge's own normal is unreliable)
        F = frame(n, hx)
        pts = [corner]
        for u, v in ((0.9, 0.3), (1.8, 1.0), (2.4, 2.0), (2.6, 3.0)):
            q, qn = cast(corner + F @ Vector((s * u * hs, v * hs, 0)), n)
            pts.append(q + qn * 0.1)
        curves[tube(f"smile_{tag}", pts, 0.5 * hs, "black")] = head

    # tongue, hanging out over the lower lip, with a groove down the middle
    tl = p["tongue_length"]
    ang = radians(60)
    root = Hv((0, -26, 56))
    d = Hd((0, -cos(ang), -sin(ang)))
    ry, rz = 6.5 * tl * hs, 1.9 * hs
    F = frame(Hd((0, -sin(ang), cos(ang))), hx)
    tc = root + d * ry * 0.8
    head.append(ellipsoid("tongue", tc, (4.3 * hs, ry, rz), F, "tongue"))
    pts = [tc + F @ Vector((0, v * ry, rz * (1 - v * v) ** 0.5 * 0.97)) for v in (0.7, 0.35, 0.0, -0.35, -0.6)]
    curves[tube("tongue_groove", pts, 0.35 * hs, "mouth")] = head

    # ---------------------------------------------------------------- ears
    for s, tag in sides:
        loc, n = cast(head_c, Hd((s * 0.70, -0.05, 0.71)))
        f = radians(p["ear_flare_deg"])
        down = Hd((s * sin(f), -0.12, -cos(f)))
        rl = 14 * hs
        head.append(ellipsoid(f"ear_{tag}", loc - n * 1.6 * hs + down * rl * 0.8, (2.6 * hs, 8 * hs, rl),
                              frame(down, Hd((s, 0, 0))), "fur_dark"))  # fmt: skip

    # ---------------------------------------------------------------- collar and tag (on the head part)
    if p["collar"] >= 0.5:
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
        width, thick = sculpt.COLLAR_WIDTH, sculpt.COLLAR_THICK
        head.append(band("collar", pts, nrms, axis, width, thick, 0.8, "collar"))
        front = 0 if (pts[0] - c0).dot(Y) < 0 else N // 2
        pf, nf = pts[front], nrms[front]
        downv = (-Z - nf * (-Z).dot(nf)).normalized()
        centre = pf + nf * (thick + 0.2) + downv * (width / 2 + 2.0)
        head.append(disc("tag", centre, 3.0, 1.2, nf, "gold"))
        # the ring the tag hangs from: from inside the collar band to just inside the tag's top edge
        out = (nf - axis * nf.dot(axis)).normalized()
        a = pf + out * 0.2 - axis * (width / 2 - 0.6)
        b = centre - downv * 2.4
        head.append(cylinder("tag_ring", Matrix.Translation(a) @ frame(b - a, X).to_4x4(), 0.4, (b - a).length,
                             "gold"))  # fmt: skip

    # ---------------------------------------------------------------- body details
    if p["spots"] >= 0.5:
        body.append(on_surface("back_spot", Vector((0, 4, 32)), (0.75, 0.45, 0.45), (9, 7, 1.2), 0.75, "fur_dark"))
        body.append(on_surface("hip_spot", Vector((-12, 12, 14)), (-0.95, 0.25, 0.2), (6, 5, 1.2), 0.75,
                               "fur_dark", Y))  # fmt: skip

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
                curves[tube(f"toe_{k}_{'ab'[dx > 0]}", pts, 0.5, "fur_dark")] = body

    # ---------------------------------------------------------------- tail
    line = [Vector(v) for v in sculpt.tail_curve(p)]
    curves[tube("tail", line, sculpt.TAIL_RADIUS, "fur", [1.0 - 0.4 * k / 8 for k in range(9)])] = tail
    tail.append(ellipsoid("tail_tip", line[-1], (3.4, 3.4, 3.4), None, "cream", 24, 12))

    for c, part in curves.items():
        part.append(bake(c))

    # ---------------------------------------------------------------- millimetres, split, joints
    to_mm = Matrix.Scale(i.scale, 4) @ Matrix.Translation((0, 0, -sculpt.FLOOR))
    for o in [blob, raw, *head, *body, *tail]:
        o.data.transform(to_mm)
        o.data.update()

    rump = inflated(raw, i.glue_gap, "rump_cutter")
    tail = [cut(o, rump) for o in tail]
    remove(raw, rump)

    joint = Matrix(i.neck_joint)
    below, above = halves(blob, joint, i.glue_gap)
    below.name, above.name = "body", "head"
    socket = cylinder("socket_cut", joint @ Matrix.Translation((0, 0, -1)), i.socket_d / 2, i.socket_depth + 1, "fur")
    above = cut(above, socket)
    remove(socket)
    peg = cylinder("peg", joint @ Matrix.Translation((0, 0, -i.peg_len)), i.peg_d / 2,
                   2 * i.peg_len - i.glue_gap / 2, "fur")  # fmt: skip

    def own(objs, frame_in_body):
        """Move a part's objects from the body's frame into its own."""
        m = frame_in_body.inverted()
        for o in objs:
            o.data.transform(m)
            o.data.update()
        return objs

    for o in [below, above, peg, *head, *body, *tail]:
        o.data.name = o.name  # the 3D view names what was clicked by its mesh
    tail_frame = Matrix(i.tail_frame)
    flip = Matrix.Rotation(pi, 4, "X")  # a connector facing the one it mates with
    return {
        "body": ([below, peg, *body], {"neck": joint, "tail": Matrix(i.tail_root)}),
        "head": (own([above, *head], joint), {"neck": flip}),
        "tail": (own(tail, tail_frame), {"root": tail_frame.inverted() @ Matrix(i.tail_root) @ flip}),
    }
