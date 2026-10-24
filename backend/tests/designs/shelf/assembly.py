from build123d import Pos


def build(p, i):
    return {"plate": [Pos()], "box": [Pos(x, 0, 0) for x in i.slot_x]}
