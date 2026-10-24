"""Mating values: one function per value, def name(p, i). Parts and assembly.py read them as i.<name>.

The duct's own frame: its axis is Z, the intake at Z=0 (it prints intake-end down) and the outlet at the
top, so air flows towards +Z. The z_* values are heights in that frame.
"""

import math

HOLE_ALLOWANCE = 0.15  # printed holes come out this much smaller (design.json manufacturing)
SCREW_LENGTHS = (4, 5, 6, 8, 10, 12, 14, 16, 20, 25, 30)
HEAD_R = 2.75  # ISO 4762 M3 head


def _screw_length(longest, through, engage, what):
    """The longest standard M3 screw no longer than `longest` that engages at least `engage` mm past `through`."""
    fits = [n for n in SCREW_LENGTHS if n <= longest]
    if not fits or fits[-1] - through < engage:
        raise ValueError(f"{what}: no standard M3 screw engages {engage:g} mm without bottoming out")
    return fits[-1]


def insert_size(p, i):
    return "M3-0.5-5.7"


def insert_len(p, i):
    return 5.7


def insert_hole_depth(p, i):
    return i.insert_len + 2


def duct_id(p, i):
    """The bore around the impeller."""
    return p["fan_d"] + 2 * p["tip_gap"]


def mount_bore(p, i):
    """The duct's bore behind the shoulder, where the motor mount slides in from the outlet."""
    return i.duct_id + 2 * p["mount_ring"] + 0.2 + HOLE_ALLOWANCE


def mount_od(p, i):
    return i.mount_bore - HOLE_ALLOWANCE - 0.2


def duct_od(p, i):
    return i.mount_bore + 2 * p["wall"]


def rim_od(p, i):
    """The duct's intake end, turned down 1 mm so the guard's skirt has a shoulder to start from."""
    return i.duct_od - 2


def guard_bore(p, i):
    return i.rim_od + 0.35 + HOLE_ALLOWANCE


def collar_id(p, i):
    return p["motor_d"] + p["can_fit"] + HOLE_ALLOWANCE


def collar_od(p, i):
    od = i.collar_id + 2 * p["collar_wall"]
    if od > i.duct_id - 16:
        raise ValueError("the motor collar leaves less than 8 mm for the vanes: a smaller motor or a bigger impeller")
    return od


def z_blades(p, i):
    """The impeller's upstream end (its hub face)."""
    return p["inlet_gap"]


def z_seat(p, i):
    """The shoulder the motor mount sits on: the bulkhead's upstream face, under the motor screw heads."""
    return p["inlet_gap"] + p["fan_h"] + p["fan_gap"]


def z_face(p, i):
    """The motor's shaft face, on the bulkhead's downstream side."""
    return i.z_seat + p["bulkhead_t"]


def duct_len(p, i):
    return i.z_face + p["motor_len"] + p["motor_tail"]


def boss_h(p, i):
    h = p["fan_gap"] - p["boss_gap"]
    if h < 6:
        raise ValueError("fan_gap - boss_gap leaves the set-screw boss under 6 mm tall")
    return h


def impeller_h(p, i):
    return p["fan_h"] + i.boss_h


def impeller_bore(p, i):
    return p["shaft_d"] + 0.05 + HOLE_ALLOWANCE


def shaft_engagement(p, i):
    e = p["shaft_len"] - p["bulkhead_t"] - p["boss_gap"]
    if e < 6:
        raise ValueError(f"the shaft reaches only {e:g} mm into the impeller: needs 6 mm (longer shaft, thinner bulkhead)")
    if e > i.impeller_h:
        raise ValueError("the shaft sticks out of the impeller: shorten it or raise fan_h")
    return e


def motor_bolt_xy(p, i):
    r = p["motor_bolt_circle"] / 2
    if r + 3 > p["motor_d"] / 2:
        raise ValueError("the motor bolt circle is wider than the motor face")
    if r - HEAD_R < p["shaft_hole_d"] / 2 + 1:
        raise ValueError("the motor screw heads overlap the bulkhead's shaft hole")
    if r - HEAD_R < p["boss_d"] / 2 + 1:
        raise ValueError("the motor screw heads sit under the impeller's boss")
    return [[r * math.cos(a), r * math.sin(a)] for a in (math.pi / 4 + k * math.pi / 2 for k in range(4))]


def motor_engagement(p, i):
    """Thread engagement the motor screws need in the motor's aluminium, as a multiple of their diameter."""
    return 1.5


def motor_screw_len(p, i):
    t = p["bulkhead_t"]
    return _screw_length(t + p["motor_tap_depth"] - 0.5, t, 3 * i.motor_engagement, "motor screws")


def pad_depth(p, i):
    """From the duct axis to the flat pad the handle bolts to."""
    return i.duct_od / 2 + p["pad_extra"]


def pad_len(p, i):
    """The pad runs back from the outlet, as long as the handle is deep."""
    if p["handle_depth"] > i.duct_len - p["guard_skirt"] - 20:
        raise ValueError("the handle is deeper than the duct is long: reduce handle_depth")
    return p["handle_depth"]


def handle_top_t(p, i):
    return p["handle_wall"] + 1


def handle_cavity(p, i):
    """The ESC bay, front-to-back × side-to-side."""
    return [p["handle_depth"] - 2 * p["handle_wall"], p["handle_w"] - 2 * p["handle_wall"]]


def handle_screw_xy(p, i):
    """The four handle screws, in the handle's frame (= the duct pad's frame): X forward, Y sideways."""
    d, w = i.handle_cavity
    a, b = d / 2 - HEAD_R - 0.75, w / 2 - HEAD_R - 0.75
    if b < p["wire_hole_d"] / 2 + 3:
        raise ValueError("the handle is too narrow for its screws beside the wire passage")
    return [[x, y] for x in (-a, a) for y in (-b, b)]


def handle_screw_len(p, i):
    t = i.handle_top_t
    return _screw_length(t + i.insert_len, t, 4.5, "handle screws")


def rib_t(p, i):
    """Ribs inside the grip's side walls that carry the cap inserts."""
    return 4


def cap_screw_xy(p, i):
    y = p["handle_w"] / 2 - (p["handle_wall"] + i.rib_t) / 2
    return [[0, -y], [0, y]]


def cap_screw_len(p, i):
    return _screw_length(p["cap_t"] + i.insert_len, p["cap_t"], 4.5, "cap screws")


def spigot_od(p, i):
    return i.mount_bore - HOLE_ALLOWANCE - 0.15
