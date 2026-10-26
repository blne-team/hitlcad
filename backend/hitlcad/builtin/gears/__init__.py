"""Gears and what they drive: the gear connector, gears in mesh, a pinion on a rack, a lead screw and its nut."""

from __future__ import annotations

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_connectors() -> list[type]:
    from hitlcad.builtin.gears.connectors import GearConnector

    return [GearConnector]


@hookimpl
def hitlcad_relations() -> list[type]:
    from hitlcad.builtin.gears.relations import GearRelation, RackRelation, ScrewRelation

    return [GearRelation, RackRelation, ScrewRelation]
