from __future__ import annotations

import copy
import base64
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.domain import ProcessFailure, PublicationFailure, ValidationFailure
from sync_framework.orchestration import _ssb_hardware_preflight, make_process_spec
from sync_framework.planning import build_plan
from sync_framework.ssb_smoke import validate_ssb_smoke_outputs
from sync_framework.validation import validate_document


REPO_ROOT = Path(__file__).resolve().parents[2]


def ssb_row(iteration: int = 0) -> dict:
    timestamp_ns = 1_750_000_000_000_000_000 + iteration
    return {
        "protocol_version": 1, "schema": "5g_ssb_rxgrid_jsonl_v1",
        "waveform_type": "5g_ssb", "profile_id": "n78_ssb_30khz",
        "iteration": iteration, "valid": True, "error": "",
        "rx_timestamp_ns": timestamp_ns, "timestamp_unix": timestamp_ns / 1e9,
        "timestamp_utc": datetime.fromtimestamp(timestamp_ns / 1e9, timezone.utc).isoformat(),
        "timestamp_semantics": "host_serialization_time_operational_only",
        "usrp": {"serial": "TEST", "channel": 0, "gain_db": 60.0},
        "center_frequency_hz": 3541.44e6, "sample_rate_hz": 15.36e6,
        "cfo_hz": 123.0, "cfo_correction_enabled": True,
        "feature_name": "rxGridSSB", "feature_dtype": "complex64",
        "feature_shape": [240, 4], "feature_flatten_order": "C", "feature_count": 960,
        "complex_features": [{"real": 1.0, "imag": -1.0}] * 960,
        "numeric_metadata": {
            "nid2": 0, "pss_metric": 0.9, "timing_offset_samples": 1,
            "timing_offset_ms": 0.1, "n_symbols_extracted": 6,
            "rxgrid_mean_abs": 1.0, "rxgrid_median_abs": 1.0,
            "rxgrid_std_abs": 0.1, "rxgrid_max_abs": 2.0,
            "rxgrid_mean_power_db": -10.0, "capture_time_ms": 20.0,
            "pss_time_ms": 1.0, "ofdm_time_ms": 2.0,
            "dsp_time_ms": 3.0, "loop_time_ms": 24.0,
        },
    }


def write_outputs(root: Path, *, valid: int, invalid: int = 0) -> None:
    producer = root / "rx_5g"
    producer.mkdir(parents=True)
    (producer / "rxgridssb.jsonl").write_text(
        "".join(json.dumps(ssb_row(index), allow_nan=False) + "\n" for index in range(valid)),
        encoding="utf-8",
    )
    (producer / "process.log").write_text(
        "=== Final statistics ===\n"
        f"iterations:         {valid + invalid}\n"
        f"valid grids:        {valid}\n"
        f"invalid grids:      {invalid}\n"
        f"JSONL lines written:{valid}\n"
        "output:             rxgridssb.jsonl\n",
        encoding="utf-8",
    )


def test_ssb_schema_and_profile_contract():
    validate_document(ssb_row(), "5g-ssb-rxgrid-row")
    profile = load_profile(REPO_ROOT / "profiles" / "ssb_rx_smoke.yaml")
    parameters = resolve_parameters(profile, {"label": "smoke", "duration_s": "10"})
    assert parameters["min_valid_ssb_rate_hz"] == 10
    assert profile.processes["rx_5g"].stop_signal == "interrupt"
    assert profile.processes["rx_5g"].readiness["pattern"] == "^=== Online loop ===$"
    for duration in (0, 3601):
        with pytest.raises(ValidationFailure):
            resolve_parameters(profile, {"label": "x", "duration_s": str(duration)})


@pytest.mark.parametrize("corruption", ["shape", "count", "samples", "nan", "semantics", "valid", "error"])
def test_ssb_schema_rejects_corruption(corruption):
    row = copy.deepcopy(ssb_row())
    if corruption == "shape":
        row["feature_shape"] = [4, 240]
    elif corruption == "count":
        row["feature_count"] = 959
    elif corruption == "samples":
        row["complex_features"].pop()
    elif corruption == "nan":
        row["cfo_hz"] = math.nan
    elif corruption == "semantics":
        row["timestamp_semantics"] = "acquisition_time"
    elif corruption == "valid":
        row["valid"] = False
    elif corruption == "error":
        row["error"] = "bad"
    if corruption == "nan":
        producer = {"row": row}
        assert math.isnan(producer["row"]["cfo_hz"])
    else:
        with pytest.raises(ValidationFailure):
            validate_document(row, "5g-ssb-rxgrid-row")


def test_ssb_output_accepts_exact_ratio_and_rate_boundaries(tmp_path):
    write_outputs(tmp_path, valid=10, invalid=2)
    summary = validate_ssb_smoke_outputs(tmp_path, duration_s=1, min_valid_ssb_rate_hz=10)
    assert summary["valid_grids"] == 10
    assert summary["valid_rate_hz"] == 10
    root = tmp_path / "exact80"
    write_outputs(root, valid=8, invalid=2)
    assert validate_ssb_smoke_outputs(root, duration_s=1, min_valid_ssb_rate_hz=8)["valid_ratio"] == 0.8


@pytest.mark.parametrize("corruption", ["truncated", "order", "stats", "ratio", "rate", "uhd", "timestamp"])
def test_ssb_outputs_reject_corruption(tmp_path, corruption):
    write_outputs(tmp_path, valid=10, invalid=0)
    data = tmp_path / "rx_5g" / "rxgridssb.jsonl"
    log = tmp_path / "rx_5g" / "process.log"
    if corruption == "truncated":
        data.write_bytes(data.read_bytes()[:-1])
    elif corruption == "order":
        rows = data.read_text().splitlines()
        rows[1] = json.dumps(ssb_row(0))
        data.write_text("\n".join(rows) + "\n")
    elif corruption == "stats":
        log.write_text(log.read_text().replace("valid grids:        10", "valid grids:        9"))
    elif corruption == "ratio":
        log.write_text(log.read_text().replace("iterations:         10", "iterations:         13").replace("invalid grids:      0", "invalid grids:      3"))
    elif corruption == "rate":
        pass
    elif corruption == "uhd":
        log.write_text(log.read_text() + "UHD RX error: overflow\n")
    elif corruption == "timestamp":
        rows = data.read_text().splitlines()
        value = json.loads(rows[0])
        value["timestamp_semantics"] = "wrong"
        rows[0] = json.dumps(value)
        data.write_text("\n".join(rows) + "\n")
    with pytest.raises(PublicationFailure):
        validate_ssb_smoke_outputs(
            tmp_path,
            duration_s=2 if corruption == "rate" else 1,
            min_valid_ssb_rate_hz=10,
        )


def test_ssb_outputs_reject_missing_files_and_nonfinite_json(tmp_path):
    with pytest.raises(PublicationFailure, match="Cannot read"):
        validate_ssb_smoke_outputs(tmp_path, 1, 1)
    write_outputs(tmp_path, valid=1)
    row = ssb_row()
    row["cfo_hz"] = math.nan
    (tmp_path / "rx_5g" / "rxgridssb.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(PublicationFailure, match="non-finite"):
        validate_ssb_smoke_outputs(tmp_path, 1, 1)
    (tmp_path / "rx_5g" / "process.log").write_text("iterations: 1\n")
    with pytest.raises(PublicationFailure, match="lacks final statistic"):
        validate_ssb_smoke_outputs(tmp_path, 1, 1)


def test_ssb_process_spec_and_preflight_are_simulable(tmp_path, monkeypatch):
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.5g-smoke.example.yaml")
    profile = load_profile(REPO_ROOT / "profiles" / "ssb_rx_smoke.yaml")
    parameters = resolve_parameters(profile, {"label": "x", "duration_s": "10"})
    run_dir = tmp_path / "runs" / "run_mock"
    (run_dir / ".control").mkdir(parents=True)
    (run_dir / "rx_5g").mkdir()
    plan = build_plan(inventory, profile, parameters, run_id="run_mock", run_dir=run_dir)
    spec = make_process_spec(plan, "rx_5g")
    assert spec.worker_config["stop_signal"] == "interrupt"
    assert spec.worker_config["readiness_regex"] == "^=== Online loop ===$"
    calls = []

    def fake_ssh(config, argv, **kwargs):
        calls.append(argv)
        if argv[:1] == ["uhd_find_devices"]:
            return subprocess.CompletedProcess(argv, 0, f"serial: {argv[-1].split('=', 1)[1]}\n", "")
        if argv[-1:] == ["--help"]:
            return subprocess.CompletedProcess(argv, 0, "usage\n", "matplotlib warning\n")
        if argv[1:2] == ["-c"]:
            return subprocess.CompletedProcess(
                argv, 0,
                json.dumps({"python": "3.10", "numpy": "1", "scipy": "1", "h5py": "1", "matplotlib": "1", "uhd": "unknown"}) + "\n",
                "compatibility warning\n",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", fake_ssh)
    _ssb_hardware_preflight(plan)
    environment = json.loads((run_dir / ".control" / "ssb-environment.json").read_text())
    assert environment["warnings"] == ["compatibility warning"]
    assert any(argv[-1:] == ["--help"] for argv in calls)
    assert any(argv[:1] == ["uhd_find_devices"] for argv in calls)

    def no_device(config, argv, **kwargs):
        result = fake_ssh(config, argv, **kwargs)
        if argv[:1] == ["uhd_find_devices"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return result

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", no_device)
    with pytest.raises(ProcessFailure, match="not discovered"):
        _ssb_hardware_preflight(plan)


def test_real_worker_maps_control_term_to_child_interrupt(tmp_path):
    output = tmp_path / "rx_5g"
    child_code = (
        "import signal,sys,time;"
        "signal.signal(signal.SIGINT,lambda *_:(print('GOT_SIGINT',flush=True),sys.exit(0)));"
        "print('=== Online loop ===',flush=True);"
        "time.sleep(60)"
    )
    spec = {
        "run_id": "run_test", "producer_id": "rx_5g", "node_id": "pc3pc4",
        "output_dir": str(output), "argv": [sys.executable, "-c", child_code],
        "cwd": str(tmp_path), "artifacts": ["process.log"], "safety_class": "dsp",
        "stop_signal": "interrupt", "readiness_regex": "^=== Online loop ===$",
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(spec, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    worker = subprocess.Popen([
        sys.executable, str(REPO_ROOT / "tools" / "remote_process_worker.py"),
        "run", "--spec", encoded,
    ])
    deadline = time.monotonic() + 5
    while not (output / "runtime" / "status.json").is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (output / "runtime" / "status.json").is_file()
    os.kill(worker.pid, signal.SIGTERM)
    assert worker.wait(timeout=5) == 0
    assert "GOT_SIGINT" in (output / "process.log").read_text()
    receipt = json.loads((output / "producer-result.json").read_text())
    assert receipt["exit_code"] == 0
