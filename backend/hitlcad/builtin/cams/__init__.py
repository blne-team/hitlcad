"""Cams: a part turning about its axis that drives a follower along its own by a table of lift."""

from __future__ import annotations

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_relations() -> list[type]:
    from hitlcad.builtin.cams.relations import CamRelation

    return [CamRelation]
