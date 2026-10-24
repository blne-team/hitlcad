"""Offset wrist pitch link supporting the final tool spindle."""
from lib.geometry import beam, cross_drum, cylinder, finish


def build(p, i):
    d = i.layout
    s, end, z = d['scale'], d['end'], d['wrist']
    shape = cross_drum(end-120*s,z,86*s,68*s,43*s)
    shape += beam((end-120*s,z),(end-65*s,z),94*s,52*s,50*s)
    shape += cylinder(30*s,40*s,(end-90*s,120*s,z),(0,90,0))
    shape -= cross_drum(end-120*s,z,149*s,6*s,28*s)
    return finish('wrist_pitch',shape,i,'#e97518')
