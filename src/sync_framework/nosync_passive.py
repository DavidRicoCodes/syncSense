"""Validation rules for the real separated-receiver no-sync experiment."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .domain import PublicationFailure, ValidationFailure
from .validation import validate_document


POSITION = re.compile(r"^R([2-8])C([2-7])$")
BLOCKED_TESTBED1_POSITIONS = {"R3C3", "R3C4", "R4C3", "R4C4"}
PARALLEL_SUMMARY = re.compile(
    r"^SUMMARY \| captured=(\d+) \| valid=(\d+) "
    r"\| invalid=(\d+) \| capture_errors=(\d+)$",
    re.MULTILINE,
)
COMPLEX64_BYTES = 8
SSB_ELEMENTS = 240 * 4


def canonical_position(value: str, testbed_id: str) -> str:
    normalized = value.strip().upper()
    if testbed_id != "testbed1":
        if not normalized or not re.fullmatch(r"[A-Z0-9._-]+", normalized):
            raise ValidationFailure("Position contains unsafe characters")
        return normalized
    match = POSITION.fullmatch(normalized)
    if match is None:
        raise ValidationFailure(
            "testbed1 position must be R<row>C<column>, rows 2..8 and columns 2..7"
        )
    if normalized in BLOCKED_TESTBED1_POSITIONS:
        raise ValidationFailure(f"testbed1 position is blocked: {normalized}")
    return normalized


def global_timeout_s(parameters: dict[str, Any]) -> float:
    return (
        90.0
        + int(parameters["num_beacons"]) * 0.1024
        + float(parameters["pre_tx_guard_s"])
        + float(parameters["tx_start_delay_s"])
        + float(parameters["rx_max_drain_s"])
        + 30.0
    )


def _read_jsonl(path: Path, description: str) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicationFailure(f"Cannot read {description}: {path}") from exc
    if not raw or not raw.endswith(b"\n"):
        raise PublicationFailure(f"{description} is empty or truncated")
    rows: list[dict[str, Any]] = []
    for number, encoded in enumerate(raw.splitlines(), 1):
        try:
            row = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationFailure(
                f"Invalid {description} JSON at row {number}"
            ) from exc
        if not isinstance(row, dict):
            raise PublicationFailure(f"Invalid {description} row {number}")
        rows.append(row)
    return rows


def validate_5g_outputs(
    run_dir: Path,
    *,
    duration_s: float,
    minimum_valid_ratio: float,
    minimum_valid_rate_hz: float,
) -> dict[str, Any]:
    producer_dir = run_dir / "rx_5g"
    rows = _read_jsonl(producer_dir / "hssb.jsonl", "5G HSSB JSONL")
    try:
        log = (producer_dir / "process.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        raise PublicationFailure("Cannot read 5G process log") from exc
    if "UHD RX error" in log:
        raise PublicationFailure("5G receiver log contains UHD RX error")
    match = PARALLEL_SUMMARY.search(log)
    if match is None:
        raise PublicationFailure("5G receiver log lacks final parallel summary")
    captured, valid, invalid, capture_errors = map(int, match.groups())
    if captured != valid + invalid or valid != len(rows) or capture_errors != 0:
        raise PublicationFailure("5G capture counters do not close")

    last_iteration = -1
    last_ticks = -1
    last_operational = -1
    for number, row in enumerate(rows, 1):
        try:
            validate_document(row, "5g-hssb-row")
        except ValidationFailure as exc:
            raise PublicationFailure(
                f"Invalid 5G HSSB schema at row {number}: {exc}"
            ) from exc
        required = {
            "schema": "joint_5g_hssb_jsonl_v1",
            "waveform_type": "5g_ssb",
            "valid": True,
            "error": "",
            "clock_domain": "local_device_epoch",
            "reference_point": "ssb_pss_start",
            "canonical_timestamp_semantics": (
                "local_usrp_device_time_first_sample_plus_"
                "detector_offset_samples"
            ),
            "timestamp_semantics": (
                "host_operational_estimate_only_from_block_end_and_"
                "detector_offset; cross_host_use_requires_ntp_anchors_"
                "and_is_not_acquisition_alignment"
            ),
            "feature_dtype": "complex64",
            "feature_shape": [240, 4],
            "feature_count": 960,
        }
        if any(row.get(key) != value for key, value in required.items()):
            raise PublicationFailure(f"Invalid 5G row contract at row {number}")
        integer_fields = (
            "iteration",
            "block_start_device_ticks",
            "detector_offset_samples",
            "event_device_ticks",
            "device_tick_rate_hz",
            "event_monotonic_ns",
            "event_unix_ns",
        )
        if any(
            not isinstance(row.get(field), int)
            or isinstance(row.get(field), bool)
            or row[field] < 0
            for field in integer_fields
        ):
            raise PublicationFailure(f"Invalid 5G timestamp at row {number}")
        if (
            row["event_device_ticks"]
            != row["block_start_device_ticks"] + row["detector_offset_samples"]
            or row["device_tick_rate_hz"] != round(row.get("sample_rate_hz", 0))
        ):
            raise PublicationFailure(f"5G canonical timestamp does not close at row {number}")
        if row["iteration"] <= last_iteration or row["event_device_ticks"] < last_ticks:
            raise PublicationFailure("5G iterations/device timestamps are not monotonic")
        if row["event_monotonic_ns"] < last_operational:
            raise PublicationFailure("5G operational timestamps are not monotonic")
        last_iteration = row["iteration"]
        last_ticks = row["event_device_ticks"]
        last_operational = row["event_monotonic_ns"]

    expected_binary_size = len(rows) * SSB_ELEMENTS * COMPLEX64_BYTES
    for name in ("rxgrid.cf32", "hssb.cf32"):
        path = producer_dir / name
        if not path.is_file() or path.stat().st_size != expected_binary_size:
            raise PublicationFailure(f"5G binary size does not close: {name}")
    if captured <= 0 or valid / captured < minimum_valid_ratio:
        raise PublicationFailure("5G valid-grid ratio is below the configured minimum")
    minimum = math.ceil(duration_s * minimum_valid_rate_hz)
    if valid < minimum:
        raise PublicationFailure(f"5G valid-grid rate is too low: {valid} < {minimum}")
    return {
        "duration_s": duration_s,
        "iterations": captured,
        "valid_grids": valid,
        "invalid_grids": invalid,
        "valid_ratio": valid / captured,
        "valid_rate_hz": valid / duration_s,
        "required_valid_grids": minimum,
    }


def validate_n310_outputs(run_dir: Path, num_beacons: int) -> dict[str, Any]:
    path = run_dir / "tx_wifi" / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationFailure("N310 TX state is missing or invalid") from exc
    fatal = {
        name: int(count)
        for name, count in state.get("async_event_counts", {}).items()
        if "burst_ack" not in name.lower() and int(count) > 0
    }
    if (
        int(state.get("sent_packets", -1)) != num_beacons
        or int(state.get("total_zero_sends", -1)) != 0
        or fatal
    ):
        raise PublicationFailure(
            "N310 TX did not close exactly without zero sends/fatal async events"
        )
    return {
        "sent_packets": num_beacons,
        "zero_sends": 0,
        "fatal_async_events": fatal,
        "tx_strategy": state.get("tx_strategy"),
    }
