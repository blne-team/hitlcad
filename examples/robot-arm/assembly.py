"""Forward kinematics: each joint angle carries every downstream body."""
from build123d import Location, Pos, Rot
from lib.geometry import NAMES


def build(p, i):
    """Place all seven bodies using the shared six joint frames."""
    data = i.layout
    delta = Pos()
    placed = {'base': [Pos()]}
    for n, name in enumerate(NAMES[1:]):
        pivot = Location(data['axes'][n], data['rotations'][n])
        delta = delta * pivot * Rot(0, 0, p[f'j{n+1}_deg']) * pivot.inverse()
        placed[name] = [delta * Pos(*data['origins'][n+1])]
    return placed
