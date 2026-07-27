from __future__ import annotations

import json
import os

import pytest

from sync_framework.catalog import (
    catalog_component,
    existing_catalog_path_for_plan,
    catalog_path_for_plan,
    catalog_relative_path,
    create_catalog_entry,
)
from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.domain import ValidationFailure
from sync_framework.planning import build_plan
from sync_framework.storage import create_run_layout
from sync_framework.validation import validate_document


RUN_ID = "run_20260727T101457916717Z_fdf8d7f13137"


def test_catalog_path_uses_normalized_experiment_dimensions():
    relative = catalog_relative_path(
        RUN_ID,
        "nosync_passive",
        {
            "testbed_id": "testbed1",
            "condition": "occupied_static",
            "position": "R5C6",
            "subject_id": "anonymous",
        },
    )
    assert relative.as_posix() == (
        "catalog/testbed=testbed1/experiment=nosync_passive/"
        "condition=occupied_static/position=R5C6/subject=anonymous/"
        f"2026/07/27/{RUN_ID}"
    )


def test_catalog_components_cannot_escape_or_collide_with_plain_value():
    unsafe = catalog_component("../../Álvaro/uno")
    assert "/" not in unsafe
    assert ".." not in unsafe
    assert unsafe != catalog_component("Alvaro-uno")
    assert len(unsafe) <= 64


def test_catalog_path_requires_generated_run(inventory_path, profile_path):
    inventory = load_inventory(inventory_path)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(profile, {"label": "x", "duration_s": "1"})
    plan = build_plan(inventory, profile, parameters)
    with pytest.raises(ValidationFailure, match="concrete run_id"):
        catalog_path_for_plan(plan)


def test_catalog_entry_links_to_canonical_run_and_is_idempotent(
    inventory_path, profile_path
):
    inventory = load_inventory(inventory_path)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(
        profile, {"label": "catalog-test", "duration_s": "1"}
    )
    run_dir = create_run_layout(
        inventory.storage_root, RUN_ID, list(profile.processes)
    )
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id=RUN_ID,
        run_dir=run_dir,
    )

    link = create_catalog_entry(
        plan, created_at="2026-07-27T10:14:57.916717+00:00"
    )
    assert link.is_symlink()
    assert link.resolve() == run_dir.resolve()
    assert not os.readlink(link).startswith("/")

    metadata = json.loads(
        (run_dir / ".control" / "catalog.json").read_text(encoding="utf-8")
    )
    validate_document(metadata, "catalog-entry")
    assert metadata["run_id"] == RUN_ID
    assert metadata["label"] == "catalog-test"
    assert metadata["dimensions"] == {
        "testbed_id": "unspecified",
        "experiment_type": "nosync_passive_simulated",
        "condition": "unspecified",
        "position": "unspecified",
        "subject_id": "unspecified",
    }
    assert create_catalog_entry(
        plan, created_at="2026-07-27T10:14:57.916717+00:00"
    ) == link
    assert existing_catalog_path_for_plan(plan) == link
    link.unlink()
    assert existing_catalog_path_for_plan(plan) is None


def test_catalog_rejects_symlink_in_hierarchy(inventory_path, profile_path, tmp_path):
    inventory = load_inventory(inventory_path)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(profile, {"label": "x", "duration_s": "1"})
    run_dir = create_run_layout(
        inventory.storage_root, RUN_ID, list(profile.processes)
    )
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id=RUN_ID,
        run_dir=run_dir,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (inventory.storage_root / "catalog").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(ValidationFailure, match="non-directory"):
        create_catalog_entry(
            plan, created_at="2026-07-27T10:14:57.916717+00:00"
        )


def test_catalog_rejects_conflicting_existing_link(inventory_path, profile_path):
    inventory = load_inventory(inventory_path)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(profile, {"label": "x", "duration_s": "1"})
    run_dir = create_run_layout(
        inventory.storage_root, RUN_ID, list(profile.processes)
    )
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id=RUN_ID,
        run_dir=run_dir,
    )
    link = create_catalog_entry(
        plan, created_at="2026-07-27T10:14:57.916717+00:00"
    )
    link.unlink()
    link.write_text("conflict", encoding="utf-8")

    with pytest.raises(ValidationFailure, match="already exists"):
        create_catalog_entry(
            plan, created_at="2026-07-27T10:14:57.916717+00:00"
        )


def test_catalog_rejects_corrupt_existing_metadata(inventory_path, profile_path):
    inventory = load_inventory(inventory_path)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(profile, {"label": "x", "duration_s": "1"})
    run_dir = create_run_layout(
        inventory.storage_root, RUN_ID, list(profile.processes)
    )
    plan = build_plan(
        inventory,
        profile,
        parameters,
        run_id=RUN_ID,
        run_dir=run_dir,
    )
    metadata = run_dir / ".control" / "catalog.json"
    metadata.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValidationFailure, match="Cannot read"):
        create_catalog_entry(
            plan, created_at="2026-07-27T10:14:57.916717+00:00"
        )
