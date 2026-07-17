"""Preflight, foreground supervision, stop, status and recovery."""

from __future__ import annotations

import json
import hashlib
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_inventory, load_profile, resolve_parameters
from .deployment import verify_remote_workspaces
from .domain import CapabilityDisabled, ExecutionPlan, ProcessFailure, SyncError, ValidationFailure
from .planning import build_plan
from .processes.base import ProcessHandle, ProcessSpec
from .processes.local import same_process
from .processes.router import ProcessRouter
from .publication import publish_session, verify_published_manifest
from .run_id import generate_run_id
from .state import StateStore, utc_now
from .storage import atomic_write_json, create_run_layout, run_directory
from .wifi_smoke import available_memory_bytes, global_timeout_s, required_available_memory_bytes
from .processes.ssh import run_ssh


def _process_records(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        producer_id: {
            "producer_id": producer_id, "node_id": resolved.definition.node_id, "role": resolved.definition.role,
            "status": "planned", "handle": None, "started_at": None, "ready_at": None,
            "stopped_at": None, "exit_code": None, "termination_reason": None,
        }
        for producer_id, resolved in plan.processes.items()
    }


def make_process_spec(plan: ExecutionPlan, producer_id: str) -> ProcessSpec:
    resolved = plan.processes[producer_id]
    artifact_ids = {
        "features": next((a.artifact_id for a in resolved.definition.expected_artifacts if a.artifact_type.startswith("synthetic_feature")), ""),
    }
    worker_config = {
        "run_id": plan.run_id, "producer_id": producer_id, "node_id": resolved.definition.node_id,
        "role": resolved.definition.role, "modality": resolved.definition.modality,
        "output_dir": str(resolved.execution_producer_dir),
        "clock_domain_id": resolved.definition.clock_domain_id or "",
        "artifact_ids": artifact_ids,
    }
    if resolved.command.safety_class != "simulation":
        worker_config.update({
            "worker_path": str(resolved.node.workspace / "tools" / "remote_process_worker.py"),
            "argv": list(resolved.argv), "cwd": str(resolved.cwd), "env": resolved.env,
            "safety_class": resolved.command.safety_class,
            "stop_signal": resolved.definition.stop_signal,
            "artifacts": [a.path for a in resolved.definition.expected_artifacts if a.artifact_type != "producer_result"],
        })
        if resolved.definition.readiness["type"] == "stdout_regex":
            worker_config["readiness_regex"] = resolved.definition.readiness["pattern"]
    return ProcessSpec(
        producer_id=producer_id, argv=resolved.argv, cwd=resolved.cwd, env=resolved.env,
        log_path=(plan.run_dir or Path(".")) / ".control" / "logs" / f"{producer_id}.log",
        safety_class=resolved.command.safety_class,
        transport=resolved.node.transport, ssh=resolved.node.ssh,
        worker_config=worker_config if resolved.node.transport == "ssh" else None,
        remote_runtime_dir=resolved.execution_producer_dir / "runtime" if resolved.node.transport == "ssh" else None,
        shared_runtime_dir=resolved.producer_dir / "runtime" if resolved.node.transport == "ssh" else None,
    )


def _allowed_safety(*, allow_remote_simulation: bool, allow_hardware_receive: bool, allow_rf_transmit: bool) -> set[str]:
    allowed: set[str] = {"simulation"}
    if allow_hardware_receive:
        allowed.add("dsp")
    if allow_rf_transmit:
        allowed.add("rf")
    return allowed


def _prepare_wifi_config(plan: ExecutionPlan, repo_root: Path) -> None:
    if plan.profile.experiment_type != "wifi_link_smoke":
        return
    source = repo_root / "modulos_rx_tx" / "configs" / "pipelines" / "wifi_beacon_online.json"
    try:
        config = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessFailure(f"Cannot load pinned WiFi RX config: {source}") from exc
    target = plan.processes["rx_wifi"].producer_dir / "runtime" / "effective-config.json"
    execution = plan.processes["rx_wifi"].execution_producer_dir
    config["waveform_config"]["detector"]["metric_threshold"] = 0.85
    config["output"]["feature_path"] = str(execution / "features.jsonl")
    config["output"]["csi_raw_path"] = str(execution / "csi.cf32")
    atomic_write_json(target, config, mode=0o600)


def _hardware_preflight(plan: ExecutionPlan) -> None:
    if plan.profile.experiment_type == "ssb_rx_smoke":
        _ssb_hardware_preflight(plan)
        return
    if plan.profile.experiment_type != "wifi_link_smoke":
        return
    tx = plan.processes["tx_wifi"]
    rx = plan.processes["rx_wifi"]
    if tx.node.transport != "ssh" or rx.node.transport != "ssh":
        return
    assert tx.node.ssh and rx.node.ssh
    run_ssh(tx.node.ssh, ["python3", "-c", "import numpy, uhd"], timeout=20)
    meminfo = run_ssh(tx.node.ssh, ["cat", "/proc/meminfo"], timeout=10).stdout
    required = required_available_memory_bytes(int(plan.parameters["num_beacons"]))
    available = available_memory_bytes(meminfo)
    if available < required:
        raise ProcessFailure(f"PC2 has insufficient available memory: {available} < {required}")
    rx_binary = next((value for value in rx.argv if "online_waveform_pipeline" in value), None)
    if not rx_binary:
        raise ProcessFailure("RX command does not identify online_waveform_pipeline")
    run_ssh(rx.node.ssh, ["test", "-x", rx_binary], timeout=10)
    config_path = rx.producer_dir / "runtime" / "effective-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for resolved, serial in ((tx, next(tx.argv[i + 1] for i, value in enumerate(tx.argv[:-1]) if value == "--serial")), (rx, config["input"]["device_args"].split("serial=", 1)[1])):
        assert resolved.node.ssh
        found = run_ssh(resolved.node.ssh, ["uhd_find_devices", "--args", f"serial={serial}"], timeout=30)
        if serial not in found.stdout:
            raise ProcessFailure(f"Configured USRP was not discovered on {resolved.node.node_id}")


def _ssb_hardware_preflight(plan: ExecutionPlan) -> None:
    rx = plan.processes["rx_5g"]
    if rx.node.transport != "ssh":
        return
    assert rx.node.ssh
    interpreter = rx.argv[0]
    script = next((value for value in rx.argv if value.endswith("/online_5g_rxgrid_jsonl.py")), None)
    if script is None:
        raise ProcessFailure("5G RX command does not identify online_5g_rxgrid_jsonl.py")
    run_ssh(rx.node.ssh, ["test", "-x", interpreter], timeout=10)
    run_ssh(rx.node.ssh, [interpreter, script, "--help"], timeout=30)
    version_probe = (
        "import json,numpy,scipy,h5py,matplotlib,uhd;"
        "print(json.dumps({'python':__import__('sys').version.split()[0],"
        "'numpy':numpy.__version__,'scipy':scipy.__version__,'h5py':h5py.__version__,"
        "'matplotlib':matplotlib.__version__,'uhd':getattr(uhd,'__version__','unknown')}))"
    )
    versions = run_ssh(rx.node.ssh, [interpreter, "-c", version_probe], timeout=30)
    try:
        environment = json.loads(versions.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ProcessFailure("Cannot parse 5G RX dependency versions") from exc
    environment.update(
        {
            "script_sha256": hashlib.sha256(
                (Path(__file__).resolve().parents[2] / "modulos_rx_tx" / "src" / "python" / "ssb_python" / "online_5g_rxgrid_jsonl.py").read_bytes()
            ).hexdigest(),
            "warnings": versions.stderr.splitlines(),
        }
    )
    atomic_write_json(plan.run_dir / ".control" / "ssb-environment.json", environment, mode=0o600)
    try:
        serial = next(rx.argv[index + 1] for index, value in enumerate(rx.argv[:-1]) if value == "--serial")
    except StopIteration as exc:
        raise ProcessFailure("5G RX serial is not configured") from exc
    found = run_ssh(rx.node.ssh, ["uhd_find_devices", "--args", f"serial={serial}"], timeout=30)
    if serial not in found.stdout:
        raise ProcessFailure("Configured 5G RX USRP was not discovered on pc3pc4")


def preflight(inventory_path: str | Path, profile_path: str | Path, supplied_parameters: dict[str, str], *, storage_override: str | Path | None = None, dry_run: bool = False, allow_remote_simulation: bool = False, allow_hardware_receive: bool = False, allow_rf_transmit: bool = False, repo_root: Path | None = None) -> tuple[ExecutionPlan, StateStore | None]:
    inventory = load_inventory(inventory_path, storage_override=storage_override)
    profile = load_profile(profile_path)
    parameters = resolve_parameters(profile, supplied_parameters)
    if dry_run:
        return build_plan(inventory, profile, parameters, enforce_capabilities=False), None
    allowed = _allowed_safety(
        allow_remote_simulation=allow_remote_simulation,
        allow_hardware_receive=allow_hardware_receive,
        allow_rf_transmit=allow_rf_transmit,
    )
    for resolved in build_plan(inventory, profile, parameters, enforce_capabilities=False).processes.values():
        if resolved.node.transport != "ssh":
            continue
        if resolved.command.safety_class == "simulation" and not allow_remote_simulation:
            raise CapabilityDisabled("Remote simulation requires --allow-remote-simulation")
        if resolved.command.safety_class == "dsp" and not allow_hardware_receive:
            raise CapabilityDisabled("Hardware reception requires --allow-hardware-receive")
        if resolved.command.safety_class == "rf" and not allow_rf_transmit:
            raise CapabilityDisabled("RF transmission requires --allow-rf-transmit")
    # Capability and command resolution checks must happen before creating a run.
    preview = build_plan(inventory, profile, parameters, enforce_capabilities=True, allowed_safety_classes=allowed)
    if any(p.node.transport == "ssh" for p in preview.processes.values()):
        verify_remote_workspaces(preview, repo_root=repo_root or Path(__file__).resolve().parents[2])
    run_id = generate_run_id()
    run_dir = create_run_layout(inventory.storage_root, run_id, list(profile.processes))
    plan = build_plan(inventory, profile, parameters, run_id=run_id, run_dir=run_dir, enforce_capabilities=True, allowed_safety_classes=allowed)
    store = StateStore(run_dir)
    store.create(
        run_id=run_id,
        profile={"profile_id": profile.profile_id, "profile_version": profile.profile_version, "digest": profile.digest, "source_path": str(profile.source_path), "parameters": parameters},
        inventory={"inventory_id": inventory.inventory_id, "digest": inventory.digest},
        inventory_path=str(inventory.source_path),
        processes=_process_records(plan),
    )
    atomic_write_json(run_dir / ".control" / "plan.json", plan.sanitized, mode=0o600)
    store.transition("PREFLIGHT", reason="preflight_started")
    adapter = ProcessRouter(allow_remote_simulation=allow_remote_simulation, allow_hardware_receive=allow_hardware_receive, allow_rf_transmit=allow_rf_transmit)
    try:
        _prepare_wifi_config(plan, repo_root or Path(__file__).resolve().parents[2])
        _hardware_preflight(plan)
        for producer_id in plan.processes:
            spec = make_process_spec(plan, producer_id)
            adapter.for_spec(spec).preflight(spec)
        # Creating the run layout already proves local write access. Also force a disk query.
        os.statvfs(run_dir)
        store.transition("ARMED", reason="preflight_passed")
    except Exception as exc:
        store.transition("FAILED", reason="preflight_failed", error={"code": getattr(exc, "code", "PREFLIGHT_FAILED"), "message": str(exc), "at": utc_now()})
        raise
    return plan, store


def load_plan_for_run(inventory_path: str | Path, run_id: str, *, storage_override: str | Path | None = None, enforce_capabilities: bool = True, allowed_safety_classes: set[str] | None = None) -> tuple[ExecutionPlan, StateStore]:
    inventory = load_inventory(inventory_path, storage_override=storage_override)
    run_dir = run_directory(inventory.storage_root, run_id)
    store = StateStore(run_dir)
    state = store.load()
    if state["inventory"]["digest"] != inventory.digest:
        raise ValidationFailure("Inventory changed after preflight; run cannot be resumed")
    profile = load_profile(state["profile"]["source_path"])
    if profile.digest != state["profile"]["digest"]:
        raise ValidationFailure("Profile changed after preflight; run cannot be resumed")
    plan = build_plan(inventory, profile, state["profile"]["parameters"], run_id=run_id, run_dir=run_dir, enforce_capabilities=enforce_capabilities, allowed_safety_classes=allowed_safety_classes)
    return plan, store


def _set_process(store: StateStore, producer_id: str, **updates: Any) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> None:
        state["processes"][producer_id].update(updates)
    return store.update(mutate)


def _read_json_pointer(value: Any, pointer: str) -> Any:
    current = value
    for raw in pointer.lstrip("/").split("/") if pointer != "/" else []:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current[token]
        elif isinstance(current, list):
            current = current[int(token)]
        else:
            raise KeyError(token)
    return current


def _is_ready(plan: ExecutionPlan, producer_id: str, adapter: Any, handle: ProcessHandle, started_monotonic: float) -> bool:
    status = adapter.probe(handle)
    if not status.running:
        raise ProcessFailure(f"Process exited before readiness: {producer_id} ({status.exit_code})")
    readiness = plan.processes[producer_id].definition.readiness
    if readiness["type"] == "process_running":
        return time.monotonic() - started_monotonic >= float(readiness["grace_s"])
    if readiness["type"] == "stdout_regex":
        path = plan.processes[producer_id].producer_dir / "runtime" / "status.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("status") == "ready"
        except (OSError, json.JSONDecodeError):
            return False
    path = plan.processes[producer_id].producer_dir / readiness["path"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return _read_json_pointer(value, readiness["json_pointer"]) == readiness["equals"]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, ValueError):
        return False


def _stop_all(plan: ExecutionPlan, store: StateStore, router: ProcessRouter, handles: dict[str, ProcessHandle], *, reason: str) -> None:
    for group in plan.profile.stop_groups:
        for producer_id in group:
            if store.load()["processes"][producer_id].get("status") == "stopped":
                continue
            handle = handles.get(producer_id)
            if handle is None:
                record = store.load()["processes"][producer_id]
                if record.get("handle"):
                    handle = ProcessHandle.from_dict(record["handle"])
                else:
                    continue
            _set_process(store, producer_id, status="stopping")
            adapter = router.for_handle(handle)
            grace = plan.processes[producer_id].definition.timeouts["stop_grace_s"]
            status = adapter.stop(handle, grace)
            termination = reason
            if status.running:
                termination = f"{reason}_sigkill"
                status = adapter.kill(handle)
            if status.running:
                raise ProcessFailure(f"Could not stop process: {producer_id}")
            exit_code = status.exit_code
            if exit_code is None and not same_process(handle):
                # A non-child recovered by another CLI cannot expose wait status.
                exit_code = -1
            _set_process(store, producer_id, status="stopped", stopped_at=utc_now(), exit_code=exit_code, termination_reason=termination)


def _supervisor_identity() -> dict[str, Any]:
    from .processes.local import process_start_ticks
    return {"pid": os.getpid(), "proc_start_ticks": process_start_ticks(os.getpid()), "heartbeat_at": utc_now()}


def start_run(plan: ExecutionPlan, store: StateStore, *, dry_run: bool = False, allow_remote_simulation: bool = False, allow_hardware_receive: bool = False, allow_rf_transmit: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"action": "start", "run_id": plan.run_id, "start_groups": [list(g) for g in plan.profile.start_groups], "stop_groups": [list(g) for g in plan.profile.stop_groups], "mutating": False}
    if store.load()["state"] != "ARMED":
        raise ProcessFailure("Run must be ARMED before start")
    router = ProcessRouter(allow_remote_simulation=allow_remote_simulation, allow_hardware_receive=allow_hardware_receive, allow_rf_transmit=allow_rf_transmit)
    handles: dict[str, ProcessHandle] = {}
    received_signal: list[int] = []
    previous_handlers: dict[int, Any] = {}
    claimed_supervisor = False

    def signal_handler(signum, _frame):
        if not received_signal:
            received_signal.append(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, signal_handler)
    try:
        identity = _supervisor_identity()

        def claim(state: dict[str, Any]) -> None:
            if state["state"] != "ARMED" or state.get("supervisor") is not None:
                raise ProcessFailure("Run is no longer available for a supervisor")
            state.update(supervisor=identity, stop_request=None)

        store.update(claim)
        claimed_supervisor = True
        for group in plan.profile.start_groups:
            starts: dict[str, float] = {}
            deadlines: dict[str, float] = {}
            for producer_id in group:
                if received_signal:
                    raise KeyboardInterrupt
                spec = make_process_spec(plan, producer_id)
                adapter = router.for_spec(spec)
                _set_process(store, producer_id, status="starting")
                handle = adapter.start(spec)
                handles[producer_id] = handle
                now = time.monotonic()
                starts[producer_id] = now
                deadlines[producer_id] = now + plan.processes[producer_id].definition.timeouts["readiness_s"]
                _set_process(store, producer_id, status="running", handle=handle.to_dict(), started_at=utc_now())
            pending = set(group)
            while pending:
                if received_signal:
                    raise KeyboardInterrupt
                for producer_id in list(pending):
                    current_adapter = router.for_handle(handles[producer_id])
                    if _is_ready(plan, producer_id, current_adapter, handles[producer_id], starts[producer_id]):
                        _set_process(store, producer_id, status="ready", ready_at=utc_now())
                        pending.remove(producer_id)
                    elif time.monotonic() >= deadlines[producer_id]:
                        raise ProcessFailure(f"Readiness timeout: {producer_id}")
                time.sleep(min(plan.profile.orchestration["monitor_interval_s"], 0.05))
        store.transition("RUNNING", reason="all_processes_ready")
        started = time.monotonic()
        last_heartbeat = 0.0
        duration = float(plan.parameters.get("duration_s", 0))
        completion = plan.profile.orchestration.get("completion")
        overall_deadline = started + (global_timeout_s(int(plan.parameters["num_beacons"])) if completion else duration)
        drain_started: float | None = None
        last_growth = started
        last_size = -1
        while True:
            now = time.monotonic()
            state = store.load()
            if received_signal:
                _stop_all(plan, store, router, handles, reason="signal")
                store.transition("ABORTED", reason=f"signal_{received_signal[0]}")
                return store.load()
            if state.get("stop_request"):
                reason = "operator_stop"
                _stop_all(plan, store, router, handles, reason=reason)
                store.transition("FINALIZING", reason=reason)
                return store.load()
            if completion and now >= overall_deadline:
                raise ProcessFailure("Global experiment timeout elapsed")
            if completion:
                finite_id = completion["finite_producer_id"]
                finite_handle = handles[finite_id]
                finite_status = router.for_handle(finite_handle).probe(finite_handle)
                if not finite_status.running and drain_started is None:
                    collected = router.for_handle(finite_handle).collect(finite_handle)
                    if collected.exit_code != 0:
                        raise ProcessFailure(f"Finite producer failed: {finite_id} ({collected.exit_code})")
                    _set_process(store, finite_id, status="stopped", stopped_at=utc_now(), exit_code=0, termination_reason="completed")
                    drain_started = now
                    last_growth = now
                if drain_started is not None:
                    quiet_path = plan.run_dir / completion["quiet_artifact"]
                    size = quiet_path.stat().st_size if quiet_path.exists() else 0
                    if size != last_size:
                        last_size = size
                        last_growth = now
                    quiet_s = float(plan.parameters[completion["quiet_s_parameter"]])
                    max_drain_s = float(plan.parameters[completion["max_drain_s_parameter"]])
                    if now - last_growth >= quiet_s or now - drain_started >= max_drain_s:
                        _stop_all(plan, store, router, handles, reason="finite_tx_drain_complete")
                        store.transition("FINALIZING", reason="finite_tx_drain_complete")
                        return store.load()
            elif now - started >= duration:
                _stop_all(plan, store, router, handles, reason="duration_elapsed")
                store.transition("FINALIZING", reason="duration_elapsed")
                return store.load()
            for producer_id, handle in handles.items():
                if store.load()["processes"][producer_id]["status"] == "stopped":
                    continue
                status = router.for_handle(handle).probe(handle)
                if not status.running:
                    if not completion or producer_id != completion["finite_producer_id"]:
                        raise ProcessFailure(f"Process exited unexpectedly: {producer_id} ({status.exit_code})")
            if now - last_heartbeat >= plan.profile.orchestration["heartbeat_interval_s"]:
                heartbeat = utc_now()
                store.update(lambda s: s["supervisor"].update(heartbeat_at=heartbeat) if s["supervisor"] else None)
                last_heartbeat = now
            time.sleep(plan.profile.orchestration["monitor_interval_s"])
    except KeyboardInterrupt:
        if handles:
            _stop_all(plan, store, router, handles, reason="signal")
        state = store.load()
        if state["state"] in {"ARMED", "RUNNING"}:
            store.transition("ABORTED", reason="signal_interrupt")
        return store.load()
    except Exception as exc:
        try:
            if handles:
                _stop_all(plan, store, router, handles, reason="failure")
        finally:
            state = store.load()
            if state["state"] in {"ARMED", "RUNNING"}:
                store.transition("FAILED", reason="process_failure", error={"code": getattr(exc, "code", "PROCESS_FAILED"), "message": str(exc), "at": utc_now()})
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if claimed_supervisor and store.load()["state"] not in {"RUNNING"}:
            store.update(lambda state: state.update(supervisor=None))


def status_run(plan: ExecutionPlan, store: StateStore) -> dict[str, Any]:
    state = store.load()
    router = ProcessRouter(allow_remote_simulation=True)
    health = {}
    for producer_id, record in state["processes"].items():
        if record.get("handle") and record["status"] not in {"stopped", "failed"}:
            handle = ProcessHandle.from_dict(record["handle"])
            health[producer_id] = router.for_handle(handle).probe(handle).__dict__
        else:
            health[producer_id] = {"running": False, "exit_code": record.get("exit_code"), "detail": record["status"]}
    return {"run_id": plan.run_id, "state": state["state"], "revision": state["revision"], "process_health": health, "stop_request": state.get("stop_request"), "last_error": state.get("last_error")}


def _supervisor_is_fresh(state: dict[str, Any], stale_s: float) -> bool:
    supervisor = state.get("supervisor")
    if not supervisor:
        return False
    handle = ProcessHandle("local", "supervisor", supervisor["pid"], supervisor["proc_start_ticks"])
    if not same_process(handle):
        return False
    try:
        heartbeat = datetime.fromisoformat(supervisor["heartbeat_at"])
    except (ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - heartbeat).total_seconds() <= stale_s


def stop_run(plan: ExecutionPlan, store: StateStore, *, reason: str = "operator", wait_s: float = 10.0, dry_run: bool = False, allow_remote_simulation: bool = False, allow_hardware_receive: bool = False, allow_rf_transmit: bool = False) -> dict[str, Any]:
    state = store.load()
    if dry_run:
        return {"action": "stop", "run_id": plan.run_id, "state": state["state"], "stop_groups": [list(g) for g in plan.profile.stop_groups], "mutating": False}
    if state["state"] == "ARMED":
        return store.transition("ABORTED", reason=f"stop_before_start:{reason}")
    if state["state"] != "RUNNING":
        raise ProcessFailure(f"Run must be RUNNING or ARMED before stop, got {state['state']}")
    request = {"requested_at": utc_now(), "reason": reason}
    store.update(lambda current: current.update(stop_request=request))
    if _supervisor_is_fresh(store.load(), plan.profile.orchestration["supervisor_stale_s"]):
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            current = store.load()
            if current["state"] != "RUNNING":
                return current
            time.sleep(0.1)
        return store.load()
    router = ProcessRouter(allow_remote_simulation=allow_remote_simulation, allow_hardware_receive=allow_hardware_receive, allow_rf_transmit=allow_rf_transmit)
    handles = {pid: ProcessHandle.from_dict(record["handle"]) for pid, record in state["processes"].items() if record.get("handle")}
    _stop_all(plan, store, router, handles, reason="stale_supervisor_stop")
    return store.transition("FINALIZING", reason="stale_supervisor_stopped")


def finalize_run(plan: ExecutionPlan, store: StateStore, *, repo_root: Path, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        state = store.load()
        return {"action": "finalize", "run_id": plan.run_id, "state": state["state"], "expected_producers": list(plan.processes), "mutating": False}
    return publish_session(plan, store, repo_root=repo_root)


def recover_run(plan: ExecutionPlan, store: StateStore, *, repo_root: Path, dry_run: bool = False, allow_remote_simulation: bool = False, allow_hardware_receive: bool = False, allow_rf_transmit: bool = False) -> dict[str, Any]:
    state = store.load()
    action = "none"
    if (store.run_dir / "manifest.json").exists():
        verify_published_manifest(store.run_dir)
        action = "reconcile_complete_manifest"
    elif state["state"] == "FINALIZING":
        action = "resume_finalization"
    elif state["state"] in {"RUNNING", "ARMED"}:
        action = "abort_incomplete_run"
    elif state["state"] in {"CREATED", "PREFLIGHT"}:
        action = "fail_incomplete_preflight"
    if dry_run:
        return {"action": "recover", "run_id": plan.run_id, "state": state["state"], "recovery_action": action, "mutating": False}
    store.update(lambda current: current.update(recovery_count=current["recovery_count"] + 1))
    if action == "reconcile_complete_manifest" and state["state"] == "FINALIZING":
        store.transition("COMPLETE", reason="recovered_complete_manifest")
    elif action == "resume_finalization":
        publish_session(plan, store, repo_root=repo_root)
    elif action == "abort_incomplete_run":
        if state["state"] == "RUNNING":
            router = ProcessRouter(allow_remote_simulation=allow_remote_simulation, allow_hardware_receive=allow_hardware_receive, allow_rf_transmit=allow_rf_transmit)
            handles = {pid: ProcessHandle.from_dict(record["handle"]) for pid, record in state["processes"].items() if record.get("handle")}
            if handles:
                _stop_all(plan, store, router, handles, reason="recovery")
        store.transition("ABORTED", reason="recovered_incomplete_run")
    elif action == "fail_incomplete_preflight":
        if state["state"] == "CREATED":
            store.transition("PREFLIGHT", reason="recovery_entered_preflight")
        store.transition("FAILED", reason="recovered_incomplete_preflight", error={"code": "RECOVERY_FAILED_PREFLIGHT", "message": "Preflight was interrupted", "at": utc_now()})
    return store.load()
