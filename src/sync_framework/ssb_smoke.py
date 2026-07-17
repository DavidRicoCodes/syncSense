"""Validation contract for the passive 5G SSB hardware smoke."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import PublicationFailure, ValidationFailure
from .validation import validate_document


SSB_ROW_SCHEMA_REF = "urn:sync:schema:v1:5g-ssb-rxgrid-row"
_FINAL_STATS = {
    "iterations": re.compile(r"^iterations:\s*(\d+)\s*$", re.MULTILINE),
    "valid_grids": re.compile(r"^valid grids:\s*(\d+)\s*$", re.MULTILINE),
    "invalid_grids": re.compile(r"^invalid grids:\s*(\d+)\s*$", re.MULTILINE),
    "jsonl_lines": re.compile(r"^JSONL lines written:\s*(\d+)\s*$", re.MULTILINE),
}


def _reject_nonfinite(token: str) -> None:
    raise ValueError(f"non-finite JSON number: {token}")


def _assert_finite(value: Any, location: str = "<row>") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise PublicationFailure(f"Non-finite value at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite(child, f"{location}[{index}]")


def _parse_final_statistics(log_text: str) -> dict[str, int]:
    if "UHD RX error" in log_text:
        raise PublicationFailure("5G receiver log contains UHD RX error")
    values: dict[str, int] = {}
    for name, pattern in _FINAL_STATS.items():
        match = pattern.search(log_text)
        if match is None:
            raise PublicationFailure(f"5G receiver log lacks final statistic: {name}")
        values[name] = int(match.group(1))
    if values["iterations"] != values["valid_grids"] + values["invalid_grids"]:
        raise PublicationFailure("5G receiver iteration totals do not close")
    if values["valid_grids"] != values["jsonl_lines"]:
        raise PublicationFailure("5G receiver JSONL count does not match final statistics")
    return values


def validate_ssb_smoke_outputs(
    run_dir: Path,
    duration_s: float,
    min_valid_ssb_rate_hz: float,
) -> dict[str, Any]:
    producer_dir = run_dir / "rx_5g"
    jsonl_path = producer_dir / "rxgridssb.jsonl"
    log_path = producer_dir / "process.log"
    try:
        raw = jsonl_path.read_bytes()
        log_text = log_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PublicationFailure(f"Cannot read 5G smoke output: {exc}") from exc
    if not raw or not raw.endswith(b"\n"):
        raise PublicationFailure("5G JSONL is empty or has a truncated final line")
    stats = _parse_final_statistics(log_text)
    last_iteration = -1
    last_timestamp_ns = -1
    rows = 0
    for line_number, encoded_line in enumerate(raw.splitlines(), 1):
        if not encoded_line:
            raise PublicationFailure(f"Blank 5G JSONL line: {line_number}")
        try:
            row = json.loads(
                encoded_line,
                parse_constant=_reject_nonfinite,
            )
            validate_document(row, "5g-ssb-rxgrid-row")
            _assert_finite(row)
            parsed_utc = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, ValidationFailure) as exc:
            raise PublicationFailure(f"Invalid 5G JSONL row {line_number}: {exc}") from exc
        iteration = row["iteration"]
        timestamp_ns = row["rx_timestamp_ns"]
        if iteration <= last_iteration:
            raise PublicationFailure("5G iterations must be unique and strictly increasing")
        if timestamp_ns < last_timestamp_ns:
            raise PublicationFailure("5G operational host timestamps must be monotonic")
        if abs(row["timestamp_unix"] - timestamp_ns / 1e9) > 1e-6:
            raise PublicationFailure("5G host timestamp representations disagree")
        if abs(parsed_utc.timestamp() - row["timestamp_unix"]) > 1.0:
            raise PublicationFailure("5G UTC timestamp is inconsistent with host serialization time")
        last_iteration = iteration
        last_timestamp_ns = timestamp_ns
        rows += 1
    if rows != stats["valid_grids"]:
        raise PublicationFailure("5G JSONL row count does not match valid grids")
    if stats["iterations"] <= 0:
        raise PublicationFailure("5G receiver completed without iterations")
    ratio = stats["valid_grids"] / stats["iterations"]
    if ratio < 0.8:
        raise PublicationFailure(f"5G valid-grid ratio below 80%: {ratio:.6f}")
    required = math.ceil(float(duration_s) * float(min_valid_ssb_rate_hz))
    if stats["valid_grids"] < required:
        raise PublicationFailure(
            f"5G valid-grid rate below requirement: {stats['valid_grids']} < {required}"
        )
    return {
        "duration_s": float(duration_s),
        "iterations": stats["iterations"],
        "valid_grids": stats["valid_grids"],
        "invalid_grids": stats["invalid_grids"],
        "jsonl_lines": rows,
        "valid_ratio": ratio,
        "valid_rate_hz": stats["valid_grids"] / float(duration_s),
        "required_valid_grids": required,
        "first_iteration": json.loads(raw.splitlines()[0])["iteration"],
        "last_iteration": last_iteration,
    }
