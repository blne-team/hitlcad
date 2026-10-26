"""Standard parts from bd_warehouse: ISO/ASME screws, nuts and washers, heat-set inserts, rolling bearings.

In model code (build123d):

    from hitlcad.builtin.catalogs.bd_warehouse import ClearanceHole, InsertHole, frames, heat_set_insert, screw

    lid_screw = screw("SocketHeadCapScrew", "M3-0.5", 14, "iso4762")    # a CatalogPart
    with Locations(*holes):
        ClearanceHole(lid_screw.native, fit="Normal")                  # sized to the fastener
    return {"lid": (lid.part, {"screws": frames(lid_screw.native)})}

Hole operations (ClearanceHole, TapHole, InsertHole, ThreadedHole, PressFitHole) are bd_warehouse's.
Each records where the fastener's own origin goes; `frames(native)` returns those frames, so the holes
and their connector cannot disagree. Every part here has a `placement` connector (its own origin,
facing out) that mates `rigid` with such a frame.

Connectors, in each part's own frame (bd_warehouse's: Z along the fastener's axis, origin at the
seat of a screw's head, at the base of nuts, washers, inserts and bearings):
    screws     placement (frame), seat (face under the head), shank (shaft), tip (frame)
    nuts       placement, bottom (face), top (face), thread (hole, opening at the bottom)
    washers    placement, bottom (face), top (face), bore (hole, opening at the top)
    inserts    placement, bottom (face), top (face), outer (shaft from the top), thread (hole, opening at the top)
    bearings   placement, bottom (face), top (face), outer (shaft from the top), bore (hole, opening at the top)
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from hitlcad.catalogs import CatalogItem
from hitlcad.sdk import CatalogPart

NAME = "bd_warehouse"
MATERIAL = {"screw": "steel", "nut": "steel", "washer": "steel", "insert": "brass", "bearing": "steel"}
CONNECTORS = {
    "screw": {"placement": "frame: the screw's origin, facing out; mates rigid with frames(...) of its hole",
              "seat": "face under the head", "shank": "shaft from under the head to the tip", "tip": "frame at the tip"},
    "nut": {"placement": "frame: mates rigid with frames(...)", "bottom": "face", "top": "face",
            "thread": "hole, opening at the bottom (the seat side)"},
    "washer": {"placement": "frame: mates rigid with frames(...)", "bottom": "face", "top": "face",
               "bore": "hole, opening at the top"},
    "insert": {"placement": "frame: mates rigid with frames(...) of its InsertHole", "bottom": "face", "top": "face",
               "outer": "shaft from the top", "thread": "hole, opening at the top"},
    "bearing": {"placement": "frame: mates rigid with frames(...) of its PressFitHole", "bottom": "face",
                "top": "face", "outer": "shaft from the top", "bore": "hole, opening at the top"},
}  # fmt: skip


def _location(z: float = 0.0, up: bool = True) -> Any:
    from build123d import Location

    return Location((0, 0, z), (0, 0, 0) if up else (180, 0, 0))


def _height(native: Any) -> float:
    bb = native.bounding_box()
    return float(bb.max.Z - bb.min.Z)


def _class(module: Any, base: Any, kind: str, family: str) -> Any:
    cls = getattr(module, kind, None)
    if not (isinstance(cls, type) and issubclass(cls, base)):
        names = ", ".join(sorted(c.__name__ for c in _subclasses(base)))
        raise ValueError(f"no bd_warehouse {family} {kind!r}; {family}s: {names}")
    return cls


def _subclasses(base: Any) -> list[Any]:
    return [c for c in base.__subclasses__() if not getattr(c, "__abstractmethods__", None)]


def _part(
    native: Any, family: str, designation: str, connectors: dict[str, Any], metadata: dict[str, Any]
) -> CatalogPart:
    return CatalogPart(shape=native, connectors={"placement": _location(up=False), **connectors}, provider=NAME,
                       designation=designation, material=MATERIAL[family], metadata={"family": family, **metadata},
                       native=native)  # fmt: skip


def _thread(native: Any) -> dict[str, Any]:
    """A threaded part's thread: its size ("M3-0.5"), major diameter and pitch, what fastener sets match."""
    return {"thread": native.thread_size, "thread_diameter": float(native.thread_diameter),
            "thread_pitch": float(native.thread_pitch)}  # fmt: skip


def _clearance(native: Any) -> dict[str, float]:
    """A screw's clearance holes. bd_warehouse's Close, Normal and Loose are ISO 273's fine, medium and coarse series
    for metric sizes (ASME B18.2.8's for inch sizes); a few sizes have none."""
    try:
        holes = native.clearance_hole_diameters
    except (KeyError, ValueError):
        return {}
    return {f"clearance_{series}": float(holes[fit]) for series, fit in
            (("fine", "Close"), ("medium", "Normal"), ("coarse", "Loose"))}  # fmt: skip


def screw(kind: str, size: str, length: float, standard: str) -> CatalogPart:
    """A screw: `kind` is a bd_warehouse screw class (SocketHeadCapScrew, CounterSunkScrew, …), `size` like
    "M3-0.5", `length` in mm, `standard` like "iso4762"."""
    from bd_warehouse import fastener

    cls = _class(fastener, fastener.Screw, kind, "screw")
    native = cls(size=size, length=length, fastener_type=standard, simple=True)
    connectors = {"seat": _location(up=False), "shank": _location(up=False), "tip": _location(-length, up=False)}
    metadata = {**_thread(native), **_clearance(native), "length": float(length),
                **{k: float(getattr(native, k)) for k in ("head_diameter", "head_height") if hasattr(native, k)}}  # fmt: skip
    return _part(native, "screw", f"{kind} {standard} {size} × {length:g}", connectors, metadata)


def nut(kind: str, size: str, standard: str) -> CatalogPart:
    """A nut: `kind` is a bd_warehouse nut class (HexNut, SquareNut, …)."""
    from bd_warehouse import fastener

    cls = _class(fastener, fastener.Nut, kind, "nut")
    if cls is fastener.HeatSetNut:
        raise ValueError("heat-set inserts come from heat_set_insert(size, standard)")
    native = cls(size=size, fastener_type=standard, simple=True)
    h = _height(native)
    connectors = {"bottom": _location(up=False), "top": _location(h), "thread": _location(up=False)}
    metadata = {**_thread(native), "length": h}
    return _part(native, "nut", f"{kind} {standard} {size}", connectors, metadata)


def washer(kind: str, size: str, standard: str) -> CatalogPart:
    """A washer: `kind` is a bd_warehouse washer class (PlainWasher, ChamferedWasher, …)."""
    from bd_warehouse import fastener

    cls = _class(fastener, fastener.Washer, kind, "washer")
    native = cls(size=size, fastener_type=standard)
    h = _height(native)
    connectors = {"bottom": _location(up=False), "top": _location(h), "bore": _location(h)}
    return _part(native, "washer", f"{kind} {standard} {size}", connectors, {"thickness": h})


def heat_set_insert(size: str, standard: str) -> CatalogPart:
    """A heat-set insert: `size` like "M3-0.5-Standard", `standard` its maker, e.g. "McMaster-Carr"."""
    from bd_warehouse import fastener

    native = fastener.HeatSetNut(size=size, fastener_type=standard, simple=True)
    h = _height(native)
    connectors = {"bottom": _location(up=False), "top": _location(h), "outer": _location(h, up=False),
                  "thread": _location(h)}  # fmt: skip
    bb = native.bounding_box()
    metadata = {**_thread(native), "length": h, "outside_diameter": float(bb.max.X - bb.min.X)}
    return _part(native, "insert", f"heat-set insert {standard} {size}", connectors, metadata)


def bearing(kind: str, size: str, standard: str) -> CatalogPart:
    """A rolling bearing: `kind` is a bd_warehouse bearing class (SingleRowDeepGrooveBallBearing, …),
    `size` like "M8-22-7" (bore-outside-width), `standard` its data source, e.g. "SKT"."""
    from bd_warehouse import bearing as bearings

    cls = _class(bearings, bearings.Bearing, kind, "bearing")
    native = cls(size=size, bearing_type=standard)
    d, outside, width = (float(native.bearing_dict[k]) for k in ("d", "D", "B"))
    connectors = {"bottom": _location(up=False), "top": _location(width), "outer": _location(width, up=False),
                  "bore": _location(width)}  # fmt: skip
    label = str(native.bearing_dict.get("Designation", "")).strip("* ").strip()
    designation = f"{kind} {size}" + (f" ({label})" if label else "")
    return _part(native, "bearing", designation, connectors, {"bore": d, "outside": outside, "width": width})


def frames(native: Any) -> list[Any]:
    """Where a hole operation put the fastener's origin, in the part being built: a pattern connector."""
    if not native.hole_locations:
        raise ValueError(f"{type(native).__name__} has not been used by a hole operation in this part yet")
    return list(native.hole_locations)


def __getattr__(name: str) -> Any:
    """bd_warehouse's hole operations, re-exported: ClearanceHole, TapHole, InsertHole, ThreadedHole, PressFitHole."""
    if name in ("ClearanceHole", "TapHole", "InsertHole", "ThreadedHole"):
        from bd_warehouse import fastener

        return getattr(fastener, name)
    if name == "PressFitHole":
        from bd_warehouse import bearing as bearings

        return bearings.PressFitHole
    raise AttributeError(name)


class BdWarehouseProvider:
    name = NAME
    usage = "from hitlcad.builtin.catalogs.bd_warehouse import screw, nut, washer, heat_set_insert, bearing, frames"

    def _items(self) -> list[CatalogItem]:
        from bd_warehouse import bearing as bearings
        from bd_warehouse import fastener

        items = []
        families = [("screw", fastener.Screw, "screw('{kind}', '{size}', <length>, '{std}')"),
                    ("nut", fastener.Nut, "nut('{kind}', '{size}', '{std}')"),
                    ("washer", fastener.Washer, "washer('{kind}', '{size}', '{std}')")]  # fmt: skip
        for family, base, call in families:
            for cls in _subclasses(base):
                fam = "insert" if cls is fastener.HeatSetNut else family
                template = "heat_set_insert('{size}', '{std}')" if fam == "insert" else call
                for std in sorted(cls.types()):
                    sizes = list(cls.sizes(std))
                    items.append(CatalogItem(provider=NAME, family=fam, designation=f"{cls.__name__} {std}",
                                             call=template.format(kind=cls.__name__, size=sizes[0] if sizes else "?", std=std),
                                             sizes=sizes, connectors=CONNECTORS[fam], material=MATERIAL[fam]))  # fmt: skip
        for cls in _subclasses(bearings.Bearing):
            for std in sorted(cls.types()):
                for size in cls.sizes(std):
                    label = str(cls.bearing_data[size].get(f"{std}:Designation", "")).strip("* ").strip()
                    items.append(CatalogItem(
                        provider=NAME, family="bearing", designation=f"{cls.__name__} {size}" + (f" ({label})" if label else ""),
                        call=f"bearing('{cls.__name__}', '{size}', '{std}')", sizes=[size],
                        connectors=CONNECTORS["bearing"], material=MATERIAL["bearing"]))  # fmt: skip
        return items

    def search(self, query: str, limit: int) -> list[CatalogItem]:
        words = query.lower().replace("×", " ").split()
        scored = []
        for item in self._index:
            text = " ".join([item.family, item.designation, *item.sizes]).lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((-score, len(item.designation), item))
        scored.sort(key=lambda s: (s[0], s[1]))
        return [self._sized(s[2], words) for s in scored[:limit]]

    @staticmethod
    def _sized(item: CatalogItem, words: list[str]) -> CatalogItem:
        """The item with its call made for the size the query names, when it names one."""
        size = next((s for s in item.sizes for w in words if s.lower().startswith(w)), None)
        if size is None:
            return item
        return item.model_copy(update={"call": item.call.replace(f"'{item.sizes[0]}'", f"'{size}'", 1)})

    @cached_property
    def _index(self) -> list[CatalogItem]:
        return self._items()


PROVIDER = BdWarehouseProvider()
