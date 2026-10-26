"""Joints: datum frames, planar faces, round holes and shafts, and the mates between them."""

from __future__ import annotations

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_connectors() -> list[type]:
    from hitlcad.builtin.joints.connectors import FaceConnector, FrameConnector, HoleConnector, ShaftConnector

    return [FrameConnector, FaceConnector, HoleConnector, ShaftConnector]


@hookimpl
def hitlcad_mates() -> list[type]:
    from hitlcad.builtin.joints.mates import BallMate, CylindricalMate, PlanarMate, RevoluteMate, RigidMate, SliderMate

    return [RigidMate, RevoluteMate, SliderMate, CylindricalMate, PlanarMate, BallMate]
