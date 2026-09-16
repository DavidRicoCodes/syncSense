from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

from sync_framework.domain import PublicationFailure
from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.domain import ProcessFailure
from sync_framework.orchestration import _wifi_hardware_preflight
from sync_framework.planning import build_plan
from sync_framework.validation import load_schema
from sync_framework.wifi_bf_like import (
    CSI_ELEMENTS_PER_FRAME,
    FEATURE_ROW_SCHEMA_REF,
    MEMORY_MARGIN_BYTES,
    global_timeout_s,
    required_stream_memory_bytes,
    validate_bf_like_rx_outputs,
    validate_bf_like_tx_outputs,
)


def feature_row(counter: int = 0, *, experiment_id: int = 7) -> dict:
    return {
        "protocol_version": 1,
        "waveform_type": 2,
        "profile_id": 3,
        "transmitter_id": 1,
        "experiment_id": experiment_id,
        "schema": "alb_he_ndp_like_sounding_v1",
        "waveform_type_name": "he_ndp_like",
        "profile_name": "alb_he_ndp_like_siso_40mhz_v1",
        "session_id": 1,
        "receiver_group_id": 0,
        "tx_timestamp_ns": 0,
        "scheduled_tx_time_ns": 0,
        "feature_name": "he_ltf_csi",
        "feature_dtype": "complex64",
        "feature_shape": [8, 242],
        "feature_flatten_order": "C",
        "feature_count": 1936,
        "valid": True,
        "error": "",
        "packet_counter": counter,
        "sample_offset": 1100 + counter * 2000,
        "has_rx_device_time": True,
        "rx_block_start_ticks": 1000 + counter * 2000,
        "rx_timestamp_ticks": 1100 + counter * 2000,
        "rx_tick_rate_hz": 40_000_000,
        "rx_timestamp_ns": 27_500 + counter * 50_000,
        "sample_rate_hz": 40_000_000.0,
        "center_frequency_hz": 2_462_000_000.0,
        "snr_db": 20.0,
        "cfo_hz": 10.0,
        "power_dbfs": -20.0,
        "numeric_metadata": {
            "num_he_ltf": 8.0,
            "he_ltf_tones": 242.0,
            "alb_control_bytes": 27.0,
            "alb_codeword_repetitions": 2.0,
            "packet_duration_samples": 5440.0,
            "fcs_valid": 1.0,
            "preamble_metric": 0.9,
            "lltf_timing_metric": 0.8,
            "he_ltf_repetition_metric": 0.98,
            "coarse_cfo_hz": 9.0,
            "fine_cfo_hz": 1.0,
        },
        "text_metadata": {
            "magic": "ALBF",
            "training_family": "he_ltf_2x_40mhz",
        },
        "complex_features": [
            {"real": 0.25, "imag": -0.25}
            for _ in range(CSI_ELEMENTS_PER_FRAME)
        ],
        "real_features": [],
        "payload": [0] * 27,
    }


def frame_timing(row: dict) -> dict:
    block_first = row["sample_offset"] - 100
    value = {
        "schema": "waveform_frame_timing_v1",
        "waveform_type": "he_ndp_like",
        "profile_name": row["profile_name"],
        "packet_counter": row["packet_counter"],
        "sample_offset": row["sample_offset"],
        "block_first_sample": block_first,
        "block_sample_count": 200_000,
        "has_rx_device_time": True,
        "block_start_device_ticks": row["rx_block_start_ticks"],
        "detector_offset_samples": 100,
        "event_device_ticks": row["rx_timestamp_ticks"],
        "device_tick_rate_hz": row["rx_tick_rate_hz"],
        "reference_point": "waveform_start",
        "canonical_timestamp_semantics": "local_usrp_device_time_first_sample_plus_detector_offset_samples",
        "event_monotonic_semantics": "operational_host_estimate_only_not_acquisition_time",
        "radio_time_semantics": "host_operational_estimate_only_includes_usb_delivery_uncertainty_not_acquisition_alignment",
    }
    for name in (
        "host_received_steady_ns", "processing_started_steady_ns",
        "json_finished_steady_ns", "csi_finished_steady_ns", "queue_wait_us",
        "block_processing_us", "json_write_us", "csi_write_us",
        "output_total_us", "block_received_to_json_us",
        "block_received_to_csi_us", "packet_duration_us",
        "packet_start_to_json_us", "packet_start_to_csi_us",
        "event_monotonic_ns", "packet_end_to_json_us", "packet_end_to_csi_us",
    ):
        value[name] = 1
    return value


def block_timing() -> dict:
    return {
        "schema": "waveform_block_timing_v1",
        "first_sample": 1000,
        "sample_count": 200_000,
        "has_rx_device_time": True,
        "block_start_device_ticks": 1000,
        "device_tick_rate_hz": 40_000_000,
        "host_received_steady_ns": 1,
        "queue_wait_us": 1,
        "processing_us": 1,
        "block_total_us": 1,
        "candidates": 1,
        "synchronized": 1,
        "decoded": 1,
        "frames": 1,
        "queue_depth_after": 0,
        "overflow": False,
        "discontinuity": False,
    }


def write_rx(root: Path, rows: list[dict] | None = None) -> None:
    rows = rows or [feature_row()]
    rx = root / "rx_wifi"
    rx.mkdir(parents=True)
    (rx / "features.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (rx / "frame-timings.jsonl").write_text(
        "".join(json.dumps(frame_timing(row)) + "\n" for row in rows),
        encoding="utf-8",
    )
    (rx / "block-timings.jsonl").write_text(
        json.dumps(block_timing()) + "\n", encoding="utf-8"
    )
    (rx / "csi.cf32").write_bytes(
        b"\0" * len(rows) * CSI_ELEMENTS_PER_FRAME * 8
    )
    (rx / "process.log").write_text(
        "Overflows : 0\nTimeouts : 0\nDiscontinuidades : 0\n"
        f"Guardados JSONL : {len(rows)}\n",
        encoding="utf-8",
    )


def write_tx(root: Path, *, experiment_id: int = 7) -> None:
    tx = root / "tx_wifi"
    tx.mkdir(parents=True)
    state = {
        "schema_version": "wifi_tx_v2",
        "role": "tx",
        "mode": "he_ndp_like",
        "status": "stopped",
        "valid": True,
        "error": None,
        "tx_strategy": "timed",
        "num_packets_requested": 1,
        "sent_packets": 1,
        "total_zero_sends": 0,
        "period_ms": 100.0,
        "gain_db": 65.0,
        "sample_rate_hz": 40_000_000.0,
        "hardware_rate_hz": 40_960_000.0,
        "bandwidth_hz": 40_000_000.0,
        "async_event_counts": {"burst_ack": 1},
        "packet_builder": {
            "mode": "he_ndp_like",
            "profile": "alb_he_ndp_like_siso_40mhz_v1",
            "num_he_ltf": 8,
            "he_ltf_csi_shape": [8, 242],
            "packet_samples": 5440,
            "sample_rate_hz": 40_000_000.0,
            "strict_ieee_ndp": False,
            "experiment_id": experiment_id,
        },
    }
    (tx / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tx / "process.log").write_text("amplitude peak target: 0.800\n")


def validate_rx(root: Path, **overrides):
    values = {
        "num_packets": 1,
        "minimum_ratio": 0.8,
        "period_ms": 100.0,
        "tx_gain_db": 65.0,
        "experiment_id": 7,
    }
    values.update(overrides)
    return validate_bf_like_rx_outputs(root, **values)


def test_bf_resource_math():
    assert global_timeout_s(20, 100, max_drain_s=10) == 132
    assert required_stream_memory_bytes(1, 100) == (
        MEMORY_MARGIN_BYTES + 5_571 * 8
    )
    assert required_stream_memory_bytes(1000, 100, strategy="streamed") == (
        MEMORY_MARGIN_BYTES + 80 * 4_096_000 * 8
    )
    with pytest.raises(ValueError):
        required_stream_memory_bytes(0, 100)
    with pytest.raises(ValueError):
        required_stream_memory_bytes(1, 100, prefetch_batches=-1)
    with pytest.raises(ValueError):
        required_stream_memory_bytes(1, 100, strategy="continuous")


def test_bf_schema_is_versioned_and_closed():
    schema = load_schema("wifi-bf-like-feature-row")
    assert schema["$id"] == FEATURE_ROW_SCHEMA_REF
    jsonschema.Draft202012Validator(schema).validate(feature_row())
    invalid = feature_row()
    invalid["unexpected"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(invalid)


def test_bf_rx_and_tx_happy_path(tmp_path):
    write_rx(tmp_path)
    write_tx(tmp_path)
    summary = validate_rx(tmp_path)
    assert summary["frames_received"] == 1
    assert summary["feature_shape"] == [8, 242]
    assert validate_bf_like_tx_outputs(
        tmp_path,
        num_packets=1,
        period_ms=100,
        tx_gain_db=65,
        tx_amplitude=0.8,
        experiment_id=7,
    )["tx_strategy"] == "timed"


@pytest.mark.parametrize(
    "corrupt",
    [
        "nonfinite_feature",
        "nonfinite_scalar",
        "nonfinite_metadata",
        "experiment",
        "metadata",
        "device_ticks",
        "device_before_block",
        "counter",
        "frame_ticks",
        "frame_count",
        "block_overflow",
        "no_block_timings",
        "csi",
        "fatal_log",
        "truncated",
    ],
)
def test_bf_rx_rejects_corruption(tmp_path, corrupt):
    row = feature_row()
    write_rx(tmp_path, [row])
    rx = tmp_path / "rx_wifi"
    if corrupt == "nonfinite_feature":
        row["complex_features"][0]["real"] = float("nan")
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "nonfinite_scalar":
        row["snr_db"] = float("nan")
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "nonfinite_metadata":
        row["numeric_metadata"]["preamble_metric"] = float("nan")
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "experiment":
        row["experiment_id"] = 8
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "metadata":
        row["numeric_metadata"]["fcs_valid"] = 0.0
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "device_ticks":
        row["rx_tick_rate_hz"] = 1
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "device_before_block":
        row["rx_timestamp_ticks"] = row["rx_block_start_ticks"] - 1
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "counter":
        row["packet_counter"] = 1
        (rx / "features.jsonl").write_text(json.dumps(row) + "\n")
    elif corrupt == "frame_ticks":
        timing = frame_timing(row)
        timing["event_device_ticks"] += 1
        (rx / "frame-timings.jsonl").write_text(json.dumps(timing) + "\n")
    elif corrupt == "frame_count":
        (rx / "frame-timings.jsonl").write_text("")
    elif corrupt == "block_overflow":
        timing = block_timing()
        timing["overflow"] = True
        (rx / "block-timings.jsonl").write_text(json.dumps(timing) + "\n")
    elif corrupt == "no_block_timings":
        (rx / "block-timings.jsonl").write_text("")
    elif corrupt == "csi":
        (rx / "csi.cf32").write_bytes(b"bad")
    elif corrupt == "fatal_log":
        (rx / "process.log").write_text("UHD RX error\n")
    elif corrupt == "truncated":
        (rx / "features.jsonl").write_text(json.dumps(row))
    with pytest.raises(PublicationFailure):
        validate_rx(tmp_path)


def test_bf_rx_rejects_ratio_and_order(tmp_path):
    rows = [feature_row(1), feature_row(0)]
    write_rx(tmp_path, rows)
    with pytest.raises(PublicationFailure):
        validate_rx(tmp_path, num_packets=2, minimum_ratio=0.5)
    other = tmp_path / "ratio"
    write_rx(other)
    with pytest.raises(PublicationFailure):
        validate_rx(other, num_packets=2, minimum_ratio=0.8)


def test_bf_rx_accepts_only_pre_frame_startup_overflow(tmp_path):
    row = feature_row()
    row["sample_offset"] = 1_000_000
    row["rx_block_start_ticks"] = 999_900
    row["rx_timestamp_ticks"] = 1_000_000
    write_rx(tmp_path, [row])
    rx = tmp_path / "rx_wifi"
    startup = block_timing()
    startup.update(
        first_sample=0,
        sample_count=400_000,
        block_start_device_ticks=1,
        overflow=True,
        discontinuity=True,
        frames=0,
        decoded=0,
    )
    (rx / "block-timings.jsonl").write_text(json.dumps(startup) + "\n")
    (rx / "process.log").write_text(
        "Overflows : 1\nTimeouts : 0\nDiscontinuidades : 1\n"
        "Guardados JSONL : 1\n"
    )
    summary = validate_rx(tmp_path)
    assert summary["startup_overflows"] == 1
    assert summary["startup_discontinuities"] == 1


@pytest.mark.parametrize("payload", [None, "{bad}\n", "[]\n"])
def test_bf_rx_rejects_missing_or_malformed_jsonl(tmp_path, payload):
    if payload is not None:
        rx = tmp_path / "rx_wifi"
        rx.mkdir(parents=True)
        (rx / "features.jsonl").write_text(payload)
    with pytest.raises(PublicationFailure):
        validate_rx(tmp_path)


def test_bf_tx_rejects_missing_state(tmp_path):
    with pytest.raises(PublicationFailure):
        validate_bf_like_tx_outputs(
            tmp_path,
            num_packets=1,
            period_ms=100,
            tx_gain_db=65,
            tx_amplitude=0.8,
            experiment_id=7,
        )


def test_bf_tx_rejects_unproven_amplitude(tmp_path):
    write_tx(tmp_path)
    (tmp_path / "tx_wifi" / "process.log").write_text(
        "amplitude peak target: 0.500\n"
    )
    with pytest.raises(PublicationFailure, match="amplitude"):
        validate_bf_like_tx_outputs(
            tmp_path,
            num_packets=1,
            period_ms=100,
            tx_gain_db=65,
            tx_amplitude=0.8,
            experiment_id=7,
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("status",), "transmitting"),
        (("period_ms",), 90.0),
        (("packet_builder", "profile"), "wrong"),
        (("async_event_counts",), []),
        (("async_event_counts", "underflow"), 1),
        (("sent_packets",), "1"),
    ],
)
def test_bf_tx_rejects_invalid_state(tmp_path, path, value):
    write_tx(tmp_path)
    state_path = tmp_path / "tx_wifi" / "state.json"
    state = json.loads(state_path.read_text())
    target = state
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    state_path.write_text(json.dumps(state))
    with pytest.raises(PublicationFailure):
        validate_bf_like_tx_outputs(
            tmp_path,
            num_packets=1,
            period_ms=100,
            tx_gain_db=65,
            tx_amplitude=0.8,
            experiment_id=7,
        )


def test_bf_hardware_preflight_checks_help_binary_memory_and_context(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    inventory = load_inventory(root / "config" / "inventory.wifi-bf-like.example.yaml")
    profile = load_profile(root / "profiles" / "wifi_bf_like.yaml")
    parameters = resolve_parameters(
        profile, {"label": "preflight", "num_packets": "20"}
    )
    plan = build_plan(inventory, profile, parameters, run_id="run_test")
    monkeypatch.setattr("sync_framework.orchestration._wifi_rx_serial", lambda plan: "RX123")
    calls = []

    def fake_run(ssh, argv, **kwargs):
        calls.append((argv, kwargs))
        if "--help" in argv:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout="--period-ms --num-packets --experiment-id "
                "--session-id --hardware-rate --tx-strategy",
                stderr="",
            )
        if argv[:2] == ["cat", "/proc/meminfo"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="MemAvailable: 33554432 kB\n", stderr=""
            )
        if argv[0] == "strings":
            return subprocess.CompletedProcess(
                argv, 0, stdout="alb_he_ndp_like_sounding_v1\n", stderr=""
            )
        if argv[0] == "uhd_find_devices":
            output = "N310 RX123"
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", fake_run)
    _wifi_hardware_preflight(plan)
    contextual = [kwargs for argv, kwargs in calls if "--help" in argv]
    assert contextual[0]["cwd"] == plan.processes["tx_wifi"].cwd
    assert contextual[0]["env"] == plan.processes["tx_wifi"].env
    assert any(argv[:2] == ["cat", "/proc/meminfo"] for argv, _ in calls)
    assert any(argv[0] == "strings" for argv, _ in calls)


def test_bf_hardware_preflight_rejects_insufficient_memory(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    inventory = load_inventory(root / "config" / "inventory.wifi-bf-like.example.yaml")
    profile = load_profile(root / "profiles" / "wifi_bf_like.yaml")
    plan = build_plan(
        inventory,
        profile,
        resolve_parameters(profile, {"label": "x", "num_packets": "20"}),
        run_id="run_test",
    )

    def fake_run(_ssh, argv, **_kwargs):
        if "--help" in argv:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout="--period-ms --num-packets --experiment-id "
                "--session-id --hardware-rate --tx-strategy",
                stderr="",
            )
        if argv[:2] == ["cat", "/proc/meminfo"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="MemAvailable: 1 kB\n", stderr=""
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", fake_run)
    with pytest.raises(ProcessFailure, match="insufficient available memory"):
        _wifi_hardware_preflight(plan)
