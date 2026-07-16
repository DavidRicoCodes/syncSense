from __future__ import annotations

import copy

import pytest

from sync_framework.config import load_document, load_inventory, load_profile, resolve_parameters
from sync_framework.domain import ValidationFailure
from sync_framework.validation import validate_event_semantics, validate_profile_semantics


def test_profile_declares_two_non_comparable_receiver_clocks(profile_path):
    profile = load_profile(profile_path)
    assert {c["comparability_group"] for c in profile.clock_domains} == {"pc3_5g_only", "pc4_wifi_only"}
    assert profile.clock_relationships[0]["relation"] == "not_comparable"
    assert profile.start_groups == (("rx_5g", "rx_wifi"), ("tx_wifi",))
    assert profile.stop_groups[0] == ("tx_wifi",)


def test_parameter_resolution_is_typed(profile_path):
    profile = load_profile(profile_path)
    params = resolve_parameters(profile, {"label": "empty", "duration_s": "1.5"})
    assert params == {"label": "empty", "duration_s": 1.5, "scene": "static"}
    with pytest.raises(ValidationFailure):
        resolve_parameters(profile, {"duration_s": "1"})


def test_inventory_rejects_secret_literal(inventory_path):
    raw = load_document(inventory_path)
    raw["nodes"][0]["commands"][0]["env"] = {"API_TOKEN": "secret"}
    from sync_framework.validation import validate_inventory_semantics
    with pytest.raises(ValidationFailure):
        validate_inventory_semantics(raw)


def test_profile_rejects_receiver_after_transmitter(profile_path):
    raw = load_document(profile_path)
    raw = copy.deepcopy(raw)
    raw["orchestration"]["start_groups"] = [["tx_wifi"], ["rx_5g", "rx_wifi"]]
    with pytest.raises(ValidationFailure):
        validate_profile_semantics(raw)


def test_event_formula_is_enforced():
    event = {
        "schema_version": "1.0.0", "run_id": "run", "event_id": "e1", "producer_id": "rx_5g", "sequence": 0,
        "modality": "5g", "frame_type": "ssb", "clock_domain_id": "clock",
        "timestamp": {"ticks": 110, "tick_rate_hz": 1000}, "reference_point": "ssb_pss_start",
        "detector": {"block_start_ticks": 100, "offset_samples": 9, "sample_rate_hz": 1000, "uncertainty_ticks": 1},
        "capture_discontinuity": {"present": False}, "artifact_ref": {"artifact_id": "features", "row_index": 0},
    }
    with pytest.raises(ValidationFailure):
        validate_event_semantics(event)

