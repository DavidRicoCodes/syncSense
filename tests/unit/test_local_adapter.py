from __future__ import annotations

import signal
import sys
import time

import pytest

from sync_framework.domain import CapabilityDisabled, ProcessFailure
from sync_framework.processes.base import ProcessSpec
from sync_framework.processes.local import LocalProcessAdapter


def make_spec(tmp_path, code, safety="simulation"):
    return ProcessSpec("worker", (sys.executable, "-c", code), tmp_path, {}, tmp_path / "worker.log", safety)


def test_local_adapter_preflight_rejects_unsafe_and_missing(tmp_path):
    adapter = LocalProcessAdapter()
    with pytest.raises(CapabilityDisabled):
        adapter.preflight(make_spec(tmp_path, "pass", safety="dsp"))
    with pytest.raises(ProcessFailure):
        adapter.preflight(ProcessSpec("x", ("definitely-missing",), tmp_path, {}, tmp_path / "x.log", "simulation"))


def test_local_adapter_stop_and_kill(tmp_path):
    adapter = LocalProcessAdapter()
    handle = adapter.start(make_spec(tmp_path, "import time; time.sleep(30)"))
    assert adapter.probe(handle).running
    stopped = adapter.stop(handle, 1)
    assert not stopped.running

    ignored = adapter.start(make_spec(tmp_path, "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"))
    time.sleep(0.1)
    assert adapter.stop(ignored, 0.05).running
    killed = adapter.kill(ignored)
    assert not killed.running
