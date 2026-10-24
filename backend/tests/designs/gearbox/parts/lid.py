"""The lid: closes the housing and holds the output bearing; four screws into the housing's inserts."""

from build123d import Align, BuildPart, Cylinder, Location, Locations, Mode, Pos

from hitlcad.catalogs.bd_warehouse import ClearanceHole, bearing, frames, screw

ON = (Align.CENTER, Align.CENTER, Align.MIN)
UNDER = (Align.CENTER, Align.CENTER, Align.MAX)


def build(p, i):
    t = p["lid_thickness"]
    seat = bearing("SingleRowDeepGrooveBallBearing", i.bearing, "SKT").metadata
    if seat["width"] >= t - p["min_wall"]:
        raise ValueError("the lid is too thin for the bearing pocket")
    lid_screw = screw("SocketHeadCapScrew", "M3-0.5", i.lid_screw_length, "iso4762").native
    with BuildPart() as lid:
        Cylinder(p["housing_od"] / 2, t, align=ON)
        with Locations(Pos(0, 0, t)):
            Cylinder(seat["outside"] / 2, seat["width"], align=UNDER, mode=Mode.SUBTRACT)
        Cylinder(p["output_d"] / 2 + 1, t, align=ON, mode=Mode.SUBTRACT)
        with Locations(*[Pos(x, y, t) for x, y in i.lid_bolt_xy]):
            ClearanceHole(lid_screw, fit="Normal", counter_sunk=False)
    return {
        "lid": (
            lid.part,
            {
                "bottom": Location((0, 0, 0), (180, 0, 0)),
                "screws": frames(lid_screw),
                "bearing_pocket": Location((0, 0, t)),
            },
        )
    }
