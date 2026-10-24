"""Where each part sits: def build(p, i) -> {part: [placement, …]}.

The body is the figurine's frame (it stands as it prints); the head sits on the neck joint and the tail stands
in its socket in the rump, each in the frame interfaces.py gives it.
"""

from mathutils import Matrix


def build(p, i):
    return {
        "body": [Matrix.Identity(4)],
        "head": [Matrix(i.neck_joint)],
        "tail": [Matrix(i.tail_root)],
    }
