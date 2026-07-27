from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sync_framework.association import (
    SEMANTICS,
    _association_inputs,
    _load_clock,
    _read_jsonl as read_association_jsonl,
    association_status,
    run_nearest_ntp_association,
)
from sync_framework.checksums import sha256_file
from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.domain import AssociationFailure, PublicationFailure, ValidationFailure
from sync_framework.nosync_passive import (
    canonical_position,
    global_timeout_s,
    validate_5g_outputs,
    validate_n310_outputs,
)
from sync_framework.inference import DummyBatchModelAdapter, run_dummy_inference
from sync_framework.orchestration import (
    _hardware_preflight,
    _prepare_wifi_config,
    make_process_spec,
)
from sync_framework.planning import build_plan, experiment_id_for_run
from sync_framework.publication import build_producer_manifest
from sync_framework.validation import validate_document


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER = REPO_ROOT / "tools" / "remote_process_worker.py"


def _hssb_row(iteration: int, ticks: int) -> dict:
    binary = {
        "path": "rxgrid.cf32",
        "path_semantics": "relative_to_jsonl_directory",
        "byte_offset": iteration * 7680,
        "dtype": "complex64",
        "shape": [240, 4],
        "flatten_order": "C",
    }
    row = {
        "schema": "joint_5g_hssb_jsonl_v1",
        "protocol_version": 1,
        "waveform_type": "5g_ssb",
        "profile_id": "n78_ssb_30khz",
        "iteration": iteration,
        "valid": True,
        "error": "",
        "clock_domain": "local_device_epoch",
        "block_start_device_ticks": ticks,
        "detector_offset_samples": 10,
        "event_device_ticks": ticks + 10,
        "device_tick_rate_hz": 15_360_000,
        "reference_point": "ssb_pss_start",
        "canonical_timestamp_semantics": (
            "local_usrp_device_time_first_sample_plus_detector_offset_samples"
        ),
        "event_monotonic_ns": 1_000_000_000 + iteration * 20_000_000,
        "event_unix_ns": 2_000_000_000 + iteration * 20_000_000,
        "timestamp_unix": 2.0 + iteration * 0.02,
        "timestamp_utc": "1970-01-01T00:00:02+00:00",
        "timestamp_semantics": (
            "host_operational_estimate_only_from_block_end_and_detector_offset; "
            "cross_host_use_requires_ntp_anchors_and_is_not_acquisition_alignment"
        ),
        "clock_anchor_schema": "rx_host_clock_anchor_v1",
        "clock_anchor_uncertainty_ns": 100,
        "capture_call_started_monotonic_ns": 1,
        "capture_finished_monotonic_ns": 2,
        "capture_finished_unix_ns": 3,
        "center_frequency_hz": 3_541_440_000.0,
        "sample_rate_hz": 15_360_000.0,
        "cfo_hz": 0.0,
        "usrp": {"channel": 0, "gain_db": 60.0},
        "feature_name": "hSSB",
        "feature_dtype": "complex64",
        "feature_shape": [240, 4],
        "feature_flatten_order": "C",
        "feature_count": 960,
        "feature_storage": "hssb_binary",
        "raw_rxgrid": binary,
        "hssb_binary": {**binary, "path": "hssb.cf32"},
        "numeric_metadata": {},
    }
    validate_document(row, "5g-hssb-row")
    return row


def test_real_profile_has_separated_roles_and_exact_clock_contract() -> None:
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive.yaml")
    parameters = resolve_parameters(
        profile,
        {"label": "real", "position": "r5c6", "num_beacons": "200"},
    )
    assert parameters["position"] == "R5C6"
    assert parameters["detector_threshold"] == 0.70
    assert {
        producer: definition.node_id
        for producer, definition in profile.processes.items()
    } == {"rx_5g": "pc1", "rx_wifi": "pc2", "tx_wifi": "pc3pc4"}
    assert profile.clock_relationships == (
        {
            "left": "pc1_5g_b210_acquisition",
            "right": "pc2_wifi_b210_acquisition",
            "relation": "not_comparable",
            "reason": "separate_receivers_without_common_time_or_frequency_reference",
        },
    )
    assert global_timeout_s(parameters) == pytest.approx(
        90 + 200 * 0.1024 + 3 + 2 + 10 + 30
    )
    assert 1 <= experiment_id_for_run("run_test") <= 0xFFFF
    assert experiment_id_for_run("run_test") == experiment_id_for_run("run_test")


def test_position_validation_is_bounded() -> None:
    assert canonical_position("r8c7", "testbed1") == "R8C7"
    with pytest.raises(ValidationFailure, match="blocked"):
        canonical_position("R3C4", "testbed1")
    with pytest.raises(ValidationFailure, match="rows 2..8"):
        canonical_position("R9C7", "testbed1")
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive.yaml")
    with pytest.raises(ValidationFailure, match="must be one of"):
        resolve_parameters(
            profile,
            {
                "label": "invalid-enum",
                "position": "R5C6",
                "condition": "not-a-condition",
            },
        )


def test_nosync_worker_specs_create_events_only_from_local_device_ticks() -> None:
    inventory = load_inventory(
        REPO_ROOT / "config" / "inventory.nosync-passive.example.yaml"
    )
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive.yaml")
    parameters = resolve_parameters(
        profile, {"label": "contract", "position": "R5C6"}
    )
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id="run_mock",
        run_dir=Path("/srv/sync-experiments/runs/run_mock"),
    )
    wifi = make_process_spec(plan, "rx_wifi").worker_config
    ssb = make_process_spec(plan, "rx_5g").worker_config
    assert wifi and ssb
    assert wifi["event_contract"]["source_path"] == "frame-timings.jsonl"
    assert wifi["event_contract"]["clock_domain_id"] == "pc2_wifi_b210_acquisition"
    assert wifi["event_contract"]["block_ticks_field"] == "block_start_device_ticks"
    assert wifi["event_contract"]["event_ticks_field"] == "event_device_ticks"
    assert wifi["event_contract"]["tick_rate_field"] == "device_tick_rate_hz"
    assert ssb["event_contract"]["clock_domain_id"] == "pc1_5g_b210_acquisition"
    assert wifi["capture_host_time"] is True
    assert ssb["capture_host_time"] is True


def test_worker_event_builder_rejects_host_time_as_canonical(tmp_path) -> None:
    name = "remote_process_worker_test"
    spec = importlib.util.spec_from_file_location(name, WORKER)
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    sys.modules[name] = worker
    spec.loader.exec_module(worker)
    output = tmp_path / "rx_wifi"
    output.mkdir()
    timing = {
        "block_start_device_ticks": 100,
        "detector_offset_samples": 7,
        "event_device_ticks": 107,
        "device_tick_rate_hz": 20_000_000,
        "event_monotonic_ns": 999,
    }
    (output / "frame-timings.jsonl").write_text(
        json.dumps(timing) + "\n", encoding="utf-8"
    )
    contract = {
        "run_id": "run_test",
        "producer_id": "rx_wifi",
        "node_id": "pc2",
        "output_dir": str(output),
        "argv": [],
        "cwd": str(tmp_path),
        "artifacts": [],
        "safety_class": "dsp",
        "event_contract": {
            "source_path": "frame-timings.jsonl",
            "output_path": "events.jsonl",
            "artifact_id": "rx_wifi_features",
            "modality": "wifi",
            "frame_type": "wifi_beacon",
            "clock_domain_id": "pc2_wifi_b210_acquisition",
            "reference_point": "wifi_ppdu_start",
            "block_ticks_field": "block_start_device_ticks",
            "offset_field": "detector_offset_samples",
            "event_ticks_field": "event_device_ticks",
            "tick_rate_field": "device_tick_rate_hz",
        },
    }
    worker.build_events(contract, output)
    event = json.loads((output / "events.jsonl").read_text(encoding="utf-8"))
    assert event["timestamp"]["ticks"] == 107
    assert "host_observed_at" not in event
    timing["event_device_ticks"] = timing["event_monotonic_ns"]
    (output / "frame-timings.jsonl").write_text(
        json.dumps(timing) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="canonical device timestamp"):
        worker.build_events(contract, output)


def test_5g_and_n310_closure_validation(tmp_path) -> None:
    run = tmp_path / "run"
    rx = run / "rx_5g"
    tx = run / "tx_wifi"
    rx.mkdir(parents=True)
    tx.mkdir()
    rows = [_hssb_row(index, index * 307_200) for index in range(10)]
    (rx / "hssb.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    payload = b"\0" * (10 * 240 * 4 * 8)
    (rx / "rxgrid.cf32").write_bytes(payload)
    (rx / "hssb.cf32").write_bytes(payload)
    (rx / "process.log").write_text(
        "SUMMARY | captured=10 | valid=10 | invalid=0 | capture_errors=0\n",
        encoding="utf-8",
    )
    summary = validate_5g_outputs(
        run,
        duration_s=1,
        minimum_valid_ratio=0.8,
        minimum_valid_rate_hz=10,
    )
    assert summary["valid_grids"] == 10
    (tx / "state.json").write_text(
        json.dumps(
            {
                "sent_packets": 200,
                "total_zero_sends": 0,
                "async_event_counts": {"burst_ack": 200},
                "tx_strategy": "timed",
            }
        ),
        encoding="utf-8",
    )
    assert validate_n310_outputs(run, 200)["sent_packets"] == 200
    rows[1]["event_device_ticks"] += 1
    (rx / "hssb.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(PublicationFailure):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=10,
        )


def test_nearest_ntp_is_retryable_and_never_compares_device_ticks(
    tmp_path, monkeypatch
) -> None:
    run = tmp_path / "run_test"
    run.mkdir()
    (run / "manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "sync_framework.association.verify_published_manifest",
        lambda _run: {
            "run_id": "run_test",
            "profile": {"profile_id": "nosync_passive"},
        },
    )
    monkeypatch.setattr(
        "sync_framework.association._association_inputs",
        lambda _run: (
            [
                {
                    "row_index": 0,
                    "device_ticks": 9_000_000_000,
                    "tick_rate_hz": 20_000_000,
                    "estimated_utc_ns": 1_000_000_000,
                }
            ],
            [
                {
                    "row_index": 0,
                    "device_ticks": 1,
                    "tick_rate_hz": 15_360_000,
                    "estimated_utc_ns": 1_010_000_000,
                }
            ],
            500,
        ),
    )
    result = run_nearest_ntp_association(run, 30)
    pair = json.loads(
        (
            Path(result["association_path"]) / "pairs.jsonl"
        ).read_text(encoding="utf-8")
    )
    assert pair["delta_t_ms"] == 10
    assert pair["semantics"] == SEMANTICS
    assert pair["wifi"]["device_ticks"] > pair["ssb_5g"]["device_ticks"]
    assert association_status(run, result["association_id"])["status"] == "SUCCEEDED"

    monkeypatch.setattr(
        "sync_framework.association._association_inputs",
        lambda _run: (_ for _ in ()).throw(AssociationFailure("NTP unavailable")),
    )
    with pytest.raises(AssociationFailure, match="NTP unavailable"):
        run_nearest_ntp_association(run, 30)
    statuses = association_status(run)["associations"]
    assert {item["status"] for item in statuses} == {"SUCCEEDED", "FAILED"}


def _write_operational_clock(producer: Path) -> None:
    producer.mkdir(parents=True, exist_ok=True)
    anchors = [
        {
            "schema_version": "1.0.0",
            "phase": phase,
            "monotonic_ns": monotonic,
            "realtime_ns": monotonic + 10_000_000_000,
            "sampling_uncertainty_ns": 100,
            "semantics": "operational_host_clock_anchor_not_acquisition_time",
        }
        for phase, monotonic in (("start", 0), ("end", 1_000_000_000))
    ]
    (producer / "host-clock-anchors.jsonl").write_text(
        "".join(json.dumps(anchor) + "\n" for anchor in anchors),
        encoding="utf-8",
    )
    ntp = {
        "schema_version": "1.0.0",
        "semantics": "operational_host_discipline_not_acquisition_sync",
        "samples": [
            {
                "schema_version": "1.0.0",
                "phase": phase,
                "observed_realtime_ns": 10_000_000_000 + index,
                "synchronized": True,
                "stratum": 2,
                "root_delay_ms": 0.1,
                "root_dispersion_ms": 0.2,
                "system_jitter_ms": 0.1,
            }
            for index, phase in enumerate(("start", "end"))
        ],
    }
    (producer / "ntp-status.json").write_text(
        json.dumps(ntp) + "\n", encoding="utf-8"
    )


def test_full_association_projects_each_host_clock_independently(
    tmp_path, monkeypatch
) -> None:
    run = tmp_path / "run_full"
    _write_operational_clock(run / "rx_wifi")
    _write_operational_clock(run / "rx_5g")
    wifi = {
        "rx_timestamp_ticks": 1,
        "rx_tick_rate_hz": 20_000_000,
    }
    timing = {
        "event_monotonic_ns": 500_000_000,
        "event_monotonic_semantics": (
            "operational_host_estimate_only_not_acquisition_time"
        ),
    }
    (run / "rx_wifi" / "features.jsonl").write_text(
        json.dumps(wifi) + "\n", encoding="utf-8"
    )
    (run / "rx_wifi" / "frame-timings.jsonl").write_text(
        json.dumps(timing) + "\n", encoding="utf-8"
    )
    ssb = _hssb_row(0, 0)
    ssb["event_monotonic_ns"] = 510_000_000
    (run / "rx_5g" / "hssb.jsonl").write_text(
        json.dumps(ssb) + "\n", encoding="utf-8"
    )
    (run / "manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "sync_framework.association.verify_published_manifest",
        lambda _run: {
            "run_id": "run_full",
            "profile": {"profile_id": "nosync_passive"},
        },
    )
    result = run_nearest_ntp_association(run, 30)
    summary = json.loads(
        (Path(result["association_path"]) / "summary.json").read_text()
    )
    assert summary["matched_pairs"] == 1
    assert summary["mean_absolute_delta_ms"] == 10

    ntp_path = run / "rx_wifi" / "ntp-status.json"
    ntp = json.loads(ntp_path.read_text())
    ntp["samples"][1]["synchronized"] = False
    ntp_path.write_text(json.dumps(ntp) + "\n", encoding="utf-8")
    with pytest.raises(AssociationFailure, match="not synchronized"):
        run_nearest_ntp_association(run, 30)


def test_dummy_inference_accepts_only_closed_association(
    tmp_path, monkeypatch
) -> None:
    run = tmp_path / "run_inference"
    run.mkdir()
    manifest_path = run / "manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    producer_values = {
        "rx_wifi": {
            "producer_id": "rx_wifi",
            "event_summary": {"count": 1},
            "artifacts": [
                {
                    "artifact_type": "wifi_csi_feature_rows",
                    "row_count": 1,
                }
            ],
        },
        "rx_5g": {
            "producer_id": "rx_5g",
            "event_summary": {"count": 1},
            "artifacts": [
                {"artifact_type": "5g_hssb_rows", "row_count": 2}
            ],
        },
    }
    producers = []
    for producer_id, value in producer_values.items():
        path = run / producer_id / "producer-manifest.json"
        path.parent.mkdir()
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        producers.append(
            {
                "producer_id": producer_id,
                "manifest_path": f"{producer_id}/producer-manifest.json",
            }
        )
    manifest = {
        "run_id": "run_inference",
        "state": "COMPLETE",
        "profile": {"profile_id": "nosync_passive"},
        "parameters": {"num_beacons": 1},
        "producers": producers,
    }
    monkeypatch.setattr(
        "sync_framework.inference.verify_published_manifest",
        lambda _run: manifest,
    )
    association = run / "associations" / "assoc_test"
    association.mkdir(parents=True)
    for name, value in (
        ("request.json", {}),
        ("state.json", {}),
        ("pairs.jsonl", {}),
        (
            "summary.json",
            {
                "matched_pairs": 1,
                "semantics": SEMANTICS,
            },
        ),
    ):
        (association / name).write_text(json.dumps(value) + "\n")
    association_manifest = {
        "schema_version": "1.0.0",
        "association_id": "assoc_test",
        "run_id": "run_inference",
        "status": "COMPLETE",
        "adapter": "nearest-ntp",
        "semantics": SEMANTICS,
        "files": [
            {"path": name, "sha256": sha256_file(association / name)}
            for name in (
                "request.json",
                "state.json",
                "pairs.jsonl",
                "summary.json",
            )
        ],
    }
    (association / "manifest.json").write_text(
        json.dumps(association_manifest) + "\n"
    )
    output = run / "inference" / "inf_test"
    output.mkdir(parents=True)
    request = {
        "schema_version": "1.0.0",
        "inference_id": "inf_test",
        "run_id": "run_inference",
        "session_manifest_path": "manifest.json",
        "session_manifest_sha256": sha256_file(manifest_path),
        "adapter": {
            "adapter_id": "dummy",
            "adapter_version": "1.0.0",
            "config_digest": "0" * 64,
        },
        "output_directory": "inference/inf_test",
        "derived_input": {
            "kind": "association",
            "manifest_path": "associations/assoc_test/manifest.json",
            "manifest_sha256": sha256_file(association / "manifest.json"),
        },
    }
    result = DummyBatchModelAdapter().run(request, run_dir=run)
    assert result["status"] == "SUCCEEDED"
    summary = json.loads((output / "summary.json").read_text())
    assert summary["nosync_passive"]["association"]["matched_pairs"] == 1

    monkeypatch.setattr(
        "sync_framework.inference.DummyBatchModelAdapter.run",
        lambda self, request, run_dir: {
            "schema_version": "1.0.0",
            "inference_id": request["inference_id"],
            "run_id": "run_inference",
            "status": "SUCCEEDED",
            "adapter": {"adapter_id": "dummy", "adapter_version": "1.0.0"},
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
            "inputs": ["manifest.json"],
            "outputs": [],
            "artifacts": [],
            "error": None,
        },
    )
    composed = run_dummy_inference(run, "assoc_test")
    assert composed["status"] == "SUCCEEDED"


def test_real_hardware_preflight_is_fully_mockable(tmp_path, monkeypatch) -> None:
    inventory = load_inventory(
        REPO_ROOT / "config" / "inventory.nosync-passive.example.yaml"
    )
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive.yaml")
    parameters = resolve_parameters(
        profile,
        {"label": "preflight", "position": "R5C6", "num_beacons": "200"},
    )
    run_dir = tmp_path / "runs" / "run_mock"
    for producer in profile.processes:
        (run_dir / producer / "runtime").mkdir(parents=True)
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id="run_mock",
        run_dir=run_dir,
    )
    _prepare_wifi_config(plan, REPO_ROOT)
    local_script = (
        REPO_ROOT
        / "modulos_rx_tx"
        / "src/python/fusion_dataset/online_5g_hssb_jsonl_parallel.py"
    )
    local_digest = sha256_file(local_script)
    calls = []

    def fake_ssh(_config, argv, **_kwargs):
        calls.append(argv)
        if argv[0] == "strings":
            return subprocess.CompletedProcess(
                argv,
                0,
                "local_usrp_device_time_first_sample_plus_"
                "detector_offset_samples\n",
                "",
            )
        if argv[0] == "sha256sum":
            return subprocess.CompletedProcess(argv, 0, local_digest + "  file\n", "")
        if argv[0] == "uhd_find_devices":
            return subprocess.CompletedProcess(
                argv,
                0,
                (
                    "N310\n"
                    if "type=n3xx" in argv[-1]
                    else f"serial: {argv[-1].split('serial=', 1)[-1]}\n"
                ),
                "",
            )
        if "-c" in argv and any("import json,numpy" in item for item in argv):
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "python": "3.10",
                        "python_executable": argv[0],
                        "numpy": "1",
                        "scipy": "1",
                        "h5py": "1",
                        "matplotlib": "1",
                        "uhd": "4",
                        "uhd_module_path": "/usr/local/lib/python3.10/site-packages/uhd",
                        "uhd_has_multi_usrp": True,
                    }
                )
                + "\n",
                "known warning\n",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("sync_framework.orchestration.run_ssh", fake_ssh)
    _hardware_preflight(plan)
    environment = json.loads(
        (run_dir / ".control" / "ssb-environment.json").read_text()
    )
    assert environment["remote_script_sha256"] == local_digest
    assert environment["warnings"] == ["known warning"]
    assert any(argv[0] == "strings" for argv in calls)


def test_real_producer_manifests_record_rows_and_canonical_events(
    tmp_path, monkeypatch
) -> None:
    inventory = load_inventory(
        REPO_ROOT / "config" / "inventory.nosync-passive.example.yaml"
    )
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive.yaml")
    parameters = resolve_parameters(
        profile, {"label": "publish", "position": "R5C6", "num_beacons": "2"}
    )
    run_dir = tmp_path / "run_mock"
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id="run_mock",
        run_dir=run_dir,
    )
    for producer_id, resolved in plan.processes.items():
        resolved.producer_dir.mkdir(parents=True)
        for artifact in resolved.definition.expected_artifacts:
            path = resolved.producer_dir / artifact.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
    monkeypatch.setattr(
        "sync_framework.publication._validate_remote_receipt",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "sync_framework.publication.validate_wifi_smoke_outputs",
        lambda *_args, **_kwargs: {"frames_received": 2},
    )
    monkeypatch.setattr(
        "sync_framework.publication.validate_5g_outputs",
        lambda *_args, **_kwargs: {"valid_grids": 3},
    )
    monkeypatch.setattr(
        "sync_framework.publication.validate_n310_outputs",
        lambda *_args, **_kwargs: {"sent_packets": 2},
    )
    monkeypatch.setattr(
        "sync_framework.publication._validate_operational_time_artifacts",
        lambda *_args: None,
    )
    state = {
        "profile": {"parameters": parameters},
        "operational_window": {
            "producer_active_duration_s": {"rx_5g": 1.0}
        },
        "processes": {
            producer_id: {
                "status": "stopped",
                "exit_code": 0,
                "handle": {"backend": "ssh"},
                "started_at": "2026-01-01T00:00:00+00:00",
                "stopped_at": "2026-01-01T00:00:01+00:00",
                "termination_reason": "completed",
            }
            for producer_id in plan.processes
        },
    }
    wifi = build_producer_manifest(plan, state, "rx_wifi")
    ssb = build_producer_manifest(plan, state, "rx_5g")
    tx = build_producer_manifest(plan, state, "tx_wifi")
    wifi_rows = next(
        item for item in wifi["artifacts"] if item["artifact_id"] == "rx_wifi_features"
    )
    ssb_rows = next(
        item for item in ssb["artifacts"] if item["artifact_id"] == "rx_5g_hssb"
    )
    assert wifi_rows["row_count"] == 2
    assert ssb_rows["row_count"] == 3
    assert ssb_rows["schema_ref"] == "urn:sync:schema:v1:5g-hssb-row"
    assert tx["clock_domain_ids"] == []


def test_nosync_validation_failure_edges(tmp_path) -> None:
    assert canonical_position("north-1", "future") == "NORTH-1"
    with pytest.raises(ValidationFailure, match="unsafe"):
        canonical_position("north west", "future")
    run = tmp_path / "run"
    rx = run / "rx_5g"
    tx = run / "tx_wifi"
    rx.mkdir(parents=True)
    tx.mkdir()
    (rx / "hssb.jsonl").write_text("{}")
    (rx / "process.log").write_text("")
    with pytest.raises(PublicationFailure, match="truncated"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=10,
        )
    (rx / "hssb.jsonl").write_text(json.dumps(_hssb_row(0, 0)) + "\n")
    (rx / "process.log").write_text("UHD RX error\n")
    with pytest.raises(PublicationFailure, match="UHD RX error"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=10,
        )
    (tx / "state.json").write_text(
        json.dumps(
            {
                "sent_packets": 1,
                "total_zero_sends": 1,
                "async_event_counts": {"underflow": 1},
            }
        )
    )
    with pytest.raises(PublicationFailure, match="did not close"):
        validate_n310_outputs(run, 2)


def test_association_defensive_failures(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(AssociationFailure, match="Cannot read"):
        read_association_jsonl(missing)
    missing.write_text("{}")
    with pytest.raises(AssociationFailure, match="Truncated"):
        read_association_jsonl(missing)
    missing.write_text("{broken}\n")
    with pytest.raises(AssociationFailure, match="Invalid association input"):
        read_association_jsonl(missing)

    run = tmp_path / "run_bad_clock"
    _write_operational_clock(run / "rx_wifi")
    anchors_path = run / "rx_wifi" / "host-clock-anchors.jsonl"
    anchors = [
        json.loads(line) for line in anchors_path.read_text().splitlines()
    ]
    anchors.reverse()
    anchors_path.write_text(
        "".join(json.dumps(item) + "\n" for item in anchors)
    )
    with pytest.raises(AssociationFailure, match="anchor phases"):
        _load_clock(run, "rx_wifi")
    anchors.reverse()
    anchors[1]["monotonic_ns"] = anchors[0]["monotonic_ns"]
    anchors_path.write_text(
        "".join(json.dumps(item) + "\n" for item in anchors)
    )
    with pytest.raises(AssociationFailure, match="Non-increasing"):
        _load_clock(run, "rx_wifi")
    anchors[1]["monotonic_ns"] = 1_000_000_000
    anchors_path.write_text(
        "".join(json.dumps(item) + "\n" for item in anchors)
    )
    (run / "rx_wifi" / "ntp-status.json").write_text("{broken}\n")
    with pytest.raises(AssociationFailure, match="Invalid NTP"):
        _load_clock(run, "rx_wifi")

    monkeypatch.setattr(
        "sync_framework.association.verify_published_manifest",
        lambda _run: {
            "run_id": "run_bad_clock",
            "profile": {"profile_id": "other"},
        },
    )
    (run / "manifest.json").write_text("{}\n")
    with pytest.raises(AssociationFailure, match="only accepts"):
        run_nearest_ntp_association(run, 30)
    with pytest.raises(AssociationFailure, match="Unknown association"):
        association_status(run, "missing")


def test_association_failure_and_unmatched_paths(tmp_path, monkeypatch) -> None:
    run = tmp_path / "run_paths"
    run.mkdir()
    (run / "manifest.json").write_text("{}\n")
    monkeypatch.setattr(
        "sync_framework.association.verify_published_manifest",
        lambda _run: {
            "run_id": "run_paths",
            "profile": {"profile_id": "nosync_passive"},
        },
    )
    monkeypatch.setattr(
        "sync_framework.association._association_inputs",
        lambda _run: ([], [], 0),
    )
    with pytest.raises(AssociationFailure, match="No valid SSB"):
        run_nearest_ntp_association(run, 30)

    monkeypatch.setattr(
        "sync_framework.association._association_inputs",
        lambda _run: (
            [
                {
                    "row_index": 0,
                    "device_ticks": 1,
                    "tick_rate_hz": 1,
                    "estimated_utc_ns": 1,
                }
            ],
            [
                {
                    "row_index": 0,
                    "device_ticks": 2,
                    "tick_rate_hz": 1,
                    "estimated_utc_ns": 1_000_000_000,
                }
            ],
            0,
        ),
    )
    unmatched = run_nearest_ntp_association(run, 1)
    summary = json.loads(
        (Path(unmatched["association_path"]) / "summary.json").read_text()
    )
    assert summary["matched_pairs"] == 0

    monkeypatch.setattr(
        "sync_framework.association._association_inputs",
        lambda _run: (_ for _ in ()).throw(ValueError("unexpected")),
    )
    with pytest.raises(AssociationFailure, match="Association failed"):
        run_nearest_ntp_association(run, 30)


def test_association_detects_raw_manifest_change(tmp_path, monkeypatch) -> None:
    run = tmp_path / "run_changed"
    run.mkdir()
    manifest_path = run / "manifest.json"
    manifest_path.write_text("{}\n")
    monkeypatch.setattr(
        "sync_framework.association.verify_published_manifest",
        lambda _run: {
            "run_id": "run_changed",
            "profile": {"profile_id": "nosync_passive"},
        },
    )
    monkeypatch.setattr(
        "sync_framework.association._association_inputs",
        lambda _run: (
            [],
            [
                {
                    "row_index": 0,
                    "device_ticks": 1,
                    "tick_rate_hz": 1,
                    "estimated_utc_ns": 1,
                }
            ],
            0,
        ),
    )
    real_sha256 = sha256_file
    manifest_calls = 0

    def changing_sha256(path: Path) -> str:
        nonlocal manifest_calls
        if path == manifest_path:
            manifest_calls += 1
            return ("a" if manifest_calls == 1 else "b") * 64
        return real_sha256(path)

    monkeypatch.setattr("sync_framework.association.sha256_file", changing_sha256)
    with pytest.raises(AssociationFailure, match="Raw session changed"):
        run_nearest_ntp_association(run, 30)


def test_more_nosync_output_failure_modes(tmp_path, monkeypatch) -> None:
    run = tmp_path / "run_more"
    rx = run / "rx_5g"
    tx = run / "tx_wifi"
    rx.mkdir(parents=True)
    tx.mkdir()
    with pytest.raises(PublicationFailure, match="Cannot read"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )
    row = _hssb_row(0, 0)
    (rx / "hssb.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(PublicationFailure, match="process log"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )
    (rx / "process.log").write_text("no summary\n")
    with pytest.raises(PublicationFailure, match="lacks final"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )
    (rx / "process.log").write_text(
        "SUMMARY | captured=2 | valid=1 | invalid=0 | capture_errors=0\n"
    )
    with pytest.raises(PublicationFailure, match="counters"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )
    (rx / "process.log").write_text(
        "SUMMARY | captured=1 | valid=1 | invalid=0 | capture_errors=0\n"
    )
    (rx / "rxgrid.cf32").write_bytes(b"")
    (rx / "hssb.cf32").write_bytes(b"")
    with pytest.raises(PublicationFailure, match="binary size"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )
    (rx / "rxgrid.cf32").write_bytes(b"\0" * 7680)
    (rx / "hssb.cf32").write_bytes(b"\0" * 7680)
    with pytest.raises(PublicationFailure, match="rate is too low"):
        validate_5g_outputs(
            run,
            duration_s=2,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )
    with pytest.raises(PublicationFailure, match="state is missing"):
        validate_n310_outputs(run, 1)


@pytest.mark.parametrize("payload", [b"{broken}\n", b"[]\n"])
def test_5g_jsonl_rejects_invalid_json_and_non_object_rows(
    tmp_path, payload: bytes
) -> None:
    run = tmp_path / "run_invalid_jsonl"
    producer = run / "rx_5g"
    producer.mkdir(parents=True)
    (producer / "hssb.jsonl").write_bytes(payload)
    with pytest.raises(PublicationFailure, match="Invalid 5G HSSB"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )


def _write_5g_closure(
    run: Path,
    rows: list[dict],
    *,
    captured: int | None = None,
    invalid: int = 0,
) -> None:
    producer = run / "rx_5g"
    producer.mkdir(parents=True, exist_ok=True)
    (producer / "hssb.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    valid = len(rows)
    captured = valid + invalid if captured is None else captured
    (producer / "process.log").write_text(
        f"SUMMARY | captured={captured} | valid={valid} | "
        f"invalid={invalid} | capture_errors=0\n",
        encoding="utf-8",
    )
    payload = b"\0" * (valid * 240 * 4 * 8)
    (producer / "rxgrid.cf32").write_bytes(payload)
    (producer / "hssb.cf32").write_bytes(payload)


def test_5g_schema_monotonicity_and_ratio_failures(tmp_path) -> None:
    run = tmp_path / "run_schema"
    invalid_schema = _hssb_row(0, 0)
    del invalid_schema["profile_id"]
    _write_5g_closure(run, [invalid_schema])
    with pytest.raises(PublicationFailure, match="Invalid 5G HSSB schema"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )

    repeated = [_hssb_row(0, 0), _hssb_row(0, 307_200)]
    _write_5g_closure(run, repeated)
    with pytest.raises(PublicationFailure, match="not monotonic"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )

    operational_backwards = [_hssb_row(0, 0), _hssb_row(1, 307_200)]
    operational_backwards[1]["event_monotonic_ns"] = 1
    _write_5g_closure(run, operational_backwards)
    with pytest.raises(PublicationFailure, match="operational timestamps"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )

    _write_5g_closure(run, [_hssb_row(0, 0)], captured=2, invalid=1)
    with pytest.raises(PublicationFailure, match="ratio"):
        validate_5g_outputs(
            run,
            duration_s=1,
            minimum_valid_ratio=0.8,
            minimum_valid_rate_hz=1,
        )


def test_association_input_closure_and_semantics_failures(tmp_path) -> None:
    run = tmp_path / "run_association_inputs"
    _write_operational_clock(run / "rx_wifi")
    _write_operational_clock(run / "rx_5g")
    (run / "rx_wifi" / "features.jsonl").write_text(
        json.dumps({"rx_timestamp_ticks": 1, "rx_tick_rate_hz": 20_000_000})
        + "\n"
    )
    (run / "rx_wifi" / "frame-timings.jsonl").write_text("")
    (run / "rx_5g" / "hssb.jsonl").write_text(
        json.dumps(_hssb_row(0, 0)) + "\n"
    )
    with pytest.raises(AssociationFailure, match="do not close"):
        _association_inputs(run)

    (run / "rx_wifi" / "frame-timings.jsonl").write_text(
        json.dumps(
            {
                "event_monotonic_ns": 1,
                "event_monotonic_semantics": "host_time_is_canonical",
            }
        )
        + "\n"
    )
    with pytest.raises(AssociationFailure, match="semantics"):
        _association_inputs(run)
