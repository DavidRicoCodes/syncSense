"""Resolve validated profiles against a local inventory."""

from __future__ import annotations

import os
from pathlib import Path
from string import Formatter
from typing import Any

from .domain import CapabilityDisabled, ExecutionPlan, ResolvedProcess, ValidationFailure


ALLOWED_PLACEHOLDERS = {
    "run_id", "run_dir", "producer_dir", "workspace", "label", "scene", "duration_s",
    "num_beacons", "rx_quiet_s", "rx_max_drain_s", "effective_config",
}


def _format_value(value: str, context: dict[str, Any]) -> str:
    fields = {field for _, field, _, _ in Formatter().parse(value) if field}
    unknown = fields - ALLOWED_PLACEHOLDERS
    if unknown:
        raise ValidationFailure(f"Unknown command placeholders: {', '.join(sorted(unknown))}")
    try:
        return value.format_map(context)
    except KeyError as exc:
        raise ValidationFailure(f"Command requires unresolved placeholder: {exc.args[0]}") from exc


def build_plan(inventory, profile, parameters: dict[str, Any], *, run_id: str | None = None, run_dir: Path | None = None, enforce_capabilities: bool = False, allowed_safety_classes: set[str] | None = None) -> ExecutionPlan:
    if enforce_capabilities and inventory.storage_backend not in {"local", "nfs"}:
        raise CapabilityDisabled(f"Unsupported storage backend: {inventory.storage_backend}")
    resolved: dict[str, ResolvedProcess] = {}
    safe_processes = []
    for producer_id, definition in profile.processes.items():
        if definition.node_id not in inventory.nodes:
            raise ValidationFailure(f"Profile references missing inventory node: {definition.node_id}")
        node = inventory.nodes[definition.node_id]
        if definition.command_ref not in node.commands:
            raise ValidationFailure(f"Node {node.node_id} does not define command {definition.command_ref}")
        command = node.commands[definition.command_ref]
        allowed = {"simulation"} if allowed_safety_classes is None else allowed_safety_classes
        if enforce_capabilities and command.safety_class not in allowed:
            if allowed_safety_classes is None:
                raise CapabilityDisabled(f"Only simulation commands are executable: {producer_id}")
            raise CapabilityDisabled(f"Safety class {command.safety_class} is not authorized: {producer_id}")
        producer_dir = (run_dir / producer_id) if run_dir else Path(f"<run_dir>/{producer_id}")
        if node.transport == "ssh":
            if inventory.client_mount is None:
                raise ValidationFailure("SSH producers require storage.client_mount")
            execution_producer_dir = (
                inventory.client_mount / "runs" / run_id / producer_id
                if run_id else Path(f"{inventory.client_mount}/runs/<run_id>/{producer_id}")
            )
        else:
            execution_producer_dir = producer_dir
        context = {
            **parameters,
            "run_id": run_id or "<generated-at-preflight>",
            "run_dir": str(run_dir or "<run_dir>"),
            "producer_dir": str(execution_producer_dir),
            "workspace": str(node.workspace),
            "effective_config": str(execution_producer_dir / "runtime" / "effective-config.json"),
        }
        argv = tuple(_format_value(arg, context) for arg in command.argv)
        cwd = Path(_format_value(str(command.cwd), context)).expanduser()
        env = {key: _format_value(value, context) for key, value in command.env.items()}
        for key in command.env_from:
            if key not in os.environ:
                raise ValidationFailure(f"Required environment variable is not set: {key}")
            env[key] = os.environ[key]
        resolved[producer_id] = ResolvedProcess(
            definition=definition, node=node, command=command, argv=argv, cwd=cwd, env=env,
            producer_dir=producer_dir, execution_producer_dir=execution_producer_dir,
        )
        safe_processes.append({
            "producer_id": producer_id, "node_id": node.node_id, "role": definition.role, "transport": node.transport,
            "command_ref": command.command_id, "command_digest": _command_digest(command), "safety_class": command.safety_class,
            "producer_dir": str(producer_dir), "lifecycle": definition.lifecycle,
        })
    sanitized = {
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_digest": profile.digest,
        "inventory_id": inventory.inventory_id,
        "inventory_digest": inventory.digest,
        "run_id": run_id or "<generated-at-preflight>",
        "parameters": parameters,
        "start_groups": [list(g) for g in profile.start_groups],
        "stop_groups": [list(g) for g in profile.stop_groups],
        "clock_domains": list(profile.clock_domains),
        "clock_relationships": list(profile.clock_relationships),
        "processes": safe_processes,
    }
    return ExecutionPlan(run_id=run_id, run_dir=run_dir, inventory=inventory, profile=profile, parameters=parameters, processes=resolved, sanitized=sanitized)


def _command_digest(command) -> str:
    from .config import document_digest
    return document_digest({"command_id": command.command_id, "argv": list(command.argv), "cwd": str(command.cwd), "env_keys": sorted(command.env), "env_from": list(command.env_from), "safety_class": command.safety_class})
