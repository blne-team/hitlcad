"""Screws and heat-set inserts from bd_warehouse. Each fastener set has its own screw and insert parts, as a set's
screws and inserts are all the instances of its parts."""

from hitlcad.builtin.catalogs.bd_warehouse import heat_set_insert, screw

SETS = {  # joint -> the screw that closes it
    "seam": "seam",
    "side_front_left": "flush", "side_front_right": "flush", "side_left_rear": "flush", "side_right_rear": "flush",
    "tray_front_floor": "button", "tray_front_ceiling": "button", "tray_rear_floor": "button",
    "tray_rear_ceiling": "button", "rail_front": "button", "rail_rear": "button",
}  # fmt: skip


def build(p, i):
    out = {"standoff_insert": heat_set_insert(*i.insert_size)}
    for joint, kind in SETS.items():
        cls, std = i.screw_kinds[kind]
        length = i.seam_screw["length"] if joint == "seam" else i.panel_screw["length"]
        out[f"{joint}_screw"] = screw(cls, "M3-0.5", length, std)
        out[f"{joint}_insert"] = heat_set_insert(*i.insert_size)
    return out
