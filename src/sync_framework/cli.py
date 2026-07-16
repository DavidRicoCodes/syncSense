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

    experiment = commands.add_parser("experiment")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    plan = experiment_commands.add_parser("plan")
    plan.add_argument("profile")
    plan.add_argument("--param", action="append", default=[])

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("profile")
    preflight_parser.add_argument("--param", action="append", default=[])
    preflight_parser.add_argument("--dry-run", action="store_true")

    start = commands.add_parser("start")
    start.add_argument("run_id")
    start.add_argument("--dry-run", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("run_id")

    stop = commands.add_parser("stop")
    stop.add_argument("run_id")
    stop.add_argument("--reason", default="operator")
    stop.add_argument("--wait", type=float, default=10.0)
    stop.add_argument("--dry-run", action="store_true")

    finalize = commands.add_parser("finalize")
    finalize.add_argument("run_id")
    finalize.add_argument("--dry-run", action="store_true")

    recover = commands.add_parser("recover")
    recover.add_argument("run_id")
    recover.add_argument("--dry-run", action="store_true")
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
        result = {
            "action": "storage_bootstrap", "inventory_id": inventory.inventory_id,
            "backend": inventory.storage_backend, "root": str(inventory.storage_root),
            "status": "not_implemented_in_safe_increment", "mutating": False,
        }
        if not args.dry_run:
            raise CapabilityDisabled("Real NFS/local storage bootstrap is outside this increment; use --dry-run")
        return result, 0
    if args.command == "experiment":
        inventory = load_inventory(args.inventory, storage_override=args.storage_root)
        profile = load_profile(args.profile)
        parameters = resolve_parameters(profile, parse_params(args.param))
        plan = build_plan(inventory, profile, parameters, enforce_capabilities=False)
        return plan.sanitized, 0
    if args.command == "preflight":
        plan, store = preflight(args.inventory, args.profile, parse_params(args.param), storage_override=args.storage_root, dry_run=args.dry_run)
        return ({"dry_run": True, **plan.sanitized} if store is None else {"run_id": plan.run_id, "state": store.load()["state"], "run_dir": str(plan.run_dir)}), 0
    plan, store = load_plan_for_run(args.inventory, args.run_id, storage_override=args.storage_root, enforce_capabilities=not getattr(args, "dry_run", False))
    if args.command == "start":
        result = start_run(plan, store, dry_run=args.dry_run)
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
        return stop_run(plan, store, reason=args.reason, wait_s=args.wait, dry_run=args.dry_run), 0
    if args.command == "finalize":
        return finalize_run(plan, store, repo_root=repo_root(), dry_run=args.dry_run), 0
    if args.command == "recover":
        return recover_run(plan, store, repo_root=repo_root(), dry_run=args.dry_run), 0
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

