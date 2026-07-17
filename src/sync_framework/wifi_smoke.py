"""Pure validation and sizing rules for the WiFi hardware integration smoke."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .domain import ProcessFailure, PublicationFailure


SAMPLES_PER_BEACON = 2_048_000
COMPLEX64_BYTES = 8
MEMORY_MARGIN_BYTES = 2 * 1024**3
CSI_ELEMENTS_PER_FRAME = 52


def required_frames(num_beacons: int) -> int:
    return math.ceil(num_beacons * 0.8)


def tx_buffer_bytes(num_beacons: int) -> int:
    return num_beacons * SAMPLES_PER_BEACON * COMPLEX64_BYTES


def required_available_memory_bytes(num_beacons: int) -> int:
    return tx_buffer_bytes(num_beacons) + MEMORY_MARGIN_BYTES


def global_timeout_s(num_beacons: int) -> float:
    return 300.0 + num_beacons * 0.1024 + 30.0


def available_memory_bytes(meminfo: str) -> int:
    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", meminfo, re.MULTILINE)
    if not match:
        raise ProcessFailure("Cannot determine remote MemAvailable")
    return int(match.group(1)) * 1024


def _read_complete_json_lines(path: Path, *, description: str = "WiFi JSONL") -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicationFailure(f"Cannot read {description}: {path}") from exc
    if raw and not raw.endswith(b"\n"):
        raise PublicationFailure(f"{description} has a truncated final line")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicationFailure(f"Invalid {description} JSON at line {line_number}") from exc
        if not isinstance(value, dict):
            raise PublicationFailure(f"{description} row {line_number} is not an object")
        rows.append(value)
    return rows


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _timing_summary(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [row[field] for row in rows]
    return {
        "mean_us": sum(values) / len(values) if values else None,
        "median_us": _percentile(values, 0.5),
        "p95_us": _percentile(values, 0.95),
        "p99_us": _percentile(values, 0.99),
        "max_us": max(values) if values else None,
    }


def _validate_timing_value(row: dict[str, Any], field: str, *, row_number: int, description: str) -> None:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PublicationFailure(f"Invalid {field} in {description} row {row_number}")


def _zero_summary_value(log: str, label: str) -> None:
    match = re.search(rf"^{re.escape(label)}\s*:\s*(\d+)\s*$", log, re.MULTILINE | re.IGNORECASE)
    if not match or int(match.group(1)) != 0:
        raise PublicationFailure(f"RX summary does not prove zero {label.lower()}")


def validate_wifi_smoke_outputs(run_dir: Path, num_beacons: int) -> dict[str, Any]:
    rx_dir = run_dir / "rx_wifi"
    tx_dir = run_dir / "tx_wifi"
    rows = _read_complete_json_lines(rx_dir / "features.jsonl")
    frame_timings = _read_complete_json_lines(
        rx_dir / "frame-timings.jsonl", description="WiFi frame timing JSONL"
    )
    block_timings = _read_complete_json_lines(
        rx_dir / "block-timings.jsonl", description="WiFi block timing JSONL"
    )
    counters: list[int] = []
    for index, row in enumerate(rows, 1):
        counter = row.get("packet_counter")
        sample_offset = row.get("sample_offset")
        features = row.get("complex_features")
        if not isinstance(counter, int) or not 0 <= counter < num_beacons:
            raise PublicationFailure(f"Invalid packet_counter in WiFi row {index}")
        if not isinstance(sample_offset, int) or isinstance(sample_offset, bool) or sample_offset < 0:
            raise PublicationFailure(f"Invalid sample_offset in WiFi row {index}")
        if not isinstance(features, list) or len(features) != CSI_ELEMENTS_PER_FRAME:
            raise PublicationFailure(f"WiFi row {index} must contain 52 complex features")
        for feature in features:
            if (
                not isinstance(feature, dict)
                or not isinstance(feature.get("real"), (int, float))
                or not isinstance(feature.get("imag"), (int, float))
            ):
                raise PublicationFailure(f"Invalid complex feature in WiFi row {index}")
        counters.append(counter)
    if len(frame_timings) != len(rows):
        raise PublicationFailure("WiFi frame timing row count does not match feature rows")
    frame_timing_fields = (
        "sample_offset",
        "block_first_sample",
        "block_sample_count",
        "host_received_steady_ns",
        "processing_started_steady_ns",
        "json_finished_steady_ns",
        "csi_finished_steady_ns",
        "queue_wait_us",
        "block_processing_us",
        "json_write_us",
        "csi_write_us",
        "output_total_us",
        "block_received_to_json_us",
        "block_received_to_csi_us",
        "packet_duration_us",
        "packet_start_to_json_us",
        "packet_start_to_csi_us",
        "packet_end_to_json_us",
        "packet_end_to_csi_us",
    )
    for index, (feature, timing) in enumerate(zip(rows, frame_timings, strict=True), 1):
        if timing.get("schema") != "wifi_frame_timing_v1":
            raise PublicationFailure(f"Invalid frame timing schema in row {index}")
        if timing.get("packet_counter") != feature.get("packet_counter"):
            raise PublicationFailure(f"Frame timing counter mismatch in row {index}")
        if timing.get("sample_offset") != feature.get("sample_offset"):
            raise PublicationFailure(f"Frame timing sample offset mismatch in row {index}")
        if timing.get("radio_time_semantics") != (
            "estimated_from_block_end_host_delivery_and_sample_"
            "offset_includes_usb_host_delivery_uncertainty"
        ):
            raise PublicationFailure(f"Invalid frame timing semantics in row {index}")
        for field in frame_timing_fields:
            _validate_timing_value(timing, field, row_number=index, description="frame timing")
        if timing["output_total_us"] < timing["json_write_us"] or timing["output_total_us"] < timing["csi_write_us"]:
            raise PublicationFailure(f"Inconsistent output timings in row {index}")
        if timing["packet_start_to_json_us"] < timing["packet_end_to_json_us"]:
            raise PublicationFailure(f"Inconsistent packet-to-JSON timings in row {index}")
        if timing["packet_start_to_csi_us"] < timing["packet_end_to_csi_us"]:
            raise PublicationFailure(f"Inconsistent packet-to-CSI timings in row {index}")
    if not block_timings:
        raise PublicationFailure("WiFi block timing JSONL must contain at least one row")
    block_timing_fields = (
        "first_sample",
        "sample_count",
        "host_received_steady_ns",
        "queue_wait_us",
        "processing_us",
        "block_total_us",
        "candidates",
        "synchronized",
        "decoded",
        "frames",
        "queue_depth_after",
    )
    for index, timing in enumerate(block_timings, 1):
        if timing.get("schema") != "wifi_block_timing_v1":
            raise PublicationFailure(f"Invalid block timing schema in row {index}")
        for field in block_timing_fields:
            _validate_timing_value(timing, field, row_number=index, description="block timing")
        if not isinstance(timing.get("overflow"), bool) or not isinstance(timing.get("discontinuity"), bool):
            raise PublicationFailure(f"Invalid flags in block timing row {index}")
        if timing["block_total_us"] < timing["processing_us"]:
            raise PublicationFailure(f"Inconsistent block timings in row {index}")
    if counters != sorted(set(counters)):
        raise PublicationFailure("WiFi packet counters must be unique and strictly increasing")
    minimum = required_frames(num_beacons)
    if len(rows) < minimum:
        raise PublicationFailure(f"WiFi reception below 80%: {len(rows)} < {minimum}")
    csi_path = rx_dir / "csi.cf32"
    expected_size = len(rows) * CSI_ELEMENTS_PER_FRAME * COMPLEX64_BYTES
    if not csi_path.is_file() or csi_path.stat().st_size != expected_size:
        raise PublicationFailure("WiFi CF32 size does not match JSONL rows")
    rx_log = (rx_dir / "process.log").read_text(encoding="utf-8", errors="replace")
    for label in ("Overflows", "Timeouts", "Discontinuidades"):
        _zero_summary_value(rx_log, label)
    saved = re.search(r"^Guardados JSONL\s*:\s*(\d+)\s*$", rx_log, re.MULTILINE)
    if not saved or int(saved.group(1)) != len(rows):
        raise PublicationFailure("RX summary row count does not match JSONL")
    tx_log = (tx_dir / "process.log").read_text(encoding="utf-8", errors="replace")
    sent = re.search(r"^Beacons incluidos\s*:\s*(\d+)\s*$", tx_log, re.MULTILINE)
    zero = re.search(r"^Zero sends\s*:\s*(\d+)\s*$", tx_log, re.MULTILINE)
    if not sent or int(sent.group(1)) != num_beacons or not zero or int(zero.group(1)) != 0:
        raise PublicationFailure("TX summary does not match the requested successful transmission")
    return {
        "beacons_requested": num_beacons,
        "frames_received": len(rows),
        "frames_required": minimum,
        "frames_lost": num_beacons - len(rows),
        "receive_ratio": len(rows) / num_beacons,
        "first_counter": counters[0] if counters else None,
        "last_counter": counters[-1] if counters else None,
        "frame_timing_rows": len(frame_timings),
        "block_timing_rows": len(block_timings),
        "timings": {
            "queue_wait": _timing_summary(block_timings, "queue_wait_us"),
            "block_processing": _timing_summary(block_timings, "processing_us"),
            "json_write": _timing_summary(frame_timings, "json_write_us"),
            "csi_write": _timing_summary(frame_timings, "csi_write_us"),
            "output_total": _timing_summary(frame_timings, "output_total_us"),
            "packet_end_to_json": _timing_summary(frame_timings, "packet_end_to_json_us"),
            "packet_end_to_csi": _timing_summary(frame_timings, "packet_end_to_csi_us"),
        },
    }
