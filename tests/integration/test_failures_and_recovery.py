from __future__ import annotations

import threading
import time
import json
import os
import signal

import pytest
import yaml

from sync_framework.domain import ProcessFailure, PublicationFailure
from sync_framework.orchestration import finalize_run, preflight, recover_run, start_run, status_run, stop_run
from sync_framework.state import utc_now


def test_stop_request_is_honored(inventory_path, profile_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "10"})

    def request_stop():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.load()["state"] == "RUNNING":
                store.update(lambda state: state.update(stop_request={"requested_at": utc_now(), "reason": "test"}))
                return
            time.sleep(0.01)

    thread = threading.Thread(target=request_stop)
    thread.start()
    state = start_run(plan, store)
    thread.join()
    assert state["state"] == "FINALIZING"
    assert state["history"][-1]["reason"] == "operator_stop"
    assert status_run(plan, store)["state"] == "FINALIZING"


def test_stop_armed_aborts_and_recover_is_idempotent(inventory_path, profile_path, tmp_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "1"})
    assert stop_run(plan, store, dry_run=True)["mutating"] is False
    state = stop_run(plan, store, reason="cancel")
    assert state["state"] == "ABORTED"
    assert recover_run(plan, store, repo_root=tmp_path)["state"] == "ABORTED"


def test_missing_artifact_fails_publication(inventory_path, profile_path, tmp_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "0.1"})
    start_run(plan, store)
    (plan.run_dir / "rx_5g" / "features.bin").unlink()
    with pytest.raises(PublicationFailure):
        finalize_run(plan, store, repo_root=tmp_path)
    assert store.load()["state"] == "FAILED"
    assert not (plan.run_dir / "manifest.json").exists()


def test_recover_resumes_finalization(inventory_path, profile_path, tmp_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "0.1"})
    start_run(plan, store)
    recovered = recover_run(plan, store, repo_root=tmp_path)
    assert recovered["state"] == "COMPLETE"


def test_readiness_timeout_rolls_back_without_tx(inventory_path, profile_path):
    raw = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    for node in raw["nodes"]:
        if node["node_id"] == "pc3":
            node["commands"][0]["argv"].extend(["--ready-delay", "1"])
    inventory_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["processes"][0]["timeouts"]["readiness_s"] = 0.1
    custom_profile = inventory_path.parent / "timeout-profile.yaml"
    custom_profile.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    plan, store = preflight(inventory_path, custom_profile, {"label": "empty", "duration_s": "1"})
    with pytest.raises(Exception):
        start_run(plan, store)
    assert store.load()["state"] == "FAILED"
    trace_path = plan.run_dir / ".control" / "worker-events.jsonl"
    trace = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
    assert '"producer_id": "tx_wifi"' not in trace


def test_transmitter_start_failure_rolls_back_transmitter_first(inventory_path, profile_path):
    raw = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    for node in raw["nodes"]:
        if node["node_id"] == "pc2":
            node["commands"][0]["argv"].append("--fail-start")
    inventory_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "1"})
    with pytest.raises(ProcessFailure):
        start_run(plan, store)
    assert store.load()["state"] == "FAILED"
    trace = [json.loads(line) for line in (plan.run_dir / ".control" / "worker-events.jsonl").read_text().splitlines()]
    tx_started = next(i for i, item in enumerate(trace) if item["producer_id"] == "tx_wifi" and item["event"] == "started")
    rx_stops = [i for i, item in enumerate(trace) if item["producer_id"].startswith("rx_") and item["event"] == "stopping"]
    assert rx_stops and all(tx_started < index for index in rx_stops)


def test_receiver_runtime_exit_fails_and_cleans_up(inventory_path, profile_path):
    raw = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    for node in raw["nodes"]:
        if node["node_id"] == "pc3":
            node["commands"][0]["argv"].extend(["--exit-after", "0.1"])
    inventory_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "2"})
    with pytest.raises(ProcessFailure, match="exited unexpectedly"):
        start_run(plan, store)
    assert store.load()["state"] == "FAILED"


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_supervisor_signals_abort_after_ordered_cleanup(inventory_path, profile_path, signum):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "10"})

    def interrupt_running():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.load()["state"] == "RUNNING":
                os.kill(os.getpid(), signum)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=interrupt_running)
    thread.start()
    state = start_run(plan, store)
    thread.join()
    assert state["state"] == "ABORTED"
    assert state["history"][-1]["reason"] == f"signal_{signum}"
    trace = [json.loads(line) for line in (plan.run_dir / ".control" / "worker-events.jsonl").read_text().splitlines()]
    stops = {(item["producer_id"], item["event"]): i for i, item in enumerate(trace)}
    assert stops[("tx_wifi", "stopping")] < stops[("rx_5g", "stopping")]


def test_invalid_event_and_repeated_lifecycle_are_blocked(inventory_path, profile_path, tmp_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "0.1"})
    start_run(plan, store)
    with pytest.raises(ProcessFailure):
        start_run(plan, store)
    event_path = plan.run_dir / "rx_5g" / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    events[0]["timestamp"]["ticks"] += 1
    event_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    with pytest.raises(PublicationFailure):
        finalize_run(plan, store, repo_root=tmp_path)
    assert store.load()["state"] == "FAILED"
    with pytest.raises(PublicationFailure):
        finalize_run(plan, store, repo_root=tmp_path)
