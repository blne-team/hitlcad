from hitlcad.catalogs.bd_warehouse import heat_set_insert, screw


def build(p, i):
    return {
        "motor_screw": screw("SocketHeadCapScrew", "M3-0.5", i.motor_screw_len, "iso4762"),
        "handle_screw": screw("SocketHeadCapScrew", "M3-0.5", i.handle_screw_len, "iso4762"),
        "cap_screw": screw("SocketHeadCapScrew", "M3-0.5", i.cap_screw_len, "iso4762"),
        "pad_insert": heat_set_insert(i.insert_size, "CNCKitchen"),
        "cap_insert": heat_set_insert(i.insert_size, "CNCKitchen"),
    }
