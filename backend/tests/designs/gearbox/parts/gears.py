"""The sun and the planets: one gear set, one parametric history."""

import math

from build123d import Align, Cylinder, Location
from lib.gear import d_bore, spur

DOWN = (180, 0, 0)


def build(p, i):
    m, bl, fw, n = p["module"], p["backlash"], p["face_width"], p["flank_points"]
    pa = math.radians(p["pressure_angle"])
    sun = spur(p["sun_teeth"], m, pa, bl, fw, n) - d_bore(p["shaft_d"], p["shaft_flat"], p["shaft_clearance"], fw)
    planet = spur(p["planet_teeth"], m, pa, bl, fw, n)
    planet -= Cylinder(p["pin_d"] / 2 + p["pin_clearance"], fw, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {
        "sun": (sun, {"bore": Location((0, 0, 0), DOWN), "axis": Location(), "teeth": Location()}),
        "planet": (planet, {"bore": Location((0, 0, fw)), "teeth": Location()}),
    }
