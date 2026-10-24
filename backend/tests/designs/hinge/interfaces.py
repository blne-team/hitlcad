def hinge_y(p, i):
    """The hinge axis runs along X, behind the box."""
    return p["size"] / 2 + p["pin_d"]


def hinge_z(p, i):
    return p["height"] + p["lid_t"] / 2


def knuckle(p, i):
    """Half the length of the base's knuckle."""
    return p["size"] / 6
