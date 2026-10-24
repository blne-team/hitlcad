"""The divider: the motherboard tray between the two chambers, spanning both shells and tying them together. It
prints flat, board side down: the flanges, the PSU ledge and the standoff pads stand up from it."""

from lib import joints
from lib.geometry import axial, box, cyl_x, frame, own, teardrop


def build(p, i):
    t, g, z_top = p["wall"], p["fit_gap"], i.z_top
    b, ins_d, ins_l, pad = i.boss, p["insert_d"], p["insert_depth"], i.pad
    x_div, xd0 = i.x_div, i.drive_x[0]
    y_d0, y_d1 = i.tray_y

    divider = box(x_div, x_div + t, y_d0, y_d1, t, z_top)
    for bottom in (True, False):
        z0, z1 = (t, t + i.foot_t) if bottom else (z_top - i.foot_t, z_top)
        divider += box(xd0, xd0 + b, y_d0, y_d1, z0, z1)
        for y in i.tray_screw_ys:
            zc = z0 + ins_l / 2 if bottom else z1 - ins_l / 2
            divider -= teardrop((i.tray_screw_x, y, zc), (0, 0, 1), (1, 0, 0), ins_d, ins_l, i.teardrop_deg)
    # PSU ledge
    divider += box(xd0, xd0 + b, i.case_l - t - p["psu_d"], y_d1, i.psu_z0 - 5, i.psu_z0)
    # Standoff inserts, through the divider and a pad behind it (no ceiling to print)
    for y, z in i.standoff_points:
        divider += cyl_x(xd0 + pad / 2, y, z, ins_d + 4, pad)
        divider -= cyl_x(x_div + (t + pad) / 2, y, z, ins_d, t + pad + 2)
    divider &= box(x_div - 1, xd0 + b + 1, y_d0, y_d1 - g, t + g, z_top - g)

    ins_len = i.insert_len
    connectors = {
        "bottom": frame((x_div + (t + b) / 2, (y_d0 + y_d1) / 2, t + g), (0, 0, -1), (1, 0, 0)),
        "front_floor_inserts": joints.inserts(i.tray_front_floor_joint, ins_len),
        "front_ceiling_inserts": joints.inserts(i.tray_front_ceiling_joint, ins_len),
        "rear_floor_inserts": joints.inserts(i.tray_rear_floor_joint, ins_len),
        "rear_ceiling_inserts": joints.inserts(i.tray_rear_ceiling_joint, ins_len),
        "standoff_inserts": [axial((x_div + ins_len, y, z), (-1, 0, 0)) for y, z in i.standoff_points],
    }
    return {"divider": own(i, "divider", divider, connectors)}
