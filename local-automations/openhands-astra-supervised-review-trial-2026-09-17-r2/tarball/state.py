"""CAS state for one event-driven auditor automation; no network or credentials.

The injected request(method, path, body) transport is relative to `/v1/kv`.
Map HTTP 404 to StateMissing and 409 to StateConflict, retaining the error code.
Explicitly bootstrap `auditor-v1` serially before enabling event delivery: the
backend's first document insert is not protected by an existing-row lock.

KV has a 64 KiB limit for the ENTIRE plaintext document, including other keys.
This client reserves 4 KiB of that budget and never silently evicts receipts.
There is no KV/GitHub transaction: persist publish_intent before POST, reconcile
the stable marker after an ambiguous response, and never automatically transfer
a publish_intent or publish_unknown job. Leases do not fence GitHub requests.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

STATE_KEY = "auditor-v1"
LEASE_SECONDS = 45 * 60  # Longer than Cloud's maximum 30-minute run.
STATE_BUDGET_BYTES = 60 * 1024


class StateMissing(RuntimeError):
    """The root must be bootstrapped before any job can execute."""


class StateConflict(RuntimeError):
    def __init__(
        self, message: str = "State conflict", *, code: str = "version_mismatch"
    ):
        super().__init__(message)
        self.code = code


class LeaseLost(StateConflict):
    def __init__(self) -> None:
        super().__init__("Job lease is no longer owned by this run", code="lease_lost")


class StateTooLarge(RuntimeError):
    """Keep durable artifacts elsewhere; do not discard dedupe receipts."""


@dataclass(frozen=True)
class Snapshot:
    value: dict[str, Any]
    version: int


@dataclass(frozen=True)
class Lease:
    job_id: str
    run_id: str
    generation: int


_TRANSITIONS = {
    "claimed": {"independent_done", "failed"},
    "independent_done": {"audited", "failed"},
    "audited": {"publish_intent", "failed"},
    "publish_intent": {"publish_unknown"},
    "publish_unknown": set(),
    "failed": set(),
}
_NO_TAKEOVER = {"publish_intent", "publish_unknown", "failed"}


class StateStore:
    def __init__(
        self,
        request: Callable[[str, str, Any], Any],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.request = request
        self.clock = clock

    @staticmethod
    def _validate(value: Any) -> None:
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(value.get("jobs"), dict)
            or not isinstance(value.get("receipts"), dict)
        ):
            raise ValueError("Invalid or unsupported auditor state")
        if (
            len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
            > STATE_BUDGET_BYTES
        ):
            raise StateTooLarge(
                "Auditor state exceeds its share of the 64 KiB KV limit"
            )

    def get(self) -> Snapshot:
        response = self.request("GET", f"/{STATE_KEY}?meta=true", None)
        value, version = response["value"], response["version"]
        self._validate(value)
        if type(version) is not int or version < 0:
            raise ValueError("KV metadata did not provide a valid document version")
        return Snapshot(copy.deepcopy(value), version)

    def bootstrap(self) -> Snapshot:
        """Serial setup only; runtime claim never initializes missing state."""
        try:
            return self.get()
        except StateMissing:
            value = {"schema_version": 1, "jobs": {}, "receipts": {}}
            try:
                self.request("PUT", f"/{STATE_KEY}?nx=true", value)
            except StateConflict as exc:
                if exc.code != "key_exists":
                    raise
            return self.get()

    def update(self, snapshot: Snapshot, value: dict[str, Any]) -> Snapshot:
        """One CAS attempt. Conflicts/ambiguous writes propagate; never overwrite."""
        self._validate(value)
        self.request(
            "PUT", f"/{STATE_KEY}?if_version={snapshot.version}", copy.deepcopy(value)
        )
        # PUT does not return the new global document version.
        return self.get()

    def claim(
        self,
        job_id: str,
        run_id: str,
        *,
        request: Mapping[str, Any],
        marker: str,
        event_ids: Sequence[str] = (),
    ) -> Lease | None:
        """Claim a repo/PR/head job key, or resume an expired pre-publish job.

        `request` holds compact immutable identifiers, including correlated review
        and trigger IDs. Duplicate heads share a caller-computed job_id/marker.
        None means an existing busy/terminal job, never a transport/CAS failure.
        """
        if not job_id or not run_id or not marker:
            raise ValueError("Job, run, and publication marker are required")
        snapshot = self.get()
        value = snapshot.value
        if job_id in value["receipts"]:
            return None
        now = self.clock()
        job = value["jobs"].get(job_id)
        if job is not None:
            if job["status"] not in _TRANSITIONS:
                raise ValueError("Unknown persisted job status")
            if job["request"] != dict(request) or job["marker"] != marker:
                raise StateConflict(
                    "Immutable job input differs", code="input_mismatch"
                )
            if job["status"] in _NO_TAKEOVER:
                return None
            if job["lease_expires_at"] > now:
                if job["run_id"] == run_id:
                    return Lease(job_id, run_id, job["generation"])
                return None
            job["generation"] += 1
            job["event_ids"] = sorted(set(job["event_ids"]) | set(event_ids))
        else:
            job = {
                "request": copy.deepcopy(dict(request)),
                "marker": marker,
                "event_ids": sorted(set(event_ids)),
                "status": "claimed",
                "generation": 1,
                "data": {},
            }
            value["jobs"][job_id] = job
        job.update(run_id=run_id, lease_expires_at=now + LEASE_SECONDS)
        lease = Lease(job_id, run_id, job["generation"])
        committed = self.update(snapshot, value)
        self._owned_job(committed, lease)
        return lease

    def _owned_job(self, snapshot: Snapshot, lease: Lease) -> dict[str, Any]:
        job = snapshot.value["jobs"].get(lease.job_id)
        if (
            job is None
            or job["run_id"] != lease.run_id
            or job["generation"] != lease.generation
            or job["lease_expires_at"] <= self.clock()
        ):
            raise LeaseLost()
        return job

    def checkpoint(
        self,
        lease: Lease,
        status: str,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Verify ownership, advance/renew a lease, and persist write-once outputs.

        Repeating the current status renews the lease. For an ambiguous GitHub
        POST use publish_unknown, never failed or an earlier stage. Data must
        contain durable content or artifact URI+hash, not only sandbox paths.
        """
        snapshot = self.get()
        job = self._owned_job(snapshot, lease)
        previous = job["status"]
        if previous not in _TRANSITIONS or (
            status != previous and status not in _TRANSITIONS[previous]
        ):
            raise ValueError(f"Invalid job transition: {previous} -> {status}")
        if previous == "failed":
            raise ValueError("Failed jobs require explicit operator recovery")
        for key, output in (data or {}).items():
            if key in job["data"] and job["data"][key] != output:
                raise ValueError(f"Checkpoint output is immutable: {key}")
            job["data"][key] = copy.deepcopy(output)
        job.update(status=status, lease_expires_at=self.clock() + LEASE_SECONDS)
        committed = self.update(snapshot, snapshot.value)
        return copy.deepcopy(self._owned_job(committed, lease))

    def finish(self, lease: Lease, *, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Save a reconciled GitHub receipt, then compact the job's large outputs.

        A verified review_id or comment_id plus the exact marker is required.
        Never call this merely because POST was attempted. Receipts are retained
        indefinitely; explicit archival/retention policy must precede pruning.
        """
        snapshot = self.get()
        job = self._owned_job(snapshot, lease)
        if job["status"] not in {"publish_intent", "publish_unknown"}:
            raise ValueError("Publication intent must be durable before finishing")
        publication_id = receipt.get("review_id", receipt.get("comment_id"))
        if type(publication_id) is not int or publication_id <= 0:
            raise ValueError("A verified positive review_id or comment_id is required")
        if receipt.get("marker") != job["marker"]:
            raise ValueError("Receipt marker does not match the job")
        saved = {
            **copy.deepcopy(dict(receipt)),
            "status": "published",
            "request": copy.deepcopy(job["request"]),
            "event_ids": list(job["event_ids"]),
            "run_id": lease.run_id,
            "generation": lease.generation,
            "published_at": self.clock(),
        }
        snapshot.value["receipts"][lease.job_id] = saved
        del snapshot.value["jobs"][lease.job_id]
        self.update(snapshot, snapshot.value)
        return copy.deepcopy(saved)
