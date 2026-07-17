#!/usr/bin/env python3
"""Run a spaced WiFi threshold campaign through syncctl.

The default cadence reproduces the previous campaign:
3 cycles * 4 active hours * 10 runs/hour = 120 runs, with 590 beacons/run.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_ROOT = Path("/srv/sync-experiments")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(timestamp: float | None = None) -> str:
    value = datetime.fromtimestamp(timestamp or time.time(), timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def threshold_tag(value: float) -> str:
    return f"{round(value * 100):03d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--active-hours", type=int, default=4)
    parser.add_argument("--runs-per-hour", type=int, default=10)
    parser.add_argument("--num-beacons", type=int, default=590)
    parser.add_argument("--cycle-gap-minutes", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--inventory", type=Path, default=REPO_ROOT / "config" / "inventory.local.yaml")
    parser.add_argument("--profile", type=Path, default=REPO_ROOT / "profiles" / "wifi_link_smoke.yaml")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--campaign-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0 and 1")
    if args.cycles < 1 or args.active_hours < 1 or args.runs_per_hour < 1:
        parser.error("cycles, active-hours and runs-per-hour must be positive")
    if not 1 <= args.num_beacons <= 600:
        parser.error("--num-beacons must be between 1 and 600")
    return args


def build_schedule(args: argparse.Namespace, campaign_id: str, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    start = time.time()
    slot_width = 3600.0 / args.runs_per_hour
    # Keep at least 150 seconds between the latest jittered start and the next slot.
    jitter_limit = min(120.0, max(0.0, slot_width - 150.0))
    cycle_span = args.active_hours * 3600.0 + args.cycle_gap_minutes * 60.0
    schedule: list[dict[str, object]] = []
    for cycle in range(1, args.cycles + 1):
        for hour in range(1, args.active_hours + 1):
            for slot in range(1, args.runs_per_hour + 1):
                target = (
                    start
                    + (cycle - 1) * cycle_span
                    + (hour - 1) * 3600.0
                    + (slot - 1) * slot_width
                    + rng.uniform(0.0, jitter_limit)
                )
                schedule.append(
                    {
                        "cycle": cycle,
                        "active_hour": hour,
                        "slot": slot,
                        "scheduled_timestamp": target,
                        "scheduled_utc": utc_iso(target),
                        "label_prefix": (
                            f"{campaign_id}_c{cycle:03d}_h{hour:02d}_s{slot:02d}"
                        ),
                    }
                )
    return schedule


def append_result(path: Path, row: dict[str, object]) -> None:
    fields = [
        "cycle",
        "active_hour",
        "slot",
        "threshold",
        "label",
        "scheduled_utc",
        "started_utc",
        "finished_utc",
        "duration_s",
        "exit_code",
        "log",
    ]
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t")
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        output.flush()
        os.fsync(output.fileno())


def main() -> int:
    args = parse_args()
    seed = args.seed if args.seed is not None else int(time.time_ns() & 0xFFFFFFFF)
    campaign_id = args.campaign_id or (
        f"wifi{args.num_beacons}_thr{threshold_tag(args.threshold)}_{utc_stamp()}"
    )
    campaign_dir = args.storage_root / "campaigns" / campaign_id
    schedule = build_schedule(args, campaign_id, seed)

    plan = {
        "schema": "wifi_threshold_campaign_v1",
        "campaign_id": campaign_id,
        "created_utc": utc_iso(),
        "threshold": args.threshold,
        "cycles": args.cycles,
        "active_hours": args.active_hours,
        "runs_per_hour": args.runs_per_hour,
        "total_runs": len(schedule),
        "num_beacons": args.num_beacons,
        "cycle_gap_minutes": args.cycle_gap_minutes,
        "seed": seed,
        "inventory": str(args.inventory),
        "profile": str(args.profile),
        "schedule": schedule,
    }

    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    campaign_dir.mkdir(parents=True, exist_ok=False)
    (campaign_dir / "campaign-plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lock_path = args.storage_root / "campaigns" / ".wifi_hardware_campaign.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another WiFi hardware campaign holds the global lock")
        lock.write(f"{campaign_id} pid={os.getpid()}\n")
        lock.flush()

        results_path = campaign_dir / "results.tsv"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT / "src")

        for item in schedule:
            delay = float(item["scheduled_timestamp"]) - time.time()
            if delay > 0:
                time.sleep(delay)

            started = time.time()
            label = f"{item['label_prefix']}_{utc_stamp()}"
            log_path = campaign_dir / f"{label}.log"
            command = [
                sys.executable,
                "-m",
                "sync_framework.cli",
                "--inventory",
                str(args.inventory),
                "--format",
                "json",
                "experiment",
                "run",
                str(args.profile),
                "--param",
                f"label={label}",
                "--param",
                f"num_beacons={args.num_beacons}",
                "--param",
                f"detector_threshold={args.threshold}",
                "--inference",
                "dummy",
                "--allow-hardware-receive",
                "--allow-rf-transmit",
            ]

            with log_path.open("w", encoding="utf-8") as log:
                log.write("COMMAND: " + " ".join(command) + "\n")
                log.flush()
                process = subprocess.Popen(
                    command,
                    cwd=REPO_ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                try:
                    exit_code = process.wait()
                except KeyboardInterrupt:
                    process.send_signal(signal.SIGINT)
                    exit_code = process.wait(timeout=30)
                    raise

            finished = time.time()
            append_result(
                results_path,
                {
                    "cycle": item["cycle"],
                    "active_hour": item["active_hour"],
                    "slot": item["slot"],
                    "threshold": args.threshold,
                    "label": label,
                    "scheduled_utc": item["scheduled_utc"],
                    "started_utc": utc_iso(started),
                    "finished_utc": utc_iso(finished),
                    "duration_s": round(finished - started, 3),
                    "exit_code": exit_code,
                    "log": str(log_path),
                },
            )

        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "summarize_wifi_threshold_campaign.py"),
                str(campaign_dir),
                "--storage-root",
                str(args.storage_root),
            ],
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
