"""Deterministic HE NDP-like BF artifacts for integration tests only."""

from __future__ import annotations

import json
from pathlib import Path


def feature_row(*, experiment_id: int, counter: int = 0) -> dict:
    row = {
        "protocol_version": 1, "waveform_type": 2, "profile_id": 3,
        "transmitter_id": 1, "experiment_id": experiment_id,
        "schema": "alb_he_ndp_like_sounding_v1",
        "waveform_type_name": "he_ndp_like",
        "profile_name": "alb_he_ndp_like_siso_40mhz_v1",
        "session_id": 1, "receiver_group_id": 0,
        "tx_timestamp_ns": 0, "scheduled_tx_time_ns": 0,
        "feature_name": "he_ltf_csi", "feature_dtype": "complex64",
        "feature_shape": [8, 242], "feature_flatten_order": "C",
        "feature_count": 1936, "valid": True, "error": "",
        "packet_counter": counter, "sample_offset": 1100,
        "has_rx_device_time": True, "rx_block_start_ticks": 1000,
        "rx_timestamp_ticks": 1100, "rx_tick_rate_hz": 40_000_000,
        "rx_timestamp_ns": 27_500, "sample_rate_hz": 40_000_000.0,
        "center_frequency_hz": 2_462_000_000.0, "snr_db": 20.0,
        "cfo_hz": 10.0, "power_dbfs": -20.0,
        "numeric_metadata": {
            "num_he_ltf": 8.0, "he_ltf_tones": 242.0,
            "alb_control_bytes": 27.0, "alb_codeword_repetitions": 2.0,
            "packet_duration_samples": 5440.0, "fcs_valid": 1.0,
            "preamble_metric": 0.9, "lltf_timing_metric": 0.8,
            "he_ltf_repetition_metric": 0.98,
            "coarse_cfo_hz": 9.0, "fine_cfo_hz": 1.0,
        },
        "text_metadata": {
            "magic": "ALBF", "training_family": "he_ltf_2x_40mhz",
        },
        "complex_features": [{"real": 0.25, "imag": -0.25}] * 1936,
        "real_features": [], "payload": [0] * 27,
    }
    return row


def write_receiver(output: Path, *, experiment_id: int) -> None:
    row = feature_row(experiment_id=experiment_id)
    output.mkdir(parents=True, exist_ok=True)
    (output / "features.jsonl").write_text(json.dumps(row) + "\n")
    scalar_names = (
        "host_received_steady_ns", "processing_started_steady_ns",
        "json_finished_steady_ns", "csi_finished_steady_ns", "queue_wait_us",
        "block_processing_us", "json_write_us", "csi_write_us",
        "output_total_us", "block_received_to_json_us",
        "block_received_to_csi_us", "packet_duration_us",
        "packet_start_to_json_us", "packet_start_to_csi_us",
        "event_monotonic_ns", "packet_end_to_json_us", "packet_end_to_csi_us",
    )
    timing = {
        "schema": "waveform_frame_timing_v1", "waveform_type": "he_ndp_like",
        "profile_name": row["profile_name"], "packet_counter": 0,
        "sample_offset": 1100, "block_first_sample": 1000,
        "block_sample_count": 200_000, "has_rx_device_time": True,
        "block_start_device_ticks": 1000, "detector_offset_samples": 100,
        "event_device_ticks": 1100, "device_tick_rate_hz": 40_000_000,
        "reference_point": "waveform_start",
        "canonical_timestamp_semantics": "local_usrp_device_time_first_sample_plus_detector_offset_samples",
        "event_monotonic_semantics": "operational_host_estimate_only_not_acquisition_time",
        "radio_time_semantics": "host_operational_estimate_only_includes_usb_delivery_uncertainty_not_acquisition_alignment",
        **{name: 1 for name in scalar_names},
    }
    (output / "frame-timings.jsonl").write_text(json.dumps(timing) + "\n")
    block = {
        "schema": "waveform_block_timing_v1", "first_sample": 1000,
        "sample_count": 200_000, "has_rx_device_time": True,
        "block_start_device_ticks": 1000, "device_tick_rate_hz": 40_000_000,
        "host_received_steady_ns": 1, "queue_wait_us": 1,
        "processing_us": 1, "block_total_us": 1, "candidates": 1,
        "synchronized": 1, "decoded": 1, "frames": 1,
        "queue_depth_after": 0, "overflow": False, "discontinuity": False,
    }
    (output / "block-timings.jsonl").write_text(json.dumps(block) + "\n")
    (output / "csi.cf32").write_bytes(b"\0" * 1936 * 8)
    (output / "process.log").write_text(
        "Overflows : 0\nTimeouts : 0\nDiscontinuidades : 0\nGuardados JSONL : 1\n"
    )


def write_transmitter(output: Path, *, experiment_id: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "wifi_tx_v2", "role": "tx", "mode": "he_ndp_like",
        "status": "stopped", "valid": True, "error": None,
        "tx_strategy": "timed", "num_packets_requested": 1,
        "sent_packets": 1, "total_zero_sends": 0, "period_ms": 100.0,
        "gain_db": 65.0, "sample_rate_hz": 40_000_000.0,
        "hardware_rate_hz": 40_960_000.0, "bandwidth_hz": 40_000_000.0,
        "async_event_counts": {"burst_ack": 1},
        "packet_builder": {
            "mode": "he_ndp_like", "profile": "alb_he_ndp_like_siso_40mhz_v1",
            "num_he_ltf": 8, "he_ltf_csi_shape": [8, 242],
            "packet_samples": 5440, "sample_rate_hz": 40_000_000.0,
            "strict_ieee_ndp": False,
            "experiment_id": experiment_id,
        },
    }
    (output / "state.json").write_text(json.dumps(state) + "\n")
    (output / "first-packet.npz").write_bytes(b"fake-npz")
    (output / "process.log").write_text(
        "amplitude peak target: 0.800\nTX stopped.\nSent packets: 1\n"
    )
