#!/usr/bin/env python3
"""Build a per-run CSV for a WiFi threshold campaign, including timing percentiles."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


TIMING_FIELDS = {
    "queue_wait": ("block-timings.jsonl", "queue_wait_us"),
    "block_processing": ("block-timings.jsonl", "processing_us"),
    "block_total": ("block-timings.jsonl", "block_total_us"),
    "json_write": ("frame-timings.jsonl", "json_write_us"),
    "csi_write": ("frame-timings.jsonl", "csi_write_us"),
    "output_total": ("frame-timings.jsonl", "output_total_us"),
    "block_to_json": ("frame-timings.jsonl", "block_received_to_json_us"),
    "block_to_csi": ("frame-timings.jsonl", "block_received_to_csi_us"),
    "packet_start_to_json": ("frame-timings.jsonl", "packet_start_to_json_us"),
    "packet_start_to_csi": ("frame-timings.jsonl", "packet_start_to_csi_us"),
    "packet_end_to_json": ("frame-timings.jsonl", "packet_end_to_json_us"),
    "packet_end_to_csi": ("frame-timings.jsonl", "packet_end_to_csi_us"),
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--storage-root", type=Path, default=Path("/srv/sync-experiments"))
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    try:
        raw = path.read_bytes()
    except OSError:
        return [], 0, False
    complete = not raw or raw.endswith(b"\n")
    rows: list[dict[str, Any]] = []
    invalid = 0
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            invalid += 1
    return rows, invalid, complete


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


def stats(values: list[float]) -> dict[str, float | None]:
    return {
        "mean_us": statistics.fmean(values) if values else None,
        "median_us": percentile(values, 0.50),
        "p95_us": percentile(values, 0.95),
        "p99_us": percentile(values, 0.99),
        "max_us": max(values) if values else None,
    }


def regex_number(text: str, pattern: str, cast: type = int) -> Any:
    match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    return cast(match.group(1)) if match else None


def seconds_between(start: str | None, stop: str | None) -> float | None:
    if not start or not stop:
        return None
    return (datetime.fromisoformat(stop) - datetime.fromisoformat(start)).total_seconds()


def index_runs(runs_root: Path) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for plan_path in runs_root.glob("*/.control/plan.json"):
        plan = load_json(plan_path)
        if not plan:
            continue
        label = plan.get("parameters", {}).get("label")
        if isinstance(label, str):
            indexed[label] = plan_path.parents[1]
    return indexed


def summarize_run(campaign_row: dict[str, str], run_dir: Path | None) -> dict[str, Any]:
    output: dict[str, Any] = dict(campaign_row)
    if run_dir is None:
        output.update({"run_id": None, "dataset_state": "NOT_FOUND"})
        return output

    plan = load_json(run_dir / ".control" / "plan.json") or {}
    state = load_json(run_dir / ".control" / "state.json") or {}
    parameters = plan.get("parameters", {})
    requested = int(parameters.get("num_beacons", 0))
    threshold = parameters.get("detector_threshold")
    if threshold is None:
        effective = load_json(run_dir / "rx_wifi" / "runtime" / "effective-config.json") or {}
        threshold = (
            effective.get("waveform_config", {})
            .get("detector", {})
            .get("metric_threshold")
        )

    features, invalid_features, features_complete = load_jsonl(run_dir / "rx_wifi" / "features.jsonl")
    counters = [row.get("packet_counter") for row in features if isinstance(row.get("packet_counter"), int)]
    csi_path = run_dir / "rx_wifi" / "csi.cf32"
    csi_bytes = csi_path.stat().st_size if csi_path.is_file() else None
    expected_csi_bytes = len(features) * 52 * 8
    rx_log = (run_dir / "rx_wifi" / "process.log").read_text(encoding="utf-8", errors="replace") if (run_dir / "rx_wifi" / "process.log").is_file() else ""
    tx_log = (run_dir / "tx_wifi" / "process.log").read_text(encoding="utf-8", errors="replace") if (run_dir / "tx_wifi" / "process.log").is_file() else ""
    processes = state.get("processes", {})
    error = state.get("last_error") or {}

    output.update(
        {
            "run_id": plan.get("run_id"),
            "dataset_state": state.get("state"),
            "failure_code": error.get("code"),
            "failure_message": error.get("message"),
            "requested_beacons": requested,
            "required_beacons_80pct": math.ceil(requested * 0.8),
            "detected_beacons": len(features),
            "lost_beacons": requested - len(features) if requested else None,
            "reception_ratio": len(features) / requested if requested else None,
            "detector_threshold": threshold,
            "first_packet_counter": min(counters) if counters else None,
            "last_packet_counter": max(counters) if counters else None,
            "unique_packet_counters": len(set(counters)),
            "invalid_feature_json_lines": invalid_features,
            "feature_jsonl_complete": features_complete,
            "csi_bytes": csi_bytes,
            "expected_csi_bytes": expected_csi_bytes,
            "jsonl_cf32_closure_ok": csi_bytes == expected_csi_bytes,
            "rx_blocks": regex_number(rx_log, r"^Bloques RX\s*:\s*(\d+)"),
            "rx_samples": regex_number(rx_log, r"^Muestras RX\s*:\s*(\d+)"),
            "overflows": regex_number(rx_log, r"^Overflows\s*:\s*(\d+)"),
            "timeouts": regex_number(rx_log, r"^Timeouts\s*:\s*(\d+)"),
            "discontinuities": regex_number(rx_log, r"^Discontinuidades\s*:\s*(\d+)"),
            "max_queue": regex_number(rx_log, r"^Cola máxima\s*:\s*(\d+)"),
            "candidates": regex_number(rx_log, r"^Candidatos\s*:\s*(\d+)"),
            "synchronized": regex_number(rx_log, r"^Sincronizados\s*:\s*(\d+)"),
            "decoded": regex_number(rx_log, r"^Decodificados\s*:\s*(\d+)"),
            "published": regex_number(rx_log, r"^Publicados\s*:\s*(\d+)"),
            "saved_jsonl": regex_number(rx_log, r"^Guardados JSONL\s*:\s*(\d+)"),
            "tx_zero_sends": regex_number(tx_log, r"^Zero sends\s*:\s*(\d+)"),
            "rx_start_to_ready_s": seconds_between(
                processes.get("rx_wifi", {}).get("started_at"),
                processes.get("rx_wifi", {}).get("ready_at"),
            ),
            "rx_runtime_s": seconds_between(
                processes.get("rx_wifi", {}).get("started_at"),
                processes.get("rx_wifi", {}).get("stopped_at"),
            ),
            "tx_runtime_s": seconds_between(
                processes.get("tx_wifi", {}).get("started_at"),
                processes.get("tx_wifi", {}).get("stopped_at"),
            ),
            "run_dir": str(run_dir),
            "manifest_path": str(run_dir / "manifest.json") if (run_dir / "manifest.json").is_file() else None,
        }
    )

    timing_cache: dict[str, list[dict[str, Any]]] = {}
    for prefix, (filename, field) in TIMING_FIELDS.items():
        if filename not in timing_cache:
            timing_cache[filename] = load_jsonl(run_dir / "rx_wifi" / filename)[0]
        values = [
            float(row[field])
            for row in timing_cache[filename]
            if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)
        ]
        for statistic, value in stats(values).items():
            output[f"{prefix}_{statistic}"] = value
    output["frame_timing_rows"] = len(timing_cache.get("frame-timings.jsonl", []))
    output["block_timing_rows"] = len(timing_cache.get("block-timings.jsonl", []))
    return output


def main() -> int:
    args = arguments()
    results_path = args.campaign_dir / "results.tsv"
    with results_path.open(encoding="utf-8", newline="") as source:
        campaign_rows = list(csv.DictReader(source, delimiter="\t"))
    run_index = index_runs(args.storage_root / "runs")
    rows = [summarize_run(row, run_index.get(row["label"])) for row in campaign_rows]
    output_path = args.output or args.campaign_dir / "wifi_threshold_runs.csv"
    fieldnames: list[str] = []
    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)
    with output_path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
