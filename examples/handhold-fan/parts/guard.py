"""The intake guard: a grille of rings and spokes with a skirt that slips over the duct's turned-down
intake end. Printed grille down."""

import math

from build123d import Align, Box, Cylinder, Location, Pos, Rot

ON = (Align.CENTER, Align.CENTER, Align.MIN)
SPOKES = 8
SKIRT_WALL = 2.4
SKIRT_GAP = 0.3  # the skirt stops short of the duct's shoulder


def ring(r_in, r_out, h):
    return Cylinder(r_out, h, align=ON) - Cylinder(r_in, h, align=ON)


def build(p, i):
    t, rib, gap = p["guard_t"], p["guard_rib"], p["guard_opening"]
    r_open, r_bore = i.duct_id / 2, i.guard_bore / 2
    skirt = p["guard_skirt"] - SKIRT_GAP
    r_hub = gap
    # the seat (on the duct's end) and the skirt
    guard = ring(r_open, r_bore + SKIRT_WALL, t) + Pos(0, 0, t) * ring(r_bore, r_bore + SKIRT_WALL, skirt)
    # a solid centre, then rings no more than `gap` apart out to the open edge
    guard += Cylinder(r_hub, t, align=ON)
    rings = math.ceil((r_open - r_hub) / (gap + rib)) - 1
    step = (r_open - r_hub) / (rings + 1)
    for k in range(1, rings + 1):
        r = r_hub + k * step
        guard += ring(r - rib / 2, r + rib / 2, t)
    for k in range(SPOKES):
        guard += Rot(0, 0, k * 360 / SPOKES) * Box(2 * r_open + 1, rib, t, align=ON)

    return {
        "intake_guard": (
            guard,
            {
                "seat": Location((0, 0, t)),
                "socket": Location((0, 0, t + skirt)),
            },
        )
    }
