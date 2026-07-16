"""JSON Schema and cross-document validation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import jsonschema

from .domain import ValidationFailure


SCHEMA_FILES = {
    "inventory": "inventory.schema.json",
    "experiment-profile": "experiment-profile.schema.json",
    "event": "event.schema.json",
    "artifact": "artifact.schema.json",
    "producer-manifest": "producer-manifest.schema.json",
    "producer-result": "producer-result.schema.json",
    "session-manifest": "session-manifest.schema.json",
    "run-state": "run-state.schema.json",
    "batch-model-request": "batch-model-request.schema.json",
    "batch-model-result": "batch-model-result.schema.json",
}


def schema_root() -> Path:
    candidates = []
    if os.environ.get("SYNC_SCHEMA_ROOT"):
        candidates.append(Path(os.environ["SYNC_SCHEMA_ROOT"]))
    candidates.extend([
        Path(__file__).resolve().parents[2] / "schemas" / "v1",
        Path(sys.prefix) / "share" / "sync-framework" / "schemas" / "v1",
        Path.cwd() / "schemas" / "v1",
    ])
    for candidate in candidates:
        if (candidate / "inventory.schema.json").is_file():
            return candidate.resolve()
    raise ValidationFailure("Cannot locate installed SYNC schemas")


def load_schema(name: str) -> dict[str, Any]:
    try:
        filename = SCHEMA_FILES[name]
    except KeyError as exc:
        raise ValidationFailure(f"Unknown schema: {name}") from exc
    return json.loads((schema_root() / filename).read_text(encoding="utf-8"))


def validate_document(value: dict[str, Any], schema_name: str) -> None:
    schema = load_schema(schema_name)
    store: dict[str, Any] = {}
    for filename in SCHEMA_FILES.values():
        document = json.loads((schema_root() / filename).read_text(encoding="utf-8"))
        store[filename] = document
        store[(schema_root() / filename).as_uri()] = document
        if "$id" in document:
            store[document["$id"]] = document
    resolver = jsonschema.RefResolver(base_uri=(schema_root().as_uri() + "/"), referrer=schema, store=store)
    validator = jsonschema.Draft202012Validator(schema, resolver=resolver, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(p) for p in error.absolute_path) or "<root>"
        raise ValidationFailure(f"{schema_name} validation failed at {location}: {error.message}")


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def validate_inventory_semantics(raw: dict[str, Any]) -> None:
    duplicates = _duplicates(n["node_id"] for n in raw["nodes"])
    if duplicates:
        raise ValidationFailure(f"Duplicate node IDs: {', '.join(sorted(duplicates))}")
    secret_markers = ("password", "secret", "token", "private_key", "passphrase")
    for node in raw["nodes"]:
        command_ids = [c["command_id"] for c in node["commands"]]
        duplicates = _duplicates(command_ids)
        if duplicates:
            raise ValidationFailure(f"Duplicate command IDs on {node['node_id']}: {', '.join(sorted(duplicates))}")
        for command in node["commands"]:
            for key in command.get("env", {}):
                if any(marker in key.lower() for marker in secret_markers):
                    raise ValidationFailure(f"Secret-like environment value must use env_from, not env: {key}")


def validate_profile_semantics(raw: dict[str, Any]) -> None:
    producer_ids = [p["producer_id"] for p in raw["processes"]]
    duplicates = _duplicates(producer_ids)
    if duplicates:
        raise ValidationFailure(f"Duplicate producer IDs: {', '.join(sorted(duplicates))}")
    producers = set(producer_ids)
    for key in ("start_groups", "stop_groups"):
        flattened = [pid for group in raw["orchestration"][key] for pid in group]
        if set(flattened) != producers or len(flattened) != len(producers):
            raise ValidationFailure(f"{key} must contain every producer exactly once")
    roles = {p["producer_id"]: p["role"] for p in raw["processes"]}
    seen_transmitter = False
    for group in raw["orchestration"]["start_groups"]:
        if any(roles[p] == "transmitter" for p in group):
            seen_transmitter = True
        if seen_transmitter and any(roles[p] == "receiver" for p in group):
            raise ValidationFailure("start_groups must place every receiver before transmitters")
    seen_receiver = False
    for group in raw["orchestration"]["stop_groups"]:
        if any(roles[p] == "receiver" for p in group):
            seen_receiver = True
        if seen_receiver and any(roles[p] == "transmitter" for p in group):
            raise ValidationFailure("stop_groups must place every transmitter before receivers")
    clocks = {c["clock_domain_id"]: c for c in raw["clock_domains"]}
    if len(clocks) != len(raw["clock_domains"]):
        raise ValidationFailure("Clock domain IDs must be unique")
    for process in raw["processes"]:
        clock_id = process.get("clock_domain_id")
        if clock_id and clock_id not in clocks:
            raise ValidationFailure(f"Unknown clock domain {clock_id} on {process['producer_id']}")
        artifact_ids = [a["artifact_id"] for a in process["expected_artifacts"]]
        if _duplicates(artifact_ids):
            raise ValidationFailure(f"Artifact IDs must be unique for {process['producer_id']}")
    for relation in raw["clock_relationships"]:
        if relation["left"] not in clocks or relation["right"] not in clocks:
            raise ValidationFailure("Clock relationship references an unknown domain")
    if raw["experiment_type"] in {"nosync_passive", "distributed_dummy"}:
        receiver_clocks = [p["clock_domain_id"] for p in raw["processes"] if p["role"] == "receiver"]
        if len(receiver_clocks) != 2 or len(set(receiver_clocks)) != 2:
            raise ValidationFailure(f"{raw['experiment_type']} requires two independent receiver clock domains")
        groups = {clocks[c]["comparability_group"] for c in receiver_clocks}
        if len(groups) != 2:
            raise ValidationFailure(f"{raw['experiment_type']} receiver clocks must have different comparability groups")
        if not any(r["relation"] == "not_comparable" and {r["left"], r["right"]} == set(receiver_clocks) for r in raw["clock_relationships"]):
            raise ValidationFailure(f"{raw['experiment_type']} must explicitly mark receiver clocks not_comparable")


def validate_relative_path(path: str) -> PurePosixPath:
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise ValidationFailure(f"Unsafe relative path: {path}")
    return parsed


def validate_event_semantics(event: dict[str, Any], *, expected_run_id: str | None = None, expected_producer_id: str | None = None, allowed_clock_domains: set[str] | None = None) -> None:
    validate_document(event, "event")
    if event["timestamp"]["tick_rate_hz"] != event["detector"]["sample_rate_hz"]:
        raise ValidationFailure("Event tick rate must equal detector sample rate")
    expected_ticks = event["detector"]["block_start_ticks"] + event["detector"]["offset_samples"]
    if event["timestamp"]["ticks"] != expected_ticks:
        raise ValidationFailure("Event timestamp must equal block_start_ticks + offset_samples")
    if expected_run_id and event["run_id"] != expected_run_id:
        raise ValidationFailure("Event run_id does not match its session")
    if expected_producer_id and event["producer_id"] != expected_producer_id:
        raise ValidationFailure("Event producer_id does not match its producer directory")
    if allowed_clock_domains is not None and event["clock_domain_id"] not in allowed_clock_domains:
        raise ValidationFailure("Event uses a clock domain not assigned to its producer")
