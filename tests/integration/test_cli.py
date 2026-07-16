from __future__ import annotations

import json

from sync_framework.cli import main


def invoke(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out else None
    return code, payload, captured.err


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

