from build123d import Box


def build(p, i):
    return {"box": (Box(p["width"], 10, 5), {})}
