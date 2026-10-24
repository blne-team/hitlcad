"""Grounded base with four mounting bores and a raised drive housing."""
from build123d import Align, Axis, Box, Pos, fillet
from lib.geometry import cylinder, finish


def build(p, i):
    s = i.layout['scale']
    plate = Box(320*s, p['width'], 24*s, align=(Align.CENTER,Align.CENTER,Align.MIN))
    plate = fillet(plate.edges().filter_by(Axis.Z), 18*s)
    shape = plate + cylinder(125*s, 68*s, (0,0,24*s)) + cylinder(112*s,8*s,(0,0,92*s))
    for x,y in i.layout['base_bolts']:
        shape -= cylinder(8.5*s,26*s,(x,y,-s))
        shape -= cylinder(14*s,8*s,(x,y,17*s))
    return finish('base',shape,i,'#343c46')
