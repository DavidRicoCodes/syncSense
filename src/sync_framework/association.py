"""Retryable approximate cross-host association derived from immutable raw data."""

from __future__ import annotations

import bisect
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checksums import sha256_file
from .domain import AssociationFailure, SCHEMA_VERSION, ValidationFailure
from .publication import verify_published_manifest
from .state import utc_now
from .storage import atomic_write_json
from .validation import validate_document


SEMANTICS = "approximate_operational_ntp_association_not_acquisition_alignment"


def generate_association_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"assoc_{stamp}_{uuid.uuid4().hex[:12]}"


def _read_jsonl(path: Path, schema: str | None = None) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssociationFailure(f"Cannot read association input: {path}") from exc
    if raw and not raw.endswith(b"\n"):
        raise AssociationFailure(f"Truncated association input: {path}")
    rows = []
    for number, encoded in enumerate(raw.splitlines(), 1):
        try:
            row = json.loads(encoded)
            if schema:
                validate_document(row, schema)
        except (json.JSONDecodeError, ValidationFailure) as exc:
            raise AssociationFailure(
                f"Invalid association input {path}:{number}"
            ) from exc
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_clock(run_dir: Path, producer_id: str) -> tuple[list[dict[str, Any]], int]:
    producer = run_dir / producer_id
    anchors = _read_jsonl(
        producer / "host-clock-anchors.jsonl", "host-clock-anchor"
    )
    if [item["phase"] for item in anchors] != ["start", "end"]:
        raise AssociationFailure(f"Invalid host anchor phases for {producer_id}")
    if anchors[1]["monotonic_ns"] <= anchors[0]["monotonic_ns"]:
        raise AssociationFailure(f"Non-increasing host anchors for {producer_id}")
    try:
        ntp = json.loads((producer / "ntp-status.json").read_text(encoding="utf-8"))
        validate_document(ntp, "ntp-status")
    except (OSError, json.JSONDecodeError, ValidationFailure) as exc:
        raise AssociationFailure(f"Invalid NTP status for {producer_id}") from exc
    if not all(sample["synchronized"] for sample in ntp["samples"]):
        raise AssociationFailure(
            f"NTP was not synchronized for the full capture on {producer_id}"
        )
    telemetry_ns = 0
    for sample in ntp["samples"]:
        telemetry_ns = max(
            telemetry_ns,
            int(
                1e6
                * sum(
                    float(sample.get(field) or 0)
                    for field in (
                        "root_delay_ms",
                        "root_dispersion_ms",
                        "system_jitter_ms",
                    )
                )
            ),
        )
    uncertainty = max(
        telemetry_ns,
        *(int(anchor["sampling_uncertainty_ns"]) for anchor in anchors),
    )
    return anchors, uncertainty


def _project_utc(monotonic_ns: int, anchors: list[dict[str, Any]]) -> int:
    first, last = anchors
    monotonic_span = last["monotonic_ns"] - first["monotonic_ns"]
    realtime_span = last["realtime_ns"] - first["realtime_ns"]
    projected = first["realtime_ns"] + (
        (monotonic_ns - first["monotonic_ns"])
        * realtime_span
        / monotonic_span
    )
    return int(round(projected))


def _association_inputs(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    wifi_anchors, wifi_uncertainty = _load_clock(run_dir, "rx_wifi")
    ssb_anchors, ssb_uncertainty = _load_clock(run_dir, "rx_5g")
    wifi_features = _read_jsonl(run_dir / "rx_wifi" / "features.jsonl")
    wifi_timings = _read_jsonl(run_dir / "rx_wifi" / "frame-timings.jsonl")
    ssb_rows = _read_jsonl(run_dir / "rx_5g" / "hssb.jsonl", "5g-hssb-row")
    if len(wifi_features) != len(wifi_timings):
        raise AssociationFailure("WiFi feature/timing rows do not close")
    wifi = []
    for index, (feature, timing) in enumerate(
        zip(wifi_features, wifi_timings, strict=True)
    ):
        if timing.get("event_monotonic_semantics") != (
            "operational_host_estimate_only_not_acquisition_time"
        ):
            raise AssociationFailure("WiFi operational timing semantics are invalid")
        wifi.append(
            {
                "row_index": index,
                "device_ticks": feature["rx_timestamp_ticks"],
                "tick_rate_hz": feature["rx_tick_rate_hz"],
                "estimated_utc_ns": _project_utc(
                    timing["event_monotonic_ns"], wifi_anchors
                ),
            }
        )
    ssb = [
        {
            "row_index": index,
            "device_ticks": row["event_device_ticks"],
            "tick_rate_hz": row["device_tick_rate_hz"],
            "estimated_utc_ns": _project_utc(
                row["event_monotonic_ns"], ssb_anchors
            ),
        }
        for index, row in enumerate(ssb_rows)
    ]
    return wifi, ssb, wifi_uncertainty + ssb_uncertainty


def run_nearest_ntp_association(
    run_dir: Path,
    maximum_delta_ms: float,
) -> dict[str, Any]:
    manifest = verify_published_manifest(run_dir)
    if manifest["profile"]["profile_id"] != "nosync_passive":
        raise AssociationFailure("nearest-ntp only accepts nosync_passive datasets")
    association_id = generate_association_id()
    output_dir = run_dir / "associations" / association_id
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = run_dir / "manifest.json"
    manifest_sha = sha256_file(manifest_path)
    request = {
        "schema_version": SCHEMA_VERSION,
        "association_id": association_id,
        "run_id": manifest["run_id"],
        "adapter": "nearest-ntp",
        "maximum_delta_ms": float(maximum_delta_ms),
        "session_manifest_path": "manifest.json",
        "session_manifest_sha256": manifest_sha,
    }
    validate_document(request, "association-request")
    atomic_write_json(output_dir / "request.json", request)
    started = utc_now()
    atomic_write_json(
        output_dir / "state.json",
        {
            "schema_version": SCHEMA_VERSION,
            "association_id": association_id,
            "run_id": manifest["run_id"],
            "status": "RUNNING",
            "started_at": started,
        },
    )
    try:
        wifi, ssb, uncertainty = _association_inputs(run_dir)
        if not ssb:
            raise AssociationFailure("No valid SSB rows are available")
        ssb_times = [item["estimated_utc_ns"] for item in ssb]
        pairs = []
        used_ssb: set[int] = set()
        maximum_delta_ns = int(round(maximum_delta_ms * 1e6))
        for beacon in wifi:
            insertion = bisect.bisect_left(ssb_times, beacon["estimated_utc_ns"])
            candidates = [
                index
                for index in (insertion - 1, insertion)
                if 0 <= index < len(ssb)
            ]
            nearest = min(
                candidates,
                key=lambda index: abs(
                    ssb[index]["estimated_utc_ns"]
                    - beacon["estimated_utc_ns"]
                ),
            )
            delta_ns = (
                ssb[nearest]["estimated_utc_ns"]
                - beacon["estimated_utc_ns"]
            )
            if abs(delta_ns) > maximum_delta_ns:
                continue
            pair = {
                "schema_version": SCHEMA_VERSION,
                "association_id": association_id,
                "pair_index": len(pairs),
                "wifi": beacon,
                "ssb_5g": ssb[nearest],
                "delta_t_ms": delta_ns / 1e6,
                "ssb_reused": nearest in used_ssb,
                "uncertainty_ns": uncertainty,
                "semantics": SEMANTICS,
            }
            validate_document(pair, "association-pair")
            pairs.append(pair)
            used_ssb.add(nearest)
        _write_jsonl(output_dir / "pairs.jsonl", pairs)
        summary = {
            "schema_version": SCHEMA_VERSION,
            "association_id": association_id,
            "run_id": manifest["run_id"],
            "semantics": SEMANTICS,
            "wifi_observations": len(wifi),
            "ssb_observations": len(ssb),
            "matched_pairs": len(pairs),
            "unmatched_wifi": len(wifi) - len(pairs),
            "maximum_delta_ms": maximum_delta_ms,
            "reused_ssb_pairs": sum(pair["ssb_reused"] for pair in pairs),
            "mean_absolute_delta_ms": (
                sum(abs(pair["delta_t_ms"]) for pair in pairs) / len(pairs)
                if pairs
                else None
            ),
        }
        atomic_write_json(output_dir / "summary.json", summary)
        result = {
            "schema_version": SCHEMA_VERSION,
            "association_id": association_id,
            "run_id": manifest["run_id"],
            "status": "SUCCEEDED",
            "started_at": started,
            "finished_at": utc_now(),
            "pair_count": len(pairs),
            "error": None,
        }
        validate_document(result, "association-result")
        atomic_write_json(output_dir / "state.json", result)
        if sha256_file(manifest_path) != manifest_sha:
            raise AssociationFailure("Raw session changed during association")
        files = [
            {
                "path": name,
                "sha256": sha256_file(output_dir / name),
            }
            for name in ("request.json", "state.json", "pairs.jsonl", "summary.json")
        ]
        association_manifest = {
            "schema_version": SCHEMA_VERSION,
            "association_id": association_id,
            "run_id": manifest["run_id"],
            "status": "COMPLETE",
            "adapter": "nearest-ntp",
            "semantics": SEMANTICS,
            "files": files,
        }
        validate_document(association_manifest, "association-manifest")
        atomic_write_json(output_dir / "manifest.json", association_manifest)
        return {
            **result,
            "association_path": str(output_dir),
            "manifest_path": str(output_dir / "manifest.json"),
        }
    except Exception as exc:
        failed = {
            "schema_version": SCHEMA_VERSION,
            "association_id": association_id,
            "run_id": manifest["run_id"],
            "status": "FAILED",
            "started_at": started,
            "finished_at": utc_now(),
            "pair_count": 0,
            "error": {
                "code": getattr(exc, "code", "ASSOCIATION_FAILED"),
                "message": str(exc),
            },
        }
        validate_document(failed, "association-result")
        atomic_write_json(output_dir / "state.json", failed)
        if isinstance(exc, AssociationFailure):
            raise
        raise AssociationFailure(
            f"Association failed: {association_id}",
            details={"association_id": association_id},
        ) from exc


def association_status(
    run_dir: Path, association_id: str | None = None
) -> dict[str, Any]:
    root = run_dir / "associations"
    if association_id:
        path = root / association_id / "state.json"
        if not path.is_file():
            raise AssociationFailure(f"Unknown association: {association_id}")
        return json.loads(path.read_text(encoding="utf-8"))
    values = []
    if root.is_dir():
        for path in sorted(root.glob("*/state.json")):
            values.append(json.loads(path.read_text(encoding="utf-8")))
    return {"run_id": run_dir.name, "associations": values}
