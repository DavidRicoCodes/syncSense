#!/usr/bin/env python3
"""Standalone SSH worker for explicitly authorized real child processes (Python 3.10+)."""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import selectors
import signal
import socket
import subprocess
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
    return int(Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[19])


def same_process(pid: int, ticks: int) -> bool:
    try:
        return start_ticks(pid) == ticks
    except (OSError, ValueError, IndexError):
        return False


def decode_spec(encoded: str) -> dict:
    padding = "=" * (-len(encoded) % 4)
    value = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    required = {"run_id", "producer_id", "node_id", "output_dir", "argv", "cwd", "artifacts"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("invalid worker specification")
    if value.get("safety_class") not in {"dsp", "rf"}:
        raise ValueError("real worker only accepts dsp or rf safety classes")
    if value.get("stop_signal", "terminate") not in {"terminate", "interrupt"}:
        raise ValueError("invalid stop signal")
    return value


def run_worker(encoded: str) -> int:
    spec = decode_spec(encoded)
    output = Path(spec["output_dir"])
    runtime = output / "runtime"
    output.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(exist_ok=True)
    pid = os.getpid()
    identity = {"pid": pid, "proc_start_ticks": start_ticks(pid), "host": socket.gethostname()}
    atomic_json(runtime / "process.json", identity)
    log_path = output / "process.log"
    stopping = False
    child: subprocess.Popen[str] | None = None

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            try:
                child_signal = signal.SIGINT if spec.get("stop_signal") == "interrupt" else signal.SIGTERM
                os.killpg(child.pid, child_signal)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in spec.get("env", {}).items()})
    started_at = utc_now()
    ready_regex = spec.get("readiness_regex")
    ready = False
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        child = subprocess.Popen(
            spec["argv"], cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True,
        )
        atomic_json(runtime / "child.json", {
            "pid": child.pid, "proc_start_ticks": start_ticks(child.pid), "started_at": utc_now()
        })
        assert child.stdout
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        heartbeat_at = 0.0
        while child.poll() is None:
            for key, _ in selector.select(timeout=0.1):
                line = key.fileobj.readline()
                if not line:
                    continue
                log.write(line)
                if not ready and (ready_regex is None or re.search(ready_regex, line)):
                    ready = True
                    atomic_json(runtime / "status.json", {"status": "ready", "at": utc_now(), **identity})
            now = time.monotonic()
            if now - heartbeat_at >= 0.5:
                atomic_json(runtime / "heartbeat.json", {"status": "running", "at": utc_now(), **identity})
                heartbeat_at = now
        for line in child.stdout:
            log.write(line)
        code = child.wait()
    artifacts = []
    for relative in spec["artifacts"]:
        path = output / relative
        if path.is_file():
            artifacts.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    receipt = {
        "schema_version": "1.0.0", "run_id": spec["run_id"], "producer_id": spec["producer_id"],
        "node_id": spec["node_id"], "simulation": False, "synthetic": False,
        "exit_code": code, "started_at": started_at, "finished_at": utc_now(),
        "process": identity, "artifacts": artifacts,
    }
    atomic_json(output / "producer-result.json", receipt)
    atomic_json(runtime / "status.json", {"status": "stopped", "exit_code": code, "at": utc_now(), **identity})
    return code


def control(action: str, runtime_dir: str, pid: int, ticks: int) -> int:
    child_identity = None
    try:
        child_identity = json.loads((Path(runtime_dir) / "child.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    worker_running = same_process(pid, ticks)
    if not worker_running and action == "kill" and child_identity and same_process(child_identity["pid"], child_identity["proc_start_ticks"]):
        try:
            os.killpg(child_identity["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not worker_running:
        print(json.dumps({"running": False, "identity_match": False}))
        return 0 if action == "probe" else 3
    if action == "probe":
        print(json.dumps({"running": True, "identity_match": True}))
        return 0
    if action == "kill" and child_identity and same_process(child_identity["pid"], child_identity["proc_start_ticks"]):
        try:
            os.killpg(child_identity["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    os.kill(pid, signal.SIGTERM if action == "term" else signal.SIGKILL)
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
    return run_worker(args.spec) if args.command == "run" else control(args.action, args.runtime_dir, args.pid, args.start_ticks)


if __name__ == "__main__":
    sys.exit(main())
