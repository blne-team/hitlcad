"""The model runtime: interface values evaluated lazily, and exactly what each piece of code read; and the
process that runs it."""

from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil
import pytest

from hitlcad.backends.base import _ended
from hitlcad.backends.workers import Ticket, Workers
from hitlcad.build.cache import Inputs, PartCache
from hitlcad.config import Settings
from hitlcad.core.report import Reads
from hitlcad.execution import runtime
from hitlcad.execution.executor import LocalExecutor, _memory_exceeds
from hitlcad.execution.runtime import Exports, ModelError, Runtime, plan

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
    out = plan(rt, lambda v: [[float(v)] * 4] * 4)
    assert set(out["placements"]) == {"a", "b"} and len(out["placements"]["b"]) == 2
    assert out["assembly_reads"]["interfaces"] == ["bolt_r"] and set(out["interfaces"]) == {"bolt_r", "holes"}


def test_model_errors_are_told_in_the_design_s_own_terms(tmp_path: Path) -> None:
    bad = write_design(
        tmp_path / "bad",
        {
            "interfaces.py": "import numpy\n\n\ndef a(p, i):\n    return i.b\n\n\ndef b(p, i):\n    return i.a\n\n\n"
            "def obj(p, i):\n    return object()\n\n\ndef counts(p, i):\n    return [numpy.int64(3), numpy.float32(0.5)]\n\n\n"
            "def ratio(p, i):\n    return numpy.float32('nan')\n",
            "parts/x.py": "def build(p, i):\n    return p['missing']\n",
            "parts/y.py": "def build(p, i):\n    return 1 / 0\n",
        },
    )
    rt = Runtime(bad, {"w": 1})
    with pytest.raises(ModelError, match="cycle: a -> b -> a") as cycle:
        rt.interface("a")
    assert cycle.value.as_json()["code"] == "model.interface_cycle"
    with pytest.raises(ModelError, match="numbers, strings, booleans"):
        rt.interface("obj")
    assert [type(v) for v in rt.interface("counts")] == [int, float]  # numpy's numbers, as plain ones
    with pytest.raises(ModelError, match="interface value 'ratio' is nan"):
        rt.interface("ratio")
    with pytest.raises(ModelError, match="no parameter 'missing'; parameters: w"):
        rt.call_build("parts/x.py", "px")
    try:
        rt.call_build("parts/y.py", "py")
    except ZeroDivisionError as e:
        assert rt.failure(e, "parts/y.py") == {
            "code": "model.exception",
            "message": "  parts/y.py line 2, in build: return 1 / 0\nZeroDivisionError: division by zero",
            "where": {"file": "parts/y.py", "line": 2},
        }
    sys.path.remove(str(bad.resolve()))


def test_sys_exit_in_the_design_s_code_is_its_error_not_the_worker_s_end(tmp_path: Path) -> None:
    src = write_design(tmp_path / "exits", {"assembly.py": "import sys\n\n\ndef build(p, i):\n    sys.exit(3)\n"})
    rt = Runtime(src, {})
    with pytest.raises(ModelError) as stopped:
        plan(rt, lambda v: v)
    assert stopped.value.as_json()["code"] == "model.exception" and "SystemExit: 3" in str(stopped.value)
    assert stopped.value.as_json()["where"] == {"file": "assembly.py", "line": 5}
    rt.close()


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


# A build process that starts a grandchild (one that would run for a minute) and says which, then waits on it or not
GRANDCHILD = ("import subprocess, sys; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "print(child.pid, flush=True)")  # fmt: skip


async def gone(pid: int) -> bool:
    """Whether process `pid` ends within a few seconds (a zombie has ended: only its parent's wait is left)."""
    for _ in range(50):
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        await asyncio.sleep(0.1)
    psutil.Process(pid).kill()
    return False


async def test_a_process_run_past_its_deadline_ends_with_what_it_started(tmp_path: Path) -> None:
    run = await LocalExecutor().run([sys.executable, "-c", GRANDCHILD + "; child.wait()"], tmp_path, timeout_s=1)
    assert (run.stopped, run.limit) == ("exceeded the 1s time limit", "time")
    assert await gone(int(run.output.split()[0]))


async def test_a_process_run_to_its_end_leaves_nothing_it_started_behind(tmp_path: Path) -> None:
    run = await LocalExecutor().run([sys.executable, "-c", GRANDCHILD], tmp_path, timeout_s=30)
    assert (run.returncode, run.stopped) == (0, None)
    assert await gone(int(run.output.split()[0]))


async def test_a_process_inherits_the_environment(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("SOME_TOOL_SETTING", "kept")
    monkeypatch.setenv("HITLCAD_PORT", "1234")
    probe = "import os, tempfile; print(os.environ.get('SOME_TOOL_SETTING'), os.environ.get('HITLCAD_PORT'), tempfile.gettempdir())"
    run = await LocalExecutor().run([sys.executable, "-c", probe], tmp_path, timeout_s=30)
    assert run.output.strip() == f"kept None {tmp_path}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
async def test_a_process_ended_by_a_signal_says_which_and_why(tmp_path: Path) -> None:
    kill = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    run = await LocalExecutor().run([sys.executable, "-c", kill], tmp_path, timeout_s=30)
    assert _ended(run.returncode) == "the build process was killed by the system, most often for running out of memory"
    other = getattr(signal, "SIGRTMIN", signal.SIGUSR1)  # a real-time one where the platform has them (Linux)
    assert _ended(-other) == f"the build process was ended by signal {int(other)} ({signal.strsignal(other)})"


async def test_the_memory_limit_is_reached_only_by_using_memory() -> None:
    """A process that ended before the first sample holds nothing: its sampler waits, it does not end."""
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(_memory_exceeds(ended.pid, 1 << 30), 1.5)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows ends a build with its server through a job object")
async def test_a_build_ends_itself_when_its_server_is_killed(tmp_path: Path) -> None:
    """A server killed outright cannot hold its builds' limits: each build ends itself once its server is gone."""
    build = "import time; from hitlcad.execution import runtime; runtime.exit_with_parent(); time.sleep(30)"
    server = (f"import os, subprocess, sys, time; build = subprocess.Popen([sys.executable, '-c', {build!r}], "
              "start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(build.pid, flush=True); time.sleep(1); os._exit(0)")  # fmt: skip
    pid = int(subprocess.run([sys.executable, "-c", server], capture_output=True, text=True, check=True).stdout)
    assert await gone(pid), "the build outlived its server"


def test_cached_parts_depend_on_the_catalogs_and_sdk_they_ran_with(tmp_path: Path, monkeypatch: Any) -> None:
    from importlib.metadata import version

    from hitlcad.builtin.build123d.backend import Build123dBackend

    def backend() -> Build123dBackend:
        return Build123dBackend(Settings())

    root = Path(runtime.__file__).parents[1]
    code = {p.relative_to(root).as_posix() for p in backend().code()}
    assert {
        "builtin/build123d/entry.py",
        "execution/runtime.py",
        "sdk/__init__.py",
        "builtin/catalogs/bd_warehouse.py",
    } <= code
    assert backend().libraries() == {name: version(name) for name in ("build123d", "bd_warehouse")}
    catalog = tmp_path / "catalog.py"
    monkeypatch.setattr(Build123dBackend, "code", lambda self: [catalog])
    catalog.write_text("A = 1", encoding="utf-8")
    before = backend().fingerprint()
    catalog.write_text("A = 2", encoding="utf-8")
    assert backend().fingerprint() != before


def test_every_code_the_build_process_writes_has_a_kind() -> None:
    """The child process is standard library only, so it names its codes as strings: each one is in the catalog,
    with the owner it says."""
    from hitlcad.kernel.errors import catalog

    kinds = catalog()
    assert set(runtime.CODES) <= set(kinds)
    assert {kinds[c].owner for c in runtime.CODES} == {"design", "hitlcad"}


def test_a_design_s_modules_are_forgotten_after_its_job(src: Path) -> None:
    """A worker runs one build after another: the next imports the design's files as they are then."""
    rt = Runtime(src, {"od": 40, "t": 3})
    rt.call_build("parts/a.py", "pa")
    rt.all_interfaces()
    rt.close()
    assert str(rt.src) not in sys.path
    assert not [m for m in sys.modules.values() if str(getattr(m, "__file__", None) or "").startswith(str(rt.src))]


class Counting:
    """An adapter whose shapes are strings, their own digest, exported as one file of their text."""

    modes: dict[str, Any] = {}

    def __init__(self) -> None:
        self.exported: list[str] = []

    def digest(self, shape: str) -> str:
        return shape

    def export(self, shape: str, name: str, out: Path, tolerance: dict[str, float]) -> dict[str, Any]:
        self.exported.append(shape)
        (out / f"{name}.stl").write_text(shape * 100, encoding="utf-8")
        return {"mesh": f"{name}.stl", "objects": f"{name}.stl"}


def test_a_worker_exports_a_shape_once_while_it_keeps_it(tmp_path: Path) -> None:
    adapter, exports, tolerance = Counting(), Exports(), {"linear": 0.01, "angular": 0.1}
    exports.budget = 250  # two of the 100-byte files below, not three
    for shape in ("a", "b", "a"):
        exports.export(adapter, shape, "p", tmp_path, tolerance)  # type: ignore[arg-type]
    assert adapter.exported == ["a", "b"]  # the second "a" written from what was kept
    assert (tmp_path / "p.stl").read_text(encoding="utf-8") == "a" * 100
    exports.export(adapter, "a", "q", tmp_path, tolerance)  # type: ignore[arg-type]  # another part's name: its own files
    exports.export(adapter, "b", "p", tmp_path, tolerance)  # type: ignore[arg-type]  # let go for the q's
    assert adapter.exported == ["a", "b", "a", "b"] and exports.bytes <= exports.budget


#: A build worker as the entry scripts are: `runtime.serve` with an adapter of its own modes only.
WORKER = """
import os, sys, tempfile, time
from hitlcad.execution import runtime
seen = []
def note(job):
    seen.append(job["n"])
    print("job", job["n"])
    return {"pid": os.getpid(), "seen": seen, "tmp": tempfile.gettempdir(), "cwd": os.getcwd()}
def stall(job):
    time.sleep(30)
def end(job):
    print("ending", flush=True)
    os._exit(3)
class Modes:
    modes = {"note": note, "stall": stall, "end": end}
print("loaded")
runtime.serve(Modes())
"""


def job(tmp_path: Path, name: str, mode: str, **fields: Any) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "job.json").write_text(json.dumps({"mode": mode, **fields}), encoding="utf-8")
    return folder / "job.json"


async def test_a_worker_runs_job_after_job_in_each_job_s_folder_keeping_what_it_holds(tmp_path: Path) -> None:
    worker = await LocalExecutor().worker([sys.executable, "-c", WORKER])
    try:
        first = await worker.job(job(tmp_path, "one", "note", n=1), timeout_s=30, memory_mb=4096)
        second = await worker.job(job(tmp_path, "two", "note", n=2), timeout_s=30, memory_mb=4096)
        assert (first.returncode, first.stopped, first.output.strip(), second.output.strip()) == (
            0,
            None,
            "job 1",
            "job 2",
        )
        result = json.loads((tmp_path / "two" / "result.json").read_text(encoding="utf-8"))
        assert result["seen"] == [1, 2] and result["pid"] in (
            worker.pid,
            *(c.pid for c in psutil.Process(worker.pid).children()),
        )
        assert result["tmp"] == result["cwd"] == str((tmp_path / "two").resolve())
        assert "loaded" in worker.started.log.read_text(encoding="utf-8")  # what it said before any job: its own log
    finally:
        await worker.stop()
    assert not worker.alive and not worker.started.folder.exists()


async def test_a_worker_past_a_job_s_deadline_or_ended_by_one_is_gone_and_says_why(tmp_path: Path) -> None:
    stalled = await LocalExecutor().worker([sys.executable, "-c", WORKER])
    run = await stalled.job(job(tmp_path, "stall", "stall"), timeout_s=1, memory_mb=4096)
    assert (run.stopped, run.limit, stalled.alive) == ("exceeded the 1s time limit", "time", False)
    ended = await LocalExecutor().worker([sys.executable, "-c", WORKER])
    run = await ended.job(job(tmp_path, "end", "end"), timeout_s=30, memory_mb=4096)
    assert (run.returncode, run.stopped, ended.alive) == (3, None, False) and "ending" in run.output
    held = await LocalExecutor().worker([sys.executable, "-c", WORKER])
    run = await held.job(job(tmp_path, "held", "stall"), timeout_s=30, memory_mb=1)  # what it holds as it waits counts
    assert (run.stopped, run.limit, held.alive) == ("used more than 1 MB of memory", "memory", False)
    for worker in (stalled, ended, held):
        await worker.stop()


async def test_a_backend_keeps_a_worker_per_design_and_replaces_one_it_needs_for_another(tmp_path: Path) -> None:
    workers, argv = Workers(LocalExecutor(), size=1), [sys.executable, "-c", WORKER]

    async def pid(design: str, name: str) -> int:
        await workers.run(Ticket(), argv, design, None, job(tmp_path, name, "note", n=0), timeout_s=30, memory_mb=4096)
        return json.loads((tmp_path / name / "result.json").read_text(encoding="utf-8"))["pid"]

    a = await pid("/a", "a1")
    assert await pid("/a", "a2") == a  # its own worker, kept
    b = await pid("/b", "b1")
    assert b != a and await gone(a)  # one worker at most: the other design's, idle, replaced
    assert await pid("/a", "a3") not in (a, b)  # nothing of /b's carries back to /a
    await workers.close()

    workers = Workers(LocalExecutor(), size=2)
    a = await pid("/a", "a4")
    for _ in range(100):  # the spare, started as /a started its own
        if spare := next((h.worker.pid for h in workers._idle if h.design is None), None):
            break
        await asyncio.sleep(0.05)
    assert await pid("/b", "b2") == spare  # the next design takes it, its libraries loaded
    assert psutil.pid_exists(a) and await pid("/a", "a5") == a  # no design's worker is replaced for a spare
    await workers.close()


async def test_a_job_stopped_while_it_replaces_a_worker_gives_its_place_back(tmp_path: Path) -> None:
    workers, argv = Workers(LocalExecutor(), size=1), [sys.executable, "-c", WORKER]
    run = {"timeout_s": 30, "memory_mb": 4096}
    await workers.run(Ticket(), argv, "/a", None, job(tmp_path, "a", "note", n=0), **run)
    (idle,) = workers._idle
    stopping, stop = asyncio.Event(), idle.worker.stop

    async def stuck() -> None:
        stopping.set()
        await asyncio.Event().wait()

    idle.worker.stop = stuck  # type: ignore[method-assign]
    replacing = asyncio.create_task(workers.run(Ticket(), argv, "/b", None, job(tmp_path, "b", "note", n=0), **run))
    await stopping.wait()
    replacing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replacing
    await stop()
    await asyncio.wait_for(workers.run(Ticket(), argv, "/c", None, job(tmp_path, "c", "note", n=0), **run), 60)
    await workers.close()
