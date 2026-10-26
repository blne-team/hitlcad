"""The housing: a cup with the ring gear cut inside, the NEMA 17 face below and the lid inserts on top."""

import math

from build123d import Align, BuildPart, Cylinder, Face, Location, Locations, Mode, Pos, Rot, extrude
from lib.gear import gear_outline

from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, InsertHole, frames, heat_set_insert, screw

ON = (Align.CENTER, Align.CENTER, Align.MIN)
DOWN = (180, 0, 0)


def build(p, i):
    m, pa, n, bl = p["module"], math.radians(p["pressure_angle"]), p["flank_points"], p["backlash"]
    zr, floor, height, od = i.ring_teeth, p["floor"], i.housing_height, p["housing_od"]
    insert_part = heat_set_insert(i.insert, "McMaster-Carr")
    if od / 2 - i.ring_root_r < insert_part.metadata["outside_diameter"] + 2 * p["min_wall"]:
        raise ValueError(f"housing_od {od} leaves no room for the lid inserts outside the ring gear")
    insert = insert_part.native
    motor_screw = screw("SocketHeadCapScrew", "M3-0.5", i.motor_screw_length, "iso4762").native
    ring_space = math.pi * m / 2 + bl / 2  # the ring's tooth space is an external tooth of the same base circle
    cutter = gear_outline(zr, m, pa, ring_space, i.ring_root_r, m * zr / 2 - m, n)
    with BuildPart() as housing:
        Cylinder(od / 2, height, align=ON)
        extrude(Pos(0, 0, floor) * Rot(0, 0, i.ring_phase_deg) * Face(cutter), height, mode=Mode.SUBTRACT)
        Cylinder(p["pilot_d"] / 2, p["pilot_depth"], align=ON, mode=Mode.SUBTRACT)
        Cylinder(p["motor_clearance_d"] / 2, floor, align=ON, mode=Mode.SUBTRACT)
        with Locations(*[Pos(x, y, floor) for x, y in i.nema_xy]):
            ClearanceHole(motor_screw, fit="Normal", depth=floor)
        with Locations(*[Pos(x, y, height) for x, y in i.lid_bolt_xy]):
            InsertHole(insert, depth=p["insert_depth"])
    return {
        "housing": (
            housing.part,
            {
                "axis": Location((0, 0, i.z_carrier)),
                "motor_face": Location((0, 0, 0), DOWN),
                "motor_pilot": Location((0, 0, 0), DOWN),
                "motor_screws": frames(motor_screw),
                "ring": Location((0, 0, i.z_gear)),
                "inserts": frames(insert),
                "top": Location((0, 0, height)),
            },
        )
    }
