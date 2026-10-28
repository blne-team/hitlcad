"""The base (a plate under the cam, and a guide block the follower's rod slides in), the cam, cut from the lift table
for a flat-faced follower, and the follower: a foot, its face at its origin, and a rod along +X."""

import math

from build123d import (
    Align,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Cylinder,
    Location,
    Polyline,
    Pos,
    Rot,
    extrude,
    make_face,
)

HEIGHT = 8  # the cam's, and the follower's foot
UP = Rot(0, 90, 0)  # Z along +X
GUIDE_X = 45


def _profile(p, table):
    """The cam's outline, in its own frame: a flat face square to direction ψ, at the base circle plus the lift where
    the cam has turned θ = −ψ, touches it where its support is, so the outline is the envelope of those faces."""
    lifts = [lift * p["cut_scale"] for _, lift in table]
    n, points = len(lifts), []
    for k in range(n):
        psi = math.radians(-k)  # the follower, seen from the cam turned k degrees
        h = p["base_r"] + lifts[k]
        dh = -(lifts[(k + 1) % n] - lifts[k - 1]) / 2 / math.radians(1)  # d h / d ψ
        points.append((h * math.cos(psi) - dh * math.sin(psi), h * math.sin(psi) + dh * math.cos(psi)))
    return points


def build(p, i):
    on = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Pos(17, 0, -5) * Box(95, 60, 4, align=on)
    guide = Pos(GUIDE_X + 7.5, 0, -5) * Box(15, 20, 17, align=on) - Pos(GUIDE_X + 7.5, 0, 4) * UP * Cylinder(4.5, 20)
    with BuildPart() as cam:
        with BuildSketch():
            with BuildLine():
                Polyline(*_profile(p, i.lift_table), close=True)
            make_face()
        extrude(amount=HEIGHT)
    foot = Pos(1.5, 0, 0) * Box(3, 16, HEIGHT)
    rod = Pos(3, 0, 0) * UP * Cylinder(4, 40, align=on)
    return {
        "base": (plate + guide, {"axis": Location((0, 0, 0)), "guide": Location((GUIDE_X, 0, 4)) * UP}),
        "camshaft": (cam.part, {"axis": Location((0, 0, 0)) * Rot(180, 0, 0), "lobe": Location((0, 0, 0))}),
        "follower": (foot + rod, {"slide": Location((0, 0, 0)) * Rot(0, -90, 0), "face": Location((0, 0, 0)) * UP}),
    }
