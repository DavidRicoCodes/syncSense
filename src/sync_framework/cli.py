"""Command line interface for the safe SYNC orchestration core."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import load_inventory, load_profile, resolve_parameters
from .domain import CapabilityDisabled, SyncError, ValidationFailure
from .inference import inference_status, run_dummy_inference
from .nfs import bootstrap_nfs, describe_nfs, teardown_nfs, verify_nfs
from .orchestration import (
    finalize_run,
    load_plan_for_run,
    preflight,
    recover_run,
    start_run,
    status_run,
    stop_run,
)
from .planning import build_plan
from .storage import run_directory


def parse_params(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValidationFailure(f"Parameter must use KEY=VALUE: {value}")
        key, raw = value.split("=", 1)
        if not key or key in result:
            raise ValidationFailure(f"Invalid or duplicate parameter: {key}")
        result[key] = raw
    return result


def repo_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
    for candidate in candidates:
        for path in (candidate, *candidate.parents):
            if (path / "ProjectDescription.md").is_file() and (path / "modulos_rx_tx").is_dir():
                return path
    raise ValidationFailure("Cannot locate the SYNC repository root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syncctl", description="Safe SYNC experiment orchestrator")
    parser.add_argument("--inventory", default=os.environ.get("SYNC_INVENTORY", "config/inventory.local.yaml"))
    parser.add_argument("--storage-root", default=None)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    storage = commands.add_parser("storage")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    bootstrap = storage_commands.add_parser("bootstrap")
    bootstrap.add_argument("--dry-run", action="store_true")
    bootstrap.add_argument("--apply", action="store_true")
    storage_commands.add_parser("verify")
    teardown = storage_commands.add_parser("teardown")
    teardown.add_argument("--apply", action="store_true")

    experiment = commands.add_parser("experiment")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    plan = experiment_commands.add_parser("plan")
    plan.add_argument("profile")
    plan.add_argument("--param", action="append", default=[])
    run = experiment_commands.add_parser("run")
    run.add_argument("profile")
    run.add_argument("--param", action="append", default=[])
    run.add_argument("--inference", choices=["dummy"], required=True)
    run.add_argument("--allow-remote-simulation", action="store_true")

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("profile")
    preflight_parser.add_argument("--param", action="append", default=[])
    preflight_parser.add_argument("--dry-run", action="store_true")
    preflight_parser.add_argument("--allow-remote-simulation", action="store_true")

    start = commands.add_parser("start")
    start.add_argument("run_id")
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--allow-remote-simulation", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("run_id")

    stop = commands.add_parser("stop")
    stop.add_argument("run_id")
    stop.add_argument("--reason", default="operator")
    stop.add_argument("--wait", type=float, default=10.0)
    stop.add_argument("--dry-run", action="store_true")
    stop.add_argument("--allow-remote-simulation", action="store_true")

    finalize = commands.add_parser("finalize")
    finalize.add_argument("run_id")
    finalize.add_argument("--dry-run", action="store_true")

    recover = commands.add_parser("recover")
    recover.add_argument("run_id")
    recover.add_argument("--dry-run", action="store_true")
    recover.add_argument("--allow-remote-simulation", action="store_true")

    inference = commands.add_parser("inference")
    inference_commands = inference.add_subparsers(dest="inference_command", required=True)
    inference_run = inference_commands.add_parser("run")
    inference_run.add_argument("run_id")
    inference_run.add_argument("--adapter", choices=["dummy"], required=True)
    inference_status_parser = inference_commands.add_parser("status")
    inference_status_parser.add_argument("run_id")
    inference_status_parser.add_argument("inference_id", nargs="?")
    return parser


def emit(value: Any, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
        return
    if isinstance(value, dict):
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
    else:
        print(value)


def dispatch(args: argparse.Namespace) -> tuple[Any, int]:
    if args.command == "storage":
        inventory = load_inventory(args.inventory, storage_override=args.storage_root)
        if args.storage_command == "bootstrap":
            if args.dry_run and args.apply:
                raise ValidationFailure("Choose either --dry-run or --apply")
            if args.dry_run:
                return describe_nfs(inventory), 0
            if args.apply:
                return bootstrap_nfs(inventory), 0
            raise CapabilityDisabled("NFS bootstrap requires explicit --dry-run or --apply")
        if args.storage_command == "verify":
            return verify_nfs(inventory), 0
        if args.storage_command == "teardown":
            if not args.apply:
                raise CapabilityDisabled("NFS teardown requires explicit --apply")
            return teardown_nfs(inventory), 0
    if args.command == "experiment":
        if args.experiment_command == "run":
            plan, store = preflight(
                args.inventory, args.profile, parse_params(args.param), storage_override=args.storage_root,
                allow_remote_simulation=args.allow_remote_simulation, repo_root=repo_root(),
            )
            assert store is not None and plan.run_dir is not None
            start_run(plan, store, allow_remote_simulation=args.allow_remote_simulation)
            manifest = finalize_run(plan, store, repo_root=repo_root())
            result = run_dummy_inference(plan.run_dir)
            return {
                "run_id": plan.run_id, "dataset_state": manifest["state"],
                "inference_id": result["inference_id"], "inference_status": result["status"],
                "run_dir": str(plan.run_dir),
                "manifest_path": str(plan.run_dir / "manifest.json"),
                "inference_path": str(plan.run_dir / "inference" / result["inference_id"]),
            }, 0
        inventory = load_inventory(args.inventory, storage_override=args.storage_root)
        profile = load_profile(args.profile)
        parameters = resolve_parameters(profile, parse_params(args.param))
        plan = build_plan(inventory, profile, parameters, enforce_capabilities=False)
        return plan.sanitized, 0
    if args.command == "preflight":
        plan, store = preflight(
            args.inventory, args.profile, parse_params(args.param), storage_override=args.storage_root,
            dry_run=args.dry_run, allow_remote_simulation=args.allow_remote_simulation, repo_root=repo_root(),
        )
        return ({"dry_run": True, **plan.sanitized} if store is None else {"run_id": plan.run_id, "state": store.load()["state"], "run_dir": str(plan.run_dir)}), 0
    if args.command == "inference":
        inventory = load_inventory(args.inventory, storage_override=args.storage_root)
        target = run_directory(inventory.storage_root, args.run_id)
        if args.inference_command == "run":
            return run_dummy_inference(target), 0
        return inference_status(target, args.inference_id), 0
    plan, store = load_plan_for_run(args.inventory, args.run_id, storage_override=args.storage_root, enforce_capabilities=not getattr(args, "dry_run", False))
    if args.command == "start":
        result = start_run(plan, store, dry_run=args.dry_run, allow_remote_simulation=args.allow_remote_simulation)
        if not args.dry_run and result.get("state") == "ABORTED":
            reason = result["history"][-1]["reason"] if result.get("history") else ""
            if reason == "signal_15":
                return result, 143
            if reason.startswith("signal"):
                return result, 130
        return result, 0
    if args.command == "status":
        return status_run(plan, store), 0
    if args.command == "stop":
        return stop_run(plan, store, reason=args.reason, wait_s=args.wait, dry_run=args.dry_run, allow_remote_simulation=args.allow_remote_simulation), 0
    if args.command == "finalize":
        return finalize_run(plan, store, repo_root=repo_root(), dry_run=args.dry_run), 0
    if args.command == "recover":
        return recover_run(plan, store, repo_root=repo_root(), dry_run=args.dry_run, allow_remote_simulation=args.allow_remote_simulation), 0
    raise ValidationFailure(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result, code = dispatch(args)
        emit(result, args.format)
        return code
    except SyncError as exc:
        payload = {"error": {"code": exc.code, "message": str(exc), "details": exc.details}}
        output_format = getattr(locals().get("args", None), "format", "text")
        if output_format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
