"""Process execution for untrusted model code.

``LocalExecutor`` runs a process under two limits, the same on every platform: a wall-clock deadline, and the
resident memory of the process and everything it started, sampled with psutil. Time is bounded by the deadline
alone: a CPU-time limit counts every thread, so it would kill parallel work (OCP's and Blender's meshers) long
before the deadline. Whatever stops a build, and once it ends, nothing it started outlives it: the one part that
differs per platform is how a process tree is held, a process group on POSIX and a job object on Windows.

The child writes its output to a file in its work directory, not a pipe: a pipe stays open as long as anything the
child started holds it, so a build that left a process behind would only end at its deadline. The child inherits
the environment, so it runs wherever the server does; hitlcad sets only its temporary folder (the work directory)
and its UTF-8, and drops its own settings. It does not isolate the filesystem or
network; swap in a container or bubblewrap executor for anything beyond local single-user use.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psutil

LOG_LIMIT = 64_000
OUTPUT_FILE = "output.log"
MEMORY_SAMPLE_S = 0.5


@dataclass
class ExecResult:
    returncode: int | None
    output: str
    stopped: str | None  # why hitlcad stopped the process, if it did: "exceeded the 180s time limit"
    duration_s: float


class Executor(Protocol):
    async def run(
        self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None
    ) -> ExecResult: ...


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
    """Returns once the process tree of ``pid`` holds more than ``limit`` bytes."""
    root = psutil.Process(pid)
    while _resident(root) <= limit:
        await asyncio.sleep(MEMORY_SAMPLE_S)


def _tail(log: Path) -> str:
    """The end of the output, at most LOG_LIMIT bytes of it."""
    with log.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - LOG_LIMIT))
        text = f.read().decode("utf-8", errors="replace")
    return text if size <= LOG_LIMIT else "…[truncated]…\n" + text


class LocalExecutor:
    @staticmethod
    def _env(cwd: Path) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("HITLCAD_")}
        return env | {
            "TMPDIR": str(cwd),
            "TEMP": str(cwd),
            "TMP": str(cwd),
            # It speaks UTF-8, whatever the platform's code page: files (UTF-8 mode) and the output read back.
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }

    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None) -> ExecResult:
        started = time.monotonic()
        log = cwd / OUTPUT_FILE
        with log.open("wb") as sink:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=self._env(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=sink,
                stderr=asyncio.subprocess.STDOUT,
                **_SPAWN,
            )
        tree = _ProcessTree(proc.pid)
        exited = asyncio.ensure_future(proc.wait())
        limits = {asyncio.ensure_future(asyncio.sleep(timeout_s)): f"exceeded the {timeout_s:.0f}s time limit"}
        if memory_mb:
            limits[asyncio.ensure_future(_memory_exceeds(proc.pid, memory_mb << 20))] = (
                f"used more than {memory_mb} MB of memory"
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
        return ExecResult(proc.returncode, _tail(log), stopped, time.monotonic() - started)
