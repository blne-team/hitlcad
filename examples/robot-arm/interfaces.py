"""Shared home-pose joint frames and envelope dimensions in millimetres."""


def layout(p, i):
    """Derive the six axes and all mating offsets once."""
    s = p['height'] / 860
    end = p['length'] - 160 * s
    shoulder = 260 * s
    elbow = p['height'] - 80 * s
    wrist = 620 * s
    return {
        'scale': s, 'end': end, 'shoulder': shoulder, 'elbow': elbow, 'wrist': wrist,
        'axes': [[0, 0, 101*s], [0, 86.5*s, shoulder],
                 [140*s, 86.5*s, elbow], [end-178.5*s, 40*s, wrist],
                 [end-120*s, 84.5*s, wrist], [end-48.5*s, 120*s, wrist]],
        'rotations': [[0,0,0], [-90,0,0], [90,0,0], [0,90,0], [-90,0,0], [0,90,0]],
        'origins': [[0,0,0], [0,0,102*s], [0,0,175*s], [0,0,wrist-55*s],
                    [0,0,wrist-50*s], [0,0,wrist-43*s], [0,0,wrist-34*s]],
        'base_bolts': [[x*125*s,y*(p['width']/2-35*s)] for x in [-1,1] for y in [-1,1]],
    }
