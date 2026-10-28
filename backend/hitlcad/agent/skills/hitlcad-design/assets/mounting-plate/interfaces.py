"""Mating centers are shared values, so geometry and later assemblies can use the same pattern."""


def bolt_xy(p, i):
    x, y = p["width"] / 2 - 10, p["depth"] / 2 - 10
    return [[-x, -y], [x, -y], [x, y], [-x, y]]


def overall_height(p, i):
    return p["thickness"] + p["boss_height"]
