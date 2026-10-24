"""Axial roll output carrying the wrist pitch drum."""
from lib.geometry import cross_drum, cylinder, finish


def build(p, i):
    d = i.layout
    s, end, z = d['scale'], d['end'], d['wrist']
    shape = cylinder(43*s,57*s,(end-177*s,40*s,z),(0,90,0))
    shape += cross_drum(end-120*s,z,-3*s,86*s,43*s)
    return finish('wrist_roll',shape,i,'#343c46')
