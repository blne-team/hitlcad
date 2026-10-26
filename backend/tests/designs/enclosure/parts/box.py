from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Pos

from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, InsertHole, frames, heat_set_insert, screw

MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p, i):
    w, h, wall, t = p["width"], p["height"], p["wall"], p["lid_t"]
    ins = heat_set_insert(i.insert, "McMaster-Carr").native
    with BuildPart() as base:
        Box(w, w, h, align=MIN)
        with Locations(Pos(0, 0, wall)):
            Box(w - 2 * wall, w - 2 * wall, h, align=MIN, mode=Mode.SUBTRACT)
        for x, y in i.corners:
            with Locations(Pos(x, y, 0)):
                Cylinder(4, h, align=MIN)
        with Locations(*[Pos(x, y, h) for x, y in i.corners]):
            InsertHole(ins, depth=6)
    s = screw("SocketHeadCapScrew", "M3-0.5", 10, "iso4762").native
    with BuildPart() as lid:
        Box(w, w, t, align=MIN)
        with Locations(*[Pos(x, y, t) for x, y in i.corners]):
            ClearanceHole(s, fit="Normal", counter_sunk=False)
    return {
        "base": (base.part, {"top": Location((0, 0, h)), "inserts": frames(ins)}),
        "lid": (lid.part, {"bottom": Location((0, 0, 0), (180, 0, 0)), "screws": frames(s)}),
    }
