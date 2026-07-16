from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from sync_framework.config import load_inventory, load_profile, resolve_parameters
from sync_framework.deployment import _local_git, _sha256, verify_remote_workspaces
from sync_framework.domain import CapabilityDisabled, ProcessFailure
import sync_framework.nfs as nfs_module
from sync_framework.nfs import NfsEndpoint, bootstrap_nfs, describe_nfs, teardown_nfs, verify_nfs
from sync_framework.planning import build_plan
from sync_framework.processes.base import ProcessHandle, ProcessSpec
from sync_framework.processes.router import ProcessRouter
from sync_framework.processes.ssh import SshProcessAdapter, remote_command, run_ssh, ssh_prefix, ssh_target


REPO_ROOT = Path(__file__).resolve().parents[2]


def completed(argv=(), code=0, out="", err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


def test_ssh_command_building_and_errors(monkeypatch):
    config = {"host": "alias", "user": "user", "port": 2222, "identity_file": "~/.ssh/id", "known_hosts_file": "~/.ssh/known"}
    assert ssh_target(config) == "user@alias"
    prefix = ssh_prefix(config)
    assert "BatchMode=yes" in prefix and "StrictHostKeyChecking=yes" in prefix and "2222" in prefix
    assert remote_command(config, ["printf", "%s", "a b"])[-1].endswith("'a b'")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(code=8, err="denied"))
    with pytest.raises(ProcessFailure, match="denied"):
        run_ssh({"host": "alias"}, ["true"])
    assert run_ssh({"host": "alias"}, ["true"], check=False).returncode == 8


def test_adapter_and_router_security_guards(tmp_path, monkeypatch):
    base = ProcessSpec("x", ("true",), tmp_path, {}, tmp_path / "x.log", "simulation")
    with pytest.raises(CapabilityDisabled):
        SshProcessAdapter().preflight(base)
    with pytest.raises(CapabilityDisabled):
        SshProcessAdapter(allow_remote_simulation=True).preflight(replace(base, safety_class="dsp"))
    with pytest.raises(CapabilityDisabled, match="reception"):
        SshProcessAdapter().preflight(replace(base, safety_class="dsp"))
    with pytest.raises(CapabilityDisabled, match="transmission"):
        SshProcessAdapter().preflight(replace(base, safety_class="rf"))
    with pytest.raises(ProcessFailure, match="Incomplete"):
        SshProcessAdapter(allow_remote_simulation=True).preflight(base)
    router = ProcessRouter()
    assert router.for_spec(replace(base, transport="ssh")) is router.ssh
    assert router.for_handle(ProcessHandle("ssh", "x", 1, 1)) is router.ssh
    with pytest.raises(ProcessFailure, match="transport"):
        router.for_spec(replace(base, transport="other"))
    with pytest.raises(ProcessFailure, match="backend"):
        router.for_handle(ProcessHandle("other", "x", 1, 1))


def test_ssh_adapter_end_to_end_with_socketless_fake(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/usr/bin/env python3\nimport subprocess,sys\nraise SystemExit(subprocess.call(sys.argv[-1], shell=True))\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    output = tmp_path / "shared" / "rx"
    (output.parent / ".control").mkdir(parents=True)
    output.mkdir()
    config = {
        "run_id": "run_fake", "producer_id": "rx", "node_id": "pc3pc4", "role": "receiver",
        "modality": "5g", "output_dir": str(output), "clock_domain_id": "synthetic_5g_epoch",
        "artifact_ids": {"features": "features"},
    }
    spec = ProcessSpec(
        "rx", ("python3", str(REPO_ROOT / "tools" / "remote_dummy_worker.py")), REPO_ROOT, {},
        tmp_path / "ssh.log", "simulation", transport="ssh", ssh={"host": "fake"},
        worker_config=config, remote_runtime_dir=output / "runtime", shared_runtime_dir=output / "runtime",
    )
    adapter = SshProcessAdapter(allow_remote_simulation=True)
    adapter.preflight(spec)
    handle = adapter.start(spec)
    assert handle.backend == "ssh" and handle.remote_pid
    assert adapter.probe(handle).running
    status = adapter.stop(handle, 3)
    assert not status.running and status.exit_code == 0
    assert (output / "producer-result.json").is_file()


def test_real_worker_contract_with_harmless_child(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/usr/bin/env python3\nimport subprocess,sys\nraise SystemExit(subprocess.call(sys.argv[-1], shell=True))\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    output = tmp_path / "shared" / "tx"
    output.mkdir(parents=True)
    config = {
        "run_id": "run_fake", "producer_id": "tx_wifi", "node_id": "pc2",
        "output_dir": str(output), "safety_class": "rf",
        "worker_path": str(REPO_ROOT / "tools" / "remote_process_worker.py"),
        "argv": [sys.executable, "-u", "-c", "print('child ready')"],
        "cwd": str(tmp_path), "env": {}, "artifacts": ["process.log"],
    }
    spec = ProcessSpec(
        "tx_wifi", tuple(config["argv"]), tmp_path, {}, tmp_path / "ssh-real.log", "rf",
        transport="ssh", ssh={"host": "fake"}, worker_config=config,
        remote_runtime_dir=output / "runtime", shared_runtime_dir=output / "runtime",
    )
    adapter = SshProcessAdapter(allow_rf_transmit=True)
    handle = adapter.start(spec)
    deadline = time.monotonic() + 5
    while adapter.probe(handle).running and time.monotonic() < deadline:
        time.sleep(0.02)
    status = adapter.collect(handle)
    assert status.exit_code == 0
    receipt = json.loads((output / "producer-result.json").read_text())
    assert receipt["simulation"] is False and receipt["synthetic"] is False
    assert receipt["artifacts"][0]["path"] == "process.log"


def test_deployment_verification_with_fake_ssh(monkeypatch):
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.distributed.example.yaml")
    profile = load_profile(REPO_ROOT / "profiles" / "distributed_dummy.yaml")
    plan = build_plan(inventory, profile, resolve_parameters(profile, {"label": "x", "duration_s": "1"}))
    head = "a" * 40
    monkeypatch.setattr("sync_framework.deployment._local_git", lambda repo, *args: head if args[0] == "rev-parse" else "")
    monkeypatch.setattr("sync_framework.deployment._sha256", lambda path: "b" * 64)

    def fake_run(ssh, argv, **kwargs):
        if "status" in argv:
            return completed(out="")
        if "branch" in argv:
            return completed(out="main\n")
        if "rev-parse" in argv:
            return completed(out=head + "\n")
        if argv[0] == "sha256sum":
            return completed(out="%s  worker\n" % ("b" * 64))
        if argv[:2] == ["python3", "--version"]:
            return completed(out="Python 3.10.12\n")
        raise AssertionError(argv)

    monkeypatch.setattr("sync_framework.deployment.run_ssh", fake_run)
    checked = verify_remote_workspaces(plan, repo_root=REPO_ROOT)
    assert {item["node_id"] for item in checked} == {"pc1", "pc2", "pc3pc4"}


def test_deployment_helpers_and_remote_guards(monkeypatch):
    assert len(_local_git(REPO_ROOT, "rev-parse", "HEAD")) == 40
    assert len(_sha256(REPO_ROOT / "tools" / "remote_dummy_worker.py")) == 64
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.distributed.example.yaml")
    profile = load_profile(REPO_ROOT / "profiles" / "distributed_dummy.yaml")
    plan = build_plan(inventory, profile, resolve_parameters(profile, {"label": "x", "duration_s": "1"}))
    head = "a" * 40
    monkeypatch.setattr("sync_framework.deployment._local_git", lambda repo, *args: head if args[0] == "rev-parse" else "")
    monkeypatch.setattr("sync_framework.deployment._sha256", lambda path: "b" * 64)

    for failure in ("dirty", "branch", "head", "digest", "python"):
        def fake_run(ssh, argv, **kwargs):
            if "status" in argv:
                return completed(out="changed\n" if failure == "dirty" else "")
            if "branch" in argv:
                return completed(out="dev\n" if failure == "branch" else "main\n")
            if "rev-parse" in argv:
                return completed(out=(("c" * 40) if failure == "head" else head) + "\n")
            if argv[0] == "sha256sum":
                return completed(out=(("d" * 64) if failure == "digest" else ("b" * 64)) + "  worker\n")
            if argv[:2] == ["python3", "--version"]:
                return completed(out="Python 3.9.0\n" if failure == "python" else "Python 3.10.0\n")
            raise AssertionError(argv)
        monkeypatch.setattr("sync_framework.deployment.run_ssh", fake_run)
        with pytest.raises(ProcessFailure):
            verify_remote_workspaces(plan, repo_root=REPO_ROOT)


def test_nfs_fake_precheck_bootstrap_and_verify(tmp_path, monkeypatch):
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.distributed.example.yaml")
    inventory = replace(inventory, storage_root=tmp_path / "server", client_mount=tmp_path / "client")
    inventory.storage_root.mkdir()
    inventory.client_mount.mkdir()
    monkeypatch.setattr(nfs_module, "EXPORT_FILE", tmp_path / "managed.exports")
    endpoints = [NfsEndpoint("pc1", {"host": "pc1"}, "10.0.0.11", "10.0.0.5")]
    assert describe_nfs(inventory)["mutating"] is False
    monkeypatch.setattr("sync_framework.nfs._precheck", lambda inv: endpoints)
    monkeypatch.setattr("sync_framework.nfs._package_local", lambda package: None)
    monkeypatch.setattr("sync_framework.nfs._package_remote", lambda endpoint, package: None)
    monkeypatch.setattr("sync_framework.nfs._install_export", lambda text: None)
    local_calls = []
    monkeypatch.setattr("sync_framework.nfs._run_local", lambda argv, **kwargs: local_calls.append(argv) or completed(argv))
    mount_info = {"source": "10.0.0.5:/", "fstype": "nfs4", "options": "rw,hard,nosuid,nodev,noexec,relatime"}
    monkeypatch.setattr("sync_framework.nfs._mount_info", lambda endpoint, mount: mount_info)

    def fake_remote(ssh, argv, **kwargs):
        if argv[:2] == ["python3", "-c"]:
            remote = Path(argv[-2])
            local = inventory.storage_root / remote.name
            local.write_text(argv[-1], encoding="utf-8")
            return completed(argv)
        if argv[:2] == ["test", "!"]:
            remote = Path(argv[-1])
            return completed(argv, 0 if not (inventory.storage_root / remote.name).exists() else 1)
        return completed(argv)

    monkeypatch.setattr("sync_framework.nfs.run_ssh", fake_remote)
    verified = verify_nfs(inventory, endpoints=endpoints)
    assert verified["nodes"][0]["node_id"] == "pc1"
    monkeypatch.setattr("sync_framework.nfs.verify_nfs", lambda inv, endpoints=None: verified)
    result = bootstrap_nfs(inventory)
    assert result["status"] == "ready" and result["mutating"] is True
    assert any("exportfs" in call for call in local_calls)


def test_nfs_internal_prechecks_packages_export_and_teardown(tmp_path, monkeypatch):
    inventory = load_inventory(REPO_ROOT / "config" / "inventory.distributed.example.yaml")
    inventory = replace(inventory, storage_root=tmp_path / "server", client_mount=tmp_path / "client")
    inventory.storage_root.mkdir()
    inventory.client_mount.mkdir()
    managed = tmp_path / "sync-framework.exports"
    monkeypatch.setattr(nfs_module, "EXPORT_FILE", managed)
    local_calls = []

    def fake_local(argv, **kwargs):
        local_calls.append(argv)
        if argv[:2] == ["dpkg", "-s"]:
            return completed(argv, 1)
        if argv[-3:] == ["ufw", "status", "numbered"]:
            return completed(argv, out="Status: active\n[ 1] 2049/tcp ALLOW IN 10.0.0.11 # sync-framework-managed\n[ 2] 22/tcp ALLOW IN Anywhere\n")
        if "install" in argv and argv[-1] == str(managed):
            shutil.copyfile(argv[-2], argv[-1])
        if argv[:4] == ["sudo", "-n", "rm", "--"]:
            Path(argv[-1]).unlink()
        return completed(argv)

    def fake_ssh(ssh, argv, **kwargs):
        host = ssh["host"]
        if argv[:2] == ["python3", "-c"]:
            last = {"pc1": "11", "pc2": "12", "pc3pc4": "13"}[host]
            return completed(argv, out=f"10.0.0.5 4000 10.0.0.{last} 22\n")
        if argv[0] == "findmnt":
            return completed(argv, 1)
        if argv[:2] == ["dpkg", "-s"]:
            return completed(argv, 1)
        return completed(argv)

    monkeypatch.setattr(nfs_module, "_run_local", fake_local)
    monkeypatch.setattr(nfs_module, "run_ssh", fake_ssh)
    endpoints = nfs_module._precheck(inventory)
    assert len(endpoints) == 3 and {item.server_address for item in endpoints} == {"10.0.0.5"}
    nfs_module._package_local("nfs-kernel-server")
    nfs_module._package_remote(endpoints[0], "nfs-common")
    text = nfs_module._export_text(inventory, endpoints)
    nfs_module._install_export(text)
    nfs_module._install_export(text)
    assert managed.read_text() == text

    info_json = json.dumps({"filesystems": [{"source": "10.0.0.5:/", "fstype": "nfs4", "options": "rw,hard"}]})
    monkeypatch.setattr(nfs_module, "run_ssh", lambda ssh, argv, **kwargs: completed(argv, out=info_json))
    assert nfs_module._mount_info(endpoints[0], inventory.client_mount)["source"] == "10.0.0.5:/"

    monkeypatch.setattr(nfs_module, "_precheck", lambda inv: endpoints)
    monkeypatch.setattr(nfs_module, "_mount_info", lambda endpoint, mount: {"source": "10.0.0.5:/", "fstype": "nfs4", "options": "rw"})
    monkeypatch.setattr(nfs_module, "run_ssh", lambda ssh, argv, **kwargs: completed(argv))
    result = teardown_nfs(inventory)
    assert result["datasets_deleted"] is False and not managed.exists()


def test_nfs_local_runner_reports_failure():
    assert nfs_module._run_local(["true"]).returncode == 0
    with pytest.raises(ProcessFailure):
        nfs_module._run_local(["false"])


def test_nfs_rejects_non_nfs_and_incomplete_ssh_inventory():
    local_inventory = load_inventory(REPO_ROOT / "config" / "inventory.example.yaml")
    with pytest.raises(Exception, match="NFS actions"):
        nfs_module._precheck(local_inventory)
    with pytest.raises(Exception, match="at least one SSH"):
        nfs_module._ssh_nodes(local_inventory)
    nodes = dict(local_inventory.nodes)
    first = next(iter(nodes))
    nodes[first] = replace(nodes[first], transport="ssh", ssh=None)
    with pytest.raises(Exception, match="no connection data"):
        nfs_module._ssh_nodes(replace(local_inventory, nodes=nodes))
