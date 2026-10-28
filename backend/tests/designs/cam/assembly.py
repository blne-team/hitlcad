from build123d import Pos, Rot

GUIDE_X = 45


def build(p, i):
    return {
        "base": [Pos()],
        "camshaft": [Rot(0, 0, p["cam_angle"])],
        "follower": [Pos(i.follower_at, 0, 4)],
        "spring": [Pos(GUIDE_X, 0, 4) * Rot(0, -90, 0)],  # its seat on the guide, its axis back along the rod
    }
