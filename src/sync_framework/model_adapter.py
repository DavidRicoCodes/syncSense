"""Validation boundary for a future externally supplied batch model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .checksums import sha256_file
from .domain import ValidationFailure
from .validation import validate_document, validate_relative_path


def validate_batch_request(request: dict[str, Any]) -> None:
    validate_document(request, "batch-model-request")
    validate_relative_path(request["session_manifest_path"])
    output_directory = validate_relative_path(request["output_directory"])
    expected_output = Path("runs") / request["run_id"] / "inference" / request["inference_id"]
    if output_directory != expected_output:
        raise ValidationFailure(f"Batch inference output must be {expected_output.as_posix()}")
    manifest_path = Path(request["session_manifest_path"])
    if not manifest_path.is_file():
        raise ValidationFailure("Batch inference input manifest does not exist")
    if sha256_file(manifest_path) != request["session_manifest_sha256"]:
        raise ValidationFailure("Batch inference input manifest checksum does not match")
    import json
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_document(manifest, "session-manifest")
    if manifest["state"] != "COMPLETE" or manifest["run_id"] != request["run_id"]:
        raise ValidationFailure("Batch inference accepts only the matching COMPLETE session")


def validate_batch_result(result: dict[str, Any]) -> None:
    validate_document(result, "batch-model-result")
