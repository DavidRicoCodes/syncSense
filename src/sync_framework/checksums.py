"""Safe file and document hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .domain import PublicationFailure


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise PublicationFailure(f"Artifact must be a regular non-symlink file: {target}")
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()

