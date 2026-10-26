"""The motor mount: a bulkhead the motor bolts under, a collar around the motor's nose, and straightening
vanes out to a ring that slides into the duct and sits on its shoulder. Printed bulkhead-down (its upstream
face, under the screw heads, on the bed): every wall rises straight from it. Reprint it for another motor."""

from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Pos, Rot

from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, frames, screw

ON = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p, i):
    h, t, n = p["collar_len"], p["bulkhead_t"], p["vane_count"]
    if h < t + 5:
        raise ValueError("the motor collar must reach at least 5 mm past the bulkhead")
    motor_screw = screw("SocketHeadCapScrew", "M3-0.5", i.motor_screw_len, "iso4762").native
    with BuildPart() as mount:
        Cylinder(i.mount_od / 2, h, align=ON)
        Cylinder(i.duct_id / 2, h, align=ON, mode=Mode.SUBTRACT)
        Cylinder(i.collar_od / 2, h, align=ON)
        with Locations(Pos(0, 0, t)):
            Cylinder(i.collar_id / 2, h - t, align=ON, mode=Mode.SUBTRACT)
        Cylinder(p["shaft_hole_d"] / 2, t, align=ON, mode=Mode.SUBTRACT)
        r0, r1 = i.collar_od / 2 - 0.5, i.duct_id / 2 + 0.5
        for k in range(n):
            with Locations(Rot(0, 0, 180 / n + k * 360 / n) * Pos((r0 + r1) / 2, 0, 0)):
                Box(r1 - r0, p["vane_t"], h, align=ON)
        with Locations(*[Location((x, y, 0), (180, 0, 0)) for x, y in i.motor_bolt_xy]):
            ClearanceHole(motor_screw, fit="Normal", counter_sunk=False)

    return {
        "motor_mount": (
            mount.part,
            {
                "seat": Location((0, 0, 0), (180, 0, 0)),
                "ring": Location((0, 0, 0)),
                "motor_face": Location((0, 0, t)),
                "motor_collar": Location((0, 0, h)),
                "motor_screws": frames(motor_screw),
            },
        )
    }
