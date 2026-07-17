from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

from sync_framework.checksums import sha256_file
from sync_framework.inference import run_dummy_inference
from sync_framework.orchestration import finalize_run, preflight, start_run
from sync_framework.state import utc_now


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ssb_smoke_duration_publication_and_dummy_inference(tmp_path):
    inventory = {
        "schema_version": "1.0.0",
        "inventory_id": "ssb_smoke_fake",
        "storage": {"backend": "local", "root": str(tmp_path / "storage")},
        "nodes": [{
            "node_id": "pc3pc4", "transport": "local", "workspace": str(REPO_ROOT),
            "commands": [{
                "command_id": "ssb_online_rx", "safety_class": "simulation",
                "argv": [
                    sys.executable, "-m", "sync_framework.testing.fake_ssb_smoke_worker",
                    "--output-dir", "{producer_dir}",
                ],
                "cwd": str(REPO_ROOT),
            }],
        }],
    }
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False))
    plan, store = preflight(
        inventory_path,
        REPO_ROOT / "profiles" / "ssb_rx_smoke.yaml",
        {"label": "fake", "duration_s": "1", "min_valid_ssb_rate_hz": "10"},
        repo_root=REPO_ROOT,
    )
    state = start_run(plan, store)
    assert state["state"] == "FINALIZING"
    assert state["processes"]["rx_5g"]["termination_reason"] == "duration_elapsed"
    producer = plan.run_dir / "rx_5g"
    paths = ["rxgridssb.jsonl", "process.log"]
    receipt = {
        "schema_version": "1.0.0", "run_id": plan.run_id, "producer_id": "rx_5g",
        "node_id": "pc3pc4", "simulation": False, "synthetic": False,
        "exit_code": 0, "finished_at": utc_now(),
        "process": {"pid": os.getpid(), "proc_start_ticks": 0, "host": "fake"},
        "artifacts": [
            {"path": path, "size_bytes": (producer / path).stat().st_size, "sha256": sha256_file(producer / path)}
            for path in paths
        ],
    }
    (producer / "producer-result.json").write_text(json.dumps(receipt) + "\n")
    manifest = finalize_run(plan, store, repo_root=REPO_ROOT)
    assert manifest["dataset_qualification"] == "integration_smoke"
    assert manifest["timestamp_semantics"] == "host_serialization_time_operational_only_no_canonical_events"
    producer_manifest = json.loads((producer / "producer-manifest.json").read_text())
    grid = next(a for a in producer_manifest["artifacts"] if a["artifact_type"] == "5g_ssb_rxgrid_rows")
    assert grid["row_count"] >= 10
    assert grid["schema_ref"] == "urn:sync:schema:v1:5g-ssb-rxgrid-row"
    result = run_dummy_inference(plan.run_dir)
    summary = json.loads((plan.run_dir / "inference" / result["inference_id"] / "summary.json").read_text())
    assert summary["ssb_rx_smoke"]["valid_grids"] == grid["row_count"]
    assert summary["ssb_rx_smoke"]["input_data"] == "real_hardware_integration_smoke"
