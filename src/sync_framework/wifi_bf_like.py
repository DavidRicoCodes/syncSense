"""Validation and resource rules for the WiFi BF-like Golay52 smoke."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import jsonschema

from .domain import PublicationFailure
from .validation import load_schema


NUM_BF_LTF = 8
NUM_ACTIVE_SUBCARRIERS = 52
CSI_ELEMENTS_PER_FRAME = NUM_BF_LTF * NUM_ACTIVE_SUBCARRIERS
COMPLEX64_BYTES = 8
HARDWARE_RATE_HZ = 20_480_000
DEFAULT_STREAM_BATCH_PACKETS = 20
DEFAULT_STREAM_PREFETCH_BATCHES = 3
MEMORY_MARGIN_BYTES = 2 * 1024**3

EXPECTED_SCHEMA = "alb_bf_like_golay52_sounding_v2"
EXPECTED_PROFILE = "alb_bf_like_golay52_siso_20mhz_v2"
EXPECTED_TRAINING_SEQUENCE_ID = 0x5201
FEATURE_ROW_SCHEMA_REF = "urn:sync:schema:v1:wifi-bf-like-feature-row"


def global_timeout_s(
    num_packets: int,
    period_ms: float,
    *,
    max_drain_s: float = 10.0,
) -> float:
    """Conservative wall-clock deadline for generation, TX and RX drain."""
    return 120.0 + num_packets * period_ms / 1000.0 + max_drain_s


def required_stream_memory_bytes(
    num_packets: int,
    period_ms: float,
    *,
    batch_packets: int = DEFAULT_STREAM_BATCH_PACKETS,
    prefetch_batches: int = DEFAULT_STREAM_PREFETCH_BATCHES,
) -> int:
    """Upper bound for queued streamed complex64 samples plus OS headroom."""
    if num_packets <= 0 or period_ms <= 0 or batch_packets <= 0:
        raise ValueError("BF stream sizing inputs must be positive")
    if prefetch_batches < 0:
        raise ValueError("BF stream prefetch count cannot be negative")
    samples_per_period = round(period_ms * HARDWARE_RATE_HZ / 1000.0)
    resident_packets = min(
        num_packets,
        batch_packets * (prefetch_batches + 1),
    )
    return (
        resident_packets * samples_per_period * COMPLEX64_BYTES
        + MEMORY_MARGIN_BYTES
    )


def _read_jsonl(path: Path, description: str) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicationFailure(f"Cannot read {description}: {path}") from exc
    if raw and not raw.endswith(b"\n"):
        raise PublicationFailure(f"{description} has a truncated final line")
    rows: list[dict[str, Any]] = []
    for number, encoded in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationFailure(
                f"Invalid {description} JSON at row {number}"
            ) from exc
        if not isinstance(value, dict):
            raise PublicationFailure(f"Invalid {description} row {number}")
        rows.append(value)
    return rows


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _exact_int(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _require_zero_summary(log: str, label: str) -> None:
    match = re.search(
        rf"^{re.escape(label)}\s*:\s*(\d+)\s*$",
        log,
        re.MULTILINE | re.IGNORECASE,
    )
    if match is None or int(match.group(1)) != 0:
        raise PublicationFailure(
            f"BF RX summary does not prove zero {label.lower()}"
        )


def _validate_feature_rows(
    rows: list[dict[str, Any]],
    *,
    num_packets: int,
    period_ms: float,
    tx_gain_db: float,
    experiment_id: int,
) -> list[int]:
    validator = jsonschema.Draft202012Validator(
        load_schema("wifi-bf-like-feature-row")
    )
    counters: list[int] = []
    previous_ticks: int | None = None
    finite_scalars = (
        "sample_rate_hz", "center_frequency_hz", "snr_db", "cfo_hz",
        "power_dbfs",
    )
    finite_metadata = (
        "stf_metric", "preamble_metric", "coarse_cfo_hz", "fine_cfo_hz",
        "bf_ltf_signal_power", "bf_ltf_noise_power",
    )
    for number, row in enumerate(rows, 1):
        errors = sorted(
            validator.iter_errors(row), key=lambda error: list(error.absolute_path)
        )
        if errors:
            location = ".".join(str(part) for part in errors[0].absolute_path)
            raise PublicationFailure(
                f"Invalid BF-like row contract at row {number}"
                + (f" ({location})" if location else "")
            )
        if any(not _finite_number(row[name]) for name in finite_scalars):
            raise PublicationFailure(f"Non-finite BF-like scalar at row {number}")
        for feature in row["complex_features"]:
            if not _finite_number(feature["real"]) or not _finite_number(feature["imag"]):
                raise PublicationFailure(
                    f"Non-finite BF-like complex feature at row {number}"
                )
        numeric = row["numeric_metadata"]
        if any(not _finite_number(numeric[name]) for name in finite_metadata):
            raise PublicationFailure(f"Non-finite BF-like metadata at row {number}")
        expected_numeric = {
            "num_bf_ltf": float(NUM_BF_LTF),
            "training_sequence_id": float(EXPECTED_TRAINING_SEQUENCE_ID),
            "golay_pair_length": 52.0,
            "golay_pair_repetitions": 4.0,
            "bf_ltf_common_phase_aligned": 1.0,
            "header_crc_valid": 1.0,
            "frame_fcs_valid": 1.0,
            "frame_period_us": float(period_ms * 1000.0),
            "tx_gain_db": float(tx_gain_db),
        }
        if any(numeric[name] != value for name, value in expected_numeric.items()):
            raise PublicationFailure(f"Invalid BF-like metadata at row {number}")
        if row["experiment_id"] != experiment_id:
            raise PublicationFailure(f"BF-like experiment_id mismatch at row {number}")
        if row["rx_tick_rate_hz"] != round(float(row["sample_rate_hz"])):
            raise PublicationFailure(f"BF-like device tick rate mismatch at row {number}")
        detector_offset = row["rx_timestamp_ticks"] - row["rx_block_start_ticks"]
        if detector_offset < 0:
            raise PublicationFailure(f"BF-like device timestamp precedes its block at row {number}")
        if previous_ticks is not None and row["rx_timestamp_ticks"] <= previous_ticks:
            raise PublicationFailure("BF-like device timestamps must be strictly increasing")
        previous_ticks = row["rx_timestamp_ticks"]
        counter = row["packet_counter"]
        if counter >= num_packets:
            raise PublicationFailure(f"Invalid BF-like packet_counter at row {number}")
        counters.append(counter)
    if counters != sorted(set(counters)):
        raise PublicationFailure(
            "BF-like packet counters must be unique and strictly increasing"
        )
    return counters


def _validate_frame_timings(
    rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> None:
    if len(rows) != len(feature_rows):
        raise PublicationFailure(
            "BF-like frame timing count does not match feature rows"
        )
    numeric_fields = (
        "host_received_steady_ns", "processing_started_steady_ns",
        "json_finished_steady_ns", "csi_finished_steady_ns", "queue_wait_us",
        "block_processing_us", "json_write_us", "csi_write_us",
        "output_total_us", "block_received_to_json_us",
        "block_received_to_csi_us", "packet_duration_us",
        "packet_start_to_json_us", "packet_start_to_csi_us",
        "event_monotonic_ns", "packet_end_to_json_us", "packet_end_to_csi_us",
    )
    for number, (timing, feature) in enumerate(zip(rows, feature_rows), 1):
        expected = {
            "schema": "waveform_frame_timing_v1",
            "waveform_type": "bf_like",
            "profile_name": EXPECTED_PROFILE,
            "packet_counter": feature["packet_counter"],
            "sample_offset": feature["sample_offset"],
            "has_rx_device_time": True,
            "block_start_device_ticks": feature["rx_block_start_ticks"],
            "event_device_ticks": feature["rx_timestamp_ticks"],
            "device_tick_rate_hz": feature["rx_tick_rate_hz"],
            "reference_point": "waveform_start",
            "canonical_timestamp_semantics": (
                "local_usrp_device_time_first_sample_plus_detector_offset_samples"
            ),
            "event_monotonic_semantics": (
                "operational_host_estimate_only_not_acquisition_time"
            ),
            "radio_time_semantics": (
                "host_operational_estimate_only_includes_usb_delivery_uncertainty_"
                "not_acquisition_alignment"
            ),
        }
        if any(timing.get(key) != value for key, value in expected.items()):
            raise PublicationFailure(f"Invalid BF-like frame timing at row {number}")
        integers = (
            "block_first_sample", "block_sample_count", "detector_offset_samples",
            *numeric_fields,
        )
        if any(
            not isinstance(timing.get(name), int)
            or isinstance(timing.get(name), bool)
            or timing[name] < 0
            for name in integers
        ):
            raise PublicationFailure(f"Invalid BF-like timing scalar at row {number}")
        if timing["sample_offset"] - timing["block_first_sample"] != timing["detector_offset_samples"]:
            raise PublicationFailure(f"BF-like detector offset mismatch at row {number}")
        if timing["block_start_device_ticks"] + timing["detector_offset_samples"] != timing["event_device_ticks"]:
            raise PublicationFailure(f"BF-like canonical timestamp mismatch at row {number}")


def _validate_block_timings(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise PublicationFailure("BF-like block timing JSONL must contain at least one row")
    previous_first: int | None = None
    previous_ticks: int | None = None
    for number, row in enumerate(rows, 1):
        if (
            row.get("schema") != "waveform_block_timing_v1"
            or row.get("has_rx_device_time") is not True
            or row.get("overflow") is not False
            or row.get("discontinuity") is not False
        ):
            raise PublicationFailure(f"Invalid BF-like block timing at row {number}")
        required_nonnegative = (
            "first_sample", "sample_count", "block_start_device_ticks",
            "device_tick_rate_hz", "host_received_steady_ns", "queue_wait_us",
            "processing_us", "block_total_us", "candidates", "synchronized",
            "decoded", "frames", "queue_depth_after",
        )
        if any(
            not isinstance(row.get(name), int)
            or isinstance(row.get(name), bool)
            or row[name] < 0
            for name in required_nonnegative
        ) or row["sample_count"] == 0 or row["device_tick_rate_hz"] == 0:
            raise PublicationFailure(f"Invalid BF-like block scalar at row {number}")
        if previous_first is not None and row["first_sample"] <= previous_first:
            raise PublicationFailure("BF-like block sample indices must increase")
        if previous_ticks is not None and row["block_start_device_ticks"] <= previous_ticks:
            raise PublicationFailure("BF-like block device ticks must increase")
        previous_first = row["first_sample"]
        previous_ticks = row["block_start_device_ticks"]


def validate_bf_like_rx_outputs(
    run_dir: Path,
    *,
    num_packets: int,
    minimum_ratio: float,
    period_ms: float,
    tx_gain_db: float,
    experiment_id: int,
) -> dict[str, Any]:
    rx_dir = run_dir / "rx_wifi"
    rows = _read_jsonl(rx_dir / "features.jsonl", "WiFi BF-like feature JSONL")
    frame_timings = _read_jsonl(
        rx_dir / "frame-timings.jsonl", "WiFi BF-like frame timing JSONL"
    )
    block_timings = _read_jsonl(
        rx_dir / "block-timings.jsonl", "WiFi BF-like block timing JSONL"
    )
    _validate_feature_rows(
        rows,
        num_packets=num_packets,
        period_ms=period_ms,
        tx_gain_db=tx_gain_db,
        experiment_id=experiment_id,
    )
    _validate_frame_timings(frame_timings, rows)
    _validate_block_timings(block_timings)
    required = math.ceil(num_packets * minimum_ratio)
    if len(rows) < required:
        raise PublicationFailure(
            f"BF-like reception below required ratio: {len(rows)} < {required}"
        )
    csi_path = rx_dir / "csi.cf32"
    expected_size = len(rows) * CSI_ELEMENTS_PER_FRAME * COMPLEX64_BYTES
    if not csi_path.is_file() or csi_path.stat().st_size != expected_size:
        raise PublicationFailure("BF-like CF32 size does not match JSONL rows")
    try:
        log = (rx_dir / "process.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        raise PublicationFailure("Cannot read BF-like RX process log") from exc
    if re.search(r"(?:UHD RX error|ERROR hilo UHD|ERROR hilo decoder)", log, re.IGNORECASE):
        raise PublicationFailure("BF-like RX log contains a fatal receive error")
    for label in ("Overflows", "Timeouts", "Discontinuidades"):
        _require_zero_summary(log, label)
    saved = re.search(r"^Guardados JSONL\s*:\s*(\d+)\s*$", log, re.MULTILINE)
    if saved is None or int(saved.group(1)) != len(rows):
        raise PublicationFailure("BF-like RX summary row count does not match JSONL")
    return {
        "packets_requested": num_packets,
        "frames_received": len(rows),
        "frames_required": required,
        "frames_lost": num_packets - len(rows),
        "receive_ratio": len(rows) / num_packets,
        "csi_elements_per_frame": CSI_ELEMENTS_PER_FRAME,
        "feature_shape": [NUM_BF_LTF, NUM_ACTIVE_SUBCARRIERS],
        "training_family": "golay_complementary_52_subcarrier",
    }


def validate_bf_like_tx_outputs(
    run_dir: Path,
    *,
    num_packets: int,
    period_ms: float,
    tx_gain_db: float,
    tx_amplitude: float,
    experiment_id: int,
) -> dict[str, Any]:
    path = run_dir / "tx_wifi" / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationFailure("BF-like TX state is missing or invalid") from exc
    if not isinstance(state, dict):
        raise PublicationFailure("BF-like TX state must be an object")
    builder = state.get("packet_builder")
    async_counts = state.get("async_event_counts")
    if not isinstance(builder, dict) or not isinstance(async_counts, dict):
        raise PublicationFailure("BF-like TX state lacks structured metadata")
    expected_state = {
        "schema_version": "wifi_tx_v2",
        "role": "tx",
        "mode": "bf",
        "status": "stopped",
        "valid": True,
        "error": None,
        "tx_strategy": "streamed",
        "num_packets_requested": num_packets,
        "sent_packets": num_packets,
        "total_zero_sends": 0,
    }
    if any(state.get(key) != value for key, value in expected_state.items()):
        raise PublicationFailure("BF-like TX final state is inconsistent")
    if (
        not _finite_number(state.get("period_ms"))
        or not math.isclose(float(state["period_ms"]), period_ms, abs_tol=1e-6)
        or not _finite_number(state.get("gain_db"))
        or not math.isclose(float(state["gain_db"]), tx_gain_db, abs_tol=1e-6)
    ):
        raise PublicationFailure("BF-like TX period or gain mismatch")
    builder_expected = {
        "mode": "bf",
        "profile": EXPECTED_PROFILE,
        "training_sequence_id": EXPECTED_TRAINING_SEQUENCE_ID,
        "num_bf_ltf": NUM_BF_LTF,
        "experiment_id": experiment_id,
    }
    if any(builder.get(key) != value for key, value in builder_expected.items()):
        raise PublicationFailure("BF-like TX waveform contract mismatch")
    fatal_events: dict[str, int] = {}
    for name, count in async_counts.items():
        if not isinstance(name, str) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise PublicationFailure("BF-like TX async event counters are invalid")
        if count > 0 and "burst_ack" not in name.lower():
            fatal_events[name] = count
    if fatal_events:
        raise PublicationFailure(
            "BF-like TX contains fatal UHD async events: "
            + ", ".join(f"{name}={count}" for name, count in sorted(fatal_events.items()))
        )
    try:
        log = (run_dir / "tx_wifi" / "process.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        raise PublicationFailure("Cannot read BF-like TX process log") from exc
    amplitude_match = re.search(
        r"^\s*amplitude peak target:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        log,
        re.MULTILINE | re.IGNORECASE,
    )
    if (
        amplitude_match is None
        or not math.isclose(
            float(amplitude_match.group(1)), tx_amplitude, abs_tol=5e-4
        )
    ):
        raise PublicationFailure("BF-like TX amplitude is not proven by its log")
    return {
        "sent_packets": num_packets,
        "zero_sends": 0,
        "fatal_async_events": {},
        "tx_strategy": "streamed",
    }
