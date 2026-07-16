"""Explicit, idempotent NFSv4 provisioning for the approved lab simulation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import CapabilityDisabled, Inventory, ProcessFailure, ValidationFailure
from .processes.ssh import run_ssh


EXPORT_FILE = Path("/etc/exports.d/sync-framework.exports")
MOUNT_OPTIONS = {"rw", "hard", "nosuid", "nodev", "noexec"}


@dataclass(frozen=True)
class NfsEndpoint:
    node_id: str
    ssh: dict[str, Any]
    client_address: str
    server_address: str


def _run_local(argv: list[str], *, check: bool = True, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise ProcessFailure(result.stderr.strip() or result.stdout.strip() or f"Command failed: {argv[0]}")
    return result


def _ssh_nodes(inventory: Inventory) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for node_id, node in sorted(inventory.nodes.items()):
        if node.transport == "ssh":
            if not node.ssh:
                raise ValidationFailure(f"SSH node has no connection data: {node_id}")
            result.append((node_id, node.ssh))
    if not result:
        raise ValidationFailure("NFS provisioning requires at least one SSH client")
    return result


def describe_nfs(inventory: Inventory) -> dict[str, Any]:
    nodes = [node_id for node_id, node in sorted(inventory.nodes.items()) if node.transport == "ssh"]
    return {
        "action": "storage_bootstrap", "backend": inventory.storage_backend,
        "server_root": str(inventory.storage_root), "client_mount": str(inventory.client_mount or ""),
        "nodes": nodes,
        "packages": {"server": "nfs-kernel-server", "clients": "nfs-common"},
        "mount_options": sorted(MOUNT_OPTIONS), "managed_export": str(EXPORT_FILE), "mutating": False,
    }


def _precheck(inventory: Inventory) -> list[NfsEndpoint]:
    if inventory.storage_backend != "nfs" or inventory.client_mount is None:
        raise CapabilityDisabled("NFS actions require storage.backend=nfs and client_mount")
    _run_local(["sudo", "-n", "true"])
    endpoints: list[NfsEndpoint] = []
    server_addresses: set[str] = set()
    probe_code = "import os; print(os.environ.get('SSH_CONNECTION',''))"
    for node_id, ssh in _ssh_nodes(inventory):
        run_ssh(ssh, ["true"], timeout=10)
        run_ssh(ssh, ["sudo", "-n", "true"], timeout=10)
        connection = run_ssh(ssh, ["python3", "-c", probe_code], timeout=10).stdout.strip().split()
        if len(connection) != 4:
            raise ProcessFailure(f"Cannot determine NFS addresses through SSH: {node_id}")
        server_address, client_address = connection[0], connection[2]
        server_addresses.add(server_address)
        endpoints.append(NfsEndpoint(node_id, ssh, client_address, server_address))
        mount = run_ssh(ssh, ["findmnt", "-n", "-o", "SOURCE,FSTYPE", "--mountpoint", str(inventory.client_mount)], check=False)
        if mount.returncode == 0 and not mount.stdout.strip().startswith(f"{server_address}:/ nfs"):
            raise ProcessFailure(f"Conflicting mount at {inventory.client_mount} on {node_id}: {mount.stdout.strip()}")
    if len(server_addresses) != 1:
        raise ProcessFailure("SSH clients do not observe one common PC5 NFS address")
    if EXPORT_FILE.exists():
        # Exact contents are checked once the intended export is known.
        if not EXPORT_FILE.is_file():
            raise ProcessFailure(f"Managed export path is not a regular file: {EXPORT_FILE}")
    for path in [Path("/etc/exports"), *Path("/etc/exports.d").glob("*")]:
        if path == EXPORT_FILE or not path.is_file():
            continue
        try:
            if str(inventory.storage_root) in path.read_text(encoding="utf-8", errors="ignore"):
                raise ProcessFailure(f"Storage root is already exported outside framework control: {path}")
        except PermissionError as exc:
            raise ProcessFailure(f"Cannot inspect existing exports: {path}") from exc
    return endpoints


def _package_local(package: str) -> None:
    if _run_local(["dpkg", "-s", package], check=False).returncode:
        _run_local(["sudo", "-n", "apt-get", "update"])
        _run_local(["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", package])


def _package_remote(endpoint: NfsEndpoint, package: str) -> None:
    if run_ssh(endpoint.ssh, ["dpkg", "-s", package], check=False).returncode:
        run_ssh(endpoint.ssh, ["sudo", "-n", "apt-get", "update"], timeout=300)
        run_ssh(endpoint.ssh, ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", package], timeout=300)


def _export_text(inventory: Inventory, endpoints: list[NfsEndpoint]) -> str:
    options = "rw,sync,no_subtree_check,root_squash,all_squash,anonuid=1000,anongid=1000,fsid=0"
    clients = " ".join(f"{endpoint.client_address}({options})" for endpoint in endpoints)
    return f"{inventory.storage_root} {clients}\n"


def _install_export(text: str) -> None:
    if EXPORT_FILE.exists() and EXPORT_FILE.read_text(encoding="utf-8") == text:
        return
    if EXPORT_FILE.exists():
        raise ProcessFailure(f"Managed export exists with unexpected content: {EXPORT_FILE}")
    fd, temporary = tempfile.mkstemp(prefix="sync-framework-", suffix=".exports")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _run_local(["sudo", "-n", "install", "-d", "-o", "root", "-g", "root", "-m", "0755", str(EXPORT_FILE.parent)])
        _run_local(["sudo", "-n", "install", "-o", "root", "-g", "root", "-m", "0644", temporary, str(EXPORT_FILE)])
    finally:
        Path(temporary).unlink(missing_ok=True)


def _mount_info(endpoint: NfsEndpoint, mount: Path) -> dict[str, str] | None:
    result = run_ssh(endpoint.ssh, ["findmnt", "-J", "-o", "SOURCE,FSTYPE,OPTIONS", "--mountpoint", str(mount)], check=False)
    if result.returncode:
        return None
    filesystems = json.loads(result.stdout).get("filesystems", [])
    return filesystems[0] if filesystems else None


def bootstrap_nfs(inventory: Inventory) -> dict[str, Any]:
    endpoints = _precheck(inventory)
    export = _export_text(inventory, endpoints)
    if EXPORT_FILE.exists() and EXPORT_FILE.read_text(encoding="utf-8") != export:
        raise ProcessFailure(f"Managed export conflicts with requested topology: {EXPORT_FILE}")
    _package_local("nfs-kernel-server")
    for endpoint in endpoints:
        _package_remote(endpoint, "nfs-common")
    _run_local(["sudo", "-n", "install", "-d", "-o", "1000", "-g", "1000", "-m", "0750", str(inventory.storage_root)])
    assert inventory.client_mount
    for endpoint in endpoints:
        run_ssh(endpoint.ssh, ["sudo", "-n", "install", "-d", "-o", "root", "-g", "root", "-m", "0755", str(inventory.client_mount)])
    _install_export(export)
    _run_local(["sudo", "-n", "exportfs", "-ra"])
    _run_local(["sudo", "-n", "systemctl", "enable", "--now", "nfs-kernel-server"])
    ufw = _run_local(["sudo", "-n", "ufw", "status"], check=False)
    if ufw.returncode == 0 and ufw.stdout.startswith("Status: active"):
        for endpoint in endpoints:
            _run_local(["sudo", "-n", "ufw", "allow", "proto", "tcp", "from", endpoint.client_address, "to", "any", "port", "2049", "comment", "sync-framework-managed"])
    for endpoint in endpoints:
        info = _mount_info(endpoint, inventory.client_mount)
        expected = f"{endpoint.server_address}:/"
        if info is None:
            run_ssh(endpoint.ssh, [
                "sudo", "-n", "mount", "-t", "nfs4", "-o", ",".join(sorted(MOUNT_OPTIONS)),
                expected, str(inventory.client_mount),
            ], timeout=60)
        elif info.get("source") != expected or not str(info.get("fstype", "")).startswith("nfs"):
            raise ProcessFailure(f"Unexpected existing mount on {endpoint.node_id}: {info}")
    verification = verify_nfs(inventory, endpoints=endpoints)
    return {"action": "storage_bootstrap", "status": "ready", "mutating": True, **verification}


def verify_nfs(inventory: Inventory, *, endpoints: list[NfsEndpoint] | None = None) -> dict[str, Any]:
    endpoints = endpoints or _precheck(inventory)
    assert inventory.client_mount
    observed = []
    writer = (
        "import os,sys; p=sys.argv[1]; t=p+'.tmp.%d'%os.getpid(); "
        "f=open(t,'w'); f.write(sys.argv[2]); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p)"
    )
    for endpoint in endpoints:
        info = _mount_info(endpoint, inventory.client_mount)
        if info is None:
            raise ProcessFailure(f"NFS is not mounted on {endpoint.node_id}")
        options = set(str(info.get("options", "")).split(","))
        if not MOUNT_OPTIONS.issubset(options):
            raise ProcessFailure(f"NFS mount options are incomplete on {endpoint.node_id}: {sorted(options)}")
        sentinel_name = f".sync-sentinel-{endpoint.node_id}"
        remote_path = inventory.client_mount / sentinel_name
        payload = f"{endpoint.node_id}\n"
        run_ssh(endpoint.ssh, ["python3", "-c", writer, str(remote_path), payload])
        local_path = inventory.storage_root / sentinel_name
        if local_path.read_text(encoding="utf-8") != payload:
            raise ProcessFailure(f"NFS sentinel is not visible from PC5: {endpoint.node_id}")
        local_path.unlink()
        if run_ssh(endpoint.ssh, ["test", "!", "-e", str(remote_path)], check=False).returncode:
            raise ProcessFailure(f"NFS sentinel deletion is not visible on {endpoint.node_id}")
        observed.append({"node_id": endpoint.node_id, "source": str(info.get("source")), "options": sorted(options)})
    return {"server_root": str(inventory.storage_root), "client_mount": str(inventory.client_mount), "nodes": observed}


def teardown_nfs(inventory: Inventory) -> dict[str, Any]:
    endpoints = _precheck(inventory)
    assert inventory.client_mount
    for endpoint in endpoints:
        info = _mount_info(endpoint, inventory.client_mount)
        if info is None:
            continue
        expected = f"{endpoint.server_address}:/"
        if info.get("source") != expected:
            raise ProcessFailure(f"Refusing to unmount unowned source on {endpoint.node_id}: {info.get('source')}")
        run_ssh(endpoint.ssh, ["sudo", "-n", "umount", str(inventory.client_mount)], timeout=60)
    if EXPORT_FILE.exists():
        requested = _export_text(inventory, endpoints)
        if EXPORT_FILE.read_text(encoding="utf-8") != requested:
            raise ProcessFailure("Refusing to remove a modified managed export")
        _run_local(["sudo", "-n", "rm", "--", str(EXPORT_FILE)])
        _run_local(["sudo", "-n", "exportfs", "-ra"])
    ufw = _run_local(["sudo", "-n", "ufw", "status", "numbered"], check=False)
    if ufw.returncode == 0 and ufw.stdout.startswith("Status: active"):
        managed_numbers = []
        for line in ufw.stdout.splitlines():
            if "sync-framework-managed" not in line or "2049" not in line:
                continue
            if not any(endpoint.client_address in line for endpoint in endpoints):
                continue
            try:
                managed_numbers.append(int(line.split("[", 1)[1].split("]", 1)[0].strip()))
            except (ValueError, IndexError):
                continue
        for number in sorted(managed_numbers, reverse=True):
            _run_local(["sudo", "-n", "ufw", "--force", "delete", str(number)])
    return {"action": "storage_teardown", "status": "removed_managed_configuration", "datasets_deleted": False, "packages_removed": False}
