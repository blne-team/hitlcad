"""Tool roll spindle with cable bore and six mounting holes."""
import math
from lib.geometry import cylinder, finish


def build(p, i):
    d = i.layout
    s, end, z = d['scale'], d['end'], d['wrist']
    shape = cylinder(27*s,35*s,(end-47*s,120*s,z),(0,90,0))
    shape += cylinder(34*s,12*s,(end-12*s,120*s,z),(0,90,0))
    shape -= cylinder(10*s,49*s,(end-48*s,120*s,z),(0,90,0))
    for n in range(6):
        angle = n*math.tau/6
        shape -= cylinder(3.3*s,14*s,(end-13*s,120*s+25*s*math.cos(angle),z+25*s*math.sin(angle)),(0,90,0))
    return finish('tool_flange',shape,i,'#b9c3cd')
