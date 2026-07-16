from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from sync_framework.config import load_document, load_inventory, load_profile, resolve_parameters
from sync_framework.domain import CapabilityDisabled, ValidationFailure
from sync_framework.planning import build_plan
from sync_framework.validation import (
    load_schema,
    validate_document,
    validate_event_semantics,
    validate_inventory_semantics,
    validate_profile_semantics,
    validate_relative_path,
)


def test_document_loading_and_schema_errors(tmp_path):
    value = {"schema_version": "1.0.0"}
    path = tmp_path / "value.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert load_document(path) == value

    with pytest.raises(ValidationFailure, match="does not exist"):
        load_document(tmp_path / "missing.yaml")
    path.write_text("[", encoding="utf-8")
    with pytest.raises(ValidationFailure, match="Cannot parse"):
        load_document(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationFailure, match="root must be an object"):
        load_document(path)
    with pytest.raises(ValidationFailure, match="Unknown schema"):
        load_schema("unknown")
    with pytest.raises(ValidationFailure, match="validation failed"):
        validate_document(value, "inventory")


def test_parameter_edge_cases(profile_path):
    profile = load_profile(profile_path)
    assert resolve_parameters(profile, {"label": "x", "duration_s": "1", "scene": "moving"})["duration_s"] == 1.0
    with pytest.raises(ValidationFailure, match="Unknown profile parameters"):
        resolve_parameters(profile, {"label": "x", "duration_s": "1", "wat": "1"})
    with pytest.raises(ValidationFailure, match="Invalid value"):
        resolve_parameters(profile, {"label": "x", "duration_s": "false"})
    with pytest.raises(ValidationFailure, match="below minimum"):
        resolve_parameters(profile, {"label": "x", "duration_s": "0"})

    typed = replace(profile, parameter_specs={
        "count": {"type": "integer", "required": True, "maximum": 3},
        "enabled": {"type": "boolean", "required": True},
        "optional": {"type": "string", "required": False},
    })
    assert resolve_parameters(typed, {"count": "2", "enabled": "yes"}) == {"count": 2, "enabled": True}
    assert resolve_parameters(typed, {"count": "2", "enabled": "0"})["enabled"] is False
    with pytest.raises(ValidationFailure, match="Invalid value"):
        resolve_parameters(typed, {"count": "2", "enabled": "maybe"})
    with pytest.raises(ValidationFailure, match="above maximum"):
        resolve_parameters(typed, {"count": "4", "enabled": "true"})


def test_inventory_semantic_duplicates_and_secret(inventory_path):
    raw = load_document(inventory_path)
    duplicate_node = copy.deepcopy(raw)
    duplicate_node["nodes"].append(copy.deepcopy(duplicate_node["nodes"][0]))
    with pytest.raises(ValidationFailure, match="Duplicate node"):
        validate_inventory_semantics(duplicate_node)

    duplicate_command = copy.deepcopy(raw)
    duplicate_command["nodes"][0]["commands"].append(copy.deepcopy(duplicate_command["nodes"][0]["commands"][0]))
    with pytest.raises(ValidationFailure, match="Duplicate command"):
        validate_inventory_semantics(duplicate_command)


def test_profile_semantic_edges(profile_path):
    raw = load_document(profile_path)

    cases = []
    duplicate = copy.deepcopy(raw)
    duplicate["processes"].append(copy.deepcopy(duplicate["processes"][0]))
    cases.append((duplicate, "Duplicate producer"))
    missing_group = copy.deepcopy(raw)
    missing_group["orchestration"]["stop_groups"] = [["tx_wifi"], ["rx_5g"]]
    cases.append((missing_group, "stop_groups"))
    bad_stop = copy.deepcopy(raw)
    bad_stop["orchestration"]["stop_groups"] = [["rx_wifi"], ["tx_wifi"], ["rx_5g"]]
    cases.append((bad_stop, "transmitter before receivers"))
    duplicate_clock = copy.deepcopy(raw)
    duplicate_clock["clock_domains"].append(copy.deepcopy(duplicate_clock["clock_domains"][0]))
    cases.append((duplicate_clock, "Clock domain IDs"))
    unknown_clock = copy.deepcopy(raw)
    unknown_clock["processes"][0]["clock_domain_id"] = "missing"
    cases.append((unknown_clock, "Unknown clock domain"))
    duplicate_artifact = copy.deepcopy(raw)
    duplicate_artifact["processes"][0]["expected_artifacts"].append(copy.deepcopy(duplicate_artifact["processes"][0]["expected_artifacts"][0]))
    cases.append((duplicate_artifact, "Artifact IDs"))
    unknown_relation = copy.deepcopy(raw)
    unknown_relation["clock_relationships"][0]["right"] = "missing"
    cases.append((unknown_relation, "unknown domain"))
    comparable = copy.deepcopy(raw)
    comparable["clock_domains"][1]["comparability_group"] = comparable["clock_domains"][0]["comparability_group"]
    cases.append((comparable, "different comparability groups"))
    no_relation = copy.deepcopy(raw)
    no_relation["clock_relationships"] = []
    cases.append((no_relation, "not_comparable"))

    for value, message in cases:
        with pytest.raises(ValidationFailure, match=message):
            validate_profile_semantics(value)


def test_planning_rejects_missing_references_and_capabilities(inventory_path, profile_path, monkeypatch):
    inventory = load_inventory(inventory_path)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(profile, {"label": "x", "duration_s": "1"})

    broken_profile = copy.deepcopy(profile)
    broken_profile.processes["rx_5g"] = replace(broken_profile.processes["rx_5g"], node_id="missing")
    with pytest.raises(ValidationFailure, match="missing inventory node"):
        build_plan(inventory, broken_profile, parameters)

    broken_inventory = copy.deepcopy(inventory)
    broken_inventory.nodes["pc3"].commands.clear()
    with pytest.raises(ValidationFailure, match="does not define command"):
        build_plan(broken_inventory, profile, parameters)

    ssh_inventory = copy.deepcopy(inventory)
    ssh_inventory.nodes["pc3"] = replace(ssh_inventory.nodes["pc3"], transport="ssh")
    with pytest.raises(ValidationFailure, match="client_mount"):
        build_plan(ssh_inventory, profile, parameters, enforce_capabilities=True)

    unsafe_inventory = copy.deepcopy(inventory)
    unsafe_inventory.nodes["pc3"].commands["simulate_rx_5g"] = replace(
        unsafe_inventory.nodes["pc3"].commands["simulate_rx_5g"], safety_class="dsp"
    )
    with pytest.raises(CapabilityDisabled, match="simulation commands"):
        build_plan(unsafe_inventory, profile, parameters, enforce_capabilities=True)

    nfs_inventory = copy.deepcopy(inventory)
    nfs_inventory = replace(nfs_inventory, storage_backend="nfs")
    assert build_plan(nfs_inventory, profile, parameters, enforce_capabilities=True).inventory.storage_backend == "nfs"

    env_inventory = copy.deepcopy(inventory)
    env_inventory.nodes["pc3"].commands["simulate_rx_5g"] = replace(
        env_inventory.nodes["pc3"].commands["simulate_rx_5g"], env_from=("SYNC_MISSING_TEST_ENV",)
    )
    monkeypatch.delenv("SYNC_MISSING_TEST_ENV", raising=False)
    with pytest.raises(ValidationFailure, match="environment variable"):
        build_plan(env_inventory, profile, parameters)

    placeholder_inventory = copy.deepcopy(inventory)
    command = placeholder_inventory.nodes["pc3"].commands["simulate_rx_5g"]
    placeholder_inventory.nodes["pc3"].commands["simulate_rx_5g"] = replace(command, argv=("{unknown}",))
    with pytest.raises(ValidationFailure, match="Unknown command placeholders"):
        build_plan(placeholder_inventory, profile, parameters)


def test_path_and_event_context_validation():
    assert str(validate_relative_path("rx/events.jsonl")) == "rx/events.jsonl"
    for path in ("/absolute", "../escape"):
        with pytest.raises(ValidationFailure, match="Unsafe relative path"):
            validate_relative_path(path)

    event = {
        "schema_version": "1.0.0", "run_id": "run", "event_id": "e1", "producer_id": "rx", "sequence": 0,
        "modality": "5g", "frame_type": "ssb", "clock_domain_id": "clock",
        "timestamp": {"ticks": 110, "tick_rate_hz": 1000}, "reference_point": "ssb_pss_start",
        "detector": {"block_start_ticks": 100, "offset_samples": 10, "sample_rate_hz": 1000, "uncertainty_ticks": 1},
        "capture_discontinuity": {"present": False}, "artifact_ref": {"artifact_id": "features", "row_index": 0},
    }
    validate_event_semantics(event, expected_run_id="run", expected_producer_id="rx", allowed_clock_domains={"clock"})
    for kwargs, message in [
        ({"expected_run_id": "other"}, "run_id"),
        ({"expected_producer_id": "other"}, "producer_id"),
        ({"allowed_clock_domains": {"other"}}, "clock domain"),
    ]:
        with pytest.raises(ValidationFailure, match=message):
            validate_event_semantics(event, **kwargs)
    bad_rate = copy.deepcopy(event)
    bad_rate["timestamp"]["tick_rate_hz"] = 999
    with pytest.raises(ValidationFailure, match="tick rate"):
        validate_event_semantics(bad_rate)
