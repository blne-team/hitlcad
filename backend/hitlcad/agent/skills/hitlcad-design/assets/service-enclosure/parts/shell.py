"""One file can produce related parts; each stays in its own manufacturing frame."""

from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Pos

from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, InsertHole, frames, heat_set_insert, screw

ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p, i):
    width, depth, height = p["width"], p["depth"], p["height"]
    insert = heat_set_insert(i.insert_size, "McMaster-Carr").native
    fastener = screw("SocketHeadCapScrew", "M3-0.5", i.screw_length, "iso4762").native
    # Catalog construction can add geometry to an active builder; resolve this read first.
    hole_depth = i.insert_hole_depth

    with BuildPart() as base:
        Box(width, depth, height, align=ON_BED)
        with Locations(Pos(0, 0, i.wall)):
            Box(width - 2 * i.wall, depth - 2 * i.wall, height, align=ON_BED, mode=Mode.SUBTRACT)
        for x, y in i.bolt_xy:
            with Locations(Pos(x, y, 0)):
                Cylinder(i.boss_radius, height, align=ON_BED)
        with Locations(*[Pos(x, y, height) for x, y in i.bolt_xy]):
            InsertHole(insert, depth=hole_depth)

    with BuildPart() as lid:
        Box(width, depth, i.lid_thickness, align=ON_BED)
        with Locations(*[Pos(x, y, i.lid_thickness) for x, y in i.bolt_xy]):
            ClearanceHole(fastener, fit="Normal", counter_sunk=False)
        for x in i.vent_centers:
            with Locations(Pos(x, 0, 0)):
                Box(i.vent_width, i.vent_length, i.lid_thickness, align=ON_BED, mode=Mode.SUBTRACT)

    return {
        "base": (base.part, {"top": Location((0, 0, height)), "inserts": frames(insert)}),
        "lid": (lid.part, {"bottom": Location((0, 0, 0), (180, 0, 0)), "screws": frames(fastener)}),
    }
