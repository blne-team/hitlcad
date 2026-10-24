"""What the case holds, as simplified envelopes: the motherboard, the 3.5" drives, the ATX power supply, the two
120 mm fans, and the brass standoffs the board sits on."""

import math

from build123d import Align, Box, Cylinder, Location, Pos, RegularPolygon, extrude
from lib.geometry import axial, box, frame

ON = (Align.CENTER, Align.CENTER, Align.MIN)
STANDOFF_HEX = 5.0  # across flats, M3 brass standoff
STUD = (3.0, 4.0)  # its male thread: diameter, length
DRIVE_THREAD = (2.8, 4.0)  # the drives' 6-32 side holes, simplified: diameter, depth


def build(p, i):
    X, Z = (1, 0, 0), (0, 0, 1)

    # Motherboard: in its own frame X runs forward from the rear I/O edge, Y up from the top edge (so the board
    # lies at negative Y), Z out of the component side; the standoff pads are on its back
    m, _, pcb_t = i.board_size
    board = box(0, m, -m, 0, 0, pcb_t)
    board_conn = {"standoffs": [frame((u, -v, 0), (0, 0, -1), X) for u, v in i.matx_holes]}

    # Drive: origin at the middle of its front edge, on its bottom
    w, length, h = i.hdd_size
    hdd = box(-w / 2, w / 2, 0, length, 0, h)
    holes = i.hdd_side_holes
    for x in (-w / 2, w / 2):
        for y in holes["y"]:
            hole = (
                Pos(x, y, holes["z"])
                * Location((0, 0, 0), (0, 90, 0))
                * Cylinder(DRIVE_THREAD[0] / 2, 2 * DRIVE_THREAD[1])
            )
            hdd -= hole
    hdd_conn = {
        "left": frame((-w / 2, length / 2, h / 2), (-1, 0, 0), (0, 1, 0)),
        "right": frame((w / 2, length / 2, h / 2), X, (0, 1, 0)),
    }

    # PSU: origin at the middle of its back's bottom edge, the body forward of it (-Y)
    pw, pd, ph = p["psu_w"], p["psu_d"], p["psu_h"]
    psu = box(-pw / 2, pw / 2, -pd, 0, 0, ph)
    psu -= Pos(0, -pd / 2, ph) * Cylinder(min(pw, pd) * 0.4, 2)  # its fan, on top
    psu_conn = {"bottom": frame((0, -i.psu_ledge / 2, 0), (0, 0, -1), X)}

    # Fan: lying on its intake face at Z=0
    f, ft, sp = i.fan, p["fan_thickness"], i.fan_hole_spacing
    fan = Box(f, f, ft, align=ON) - Cylinder((f - 6) / 2, ft, align=ON)
    corners = [(dx, dy) for dx in (-sp / 2, sp / 2) for dy in (-sp / 2, sp / 2)]
    for dx, dy in corners:
        fan -= Pos(dx, dy, 0) * Cylinder(i.fan_hole_d / 2, ft, align=ON)
    fan_conn = {"holes": [axial((dx, dy, 0), (0, 0, -1)) for dx, dy in corners]}

    # Standoff: hex body from Z=0 up, the stud below
    so_h = p["standoff_h"]
    standoff = extrude(RegularPolygon(STANDOFF_HEX / math.sqrt(3), 6), amount=so_h)
    standoff += Cylinder(STUD[0] / 2, STUD[1], align=(Align.CENTER, Align.CENTER, Align.MAX))
    standoff_conn = {"seat": frame((0, 0, 0), (0, 0, -1), X), "top": frame((0, 0, so_h), Z, X)}

    return {
        "motherboard": (board, board_conn),
        "hdd": (hdd, hdd_conn),
        "psu": (psu, psu_conn),
        "fan": (fan, fan_conn),
        "standoff": (standoff, standoff_conn),
    }
