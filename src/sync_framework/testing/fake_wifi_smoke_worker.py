"""Harmless local worker used to exercise finite WiFi-smoke orchestration."""

from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["receiver", "transmitter"], required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    (output / "runtime").mkdir(parents=True, exist_ok=True)
    if args.role == "transmitter":
        time.sleep(0.1)
        (output / "process.log").write_text("Beacons incluidos : 1\nZero sends : 0\n")
        return 0
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    (output / "runtime" / "status.json").write_text(json.dumps({"status": "ready"}) + "\n")
    row = {
        "packet_counter": 0,
        "sample_offset": 0,
        "complex_features": [{"real": 0.0, "imag": 0.0}] * 52,
    }
    (output / "features.jsonl").write_text(json.dumps(row) + "\n")
    frame_timing = {
        "schema": "wifi_frame_timing_v1",
        "packet_counter": 0,
        "sample_offset": 0,
        "block_first_sample": 0,
        "block_sample_count": 200000,
        "host_received_steady_ns": 1000,
        "processing_started_steady_ns": 2000,
        "json_finished_steady_ns": 3000,
        "csi_finished_steady_ns": 4000,
        "queue_wait_us": 1,
        "block_processing_us": 2,
        "json_write_us": 1,
        "csi_write_us": 1,
        "output_total_us": 2,
        "block_received_to_json_us": 3,
        "block_received_to_csi_us": 4,
        "packet_duration_us": 100,
        "packet_start_to_json_us": 103,
        "packet_start_to_csi_us": 104,
        "packet_end_to_json_us": 3,
        "packet_end_to_csi_us": 4,
        "radio_time_semantics": (
            "estimated_from_block_end_host_delivery_and_sample_"
            "offset_includes_usb_host_delivery_uncertainty"
        ),
    }
    block_timing = {
        "schema": "wifi_block_timing_v1",
        "first_sample": 0,
        "sample_count": 200000,
        "host_received_steady_ns": 1000,
        "queue_wait_us": 1,
        "processing_us": 2,
        "block_total_us": 4,
        "candidates": 1,
        "synchronized": 1,
        "decoded": 1,
        "frames": 1,
        "queue_depth_after": 0,
        "overflow": False,
        "discontinuity": False,
    }
    (output / "frame-timings.jsonl").write_text(json.dumps(frame_timing) + "\n")
    (output / "block-timings.jsonl").write_text(json.dumps(block_timing) + "\n")
    while not stopping:
        time.sleep(0.02)
    (output / "process.log").write_text(
        "Overflows : 0\nTimeouts : 0\nDiscontinuidades : 0\nGuardados JSONL : 1\n"
    )
    (output / "csi.cf32").write_bytes(b"\0" * 52 * 8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
