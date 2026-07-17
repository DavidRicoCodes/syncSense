from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml
import pytest

from sync_framework.checksums import sha256_file
from sync_framework.domain import ProcessFailure
from sync_framework.inference import run_dummy_inference
from sync_framework.orchestration import finalize_run, preflight, start_run
from sync_framework.state import utc_now


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_receipt(plan, producer_id: str, paths: list[str]) -> None:
    producer_dir = plan.run_dir / producer_id
    receipt = {
        "schema_version": "1.0.0",
        "run_id": plan.run_id,
        "producer_id": producer_id,
        "node_id": plan.processes[producer_id].definition.node_id,
        "simulation": False,
        "synthetic": False,
        "exit_code": 0,
        "finished_at": utc_now(),
        "process": {"pid": os.getpid(), "proc_start_ticks": 0, "host": "fake"},
        "artifacts": [
            {
                "path": path,
                "size_bytes": (producer_dir / path).stat().st_size,
                "sha256": sha256_file(producer_dir / path),
            }
            for path in paths
        ],
    }
    (producer_dir / "producer-result.json").write_text(
        json.dumps(receipt) + "\n",
        encoding="utf-8",
    )


def test_combined_nosync_hardware_smoke_closes_both_receivers(tmp_path):
    inventory = {
        "schema_version": "1.0.0",
        "inventory_id": "nosync_hardware_fake",
        "storage": {"backend": "local", "root": str(tmp_path / "storage")},
        "nodes": [
            {
                "node_id": "pc2",
                "transport": "local",
                "workspace": str(REPO_ROOT),
                "commands": [
                    {
                        "command_id": "wifi_finite_tx",
                        "safety_class": "simulation",
                        "argv": [
                            sys.executable,
                            "-m",
                            "sync_framework.testing.fake_wifi_smoke_worker",
                            "--role",
                            "transmitter",
                            "--output-dir",
                            "{producer_dir}",
                        ],
                        "cwd": str(REPO_ROOT),
                    }
                ],
            },
            {
                "node_id": "pc3pc4",
                "transport": "local",
                "workspace": str(REPO_ROOT),
                "commands": [
                    {
                        "command_id": "wifi_online_rx",
                        "safety_class": "simulation",
                        "argv": [
                            sys.executable,
                            "-m",
                            "sync_framework.testing.fake_wifi_smoke_worker",
                            "--role",
                            "receiver",
                            "--output-dir",
                            "{producer_dir}",
                        ],
                        "cwd": str(REPO_ROOT),
                    },
                    {
                        "command_id": "ssb_online_rx",
                        "safety_class": "simulation",
                        "argv": [
                            sys.executable,
                            "-m",
                            "sync_framework.testing.fake_ssb_smoke_worker",
                            "--output-dir",
                            "{producer_dir}",
                        ],
                        "cwd": str(REPO_ROOT),
                    },
                ],
            },
        ],
    }
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")
    plan, store = preflight(
        inventory_path,
        REPO_ROOT / "profiles" / "nosync_passive_hardware_smoke.yaml",
        {
            "label": "combined-fake",
            "num_beacons": "1",
            "rx_quiet_s": "0.1",
            "rx_max_drain_s": "1",
            "min_valid_ssb_rate_hz": "0.1",
        },
        repo_root=REPO_ROOT,
    )

    state = start_run(plan, store)

    assert state["state"] == "FINALIZING"
    assert state["processes"]["tx_wifi"]["termination_reason"] == "completed"
    assert state["processes"]["rx_5g"]["termination_reason"] == "finite_tx_drain_complete"
    assert state["processes"]["rx_wifi"]["termination_reason"] == "finite_tx_drain_complete"
    assert state["processes"]["tx_wifi"]["ready_at"] >= state["processes"]["rx_5g"]["ready_at"]
    assert state["processes"]["tx_wifi"]["ready_at"] >= state["processes"]["rx_wifi"]["ready_at"]
    window = state["operational_window"]
    assert window["semantics"] == "pc5_supervisor_operational_boundary_not_acquisition_time"
    assert window["duration_s"] > 0
    assert set(window["producer_active_duration_s"]) == {"rx_5g", "rx_wifi"}
    rx_stop_delta = abs(
        (
            datetime.fromisoformat(state["processes"]["rx_5g"]["stopped_at"])
            - datetime.fromisoformat(state["processes"]["rx_wifi"]["stopped_at"])
        ).total_seconds()
    )
    assert rx_stop_delta < 1

    _write_receipt(plan, "rx_5g", ["rxgridssb.jsonl", "process.log"])
    _write_receipt(
        plan,
        "rx_wifi",
        [
            "features.jsonl",
            "csi.cf32",
            "frame-timings.jsonl",
            "block-timings.jsonl",
            "process.log",
        ],
    )
    _write_receipt(plan, "tx_wifi", ["process.log"])

    manifest = finalize_run(plan, store, repo_root=REPO_ROOT)

    assert manifest["state"] == "COMPLETE"
    assert manifest["dataset_qualification"] == "integration_smoke"
    assert manifest["operational_window"] == window
    assert manifest["clock_relationships"] == [
        {
            "left": "pc3pc4_5g_b210_acquisition",
            "right": "pc3pc4_wifi_b210_acquisition",
            "relation": "not_comparable",
            "reason": "separate_b210_devices_without_common_acquisition_timebase",
        }
    ]
    assert "no_cross_band_pairing" in manifest["timestamp_semantics"]
    result = run_dummy_inference(plan.run_dir)
    summary = json.loads(
        (
            plan.run_dir
            / "inference"
            / result["inference_id"]
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    combined = summary["nosync_passive_hardware_smoke"]
    assert combined["wifi"]["beacons_requested"] == 1
    assert combined["wifi"]["frames_received"] == 1
    assert combined["ssb_5g"]["valid_grids"] > 0
    assert combined["fusion_performed"] is False
    assert combined["clock_relation"] == "not_comparable"


def test_combined_nosync_does_not_start_tx_when_one_receiver_never_readies(tmp_path):
    inventory = {
        "schema_version": "1.0.0",
        "inventory_id": "nosync_hardware_failed_rx",
        "storage": {"backend": "local", "root": str(tmp_path / "storage")},
        "nodes": [
            {
                "node_id": "pc2",
                "transport": "local",
                "workspace": str(REPO_ROOT),
                "commands": [
                    {
                        "command_id": "wifi_finite_tx",
                        "safety_class": "simulation",
                        "argv": [
                            sys.executable,
                            "-m",
                            "sync_framework.testing.fake_wifi_smoke_worker",
                            "--role",
                            "transmitter",
                            "--output-dir",
                            "{producer_dir}",
                        ],
                        "cwd": str(REPO_ROOT),
                    }
                ],
            },
            {
                "node_id": "pc3pc4",
                "transport": "local",
                "workspace": str(REPO_ROOT),
                "commands": [
                    {
                        "command_id": "wifi_online_rx",
                        "safety_class": "simulation",
                        "argv": [sys.executable, "-c", "raise SystemExit(2)"],
                        "cwd": str(REPO_ROOT),
                    },
                    {
                        "command_id": "ssb_online_rx",
                        "safety_class": "simulation",
                        "argv": [
                            sys.executable,
                            "-m",
                            "sync_framework.testing.fake_ssb_smoke_worker",
                            "--output-dir",
                            "{producer_dir}",
                        ],
                        "cwd": str(REPO_ROOT),
                    },
                ],
            },
        ],
    }
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")
    plan, store = preflight(
        inventory_path,
        REPO_ROOT / "profiles" / "nosync_passive_hardware_smoke.yaml",
        {
            "label": "failed-rx",
            "num_beacons": "1",
            "rx_quiet_s": "0.1",
            "rx_max_drain_s": "1",
            "min_valid_ssb_rate_hz": "0.1",
        },
        repo_root=REPO_ROOT,
    )

    with pytest.raises(ProcessFailure, match="before readiness"):
        start_run(plan, store)

    state = store.load()
    assert state["state"] == "FAILED"
    assert state["processes"]["tx_wifi"]["status"] == "planned"
    assert state["processes"]["tx_wifi"]["started_at"] is None
    assert state["operational_window"] is None
    assert not (plan.run_dir / "manifest.json").exists()
