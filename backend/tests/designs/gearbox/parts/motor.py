"""A NEMA 17 stepper, simplified: its body with pilot boss and the four tapped holes of its face, and its rotor, the
D-shaft that turns in it."""

from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Pos

ON = (Align.CENTER, Align.CENTER, Align.MIN)
UNDER = (Align.CENTER, Align.CENTER, Align.MAX)


def build(p, i):
    d, flat, m = p["shaft_d"], p["shaft_flat"], i.motor
    with BuildPart() as motor:
        Box(m["body"], m["body"], m["length"], align=UNDER)
        Cylinder(m["boss_d"] / 2, m["boss_h"], align=ON)
        with Locations(*[Pos(x, y, 0) for x, y in i.nema_xy]):
            Cylinder(m["tap_d"] / 2, m["tap_depth"], align=UNDER, mode=Mode.SUBTRACT)
    with BuildPart() as rotor:
        Cylinder(d / 2, m["shaft_length"], align=ON)
        with Locations(Pos(flat, 0, m["shaft_length"] / 2)):
            Box(d, d, m["shaft_length"], mode=Mode.SUBTRACT)  # the flat: the shaft is `flat` across from it
    return {
        "motor": (
            motor.part,
            {
                "face": Location((0, 0, 0)),
                "pilot": Location((0, 0, 0)),
                "spindle": Location((0, 0, m["boss_h"])),
                "screw_holes": [Location((x, y, 0)) for x, y in i.nema_xy],
            },
        ),
        "rotor": (rotor.part, {"axis": Location((0, 0, 0), (180, 0, 0)), "shaft": Location((0, 0, 0))}),
    }
