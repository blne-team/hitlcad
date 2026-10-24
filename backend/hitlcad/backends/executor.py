"""Process execution for untrusted model code.

``LocalExecutor`` applies a memory cap, a scrubbed environment, a wall-clock deadline, and kills the process
together with everything it started. On POSIX that is resource limits and a process group (plus a file-size
cap; macOS enforces no address-space limit, so there memory is not capped); on Windows, a job object. Time is bounded by the deadline alone: a CPU-time limit counts every thread, so
it would kill parallel work (OCP's and Blender's meshers) long before the deadline. It does not isolate the
filesystem or network; swap in a container or bubblewrap executor for anything beyond local single-user use.
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

LOG_LIMIT = 64_000


@dataclass
class ExecResult:
    returncode: int | None
    output: str
    timed_out: bool
    duration_s: float


class Executor(Protocol):
    async def run(
        self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None
    ) -> ExecResult: ...


if sys.platform == "win32":
    import win32api
    import win32con
    import win32job

    def _spawn_options(memory_mb: int | None) -> dict[str, Any]:
        return {}

    class _ProcessTree:
        """A job object holding the process and what it starts: the memory cap, and killed as one."""

        def __init__(self, pid: int, memory_mb: int | None) -> None:
            self.job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            flags = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if memory_mb:
                flags |= win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
                info["JobMemoryLimit"] = memory_mb << 20
            info["BasicLimitInformation"]["LimitFlags"] = flags
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, info)
            process = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
            try:
                win32job.AssignProcessToJobObject(self.job, process)
            finally:
                win32api.CloseHandle(process)

        def kill(self) -> None:
            win32job.TerminateJobObject(self.job, 1)

        def close(self) -> None:
            self.job.Close()  # KILL_ON_JOB_CLOSE: nothing it started outlives it

else:
    import resource

    def _spawn_options(memory_mb: int | None) -> dict[str, Any]:
        def limits() -> None:
            if memory_mb and sys.platform == "linux":
                cap = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            resource.setrlimit(resource.RLIMIT_FSIZE, (512 << 20, 512 << 20))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        return {"preexec_fn": limits, "start_new_session": True}

    class _ProcessTree:
        """The process's own session: killed as one process group."""

        def __init__(self, pid: int, memory_mb: int | None) -> None:
            self.pid = pid

        def kill(self) -> None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)

        def close(self) -> None:
            pass


class LocalExecutor:
    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        self.extra_env = extra_env or {}

    def _env(self, cwd: Path) -> dict[str, str]:
        # The work directory is the child's home and temporary folder, under every platform's names for them.
        env = {key: str(cwd) for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMPDIR", "TEMP", "TMP")}
        env |= {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # glibc reserves a 64 MB arena per thread; with many cores that exhausts RLIMIT_AS and
            # OCP's parallel mesher deadlocks instead of failing.
            "MALLOC_ARENA_MAX": "2",
        }
        # The display on Linux; on Windows, what any process needs to start (SYSTEMROOT) and find programs.
        for key in ("DISPLAY", "XDG_RUNTIME_DIR", "LD_LIBRARY_PATH",
                    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"):  # fmt: skip
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(self.extra_env)
        return env

    async def run(self, argv: list[str], cwd: Path, *, timeout_s: float, memory_mb: int | None = None) -> ExecResult:
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._env(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **_spawn_options(memory_mb),
        )
        tree = _ProcessTree(proc.pid, memory_mb)
        timed_out = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            tree.kill()
            out, _ = await proc.communicate()
        except asyncio.CancelledError:  # the build was cancelled (a shutdown): leave no process behind
            tree.kill()
            await asyncio.shield(proc.wait())
            raise
        finally:
            tree.close()
        text = out.decode("utf-8", errors="replace")
        if len(text) > LOG_LIMIT:
            text = "…[truncated]…\n" + text[-LOG_LIMIT:]
        return ExecResult(proc.returncode, text, timed_out, time.monotonic() - started)
