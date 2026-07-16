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
    row = {"packet_counter": 0, "complex_features": [{"real": 0.0, "imag": 0.0}] * 52}
    (output / "features.jsonl").write_text(json.dumps(row) + "\n")
    while not stopping:
        time.sleep(0.02)
    (output / "process.log").write_text(
        "Overflows : 0\nTimeouts : 0\nDiscontinuidades : 0\nGuardados JSONL : 1\n"
    )
    (output / "csi.cf32").write_bytes(b"\0" * 52 * 8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
