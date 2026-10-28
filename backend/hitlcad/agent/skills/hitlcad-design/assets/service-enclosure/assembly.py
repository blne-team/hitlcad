"""Place every instance from the same values that built its mating features."""

from build123d import Pos


def build(p, i):
    height = p["height"]
    return {
        "base": [Pos()],
        "lid": [Pos(0, 0, height)],
        "insert": [Pos(x, y, height - i.insert_length) for x, y in i.bolt_xy],
        "screw": [Pos(x, y, height + i.lid_thickness) for x, y in i.bolt_xy],
    }
