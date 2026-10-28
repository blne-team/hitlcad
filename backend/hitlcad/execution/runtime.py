"""The model runtime, and the build worker that serves it, in every backend's own Python. Standard library only,
so it runs in any Python, Blender's bundled one included.

A design's code is `interfaces.py` (one function per mating value), part files under `parts/` and
`assembly.py`. `Runtime` runs them with `p` (parameter values) and `i` (interface values) and
records exactly which keys each one read, so hitlcad knows what every part depends on: for its
cache, and for impact questions.

A build worker (`serve`) is a backend's process for one design, kept between its builds as a notebook's
kernel is kept between cells: it runs job after job, each a job.json whose path comes on stdin, keeping what
it loaded (its backend's libraries, the plugins' model packages) and, by each part shape's content
(`Adapter.digest`), what it exported, so a part that comes out of a part file as it did before is not
exported again. What is the design's own (its sources on `sys.path`, the modules its files ran as and
imported from lib/) is dropped after every job, so the next runs from the files as they are; what its code
changes in a library stays, as it would in a notebook, until the worker is replaced. The backend's entry
script gives the worker an `Adapter`: what its part values are and how they export.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import numbers
import os
import sys
import tempfile
import threading
import time
import traceback
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

INTERFACES = "interfaces.py"
ASSEMBLY = "assembly.py"
LIB = "lib"
#: How often (s) a build checks that the server that started it is still there.
PARENT_POLL_S = 1.0
#: What a job prints, and its libraries print, beside its job.json.
OUTPUT_FILE = "output.log"
#: Starts the line a worker answers a job with, on its own stdout: nothing a job prints reaches it.
DONE = "\x1ehitlcad-done "
#: The fields of a built part that name its files (`BuiltPart`).
FILES = ("mesh", "step", "objects", "view")


# The codes of the errors model code meets here (`hitlcad.kernel.errors` has their kinds); the worker writes
# each as {"code", "message", "where": {"file", "line"}}.
EXCEPTION = "model.exception"
NO_BUILD = "model.no_build_function"
RETURN = "model.return_type"
CONNECTORS = "model.connectors_missing"
PARTS = "model.parts_mismatch"
FRAME = "model.frame_type"
SHAPE = "model.shape"
CYCLE = "model.interface_cycle"
INTERFACE = "model.unknown_interface"
PARAMETER = "model.unknown_parameter"
VALUE = "model.interface_value"
PART_FILE = "build.part_file_missing"
BUG = "hitlcad.bug"
CODES = (EXCEPTION, NO_BUILD, RETURN, CONNECTORS, PARTS, FRAME, SHAPE, CYCLE, INTERFACE, PARAMETER, VALUE, PART_FILE,
         BUG)  # fmt: skip


class ModelError(Exception):
    """An error in the design's code, already described in its own terms, with its code."""

    def __init__(self, code: str, message: str, file: str | None = None, line: int | None = None) -> None:
        super().__init__(message)
        self.code, self.file, self.line = code, file, line

    def as_json(self) -> dict[str, Any]:
        where = {"file": self.file, "line": self.line} if self.file else None
        return {"code": self.code, "message": str(self), "where": where}


class Reads:
    def __init__(self) -> None:
        self.parameters: set[str] = set()
        self.interfaces: set[str] = set()
        self.modules: set[str] = set()

    def as_json(self) -> dict[str, list[str]]:
        return {"parameters": sorted(self.parameters), "interfaces": sorted(self.interfaces),
                "modules": sorted(self.modules)}  # fmt: skip


class Runtime:
    """Runs one design's code from `src` with parameter values `values`."""

    def __init__(self, src: Path, values: dict[str, Any]) -> None:
        self.src = src.resolve()
        self.values = values
        self._stack: list[Reads] = []
        self._interfaces: dict[str, Any] = {}
        self._interface_reads: dict[str, Reads] = {}
        self._evaluating: list[str] = []
        self._module: ModuleType | None = None
        if str(self.src) not in sys.path:
            sys.path.insert(0, str(self.src))
        self.p = _Params(self)
        self.i = _Interfaces(self)

    # ------------------------------------------------------------------ reads

    @contextmanager
    def reading(self) -> Iterator[Reads]:
        reads = Reads()
        self._stack.append(reads)
        try:
            yield reads
        finally:
            self._stack.pop()

    def _note(self, kind: str, name: str) -> None:
        if self._stack:
            getattr(self._stack[-1], kind).add(name)

    # ------------------------------------------------------------------ interfaces

    def _interfaces_module(self) -> ModuleType | None:
        if self._module is None and (self.src / INTERFACES).is_file():
            self._module = self.load(INTERFACES, "hitlcad_interfaces")
        return self._module

    def interface_names(self) -> list[str]:
        module = self._interfaces_module()
        if module is None:
            return []
        return [n for n, v in vars(module).items()
                if callable(v) and not n.startswith("_") and getattr(v, "__module__", None) == module.__name__]  # fmt: skip

    def interface(self, name: str) -> Any:
        self._note("interfaces", name)
        if name in self._interfaces:
            return self._interfaces[name]
        if name in self._evaluating:
            raise ModelError(CYCLE, "interfaces.py: values depend on each other in a cycle: "
                             + " -> ".join([*self._evaluating, name]), INTERFACES)  # fmt: skip
        module = self._interfaces_module()
        fn = getattr(module, name, None) if module else None
        if name.startswith("_") or not callable(fn):
            known = ", ".join(self.interface_names()) or "none (interfaces.py has no values)"
            raise ModelError(INTERFACE, f"no interface value {name!r}; interface values: {known}")
        self._evaluating.append(name)
        try:
            with self.reading() as reads:
                value = fn(self.p, self.i)
        finally:
            self._evaluating.pop()
        self._interfaces[name] = _plain(value, f"interface value {name!r}")
        self._interface_reads[name] = reads
        return self._interfaces[name]

    def all_interfaces(self) -> dict[str, dict[str, Any]]:
        for name in self.interface_names():
            self.interface(name)
        return {n: {"value": v, "reads": self._interface_reads[n].as_json()} for n, v in self._interfaces.items()}

    # ------------------------------------------------------------------ modules

    def load(self, relative: str, module_name: str, *, fresh_lib: bool = False) -> ModuleType:
        """Import one of the design's files afresh. With `fresh_lib`, its lib/ modules are imported afresh too,
        so the ones it pulls in can be told from the ones earlier files did."""
        if fresh_lib:
            for name in [m for m, mod in sys.modules.items() if _in(getattr(mod, "__file__", None), self.src / LIB)]:
                del sys.modules[name]
        path = self.src / relative
        if not path.is_file():
            raise ModelError(PART_FILE, f"{relative} does not exist", relative)
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def close(self) -> None:
        """Forget the design: its sources' place on `sys.path`, and every module loaded from them."""
        for name in [m for m, mod in sys.modules.items() if _in(getattr(mod, "__file__", None), self.src)]:
            del sys.modules[name]
        while str(self.src) in sys.path:
            sys.path.remove(str(self.src))

    def lib_modules(self) -> set[str]:
        return {Path(mod.__file__).relative_to(self.src).as_posix() for mod in list(sys.modules.values())
                if _in(getattr(mod, "__file__", None), self.src / LIB)}  # fmt: skip

    def call_build(self, relative: str, module_name: str) -> tuple[Any, Reads]:
        """Run `build(p, i)` of a design file; returns what it returned and what it read."""
        with self.reading() as reads:
            module = self.load(relative, module_name, fresh_lib=True)
            build = getattr(module, "build", None)
            if not callable(build):
                raise ModelError(NO_BUILD, f"{relative} must define a top-level function build(p, i)", relative)
            result = build(self.p, self.i)
            reads.modules |= self.lib_modules()
        return result, reads

    # ------------------------------------------------------------------ errors

    def describe(self, exc: BaseException) -> ModelError:
        """An exception in the design's own terms: the frames of its files, then the error, placed at the last of
        them."""
        if isinstance(exc, ModelError):
            return exc
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if _in(f.filename, self.src)]
        files = [Path(f.filename).resolve().relative_to(self.src).as_posix() for f in frames]
        lines = [
            f"  {file} line {f.lineno}, in {f.name}: {f.line or ''}" for file, f in zip(files, frames, strict=True)
        ]
        last = (files[-1], frames[-1].lineno) if frames else (None, None)
        return ModelError(EXCEPTION, "\n".join(lines + [f"{type(exc).__name__}: {exc}"]), *last)

    def failure(self, exc: BaseException, file: str) -> dict[str, Any]:
        """What went wrong building a part file, as the worker writes it: placed in the file, at least."""
        error = self.describe(exc)
        error.file = error.file or file
        return error.as_json()


class _Params(Mapping[str, Any]):
    """The parameter values, recording each one read."""

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime

    def __getitem__(self, key: str) -> Any:
        if key not in self._rt.values:
            raise ModelError(PARAMETER, f"no parameter {key!r}; parameters: {', '.join(self._rt.values) or 'none'}")
        self._rt._note("parameters", key)
        return self._rt.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._rt.values)

    def __len__(self) -> int:
        return len(self._rt.values)


class _Interfaces:
    """The interface values, as attributes: `i.lid_bolt_xy`."""

    def __init__(self, runtime: Runtime) -> None:
        object.__setattr__(self, "_rt", runtime)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self._rt.interface(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise ModelError(VALUE, "interface values are computed by interfaces.py, not assigned")


def _in(filename: str | None, folder: Path) -> bool:
    return bool(filename) and Path(filename).resolve().is_relative_to(folder)


def _plain(value: Any, what: str) -> Any:
    """A JSON value (numbers, strings, booleans, lists and string-keyed dicts of them)."""
    if isinstance(value, bool | str) or value is None:
        return value
    if isinstance(value, numbers.Integral):  # numpy's integers too
        return int(value)
    if isinstance(value, numbers.Real):  # numpy's floats too
        if not math.isfinite(number := float(value)):
            raise ModelError(VALUE, f"{what} is {number}", INTERFACES)
        return number
    if isinstance(value, list | tuple):
        return [_plain(v, what) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: _plain(v, what) for k, v in value.items()}
    raise ModelError(VALUE, f"{what} is a {type(value).__name__}; interface values are numbers, strings, booleans, "
                     "and lists or dicts of them", INTERFACES)  # fmt: skip


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else [value]


def exit_with_parent() -> None:
    """End this process once the one that started it is gone. The server holds a build's limits (its deadline,
    its memory); a server killed outright cannot, so its builds end themselves rather than run on without any.
    On Windows a job object ends them with the server already (and a parent's pid never changes there)."""
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(PARENT_POLL_S)
        os._exit(1)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()


class Adapter(Protocol):
    """What a backend's entry script gives its worker: what the backend's part values are, and how they export."""

    #: Jobs of the backend's own that run no model code, by mode (Blender's `assembly`): job -> result fields.
    modes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]

    def placement(self, value: Any) -> list[list[float]]:
        """A placement or connector frame as a 4 × 4 matrix, or a `ModelError` saying what it takes."""

    def begin(self) -> None:
        """Ready to run a part file (Blender: an empty scene)."""

    def unpack(self, what: str, value: Any) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
        """A part file's value for one part (`what` names it): its shape, its connectors by name, and its catalog
        entry (provider, designation, material, metadata) when it is a catalog part; or a `ModelError`."""

    def digest(self, shape: Any) -> str | None:
        """The shape's content, everything its exports show: equal digests export alike. None: never reused."""

    def export(self, shape: Any, name: str, out: Path, tolerance: dict[str, float]) -> dict[str, Any]:
        """The part's files under `out` in its own frame, named by `BuiltPart`'s fields (`FILES`, relative to
        `out`), with `valid` where the worker checks shapes."""


class Exports:
    """What a worker exported, by the shape's digest, the part's name and the mesh tolerances: the files' bytes,
    the least recently used let go first once they hold more than `budget` bytes."""

    def __init__(self) -> None:
        self.budget = 0
        self.bytes = 0
        self._kept: OrderedDict[str, tuple[dict[str, Any], dict[str, bytes]]] = OrderedDict()

    def export(self, adapter: Adapter, shape: Any, name: str, out: Path, tolerance: dict[str, float]) -> dict[str, Any]:
        digest = adapter.digest(shape)
        key = digest and hashlib.sha256(json.dumps([digest, name, tolerance], sort_keys=True).encode()).hexdigest()
        if key in self._kept:
            self._kept.move_to_end(key)
            fields, files = self._kept[key]
            for rel, data in files.items():
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_bytes(data)
            return fields
        fields = adapter.export(shape, name, out, tolerance)
        if key:
            files = {fields[f]: (out / fields[f]).read_bytes() for f in FILES if fields.get(f)}
            self._kept[key] = fields, files
            self.bytes += sum(len(data) for data in files.values())
            while self.bytes > self.budget and self._kept:
                _, (_, gone) = self._kept.popitem(last=False)
                self.bytes -= sum(len(data) for data in gone.values())
        return fields


def serve(adapter: Adapter) -> None:
    """The entry scripts' main, once they have loaded what their backend needs: a build worker (above). Each job
    runs in its job's folder, its working and temporary folder, with what it prints going to its `OUTPUT_FILE`
    there; model code runs in the design's sources folder, so its relative paths (vendor/…) resolve there.
    result.json is written beside the job (always), then the job's path is answered on stdout after `DONE`."""
    exit_with_parent()
    answer = os.fdopen(os.dup(1), "w", encoding="utf-8")  # stdout and stderr become each job's log
    exports = Exports()
    home = Path.cwd()
    for line in sys.stdin:
        job_file = Path(line.strip())
        _job(job_file, adapter, exports)
        # Out of the job's folder, its log closed: an idle worker holds no file of it (Windows deletes none held).
        os.chdir(home)
        with open(os.devnull, "wb") as null:
            os.dup2(null.fileno(), 1)
            os.dup2(null.fileno(), 2)
        answer.write(f"{DONE}{job_file}\n")
        answer.flush()


def _job(job_file: Path, adapter: Adapter, exports: Exports) -> None:
    out = job_file.parent
    sys.stdout.flush()
    sys.stderr.flush()
    with (out / OUTPUT_FILE).open("wb") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    os.chdir(out)
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[name] = str(out)
    tempfile.tempdir = None  # read again from the environment
    job = json.loads(job_file.read_text(encoding="utf-8"))
    sys.path[:0] = [p for p in job.get("paths", []) if p not in sys.path]  # the plugins' model packages
    job["out"] = str(out)
    exports.budget = job.get("cache_mb", 0) << 20
    result: dict[str, Any] = {"ok": False}
    rt = None
    try:
        if job["mode"] in adapter.modes:  # runs no model code
            result = {"ok": True, **adapter.modes[job["mode"]](job)}
        else:
            rt = Runtime(Path(job["src"]), job["params"])
            os.chdir(rt.src)
            done = plan(rt, adapter.placement) if job["mode"] == "plan" else parts(rt, job, adapter, exports)
            result = {"ok": True, **done}
    except ModelError as e:
        result = {"ok": False, "error": e.as_json()}
    except Exception:
        result = {
            "ok": False,
            "error": ModelError(BUG, "internal build error:\n" + traceback.format_exc(limit=8)).as_json(),
        }
    finally:
        if rt:
            rt.close()
        os.chdir(out)
        (out / "result.json").write_text(json.dumps(result), encoding="utf-8")
        sys.stdout.flush()
        sys.stderr.flush()


def parts(rt: Runtime, job: dict[str, Any], adapter: Adapter, exports: Exports) -> dict[str, Any]:
    """Build each part file of `job["files"]` ({file: {part: [declared connectors]}}) on its own: one that fails
    says why, and the others still build."""
    tolerance = {"linear": job["mesh_tolerance_mm"], "angular": job["mesh_angular_tolerance"]}
    out = Path(job["out"])
    files = []
    for n, (file, expected) in enumerate(job["files"].items()):
        try:
            adapter.begin()
            result, reads = rt.call_build(file, f"hitlcad_part_{n}")
            if not isinstance(result, dict):
                raise ModelError(RETURN, f"{file}: build(p, i) returned {type(result).__name__}; return {{part name: …}}",
                                 file)  # fmt: skip
            if set(result) != set(expected):
                raise ModelError(PARTS, f"{file} returned parts {', '.join(map(str, result)) or 'none'}; design.json "
                                 f"declares {', '.join(expected)} in it")  # fmt: skip
            built = [_part(file, name, result[name], declared, adapter, exports, out, tolerance)
                     for name, declared in expected.items()]  # fmt: skip
            files.append({"file": file, "reads": reads.as_json(), "parts": built})
        except (Exception, SystemExit) as e:  # each file on its own: the others still build; sys.exit() is its error
            files.append({"file": file, "error": rt.failure(e, file)})
    return {"files": files}


def _part(file: str, name: str, value: Any, declared: list[str], adapter: Adapter, exports: Exports, out: Path,
          tolerance: dict[str, float]) -> dict[str, Any]:  # fmt: skip
    shape, connectors, catalog = adapter.unpack(f"{file}: part {name!r}", value)
    missing = [c for c in declared if c not in connectors]
    if missing:
        raise ModelError(CONNECTORS, f"{file}: part {name!r} declares connectors it does not return: "
                         f"{', '.join(missing)}", file)  # fmt: skip
    frames = {c: [adapter.placement(v) for v in as_list(connectors[c])] for c in declared}
    (out / "parts").mkdir(exist_ok=True)
    return {"name": name, **exports.export(adapter, shape, name, out, tolerance), "connectors": frames,
            "catalog": catalog}  # fmt: skip


def plan(rt: Runtime, placement: Callable[[Any], list[list[float]]]) -> dict[str, Any]:
    """Evaluate every interface value and run assembly.py: what the build places, before any geometry."""
    try:
        interfaces = rt.all_interfaces()
        placed: dict[str, list[list[list[float]]]] = {}
        reads = Reads()
        if (rt.src / ASSEMBLY).is_file():
            result, reads = rt.call_build(ASSEMBLY, "hitlcad_assembly")
            if not isinstance(result, dict):
                raise ModelError(RETURN, f"assembly.py build(p, i) returned {type(result).__name__}; return "
                                 "{part or component name: [placement, …]}", ASSEMBLY)  # fmt: skip
            for name, value in result.items():
                if not isinstance(name, str):
                    raise ModelError(RETURN, f"assembly.py: {name!r} is not a part or component name", ASSEMBLY)
                placed[name] = [placement(v) for v in as_list(value)]
    except ModelError:
        raise
    except (Exception, SystemExit) as e:  # sys.exit() in the design's code is its error too
        raise rt.describe(e) from None
    return {"interfaces": interfaces, "placements": placed, "assembly_reads": reads.as_json()}
