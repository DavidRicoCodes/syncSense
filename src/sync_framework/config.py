"""Configuration loading, canonicalization and dataclass conversion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .domain import (
    CommandSpec,
    ExpectedArtifact,
    ExperimentProfile,
    Inventory,
    NodeSpec,
    ProcessDefinition,
    ValidationFailure,
)
from .validation import validate_document, validate_inventory_semantics, validate_profile_semantics


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def document_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValidationFailure(f"Configuration file does not exist: {source}")
    try:
        if source.suffix.lower() == ".json":
            value = json.loads(source.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValidationFailure(f"Cannot parse configuration {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationFailure(f"Configuration root must be an object: {source}")
    return value


def load_inventory(path: str | Path, *, storage_override: str | Path | None = None) -> Inventory:
    source = Path(path).expanduser().resolve()
    raw = load_document(source)
    validate_document(raw, "inventory")
    validate_inventory_semantics(raw)
    nodes: dict[str, NodeSpec] = {}
    for item in raw["nodes"]:
        workspace = Path(item["workspace"]).expanduser()
        commands: dict[str, CommandSpec] = {}
        for command in item["commands"]:
            cwd = Path(command.get("cwd", str(workspace))).expanduser()
            commands[command["command_id"]] = CommandSpec(
                command_id=command["command_id"],
                argv=tuple(command["argv"]),
                cwd=cwd,
                env=dict(command.get("env", {})),
                env_from=tuple(command.get("env_from", [])),
                safety_class=command["safety_class"],
            )
        nodes[item["node_id"]] = NodeSpec(
            node_id=item["node_id"],
            transport=item["transport"],
            workspace=workspace,
            commands=commands,
            ssh=item.get("ssh"),
        )
    storage = raw["storage"]
    root = Path(storage_override).expanduser().resolve() if storage_override else Path(storage["root"]).expanduser().resolve()
    return Inventory(
        inventory_id=raw["inventory_id"],
        storage_backend=storage["backend"],
        storage_root=root,
        client_mount=Path(storage["client_mount"]).expanduser() if storage.get("client_mount") else None,
        nodes=nodes,
        source_path=source,
        digest=document_digest(raw),
    )


def load_profile(path: str | Path) -> ExperimentProfile:
    source = Path(path).expanduser().resolve()
    raw = load_document(source)
    validate_document(raw, "experiment-profile")
    validate_profile_semantics(raw)
    processes: dict[str, ProcessDefinition] = {}
    for item in raw["processes"]:
        artifacts = tuple(
            ExpectedArtifact(
                artifact_id=a["artifact_id"], artifact_type=a["artifact_type"], media_type=a["media_type"],
                path=a["path"], required=a["required"], event_index=a.get("event_index", False),
            )
            for a in item["expected_artifacts"]
        )
        processes[item["producer_id"]] = ProcessDefinition(
            producer_id=item["producer_id"], node_id=item["node_id"], role=item["role"], modality=item["modality"],
            command_ref=item["command_ref"], clock_domain_id=item.get("clock_domain_id"), readiness=dict(item["readiness"]),
            timeouts={k: float(v) for k, v in item["timeouts"].items()}, expected_artifacts=artifacts,
            lifecycle=item.get("lifecycle", "continuous"),
            stop_signal=item.get("stop_signal", "terminate"),
        )
    orchestration = raw["orchestration"]
    return ExperimentProfile(
        profile_id=raw["profile_id"], profile_version=raw["profile_version"], experiment_type=raw["experiment_type"],
        description=raw.get("description", ""), parameter_specs=dict(raw["parameters"]),
        clock_domains=tuple(raw["clock_domains"]), clock_relationships=tuple(raw["clock_relationships"]),
        processes=processes,
        start_groups=tuple(tuple(g) for g in orchestration["start_groups"]),
        stop_groups=tuple(tuple(g) for g in orchestration["stop_groups"]),
        orchestration={k: v for k, v in orchestration.items() if not k.endswith("groups")},
        source_path=source, digest=document_digest(raw),
    )


def resolve_parameters(profile: ExperimentProfile, supplied: dict[str, str]) -> dict[str, Any]:
    unknown = set(supplied) - set(profile.parameter_specs)
    if unknown:
        raise ValidationFailure(f"Unknown profile parameters: {', '.join(sorted(unknown))}")
    resolved: dict[str, Any] = {}
    for name, spec in profile.parameter_specs.items():
        if name in supplied:
            raw: Any = supplied[name]
        elif "default" in spec:
            raw = spec["default"]
        elif spec["required"]:
            raise ValidationFailure(f"Required profile parameter is missing: {name}")
        else:
            continue
        try:
            if spec["type"] == "string":
                value = str(raw)
            elif spec["type"] == "integer":
                value = int(raw)
            elif spec["type"] == "number":
                value = float(raw)
            elif spec["type"] == "boolean":
                if isinstance(raw, bool):
                    value = raw
                elif str(raw).lower() in {"true", "1", "yes"}:
                    value = True
                elif str(raw).lower() in {"false", "0", "no"}:
                    value = False
                else:
                    raise ValueError("expected boolean")
            else:  # schema prevents this
                raise ValueError("unsupported parameter type")
        except (TypeError, ValueError) as exc:
            raise ValidationFailure(f"Invalid value for parameter {name}: {raw!r}") from exc
        if "minimum" in spec and value < spec["minimum"]:
            raise ValidationFailure(f"Parameter {name} is below minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ValidationFailure(f"Parameter {name} is above maximum {spec['maximum']}")
        resolved[name] = value
    return resolved
