"""The 750 W brushless motor, simplified: its can, the shaft and the four M3 holes in its shaft face (Z=0)."""

from build123d import Align, BuildPart, Cylinder, Location, Locations, Mode, Pos

ON = (Align.CENTER, Align.CENTER, Align.MIN)
UNDER = (Align.CENTER, Align.CENTER, Align.MAX)


def build(p, i):
    with BuildPart() as motor:
        Cylinder(p["motor_d"] / 2, p["motor_len"], align=UNDER)
        Cylinder(p["shaft_d"] / 2, p["shaft_len"], align=ON)
        with Locations(*[Pos(x, y, 0) for x, y in i.motor_bolt_xy]):
            Cylinder(1.25, p["motor_tap_depth"], align=UNDER, mode=Mode.SUBTRACT)
    return {
        "motor": (
            motor.part,
            {
                "face": Location((0, 0, 0)),
                "can": Location((0, 0, 0), (180, 0, 0)),
                "shaft": Location((0, 0, 0)),
                "screw_holes": [Location((x, y, 0)) for x, y in i.motor_bolt_xy],
            },
        )
    }
