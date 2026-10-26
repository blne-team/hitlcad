"""Bought parts: the output bearing, the lid inserts, the screws."""

from hitlcad.builtin.catalogs.bd_warehouse import bearing, heat_set_insert, screw


def build(p, i):
    return {
        "bearing": bearing("SingleRowDeepGrooveBallBearing", i.bearing, "SKT"),
        "insert": heat_set_insert(i.insert, "McMaster-Carr"),
        "lid_screw": screw("SocketHeadCapScrew", "M3-0.5", i.lid_screw_length, "iso4762"),
        "motor_screw": screw("SocketHeadCapScrew", "M3-0.5", i.motor_screw_length, "iso4762"),
    }
