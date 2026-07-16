"""Local run layout and durable atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .domain import ValidationFailure
from .run_id import validate_run_id


def run_directory(storage_root: Path, run_id: str) -> Path:
    validate_run_id(run_id)
    return storage_root / "runs" / run_id


def create_run_layout(storage_root: Path, run_id: str, producer_ids: list[str]) -> Path:
    run_dir = run_directory(storage_root, run_id)
    if run_dir.exists():
        raise ValidationFailure(f"Run directory already exists: {run_dir}")
    control = run_dir / ".control"
    (control / "logs").mkdir(parents=True, mode=0o700)
    control.chmod(0o700)
    for producer_id in producer_ids:
        (run_dir / producer_id).mkdir(mode=0o750)
    return run_dir


def atomic_write_json(path: str | Path, value: Any, *, mode: int = 0o640) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()

