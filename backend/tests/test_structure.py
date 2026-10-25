"""The structure analysis: what design.json declares refers to what exists."""

from __future__ import annotations

from hitlcad import materials
from hitlcad.analysis.structure import structure_results
from hitlcad.core.design import DesignSpec


def spec(**extra) -> DesignSpec:
    data = {
        "name": "d",
        "backend": "build123d",
        "ground": "a",
        "parts": {
            "a": {"role": "x", "file": "parts/a.py", "material": "PLA", "connectors": {"top": {"kind": "face"}}},
            "b": {"role": "y", "file": "parts/a.py", "material": "PLA", "connectors": {"bottom": {"kind": "face"}}},
            "nut": {
                "role": "z",
                "file": "parts/a.py",
                "catalog": {"designation": "M3 nut", "fidelity": "exact"},
                "connectors": {"seat": {"kind": "face"}},
            },
        },
        "components": {"sub": {"design": "../sub"}},
    }
    return DesignSpec.model_validate(data | extra)


def details(results) -> list[str]:
    return [f"{r.subject}: {r.detail}" for r in results if r.status == "fail"]


def test_every_reference_must_resolve() -> None:
    bad = spec(
        mates=[
            {"type": "rigid", "a": "a.top", "b": "b.side"},
            {"type": "rigid", "a": "a.top", "b": "c.x"},
            {"type": "rigid", "a": "a.top", "b": "sub.nothing"},
        ],
        relations=[{"type": "gear", "a": "a.top", "b": "b.bottom"}],
        fasteners=[{"fastener": "b.bottom", "through": ["a.top"], "into": "nut.seat"}],
    )
    out = details(structure_results(bad, ["a", "b", "nut", "sub"], {"sub": {"out": "x.y"}}, materials.builtin()))
    assert any("declares no connector 'side'" in d for d in out)
    assert any("'c' is not a part or component" in d for d in out)
    assert any("exports no connector 'nothing'; exports: out" in d for d in out)
    assert any("joins a gear and a gear; a.top is a face" in d for d in out)
    assert any("b is not a catalog part" in d for d in out)


def test_placement_ground_and_materials_are_checked() -> None:
    out = structure_results(spec(parts={"a": {"role": "x", "file": "parts/a.py", "material": "unobtainium"},
                                        "b": {"role": "y", "file": "parts/a.py"}}),
                            ["b"], {}, materials.builtin())  # fmt: skip
    text = details(out)
    assert any("a is declared but assembly.py does not place it" in d for d in text)
    assert any("ground a is not placed" in d for d in text)
    assert any("'unobtainium' is not in the material registry" in d for d in text)
    assert any("b is made but has no material" in d for d in text)


def test_a_sound_structure_has_nothing_to_say() -> None:
    ok = spec(mates=[{"type": "rigid", "a": "a.top", "b": "b.bottom"}, {"type": "rigid", "a": "a.top", "b": "sub.out"}])
    assert details(structure_results(ok, ["a", "b", "nut", "sub"], {"sub": {"out": "x.y"}}, materials.builtin())) == []


def test_a_material_must_suit_its_part_s_process() -> None:
    milled = spec(manufacturing={"process": "cnc_milling"},
                  parts={"a": {"role": "x", "file": "parts/a.py", "material": "PLA"},
                         "b": {"role": "y", "file": "parts/a.py", "material": "aluminium_6061"},
                         "c": {"role": "z", "file": "parts/a.py", "material": "PLA",
                               "manufacturing": {"process": "fdm"}}})  # fmt: skip
    assert details(structure_results(milled, ["a", "b", "c", "sub"], {}, materials.builtin())) == [
        "a: a is PLA, which cnc_milling cannot make (PLA suits fdm): change its material or its process"
    ]
