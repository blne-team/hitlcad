"""Build a closed material boundary around an open cavity, in millimetres."""

from math import pi, sin

from hitlcad_blender import section_form


def build(p, i):
    height, radius = p["height"], p["radius"]
    levels = 24

    def outer_radius(z):
        t = z / height
        return radius * (1 + p["taper"] * t + p["swell"] * sin(pi * t))

    sections = [(height * level / levels, outer_radius(height * level / levels)) for level in range(levels + 1)]
    return {"form": section_form(sections=sections, wall=p["wall"], chord_tolerance=0.03, name="form")}
