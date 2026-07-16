#!/usr/bin/env python3
"""Standalone, harmless distributed worker (Python 3.10+, stdlib only)."""

from __future__ import annotations

import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.tmp.%d" % (path.name, os.getpid()))
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def start_ticks(pid: int) -> int:
    stat = Path("/proc/%d/stat" % pid).read_text(encoding="utf-8")
    return int(stat.rsplit(")", 1)[1].split()[19])


def same_process(pid: int, ticks: int) -> bool:
    try:
        return start_ticks(pid) == ticks
    except (OSError, ValueError, IndexError):
        return False


def append_trace(output: Path, producer_id: str, event: str) -> None:
    trace = output.parent / ".control" / "worker-events.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    with trace.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps({
            "producer_id": producer_id, "event": event, "at": utc_now(),
            "host": socket.gethostname(), "simulation": True,
        }, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def decode_spec(encoded: str) -> dict:
    padding = "=" * (-len(encoded) % 4)
    value = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    required = {"run_id", "producer_id", "role", "modality", "output_dir", "artifact_ids"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("invalid worker specification")
    if value["role"] not in {"receiver", "transmitter"} or value["modality"] not in {"5g", "wifi"}:
        raise ValueError("invalid worker role or modality")
    return value


def write_outputs(spec: dict, output: Path) -> list[Path]:
    producer = spec["producer_id"]
    paths: list[Path] = []
    if spec["role"] == "receiver":
        features = output / "features.bin"
        features.write_bytes(("synthetic capture from %s on %s\n" % (producer, socket.gethostname())).encode())
        paths.append(features)
        rate = 1_000_000
        base = 10_000 if spec["modality"] == "5g" else 80_000
        artifact_id = spec["artifact_ids"]["features"]
        events = output / "events.jsonl"
        with events.open("w", encoding="utf-8") as handle:
            for sequence in range(3):
                block = base + sequence * rate
                offset = 32 + sequence
                event = {
                    "schema_version": "1.0.0", "run_id": spec["run_id"],
                    "event_id": "%s-%06d" % (producer, sequence), "producer_id": producer,
                    "sequence": sequence, "modality": spec["modality"],
                    "frame_type": "synthetic_5g_frame" if spec["modality"] == "5g" else "synthetic_wifi_frame",
                    "clock_domain_id": spec["clock_domain_id"],
                    "timestamp": {"ticks": block + offset, "tick_rate_hz": rate},
                    "reference_point": "synthetic_frame_start",
                    "detector": {"block_start_ticks": block, "offset_samples": offset, "sample_rate_hz": rate, "uncertainty_ticks": 1},
                    "capture_discontinuity": {"present": False},
                    "artifact_ref": {"artifact_id": artifact_id, "row_index": sequence},
                    "host_observed_at": utc_now(), "host_time_semantics": "operational_only",
                }
                handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        paths.append(events)
        metrics = {"producer_id": producer, "events": 3, "simulation": True, "synthetic": True}
    else:
        metrics = {"producer_id": producer, "transmissions": 0, "simulation": True, "synthetic": True}
    metrics_path = output / "metrics.json"
    atomic_json(metrics_path, metrics)
    paths.append(metrics_path)
    return paths


def run_worker(encoded: str) -> int:
    spec = decode_spec(encoded)
    output = Path(spec["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / "runtime"
    runtime.mkdir(exist_ok=True)
    try:
        os.setsid()
    except OSError:
        pass
    pid = os.getpid()
    ticks = start_ticks(pid)
    identity = {"pid": pid, "proc_start_ticks": ticks, "host": socket.gethostname()}
    atomic_json(runtime / "process.json", identity)
    append_trace(output, spec["producer_id"], "started")
    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    ready_delay = float(spec.get("ready_delay_s", 0.1))
    started = time.monotonic()
    while time.monotonic() - started < ready_delay:
        time.sleep(0.02)
    atomic_json(runtime / "status.json", {"status": "ready", "producer_id": spec["producer_id"], "at": utc_now(), **identity})
    append_trace(output, spec["producer_id"], "ready")
    heartbeat_at = 0.0
    while not stopping:
        now = time.monotonic()
        if now - heartbeat_at >= 0.5:
            atomic_json(runtime / "heartbeat.json", {"status": "running", "at": utc_now(), **identity})
            heartbeat_at = now
        time.sleep(0.05)
    append_trace(output, spec["producer_id"], "stopping")
    outputs = write_outputs(spec, output)
    receipt = {
        "schema_version": "1.0.0", "run_id": spec["run_id"], "producer_id": spec["producer_id"],
        "node_id": spec["node_id"], "simulation": True, "synthetic": True, "exit_code": 0,
        "finished_at": utc_now(), "process": identity,
        "artifacts": [{"path": path.name, "size_bytes": path.stat().st_size, "sha256": sha256(path)} for path in outputs],
    }
    atomic_json(output / "producer-result.json", receipt)
    atomic_json(runtime / "status.json", {"status": "stopped", "producer_id": spec["producer_id"], "at": utc_now(), **identity})
    append_trace(output, spec["producer_id"], "stopped")
    return 0


def control(action: str, runtime_dir: str, pid: int, ticks: int) -> int:
    if not same_process(pid, ticks):
        print(json.dumps({"running": False, "identity_match": False}))
        return 0 if action == "probe" else 3
    if action == "probe":
        print(json.dumps({"running": True, "identity_match": True}))
        return 0
    signum = signal.SIGTERM if action == "term" else signal.SIGKILL
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass
    print(json.dumps({"running": same_process(pid, ticks), "identity_match": True}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--spec", required=True)
    ctl = sub.add_parser("control")
    ctl.add_argument("--action", choices=["probe", "term", "kill"], required=True)
    ctl.add_argument("--runtime-dir", required=True)
    ctl.add_argument("--pid", type=int, required=True)
    ctl.add_argument("--start-ticks", type=int, required=True)
    args = parser.parse_args()
    if args.command == "run":
        return run_worker(args.spec)
    return control(args.action, args.runtime_dir, args.pid, args.start_ticks)


if __name__ == "__main__":
    sys.exit(main())
