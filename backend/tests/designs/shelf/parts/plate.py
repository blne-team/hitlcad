from build123d import Align, Box, Location

ON = (Align.CENTER, Align.CENTER, Align.MAX)


def build(p, i):
    s = p["box_size"]
    plate = Box(2 * s + 60, s + 30, p["plate_t"], align=ON)
    return {"plate": (plate, {"slots": [Location((x, 0, 0)) for x in i.slot_x]})}
