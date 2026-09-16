from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

from sync_framework.checksums import sha256_file
from sync_framework.inference import run_dummy_inference
from sync_framework.orchestration import finalize_run, preflight, start_run
from sync_framework.planning import experiment_id_for_run
from sync_framework.state import utc_now
from sync_framework.testing.bf_like_data import write_receiver, write_transmitter


REPO_ROOT = Path(__file__).resolve().parents[2]


def _receipt(plan, producer_id: str) -> None:
    producer = plan.processes[producer_id]
    artifacts = [
        expected.path
        for expected in producer.definition.expected_artifacts
        if expected.artifact_type != "producer_result"
    ]
    value = {
        "schema_version": "1.0.0",
        "run_id": plan.run_id,
        "producer_id": producer_id,
        "node_id": producer.definition.node_id,
        "simulation": False,
        "synthetic": False,
        "exit_code": 0,
        "finished_at": utc_now(),
        "process": {"pid": os.getpid(), "proc_start_ticks": 0, "host": "fake"},
        "artifacts": [
            {
                "path": path,
                "size_bytes": (producer.producer_dir / path).stat().st_size,
                "sha256": sha256_file(producer.producer_dir / path),
            }
            for path in artifacts
        ],
    }
    (producer.producer_dir / "producer-result.json").write_text(
        json.dumps(value) + "\n"
    )


def test_bf_like_receiver_first_publication_and_dummy_inference(tmp_path):
    profile = yaml.safe_load(
        (REPO_ROOT / "profiles" / "wifi_bf_like.yaml").read_text()
    )
    profile["processes"][0]["readiness"] = {
        "type": "json_file",
        "path": "runtime/status.json",
        "json_pointer": "/status",
        "equals": "ready",
    }
    profile_path = tmp_path / "wifi_bf_like.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))
    inventory = {
        "schema_version": "1.0.0",
        "inventory_id": "bf_like_fake",
        "storage": {"backend": "local", "root": str(tmp_path / "storage")},
        "nodes": [
            {
                "node_id": "pc2", "transport": "local",
                "workspace": str(REPO_ROOT),
                "commands": [{
                    "command_id": "wifi_bf_like_rx",
                    "safety_class": "simulation",
                    "argv": [sys.executable, "-m", "sync_framework.testing.fake_wifi_smoke_worker", "--role", "receiver", "--output-dir", "{producer_dir}"],
                    "cwd": str(REPO_ROOT),
                    "env": {"SYNC_WIFI_RX_DEVICE_ARGS": "serial=fake"},
                }],
            },
            {
                "node_id": "pc3pc4", "transport": "local",
                "workspace": str(REPO_ROOT),
                "commands": [{
                    "command_id": "wifi_bf_like_n310_tx",
                    "safety_class": "simulation",
                    "argv": [sys.executable, "-m", "sync_framework.testing.fake_wifi_smoke_worker", "--role", "transmitter", "--output-dir", "{producer_dir}"],
                    "cwd": str(REPO_ROOT),
                }],
            },
        ],
    }
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False))
    plan, store = preflight(
        inventory_path,
        profile_path,
        {
            "label": "bf-fake", "num_packets": "1", "rx_quiet_s": "0.1",
            "rx_max_drain_s": "1",
        },
        repo_root=REPO_ROOT,
    )
    state = start_run(plan, store)
    assert state["state"] == "FINALIZING"
    assert state["processes"]["tx_wifi"]["started_at"] >= state["processes"]["rx_wifi"]["ready_at"]
    assert state["processes"]["tx_wifi"]["stopped_at"] <= state["processes"]["rx_wifi"]["stopped_at"]
    experiment_id = experiment_id_for_run(plan.run_id)
    write_receiver(plan.run_dir / "rx_wifi", experiment_id=experiment_id)
    write_transmitter(plan.run_dir / "tx_wifi", experiment_id=experiment_id)
    _receipt(plan, "rx_wifi")
    _receipt(plan, "tx_wifi")
    manifest = finalize_run(plan, store, repo_root=REPO_ROOT)
    assert manifest["state"] == "COMPLETE"
    assert manifest["dataset_qualification"] == "integration_smoke"
    assert manifest["timestamp_semantics"] == "validated_local_usrp_device_fields_no_canonical_event_index"
    rx_manifest = json.loads((plan.run_dir / "rx_wifi" / "producer-manifest.json").read_text())
    feature = next(
        artifact for artifact in rx_manifest["artifacts"]
        if artifact["artifact_type"] == "wifi_he_ltf_feature_rows"
    )
    assert feature["row_count"] == 1
    assert feature["schema_ref"] == "urn:sync:schema:v1:wifi-bf-like-feature-row"
    result = run_dummy_inference(plan.run_dir)
    summary = json.loads(
        (plan.run_dir / "inference" / result["inference_id"] / "summary.json").read_text()
    )
    assert summary["wifi_bf_like"]["packets_requested"] == 1
    assert summary["wifi_bf_like"]["frames_received"] == 1
    assert summary["wifi_bf_like"]["feature_shape"] == [8, 242]
    assert summary["wifi_bf_like"]["waveform_profile"] == "alb_he_ndp_like_siso_40mhz_v1"
    assert summary["wifi_bf_like"]["classification_performed"] is False
