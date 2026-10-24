"""Where every instance sits, from the interface values."""

from build123d import Pos, Rot

from hitlcad.catalogs.bd_warehouse import bearing, heat_set_insert, screw


def build(p, i):
    top = i.housing_height
    lid_top = top + p["lid_thickness"]
    insert_length = heat_set_insert(i.insert, "McMaster-Carr").metadata["length"]
    seat_width = bearing("SingleRowDeepGrooveBallBearing", i.bearing, "SKT").metadata["width"]
    head = screw("SocketHeadCapScrew", "M3-0.5", i.motor_screw_length, "iso4762").metadata["head_height"]
    return {
        "housing": [Pos()],
        "motor": [Pos()],  # its face at Z=0, body below
        "sun": [Pos(0, 0, i.z_gear)],
        "planet": [Pos(x, y, i.z_gear) * Rot(0, 0, turn) for x, y, turn in i.planet_poses],
        "carrier": [Pos(0, 0, i.z_carrier)],
        "lid": [Pos(0, 0, top)],
        "bearing": [Pos(0, 0, lid_top - seat_width)],
        "insert": [Pos(x, y, top - insert_length) for x, y in i.lid_bolt_xy],
        "lid_screw": [Pos(x, y, lid_top) for x, y in i.lid_bolt_xy],
        "motor_screw": [Pos(x, y, p["floor"] - head) for x, y in i.nema_xy],  # in the counterbores
    }
