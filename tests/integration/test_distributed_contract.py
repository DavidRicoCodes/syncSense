from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.domain import CapabilityDisabled, InferenceFailure
from sync_framework.inference import inference_status, run_dummy_inference
from sync_framework.orchestration import finalize_run, preflight, start_run
from sync_framework.planning import build_plan
from sync_framework.publication import _validate_dummy_receipt


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_distributed_profile_has_independent_synthetic_clocks():
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.distributed.example.yaml")
    profile = load_profile(REPO_ROOT / "profiles" / "distributed_dummy.yaml")
    parameters = resolve_parameters(profile, {"label": "contract", "duration_s": "1"})
    plan = build_plan(inventory, profile, parameters)
    assert profile.experiment_type == "distributed_dummy"
    assert {item["epoch"] for item in profile.clock_domains} == {"synthetic_epoch"}
    assert profile.clock_relationships[0]["relation"] == "not_comparable"
    assert plan.processes["rx_5g"].execution_producer_dir.as_posix().startswith("/mnt/sync-experiments/")
    with pytest.raises(CapabilityDisabled):
        preflight(
            REPO_ROOT / "config" / "inventory.distributed.example.yaml",
            REPO_ROOT / "profiles" / "distributed_dummy.yaml",
            {"label": "blocked", "duration_s": "1"},
        )


def test_standalone_worker_writes_atomic_receipt(tmp_path):
    output = tmp_path / "runs" / "run_test" / "rx_5g"
    (output.parent / ".control").mkdir(parents=True)
    spec = {
        "run_id": "run_test", "producer_id": "rx_5g", "node_id": "pc3pc4",
        "role": "receiver", "modality": "5g", "output_dir": str(output),
        "clock_domain_id": "synthetic_5g_epoch", "artifact_ids": {"features": "rx_5g_features"},
    }
    encoded = base64.urlsafe_b64encode(json.dumps(spec).encode()).decode().rstrip("=")
    process = subprocess.Popen([sys.executable, str(REPO_ROOT / "tools" / "remote_dummy_worker.py"), "run", "--spec", encoded])
    deadline = time.monotonic() + 5
    while not (output / "runtime" / "status.json").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    os.kill(process.pid, signal.SIGTERM)
    assert process.wait(timeout=5) == 0
    receipt = json.loads((output / "producer-result.json").read_text())
    assert receipt["simulation"] is True and receipt["synthetic"] is True
    assert {item["path"] for item in receipt["artifacts"]} == {"features.bin", "events.jsonl", "metrics.json"}
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.distributed.example.yaml")
    profile = load_profile(REPO_ROOT / "profiles" / "distributed_dummy.yaml")
    plan = build_plan(
        inventory, profile, resolve_parameters(profile, {"label": "receipt", "duration_s": "1"}),
        run_id="run_test", run_dir=output.parent,
    )
    _validate_dummy_receipt(plan, "rx_5g")


def test_dummy_inference_preserves_complete_dataset(inventory_path, profile_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "infer", "duration_s": "0.1"})
    start_run(plan, store)
    finalize_run(plan, store, repo_root=REPO_ROOT)
    before = (plan.run_dir / "manifest.json").read_bytes()
    result = run_dummy_inference(plan.run_dir)
    assert result["status"] == "SUCCEEDED"
    assert result["artifacts"][0]["artifact_type"] == "synthetic_inference_summary"
    assert (plan.run_dir / "manifest.json").read_bytes() == before
    assert inference_status(plan.run_dir, result["inference_id"])["status"] == "SUCCEEDED"
    assert len(inference_status(plan.run_dir)["inferences"]) == 1
    with pytest.raises(InferenceFailure, match="Unknown inference"):
        inference_status(plan.run_dir, "missing")
