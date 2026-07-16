"""OpenSSH process adapter restricted to explicitly allowed simulations."""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import IO, Any

from ..domain import CapabilityDisabled, ProcessFailure
from .base import ProcessHandle, ProcessSpec, ProcessStatus
from .fake import FakeProcessAdapter
from .local import process_start_ticks, same_process


def ssh_target(config: dict[str, Any]) -> str:
    host = config["host"]
    return f"{config['user']}@{host}" if config.get("user") else host


def ssh_prefix(config: dict[str, Any]) -> list[str]:
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=3",
        "-o", "ServerAliveCountMax=2",
    ]
    if config.get("port"):
        command += ["-p", str(config["port"])]
    if config.get("identity_file"):
        command += ["-i", os.path.expanduser(config["identity_file"])]
    if config.get("known_hosts_file"):
        command += ["-o", f"UserKnownHostsFile={os.path.expanduser(config['known_hosts_file'])}"]
    return command + [ssh_target(config), "--"]


def remote_command(config: dict[str, Any], argv: list[str]) -> list[str]:
    # OpenSSH invokes a remote shell. shlex.join preserves argv boundaries and
    # the caller only supplies validated configuration/worker values.
    return ssh_prefix(config) + [shlex.join(argv)]


def run_ssh(config: dict[str, Any], argv: list[str], *, timeout: float = 15.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        remote_command(config, argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=timeout, check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ProcessFailure(f"SSH command failed on {config['host']}: {detail}")
    return result


class SshProcessAdapter:
    def __init__(self, *, allow_remote_simulation: bool = False):
        self.allow_remote_simulation = allow_remote_simulation
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._logs: dict[int, IO[bytes]] = {}

    def preflight(self, spec: ProcessSpec) -> None:
        if not self.allow_remote_simulation:
            raise CapabilityDisabled("Remote simulation requires --allow-remote-simulation")
        if spec.safety_class != "simulation":
            raise CapabilityDisabled(f"Remote command is not simulation: {spec.producer_id}")
        if not spec.ssh or not spec.worker_config or not spec.remote_runtime_dir or not spec.shared_runtime_dir:
            raise ProcessFailure(f"Incomplete SSH process specification: {spec.producer_id}")
        if shutil.which("ssh") is None:
            raise ProcessFailure("OpenSSH client is not available")
        run_ssh(spec.ssh, ["true"], timeout=10)
        worker_path = Path(spec.argv[1]) if len(spec.argv) > 1 else None
        if spec.argv[:1] != ("python3",) or worker_path is None:
            raise ProcessFailure("Remote simulations must use the standalone Python worker")
        run_ssh(spec.ssh, ["test", "-f", str(worker_path)], timeout=10)
        output_dir = str(spec.worker_config["output_dir"])
        run_ssh(spec.ssh, ["test", "-d", output_dir, "-a", "-w", output_dir], timeout=10)

    def start(self, spec: ProcessSpec) -> ProcessHandle:
        self.preflight(spec)
        assert spec.ssh and spec.worker_config and spec.remote_runtime_dir and spec.shared_runtime_dir
        encoded = base64.urlsafe_b64encode(
            json.dumps(spec.worker_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        argv = list(spec.argv) + ["run", "--spec", encoded]
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = spec.log_path.open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                remote_command(spec.ssh, argv), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except Exception:
            log.close()
            raise
        self._processes[process.pid] = process
        self._logs[process.pid] = log
        identity_path = spec.shared_runtime_dir / "process.json"
        deadline = time.monotonic() + 8.0
        identity: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.collect(ProcessHandle("ssh", spec.producer_id, process.pid, process_start_ticks(process.pid) if Path(f"/proc/{process.pid}").exists() else 0))
                raise ProcessFailure(f"Remote worker exited before publishing identity: {spec.producer_id}")
            try:
                candidate = json.loads(identity_path.read_text(encoding="utf-8"))
                if isinstance(candidate.get("pid"), int) and isinstance(candidate.get("proc_start_ticks"), int):
                    identity = candidate
                    break
            except (OSError, json.JSONDecodeError):
                pass
            time.sleep(0.05)
        if identity is None:
            process.terminate()
            self.collect(ProcessHandle("ssh", spec.producer_id, process.pid, process_start_ticks(process.pid)))
            raise ProcessFailure(f"Remote worker identity timeout: {spec.producer_id}")
        return ProcessHandle(
            backend="ssh", producer_id=spec.producer_id, pid=process.pid,
            proc_start_ticks=process_start_ticks(process.pid), ssh_host=ssh_target(spec.ssh),
            remote_pid=identity["pid"], remote_start_ticks=identity["proc_start_ticks"],
            worker_path=spec.argv[1], remote_runtime_dir=str(spec.remote_runtime_dir),
        )

    def _config_for(self, handle: ProcessHandle) -> dict[str, Any]:
        if not handle.ssh_host:
            raise ProcessFailure("SSH handle has no host")
        return {"host": handle.ssh_host}

    def _remote_control(self, handle: ProcessHandle, action: str) -> ProcessStatus:
        if not all((handle.worker_path, handle.remote_runtime_dir, handle.remote_pid, handle.remote_start_ticks)):
            raise ProcessFailure(f"Incomplete persisted remote identity: {handle.producer_id}")
        result = run_ssh(self._config_for(handle), [
            "python3", handle.worker_path, "control", "--action", action,
            "--runtime-dir", handle.remote_runtime_dir, "--pid", str(handle.remote_pid),
            "--start-ticks", str(handle.remote_start_ticks),
        ], timeout=10, check=False)
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            return ProcessStatus(bool(payload["running"]), None, f"remote {action}")
        except (ValueError, KeyError, IndexError):
            if result.returncode == 255:
                return ProcessStatus(True, None, "SSH transport unavailable")
            return ProcessStatus(False, result.returncode, result.stderr.strip())

    def probe(self, handle: ProcessHandle) -> ProcessStatus:
        process = self._processes.get(handle.pid)
        if process is not None:
            code = process.poll()
            return ProcessStatus(code is None, code, "attached SSH worker command")
        return self._remote_control(handle, "probe")

    def stop(self, handle: ProcessHandle, grace_s: float) -> ProcessStatus:
        status = self._remote_control(handle, "term")
        deadline = time.monotonic() + grace_s
        while status.running and time.monotonic() < deadline:
            time.sleep(0.05)
            status = self.probe(handle)
        return self.collect(handle) if not status.running else status

    def kill(self, handle: ProcessHandle) -> ProcessStatus:
        status = self._remote_control(handle, "kill")
        deadline = time.monotonic() + 2.0
        while status.running and time.monotonic() < deadline:
            time.sleep(0.05)
            status = self.probe(handle)
        return self.collect(handle) if not status.running else status

    def collect(self, handle: ProcessHandle) -> ProcessStatus:
        process = self._processes.get(handle.pid)
        code: int | None = None
        if process is not None:
            try:
                code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return ProcessStatus(True, None, "SSH process still running")
            log = self._logs.pop(handle.pid, None)
            if log:
                log.close()
            self._processes.pop(handle.pid, None)
        elif same_process(handle):
            return ProcessStatus(True, None, "external SSH process")
        return ProcessStatus(False, code if code is not None else 0, "collected")


class FakeSshProcessAdapter(FakeProcessAdapter):
    """SSH-shaped deterministic test double that never opens a socket."""
