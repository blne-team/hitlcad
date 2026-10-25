from __future__ import annotations

import importlib.util
import sys
from importlib.resources import files
from pathlib import Path

from hitlcad.backends.subprocess_backend import SubprocessBackend


class Build123dBackend(SubprocessBackend):
    name = "build123d"
    title = "build123d (B-rep)"
    summary = (
        "Exact boundary-representation solid modelling on OpenCASCADE. Best for mechanical and "
        "functional parts: enclosures, brackets, mounts, gears, fixtures, anything with precise "
        "dimensions, holes, fillets and chamfers that may be manufactured. Exports STEP; connector "
        "features and fits are measured on the B-rep. Standard parts come from catalog providers. "
        "Poor at organic or sculpted shapes."
    )
    brep = True
    entry = Path(__file__).with_name("entry.py")

    def __init__(self, *, python: str | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.python = python or sys.executable

    def availability(self) -> tuple[bool, str]:
        if importlib.util.find_spec("build123d") is None:
            return False, "build123d is not installed in this environment"
        return True, "build123d importable"

    def authoring_guide(self) -> str:
        return files("hitlcad.backends.build123d").joinpath("guide.md").read_text(encoding="utf-8")

    def command(self, job_file: Path) -> list[str]:
        # Isolated, writing no bytecode, unbuffered (a stopped build's last words reach its log), in UTF-8 mode.
        return [self.python, "-I", "-B", "-u", "-X", "utf8", str(self.entry), str(job_file)]
