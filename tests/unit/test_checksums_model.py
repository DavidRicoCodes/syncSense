from __future__ import annotations

import json

import pytest

from sync_framework.checksums import sha256_file
from sync_framework.domain import PublicationFailure, ValidationFailure
from sync_framework.model_adapter import validate_batch_request
from sync_framework.storage import atomic_write_json


def test_checksum_rejects_symlink(tmp_path):
    source = tmp_path / "source"
    source.write_text("data", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(PublicationFailure):
        sha256_file(link)


def test_batch_request_rejects_non_complete_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    atomic_write_json(path, {"state": "FAILED"})
    request = {
        "schema_version": "1.0.0", "inference_id": "i1", "run_id": "r1", "session_manifest_path": str(path),
        "session_manifest_sha256": sha256_file(path), "adapter_id": "external", "adapter_version": "1",
        "config_digest": "0" * 64, "output_dir": str(tmp_path / "out"),
    }
    with pytest.raises(ValidationFailure):
        validate_batch_request(request)

