"""The puppy's sculpt, without Blender: its metaball elements and the fixed points of its layout, in "dog
units" (the sculpt is roughly 100 tall and scaled to the `height` parameter as a whole).

interfaces.py evaluates the metaball field here to find the top of the figurine (the head, or an ear when
it stands higher), so the scale (and with it every joint frame) is known before any geometry is built;
parts/figure.py builds the same elements and ears in Blender. Sculpt coordinates: Z up, the dog faces -Y.
"""

from math import cos, radians, sin

import numpy as np

# Metaball field: stiffness 2, threshold 0.27 -> an isolated ball's surface sits at ~0.698 * radius.
THRESHOLD = 0.27
STIFFNESS = 2.0
K = 1.0 / 0.698

FLOOR = 1.0  # the bottom is cut flat here so the figurine stands steadily
PIVOT = (0.0, -4.0, 52.0)  # the head scales and tilts about the top of the neck

COLLAR_CENTRE = (0.0, -3.5, 47.5)
COLLAR_TILT_DEG = 22.0  # the collar's plane leans forward, following the neck
COLLAR_WIDTH = 5.0
COLLAR_THICK = 2.0

TAIL_POINTS = ((0.0, 20.0, 14.0), (0.0, 42.0, 15.0), (0.0, 40.0, 40.0))  # quadratic Bezier from the rump
TAIL_RADIUS = 4.4  # at the root; it tapers to 60 % at the tip
TAIL_PLUG = 1.5  # the tail's flat root face, this far along it from the start of its centre line, inside the rump


def rot_x(a):
    return np.array([[1, 0, 0], [0, cos(a), -sin(a)], [0, sin(a), cos(a)]])


def rot_y(a):
    return np.array([[cos(a), 0, sin(a)], [0, 1, 0], [-sin(a), 0, cos(a)]])


def head_map(p):
    """(H, Rh): H maps a point written in head-local coordinates (untilted, unscaled) onto the sculpt;
    Rh is the head's rotation."""
    hs = p["head_scale"]
    rh = rot_y(radians(p["head_tilt_deg"]))
    pivot = np.array(PIVOT)

    def h(v):
        return pivot + rh @ ((np.asarray(v, float) - pivot) * hs)

    return h, rh


def elements(p):
    """The blended body: ("ball", centre, r), ("ellipsoid", centre, radii, rotation) and ("capsule", a, b, r),
    each sized by its own surface (as if alone)."""
    hs = p["head_scale"]
    h, rh = head_map(p)
    out = [
        ("ellipsoid", (0, 4, 30), (16, 17, 23), rot_x(radians(15))),  # torso
        ("ball", (0, -6, 36), 12),  # chest
        ("ball", (0, -4, 48), 10.5),  # neck
    ]
    for s in (-1, 1):
        out += [
            ("ellipsoid", (s * 12, 12, 14), (10, 14, 13), np.eye(3)),  # haunch
            ("ellipsoid", (s * 15, -2, 4.5), (6, 11, 4.5), np.eye(3)),  # hind foot
            ("capsule", (s * 8, -8, 32), (s * 8, -12, 7), 5),  # front leg
            ("ellipsoid", (s * 8, -15, 4.5), (6.5, 8, 4.5), np.eye(3)),  # front paw
        ]
    out += [
        ("ellipsoid", h((0, -6, 67)), tuple(r * hs for r in (18, 16, 16.5)), rh),  # cranium
        ("ellipsoid", h((0, -20, 59.5)), tuple(r * hs for r in (10.5, 10.5, 8.5)), rh),  # muzzle
        ("ball", h((0, -17, 53)), 6 * hs),  # chin / lower jaw
    ]
    for s in (-1, 1):
        out.append(("ball", h((s * 7.5, -16, 61)), 7.5 * hs))  # smiling cheeks
    return out


def field(points, els):
    """Blender's metaball field (stiffness × (1 − d²/R²)³ per element) at an (n, 3) array of points."""
    total = np.zeros(len(points))
    for el in els:
        kind = el[0]
        if kind == "ball":
            d2 = np.sum((points - np.asarray(el[1], float)) ** 2, axis=1) / (el[2] * K) ** 2
        elif kind == "ellipsoid":
            local = (points - np.asarray(el[1], float)) @ np.asarray(el[3], float)
            d2 = np.sum((local / np.asarray(el[2], float)) ** 2, axis=1) / K**2
        else:
            a, b = np.asarray(el[1], float), np.asarray(el[2], float)
            mid, half = (a + b) / 2, np.linalg.norm(b - a) / 2
            u = (b - a) / (2 * half)
            rel = points - mid
            along = rel @ u
            radial2 = np.sum(rel**2, axis=1) - along**2
            d2 = (np.maximum(np.abs(along) - half, 0) ** 2 + radial2) / (el[3] * K) ** 2
        f = np.clip(1 - d2, 0, None)
        total += STIFFNESS * f**3
    return total


def _blob_top(els):
    """The highest point of the blended body (the top of the head)."""
    reach = [np.asarray(e[1], float) + K * (max(e[2]) if e[0] == "ellipsoid" else e[-1]) for e in els]
    lo = np.min([np.asarray(e[1], float) for e in els], axis=0) - 40
    hi = np.max(reach, axis=0)
    xs, ys = np.arange(lo[0], hi[0], 0.5), np.arange(lo[1], hi[1], 0.5)
    gx, gy = (g.ravel() for g in np.meshgrid(xs, ys))
    z = hi[2]
    while True:  # the first horizontal slice, from the top down, that reaches inside the body
        inside = field(np.column_stack([gx, gy, np.full(gx.shape, z)]), els) >= THRESHOLD
        if inside.any():
            break
        z -= 0.5
    best = np.argmax(inside)
    fx, fy = (g.ravel() for g in np.meshgrid(gx[best] + np.arange(-1, 1.01, 0.05), gy[best] + np.arange(-1, 1.01, 0.05)))
    below, above = np.full(fx.shape, z - 0.5), np.full(fx.shape, z + 0.5)
    for _ in range(40):  # bisect each column between a point inside and one outside
        mid = (below + above) / 2
        ins = field(np.column_stack([fx, fy, mid]), els) >= THRESHOLD
        below, above = np.where(ins, mid, below), np.where(ins, above, mid)
    ok = field(np.column_stack([fx, fy, z - 0.5 + 0 * fx]), els) >= THRESHOLD
    return float(np.max(below[ok]))


def field_cast(els):
    """cast(target, direction) -> (point, normal) on the blended body, hit by a ray coming from outside along
    `direction` toward `target`: as parts/figure.py casts against the mesh, on the field instead."""

    def cast(target, direction, dist=80.0):
        d = np.asarray(direction, float) / np.linalg.norm(direction)
        o, step = np.asarray(target, float) + d * dist, 0.25
        ts = np.arange(0, 2 * dist, step)
        inside = field(o - np.outer(ts, d), els) >= THRESHOLD
        k = int(np.argmax(inside))
        lo, hi = ts[k] - step, ts[k]
        for _ in range(40):
            mid = (lo + hi) / 2
            if field((o - d * mid)[None], els)[0] >= THRESHOLD:
                hi = mid
            else:
                lo = mid
        q = o - d * hi
        e = np.eye(3) * 1e-4
        grad = np.array([field((q + e[j])[None], els)[0] - field((q - e[j])[None], els)[0] for j in range(3)])
        return q, -grad / np.linalg.norm(grad)

    return cast


def rotation(z, xhint):
    """3 × 3 rotation whose Z column is `z` and X column is as close to `xhint` as possible."""
    z = np.asarray(z, float) / np.linalg.norm(z)
    y = np.cross(z, xhint)
    y /= np.linalg.norm(y)
    return np.column_stack([np.cross(y, z), y, z])


def ears(p, cast, thinnest=0.0):
    """The two floppy ears: (side, centre, radii, rotation) of each ellipsoid, hung from the head's surface
    found by `cast`. `thinnest` is the narrowest detail that prints (dog units): a flat ear is at least that
    thick from its middle to each face, so its thin tip still prints."""
    hs = p["head_scale"]
    h, rh = head_map(p)
    f = radians(p["ear_flare_deg"])
    out = []
    for s, side in ((-1, "r"), (1, "l")):
        loc, n = cast(h((0, -6, 67)), rh @ np.array((s * 0.70, -0.05, 0.71)))
        down = rh @ np.array((s * sin(f), -0.12, -cos(f)))
        down /= np.linalg.norm(down)
        rl = 14 * hs
        radii = (max(2.6 * hs, thinnest), 8 * hs, rl)
        out.append((side, loc - n * 1.6 * hs + down * rl * 0.8, radii, rotation(down, rh @ np.array((s, 0, 0)))))
    return out


def top(p):
    """The highest point of the figurine in dog units: the head's, or an ear's when it stands higher."""
    els = elements(p)
    tops = [_blob_top(els)]
    for _, centre, radii, rot in ears(p, field_cast(els)):
        tops.append(centre[2] + float(np.sqrt(np.sum((rot[2] * np.asarray(radii)) ** 2))))
    return max(tops)


def tail_curve(p, n=9):
    """The tail's centre line: the Bezier from the rump, wagged sideways about the Y axis at its root."""
    t0, t1, t2 = (np.array(v) for v in TAIL_POINTS)
    wag = rot_y(radians(p["tail_wag_deg"]))
    pts = []
    for k in range(n):
        t = k / (n - 1)
        q = (1 - t) ** 2 * t0 + 2 * (1 - t) * t * t1 + t * t * t2
        pts.append(t0 + wag @ (q - t0))
    return pts


def frame(origin, x, z):
    """A 4 × 4 frame (nested lists) from its origin, X and Z directions."""
    z = np.asarray(z, float) / np.linalg.norm(z)
    y = np.cross(z, x)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, origin
    return m.tolist()
