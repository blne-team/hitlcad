"""The model runtime: interface values evaluated lazily, and exactly what each piece of code read; and the
process that runs it."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from hitlcad.backends.executor import LocalExecutor
from hitlcad.backends.runtime import ModelError, Runtime, plan
from hitlcad.backends.subprocess_backend import _ended
from hitlcad.build.cache import Inputs, PartCache
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
    declared = {"a": ["top"]}

    def key(declared: dict[str, list[str]] = declared, **change: Any) -> str | None:
        fields = {"fingerprint": "be", "sources": sources, "assets": {}, "values": {"t": 3, "od": 40},
                  "interfaces": {"holes": [1]}, **change}  # fmt: skip
        return PartCache.key("parts/a.py", declared, Inputs(**fields), reads)

    assert key() == key(values={"t": 3, "od": 99})  # od: not read
    assert key() != key(values={"t": 4, "od": 40})
    assert key() != key(interfaces={"holes": [2]})
    assert key() != key(sources={**sources, "lib/shapes.py": "changed"})
    assert key() != key(fingerprint="other")
    assert key() != key(assets={"vendor/m.step": "digest"})
    assert key() != key({"a": ["top", "bottom"]})  # design.json asks for another connector
    assert key(values={}) is None  # t no longer exists


async def test_a_build_process_past_its_deadline_ends_with_what_it_started(tmp_path: Path) -> None:
    # The child starts a grandchild that would write a file after the deadline, then waits on it.
    late = "import pathlib, time; time.sleep(3); pathlib.Path('alive').touch()"
    parent = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {late!r}])"
    run = await LocalExecutor().run([sys.executable, "-c", parent], tmp_path, timeout_s=1)
    assert run.stopped == "exceeded the 1s time limit"
    await asyncio.sleep(4)
    assert not (tmp_path / "alive").exists()


async def test_a_build_process_that_finishes_leaves_nothing_it_started_behind(tmp_path: Path) -> None:
    late = "import pathlib, time; time.sleep(2); pathlib.Path('alive').touch()"
    parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {late!r}])"
    run = await LocalExecutor().run([sys.executable, "-c", parent], tmp_path, timeout_s=30)
    assert (run.returncode, run.stopped) == (0, None)
    await asyncio.sleep(3)
    assert not (tmp_path / "alive").exists()


async def test_a_build_process_over_its_memory_is_stopped(tmp_path: Path) -> None:
    hog = "import time; held = b'x' * (400 << 20); time.sleep(30)"
    run = await LocalExecutor().run([sys.executable, "-c", hog], tmp_path, timeout_s=60, memory_mb=200)
    assert run.stopped == "used more than 200 MB of memory"
    assert run.duration_s < 20


async def test_a_build_process_inherits_the_environment(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("SOME_TOOL_SETTING", "kept")
    monkeypatch.setenv("HITLCAD_PORT", "1234")
    probe = "import os, tempfile; print(os.environ.get('SOME_TOOL_SETTING'), os.environ.get('HITLCAD_PORT'), tempfile.gettempdir())"
    run = await LocalExecutor().run([sys.executable, "-c", probe], tmp_path, timeout_s=30)
    assert run.output.strip() == f"kept None {tmp_path}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
async def test_a_build_process_ended_by_a_signal_says_why(tmp_path: Path) -> None:
    kill = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    run = await LocalExecutor().run([sys.executable, "-c", kill], tmp_path, timeout_s=30)
    assert _ended(run.returncode) == "the build process was killed by the system, most often for running out of memory"
