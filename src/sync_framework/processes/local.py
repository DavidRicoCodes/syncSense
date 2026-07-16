"""Safe local subprocess adapter restricted to simulation commands."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import IO

from ..domain import CapabilityDisabled, ProcessFailure
from .base import ProcessHandle, ProcessSpec, ProcessStatus


def process_start_ticks(pid: int) -> int:
    try:
        # Field 22 in /proc/<pid>/stat. Account for spaces in the comm field.
        rest = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return int(rest[19])
    except (OSError, ValueError, IndexError) as exc:
        raise ProcessFailure(f"Cannot identify process {pid}") from exc


def same_process(handle: ProcessHandle) -> bool:
    try:
        return process_start_ticks(handle.pid) == handle.proc_start_ticks
    except ProcessFailure:
        return False


class LocalProcessAdapter:
    def __init__(self) -> None:
        self._processes: dict[int, subprocess.Popen] = {}
        self._logs: dict[int, IO[bytes]] = {}

    def preflight(self, spec: ProcessSpec) -> None:
        if spec.safety_class != "simulation":
            raise CapabilityDisabled(f"Local command is not classified as simulation: {spec.producer_id}")
        executable = spec.argv[0]
        if "/" in executable:
            target = Path(executable).expanduser()
            if not target.is_file() or not os.access(target, os.X_OK):
                raise ProcessFailure(f"Executable is not available: {target}")
        elif shutil.which(executable) is None:
            raise ProcessFailure(f"Executable is not on PATH: {executable}")
        if not spec.cwd.is_dir():
            raise ProcessFailure(f"Working directory does not exist: {spec.cwd}")

    def start(self, spec: ProcessSpec) -> ProcessHandle:
        self.preflight(spec)
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = spec.log_path.open("ab", buffering=0)
        env = os.environ.copy()
        env.update(spec.env)
        try:
            process = subprocess.Popen(
                list(spec.argv), cwd=spec.cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, shell=False, start_new_session=True,
            )
        except Exception:
            log.close()
            raise
        self._processes[process.pid] = process
        self._logs[process.pid] = log
        return ProcessHandle(backend="local", producer_id=spec.producer_id, pid=process.pid, proc_start_ticks=process_start_ticks(process.pid))

    def probe(self, handle: ProcessHandle) -> ProcessStatus:
        process = self._processes.get(handle.pid)
        if process is not None:
            code = process.poll()
            return ProcessStatus(running=code is None, exit_code=code)
        return ProcessStatus(running=same_process(handle), detail="external process handle")

    def stop(self, handle: ProcessHandle, grace_s: float) -> ProcessStatus:
        if same_process(handle):
            try:
                os.killpg(handle.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            status = self.probe(handle)
            if not status.running:
                return self.collect(handle)
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return self.probe(handle)

    def kill(self, handle: ProcessHandle) -> ProcessStatus:
        if same_process(handle):
            try:
                os.killpg(handle.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process = self._processes.get(handle.pid)
        if process is not None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return ProcessStatus(running=True, detail="process did not exit after SIGKILL")
        return self.collect(handle)

    def collect(self, handle: ProcessHandle) -> ProcessStatus:
        process = self._processes.get(handle.pid)
        code = process.poll() if process is not None else None
        running = same_process(handle) if process is None else code is None
        if not running:
            log = self._logs.pop(handle.pid, None)
            if log is not None:
                log.close()
            self._processes.pop(handle.pid, None)
        return ProcessStatus(running=running, exit_code=code)

