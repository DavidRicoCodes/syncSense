"""Run identifier generation and validation."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from .domain import ValidationFailure


RUN_ID_RE = re.compile(r"^run_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{12}$")


def generate_run_id(now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"run_{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:12]}"


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValidationFailure(f"Invalid run_id: {run_id}")
    return run_id

