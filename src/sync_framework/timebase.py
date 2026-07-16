"""Exact integer timestamp helpers."""

from __future__ import annotations

from .domain import ValidationFailure


def frame_timestamp_ticks(block_start_ticks: int, detector_offset_samples: int, sample_rate_hz: int) -> dict[str, int]:
    if block_start_ticks < 0 or detector_offset_samples < 0 or sample_rate_hz <= 0:
        raise ValidationFailure("Timestamp inputs must be non-negative and sample_rate_hz positive")
    return {"ticks": block_start_ticks + detector_offset_samples, "tick_rate_hz": sample_rate_hz}

