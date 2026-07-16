"""Harmless local worker used to validate orchestration without DSP or hardware."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

from sync_framework.state import utc_now
from sync_framework.storage import atomic_write_json
from sync_framework.timebase import frame_timestamp_ticks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SYNC simulation worker")
    parser.add_argument("--producer-id", required=True)
    parser.add_argument("--role", choices=["receiver", "transmitter"], required=True)
    parser.add_argument("--modality", choices=["5g", "wifi"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--clock-domain-id", default="")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--ready-delay", type=float, default=0.05)
    parser.add_argument("--exit-after", type=float, default=0.0)
    parser.add_argument("--fail-start", action="store_true")
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--omit-artifact", action="store_true")
    return parser.parse_args()


def append_trace(output_dir: Path, producer_id: str, event: str) -> None:
    trace = output_dir.parent / ".control" / "worker-events.jsonl"
    line = json.dumps({"producer_id": producer_id, "event": event, "at": utc_now()}, sort_keys=True) + "\n"
    fd = os.open(trace, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def write_receiver_outputs(args: argparse.Namespace, output_dir: Path) -> None:
    rate = 15_360_000 if args.modality == "5g" else 20_000_000
    base = 1000 if args.modality == "5g" else 5000
    frame_type = "ssb" if args.modality == "5g" else "wifi_beacon"
    reference = "ssb_pss_start" if args.modality == "5g" else "wifi_ppdu_start"
    if not args.omit_artifact:
        (output_dir / "features.bin").write_bytes((args.producer_id + "\n").encode("utf-8"))
    events = []
    for sequence in range(3):
        block_start = base + sequence * rate // 10
        offset = 64 + sequence
        events.append({
            "schema_version": "1.0.0", "run_id": args.run_id,
            "event_id": f"{args.producer_id}-{sequence:06d}", "producer_id": args.producer_id,
            "sequence": sequence, "modality": args.modality, "frame_type": frame_type,
            "clock_domain_id": args.clock_domain_id,
            "timestamp": frame_timestamp_ticks(block_start, offset, rate),
            "reference_point": reference,
            "detector": {"block_start_ticks": block_start, "offset_samples": offset, "sample_rate_hz": rate, "uncertainty_ticks": 1},
            "capture_discontinuity": {"present": False},
            "artifact_ref": {"artifact_id": args.artifact_id, "row_index": sequence},
            "host_observed_at": utc_now(),
            "host_time_semantics": "operational_only",
        })
    (output_dir / "events.jsonl").write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events), encoding="utf-8")
    atomic_write_json(output_dir / "metrics.json", {"producer_id": args.producer_id, "events": len(events), "simulation": True})


def write_transmitter_outputs(args: argparse.Namespace, output_dir: Path) -> None:
    if not args.omit_artifact:
        atomic_write_json(output_dir / "metrics.json", {"producer_id": args.producer_id, "simulation": True, "transmissions": 0})


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    append_trace(output_dir, args.producer_id, "started")
    if args.fail_start:
        return 23
    stop = False

    def handle_term(_signum, _frame):
        nonlocal stop
        if not args.ignore_term:
            stop = True

    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGINT, handle_term)
    time.sleep(max(0.0, args.ready_delay))
    (output_dir / "runtime").mkdir(exist_ok=True)
    atomic_write_json(output_dir / "runtime" / "status.json", {"status": "ready", "producer_id": args.producer_id, "at": utc_now()})
    append_trace(output_dir, args.producer_id, "ready")
    started = time.monotonic()
    while not stop:
        if args.exit_after > 0 and time.monotonic() - started >= args.exit_after:
            return 24
        time.sleep(0.05)
    append_trace(output_dir, args.producer_id, "stopping")
    if args.role == "receiver":
        write_receiver_outputs(args, output_dir)
    else:
        write_transmitter_outputs(args, output_dir)
    atomic_write_json(output_dir / "runtime" / "status.json", {"status": "stopped", "producer_id": args.producer_id, "at": utc_now()})
    append_trace(output_dir, args.producer_id, "stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
