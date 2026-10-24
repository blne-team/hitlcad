"""The duct: a tube around the impeller with a shoulder the motor mount sits on, and a flat pad underneath
for the handle. Printed intake-end down: the intake is at Z=0, the outlet at the top, and every step faces
up. The pad's underside slopes at 40° so it needs no support."""

import math

from build123d import Align, BuildPart, BuildSketch, Cone, Cylinder, Location, Locations, Mode, Plane, Polygon, Pos, add, extrude

from lib.printing import TEARDROP_DEG, insert_hole, teardrop

ON = (Align.CENTER, Align.CENTER, Align.MIN)


def pad_frame(i):
    """On the pad face, Z out of it (-Y), X towards the outlet (+Z): the handle's own frame."""
    return Location(Plane(origin=(0, -i.pad_depth, i.duct_len - i.pad_len / 2), x_dir=(0, 0, 1), z_dir=(0, -1, 0)))


def build(p, i):
    L, ro, skirt = i.duct_len, i.duct_od / 2, p["guard_skirt"]
    pad = pad_frame(i)
    # the pad's side profile (Y, Z): flat face below the duct, underside sloping back up into the tube
    y_in = ro - p["wall"]
    z_top, z_low = L, L - i.pad_len
    z_root = z_low - (i.pad_depth - y_in) / math.tan(math.radians(TEARDROP_DEG))
    if z_root < skirt:
        raise ValueError("the duct is too short for the handle pad: reduce handle_depth or pad_extra")

    with BuildPart() as duct:
        Cylinder(i.rim_od / 2, skirt, align=ON)
        step = (ro - i.rim_od / 2) / math.tan(math.radians(TEARDROP_DEG))  # the rim's shoulder, sloped
        with Locations(Pos(0, 0, skirt)):
            Cone(i.rim_od / 2, ro, step, align=ON)
        with Locations(Pos(0, 0, skirt + step)):
            Cylinder(ro, L - skirt - step, align=ON)
        with BuildSketch(Plane.YZ.offset(-p["handle_w"] / 2)):
            Polygon((-y_in, z_root), (-i.pad_depth, z_low), (-i.pad_depth, z_top), (-y_in, z_top), align=None)
        extrude(amount=p["handle_w"])
        Cylinder(i.duct_id / 2, i.z_seat, align=ON, mode=Mode.SUBTRACT)
        with Locations(Pos(0, 0, i.z_seat)):
            Cylinder(i.mount_bore / 2, L - i.z_seat, align=ON, mode=Mode.SUBTRACT)
        # handle pad: inserts and the motor wire passage
        inserts = []
        for x, y in i.handle_screw_xy:
            hole, frame = insert_hole(pad * Pos(x, y, 0), i.insert_len, i.insert_hole_depth)
            add(hole, mode=Mode.SUBTRACT)
            inserts.append(frame)
        add(teardrop(pad, p["wire_hole_d"], i.pad_depth), mode=Mode.SUBTRACT)

    return {
        "duct": (
            duct.part,
            {
                "mount_seat": Location((0, 0, i.z_seat)),
                "mount_bore": Location((0, 0, L)),
                "intake": Location((0, 0, 0), (180, 0, 0)),
                "rim": Location((0, 0, skirt), (180, 0, 0)),
                "outlet": Location((0, 0, L)),
                "pad": pad,
                "inserts": inserts,
            },
        )
    }
