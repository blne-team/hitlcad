"""The drive rails: two mirror-image plates that hold the drives by their side screw holes, with a rib under each
drive above the first and a foot screwed to the floor. Each prints flat, outer face down."""

from lib import joints
from lib.geometry import box, cyl_x, frame, own, teardrop

DRIVE_SCREW_HOLE = 3.5  # 6-32 UNC drive screws through the plates


def _one_side(joint, side, xdc):
    """A rail joint's stack on this rail."""
    return {**joint, "stacks": [st for st in joint["stacks"] if (st[0] < xdc) == (side == "left")]}


def build(p, i):
    t, g, H = p["wall"], p["fit_gap"], i.case_h
    hdd_w, hdd_l, hdd_h = i.hdd_size
    clear = i.hdd_clear
    cx0, cx1 = i.cage_x
    y_c0, y_c1 = i.cage_y
    holes = i.hdd_side_holes
    out = {}
    for side, sgn, x_out in (("left", 1, cx0), ("right", -1, cx1)):
        x_in = x_out + sgn * t
        lo, hi = min(x_out, x_in), max(x_out, x_in)
        plate = box(lo, hi, y_c0, y_c1, t, i.cage_top)
        plate += box(min(x_in, x_in + sgn * i.foot_w), max(x_in, x_in + sgn * i.foot_w), y_c0, y_c1, t, t + i.foot_t)
        for z_drive in i.z_drives[1:]:
            rib = (min(x_in, x_in + sgn * p["rail_w"]), max(x_in, x_in + sgn * p["rail_w"]))
            plate += box(*rib, y_c0, y_c1, z_drive - clear - t, z_drive - clear)
        for z_drive in i.z_drives:
            for hy in holes["y"]:
                plate -= cyl_x((lo + hi) / 2, y_c0 + hy, z_drive + holes["z"], DRIVE_SCREW_HOLE, t + 2)
        for s, x, y in i.rail_feet:
            if s == side:
                plate -= teardrop(
                    (x, y, t + p["insert_depth"] / 2),
                    (0, 0, 1),
                    (sgn, 0, 0),
                    p["insert_d"],
                    p["insert_depth"],
                    i.teardrop_deg,
                )
        plate &= box(cx0 - 1, cx1 + 1, y_c0 - 1, y_c1 + 1, t + g, H)

        name = f"hdd_rail_{side}"
        connectors = {
            "bottom": frame((x_out + sgn * (t + i.foot_w) / 2, (y_c0 + y_c1) / 2, t + g), (0, 0, -1), (1, 0, 0)),
            "bays": [
                frame((x_in + sgn * clear, y_c0 + hdd_l / 2, z + hdd_h / 2), (sgn, 0, 0), (0, 1, 0)) for z in i.z_drives
            ],
            "front_insert": joints.inserts(_one_side(i.rail_front_joint, side, i.xdc), i.insert_len)[0],
            "rear_insert": joints.inserts(_one_side(i.rail_rear_joint, side, i.xdc), i.insert_len)[0],
        }
        out[name] = own(i, name, plate, connectors)
    return out
