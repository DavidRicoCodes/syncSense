from __future__ import annotations

import json
import runpy

import pytest

from sync_framework.cli import main


def invoke(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out else None
    return code, payload, captured.err


def test_module_entrypoint(monkeypatch):
    monkeypatch.setattr("sync_framework.cli.main", lambda argv=None: 0)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("sync_framework.__main__", run_name="__main__")
    assert stopped.value.code == 0


def test_cli_end_to_end(inventory_path, profile_path, capsys):
    common = ("--inventory", str(inventory_path), "--format", "json")
    code, planned, _ = invoke(capsys, *common, "experiment", "plan", str(profile_path), "--param", "label=empty", "--param", "duration_s=0.15")
    assert code == 0 and planned["run_id"] == "<generated-at-preflight>"

    code, created, _ = invoke(capsys, *common, "preflight", str(profile_path), "--param", "label=empty", "--param", "duration_s=0.15")
    assert code == 0 and created["state"] == "ARMED"
    run_id = created["run_id"]

    code, dry_start, _ = invoke(capsys, *common, "start", run_id, "--dry-run")
    assert code == 0 and dry_start["mutating"] is False
    code, finalizing, _ = invoke(capsys, *common, "start", run_id)
    assert code == 0 and finalizing["state"] == "FINALIZING"
    code, status, _ = invoke(capsys, *common, "status", run_id)
    assert code == 0 and status["state"] == "FINALIZING"
    code, dry_final, _ = invoke(capsys, *common, "finalize", run_id, "--dry-run")
    assert code == 0 and dry_final["mutating"] is False
    code, manifest, _ = invoke(capsys, *common, "finalize", run_id)
    assert code == 0 and manifest["state"] == "COMPLETE"
    code, recovered, _ = invoke(capsys, *common, "recover", run_id)
    assert code == 0 and recovered["state"] == "COMPLETE"


def test_cli_storage_and_validation_errors(inventory_path, profile_path, capsys):
    common = ("--inventory", str(inventory_path), "--format", "json")
    code, payload, _ = invoke(capsys, *common, "storage", "bootstrap", "--dry-run")
    assert code == 0 and payload["mutating"] is False
    code, payload, error = invoke(capsys, *common, "storage", "bootstrap")
    assert code == 4 and payload is None and "CAPABILITY_DISABLED" in error
    code, payload, error = invoke(capsys, *common, "experiment", "plan", str(profile_path), "--param", "duration_s=1")
    assert code == 2 and payload is None and "VALIDATION_FAILED" in error


def test_cli_composite_run_and_inference_status(inventory_path, profile_path, capsys):
    common = ("--inventory", str(inventory_path), "--format", "json")
    code, result, error = invoke(
        capsys, *common, "experiment", "run", str(profile_path),
        "--param", "label=composite", "--param", "duration_s=0.1", "--inference", "dummy",
    )
    assert code == 0, error
    assert result["dataset_state"] == "COMPLETE" and result["inference_status"] == "SUCCEEDED"
    code, status, error = invoke(capsys, *common, "inference", "status", result["run_id"], result["inference_id"])
    assert code == 0 and status["status"] == "SUCCEEDED", error
    code, retried, error = invoke(capsys, *common, "inference", "run", result["run_id"], "--adapter", "dummy")
    assert code == 0 and retried["status"] == "SUCCEEDED", error


def test_cli_nfs_routes_require_explicit_apply(inventory_path, capsys, monkeypatch):
    common = ("--inventory", str(inventory_path), "--format", "json", "storage")
    monkeypatch.setattr("sync_framework.cli.bootstrap_nfs", lambda inv: {"status": "ready"})
    monkeypatch.setattr("sync_framework.cli.verify_nfs", lambda inv: {"status": "verified"})
    monkeypatch.setattr("sync_framework.cli.teardown_nfs", lambda inv: {"status": "removed"})
    assert invoke(capsys, *common, "bootstrap", "--apply")[1]["status"] == "ready"
    assert invoke(capsys, *common, "verify")[1]["status"] == "verified"
    assert invoke(capsys, *common, "teardown", "--apply")[1]["status"] == "removed"
    code, _, error = invoke(capsys, *common, "teardown")
    assert code == 4 and "CAPABILITY_DISABLED" in error
