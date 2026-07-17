"""Local deterministic dummy inference over immutable COMPLETE sessions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checksums import sha256_file
from .config import document_digest
from .domain import InferenceFailure, SCHEMA_VERSION
from .publication import _ssb_validation_duration, verify_published_manifest
from .state import utc_now
from .storage import atomic_write_json
from .validation import validate_document
from .ssb_smoke import validate_ssb_smoke_outputs


def generate_inference_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"inf_{stamp}_{uuid.uuid4().hex[:12]}"


class DummyBatchModelAdapter:
    adapter_id = "dummy"
    adapter_version = "1.0.0"

    def run(self, request: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
        validate_document(request, "batch-model-request")
        manifest = verify_published_manifest(run_dir)
        manifest_path = run_dir / request["session_manifest_path"]
        if manifest["state"] != "COMPLETE" or sha256_file(manifest_path) != request["session_manifest_sha256"]:
            raise InferenceFailure("Dummy inference requires the exact verified COMPLETE manifest")
        output_dir = run_dir / request["output_directory"]
        before = sha256_file(manifest_path)
        started = utc_now()
        producer_manifests = []
        event_count = 0
        artifact_count = 0
        for producer in manifest["producers"]:
            value = json.loads((run_dir / producer["manifest_path"]).read_text(encoding="utf-8"))
            producer_manifests.append(value)
            event_count += value["event_summary"]["count"]
            artifact_count += len(value["artifacts"])
        summary = {
            "schema_version": SCHEMA_VERSION, "run_id": manifest["run_id"],
            "inference_id": request["inference_id"], "simulation": True,
            "producer_count": len(producer_manifests), "artifact_count": artifact_count,
            "event_count": event_count,
            "producer_ids": sorted(item["producer_id"] for item in producer_manifests),
            "note": "Deterministic orchestration test only; no sensing or ML logic was executed.",
        }
        if manifest["profile"]["profile_id"] == "wifi_link_smoke":
            requested = int(manifest["parameters"]["num_beacons"])
            received = next(
                int(artifact["row_count"])
                for producer in producer_manifests if producer["producer_id"] == "rx_wifi"
                for artifact in producer["artifacts"] if artifact["artifact_type"] == "wifi_csi_feature_rows"
            )
            summary["wifi_smoke"] = {
                "beacons_requested": requested,
                "frames_received": received,
                "frames_lost": requested - received,
                "receive_ratio": received / requested,
                "input_data": "real_hardware_integration_smoke",
            }
        elif manifest["profile"]["profile_id"] == "ssb_rx_smoke":
            ssb = validate_ssb_smoke_outputs(
                run_dir,
                float(manifest["parameters"]["duration_s"]),
                float(manifest["parameters"]["min_valid_ssb_rate_hz"]),
            )
            summary["ssb_rx_smoke"] = {
                "duration_s": ssb["duration_s"],
                "iterations": ssb["iterations"],
                "valid_grids": ssb["valid_grids"],
                "invalid_grids": ssb["invalid_grids"],
                "valid_ratio": ssb["valid_ratio"],
                "valid_rate_hz": ssb["valid_rate_hz"],
                "input_data": "real_hardware_integration_smoke",
            }
        elif manifest["profile"]["profile_id"] == "nosync_passive_hardware_smoke":
            requested = int(manifest["parameters"]["num_beacons"])
            received = next(
                int(artifact["row_count"])
                for producer in producer_manifests if producer["producer_id"] == "rx_wifi"
                for artifact in producer["artifacts"] if artifact["artifact_type"] == "wifi_csi_feature_rows"
            )
            state_like = {
                "profile": {"parameters": manifest["parameters"]},
                "operational_window": manifest["operational_window"],
            }
            ssb = validate_ssb_smoke_outputs(
                run_dir,
                _ssb_validation_duration(state_like, "nosync_passive_hardware_smoke"),
                float(manifest["parameters"]["min_valid_ssb_rate_hz"]),
            )
            summary["nosync_passive_hardware_smoke"] = {
                "wifi": {
                    "beacons_requested": requested,
                    "frames_received": received,
                    "frames_lost": requested - received,
                    "receive_ratio": received / requested,
                },
                "ssb_5g": {
                    "observed_duration_s": ssb["duration_s"],
                    "iterations": ssb["iterations"],
                    "valid_grids": ssb["valid_grids"],
                    "invalid_grids": ssb["invalid_grids"],
                    "valid_ratio": ssb["valid_ratio"],
                    "valid_rate_hz": ssb["valid_rate_hz"],
                },
                "input_data": "real_hardware_integration_smoke",
                "fusion_performed": False,
                "clock_relation": "not_comparable",
            }
        summary_path = output_dir / "summary.json"
        atomic_write_json(summary_path, summary)
        artifact = {
            "schema_version": SCHEMA_VERSION, "artifact_id": "dummy_inference_summary",
            "producer_id": "dummy_model", "artifact_type": "synthetic_inference_summary",
            "media_type": "application/json", "path": "summary.json",
            "size_bytes": summary_path.stat().st_size,
            "checksum": {"algorithm": "sha256", "hex": sha256_file(summary_path)},
        }
        validate_document(artifact, "artifact")
        if sha256_file(manifest_path) != before:
            raise InferenceFailure("Session manifest changed during inference")
        result = {
            "schema_version": SCHEMA_VERSION, "inference_id": request["inference_id"],
            "run_id": manifest["run_id"], "status": "SUCCEEDED",
            "adapter": {"adapter_id": self.adapter_id, "adapter_version": self.adapter_version},
            "started_at": started, "finished_at": utc_now(),
            "inputs": ["manifest.json"], "outputs": ["summary.json"],
            "artifacts": [artifact], "error": None,
        }
        validate_document(result, "batch-model-result")
        return result


def run_dummy_inference(run_dir: Path) -> dict[str, Any]:
    manifest = verify_published_manifest(run_dir)
    inference_id = generate_inference_id()
    relative_output = f"inference/{inference_id}"
    output_dir = run_dir / relative_output
    output_dir.mkdir(parents=True, exist_ok=False)
    adapter = DummyBatchModelAdapter()
    config_digest = document_digest({"adapter": adapter.adapter_id, "version": adapter.adapter_version})
    request = {
        "schema_version": SCHEMA_VERSION, "inference_id": inference_id, "run_id": manifest["run_id"],
        "session_manifest_path": "manifest.json", "session_manifest_sha256": sha256_file(run_dir / "manifest.json"),
        "adapter": {"adapter_id": adapter.adapter_id, "adapter_version": adapter.adapter_version, "config_digest": config_digest},
        "output_directory": relative_output,
    }
    validate_document(request, "batch-model-request")
    atomic_write_json(output_dir / "request.json", request)
    atomic_write_json(output_dir / "state.json", {"inference_id": inference_id, "run_id": manifest["run_id"], "status": "RUNNING", "started_at": utc_now()})
    try:
        result = adapter.run(request, run_dir=run_dir)
    except Exception as exc:
        result = {
            "schema_version": SCHEMA_VERSION, "inference_id": inference_id, "run_id": manifest["run_id"],
            "status": "FAILED", "adapter": {"adapter_id": adapter.adapter_id, "adapter_version": adapter.adapter_version},
            "started_at": utc_now(), "finished_at": utc_now(), "inputs": ["manifest.json"],
            "outputs": [], "artifacts": [], "error": {"code": getattr(exc, "code", "DUMMY_INFERENCE_FAILED"), "message": str(exc)},
        }
        validate_document(result, "batch-model-result")
    atomic_write_json(output_dir / "result.json", result)
    atomic_write_json(output_dir / "state.json", {"inference_id": inference_id, "run_id": manifest["run_id"], "status": result["status"], "finished_at": result["finished_at"]})
    if result["status"] != "SUCCEEDED":
        raise InferenceFailure(f"Dummy inference failed: {inference_id}", details={"inference_id": inference_id})
    return result


def inference_status(run_dir: Path, inference_id: str | None = None) -> dict[str, Any]:
    root = run_dir / "inference"
    if inference_id:
        path = root / inference_id / "state.json"
        if not path.is_file():
            raise InferenceFailure(f"Unknown inference: {inference_id}")
        return json.loads(path.read_text(encoding="utf-8"))
    values = []
    if root.is_dir():
        for path in sorted(root.glob("*/state.json")):
            values.append(json.loads(path.read_text(encoding="utf-8")))
    return {"run_id": run_dir.name, "inferences": values}
