from __future__ import annotations

import json

import pytest

from sync_framework.domain import InvalidTransition, ValidationFailure
from sync_framework.run_id import RUN_ID_RE, generate_run_id, validate_run_id
from sync_framework.state import StateStore
from sync_framework.storage import create_run_layout
from sync_framework.timebase import frame_timestamp_ticks


def test_run_id_format_and_uniqueness():
    first = generate_run_id()
    second = generate_run_id()
    assert RUN_ID_RE.fullmatch(first)
    assert first != second
    with pytest.raises(ValidationFailure):
        validate_run_id("../unsafe")


def test_exact_frame_timestamp():
    assert frame_timestamp_ticks(100, 17, 20_000_000) == {"ticks": 117, "tick_rate_hz": 20_000_000}
    with pytest.raises(ValidationFailure):
        frame_timestamp_ticks(0, -1, 1)


def test_state_machine_and_audit(tmp_path):
    run_id = generate_run_id()
    run_dir = create_run_layout(tmp_path, run_id, ["rx"])
    store = StateStore(run_dir)
    store.create(
        run_id=run_id,
        profile={"profile_id": "test", "profile_version": "1.0.0", "digest": "0" * 64, "source_path": "profile.yaml", "parameters": {}},
        inventory={"inventory_id": "test", "digest": "1" * 64},
        inventory_path="inventory.yaml",
        processes={},
    )
    store.transition("PREFLIGHT", reason="test")
    store.transition("ARMED", reason="test")
    with pytest.raises(InvalidTransition):
        store.transition("COMPLETE", reason="illegal")
    state = store.load()
    assert state["state"] == "ARMED"
    lines = [json.loads(line) for line in store.audit_path.read_text(encoding="utf-8").splitlines()]
    assert [line["to"] for line in lines] == ["PREFLIGHT", "ARMED"]

    store.audit_path.write_text("corrupt\n", encoding="utf-8")
    store.update(lambda state: state.update(stop_request=None))
    repaired = [json.loads(line) for line in store.audit_path.read_text(encoding="utf-8").splitlines()]
    assert repaired == store.load()["history"]
