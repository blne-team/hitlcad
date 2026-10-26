"""Process execution for untrusted model code.

``LocalExecutor`` runs a process under two limits, the same on every platform: a wall-clock deadline, and the
resident memory of the process and everything it started, sampled with psutil. Time is bounded by the deadline
alone: a CPU-time limit counts every thread, so it would kill parallel work (OCP's and Blender's meshers) long
before the deadline. Whatever stops a build, and once it ends, nothing it started outlives it: the one part that
differs per platform is how a process tree is held, a process group on POSIX and a job object on Windows.

The child writes its output to a file in its work directory, not a pipe: a pipe stays open as long as anything the
child started holds it, so a build that left a process behind would only end at its deadline. The child inherits
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

LOG_LIMIT = 64_000
OUTPUT_FILE = "output.log"
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
    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None) -> ExecResult:
        """A process run to its end in `cwd`."""
        ...

    async def run_job(self, argv: list[str], job_file: Path, *, timeout_s: float,
                      memory_mb: int | None = None) -> ExecResult:  # fmt: skip
        """A build process for one job: `argv` starts one that waits for its job's path on stdin."""
        ...

    def warm(self, argv: list[str]) -> None:
        """Keep build processes of `argv` started, ready for a job."""

    async def close(self) -> None:
        """Stop the build processes kept started."""


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


class LocalExecutor:
    def __init__(self, warm: int = 0) -> None:
        self.spares = warm  # build processes kept started, for each command `warm` was asked for
        self._ready: dict[tuple[str, ...], list[_Started]] = {}
        self._kept: set[tuple[str, ...]] = set()
        self._starting: set[asyncio.Task[None]] = set()

    @staticmethod
    def _env(cwd: Path) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("HITLCAD_")}
        return env | {"TMPDIR": str(cwd), "TEMP": str(cwd), "TMP": str(cwd)}

    async def _start(self, argv: list[str], cwd: Path, stdin: int) -> _Started:
        log = cwd / OUTPUT_FILE
        with log.open("wb") as sink:
            proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=self._env(cwd), stdin=stdin, stdout=sink,
                                                        stderr=asyncio.subprocess.STDOUT, **_SPAWN)  # fmt: skip
        return _Started(proc, _ProcessTree(proc.pid), cwd, log)

    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None) -> ExecResult:
        return await self._watch(await self._start(argv, cwd, asyncio.subprocess.DEVNULL), timeout_s, memory_mb)

    async def run_job(self, argv: list[str], job_file: Path, *, timeout_s: float,
                      memory_mb: int | None = None) -> ExecResult:  # fmt: skip
        """A build process for one job: `argv` starts one that waits for its job's path on stdin. It is one kept
        started where there is one (`warm`), and the next one starts as it takes the job; its limits count from
        the job."""
        key = tuple(argv)
        build = self._take(key) or await self._waiting(key)
        if key in self._kept:
            self._keep(key)
        assert build.proc.stdin is not None
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # it ended while it waited: as it ended says
            build.proc.stdin.write(f"{job_file.resolve()}\n".encode())
            await build.proc.stdin.drain()
            build.proc.stdin.close()
        try:
            return await self._watch(build, timeout_s, memory_mb)
        finally:
            shutil.rmtree(build.folder, ignore_errors=True)

    def warm(self, argv: list[str]) -> None:
        """Keep `spares` build processes of `argv` started from now on: what a build would wait for is loaded
        before it asks. Each is a build process like any, for one job."""
        if self.spares:
            self._kept.add(tuple(argv))
            self._keep(tuple(argv))

    async def close(self) -> None:
        """Stop the build processes kept started."""
        for task in self._starting:
            task.cancel()
        await asyncio.gather(*self._starting, return_exceptions=True)
        for ready in self._ready.values():
            for build in ready:
                await self._discard(build)
        self._ready.clear()

    async def _waiting(self, key: tuple[str, ...]) -> _Started:
        return await self._start(list(key), Path(tempfile.mkdtemp(prefix="hitlcad-build-")), asyncio.subprocess.PIPE)

    def _take(self, key: tuple[str, ...]) -> _Started | None:
        ready = self._ready.get(key, [])
        while ready:
            build = ready.pop(0)
            if build.proc.returncode is None:
                return build
            build.tree.close()
            shutil.rmtree(build.folder, ignore_errors=True)
        return None

    def _keep(self, key: tuple[str, ...]) -> None:
        starting = sum(1 for task in self._starting if task.get_name() == repr(key))
        for _ in range(self.spares - len(self._ready.get(key, [])) - starting):
            task = asyncio.get_running_loop().create_task(self._spare(key), name=repr(key))
            self._starting.add(task)
            task.add_done_callback(self._starting.discard)

    async def _spare(self, key: tuple[str, ...]) -> None:
        self._ready.setdefault(key, []).append(await self._waiting(key))

    @staticmethod
    async def _discard(build: _Started) -> None:
        build.tree.kill()
        await build.proc.wait()
        build.tree.close()
        shutil.rmtree(build.folder, ignore_errors=True)

    async def _watch(self, started: _Started, timeout_s: float, memory_mb: int | None) -> ExecResult:
        """Wait for a process to end, under a deadline and a memory limit from now; then nothing it started is left."""
        begun = time.monotonic()
        proc, tree = started.proc, started.tree
        exited = asyncio.ensure_future(proc.wait())
        limits: dict[asyncio.Future[Any], tuple[Literal["time", "memory"], str]] = {
            asyncio.ensure_future(asyncio.sleep(timeout_s)): ("time", f"exceeded the {timeout_s:.0f}s time limit")
        }
        if memory_mb is not None:
            limits[asyncio.ensure_future(_memory_exceeds(proc.pid, memory_mb << 20))] = (
                "memory",
                f"used more than {memory_mb} MB of memory",
            )
        try:
            done, _ = await asyncio.wait([exited, *limits], return_when=asyncio.FIRST_COMPLETED)
            stopped = None if exited in done else next(why for limit, why in limits.items() if limit in done)
            tree.kill()  # stopped, or done: either way nothing it started outlives it
            await exited
        except asyncio.CancelledError:  # the build was cancelled (a shutdown): leave no process behind
            tree.kill()
            await asyncio.shield(proc.wait())
            raise
        finally:
            for limit in limits:
                limit.cancel()
            tree.close()
        limit, why = stopped or (None, None)
        return ExecResult(proc.returncode, _tail(started.log), why, time.monotonic() - begun, limit)  # type: ignore[arg-type]
