"""Mating values of the NAS case: one function per value, def name(p, i). Parts and assembly.py read them as i.<name>.

The case frame, in which every value here is given: X from the left side panel to the right, Y from the front
panel to the rear, Z up; the origin is the front-left-bottom corner of the closed case. The motherboard chamber
is on the left (the board stands on the divider, components facing -X), the drive chamber on the right. Each
printed part is modelled in its own frame, the way it prints; `print_frames` says where that frame sits here.
"""

import math

from hitlcad.catalogs.bd_warehouse import screw as _screw

# Fixed standards (not tunable): micro-ATX board and 3.5" drive envelopes.
MATX = 244.0
PCB_T = 1.6
HDD_W, HDD_L, HDD_H = 101.6, 147.0, 26.1
HDD_SIDE_HOLES = (28.5, 130.1)  # from the drive's front, 6-32 side holes
HDD_SIDE_HOLE_Z = 6.35  # above the drive's bottom
FAN_HOLE_SPACING = {80: 71.5, 92: 82.5, 120: 105.0, 140: 124.5}
# Approximate micro-ATX standoff positions: (distance forward from the rear I/O edge, distance down from the top
# edge). VERIFY against the real board before printing.
MATX_HOLES = [
    (10.2, 10.2), (165.1, 10.2), (236.2, 10.2),
    (10.2, 165.1), (236.2, 165.1),
    (10.2, 236.2), (165.1, 236.2), (236.2, 236.2),
]  # fmt: skip

REAR_GAP = 2.0  # board rear edge to rear panel
HDD_CLEAR = 0.5  # drive to cage, each side and below
SATA_ROOM = 30.0  # behind the drives for SATA connectors
TEARDROP_DEG = 40  # roofs of horizontal holes and the counterbore ends, from vertical: under FDM's 45°
SEAM_UNDER_HEAD = 5.0  # the seam counterbores end 5 mm short of the seam (before their cone)
PSU_LEDGE = 8.0  # depth of the PSU ledge on the rear panel

# Hardware: M3 screws into ruthex M3 × 4 heat-set inserts (made for Ø4.0 holes at least 5 mm deep).
INSERT = ("M3-0.5-4.0", "ruthex")
INSERT_LEN = 4.0
SCREWS = {"seam": ("SocketHeadCapScrew", "iso4762"), "flush": ("CounterSunkScrew", "iso10642"),
          "button": ("ButtonHeadScrew", "iso7380_1")}  # fmt: skip
M3_LENGTHS = (4, 5, 6, 8, 10, 12, 16, 20)
MIN_ENGAGEMENT_D = 0.9  # thread engagement in the 4 mm insert, × Ø: design.json's fastener sets say the same


def fan(p, i):
    f = int(p["fan_size"])
    if f not in FAN_HOLE_SPACING:
        raise ValueError(f"fan_size must be one of {sorted(FAN_HOLE_SPACING)} mm")
    return f


def fan_hole_spacing(p, i):
    return FAN_HOLE_SPACING[i.fan]


def boss(p, i):
    """The size of every screw boss."""
    return p["insert_d"] + 6


def pad(p, i):
    """Insert pads behind the standoffs, on the divider's drive-chamber side."""
    return max(p["insert_depth"] + 1 - p["wall"], 1)


def foot_t(p, i):
    """Thickness of the rail feet and the divider's flanges."""
    return p["insert_depth"] + 1.5


def foot_w(p, i):
    return p["rail_w"] + 8


def board_chamber(p, i):
    return p["standoff_h"] + PCB_T + p["board_clearance"]


def cage_wo(p, i):
    """Outside width of the drive cage (the two rail plates)."""
    return HDD_W + 2 * HDD_CLEAR + 2 * p["wall"]


def drive_chamber(p, i):
    return max(i.cage_wo, p["psu_w"] + i.pad + 3, i.fan + 2 * i.boss + 2) + 2


def inner_depth(p, i):
    return max(MATX + REAR_GAP + p["board_front_gap"], 2 * p["fan_thickness"] + 3 + HDD_L + SATA_ROOM)


def z_drives(p, i):
    """The bottom of each drive."""
    if p["hdd_gap"] < p["wall"] + 1.5:
        raise ValueError("hdd_gap must be at least wall + 1.5 mm to fit the drive rails")
    if p["rail_w"] >= 10:
        raise ValueError("rail_w must be under 10 mm or the rails hit the drives' screw holes")
    first = p["wall"] + i.foot_t + HDD_CLEAR
    return [first + k * (HDD_H + p["hdd_gap"]) for k in range(int(p["hdd_count"]))]


def cage_top(p, i):
    return i.z_drives[-1] + HDD_H + 3


def z_fan(p, i):
    """The fans' axis height."""
    return max((i.z_drives[0] + i.z_drives[-1] + HDD_H) / 2, p["wall"] + i.fan / 2 + 1)


def inner_height(p, i):
    low_top = max(i.cage_top, i.z_fan + i.fan / 2)
    return max(MATX + 2 * p["board_margin"], low_top + 3 + 5 + p["psu_h"] + i.boss + 2 - p["wall"])


def case_w(p, i):
    return 3 * p["wall"] + i.board_chamber + i.drive_chamber


def case_l(p, i):
    return 2 * p["wall"] + i.inner_depth


def case_h(p, i):
    return 2 * p["wall"] + i.inner_height


def seam_y(p, i):
    """Where the front and rear shells meet."""
    return i.case_l / 2


def z_top(p, i):
    """The ceiling's inner face."""
    return p["wall"] + i.inner_height


def x_div(p, i):
    """The divider's board-side face."""
    return p["wall"] + i.board_chamber


def drive_x(p, i):
    """The drive chamber's interior, [left, right]."""
    return [i.x_div + p["wall"], i.case_w - p["wall"]]


def xdc(p, i):
    """The drive chamber's centre line: the drives and the fans."""
    return sum(i.drive_x) / 2


def board_back_x(p, i):
    """The motherboard's solder side: the standoffs' height off the divider."""
    return i.x_div - p["standoff_h"]


def board_front_x(p, i):
    """The motherboard's component side."""
    return i.board_back_x - PCB_T


def board_rear_y(p, i):
    return i.case_l - p["wall"] - REAR_GAP


def board_top_z(p, i):
    return p["wall"] + p["board_margin"] + MATX


def standoff_points(p, i):
    """Each standoff's axis on the divider face, [y, z]."""
    return [[i.board_rear_y - u, i.board_top_z - v] for u, v in MATX_HOLES]


def matx_holes(p, i):
    """The micro-ATX mounting holes on the board: [from the rear I/O edge, down from the top edge]."""
    return [list(h) for h in MATX_HOLES]


def board_size(p, i):
    return [MATX, MATX, PCB_T]


def hdd_size(p, i):
    """A 3.5" drive: [width, length, height]."""
    return [HDD_W, HDD_L, HDD_H]


def hdd_clear(p, i):
    return HDD_CLEAR


def hdd_side_holes(p, i):
    """The drives' side screw holes: [from the drive's front, …] at a height above its bottom."""
    return {"y": list(HDD_SIDE_HOLES), "z": HDD_SIDE_HOLE_Z}


def fan_hole_d(p, i):
    """The fans' screw holes and the panels' holes for them."""
    return 4.3


def psu_ledge(p, i):
    """Depth of the PSU ledge on the rear panel."""
    return PSU_LEDGE


def cage_x(p, i):
    """The drive cage's outside faces, [left, right]."""
    return [i.xdc - i.cage_wo / 2, i.xdc + i.cage_wo / 2]


def cage_y(p, i):
    """The drive cage's front and rear: drive fronts at the first."""
    y0 = p["wall"] + p["fan_thickness"] + 3
    return [y0, y0 + HDD_L]


def psu_x0(p, i):
    return i.drive_x[0] + i.pad + 1


def psu_z0(p, i):
    """The PSU's bottom: the ledges it rests on."""
    z = i.z_top - p["psu_h"] - i.boss - 2
    if z - 5 < max(i.cage_top, i.z_fan + i.fan / 2) + 3:
        raise ValueError("Drives and fans run into the PSU: reduce hdd_count, hdd_gap or fan_size")
    return z


def seam_boss_x(p, i):
    """The seam bosses' centres along X, on the floor and the ceiling."""
    t, b = p["wall"], i.boss
    return [t + b / 2, (i.cage_x[1] + i.drive_x[1]) / 2]


def seam_boss_z(p, i):
    return [p["wall"] + i.boss / 2, i.z_top - i.boss / 2]


def side_holes(p, i):
    """The side-panel screws: [side, shell, y, z]. A side boss on the floor and one on the ceiling at each y; the
    rear-left pair stands further forward, clear of the expansion-card brackets."""
    t, s, L = p["wall"], i.seam_y, i.case_l
    rows = [(t + 15, "front", ("left", "right")), (s - 35, "front", ("left", "right")),
            (s + 35, "rear", ("left", "right")), (L - t - 40, "rear", ("left",)), (L - t - 15, "rear", ("right",))]  # fmt: skip
    return [[side, shell, y, z] for y, shell, sides in rows for side in sides for z in i.seam_boss_z]


def tray_y(p, i):
    """The divider's front and rear ends."""
    return [i.board_rear_y - MATX - 3, i.case_l - p["wall"]]


def tray_screw_ys(p, i):
    """The divider's flange screws along Y (each on the floor and the ceiling)."""
    y0, y1 = i.tray_y
    return [y0 + 15 + k * (y1 - y0 - 30) / 3 for k in range(4)]


def tray_screw_x(p, i):
    return i.drive_x[0] + i.boss / 2


def rail_feet(p, i):
    """The rail feet's screws, [side, x, y]: one near each end of each rail."""
    t = p["wall"]
    out = []
    for side, x_out, sgn in (("left", i.cage_x[0], 1), ("right", i.cage_x[1], -1)):
        x = x_out + sgn * (t + i.foot_w / 2)
        out += [[side, x, y] for y in (i.cage_y[0] + 15, i.cage_y[1] - 15)]
    return out


def print_frames(p, i):
    """Where each printed part's own frame sits in the case: [origin, x_dir, z_dir]. Z is the print's up and the
    origin lies on the bed. The shells print panel-down, the divider and rails flat, the side panels outer face up
    (for their countersinks)."""
    t, W, L, H, s = p["wall"], i.case_w, i.case_l, i.case_h, i.seam_y
    ym = (i.tray_y[0] + i.tray_y[1]) / 2
    yc, zc = sum(i.cage_y) / 2, (t + i.cage_top) / 2
    X, Y = [1, 0, 0], [0, 1, 0]
    return {
        "front_shell": [[W / 2, 0, H / 2], X, [0, 1, 0]],
        "rear_shell": [[W / 2, L, H / 2], X, [0, -1, 0]],
        "divider": [[i.x_div, ym, H / 2], Y, [1, 0, 0]],
        "hdd_rail_left": [[i.cage_x[0], yc, zc], Y, [1, 0, 0]],
        "hdd_rail_right": [[i.cage_x[1], yc, zc], Y, [-1, 0, 0]],
        "side_front": [[t, s / 2, H / 2], Y, [-1, 0, 0]],
        "side_left_rear": [[t, (s + L) / 2, H / 2], Y, [-1, 0, 0]],
        "side_right_rear": [[W - t, (s + L) / 2, H / 2], Y, [1, 0, 0]],
    }


# --------------------------------------------------------------------------- fasteners


def teardrop_deg(p, i):
    return TEARDROP_DEG


def seam_under_head(p, i):
    return SEAM_UNDER_HEAD


def insert_size(p, i):
    return list(INSERT)


def insert_len(p, i):
    return INSERT_LEN


def _screw_fit(clamp, depth, what):
    """The longest standard M3 screw that clamps `clamp` mm (seat to the hole's opening) and engages at
    least MIN_ENGAGEMENT_D × Ø in the insert without running past it, and how far the insert is pushed below the
    opening (at most the room the hole leaves under it) so that it does not."""
    room = depth - INSERT_LEN
    if room < 0:
        raise ValueError(f"{what}: the insert holes are shallower than the 4 mm inserts: raise insert_depth")
    for length in sorted(M3_LENGTHS, reverse=True):
        recess = max(0.0, length - clamp - INSERT_LEN)
        if recess <= room + 1e-9 and length - clamp - recess >= MIN_ENGAGEMENT_D * 3:
            return {"length": length, "recess": recess, "clamp": clamp}
    raise ValueError(f"{what}: no standard M3 screw clamps {clamp:g} mm and engages the insert")


def seam_seat(p, i):
    """How far in front of the seam the seam screws' heads sit: on the cone that ends each counterbore, where it
    narrows to the head's diameter."""
    kind, std = SCREWS["seam"]
    deg = math.radians(i.teardrop_deg)
    head_r = _screw(kind, "M3-0.5", 10, std).metadata["head_diameter"] / 2
    cb_r = (p["screw_d"] + 3) / 2
    if head_r >= cb_r:
        raise ValueError("the seam counterbore is narrower than the screw head: raise screw_d")
    return i.seam_under_head + (head_r - p["screw_d"] / 2) / math.tan(deg)


def screw_kinds(p, i):
    """The screw for each joint: [bd_warehouse class, standard]."""
    return {k: list(v) for k, v in SCREWS.items()}


def flush_head(p, i):
    """The countersunk side-panel screws' head: [diameter, height]."""
    kind, std = SCREWS["flush"]
    meta = _screw(kind, "M3-0.5", 8, std).metadata
    return [meta["head_diameter"], meta["head_height"]]


def seam_screw(p, i):
    g = p["fit_gap"]
    return _screw_fit(i.seam_seat + g / 2, p["insert_depth"] - g / 2, "seam screws")


def panel_screw(p, i):
    """The screws through a 3 mm wall or panel and the fit gap into an insert: side panels, divider and rails."""
    t, g = p["wall"], p["fit_gap"]
    if i.flush_head[1] > t - 0.3:
        raise ValueError("the side panels are too thin for countersunk M3 heads: raise wall")
    return _screw_fit(t + g, p["insert_depth"] - g, "panel screws")


def _stack(o, d):
    return [*o, *d]


def seam_joint(p, i):
    """Front shell to rear shell: screws from the front shell's bosses into inserts in the rear shell's. Each stack
    is [opening of the insert hole, direction out of it towards the head]."""
    o_y = i.seam_y + p["fit_gap"] / 2
    stacks = [_stack([x, o_y, z], [0, -1, 0]) for x in i.seam_boss_x for z in i.seam_boss_z]
    return {**i.seam_screw, "stacks": stacks}


def _side_joint(p, i, shell, sides):
    """The side-panel screws of one shell and side (a separate set per side, as the left and right screws share
    their axes)."""
    t, g, W = p["wall"], p["fit_gap"], i.case_w
    stacks = []
    for side, sh, y, z in i.side_holes:
        if sh == shell and side in sides:
            x, d = (t + g, -1) if side == "left" else (W - t - g, 1)
            stacks.append(_stack([x, y, z], [d, 0, 0]))
    return {**i.panel_screw, "stacks": stacks}


def side_front_left_joint(p, i):
    return _side_joint(p, i, "front", ("left",))


def side_front_right_joint(p, i):
    return _side_joint(p, i, "front", ("right",))


def side_left_rear_joint(p, i):
    return _side_joint(p, i, "rear", ("left",))


def side_right_rear_joint(p, i):
    return _side_joint(p, i, "rear", ("right",))


def _tray_joint(p, i, front, floor):
    """The divider's flange screws in one shell: up through the floor, or down through the ceiling (a separate set,
    as each floor screw shares its axis with a ceiling screw)."""
    t, g = p["wall"], p["fit_gap"]
    o_z, d = (t + g, -1) if floor else (i.z_top - g, 1)
    stacks = [_stack([i.tray_screw_x, y, o_z], [0, 0, d]) for y in i.tray_screw_ys if (y < i.seam_y) == front]
    return {**i.panel_screw, "stacks": stacks}


def tray_front_floor_joint(p, i):
    return _tray_joint(p, i, True, True)


def tray_front_ceiling_joint(p, i):
    return _tray_joint(p, i, True, False)


def tray_rear_floor_joint(p, i):
    return _tray_joint(p, i, False, True)


def tray_rear_ceiling_joint(p, i):
    return _tray_joint(p, i, False, False)


def _rail_joint(p, i, front):
    t, g = p["wall"], p["fit_gap"]
    stacks = [_stack([x, y, t + g], [0, 0, -1]) for _, x, y in i.rail_feet if (y < i.seam_y) == front]
    return {**i.panel_screw, "stacks": stacks}


def rail_front_joint(p, i):
    return _rail_joint(p, i, True)


def rail_rear_joint(p, i):
    return _rail_joint(p, i, False)
