"""Rotating pedestal and shoulder drive envelope."""
from lib.geometry import cross_drum, cylinder, finish


def build(p, i):
    d = i.layout
    s = d['scale']
    shape = cylinder(110*s,32*s,(0,0,102*s))
    shape += cylinder(83*s,126*s,(0,0,134*s))
    shape += cross_drum(0,d['shoulder'],-85*s,170*s,90*s)
    # Recessed side cover is part of the rigid housing envelope.
    shape -= cross_drum(0,d['shoulder'],-86*s,6*s,62*s)
    return finish('turret',shape,i,'#e97518')
