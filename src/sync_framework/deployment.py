"""Read-only verification that remote clones match the deployed PC5 code."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .domain import ExecutionPlan, ProcessFailure
from .processes.ssh import run_ssh


def _local_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=False,
    )
    if result.returncode:
        raise ProcessFailure(f"Local Git verification failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_remote_workspaces(plan: ExecutionPlan, *, repo_root: Path) -> list[dict[str, str]]:
    expected_head = _local_git(repo_root, "rev-parse", "HEAD")
    if _local_git(repo_root, "status", "--porcelain", "--ignore-submodules=all"):
        raise ProcessFailure("PC5 parent worktree must be clean before a distributed run")
    checked: list[dict[str, str]] = []
    for node_id in sorted({p.definition.node_id for p in plan.processes.values()}):
        node = plan.inventory.nodes[node_id]
        if node.transport != "ssh":
            continue
        assert node.ssh
        workspace = str(node.workspace)
        status = run_ssh(node.ssh, ["git", "-C", workspace, "status", "--porcelain", "--ignore-submodules=all"]).stdout.strip()
        branch = run_ssh(node.ssh, ["git", "-C", workspace, "branch", "--show-current"]).stdout.strip()
        head = run_ssh(node.ssh, ["git", "-C", workspace, "rev-parse", "HEAD"]).stdout.strip()
        if status:
            raise ProcessFailure(f"Remote worktree is dirty: {node_id}")
        if branch != "main":
            raise ProcessFailure(f"Remote node is not on main: {node_id} ({branch})")
        if head != expected_head:
            raise ProcessFailure(f"Remote commit differs from PC5: {node_id} ({head})")
        node_processes = [p for p in plan.processes.values() if p.definition.node_id == node_id]
        workers = {
            str(node.workspace / "tools" / ("remote_dummy_worker.py" if p.command.safety_class == "simulation" else "remote_process_worker.py"))
            for p in node_processes
        }
        if len(workers) != 1:
            raise ProcessFailure(f"Node {node_id} must use one standalone worker")
        worker = next(iter(workers))
        local_worker = repo_root / "tools" / Path(worker).name
        remote_digest = run_ssh(node.ssh, ["sha256sum", worker]).stdout.split()[0]
        local_digest = _sha256(local_worker)
        if remote_digest != local_digest:
            raise ProcessFailure(f"Worker digest differs from PC5: {node_id}")
        version_text = run_ssh(node.ssh, ["python3", "--version"]).stdout.strip() or run_ssh(node.ssh, ["python3", "--version"]).stderr.strip()
        match = re.search(r"(\d+)\.(\d+)", version_text)
        if not match or tuple(map(int, match.groups())) < (3, 10):
            raise ProcessFailure(f"Python 3.10+ is required on {node_id}: {version_text}")
        submodule_head = "not-required"
        if any(p.command.safety_class != "simulation" for p in node_processes):
            submodule_head = run_ssh(node.ssh, ["git", "-C", f"{workspace}/modulos_rx_tx", "rev-parse", "HEAD"]).stdout.strip()
            expected_submodule = _local_git(repo_root / "modulos_rx_tx", "rev-parse", "HEAD")
            if submodule_head != expected_submodule:
                raise ProcessFailure(f"modulos_rx_tx commit differs on {node_id}: {submodule_head}")
        checked.append({"node_id": node_id, "branch": branch, "head": head, "worker_sha256": remote_digest, "python": version_text, "modulos_rx_tx": submodule_head})
    return checked
