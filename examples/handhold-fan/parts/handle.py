"""The pistol grip and its end cap. The grip prints upside down: the saddle that bolts to the duct pad is at
Z=0 and the ESC bay opens at the top, where the cap closes it. X points forward (towards the nozzle)."""

from build123d import Align, Axis, Box, BuildPart, Cylinder, Location, Locations, Mode, Pos, add, fillet

from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, InsertHole, frames, heat_set_insert, screw

from lib.printing import teardrop

ON = (Align.CENTER, Align.CENTER, Align.MIN)
RIB_W = 10
CABLE_X = -12  # the power cable leaves behind the ESC


def corner_r(p):
    return min(10, p["handle_w"] / 2 - 2)


def build(p, i):
    H, D, W, wall = p["handle_len"], p["handle_depth"], p["handle_w"], p["handle_wall"]
    top = i.handle_top_t
    cd, cw = i.handle_cavity
    handle_screw = screw("SocketHeadCapScrew", "M3-0.5", i.handle_screw_len, "iso4762").native
    cap_screw = screw("SocketHeadCapScrew", "M3-0.5", i.cap_screw_len, "iso4762").native
    ins = heat_set_insert(i.insert_size, "CNCKitchen").native

    with BuildPart() as handle:
        Box(D, W, H, align=ON)
        fillet(handle.edges().filter_by(Axis.Z), corner_r(p))
        with Locations(Pos(0, 0, top)):
            Box(cd, cw, H - top, align=ON, mode=Mode.SUBTRACT)
        for s in (-1, 1):
            with Locations(Pos(0, s * (cw / 2 - i.rib_t / 2), top)):
                Box(RIB_W, i.rib_t, H - top, align=ON)
        Cylinder(p["wire_hole_d"] / 2, top, align=ON, mode=Mode.SUBTRACT)
        with Locations(*[Pos(x, y, top) for x, y in i.handle_screw_xy]):
            ClearanceHole(handle_screw, fit="Normal", counter_sunk=False)
        with Locations(*[Pos(x, y, H) for x, y in i.cap_screw_xy]):
            InsertHole(ins, depth=i.insert_hole_depth)
        # switch or throttle pot under the index finger
        add(teardrop(Location((D / 2, 0, top + p["switch_d"] / 2 + 10), (0, 90, 0)), p["switch_d"], wall + 1), mode=Mode.SUBTRACT)

    with BuildPart() as cap:
        Box(D, W, p["cap_t"], align=ON)
        fillet(cap.edges().filter_by(Axis.Z), corner_r(p))
        with Locations(*[Location((x, -y, 0), (180, 0, 0)) for x, y in i.cap_screw_xy]):
            ClearanceHole(cap_screw, fit="Normal", counter_sunk=False)
        with Locations(Pos(CABLE_X, 0, 0)):
            Cylinder(p["cable_d"] / 2, p["cap_t"], align=ON, mode=Mode.SUBTRACT)

    return {
        "handle": (
            handle.part,
            {
                "saddle": Location((0, 0, 0), (180, 0, 0)),
                "screws": frames(handle_screw),
                "bottom": Location((0, 0, H)),
                "cap_inserts": frames(ins),
            },
        ),
        "grip_cap": (
            cap.part,
            {"top": Location((0, 0, p["cap_t"])), "screws": frames(cap_screw)},
        ),
    }
