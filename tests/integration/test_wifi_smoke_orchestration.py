from __future__ import annotations

import sys
import json
import os
from pathlib import Path

import yaml

from sync_framework.orchestration import preflight, start_run
from sync_framework.orchestration import finalize_run
from sync_framework.inference import run_dummy_inference
from sync_framework.checksums import sha256_file
from sync_framework.state import utc_now


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_wifi_smoke_finite_tx_then_quiet_rx(tmp_path):
    inventory = {
        "schema_version": "1.0.0",
        "inventory_id": "wifi_smoke_fake",
        "storage": {"backend": "local", "root": str(tmp_path / "storage")},
        "nodes": [
            {
                "node_id": "pc2", "transport": "local", "workspace": str(REPO_ROOT),
                "commands": [{
                    "command_id": "wifi_finite_tx", "safety_class": "simulation",
                    "argv": [sys.executable, "-m", "sync_framework.testing.fake_wifi_smoke_worker", "--role", "transmitter", "--output-dir", "{producer_dir}"],
                    "cwd": str(REPO_ROOT),
                }],
            },
            {
                "node_id": "pc3pc4", "transport": "local", "workspace": str(REPO_ROOT),
                "commands": [{
                    "command_id": "wifi_online_rx", "safety_class": "simulation",
                    "argv": [sys.executable, "-m", "sync_framework.testing.fake_wifi_smoke_worker", "--role", "receiver", "--output-dir", "{producer_dir}"],
                    "cwd": str(REPO_ROOT),
                }],
            },
        ],
    }
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False))
    plan, store = preflight(
        inventory_path, REPO_ROOT / "profiles" / "wifi_link_smoke.yaml",
        {"label": "fake", "num_beacons": "1", "rx_quiet_s": "0.1", "rx_max_drain_s": "1"},
        repo_root=REPO_ROOT,
    )
    state = start_run(plan, store)
    assert state["state"] == "FINALIZING"
    assert state["processes"]["tx_wifi"]["termination_reason"] == "completed"
    assert state["processes"]["rx_wifi"]["termination_reason"] == "finite_tx_drain_complete"
    assert state["processes"]["tx_wifi"]["stopped_at"] <= state["processes"]["rx_wifi"]["stopped_at"]
    for producer_id, paths in {
        "rx_wifi": ["features.jsonl", "csi.cf32", "process.log"],
        "tx_wifi": ["process.log"],
    }.items():
        producer_dir = plan.run_dir / producer_id
        receipt = {
            "schema_version": "1.0.0", "run_id": plan.run_id, "producer_id": producer_id,
            "node_id": plan.processes[producer_id].definition.node_id,
            "simulation": False, "synthetic": False, "exit_code": 0,
            "finished_at": utc_now(),
            "process": {"pid": os.getpid(), "proc_start_ticks": 0, "host": "fake"},
            "artifacts": [
                {"path": path, "size_bytes": (producer_dir / path).stat().st_size, "sha256": sha256_file(producer_dir / path)}
                for path in paths
            ],
        }
        (producer_dir / "producer-result.json").write_text(json.dumps(receipt) + "\n")
    manifest = finalize_run(plan, store, repo_root=REPO_ROOT)
    assert manifest["state"] == "COMPLETE"
    assert manifest["dataset_qualification"] == "integration_smoke"
    result = run_dummy_inference(plan.run_dir)
    summary = json.loads((plan.run_dir / "inference" / result["inference_id"] / "summary.json").read_text())
    assert summary["wifi_smoke"]["beacons_requested"] == 1
    assert summary["wifi_smoke"]["frames_received"] == 1
