from __future__ import annotations

from pathlib import Path

import pytest

from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.domain import ProcessFailure, PublicationFailure
from sync_framework.orchestration import _hardware_preflight, preflight
from sync_framework.planning import build_plan
from sync_framework.publication import _ssb_validation_duration


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_combined_profile_contract_and_dry_run_is_non_mutating(tmp_path):
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive_hardware_smoke.yaml")
    parameters = resolve_parameters(
        profile,
        {"label": "combined", "num_beacons": "50"},
    )
    assert parameters["detector_threshold"] == 0.85
    assert parameters["min_valid_ssb_rate_hz"] == 10
    assert profile.start_groups == (("rx_5g", "rx_wifi"), ("tx_wifi",))
    assert profile.stop_groups == (("tx_wifi",), ("rx_5g", "rx_wifi"))
    assert profile.clock_relationships[0]["relation"] == "not_comparable"

    inventory = load_inventory(
        REPO_ROOT / "config" / "inventory.nosync-hardware-smoke.example.yaml",
        storage_override=tmp_path / "must-not-exist",
    )
    plan = build_plan(inventory, profile, parameters)
    assert set(plan.processes) == {"rx_5g", "rx_wifi", "tx_wifi"}
    dry_plan, store = preflight(
        inventory.source_path,
        profile.source_path,
        {"label": "combined", "num_beacons": "50"},
        storage_override=tmp_path / "must-not-exist",
        dry_run=True,
        repo_root=REPO_ROOT,
    )
    assert dry_plan.run_id is None
    assert store is None
    assert not (tmp_path / "must-not-exist").exists()


def test_combined_preflight_runs_both_contracts_and_rejects_same_serial(
    tmp_path,
    monkeypatch,
):
    inventory = load_inventory(
        REPO_ROOT / "config" / "inventory.nosync-hardware-smoke.example.yaml"
    )
    profile = load_profile(REPO_ROOT / "profiles" / "nosync_passive_hardware_smoke.yaml")
    parameters = resolve_parameters(
        profile,
        {"label": "combined", "num_beacons": "50"},
    )
    run_dir = tmp_path / "run"
    (run_dir / "rx_wifi" / "runtime").mkdir(parents=True)
    (run_dir / "rx_5g").mkdir()
    (run_dir / "tx_wifi").mkdir()
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id="run_mock",
        run_dir=run_dir,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "sync_framework.orchestration._wifi_hardware_preflight",
        lambda _plan: calls.append("wifi"),
    )
    monkeypatch.setattr(
        "sync_framework.orchestration._ssb_hardware_preflight",
        lambda _plan: calls.append("ssb"),
    )
    monkeypatch.setattr(
        "sync_framework.orchestration._wifi_rx_serial",
        lambda _plan: "WIFI",
    )
    monkeypatch.setattr(
        "sync_framework.orchestration._argument_value",
        lambda _argv, _option: "5G",
    )

    _hardware_preflight(plan)

    assert calls == ["wifi", "ssb"]

    monkeypatch.setattr(
        "sync_framework.orchestration._argument_value",
        lambda _argv, _option: "WIFI",
    )
    with pytest.raises(ProcessFailure, match="different USRP serials"):
        _hardware_preflight(plan)


def test_combined_ssb_duration_comes_only_from_operational_window():
    state = {
        "profile": {"parameters": {}},
        "operational_window": {
            "producer_active_duration_s": {"rx_5g": 12.5},
        },
    }
    assert _ssb_validation_duration(state, "nosync_passive_hardware_smoke") == 12.5
    state["operational_window"] = None
    with pytest.raises(PublicationFailure, match="operational duration"):
        _ssb_validation_duration(state, "nosync_passive_hardware_smoke")
