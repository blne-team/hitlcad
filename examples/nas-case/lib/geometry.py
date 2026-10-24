"""Shapes and frames the part files and assembly.py share. Everything is in the case frame (see interfaces.py)
until `own` moves it into a printed part's own frame."""

import math

from build123d import Align, Box, Cone, Cylinder, Location, Plane, Polygon, Pos, Rot, Vector, extrude


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl_x(x, y, z, d, length):
    """A cylinder along X centred on (x, y, z)."""
    return Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(d / 2, length)


def cyl_y(x, y, z, d, length):
    """A cylinder along Y centred on (x, y, z)."""
    return Pos(x, y, z) * Rot(90, 0, 0) * Cylinder(d / 2, length)


def frame(origin, z, x):
    """A frame at `origin` with its Z along `z` and its X along `x`."""
    return Location(Plane(origin=tuple(origin), x_dir=tuple(x), z_dir=tuple(z)))


def axial(origin, z):
    """A frame on an axis along `z`, where the turn about it does not matter."""
    z = Vector(*z)
    x = Vector(0, 0, 1).cross(z) if abs(z.Z) < 0.9 else Vector(1, 0, 0)
    return frame(origin, z, x)


def placement(i, part):
    """Where a printed part's own (print) frame sits in the case: its placement in assembly.py."""
    origin, x_dir, z_dir = i.print_frames[part]
    return frame(origin, z_dir, x_dir)


def own(i, part, shape, connectors):
    """A printed part and its connectors, modelled in the case frame, moved into the part's own frame."""
    back = placement(i, part).inverse()
    moved = {name: [back * f for f in v] if isinstance(v, list) else back * v for name, v in connectors.items()}
    return back * shape, moved


def teardrop(c, axis, up, d, length, deg):
    """A round hole along `axis` centred on c, with a roof `deg` from vertical pointing `up` (the print's +Z) so it
    prints without support."""
    r = d / 2
    a, u = Vector(*axis), Vector(*up)
    cs, sn = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    roof = Polygon((0, 0), (r * cs, r * sn), (0, r / math.cos(math.radians(90 - deg))), (-r * cs, r * sn), align=None)
    shape = Cylinder(r, length) + extrude(Pos(0, 0, -length / 2) * roof, amount=length, dir=(0, 0, 1))
    return Plane(origin=c, x_dir=u.cross(a), z_dir=a) * shape


def wedge_boss(x0, x1, ya, yb, z_wall, zdir, h, down):
    """A boss h tall on the floor (zdir=+1) or the ceiling (zdir=-1), spanning x0..x1 and ya..yb, with a ~40°
    chamfer on the side that faces down when the shell prints (down=-1: -Y, +1: +Y)."""
    zt = z_wall + zdir * h
    c = 1.2 * h
    if down < 0:
        pts = [(yb, z_wall), (yb, zt), (ya, zt), (ya - c, z_wall)]
    else:
        pts = [(ya, z_wall), (ya, zt), (yb, zt), (yb + c, z_wall)]
    return extrude(Plane.YZ.offset(x0) * Polygon(*pts, align=None), amount=x1 - x0, dir=(1, 0, 0))


def fan_cutout(x, y, z, fan, spacing, thick, bar, hole_d):
    """A fan opening with vertical grille bars, and its four screw holes."""
    opening = cyl_y(x, y, z, fan - 4, thick + 2)
    pitch = 4 * bar
    n = int((fan - 4) / pitch)
    for k in range(-n // 2, n // 2 + 1):
        opening -= box(x + k * pitch - bar / 2, x + k * pitch + bar / 2, y - thick, y + thick, z - fan, z + fan)
    for dx in (-spacing / 2, spacing / 2):
        for dz in (-spacing / 2, spacing / 2):
            opening += cyl_y(x + dx, y, z + dz, hole_d, thick + 2)
    return opening


def fan_holes(x, y, z, spacing, out):
    """The fan screw holes' frames: at the panel's inner face, Z out of it into the case (`out`, ±1 along Y)."""
    return [
        axial((x + dx, y, z + dz), (0, out, 0))
        for dx in (-spacing / 2, spacing / 2)
        for dz in (-spacing / 2, spacing / 2)
    ]


def counterbore(x, y_open, y_tip, z, d_screw, deg):
    """A counterbore along +Y for a screw head, open from y_open, ending in a cone `deg` from the axis that narrows
    it to the screw's hole at y_tip (it prints as a ceiling)."""
    cb_r = (d_screw + 3) / 2
    cone_h = (cb_r - d_screw / 2) / math.tan(math.radians(deg))
    y_end = y_tip - cone_h
    bore = cyl_y(x, (y_open + y_end) / 2, z, 2 * cb_r, y_end - y_open)
    return bore + Pos(x, y_end, z) * Rot(-90, 0, 0) * Cone(
        cb_r, d_screw / 2, cone_h, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )


def countersink(c, axis, head_d, head_h, d_screw):
    """A countersink for a flat head of diameter head_d and height head_h, opening at c along -axis (into the
    part), as steep as the head and 0.2 mm wider."""
    slope = (head_d / 2 - d_screw / 2) / head_h
    r_top = head_d / 2 + 0.2
    depth = (r_top - d_screw / 2) / slope
    a = Vector(*axis)
    return Plane(origin=c, z_dir=-a) * Cone(r_top, d_screw / 2, depth, align=(Align.CENTER, Align.CENTER, Align.MIN))
