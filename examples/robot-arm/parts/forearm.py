"""Elbow output link and axial wrist drive housing."""
from lib.geometry import beam, cross_drum, cylinder, finish


def build(p, i):
    d = i.layout
    s, end = d['scale'], d['end']
    a, b = (140*s,d['elbow']), (end-230*s,d['wrist'])
    shape = beam(a,b,-5*s,90*s,88*s)
    shape += cross_drum(*a,-5*s,90*s,78*s)
    shape += cross_drum(*b,-5*s,90*s,44*s)
    shape += cylinder(55*s,60*s,(end-240*s,40*s,d['wrist']),(0,90,0))
    shape -= cross_drum(*a,-6*s,6*s,54*s)
    return finish('forearm',shape,i,'#f28a20')
