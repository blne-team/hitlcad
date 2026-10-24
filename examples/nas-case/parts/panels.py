"""The side panels: flat plates that close the case, screwed with countersunk M3 screws into inserts in the shells'
corner bosses. Each prints outer face up, so the countersinks open upwards. The two front panels are one part,
placed twice (left, and turned over on the right); the rear-left one has the vents over the CPU."""

from build123d import Plane
from lib import joints
from lib.geometry import box, countersink, cyl_x, frame, own


def _screw_holes(panel, joint, p, i):
    """Clearance holes with countersinks, at the joint's seats (on the panel's outer face)."""
    d_head, h_head = i.flush_head
    t = p["wall"]
    for f in joints.seats(joint):
        o, z = f.position, Plane(f).z_dir
        x_mid = o.X - z.X * t / 2
        panel -= cyl_x(x_mid, o.Y, o.Z, p["screw_d"], t + 2)
        panel -= countersink(o, z, d_head, h_head, p["screw_d"])
    return panel


def build(p, i):
    t, g, s, L, H, W = p["wall"], p["fit_gap"], i.seam_y, i.case_l, i.case_h, i.case_w
    X, Y = (1, 0, 0), (0, 1, 0)

    front_joint = i.side_front_left_joint
    front = _screw_holes(box(0, t, 0, s - g / 2, 0, H), front_joint, p, i)

    left_rear = _screw_holes(box(0, t, s + g / 2, L, 0, H), i.side_left_rear_joint, p, i)
    vw, size = p["vent_slot_w"], p["vent_size"]
    vy, vz = i.board_rear_y - 95, i.board_top_z - 85
    for k in range(max(1, int(size // (2 * vw)))):
        y = vy - size / 2 + vw / 2 + k * 2 * vw
        left_rear -= box(-1, t + 1, y - vw / 2, y + vw / 2, vz - size / 2, vz + size / 2)

    right_rear = _screw_holes(box(W - t, W, s + g / 2, L, 0, H), i.side_right_rear_joint, p, i)

    yr = (s + L) / 2
    return {
        "side_front": own(
            i, "side_front", front, {"inner": frame((t, s / 2, H / 2), X, Y), "screws": joints.seats(front_joint)}
        ),
        "side_left_rear": own(
            i,
            "side_left_rear",
            left_rear,
            {"inner": frame((t, yr, H / 2), X, Y), "screws": joints.seats(i.side_left_rear_joint)},
        ),
        "side_right_rear": own(
            i,
            "side_right_rear",
            right_rear,
            {"inner": frame((W - t, yr, H / 2), (-1, 0, 0), Y), "screws": joints.seats(i.side_right_rear_joint)},
        ),
    }
