"""The base with its knuckle (and a stop post), and the lid with its pin between two knuckles of its own."""

from build123d import Align, Box, Cylinder, Pos, Rot

ON = (Align.CENTER, Align.CENTER, Align.MIN)


def _along_x(x0, x1, y, z, r):
    """A cylinder along X from x0 to x1."""
    return Pos((x0 + x1) / 2, y, z) * Rot(0, 90, 0) * Cylinder(r, x1 - x0)


def build(p, i):
    s, h, t, d, gap = p["size"], p["height"], p["lid_t"], p["pin_d"], p["knuckle_gap"]
    y, z, k = i.hinge_y, i.hinge_z, i.knuckle
    base = Box(s, s, h, align=ON)
    base += _along_x(-k, k, y, z, d) + Pos(0, (y + s / 2) / 2, z / 2) * Box(2 * k, y - s / 2, z)
    base -= _along_x(-k - 1, k + 1, y, z, d / 2 + p["pin_clearance"] / 2)
    if p["stop_h"] > 0:
        base += Pos(0, y + 2 * d + 1, 0) * Box(s / 2, 2, h + p["stop_h"], align=ON)
    lid = Pos(0, 0, h) * Box(s, s, t, align=ON)
    for x0, x1 in ((k + gap, s / 2), (-s / 2, -k - gap)):
        lid += _along_x(x0, x1, y, z, d) + Pos((x0 + x1) / 2, (y + s / 2) / 2, z) * Box(x1 - x0, y - s / 2, t)
    lid += _along_x(-k - gap, k + gap, y, z, d / 2)
    hinge = Pos(k, y, z) * Rot(0, 90, 0)  # the knuckle hole's opening, Z along +X out of it
    pin = Pos(k + gap, y, z) * Rot(0, 90, 0) * Rot(180, 0, 0)  # the pin's shoulder, facing the hole
    foot = Pos(0, 0, 0) * Rot(180, 0, 0)  # the bottom face, Z down out of it
    return {"base": (base, {"hinge": hinge, "foot": foot}), "lid": (lid, {"pin": pin})}
