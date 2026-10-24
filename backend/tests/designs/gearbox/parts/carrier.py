"""The carrier: a plate with the planet pins below it and the output shaft above."""

from build123d import Align, Cylinder, Location, Pos

ON = (Align.CENTER, Align.CENTER, Align.MIN)
UNDER = (Align.CENTER, Align.CENTER, Align.MAX)
DOWN = (180, 0, 0)


def build(p, i):
    t, fw, gap, pin_r = p["carrier_thickness"], p["face_width"], p["axial_gap"], p["pin_d"] / 2
    plate_r = i.centre_distance + pin_r + 2 * p["min_wall"]
    if plate_r >= p["module"] * i.ring_teeth / 2 - p["module"] - gap:
        raise ValueError("the carrier plate would touch the ring gear's teeth")
    carrier = Cylinder(plate_r, t, align=ON)
    pins = [(x, y) for x, y, _ in i.planet_poses]
    for x, y in pins:
        carrier += Pos(x, y, 0) * Cylinder(pin_r, fw + gap, align=UNDER)
    shaft = i.housing_height + p["lid_thickness"] + p["output_length"] - i.z_carrier - t
    carrier += Pos(0, 0, t) * Cylinder(p["output_d"] / 2, shaft, align=ON)
    reach = i.motor["boss_h"] + i.motor["shaft_length"] - i.z_carrier + gap  # the motor shaft runs on into the carrier
    if reach > 0:
        carrier -= Cylinder(p["shaft_d"] / 2 + gap, reach, align=ON)
    return {
        "carrier": (
            carrier,
            {
                "pins": [Location((x, y, 0), DOWN) for x, y in pins],
                "axis": Location((0, 0, 0), DOWN),
                "output": Location((0, 0, t)),
            },
        )
    }
