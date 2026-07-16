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


def _read_complete_json_lines(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicationFailure(f"Cannot read WiFi JSONL: {path}") from exc
    if raw and not raw.endswith(b"\n"):
        raise PublicationFailure("WiFi JSONL has a truncated final line")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicationFailure(f"Invalid WiFi JSON at line {line_number}") from exc
        if not isinstance(value, dict):
            raise PublicationFailure(f"WiFi JSONL row {line_number} is not an object")
        rows.append(value)
    return rows


def _zero_summary_value(log: str, label: str) -> None:
    match = re.search(rf"^{re.escape(label)}\s*:\s*(\d+)\s*$", log, re.MULTILINE | re.IGNORECASE)
    if not match or int(match.group(1)) != 0:
        raise PublicationFailure(f"RX summary does not prove zero {label.lower()}")


def validate_wifi_smoke_outputs(run_dir: Path, num_beacons: int) -> dict[str, Any]:
    rx_dir = run_dir / "rx_wifi"
    tx_dir = run_dir / "tx_wifi"
    rows = _read_complete_json_lines(rx_dir / "features.jsonl")
    counters: list[int] = []
    for index, row in enumerate(rows, 1):
        counter = row.get("packet_counter")
        features = row.get("complex_features")
        if not isinstance(counter, int) or not 0 <= counter < num_beacons:
            raise PublicationFailure(f"Invalid packet_counter in WiFi row {index}")
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
    }
