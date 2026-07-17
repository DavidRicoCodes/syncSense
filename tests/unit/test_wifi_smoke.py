from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sync_framework.config import load_profile, resolve_parameters
from sync_framework.domain import PublicationFailure, ValidationFailure
from sync_framework.wifi_smoke import (
    CSI_ELEMENTS_PER_FRAME,
    MEMORY_MARGIN_BYTES,
    available_memory_bytes,
    global_timeout_s,
    required_available_memory_bytes,
    required_frames,
    tx_buffer_bytes,
    validate_wifi_smoke_outputs,
)
from sync_framework.config import load_inventory
from sync_framework.planning import build_plan
from sync_framework.orchestration import _hardware_preflight, _prepare_wifi_config
from sync_framework.domain import ProcessFailure


REPO_ROOT = Path(__file__).resolve().parents[2]


def _row(counter: int) -> dict:
    return {
        "packet_counter": counter,
        "sample_offset": counter * 2_048_000,
        "complex_features": [{"real": float(i), "imag": -float(i)} for i in range(CSI_ELEMENTS_PER_FRAME)],
    }


def _valid_outputs(root: Path, requested: int, received: int) -> None:
    rx = root / "rx_wifi"
    tx = root / "tx_wifi"
    rx.mkdir(parents=True)
    tx.mkdir()
    (rx / "features.jsonl").write_text(
        "".join(json.dumps(_row(i)) + "\n" for i in range(received)), encoding="utf-8"
    )
    (rx / "csi.cf32").write_bytes(b"\0" * (received * 52 * 8))
    frame_timings = [
        {
            "schema": "wifi_frame_timing_v1",
            "packet_counter": i,
            "sample_offset": i * 2_048_000,
            "block_first_sample": i * 2_048_000,
            "block_sample_count": 2_048_000,
            "host_received_steady_ns": 1_000,
            "processing_started_steady_ns": 2_000,
            "json_finished_steady_ns": 3_000,
            "csi_finished_steady_ns": 4_000,
            "queue_wait_us": 10,
            "block_processing_us": 20,
            "json_write_us": 3,
            "csi_write_us": 2,
            "output_total_us": 5,
            "block_received_to_json_us": 33,
            "block_received_to_csi_us": 35,
            "packet_duration_us": 1_000,
            "packet_start_to_json_us": 1033,
            "packet_start_to_csi_us": 1035,
            "packet_end_to_json_us": 33,
            "packet_end_to_csi_us": 35,
            "radio_time_semantics": (
                "estimated_from_block_end_host_delivery_and_sample_"
                "offset_includes_usb_host_delivery_uncertainty"
            ),
        }
        for i in range(received)
    ]
    (rx / "frame-timings.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in frame_timings), encoding="utf-8"
    )
    (rx / "block-timings.jsonl").write_text(
        json.dumps(
            {
                "schema": "wifi_block_timing_v1",
                "first_sample": 0,
                "sample_count": 2_048_000,
                "host_received_steady_ns": 1_000,
                "queue_wait_us": 10,
                "processing_us": 20,
                "block_total_us": 25,
                "candidates": received,
                "synchronized": received,
                "decoded": received,
                "frames": received,
                "queue_depth_after": 0,
                "overflow": False,
                "discontinuity": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (rx / "process.log").write_text(
        f"Overflows : 0\nTimeouts : 0\nDiscontinuidades : 0\nGuardados JSONL : {received}\n",
        encoding="utf-8",
    )
    (tx / "process.log").write_text(
        f"Beacons incluidos : {requested}\nZero sends : 0\n", encoding="utf-8"
    )


@pytest.mark.parametrize(("requested", "minimum"), [(1, 1), (50, 40), (500, 400), (600, 480)])
def test_wifi_smoke_math_and_limits(requested, minimum):
    assert required_frames(requested) == minimum
    assert tx_buffer_bytes(requested) == requested * 2_048_000 * 8
    assert required_available_memory_bytes(requested) == tx_buffer_bytes(requested) + MEMORY_MARGIN_BYTES
    assert global_timeout_s(requested) == 330 + requested * 0.1024
    profile = load_profile(REPO_ROOT / "profiles" / "wifi_link_smoke.yaml")
    assert resolve_parameters(profile, {"label": "x", "num_beacons": str(requested)})["num_beacons"] == requested


def test_wifi_smoke_parameter_range_and_meminfo():
    profile = load_profile(REPO_ROOT / "profiles" / "wifi_link_smoke.yaml")
    for invalid in (0, 601):
        with pytest.raises(ValidationFailure):
            resolve_parameters(profile, {"label": "x", "num_beacons": str(invalid)})
    assert available_memory_bytes("MemTotal: 9 kB\nMemAvailable: 123 kB\n") == 123 * 1024
    with pytest.raises(ProcessFailure):
        available_memory_bytes("MemTotal: 9 kB\n")


def test_wifi_smoke_output_closure(tmp_path):
    _valid_outputs(tmp_path, 50, 40)
    result = validate_wifi_smoke_outputs(tmp_path, 50)
    assert result["frames_received"] == 40
    assert result["frames_required"] == 40
    assert result["receive_ratio"] == 0.8
    assert result["frame_timing_rows"] == 40
    assert result["timings"]["packet_end_to_json"]["p95_us"] == 33


@pytest.mark.parametrize(
    "corruption",
    [
        "below_threshold",
        "truncated",
        "cf32",
        "counter",
        "features",
        "sample_offset",
        "frame_timing_count",
        "frame_timing_counter",
        "frame_timing_semantics",
        "frame_timing_value",
        "frame_timing_consistency",
        "block_timing",
        "block_timing_schema",
        "block_timing_flags",
        "block_timing_consistency",
        "overflow",
        "zero_send",
    ],
)
def test_wifi_smoke_rejects_corruption(tmp_path, corruption):
    received = 39 if corruption == "below_threshold" else 40
    _valid_outputs(tmp_path, 50, received)
    if corruption == "truncated":
        path = tmp_path / "rx_wifi" / "features.jsonl"
        path.write_bytes(path.read_bytes()[:-1])
    elif corruption == "cf32":
        (tmp_path / "rx_wifi" / "csi.cf32").write_bytes(b"bad")
    elif corruption == "counter":
        path = tmp_path / "rx_wifi" / "features.jsonl"
        rows = path.read_text().splitlines()
        rows[1] = json.dumps(_row(0))
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "features":
        path = tmp_path / "rx_wifi" / "features.jsonl"
        rows = path.read_text().splitlines()
        value = json.loads(rows[0])
        value["complex_features"] = value["complex_features"][:-1]
        rows[0] = json.dumps(value)
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "sample_offset":
        path = tmp_path / "rx_wifi" / "features.jsonl"
        rows = path.read_text().splitlines()
        value = json.loads(rows[0])
        value["sample_offset"] = -1
        rows[0] = json.dumps(value)
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "frame_timing_count":
        path = tmp_path / "rx_wifi" / "frame-timings.jsonl"
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    elif corruption == "frame_timing_counter":
        path = tmp_path / "rx_wifi" / "frame-timings.jsonl"
        rows = path.read_text().splitlines()
        value = json.loads(rows[0])
        value["packet_counter"] = 999
        rows[0] = json.dumps(value)
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "frame_timing_semantics":
        path = tmp_path / "rx_wifi" / "frame-timings.jsonl"
        rows = path.read_text().splitlines()
        value = json.loads(rows[0])
        value["radio_time_semantics"] = "rf_timestamp"
        rows[0] = json.dumps(value)
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "frame_timing_value":
        path = tmp_path / "rx_wifi" / "frame-timings.jsonl"
        rows = path.read_text().splitlines()
        value = json.loads(rows[0])
        value["queue_wait_us"] = True
        rows[0] = json.dumps(value)
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "frame_timing_consistency":
        path = tmp_path / "rx_wifi" / "frame-timings.jsonl"
        rows = path.read_text().splitlines()
        value = json.loads(rows[0])
        value["output_total_us"] = 0
        rows[0] = json.dumps(value)
        path.write_text("\n".join(rows) + "\n")
    elif corruption == "block_timing":
        (tmp_path / "rx_wifi" / "block-timings.jsonl").write_text("")
    elif corruption == "block_timing_schema":
        path = tmp_path / "rx_wifi" / "block-timings.jsonl"
        value = json.loads(path.read_text())
        value["schema"] = "wrong"
        path.write_text(json.dumps(value) + "\n")
    elif corruption == "block_timing_flags":
        path = tmp_path / "rx_wifi" / "block-timings.jsonl"
        value = json.loads(path.read_text())
        value["overflow"] = 0
        path.write_text(json.dumps(value) + "\n")
    elif corruption == "block_timing_consistency":
        path = tmp_path / "rx_wifi" / "block-timings.jsonl"
        value = json.loads(path.read_text())
        value["block_total_us"] = 0
        path.write_text(json.dumps(value) + "\n")
    elif corruption == "overflow":
        path = tmp_path / "rx_wifi" / "process.log"
        path.write_text(path.read_text().replace("Overflows : 0", "Overflows : 1"))
    elif corruption == "zero_send":
        path = tmp_path / "tx_wifi" / "process.log"
        path.write_text(path.read_text().replace("Zero sends : 0", "Zero sends : 1"))
    with pytest.raises(PublicationFailure):
        validate_wifi_smoke_outputs(tmp_path, 50)


def test_wifi_hardware_preflight_contract_is_fully_mockable(tmp_path, monkeypatch):
    profile = load_profile(REPO_ROOT / "profiles" / "wifi_link_smoke.yaml")
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.wifi-smoke.example.yaml")
    parameters = resolve_parameters(
        profile, {"label": "mock", "num_beacons": "50", "detector_threshold": "0.8"}
    )
    run_dir = tmp_path / "runs" / "run_mock"
    (run_dir / "rx_wifi" / "runtime").mkdir(parents=True)
    (run_dir / "tx_wifi").mkdir()
    plan = build_plan(inventory, profile, parameters, run_id="run_mock", run_dir=run_dir)
    _prepare_wifi_config(plan, REPO_ROOT)
    effective = json.loads((run_dir / "rx_wifi" / "runtime" / "effective-config.json").read_text())
    assert effective["waveform_config"]["detector"]["metric_threshold"] == 0.8
    assert effective["output"]["feature_path"].endswith("/rx_wifi/features.jsonl")
    assert effective["output"]["csi_raw_path"].endswith("/rx_wifi/csi.cf32")
    assert effective["output"]["frame_timing_path"].endswith("/rx_wifi/frame-timings.jsonl")
    assert effective["output"]["block_timing_path"].endswith("/rx_wifi/block-timings.jsonl")
    calls = []

    def fake_ssh(config, argv, **kwargs):
        calls.append((config["host"], argv))
        if argv[:2] == ["cat", "/proc/meminfo"]:
            return subprocess.CompletedProcess(argv, 0, "MemAvailable: 30000000 kB\n", "")
        if argv[:1] == ["uhd_find_devices"]:
            return subprocess.CompletedProcess(argv, 0, f"serial: {argv[-1].split('=', 1)[1]}\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", fake_ssh)
    _hardware_preflight(plan)
    assert any(argv[:2] == ["python3", "-c"] for _, argv in calls)
    assert sum(argv[:1] == ["uhd_find_devices"] for _, argv in calls) == 2

    def low_memory(config, argv, **kwargs):
        if argv[:2] == ["cat", "/proc/meminfo"]:
            return subprocess.CompletedProcess(argv, 0, "MemAvailable: 1 kB\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", low_memory)
    with pytest.raises(ProcessFailure, match="insufficient"):
        _hardware_preflight(plan)
