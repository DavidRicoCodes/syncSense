"""Persistent run state machine backed by atomic JSON and a JSONL audit."""

from __future__ import annotations

import copy
import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .domain import InvalidTransition, SCHEMA_VERSION, ValidationFailure
from .storage import atomic_write_json
from .validation import validate_document


ALLOWED_TRANSITIONS = {
    "CREATED": {"PREFLIGHT"},
    "PREFLIGHT": {"ARMED", "FAILED"},
    "ARMED": {"RUNNING", "FAILED", "ABORTED"},
    "RUNNING": {"FINALIZING", "FAILED", "ABORTED"},
    "FINALIZING": {"COMPLETE", "FAILED"},
    "COMPLETE": set(), "FAILED": set(), "ABORTED": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.control_dir = self.run_dir / ".control"
        self.state_path = self.control_dir / "state.json"
        self.audit_path = self.control_dir / "transitions.jsonl"
        self.lock_path = self.control_dir / "state.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.control_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def create(self, *, run_id: str, profile: dict[str, Any], inventory: dict[str, Any], inventory_path: str, processes: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        state = {
            "schema_version": SCHEMA_VERSION, "run_id": run_id, "revision": 0, "state": "CREATED",
            "created_at": now, "updated_at": now, "profile": profile, "inventory": inventory,
            "inventory_path": inventory_path, "supervisor": None, "stop_request": None,
            "processes": processes, "last_error": None, "history": [], "recovery_count": 0,
        }
        validate_document(state, "run-state")
        with self.locked():
            if self.state_path.exists():
                raise ValidationFailure(f"State already exists: {self.state_path}")
            atomic_write_json(self.state_path, state, mode=0o600)
        return state

    def load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationFailure(f"Cannot read run state {self.state_path}: {exc}") from exc
        validate_document(state, "run-state")
        return state

    def update(self, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.locked():
            state = self.load()
            updated = copy.deepcopy(state)
            mutator(updated)
            updated["revision"] = state["revision"] + 1
            updated["updated_at"] = utc_now()
            validate_document(updated, "run-state")
            atomic_write_json(self.state_path, updated, mode=0o600)
            self._repair_audit(updated)
            return updated

    def transition(self, target: str, *, reason: str, actor: str = "syncctl", error: dict[str, Any] | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            source = state["state"]
            if target not in ALLOWED_TRANSITIONS[source]:
                raise InvalidTransition(f"Cannot transition {source} -> {target}")
            entry = {"seq": len(state["history"]), "from": source, "to": target, "at": utc_now(), "reason": reason, "actor": actor}
            if error:
                entry["error_code"] = error.get("code")
                state["last_error"] = error
            state["history"].append(entry)
            state["state"] = target
        return self.update(mutate)

    def _repair_audit(self, state: dict[str, Any]) -> None:
        entries: list[dict[str, Any]] = []
        malformed = False
        if self.audit_path.exists():
            try:
                with self.audit_path.open("r", encoding="utf-8") as handle:
                    entries = [json.loads(line) for line in handle if line.strip()]
            except (OSError, json.JSONDecodeError):
                malformed = True
        if not malformed and entries == state["history"]:
            return
        temporary = self.audit_path.with_name(f".{self.audit_path.name}.tmp.{os.getpid()}")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                for entry in state["history"]:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.audit_path)
            directory_fd = os.open(self.audit_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()
