"""Human-navigable catalog entries for canonical run directories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from .domain import SCHEMA_VERSION, ExecutionPlan, ValidationFailure
from .run_id import validate_run_id
from .storage import atomic_write_json
from .validation import validate_document


SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UNSPECIFIED = "unspecified"


def catalog_component(value: Any) -> str:
    """Return a bounded, readable and collision-resistant path component."""
    raw = str(value).strip()
    if raw not in {"", ".", ".."} and SAFE_COMPONENT_RE.fullmatch(raw):
        return raw
    readable = (
        unicodedata.normalize("NFKD", raw)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", readable).strip("._-")
    readable = (readable or "value")[:48].rstrip("._-") or "value"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{readable}-{digest}"


def catalog_dimensions(
    experiment_type: str, parameters: dict[str, Any]
) -> dict[str, str]:
    """Extract the stable dimensions used by the filesystem view."""
    return {
        "testbed_id": str(parameters.get("testbed_id", UNSPECIFIED)),
        "experiment_type": str(experiment_type),
        "condition": str(parameters.get("condition", UNSPECIFIED)),
        "position": str(parameters.get("position", UNSPECIFIED)),
        "subject_id": str(parameters.get("subject_id", UNSPECIFIED)),
    }


def catalog_relative_path(
    run_id: str, experiment_type: str, parameters: dict[str, Any]
) -> PurePosixPath:
    """Build the catalog path without consulting or changing the filesystem."""
    validate_run_id(run_id)
    dimensions = catalog_dimensions(experiment_type, parameters)
    return PurePosixPath(
        "catalog",
        catalog_component(dimensions["testbed_id"]),
        catalog_component(dimensions["experiment_type"]),
        catalog_component(dimensions["condition"]),
        catalog_component(dimensions["position"]),
        catalog_component(dimensions["subject_id"]),
        run_id,
    )


def catalog_path_for_plan(plan: ExecutionPlan) -> Path:
    if plan.run_id is None:
        raise ValidationFailure("A catalog path requires a concrete run_id")
    relative = catalog_relative_path(
        plan.run_id, plan.profile.experiment_type, plan.parameters
    )
    return plan.inventory.storage_root.joinpath(*relative.parts)


def existing_catalog_path_for_plan(plan: ExecutionPlan) -> Path | None:
    """Return the catalog link only when it resolves to this canonical run."""
    path = catalog_path_for_plan(plan)
    if (
        plan.run_dir is None
        or not path.is_symlink()
        or path.resolve() != plan.run_dir.resolve()
    ):
        return None
    return path


def _entry_for_plan(plan: ExecutionPlan, created_at: str) -> dict[str, Any]:
    if plan.run_id is None or plan.run_dir is None:
        raise ValidationFailure("A catalog entry requires a concrete run")
    relative_catalog = catalog_relative_path(
        plan.run_id, plan.profile.experiment_type, plan.parameters
    )
    relative_run = PurePosixPath("runs", plan.run_id)
    entry = {
        "schema_version": SCHEMA_VERSION,
        "run_id": plan.run_id,
        "created_at": created_at,
        "label": str(plan.parameters.get("label", "")),
        "profile": {
            "profile_id": plan.profile.profile_id,
            "experiment_type": plan.profile.experiment_type,
        },
        "dimensions": catalog_dimensions(
            plan.profile.experiment_type, plan.parameters
        ),
        "catalog_path": relative_catalog.as_posix(),
        "run_path": relative_run.as_posix(),
        "state_path": (relative_run / ".control" / "state.json").as_posix(),
        "manifest_path": (relative_run / "manifest.json").as_posix(),
    }
    validate_document(entry, "catalog-entry")
    return entry


def _ensure_directory_tree(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for component in relative.parts:
        current = current / component
        if os.path.lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise ValidationFailure(
                    f"Catalog hierarchy contains a non-directory: {current}"
                )
            continue
        try:
            current.mkdir(mode=0o750)
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise ValidationFailure(
                    f"Catalog hierarchy contains a non-directory: {current}"
                )
    return current


def _write_relative_symlink(link_path: Path, target: Path) -> None:
    if os.path.lexists(link_path):
        if link_path.is_symlink() and link_path.resolve() == target.resolve():
            return
        raise ValidationFailure(f"Catalog entry already exists: {link_path}")
    relative_target = os.path.relpath(target, start=link_path.parent)
    temporary = link_path.with_name(f".{link_path.name}.tmp.{os.getpid()}")
    try:
        os.symlink(relative_target, temporary, target_is_directory=True)
        os.replace(temporary, link_path)
        directory_fd = os.open(link_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def create_catalog_entry(plan: ExecutionPlan, *, created_at: str) -> Path:
    """Publish an idempotent catalog view for one canonical run."""
    if plan.run_dir is None:
        raise ValidationFailure("A catalog entry requires a concrete run directory")
    entry = _entry_for_plan(plan, created_at)
    metadata_path = plan.run_dir / ".control" / "catalog.json"
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailure(
                f"Cannot read existing catalog metadata: {metadata_path}"
            ) from exc
        validate_document(existing, "catalog-entry")
        if existing != entry:
            raise ValidationFailure(
                f"Existing catalog metadata differs for {plan.run_id}"
            )
    else:
        atomic_write_json(metadata_path, entry, mode=0o600)

    relative_link = PurePosixPath(entry["catalog_path"])
    parent = _ensure_directory_tree(
        plan.inventory.storage_root, relative_link.parent
    )
    link_path = parent / relative_link.name
    _write_relative_symlink(link_path, plan.run_dir)
    return link_path
