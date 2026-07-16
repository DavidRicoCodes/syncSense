from __future__ import annotations

from pathlib import Path

import pytest

from sync_framework.domain import CapabilityDisabled
from sync_framework.processes.base import ProcessSpec
from sync_framework.processes.fake import FakeBehavior, FakeProcessAdapter
from sync_framework.processes.ssh import FakeSshProcessAdapter, SshProcessAdapter


def spec(name="rx", safety="simulation"):
    return ProcessSpec(name, ("true",), Path("."), {}, Path("/tmp/fake.log"), safety)


def test_fake_adapter_records_order():
    adapter = FakeProcessAdapter()
    handle = adapter.start(spec())
    assert adapter.probe(handle).running
    assert not adapter.stop(handle, 0).running
    assert adapter.events == [("start", "rx"), ("stop", "rx")]


def test_fake_start_failure():
    adapter = FakeProcessAdapter({"rx": FakeBehavior(fail_start=True)})
    with pytest.raises(Exception):
        adapter.start(spec())


def test_real_ssh_is_not_constructible():
    with pytest.raises(CapabilityDisabled):
        SshProcessAdapter()


def test_fake_ssh_uses_only_the_in_memory_process_contract():
    adapter = FakeSshProcessAdapter()
    handle = adapter.start(spec("remote-shaped"))
    assert handle.backend == "fake"
    assert adapter.stop(handle, 0).exit_code == 0
