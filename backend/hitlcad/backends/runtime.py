"""The model runtime every backend's entry script uses inside its child process. Standard library only,
so it runs in any Python, Blender's bundled one included.

A design's code is `interfaces.py` (one function per mating value), part files under `parts/` and
`assembly.py`. This module runs them with `p` (parameter values) and `i` (interface values) and
records exactly which keys each one read, so hitlcad knows what every part depends on: for its
cache, and for impact questions.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import traceback
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

INTERFACES = "interfaces.py"
ASSEMBLY = "assembly.py"
LIB = "lib"


class ModelError(Exception):
    """An error in the design's code, already described in its own terms."""


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
            raise ModelError("interfaces.py: values depend on each other in a cycle: "
                             + " -> ".join([*self._evaluating, name]))  # fmt: skip
        module = self._interfaces_module()
        fn = getattr(module, name, None) if module else None
        if name.startswith("_") or not callable(fn):
            known = ", ".join(self.interface_names()) or "none (interfaces.py has no values)"
            raise ModelError(f"no interface value {name!r}; interface values: {known}")
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
            raise ModelError(f"{relative} does not exist")
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def lib_modules(self) -> set[str]:
        return {Path(mod.__file__).relative_to(self.src).as_posix() for mod in list(sys.modules.values())
                if _in(getattr(mod, "__file__", None), self.src / LIB)}  # fmt: skip

    def call_build(self, relative: str, module_name: str) -> tuple[Any, Reads]:
        """Run `build(p, i)` of a design file; returns what it returned and what it read."""
        with self.reading() as reads:
            module = self.load(relative, module_name, fresh_lib=True)
            build = getattr(module, "build", None)
            if not callable(build):
                raise ModelError(f"{relative} must define a top-level function build(p, i)")
            result = build(self.p, self.i)
            reads.modules |= self.lib_modules()
        return result, reads

    # ------------------------------------------------------------------ errors

    def describe(self, exc: BaseException) -> str:
        """An exception in the design's own terms: the frames of its files, then the error."""
        if isinstance(exc, ModelError):
            return str(exc)
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if _in(f.filename, self.src)]
        lines = [f"  {Path(f.filename).resolve().relative_to(self.src).as_posix()} line {f.lineno}, in {f.name}: "
                 f"{f.line or ''}" for f in frames]  # fmt: skip
        return "\n".join(lines + [f"{type(exc).__name__}: {exc}"])


class _Params(Mapping[str, Any]):
    """The parameter values, recording each one read."""

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime

    def __getitem__(self, key: str) -> Any:
        if key not in self._rt.values:
            raise ModelError(f"no parameter {key!r}; parameters: {', '.join(self._rt.values) or 'none'}")
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
        raise ModelError("interface values are computed by interfaces.py, not assigned")


def _in(filename: str | None, folder: Path) -> bool:
    if not filename:
        return False
    try:
        Path(filename).resolve().relative_to(folder)
        return True
    except ValueError:
        return False


def _plain(value: Any, what: str) -> Any:
    """A JSON value (numbers, strings, booleans, lists and string-keyed dicts of them)."""
    if isinstance(value, bool | str) or value is None:
        return value
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            raise ModelError(f"{what} is {value}")
        return value
    if isinstance(value, list | tuple):
        return [_plain(v, what) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: _plain(v, what) for k, v in value.items()}
    if hasattr(value, "__float__"):  # numpy scalars
        return float(value)
    raise ModelError(f"{what} is a {type(value).__name__}; interface values are numbers, strings, booleans, "
                     "and lists or dicts of them")  # fmt: skip


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else [value]


def run(job_file: Path, modes: dict[str, Callable[[Runtime, dict[str, Any]], dict[str, Any]]]) -> None:
    """The entry scripts' main: read job.json, run its mode, write result.json (always). Model code runs in
    the design's sources folder, so its relative paths (vendor/…) resolve there; outputs go to `job["out"]`,
    the folder the entry was started in."""
    job = json.loads(job_file.read_text(encoding="utf-8"))
    out = Path.cwd()
    job["out"] = str(out)
    result: dict[str, Any] = {"ok": False}
    try:
        runtime = Runtime(Path(job["src"]), job["params"])
        os.chdir(runtime.src)
        result = {"ok": True, **modes[job["mode"]](runtime, job)}
    except ModelError as e:
        result = {"ok": False, "error": str(e)}
    except Exception:
        result = {"ok": False, "error": "internal build error:\n" + traceback.format_exc(limit=8)}
    finally:
        (out / "result.json").write_text(json.dumps(result), encoding="utf-8")


def plan(rt: Runtime, job: dict[str, Any], placements: Callable[[Any], list[list[list[float]]]]) -> dict[str, Any]:
    """Evaluate every interface value and run assembly.py: what the build places, before any geometry."""
    try:
        interfaces = rt.all_interfaces()
        placed: dict[str, list[list[list[float]]]] = {}
        reads = Reads()
        if (rt.src / ASSEMBLY).is_file():
            result, reads = rt.call_build(ASSEMBLY, "hitlcad_assembly")
            if not isinstance(result, dict):
                raise ModelError(f"assembly.py build(p, i) returned {type(result).__name__}; return "
                                 "{part or component name: [placement, …]}")  # fmt: skip
            for name, value in result.items():
                if not isinstance(name, str):
                    raise ModelError(f"assembly.py: {name!r} is not a part or component name")
                placed[name] = [placements(v) for v in as_list(value)]
    except ModelError:
        raise
    except Exception as e:
        raise ModelError(rt.describe(e)) from None
    return {"interfaces": interfaces, "placements": placed, "assembly_reads": reads.as_json()}
