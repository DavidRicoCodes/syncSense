"""Deterministic in-memory adapter for unit tests."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import ProcessFailure
from .base import ProcessHandle, ProcessSpec, ProcessStatus


@dataclass
class FakeBehavior:
    fail_start: bool = False
    ignore_stop: bool = False
    exit_code: int = 0


class FakeProcessAdapter:
    def __init__(self, behaviors: dict[str, FakeBehavior] | None = None):
        self.behaviors = behaviors or {}
        self.running: dict[int, str] = {}
        self.events: list[tuple[str, str]] = []
        self._next_pid = 10000

    def preflight(self, spec: ProcessSpec) -> None:
        if spec.safety_class != "simulation":
            raise ProcessFailure("Fake adapter accepts simulation commands only")

    def start(self, spec: ProcessSpec) -> ProcessHandle:
        behavior = self.behaviors.get(spec.producer_id, FakeBehavior())
        self.events.append(("start", spec.producer_id))
        if behavior.fail_start:
            raise ProcessFailure(f"Programmed fake start failure: {spec.producer_id}")
        self._next_pid += 1
        self.running[self._next_pid] = spec.producer_id
        return ProcessHandle("fake", spec.producer_id, self._next_pid, self._next_pid)

    def probe(self, handle: ProcessHandle) -> ProcessStatus:
        running = handle.pid in self.running
        behavior = self.behaviors.get(handle.producer_id, FakeBehavior())
        return ProcessStatus(running, None if running else behavior.exit_code)

    def stop(self, handle: ProcessHandle, grace_s: float) -> ProcessStatus:
        self.events.append(("stop", handle.producer_id))
        behavior = self.behaviors.get(handle.producer_id, FakeBehavior())
        if not behavior.ignore_stop:
            self.running.pop(handle.pid, None)
        return self.probe(handle)

    def kill(self, handle: ProcessHandle) -> ProcessStatus:
        self.events.append(("kill", handle.producer_id))
        self.running.pop(handle.pid, None)
        return self.probe(handle)

    def collect(self, handle: ProcessHandle) -> ProcessStatus:
        return self.probe(handle)

