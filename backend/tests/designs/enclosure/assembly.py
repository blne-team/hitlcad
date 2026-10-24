from build123d import Pos


def build(p, i):
    h, t = p["height"], p["lid_t"]
    return {
        "base": [Pos()],
        "lid": [Pos(0, 0, h)],
        "insert": [Pos(x, y, h - 5.7) for x, y in i.corners],
        "screw": [Pos(x, y, h + t) for x, y in i.corners],
    }
