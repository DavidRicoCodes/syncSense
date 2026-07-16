from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest
import yaml

from sync_framework.checksums import sha256_file
from sync_framework.cli import emit, parse_params
from sync_framework.domain import ProcessFailure, PublicationFailure, ValidationFailure
from sync_framework.model_adapter import validate_batch_request, validate_batch_result
from sync_framework.orchestration import (
    finalize_run,
    load_plan_for_run,
    preflight,
    recover_run,
    start_run,
    status_run,
    stop_run,
)
from sync_framework.publication import verify_published_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cli_helpers_and_preflight_status(inventory_path, profile_path, capsys):
    assert parse_params(["label=a=b", "duration_s=1"]) == {"label": "a=b", "duration_s": "1"}
    for values in (["missing"], ["x=1", "x=2"], ["=bad"]):
        with pytest.raises(ValidationFailure):
            parse_params(values)
    emit("hello", "text")
    assert capsys.readouterr().out == "hello\n"

    plan, store = preflight(inventory_path, profile_path, {"label": "x", "duration_s": "1"})
    status = status_run(plan, store)
    assert status["state"] == "ARMED"
    assert all(not item["running"] for item in status["process_health"].values())
    assert start_run(plan, store, dry_run=True)["mutating"] is False
    assert finalize_run(plan, store, repo_root=REPO_ROOT, dry_run=True)["mutating"] is False
    assert recover_run(plan, store, repo_root=REPO_ROOT, dry_run=True)["recovery_action"] == "abort_incomplete_run"


def test_invalid_lifecycle_and_changed_inventory(inventory_path, profile_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "x", "duration_s": "1"})
    stop_run(plan, store, reason="cancel")
    with pytest.raises(ProcessFailure, match="ARMED before start"):
        start_run(plan, store)
    with pytest.raises(ProcessFailure, match="RUNNING or ARMED"):
        stop_run(plan, store)

    raw = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    raw["inventory_id"] = "changed"
    inventory_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationFailure, match="Inventory changed"):
        load_plan_for_run(inventory_path, plan.run_id)


def test_batch_contract_and_published_corruption(inventory_path, profile_path, tmp_path, monkeypatch):
    plan, store = preflight(inventory_path, profile_path, {"label": "x", "duration_s": "0.1"})
    start_run(plan, store)
    manifest = finalize_run(plan, store, repo_root=REPO_ROOT)
    manifest_path = plan.run_dir / "manifest.json"
    monkeypatch.chdir(plan.inventory.storage_root)
    relative_manifest = manifest_path.relative_to(plan.inventory.storage_root).as_posix()
    request = {
        "schema_version": "1.0.0", "inference_id": "infer_1", "run_id": plan.run_id,
        "session_manifest_path": relative_manifest, "session_manifest_sha256": sha256_file(manifest_path),
        "adapter": {"adapter_id": "external", "adapter_version": "1", "config_digest": "0" * 64},
        "output_directory": f"runs/{plan.run_id}/inference/infer_1",
    }
    validate_batch_request(request)
    missing = copy.deepcopy(request)
    missing["session_manifest_path"] = "runs/missing/manifest.json"
    with pytest.raises(ValidationFailure, match="does not exist"):
        validate_batch_request(missing)
    bad_digest = copy.deepcopy(request)
    bad_digest["session_manifest_sha256"] = "f" * 64
    with pytest.raises(ValidationFailure, match="checksum"):
        validate_batch_request(bad_digest)

    result = {
        "schema_version": "1.0.0", "inference_id": "infer_1", "run_id": plan.run_id, "status": "SUCCEEDED",
        "adapter": {"adapter_id": "external", "adapter_version": "1"}, "started_at": manifest["published_at"],
        "finished_at": manifest["published_at"], "inputs": [relative_manifest], "outputs": [], "artifacts": [], "error": None,
    }
    validate_batch_result(result)

    producer_manifest = plan.run_dir / manifest["producers"][0]["manifest_path"]
    producer_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(PublicationFailure, match="checksum mismatch"):
        verify_published_manifest(plan.run_dir)


def test_recover_synthetic_incomplete_running(inventory_path, profile_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "x", "duration_s": "1"})
    store.transition("RUNNING", reason="synthetic_crash")
    recovered = recover_run(plan, store, repo_root=REPO_ROOT)
    assert recovered["state"] == "ABORTED"


def test_existing_supervisor_claim_and_concurrent_finalize_are_blocked(inventory_path, profile_path):
    claimed_plan, claimed_store = preflight(inventory_path, profile_path, {"label": "x", "duration_s": "1"})
    marker = {"pid": 1, "proc_start_ticks": 1, "heartbeat_at": claimed_store.load()["updated_at"]}
    claimed_store.update(lambda state: state.update(supervisor=marker))
    with pytest.raises(ProcessFailure, match="no longer available"):
        start_run(claimed_plan, claimed_store)
    assert claimed_store.load()["supervisor"] == marker

    plan, store = preflight(inventory_path, profile_path, {"label": "x", "duration_s": "0.1"})
    start_run(plan, store)
    outcomes = []

    def publish():
        try:
            outcomes.append(("ok", finalize_run(plan, store, repo_root=REPO_ROOT)["state"]))
        except PublicationFailure as exc:
            outcomes.append(("blocked", str(exc)))

    first = threading.Thread(target=publish)
    second = threading.Thread(target=publish)
    first.start()
    second.start()
    first.join()
    second.join()
    assert sorted(kind for kind, _ in outcomes) == ["blocked", "ok"]
    assert store.load()["state"] == "COMPLETE"
    assert verify_published_manifest(plan.run_dir)["state"] == "COMPLETE"
