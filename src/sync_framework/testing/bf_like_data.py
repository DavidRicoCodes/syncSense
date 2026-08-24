"""Deterministic valid BF-like artifacts for integration tests only."""

from __future__ import annotations

import json
from pathlib import Path


def feature_row(*, experiment_id: int, counter: int = 0) -> dict:
    row = {
        "protocol_version": 1, "waveform_type": 2, "profile_id": 2,
        "transmitter_id": 1, "experiment_id": experiment_id,
        "schema": "alb_bf_like_golay52_sounding_v2",
        "waveform_type_name": "bf_like",
        "profile_name": "alb_bf_like_golay52_siso_20mhz_v2",
        "session_id": 1, "receiver_group_id": 0,
        "tx_timestamp_ns": 0, "scheduled_tx_time_ns": 0,
        "feature_name": "bf_ltf_csi", "feature_dtype": "complex64",
        "feature_shape": [8, 52], "feature_flatten_order": "C",
        "feature_count": 416, "valid": True, "error": "",
        "packet_counter": counter, "sample_offset": 1100,
        "has_rx_device_time": True, "rx_block_start_ticks": 1000,
        "rx_timestamp_ticks": 1100, "rx_tick_rate_hz": 20_000_000,
        "rx_timestamp_ns": 55_000, "sample_rate_hz": 20_000_000.0,
        "center_frequency_hz": 2_462_000_000.0, "snr_db": 20.0,
        "cfo_hz": 10.0, "power_dbfs": -20.0,
        "numeric_metadata": {
            "burst_id": 0.0, "sounding_index": 0.0, "beam_id": 0.0,
            "codebook_id": 0.0, "antenna_mask": 1.0,
            "num_tx_antennas": 1.0, "num_rx_antennas_expected": 1.0,
            "num_spatial_streams": 1.0, "num_bf_ltf": 8.0,
            "training_sequence_id": 0x5201, "training_seed": 93.0,
            "bandwidth_hz": 20_000_000.0, "tx_gain_db": 60.0,
            "tx_channel": 0.0, "tx_antenna_id": 0.0,
            "frame_period_us": 100_000.0, "packet_duration_samples": 4640.0,
            "header_crc_valid": 1.0, "frame_fcs_valid": 1.0,
            "stf_metric": 0.95, "preamble_metric": 0.9,
            "coarse_cfo_hz": 9.0, "fine_cfo_hz": 1.0,
            "bf_ltf_signal_power": 1.0, "bf_ltf_noise_power": 0.01,
            "bf_ltf_common_phase_aligned": 1.0,
            "golay_pair_length": 52.0, "golay_pair_repetitions": 4.0,
        },
        "text_metadata": {
            "magic": "ALBBFLK1", "bf_sig_magic": "ABFS",
            "training_family": "golay_complementary_52_subcarrier",
        },
        "complex_features": [{"real": 0.25, "imag": -0.25}] * 416,
        "real_features": [], "payload": [],
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
        "schema": "waveform_frame_timing_v1", "waveform_type": "bf_like",
        "profile_name": row["profile_name"], "packet_counter": 0,
        "sample_offset": 1100, "block_first_sample": 1000,
        "block_sample_count": 200_000, "has_rx_device_time": True,
        "block_start_device_ticks": 1000, "detector_offset_samples": 100,
        "event_device_ticks": 1100, "device_tick_rate_hz": 20_000_000,
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
        "block_start_device_ticks": 1000, "device_tick_rate_hz": 20_000_000,
        "host_received_steady_ns": 1, "queue_wait_us": 1,
        "processing_us": 1, "block_total_us": 1, "candidates": 1,
        "synchronized": 1, "decoded": 1, "frames": 1,
        "queue_depth_after": 0, "overflow": False, "discontinuity": False,
    }
    (output / "block-timings.jsonl").write_text(json.dumps(block) + "\n")
    (output / "csi.cf32").write_bytes(b"\0" * 416 * 8)
    (output / "process.log").write_text(
        "Overflows : 0\nTimeouts : 0\nDiscontinuidades : 0\nGuardados JSONL : 1\n"
    )


def write_transmitter(output: Path, *, experiment_id: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "wifi_tx_v2", "role": "tx", "mode": "bf",
        "status": "stopped", "valid": True, "error": None,
        "tx_strategy": "streamed", "num_packets_requested": 1,
        "sent_packets": 1, "total_zero_sends": 0, "period_ms": 100.0,
        "gain_db": 60.0, "async_event_counts": {"burst_ack": 1},
        "packet_builder": {
            "mode": "bf", "profile": "alb_bf_like_golay52_siso_20mhz_v2",
            "training_sequence_id": 0x5201, "num_bf_ltf": 8,
            "experiment_id": experiment_id,
        },
    }
    (output / "state.json").write_text(json.dumps(state) + "\n")
    (output / "first-packet.npz").write_bytes(b"fake-npz")
    (output / "process.log").write_text(
        "amplitude peak target: 0.600\nTX stopped.\nSent packets: 1\n"
    )
