"""The address grammar every surface shares: design.json, build reports, picks, pins and tools.

    lid                     a part, or an instance of a part placed once
    planet[2]               one instance
    tilt/planet[2]          an instance inside a component instance
    lid.bearing_pocket      a connector (on every instance of the part)
    lid[0].bottom           a connector of one instance
    tilt.output             a component's export
    lid.bottom+housing.top  a mate, by its two ends

Names are letters, digits and `_`, not starting with a digit. Anything an agent reads in a tool's
answer parses back with these functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
NAME_RE = re.compile(rf"^{NAME}$")
_SEGMENT = re.compile(rf"^({NAME})(?:\[(\d+)\])?$")


def check_name(name: str, what: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError(f"{what} name {name!r}: use letters, digits and _, not starting with a digit")
    return name


@dataclass(frozen=True)
class Segment:
    """One step of an instance path: a part or component name, and optionally one instance of it."""

    name: str
    index: int | None = None

    def __str__(self) -> str:
        return self.name if self.index is None else f"{self.name}[{self.index}]"


@dataclass(frozen=True)
class InstancePath:
    segments: tuple[Segment, ...]

    @classmethod
    def parse(cls, text: str) -> InstancePath:
        parts = text.strip().split("/")
        segments = []
        for part in parts:
            m = _SEGMENT.match(part)
            if not m:
                raise ValueError(f"{text!r} is not an instance path like 'lid', 'planet[2]' or 'tilt/planet[2]'")
            segments.append(Segment(m.group(1), int(m.group(2)) if m.group(2) is not None else None))
        return cls(tuple(segments))

    @property
    def head(self) -> Segment:
        return self.segments[0]

    @property
    def leaf(self) -> Segment:
        return self.segments[-1]

    def child(self, segment: Segment) -> InstancePath:
        return InstancePath((*self.segments, segment))

    def __str__(self) -> str:
        return "/".join(str(s) for s in self.segments)


@dataclass(frozen=True)
class End:
    """A connector end: `part.connector`, `part[k].connector`, or `component.export`."""

    owner: Segment
    connector: str

    @classmethod
    def parse(cls, text: str) -> End:
        owner, sep, connector = text.strip().rpartition(".")
        if not sep or not NAME_RE.match(connector) or "/" in owner:
            raise ValueError(f"{text!r} is not a connector like 'lid.bottom', 'planet[1].bore' or 'tilt.output'")
        (segment,) = InstancePath.parse(owner).segments
        return cls(segment, connector)

    def __str__(self) -> str:
        return f"{self.owner}.{self.connector}"


INTERFACE_PREFIX = "interfaces."


def is_interface_ref(ref: str) -> bool:
    return ref.startswith(INTERFACE_PREFIX)
