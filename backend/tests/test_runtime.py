"""The model runtime: interface values evaluated lazily, and exactly what each piece of code read."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hitlcad.backends.runtime import ModelError, Runtime, plan
from hitlcad.build.cache import PartCache
from hitlcad.core.report import Reads

from .conftest import write_design


@pytest.fixture
def src(tmp_path: Path) -> Path:
    return write_design(
        tmp_path / "src",
        {
            "interfaces.py": "def bolt_r(p, i):\n    return p['od'] / 2 - 5\n\n\ndef holes(p, i):\n"
            "    return [[i.bolt_r, 0], [-i.bolt_r, 0]]\n\n\ndef _helper():\n    return 1\n",
            "lib/shapes.py": "def double(x):\n    return 2 * x\n",
            "parts/a.py": "from lib.shapes import double\n\n\ndef build(p, i):\n    return {'a': (double(p['t']), i.holes)}\n",
            "parts/b.py": "def build(p, i):\n    return {'b': p['t']}\n",
            "assembly.py": "def build(p, i):\n    return {'a': [i.bolt_r], 'b': [0, 1]}\n",
        },
    )


def test_each_file_records_what_it_read(src: Path) -> None:
    rt = Runtime(src, {"od": 40, "t": 3, "unused": 1})
    result, reads = rt.call_build("parts/a.py", "pa")
    assert result == {"a": (6, [[15.0, 0], [-15.0, 0]])}
    assert reads.parameters == {"t"} and reads.interfaces == {"holes"} and reads.modules == {"lib/shapes.py"}
    _, reads_b = rt.call_build("parts/b.py", "pb")
    assert reads_b.parameters == {"t"} and not reads_b.interfaces and not reads_b.modules
    values = rt.all_interfaces()
    assert values["holes"]["reads"] == {"parameters": [], "interfaces": ["bolt_r"], "modules": []}
    assert values["bolt_r"]["reads"]["parameters"] == ["od"] and "_helper" not in values


def test_plan_places_instances_before_any_geometry(src: Path) -> None:
    rt = Runtime(src, {"od": 40, "t": 3})
    out = plan(rt, {}, lambda v: [[float(v)] * 4] * 4)
    assert set(out["placements"]) == {"a", "b"} and len(out["placements"]["b"]) == 2
    assert out["assembly_reads"]["interfaces"] == ["bolt_r"] and set(out["interfaces"]) == {"bolt_r", "holes"}


def test_model_errors_are_told_in_the_design_s_own_terms(tmp_path: Path) -> None:
    bad = write_design(
        tmp_path / "bad",
        {
            "interfaces.py": "def a(p, i):\n    return i.b\n\n\ndef b(p, i):\n    return i.a\n\n\ndef obj(p, i):\n    return object()\n",
            "parts/x.py": "def build(p, i):\n    return p['missing']\n",
            "parts/y.py": "def build(p, i):\n    return 1 / 0\n",
        },
    )
    rt = Runtime(bad, {"w": 1})
    with pytest.raises(ModelError, match="cycle: a -> b -> a"):
        rt.interface("a")
    with pytest.raises(ModelError, match="numbers, strings, booleans"):
        rt.interface("obj")
    with pytest.raises(ModelError, match="no parameter 'missing'; parameters: w"):
        rt.call_build("parts/x.py", "px")
    try:
        rt.call_build("parts/y.py", "py")
    except ZeroDivisionError as e:
        assert rt.describe(e).splitlines() == [
            "  parts/y.py line 2, in build: return 1 / 0",
            "ZeroDivisionError: division by zero",
        ]
    sys.path.remove(str(bad.resolve()))


def test_the_part_cache_keys_on_exactly_what_a_file_read(tmp_path: Path) -> None:
    reads = Reads(parameters=["t"], interfaces=["holes"], modules=["lib/shapes.py"])
    sources = {"parts/a.py": "code", "lib/shapes.py": "helper"}
    key = PartCache.key("be", "parts/a.py", sources, {"t": 3, "od": 40}, {"holes": [1]}, reads)
    assert key == PartCache.key("be", "parts/a.py", sources, {"t": 3, "od": 99}, {"holes": [1]}, reads)  # od: not read
    assert key != PartCache.key("be", "parts/a.py", sources, {"t": 4, "od": 40}, {"holes": [1]}, reads)
    assert key != PartCache.key("be", "parts/a.py", sources, {"t": 3}, {"holes": [2]}, reads)
    assert key != PartCache.key(
        "be", "parts/a.py", {**sources, "lib/shapes.py": "changed"}, {"t": 3}, {"holes": [1]}, reads
    )
    assert key != PartCache.key("other", "parts/a.py", sources, {"t": 3}, {"holes": [1]}, reads)
    assert PartCache.key("be", "parts/a.py", sources, {}, {"holes": [1]}, reads) is None  # t no longer exists
