"""Artifact validation and atomic publication of COMPLETE sessions."""

from __future__ import annotations

import json
import fcntl
import subprocess
from contextlib import contextmanager
from collections import defaultdict
from pathlib import Path
from typing import Any

from .checksums import sha256_file
from .domain import ExecutionPlan, PublicationFailure, SCHEMA_VERSION, ValidationFailure
from .state import StateStore, utc_now
from .storage import atomic_write_json
from .validation import validate_document, validate_event_semantics, validate_relative_path
from .wifi_smoke import validate_wifi_smoke_outputs
from .ssb_smoke import SSB_ROW_SCHEMA_REF, validate_ssb_smoke_outputs


def _artifact_record(producer_id: str, expected, path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PublicationFailure(f"Required artifact is missing or unsafe: {path}")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": expected.artifact_id,
        "producer_id": producer_id,
        "artifact_type": expected.artifact_type,
        "media_type": expected.media_type,
        "path": expected.path,
        "size_bytes": path.stat().st_size,
        "checksum": {"algorithm": "sha256", "hex": sha256_file(path)},
    }


def _validate_event_index(path: Path, *, run_id: str, producer_id: str, clock_domains: set[str], artifact_ids: set[str]) -> tuple[int, dict[str, Any]]:
    seen_ids: set[str] = set()
    last_by_clock: dict[str, tuple[int, int, int]] = {}
    summary: dict[str, dict[str, int]] = defaultdict(dict)
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PublicationFailure(f"Cannot read event index {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise PublicationFailure(f"Blank line in event index {path}:{line_number}")
        try:
            event = json.loads(line)
            validate_event_semantics(event, expected_run_id=run_id, expected_producer_id=producer_id, allowed_clock_domains=clock_domains)
        except (json.JSONDecodeError, ValidationFailure) as exc:
            raise PublicationFailure(f"Invalid event at {path}:{line_number}: {exc}") from exc
        if event["event_id"] in seen_ids:
            raise PublicationFailure(f"Duplicate event_id: {event['event_id']}")
        seen_ids.add(event["event_id"])
        if event["artifact_ref"]["artifact_id"] not in artifact_ids:
            raise PublicationFailure(f"Event references an unknown artifact: {event['artifact_ref']['artifact_id']}")
        clock = event["clock_domain_id"]
        ticks = event["timestamp"]["ticks"]
        rate = event["timestamp"]["tick_rate_hz"]
        sequence = event["sequence"]
        if clock in last_by_clock:
            last_ticks, last_rate, last_sequence = last_by_clock[clock]
            if rate != last_rate or ticks < last_ticks or sequence <= last_sequence:
                raise PublicationFailure(f"Events are not monotonic in clock domain {clock}")
        last_by_clock[clock] = (ticks, rate, sequence)
        if not summary[clock]:
            summary[clock] = {"count": 0, "first_ticks": ticks, "last_ticks": ticks, "tick_rate_hz": rate}
        summary[clock]["count"] += 1
        summary[clock]["last_ticks"] = ticks
        count += 1
    return count, dict(summary)


def build_producer_manifest(plan: ExecutionPlan, state: dict[str, Any], producer_id: str) -> dict[str, Any]:
    resolved = plan.processes[producer_id]
    definition = resolved.definition
    process_state = state["processes"][producer_id]
    if process_state.get("status") != "stopped" or process_state.get("exit_code") != 0:
        raise PublicationFailure(f"Producer did not stop successfully: {producer_id}")
    if plan.profile.experiment_type in {"distributed_dummy", "wifi_link_smoke", "ssb_rx_smoke"}:
        _validate_remote_receipt(plan, producer_id)
    wifi_summary = None
    ssb_summary = None
    if plan.profile.experiment_type == "wifi_link_smoke":
        wifi_summary = validate_wifi_smoke_outputs(plan.run_dir, int(plan.parameters["num_beacons"]))
    elif plan.profile.experiment_type == "ssb_rx_smoke":
        ssb_summary = validate_ssb_smoke_outputs(
            plan.run_dir,
            float(plan.parameters["duration_s"]),
            float(plan.parameters["min_valid_ssb_rate_hz"]),
        )
    records = []
    expected_by_id = {a.artifact_id: a for a in definition.expected_artifacts}
    for expected in definition.expected_artifacts:
        validate_relative_path(expected.path)
        artifact_path = resolved.producer_dir / expected.path
        if not artifact_path.exists() and not expected.required:
            continue
        record = _artifact_record(producer_id, expected, artifact_path)
        validate_document(record, "artifact")
        records.append(record)
    if wifi_summary and producer_id == "rx_wifi":
        for record in records:
            if record["artifact_type"] == "wifi_csi_feature_rows":
                record["row_count"] = wifi_summary["frames_received"]
                validate_document(record, "artifact")
    if ssb_summary and producer_id == "rx_5g":
        for record in records:
            if record["artifact_type"] == "5g_ssb_rxgrid_rows":
                record["row_count"] = ssb_summary["valid_grids"]
                record["schema_ref"] = SSB_ROW_SCHEMA_REF
                validate_document(record, "artifact")
    event_count = 0
    by_clock: dict[str, Any] = {}
    assigned_clocks = {definition.clock_domain_id} if definition.clock_domain_id else set()
    for record in records:
        expected = expected_by_id[record["artifact_id"]]
        if expected.event_index:
            event_count, by_clock = _validate_event_index(
                resolved.producer_dir / expected.path,
                run_id=plan.run_id or "", producer_id=producer_id, clock_domains=assigned_clocks,
                artifact_ids=set(expected_by_id),
            )
            record["row_count"] = event_count
            record["schema_ref"] = "urn:sync:schema:v1:event"
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": plan.run_id, "producer_id": producer_id,
        "role": definition.role, "node_id": definition.node_id, "status": "COMPLETE",
        "clock_domain_ids": sorted(assigned_clocks),
        "process": {
            "backend": process_state["handle"]["backend"], "started_at": process_state["started_at"],
            "stopped_at": process_state["stopped_at"], "exit_code": process_state["exit_code"],
            "termination_reason": process_state["termination_reason"],
        },
        "artifacts": records,
        "event_summary": {"count": event_count, "by_clock_domain": by_clock},
    }
    validate_document(manifest, "producer-manifest")
    return manifest


def _validate_remote_receipt(plan: ExecutionPlan, producer_id: str) -> None:
    resolved = plan.processes[producer_id]
    path = resolved.producer_dir / "producer-result.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationFailure(f"Missing or invalid producer receipt: {producer_id}") from exc
    try:
        validate_document(receipt, "producer-result")
    except ValidationFailure as exc:
        raise PublicationFailure(f"Invalid producer receipt schema: {producer_id}") from exc
    if (
        receipt.get("run_id") != plan.run_id or receipt.get("producer_id") != producer_id
        or receipt.get("node_id") != resolved.definition.node_id or receipt.get("exit_code") != 0
    ):
        raise PublicationFailure(f"Producer receipt identity/result mismatch: {producer_id}")
    expected_flags = plan.profile.experiment_type == "distributed_dummy"
    if receipt.get("simulation") is not expected_flags or receipt.get("synthetic") is not expected_flags:
        raise PublicationFailure(f"Producer receipt simulation classification mismatch: {producer_id}")
    declared = {item.get("path"): item for item in receipt.get("artifacts", []) if isinstance(item, dict)}
    expected = [a for a in resolved.definition.expected_artifacts if a.artifact_type != "producer_result"]
    if set(declared) != {a.path for a in expected}:
        raise PublicationFailure(f"Producer receipt does not close expected artifacts: {producer_id}")
    for artifact in expected:
        artifact_path = resolved.producer_dir / artifact.path
        item = declared[artifact.path]
        if not artifact_path.is_file() or item.get("size_bytes") != artifact_path.stat().st_size or item.get("sha256") != sha256_file(artifact_path):
            raise PublicationFailure(f"Producer receipt checksum mismatch: {producer_id}/{artifact.path}")


# Backwards-compatible internal name used by existing contract tests.
_validate_dummy_receipt = _validate_remote_receipt


def _git_revision(path: Path) -> dict[str, Any]:
    def run(*args: str) -> tuple[int, str]:
        result = subprocess.run(["git", "-C", str(path), *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
        return result.returncode, result.stdout.strip()
    code, head = run("rev-parse", "--verify", "HEAD")
    status_code, status = run("status", "--porcelain")
    if code != 0:
        return {"head": None, "worktree_state": "unborn", "dirty": bool(status) if status_code == 0 else True}
    return {"head": head, "worktree_state": "dirty" if status else "clean", "dirty": bool(status)}


def git_revisions(repo_root: Path) -> dict[str, Any]:
    return {
        "parent": _git_revision(repo_root),
        "modulos_rx_tx": _git_revision(repo_root / "modulos_rx_tx"),
        "rx_sync": _git_revision(repo_root / "rx_sync"),
    }


def verify_published_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        validate_document(manifest, "session-manifest")
    except (OSError, json.JSONDecodeError, ValidationFailure) as exc:
        raise PublicationFailure(f"Published session manifest is invalid: {exc}") from exc
    for producer in manifest["producers"]:
        manifest_path = run_dir / producer["manifest_path"]
        if sha256_file(manifest_path) != producer["manifest_checksum"]:
            raise PublicationFailure(f"Producer manifest checksum mismatch: {manifest_path}")
        producer_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_document(producer_manifest, "producer-manifest")
        producer_dir = manifest_path.parent
        for artifact in producer_manifest["artifacts"]:
            if sha256_file(producer_dir / artifact["path"]) != artifact["checksum"]["hex"]:
                raise PublicationFailure(f"Artifact checksum mismatch: {producer_dir / artifact['path']}")
    return manifest


@contextmanager
def _publication_lock(store: StateStore):
    lock_path = store.control_dir / "publication.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def publish_session(plan: ExecutionPlan, store: StateStore, *, repo_root: Path) -> dict[str, Any]:
    with _publication_lock(store):
        return _publish_session_locked(plan, store, repo_root=repo_root)


def _publish_session_locked(plan: ExecutionPlan, store: StateStore, *, repo_root: Path) -> dict[str, Any]:
    state = store.load()
    if state["state"] != "FINALIZING":
        raise PublicationFailure(f"Run must be FINALIZING before publication, got {state['state']}")
    final_path = store.run_dir / "manifest.json"
    if final_path.exists():
        manifest = verify_published_manifest(store.run_dir)
        store.transition("COMPLETE", reason="reconciled_existing_complete_manifest")
        return manifest
    producer_refs = []
    try:
        for producer_id in plan.processes:
            manifest = build_producer_manifest(plan, state, producer_id)
            path = plan.processes[producer_id].producer_dir / "producer-manifest.json"
            atomic_write_json(path, manifest)
            producer_refs.append({
                "producer_id": producer_id,
                "manifest_path": f"{producer_id}/producer-manifest.json",
                "manifest_checksum": sha256_file(path),
            })
        session = {
            "schema_version": SCHEMA_VERSION, "run_id": plan.run_id, "state": "COMPLETE", "published_at": utc_now(),
            "profile": {"profile_id": plan.profile.profile_id, "profile_version": plan.profile.profile_version, "digest": plan.profile.digest},
            "inventory": {"inventory_id": plan.inventory.inventory_id, "digest": plan.inventory.digest},
            "parameters": plan.parameters,
            "git_revisions": git_revisions(repo_root),
            "roles": [{"producer_id": p.definition.producer_id, "node_id": p.definition.node_id, "role": p.definition.role, "modality": p.definition.modality} for p in plan.processes.values()],
            "clock_domains": list(plan.profile.clock_domains),
            "clock_relationships": list(plan.profile.clock_relationships),
            "dataset_qualification": "integration_smoke" if plan.profile.experiment_type in {"wifi_link_smoke", "ssb_rx_smoke"} else "framework_validation",
            "timestamp_semantics": (
                "native_receiver_fields_unverified_no_canonical_events"
                if plan.profile.experiment_type == "wifi_link_smoke"
                else "host_serialization_time_operational_only_no_canonical_events"
                if plan.profile.experiment_type == "ssb_rx_smoke"
                else "profile_defined"
            ),
            "producers": producer_refs,
            "inference_runs": [],
        }
        validate_document(session, "session-manifest")
        atomic_write_json(final_path, session, mode=0o644)
        verify_published_manifest(store.run_dir)
        store.transition("COMPLETE", reason="session_manifest_published")
        return session
    except Exception as exc:
        if final_path.exists():
            final_path.unlink()
        current = store.load()
        if current["state"] == "FINALIZING":
            store.transition("FAILED", reason="publication_failed", error={"code": "PUBLICATION_FAILED", "message": str(exc), "at": utc_now()})
        if isinstance(exc, PublicationFailure):
            raise
        raise PublicationFailure(str(exc)) from exc
