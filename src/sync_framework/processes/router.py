"""Select local or SSH adapters without losing per-process state."""

from __future__ import annotations

from ..domain import ProcessFailure
from .base import ProcessHandle, ProcessSpec
from .local import LocalProcessAdapter
from .ssh import SshProcessAdapter


class ProcessRouter:
    def __init__(self, *, allow_remote_simulation: bool = False) -> None:
        self.local = LocalProcessAdapter()
        self.ssh = SshProcessAdapter(allow_remote_simulation=allow_remote_simulation)

    def for_spec(self, spec: ProcessSpec):
        if spec.transport == "local":
            return self.local
        if spec.transport == "ssh":
            return self.ssh
        raise ProcessFailure(f"Unsupported process transport: {spec.transport}")

    def for_handle(self, handle: ProcessHandle):
        if handle.backend == "local":
            return self.local
        if handle.backend == "ssh":
            return self.ssh
        raise ProcessFailure(f"Unsupported process backend: {handle.backend}")
