"""Process adapter contract and shared process records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ProcessSpec:
    producer_id: str
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    log_path: Path
    safety_class: str
    transport: str = "local"
    ssh: dict[str, Any] | None = None
    worker_config: dict[str, Any] | None = None
    remote_runtime_dir: Path | None = None
    shared_runtime_dir: Path | None = None


@dataclass(frozen=True)
class ProcessHandle:
    backend: str
    producer_id: str
    pid: int
    proc_start_ticks: int
    ssh_host: str | None = None
    remote_pid: int | None = None
    remote_start_ticks: int | None = None
    worker_path: str | None = None
    remote_runtime_dir: str | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, value: dict) -> "ProcessHandle":
        return cls(**value)


@dataclass(frozen=True)
class ProcessStatus:
    running: bool
    exit_code: int | None = None
    detail: str = ""


class ProcessAdapter(Protocol):
    def preflight(self, spec: ProcessSpec) -> None: ...
    def start(self, spec: ProcessSpec) -> ProcessHandle: ...
    def probe(self, handle: ProcessHandle) -> ProcessStatus: ...
    def stop(self, handle: ProcessHandle, grace_s: float) -> ProcessStatus: ...
    def kill(self, handle: ProcessHandle) -> ProcessStatus: ...
    def collect(self, handle: ProcessHandle) -> ProcessStatus: ...
