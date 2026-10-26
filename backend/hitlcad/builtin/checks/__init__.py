"""The requirement checks a design's brief is measured by."""

from __future__ import annotations

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_checks() -> list[type]:
    from hitlcad.builtin.checks import checks

    return [checks.SizeCheck, checks.FitsWithinCheck, checks.VolumeCheck, checks.MassCheck, checks.HoleCheck,
            checks.WallCheck, checks.BodiesCheck, checks.ClearanceCheck, checks.StackCheck]  # fmt: skip
