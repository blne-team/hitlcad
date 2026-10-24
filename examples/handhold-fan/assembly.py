"""Where each part and component instance sits: def build(p, i) -> {name: [placement, …]}.

The blower blows along +X (nozzle forward), the handle hangs below (-Z), the duct's intake is at the origin.
"""

from build123d import Location, Plane, Pos

from hitlcad.sdk import mate_to


def build(p, i):
    duct = Location(Plane(origin=(0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)))
    pad = duct * Location(
        Plane(origin=(0, -i.pad_depth, i.duct_len - i.pad_len / 2), x_dir=(0, 0, 1), z_dir=(0, -1, 0))
    )
    mount = duct * Pos(0, 0, i.z_seat)
    handle = mate_to(Location((0, 0, 0), (180, 0, 0)), pad)
    cap = handle * mate_to(Location((0, 0, p["cap_t"])), Pos(0, 0, p["handle_len"]))
    return {
        "duct": [duct],
        "motor_mount": [mount],
        "motor": [mount * mate_to(Location((0, 0, 0)), Pos(0, 0, p["bulkhead_t"]))],
        "impeller": [duct * Pos(0, 0, i.z_blades)],
        "intake_guard": [duct * mate_to(Location((0, 0, p["guard_t"])), Location((0, 0, 0), (180, 0, 0)))],
        "nozzle": [duct * mate_to(Location((0, 0, p["nozzle_len"])), Pos(0, 0, i.duct_len))],
        "handle": [handle],
        "grip_cap": [cap],
        "motor_screw": [mount * Location((x, y, 0), (180, 0, 0)) for x, y in i.motor_bolt_xy],
        "pad_insert": [pad * Pos(x, y, -i.insert_len) for x, y in i.handle_screw_xy],
        "handle_screw": [handle * Pos(x, y, i.handle_top_t) for x, y in i.handle_screw_xy],
        "cap_insert": [handle * Pos(x, y, p["handle_len"] - i.insert_len) for x, y in i.cap_screw_xy],
        "cap_screw": [cap * Location((x, -y, 0), (180, 0, 0)) for x, y in i.cap_screw_xy],
    }
