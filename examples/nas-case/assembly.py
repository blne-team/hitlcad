"""Where every instance sits in the case frame (see interfaces.py). Printed parts sit at their print frames; the
hardware follows the joints' frames with mate_to, the way its rigid mates and fastener sets check it."""

from build123d import Location, Pos, Rot
from lib import joints
from lib.geometry import axial, frame, placement

from hitlcad.catalogs.bd_warehouse import heat_set_insert
from hitlcad.sdk import mate_to

PRINTED = [
    "front_shell",
    "rear_shell",
    "divider",
    "hdd_rail_left",
    "hdd_rail_right",
    "side_left_rear",
    "side_right_rear",
]
JOINTS = [
    "seam",
    "side_front_left",
    "side_front_right",
    "side_left_rear",
    "side_right_rear",
    "tray_front_floor",
    "tray_front_ceiling",
    "tray_rear_floor",
    "tray_rear_ceiling",
    "rail_front",
    "rail_rear",
]


def build(p, i):
    t, L, H, W = p["wall"], i.case_l, i.case_h, i.case_w
    fitting = heat_set_insert(*i.insert_size).connectors[
        "placement"
    ]  # every bd_warehouse part's own origin, facing out

    out = {name: [placement(i, name)] for name in PRINTED}
    # the front side panels: the left one, and the same part turned over about Y on the right
    side = placement(i, "side_front")
    out["side_front"] = [side, Pos(W, 0, H) * Rot(0, 180, 0) * side]

    for j in JOINTS:
        joint = getattr(i, f"{j}_joint")
        out[f"{j}_screw"] = [mate_to(fitting, f) for f in joints.seats(joint)]
        out[f"{j}_insert"] = [mate_to(fitting, f) for f in joints.inserts(joint, i.insert_len)]

    # the board on its standoffs, screwed into inserts flush with the divider's board side
    standoffs = [(i.x_div, y, z) for y, z in i.standoff_points]
    out["standoff_insert"] = [mate_to(fitting, axial((x + i.insert_len, y, z), (-1, 0, 0))) for x, y, z in standoffs]
    seat = Location((0, 0, 0), (180, 0, 0))
    out["standoff"] = [mate_to(seat, axial(o, (-1, 0, 0))) for o in standoffs]
    out["motherboard"] = [frame((i.board_back_x, i.board_rear_y, i.board_top_z), (-1, 0, 0), (0, -1, 0))]

    out["hdd"] = [Pos(i.xdc, i.cage_y[0], z) for z in i.z_drives]
    out["psu"] = [Pos(i.psu_x0 + p["psu_w"] / 2, L - t, i.psu_z0)]
    out["fan"] = [
        frame((i.xdc, t, i.z_fan), (0, 1, 0), (1, 0, 0)),
        frame((i.xdc, L - t, i.z_fan), (0, -1, 0), (1, 0, 0)),
    ]
    return out
