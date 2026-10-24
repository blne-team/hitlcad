"""The impeller: flat blades on a hub, a set-screw boss on the shaft side. Printed hub-face down, so the
boss (where the shaft enters) is at the top and nothing overhangs: the blades lean 90° - blade_angle from
vertical, and each blade ends in a short vertical foot and cap instead of a knife edge."""

import math

from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Pos, Rot, add

from lib.printing import teardrop

ON = (Align.CENTER, Align.CENTER, Align.MIN)
FOOT = 1.0  # height of the vertical foot and cap at each end of a blade


def build(p, i):
    fh, bh, H = p["fan_h"], i.boss_h, i.impeller_h
    r_hub, r_tip = p["hub_d"] / 2, p["fan_d"] / 2
    beta = math.radians(p["blade_angle"])
    span = r_tip - r_hub + 1
    w = p["blade_t"] / math.sin(beta)  # the blade's width in a horizontal section

    def y_at(z):  # where the blade's centre plane crosses height z
        return (z - fh / 2) / math.tan(beta)

    with BuildPart() as blades:
        for k in range(p["blade_count"]):
            with Locations(Rot(0, 0, k * 360 / p["blade_count"]) * Pos(r_hub - 1 + span / 2, 0, 0)):
                with Locations(Pos(0, 0, fh / 2) * Rot(p["blade_angle"] - 90, 0, 0)):
                    Box(span, p["blade_t"], 3 * fh)
        with Locations(Pos(0, 0, FOOT)):
            Box(4 * r_tip, 4 * r_tip, fh - 2 * FOOT, align=ON, mode=Mode.INTERSECT)
        for k in range(p["blade_count"]):
            with Locations(Rot(0, 0, k * 360 / p["blade_count"]) * Pos(r_hub - 1 + span / 2, 0, 0)):
                with Locations(Pos(0, y_at(FOOT), 0)):
                    Box(span, w, FOOT, align=ON)
                with Locations(Pos(0, y_at(fh - FOOT), fh - FOOT)):
                    Box(span, w, FOOT, align=ON)
        Cylinder(r_tip, fh, align=ON, mode=Mode.INTERSECT)

    with BuildPart() as impeller:
        Cylinder(r_hub, fh, align=ON)
        with Locations(Pos(0, 0, fh)):
            Cylinder(p["boss_d"] / 2, bh, align=ON)
        add(blades.part)
        Cylinder(i.impeller_bore / 2, H, align=ON, mode=Mode.SUBTRACT)
        # radial hole for an M3 set screw: drill 2.5 mm, tap M3
        add(teardrop(Location((p["boss_d"] / 2, 0, fh + bh / 2), (0, 90, 0)), 2.5, p["boss_d"] / 2), mode=Mode.SUBTRACT)

    return {"impeller": (impeller.part, {"bore": Location((0, 0, H))})}
