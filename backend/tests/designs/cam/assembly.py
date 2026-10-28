from build123d import Pos, Rot


def build(p, i):
    return {
        "base": [Pos()],
        "camshaft": [Rot(0, 0, p["cam_angle"])],
        "follower": [Pos(i.follower_at, 0, 4)],
    }
