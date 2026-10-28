"""Relationships shared by the housing, lid, hardware and placements, in millimetres."""

from hitlcad.builtin.catalogs.bd_warehouse import heat_set_insert


def wall(p, i):
    """Local teaching choice; critical interfaces are kept independent of overall size."""
    return 2.0


def lid_thickness(p, i):
    return 3.0


def insert_size(p, i):
    return "M3-0.5-Standard"


def insert_length(p, i):
    return heat_set_insert(i.insert_size, "McMaster-Carr").metadata["length"]


def insert_hole_depth(p, i):
    insert = heat_set_insert(i.insert_size, "McMaster-Carr")
    return insert.metadata["length"] + 2 * insert.metadata["thread_pitch"]


def screw_length(p, i):
    return 8.0


def boss_radius(p, i):
    return 4.0


def bolt_xy(p, i):
    """Keep attachment centers a fixed distance from the changing housing edges."""
    inset = 5.0
    x, y = p["width"] / 2 - inset, p["depth"] / 2 - inset
    if p["height"] - i.insert_hole_depth < i.wall:
        raise ValueError("insert holes would consume the base floor")
    return [[a, b] for a in (-x, x) for b in (-y, y)]


def vent_width(p, i):
    return 2.0


def vent_length(p, i):
    return p["depth"] - 16.0


def vent_centers(p, i):
    """A centered field with fixed pitch; reject counts that consume its side borders."""
    count, pitch, border = p["vent_count"], 4.0, 12.0
    occupied_width = (count - 1) * pitch + i.vent_width
    if occupied_width > p["width"] - 2 * border:
        raise ValueError("slot field exceeds protected borders: reduce count or increase width")
    return [(index - (count - 1) / 2) * pitch for index in range(count)]
