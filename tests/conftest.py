from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile_path() -> Path:
    return REPO_ROOT / "profiles" / "nosync_passive.yaml"


@pytest.fixture
def inventory_path(tmp_path: Path) -> Path:
    value = yaml.safe_load((REPO_ROOT / "config" / "inventory.example.yaml").read_text(encoding="utf-8"))
    value["storage"]["root"] = str(tmp_path / "storage")
    for node in value["nodes"]:
        node["workspace"] = str(REPO_ROOT)
        for command in node["commands"]:
            command["argv"][0] = sys.executable
            command["cwd"] = str(REPO_ROOT)
    path = tmp_path / "inventory.local.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path

