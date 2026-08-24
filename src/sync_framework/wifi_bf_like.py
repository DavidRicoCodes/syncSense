"""Validation rules for the WiFi BF-like Golay52 hardware experiment."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .domain import PublicationFailure


NUM_BF_LTF = 8
NUM_ACTIVE_SUBCARRIERS = 52
CSI_ELEMENTS_PER_FRAME = NUM_BF_LTF * NUM_ACTIVE_SUBCARRIERS
COMPLEX64_BYTES = 8

EXPECTED_SCHEMA = "alb_bf_like_golay52_sounding_v2"
EXPECTED_PROFILE = "alb_bf_like_golay52_siso_20mhz_v2"
EXPECTED_TRAINING_SEQUENCE_ID = 0x5201


def _read_jsonl(path: Path, description: str) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicationFailure(f"Cannot read {description}: {path}") from exc

    if raw and not raw.endswith(b"\n"):
        raise PublicationFailure(f"{description} has a truncated final line")

    rows: list[dict[str, Any]] = []
    for number, encoded in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationFailure(
                f"Invalid {description} JSON at row {number}"
            ) from exc

        if not isinstance(value, dict):
            raise PublicationFailure(
                f"Invalid {description} row {number}"
            )

        rows.append(value)

    return rows


def _require_zero_summary(log: str, label: str) -> None:
    match = re.search(
        rf"^{re.escape(label)}\s*:\s*(\d+)\s*$",
        log,
        re.MULTILINE | re.IGNORECASE,
    )
    if match is None or int(match.group(1)) != 0:
        raise PublicationFailure(
            f"BF RX summary does not prove zero {label.lower()}"
        )


def validate_bf_like_rx_outputs(
    run_dir: Path,
    *,
    num_packets: int,
    minimum_ratio: float,
) -> dict[str, Any]:
    rx_dir = run_dir / "rx_wifi"

    rows = _read_jsonl(
        rx_dir / "features.jsonl",
        "WiFi BF-like feature JSONL",
    )

    frame_timings = _read_jsonl(
        rx_dir / "frame-timings.jsonl",
        "WiFi BF-like frame timing JSONL",
    )

    block_timings = _read_jsonl(
        rx_dir / "block-timings.jsonl",
        "WiFi BF-like block timing JSONL",
    )

    counters: list[int] = []

    for number, row in enumerate(rows, 1):
        expected = {
            "schema": EXPECTED_SCHEMA,
            "waveform_type_name": "bf_like",
            "profile_name": EXPECTED_PROFILE,
            "profile_id": 2,
            "feature_name": "bf_ltf_csi",
            "feature_dtype": "complex64",
            "feature_shape": [NUM_BF_LTF, NUM_ACTIVE_SUBCARRIERS],
            "feature_flatten_order": "C",
            "feature_count": CSI_ELEMENTS_PER_FRAME,
            "valid": True,
        }

        if any(row.get(key) != value for key, value in expected.items()):
            raise PublicationFailure(
                f"Invalid BF-like row contract at row {number}"
            )

        counter = row.get("packet_counter")
        if (
            not isinstance(counter, int)
            or isinstance(counter, bool)
            or not 0 <= counter < num_packets
        ):
            raise PublicationFailure(
                f"Invalid BF-like packet_counter at row {number}"
            )

        sample_offset = row.get("sample_offset")
        if (
            not isinstance(sample_offset, int)
            or isinstance(sample_offset, bool)
            or sample_offset < 0
        ):
            raise PublicationFailure(
                f"Invalid BF-like sample_offset at row {number}"
            )

        features = row.get("complex_features")
        if (
            not isinstance(features, list)
            or len(features) != CSI_ELEMENTS_PER_FRAME
        ):
            raise PublicationFailure(
                f"BF-like row {number} must contain "
                f"{CSI_ELEMENTS_PER_FRAME} complex CSI values"
            )

        for feature in features:
            if (
                not isinstance(feature, dict)
                or not isinstance(feature.get("real"), (int, float))
                or not isinstance(feature.get("imag"), (int, float))
            ):
                raise PublicationFailure(
                    f"Invalid BF-like complex feature at row {number}"
                )

        numeric = row.get("numeric_metadata")
        text = row.get("text_metadata")

        if not isinstance(numeric, dict) or not isinstance(text, dict):
            raise PublicationFailure(
                f"Missing BF-like metadata at row {number}"
            )

        numeric_expected = {
            "num_bf_ltf": float(NUM_BF_LTF),
            "training_sequence_id": float(EXPECTED_TRAINING_SEQUENCE_ID),
            "golay_pair_length": 52.0,
            "golay_pair_repetitions": 4.0,
            "bf_ltf_common_phase_aligned": 1.0,
        }

        if any(
            numeric.get(key) != value
            for key, value in numeric_expected.items()
        ):
            raise PublicationFailure(
                f"Invalid BF-like numeric metadata at row {number}"
            )

        if (
            text.get("training_family")
            != "golay_complementary_52_subcarrier"
        ):
            raise PublicationFailure(
                f"Invalid BF-like training family at row {number}"
            )

        counters.append(counter)

    if counters != sorted(set(counters)):
        raise PublicationFailure(
            "BF-like packet counters must be unique and strictly increasing"
        )

    if len(frame_timings) != len(rows):
        raise PublicationFailure(
            "BF-like frame timing count does not match feature rows"
        )

    if not block_timings:
        raise PublicationFailure(
            "BF-like block timing JSONL must contain at least one row"
        )

    required = math.ceil(num_packets * minimum_ratio)

    if len(rows) < required:
        raise PublicationFailure(
            "BF-like reception below required ratio: "
            f"{len(rows)} < {required}"
        )

    csi_path = rx_dir / "csi.cf32"
    expected_size = (
        len(rows)
        * CSI_ELEMENTS_PER_FRAME
        * COMPLEX64_BYTES
    )

    if (
        not csi_path.is_file()
        or csi_path.stat().st_size != expected_size
    ):
        raise PublicationFailure(
            "BF-like CF32 size does not match JSONL rows"
        )

    try:
        log = (rx_dir / "process.log").read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise PublicationFailure(
            "Cannot read BF-like RX process log"
        ) from exc

    for label in (
        "Overflows",
        "Timeouts",
        "Discontinuidades",
    ):
        _require_zero_summary(log, label)

    saved = re.search(
        r"^Guardados JSONL\s*:\s*(\d+)\s*$",
        log,
        re.MULTILINE,
    )

    if saved is None or int(saved.group(1)) != len(rows):
        raise PublicationFailure(
            "BF-like RX summary row count does not match JSONL"
        )

    return {
        "packets_requested": num_packets,
        "frames_received": len(rows),
        "frames_required": required,
        "frames_lost": num_packets - len(rows),
        "receive_ratio": len(rows) / num_packets,
        "csi_elements_per_frame": CSI_ELEMENTS_PER_FRAME,
        "feature_shape": [NUM_BF_LTF, NUM_ACTIVE_SUBCARRIERS],
        "training_family": "golay_complementary_52_subcarrier",
    }


def validate_bf_like_tx_outputs(
    run_dir: Path,
    *,
    num_packets: int,
) -> dict[str, Any]:
    path = run_dir / "tx_wifi" / "state.json"

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationFailure(
            "BF-like TX state is missing or invalid"
        ) from exc

    if state.get("mode") != "bf":
        raise PublicationFailure(
            "BF-like TX state does not report mode=bf"
        )

    builder = state.get("packet_builder")
    if not isinstance(builder, dict):
        raise PublicationFailure(
            "BF-like TX state lacks packet builder metadata"
        )

    if (
        builder.get("profile") != EXPECTED_PROFILE
        or builder.get("training_sequence_id")
        != EXPECTED_TRAINING_SEQUENCE_ID
        or builder.get("num_bf_ltf") != NUM_BF_LTF
    ):
        raise PublicationFailure(
            "BF-like TX waveform contract mismatch"
        )

    if int(state.get("sent_packets", -1)) != num_packets:
        raise PublicationFailure(
            "BF-like TX did not send the requested packet count"
        )

    if int(state.get("total_zero_sends", -1)) != 0:
        raise PublicationFailure(
            "BF-like TX contains zero-send failures"
        )

    fatal_events: dict[str, int] = {}

    for name, count in state.get(
        "async_event_counts",
        {},
    ).items():
        value = int(count)

        if value <= 0:
            continue

        # Burst acknowledgements are successful UHD telemetry.
        if "burst_ack" in name.lower():
            continue

        fatal_events[name] = value

    if fatal_events:
        raise PublicationFailure(
            "BF-like TX contains fatal UHD async events: "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(fatal_events.items())
            )
        )

    if state.get("valid") is not True or state.get("error") is not None:
        raise PublicationFailure(
            "BF-like TX final state is not valid"
        )

    return {
        "sent_packets": num_packets,
        "zero_sends": 0,
        "fatal_async_events": {},
        "tx_strategy": state.get("tx_strategy"),
    }
