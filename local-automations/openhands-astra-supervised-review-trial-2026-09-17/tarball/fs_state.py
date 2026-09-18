"""Persistent single-host checkpoints for explicitly configured manual Automation trials."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from state import STATE_BUDGET_BYTES, StateConflict, StateMissing, StateStore, StateTooLarge

LOCAL_STATE_BUDGET_BYTES = 1024 * 1024
LOCAL_STATE_DOCUMENT_BYTES = LOCAL_STATE_BUDGET_BYTES + 4 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def atomic_json(path, value, *, limit=2 * 1024 * 1024, immutable=False):
    path = Path(path)
    content = encoded(value)
    if len(content) > limit:
        raise StateTooLarge("Artifact exceeds the explicit byte budget")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".checkpoint-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(temporary, path)  # Atomic create-only: never replace an artifact.
            temporary.unlink()
        else:
            os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return content


class LocalStateStore(StateStore):
    """Single-host trial state; inherit all CAS, lease and publication semantics.

    Production Cloud validation remains 60 KiB. This local limit neither evicts
    grades/receipts nor changes the Cloud storage prerequisite.
    """

    @staticmethod
    def _validate(value):
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(value.get("jobs"), dict)
            or not isinstance(value.get("receipts"), dict)
        ):
            raise ValueError("Invalid or unsupported auditor state")
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > LOCAL_STATE_BUDGET_BYTES:
            raise StateTooLarge("Local per-PR state exceeds its explicit 1 MiB budget")


class FileKV:
    """Bounded per-PR value with flock + version CAS + fsync/atomic replacement.

    This is persistent single-host state, not ephemeral Cloud sandbox storage.
    Default compatibility limit is 60 KiB; manual callers explicitly select 1 MiB.
    """

    def __init__(self, path, *, on_write=None, value_budget_bytes=STATE_BUDGET_BYTES):
        if type(value_budget_bytes) is not int or not 1 <= value_budget_bytes <= LOCAL_STATE_BUDGET_BYTES:
            raise ValueError("Local value budget must be bounded at 1 MiB")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.on_write = on_write
        self.value_budget_bytes = value_budget_bytes
        self.document_budget_bytes = value_budget_bytes + 4 * 1024

    def request(self, method, path, body):
        target = urlsplit(path)
        if target.path != "/auditor-v1":
            raise ValueError("Only auditor-v1 state is supported")
        query = parse_qs(target.query)
        if method not in {"GET", "PUT"}:
            raise ValueError("Unsupported checkpoint operation")
        committed = None
        with self.lock_path.open("a+b") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists() and self.path.stat().st_size > self.document_budget_bytes:
                raise StateTooLarge("Checkpoint document exceeds its explicit byte budget")
            document = json.loads(self.path.read_text()) if self.path.exists() else None
            if method == "GET":
                if query != {"meta": ["true"]}:
                    raise ValueError("Metadata read required")
                if document is None:
                    raise StateMissing()
                return copy.deepcopy(document)
            if len(encoded(body)) > self.value_budget_bytes:
                raise StateTooLarge("Per-PR checkpoint exceeds its explicit byte budget")
            if query == {"nx": ["true"]}:
                if document is not None:
                    raise StateConflict(code="key_exists")
            elif set(query) == {"if_version"}:
                if document is None:
                    raise StateMissing()
                if len(query["if_version"]) != 1 or int(query["if_version"][0]) != document["version"]:
                    raise StateConflict(code="version_mismatch")
            else:
                raise ValueError("Conditional write required")
            committed = {"value": copy.deepcopy(body), "version": (document or {}).get("version", 0) + 1}
            atomic_json(self.path, committed, limit=self.document_budget_bytes)
        if self.on_write:
            self.on_write(copy.deepcopy(committed["value"]))
        return {"ok": True}
