"""Typed domain objects used after schema validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


SCHEMA_VERSION = "1.0.0"


class SyncError(RuntimeError):
    """Base error with a stable machine-readable code and CLI exit status."""

    exit_code = 2
    code = "SYNC_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ValidationFailure(SyncError):
    code = "VALIDATION_FAILED"
    exit_code = 2


class InvalidTransition(SyncError):
    code = "INVALID_TRANSITION"
    exit_code = 3


class CapabilityDisabled(SyncError):
    code = "CAPABILITY_DISABLED"
    exit_code = 4


class ProcessFailure(SyncError):
    code = "PROCESS_FAILED"
    exit_code = 5


class PublicationFailure(SyncError):
    code = "PUBLICATION_FAILED"
    exit_code = 6


class InferenceFailure(SyncError):
    code = "INFERENCE_FAILED"
    exit_code = 7


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    env_from: tuple[str, ...]
    safety_class: str


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    transport: str
    workspace: Path
    commands: dict[str, CommandSpec]
    ssh: dict[str, Any] | None = None


@dataclass(frozen=True)
class Inventory:
    inventory_id: str
    storage_backend: str
    storage_root: Path
    client_mount: Path | None
    nodes: dict[str, NodeSpec]
    source_path: Path
    digest: str


@dataclass(frozen=True)
class ExpectedArtifact:
    artifact_id: str
    artifact_type: str
    media_type: str
    path: str
    required: bool
    event_index: bool = False


@dataclass(frozen=True)
class ProcessDefinition:
    producer_id: str
    node_id: str
    role: str
    modality: str
    command_ref: str
    clock_domain_id: str | None
    readiness: dict[str, Any]
    timeouts: dict[str, float]
    expected_artifacts: tuple[ExpectedArtifact, ...]
    lifecycle: str = "continuous"
    stop_signal: str = "terminate"


@dataclass(frozen=True)
class ExperimentProfile:
    profile_id: str
    profile_version: str
    experiment_type: str
    description: str
    parameter_specs: dict[str, dict[str, Any]]
    clock_domains: tuple[dict[str, Any], ...]
    clock_relationships: tuple[dict[str, Any], ...]
    processes: dict[str, ProcessDefinition]
    start_groups: tuple[tuple[str, ...], ...]
    stop_groups: tuple[tuple[str, ...], ...]
    orchestration: dict[str, Any]
    source_path: Path
    digest: str


@dataclass(frozen=True)
class ResolvedProcess:
    definition: ProcessDefinition
    node: NodeSpec
    command: CommandSpec
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    producer_dir: Path
    execution_producer_dir: Path


@dataclass(frozen=True)
class ExecutionPlan:
    run_id: str | None
    run_dir: Path | None
    inventory: Inventory
    profile: ExperimentProfile
    parameters: dict[str, Any]
    processes: dict[str, ResolvedProcess]
    sanitized: dict[str, Any] = field(default_factory=dict)


class BatchModelAdapter(Protocol):
    """Future batch inference boundary; no implementation is provided in v1."""

    def run(self, request: dict[str, Any]) -> dict[str, Any]: ...
