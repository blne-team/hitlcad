"""The front and rear shells: each a panel, the floor and the ceiling up to the seam, printed panel-down so the
bosses' 40° chamfers, the teardrop holes and the counterbore cones need no support. Seam screws run from the
front shell's bosses into inserts in the rear shell's; the side panels, the divider and the rails screw to them."""

import math

from build123d import Polygon, Pos, extrude
from lib import joints
from lib.geometry import box, counterbore, cyl_y, fan_cutout, fan_holes, frame, own, teardrop, wedge_boss


def build(p, i):
    t, g, s = p["wall"], p["fit_gap"], i.seam_y
    W, L, H, z_top = i.case_w, i.case_l, i.case_h, i.z_top
    b, ins_d, ins_l, sd, deg = i.boss, p["insert_d"], p["insert_depth"], p["screw_d"], i.teardrop_deg
    xd0, xd1 = i.drive_x
    X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)

    def shell_base(y0, y1):
        return box(t, W - t, y0, y1, 0, t) + box(t, W - t, y0, y1, z_top, H)

    front = shell_base(0, s) + box(t, W - t, 0, t, t, z_top)
    rear = shell_base(s, L) + box(t, W - t, L - t, L, t, z_top)

    # Front: intake fan, power button
    front -= fan_cutout(i.xdc, t / 2, i.z_fan, i.fan, i.fan_hole_spacing, t, t, i.fan_hole_d)
    front -= cyl_y(t + i.board_chamber / 2, t / 2, z_top - 25, p["button_d"], t + 2)

    # Rear: exhaust fan, PSU opening and ledge, I/O aperture, 4 low-profile slots
    rear -= fan_cutout(i.xdc, L - t / 2, i.z_fan, i.fan, i.fan_hole_spacing, t, t, i.fan_hole_d)
    px, pz, pw, ph = i.psu_x0, i.psu_z0, p["psu_w"], p["psu_h"]
    rear -= box(px + 6, px + pw - 6, L - t - 1, L + 1, pz + 5, pz + ph - 5)
    rear += box(px + 10, px + pw - 10, L - t - i.psu_ledge, L - t, pz - 5, pz)
    x_pcb = i.board_front_x
    io_z1 = i.board_top_z - 3
    io_z0 = io_z1 - 158.75
    rear -= box(x_pcb + 2.24 - 44.45, x_pcb + 2.24, L - t - 1, L + 1, io_z0, io_z1)
    slot_x0 = max(t + 4, x_pcb - 100)
    for k in range(4):
        zc = io_z0 - 12 - k * 20.32
        rear -= box(slot_x0, x_pcb - 3, L - t - 1, L + 1, zc - 6, zc + 6)
    # Rear top: vents over the PSU, pointed at the end that is up when the rear shell prints
    vw = p["vent_slot_w"]
    for k in range(int((pw - 20) // (2 * vw))):
        x = px + 10 + vw / 2 + k * 2 * vw
        y0 = L - t - p["psu_d"] + 25
        rear -= box(x - vw / 2, x + vw / 2, y0, L - t - 25, z_top - 1, H + 1)
        tip = Polygon((x - vw / 2, y0), (x + vw / 2, y0), (x, y0 - vw / 2 / math.tan(math.radians(deg))), align=None)
        rear -= extrude(Pos(0, 0, z_top - 1) * tip, amount=t + 2, dir=(0, 0, 1))

    # Seam bosses: screws from the front shell (counterbored, the head on a cone) into inserts in the rear shell
    for xc in i.seam_boss_x:
        bx0, bx1 = xc - b / 2, xc + b / 2
        for zc in i.seam_boss_z:
            z_wall, zdir = (t, 1) if zc < H / 2 else (z_top, -1)
            front += wedge_boss(bx0, bx1, s - b, s, z_wall, zdir, b, -1)
            front -= cyl_y(xc, s - b, zc, sd, 2.5 * b + 2)
            front -= counterbore(xc, s - 2.5 * b - 1, s - i.seam_under_head, zc, sd, deg)
            rear += wedge_boss(bx0, bx1, s, s + b, z_wall, zdir, b, 1)
            rear -= cyl_y(xc, s + ins_l / 2, zc, ins_d, ins_l)

    # Side-panel bosses: inserts along X in the floor and ceiling corners
    for side, shell, y, zc in i.side_holes:
        down = -1 if shell == "front" else 1
        bx0, bx1 = (t, t + b) if side == "left" else (xd1 - b, xd1)
        x_ins = t + ins_l / 2 if side == "left" else xd1 - ins_l / 2
        z_wall, zdir = (t, 1) if zc < H / 2 else (z_top, -1)
        boss = wedge_boss(bx0, bx1, y - b / 2, y + b / 2, z_wall, zdir, b, down)
        boss -= teardrop((x_ins, y, zc), X, (0, -down, 0), ins_d, ins_l, deg)
        if shell == "front":
            front += boss
        else:
            rear += boss

    # Screw holes through the floor and ceiling for the divider's flanges and the rails' feet
    def floor_hole(x, y, hz):
        if y < s:
            return "front", teardrop((x, y, hz), Z, Y, sd, t + 2, deg)
        return "rear", teardrop((x, y, hz), Z, (0, -1, 0), sd, t + 2, deg)

    holes = [floor_hole(i.tray_screw_x, y, hz) for y in i.tray_screw_ys for hz in (t / 2, z_top + t / 2)]
    holes += [floor_hole(x, y, t / 2) for _, x, y in i.rail_feet]
    for shell, hole in holes:
        if shell == "front":
            front -= hole
        else:
            rear -= hole

    # Fit gaps to the side panels and between the shells
    front &= box(t + g, W - t - g, -1, s - g / 2, -1, H + 1)
    rear &= box(t + g, W - t - g, s + g / 2, L + 1, -1, H + 1)

    ins_len = i.insert_len
    yd_mid = sum(i.tray_y) / 2
    yc = sum(i.cage_y) / 2
    rail_mid = (t + i.foot_w) / 2
    cx0, cx1 = i.cage_x
    front_conn = {
        "rear_seam": frame((W / 2, s + g / 2, t / 2), Y, X),
        "seam_screws": joints.seats(i.seam_joint),
        "fan_holes": fan_holes(i.xdc, t, i.z_fan, i.fan_hole_spacing, 1),
        "tray_seat": frame((i.x_div + (t + b) / 2, yd_mid, t + g), Z, X),
        "rail_left_seat": frame((cx0 + rail_mid, yc, t + g), Z, X),
        "rail_right_seat": frame((cx1 - rail_mid, yc, t + g), Z, X),
        "side_seats": [frame((t, s / 2, H / 2), (-1, 0, 0), Y), frame((W - t, s / 2, H / 2), X, Y)],
        "left_side_inserts": joints.inserts(i.side_front_left_joint, ins_len),
        "right_side_inserts": joints.inserts(i.side_front_right_joint, ins_len),
        "tray_floor_screws": joints.seats(i.tray_front_floor_joint),
        "tray_ceiling_screws": joints.seats(i.tray_front_ceiling_joint),
        "rail_screws": joints.seats(i.rail_front_joint),
    }
    yr = (s + L) / 2
    rear_conn = {
        "seam": frame((W / 2, s + g / 2, t / 2), (0, -1, 0), X),
        "seam_inserts": joints.inserts(i.seam_joint, ins_len),
        "fan_holes": fan_holes(i.xdc, L - t, i.z_fan, i.fan_hole_spacing, -1),
        "psu_ledge": frame((px + pw / 2, L - t - i.psu_ledge / 2, pz), Z, X),
        "left_side_seat": frame((t, yr, H / 2), (-1, 0, 0), Y),
        "right_side_seat": frame((W - t, yr, H / 2), X, Y),
        "left_side_inserts": joints.inserts(i.side_left_rear_joint, ins_len),
        "right_side_inserts": joints.inserts(i.side_right_rear_joint, ins_len),
        "tray_floor_screws": joints.seats(i.tray_rear_floor_joint),
        "tray_ceiling_screws": joints.seats(i.tray_rear_ceiling_joint),
        "rail_screws": joints.seats(i.rail_rear_joint),
    }
    return {
        "front_shell": own(i, "front_shell", front, front_conn),
        "rear_shell": own(i, "rear_shell", rear, rear_conn),
    }
