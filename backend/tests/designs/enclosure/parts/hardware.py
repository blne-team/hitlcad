from hitlcad.builtin.catalogs.bd_warehouse import heat_set_insert, screw


def build(p, i):
    return {
        "insert": heat_set_insert(i.insert, "McMaster-Carr"),
        "screw": screw("SocketHeadCapScrew", "M3-0.5", 9, "iso4762"),
    }
