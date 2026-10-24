def corners(p, i):
    c = p["width"] / 2 - 5
    return [[x, y] for x in (-c, c) for y in (-c, c)]


def insert(p, i):
    return "M3-0.5-Standard"
