"""Return catalog parts with their actual geometry, metadata and connector frames."""

from hitlcad.builtin.catalogs.bd_warehouse import heat_set_insert, screw


def build(p, i):
    return {
        "insert": heat_set_insert(i.insert_size, "McMaster-Carr"),
        "screw": screw("SocketHeadCapScrew", "M3-0.5", i.screw_length, "iso4762"),
    }
