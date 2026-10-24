"""Shoulder-to-elbow link with rounded joint housings."""
from lib.geometry import beam, cross_drum, finish


def build(p, i):
    d = i.layout
    s = d['scale']
    a, b = (0,d['shoulder']), (140*s,d['elbow'])
    shape = beam(a,b,88*s,70*s,105*s)
    shape += cross_drum(*a,88*s,70*s,85*s)
    shape += cross_drum(*b,88*s,70*s,80*s)
    for center, radius in [(a,61*s),(b,55*s)]:
        shape -= cross_drum(*center,153*s,6*s,radius)
    return finish('upper_arm',shape,i,'#f28a20')
