from __future__ import annotations

import json
from pathlib import Path

import yaml

from sync_framework.orchestration import finalize_run, preflight, start_run
from sync_framework.publication import verify_published_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_nosync_passive_happy_path(inventory_path, profile_path):
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "0.3"})
    assert store is not None and store.load()["state"] == "ARMED"
    finalizing = start_run(plan, store)
    assert finalizing["state"] == "FINALIZING"
    assert all(record["exit_code"] == 0 for record in finalizing["processes"].values())
    manifest = finalize_run(plan, store, repo_root=REPO_ROOT)
    assert manifest["state"] == "COMPLETE"
    assert manifest["inference_runs"] == []
    assert len(manifest["git_revisions"]["parent"]["head"]) == 40
    assert manifest["git_revisions"]["parent"]["worktree_state"] in {"clean", "dirty"}
    assert manifest["clock_relationships"] == [{
        "left": "pc3_5g_acquisition", "right": "pc4_wifi_acquisition",
        "relation": "not_comparable", "reason": "no_common_acquisition_timebase",
    }]
    verify_published_manifest(plan.run_dir)

    trace = [json.loads(line) for line in (plan.run_dir / ".control" / "worker-events.jsonl").read_text(encoding="utf-8").splitlines()]
    positions = {(entry["producer_id"], entry["event"]): index for index, entry in enumerate(trace)}
    assert positions[("tx_wifi", "started")] > positions[("rx_5g", "ready")]
    assert positions[("tx_wifi", "started")] > positions[("rx_wifi", "ready")]
    assert positions[("tx_wifi", "stopping")] < positions[("rx_5g", "stopping")]
    assert positions[("tx_wifi", "stopping")] < positions[("rx_wifi", "stopping")]


def test_preflight_dry_run_is_non_mutating(inventory_path, profile_path, tmp_path):
    before = set(tmp_path.rglob("*"))
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "1"}, dry_run=True)
    after = set(tmp_path.rglob("*"))
    assert store is None
    assert plan.run_id is None
    assert before == after


def test_receiver_start_failure_never_starts_transmitter(inventory_path, profile_path):
    raw = yaml.safe_load(Path(inventory_path).read_text(encoding="utf-8"))
    for node in raw["nodes"]:
        if node["node_id"] == "pc3":
            node["commands"][0]["argv"].append("--fail-start")
    Path(inventory_path).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    plan, store = preflight(inventory_path, profile_path, {"label": "empty", "duration_s": "1"})
    try:
        start_run(plan, store)
    except Exception:
        pass
    assert store.load()["state"] == "FAILED"
    trace_path = plan.run_dir / ".control" / "worker-events.jsonl"
    trace = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
    assert '"producer_id": "tx_wifi"' not in trace
    assert not (plan.run_dir / "manifest.json").exists()
