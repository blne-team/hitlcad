"""Process execution for untrusted model code.

``LocalExecutor`` runs a process to its end under a wall-clock deadline (the renderer's), and a build worker's
every job under two limits, the same on every platform: that deadline, and the resident memory of the process and
everything it started, sampled with psutil. Time is bounded by the deadline alone: a CPU-time limit counts every thread, so it would kill parallel work (OCP's and Blender's meshers) long
before the deadline. Whatever stops a build, and once it ends, nothing it started outlives it: the one part that
differs per platform is how a process tree is held, a process group on POSIX and a job object on Windows.

A process run to its end writes its output to a file in its work directory, not a pipe: a pipe stays open as long
as anything the child started holds it, so a run that left a process behind would only end at its deadline. A build
worker (`Worker`, `runtime.serve`) runs job after job: each job's output goes to a file in the job's folder, and
the worker answers on its stdout, read for its answers only, and ends with its process tree. The child inherits
the environment, so it runs wherever the server does; hitlcad sets only its temporary folder (the work directory)
and drops its own settings. The output is read as UTF-8: each backend's command makes its interpreter write it so
(an interpreter run isolated, as backends are, ignores PYTHON* variables). It does not isolate the filesystem or
network; swap in a container or bubblewrap executor for anything beyond local single-user use.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import psutil

from hitlcad.execution.runtime import DONE, OUTPUT_FILE

LOG_LIMIT = 64_000
MEMORY_SAMPLE_S = 0.5
#: Lines of a process's output an error quotes.
TAIL_LINES = 40


def last_lines(text: str) -> str:
    """The last TAIL_LINES lines of a process's output: what an error quotes."""
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


@dataclass
class ExecResult:
    returncode: int
    output: str
    stopped: str | None  # why hitlcad stopped the process, if it did: "exceeded the 180s time limit"
    duration_s: float
    limit: Literal["time", "memory"] | None = None  # the limit it stopped at


class Executor(Protocol):
    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float) -> ExecResult:
        """A process run to its end in `cwd`, under a deadline."""
        ...

    async def worker(self, argv: list[str]) -> Worker:
        """A build worker `argv` starts: it reads its jobs' paths on stdin (`runtime.serve`)."""
        ...


if sys.platform == "win32":
    import win32api
    import win32con
    import win32job

    _SPAWN: dict[str, Any] = {}

    class _ProcessTree:
        """A job object holding the process and what it starts."""

        def __init__(self, pid: int) -> None:
            self.job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, info)
            process = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
            try:
                win32job.AssignProcessToJobObject(self.job, process)
            finally:
                win32api.CloseHandle(process)

        def kill(self) -> None:
            win32job.TerminateJobObject(self.job, 1)

        def close(self) -> None:
            self.job.Close()

else:
    _SPAWN = {"start_new_session": True}

    class _ProcessTree:
        """The process's own session, killed as one process group."""

        def __init__(self, pid: int) -> None:
            self.pid = pid

        def kill(self) -> None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)

        def close(self) -> None:
            pass


def _resident(root: psutil.Process) -> int:
    """Bytes of memory the process and everything it started hold."""
    try:
        processes = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0
    total = 0
    for p in processes:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            total += p.memory_info().rss
    return total


async def _memory_exceeds(pid: int, limit: int) -> None:
    """Returns once the process tree of ``pid`` holds more than ``limit`` bytes, and never otherwise: a process
    that is gone (it ended before the first sample) holds nothing."""
    try:
        root: psutil.Process | None = psutil.Process(pid)
    except psutil.NoSuchProcess:
        root = None
    while root is None or _resident(root) <= limit:
        await asyncio.sleep(MEMORY_SAMPLE_S)


def _tail(log: Path) -> str:
    """The end of the output, at most LOG_LIMIT bytes of it."""
    with log.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - LOG_LIMIT))
        text = f.read().decode("utf-8", errors="replace")
    return text if size <= LOG_LIMIT else "…[truncated]…\n" + text


@dataclass
class _Started:
    """A process started: the tree it holds, the folder it started in and its output there."""

    proc: asyncio.subprocess.Process
    tree: _ProcessTree
    folder: Path
    log: Path


class Worker:
    """A build worker: a process that runs job after job (`runtime.serve`), under each job's deadline and a memory
    limit on everything it holds. What it prints outside its jobs (as it starts) is kept in its own folder."""

    def __init__(self, started: _Started) -> None:
        self.started = started
        self._answers: asyncio.Queue[str] = asyncio.Queue()
        self._exited = asyncio.ensure_future(started.proc.wait())
        self._reader = asyncio.ensure_future(self._read())

    @property
    def pid(self) -> int:
        return self.started.proc.pid

    @property
    def alive(self) -> bool:
        return self.started.proc.returncode is None

    def resident(self) -> int:
        """Bytes of memory it holds, with everything it started."""
        try:
            return _resident(psutil.Process(self.pid))
        except psutil.NoSuchProcess:
            return 0

    async def _read(self) -> None:
        assert self.started.proc.stdout is not None
        with self.started.log.open("ab") as log:
            async for line in self.started.proc.stdout:
                text = line.decode("utf-8", errors="replace")
                if text.startswith(DONE):
                    self._answers.put_nowait(text[len(DONE) :].strip())
                else:
                    log.write(line)
                    log.flush()

    async def job(self, job_file: Path, *, timeout_s: float, memory_mb: int) -> ExecResult:
        """Run a job (its job.json), its deadline and memory limit counting from now. A worker that overruns one,
        or ends, is gone with everything it started: the job's result says why."""
        begun = time.monotonic()
        proc, tree = self.started.proc, self.started.tree
        log = job_file.parent / OUTPUT_FILE
        assert proc.stdin is not None
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # it ended while it waited: as it ended says
            proc.stdin.write(f"{job_file.resolve()}\n".encode())
            await proc.stdin.drain()
        answered = asyncio.ensure_future(self._answers.get())
        limits: dict[asyncio.Future[Any], tuple[Literal["time", "memory"], str]] = {
            asyncio.ensure_future(asyncio.sleep(timeout_s)): ("time", f"exceeded the {timeout_s:.0f}s time limit"),
            asyncio.ensure_future(_memory_exceeds(proc.pid, memory_mb << 20)): (
                "memory", f"used more than {memory_mb} MB of memory"),
        }  # fmt: skip
        try:
            done, _ = await asyncio.wait([answered, self._exited, *limits], return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:  # the build was cancelled (a shutdown): leave no process behind
            await asyncio.shield(self.stop())
            raise
        finally:
            for limit in limits:
                limit.cancel()
        output = _tail(log) if log.is_file() else ""
        if answered in done:
            return ExecResult(0, output, None, time.monotonic() - begun)
        answered.cancel()
        stopped = next((why for limit, why in limits.items() if limit in done), None)
        tree.kill()  # stopped, or ended: either way nothing it started outlives it
        await self._exited
        if stopped is None:  # it ended on its own: what it said as it did may be its own, not the job's
            output = "\n".join(filter(None, [output, _tail(self.started.log)]))
        limit, why = stopped or (None, None)
        return ExecResult(proc.returncode, output, why, time.monotonic() - begun, limit)  # type: ignore[arg-type]

    async def stop(self) -> None:
        self.started.tree.kill()
        await self._exited
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)
        self.kill()

    def kill(self) -> None:
        """End it from outside the event loop it was started on, which is gone or not this one: its process tree,
        then its folder. What waited on it there ends with that loop."""
        self.started.tree.kill()
        self.started.tree.close()
        shutil.rmtree(self.started.folder, ignore_errors=True)


class LocalExecutor:
    @staticmethod
    def _env(cwd: Path) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("HITLCAD_")}
        return env | {"TMPDIR": str(cwd), "TEMP": str(cwd), "TMP": str(cwd)}

    async def _start(self, argv: list[str], cwd: Path, stdin: int, stdout: int | None = None) -> _Started:
        log = cwd / OUTPUT_FILE
        with log.open("wb") as sink:
            proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=self._env(cwd), stdin=stdin,
                                                        stdout=sink if stdout is None else stdout,
                                                        stderr=sink, **_SPAWN)  # fmt: skip
        return _Started(proc, _ProcessTree(proc.pid), cwd, log)

    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float) -> ExecResult:
        return await self._watch(await self._start(argv, cwd, asyncio.subprocess.DEVNULL), timeout_s)

    async def worker(self, argv: list[str]) -> Worker:
        """A build worker, started in a folder of its own: its working and temporary folder until a job comes."""
        folder = Path(tempfile.mkdtemp(prefix="hitlcad-worker-"))
        return Worker(await self._start(argv, folder, asyncio.subprocess.PIPE, asyncio.subprocess.PIPE))

    async def _watch(self, started: _Started, timeout_s: float) -> ExecResult:
        """Wait for a process to end, under a deadline from now; then nothing it started is left."""
        begun = time.monotonic()
        proc, tree = started.proc, started.tree
        exited = asyncio.ensure_future(proc.wait())
        deadline = asyncio.ensure_future(asyncio.sleep(timeout_s))
        try:
            await asyncio.wait([exited, deadline], return_when=asyncio.FIRST_COMPLETED)
            late = not exited.done()
            tree.kill()  # stopped, or done: either way nothing it started outlives it
            await exited
        except asyncio.CancelledError:  # the build was cancelled (a shutdown): leave no process behind
            tree.kill()
            await asyncio.shield(proc.wait())
            raise
        finally:
            deadline.cancel()
            tree.close()
        why = f"exceeded the {timeout_s:.0f}s time limit" if late else None
        return ExecResult(proc.returncode, _tail(started.log), why, time.monotonic() - begun, "time" if late else None)
