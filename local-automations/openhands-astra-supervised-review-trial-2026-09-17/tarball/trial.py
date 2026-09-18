"""Bounded manual trial dispatched by a native, single-host OpenHands Automation.

Persistent host storage is explicit; this is never an ephemeral Cloud fallback.
The ordinary event entrypoint retains its Automation KV requirement.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from controller import Auditor, AuditStopped, validate_retired_prs
from core import ALLOWED_REPOSITORIES
from fs_state import FileKV, LocalStateStore, atomic_json, LOCAL_STATE_BUDGET_BYTES
from github import GitHub, _CLOSING_ISSUES_QUERY
from runtime import Transport, github_transport
from source import SourceRepo


def validate_trial(config, environ):
    if config.get("mode") != "manual-trial" or config.get("state_backend") != "persistent_host_file":
        raise AuditStopped("explicit_persistent_host_trial_required")
    if config.get("secret_backend") != "macos_keychain" or sys.platform != "darwin":
        raise AuditStopped("native_macos_trial_required")
    if environ.get("SANDBOX_ID"):
        raise AuditStopped("ephemeral_sandbox_trial_forbidden")
    envelope = json.loads(environ.get("AUTOMATION_EVENT_PAYLOAD", "{}"))
    trigger = envelope.get("trigger_payload", {})
    if (envelope.get("trigger") != "event" or "event" in envelope
            or trigger.get("type") != "event" or trigger.get("filter") != "`false`"
            or not re.fullmatch(r"[0-9a-f-]{36}", str(envelope.get("automation_id", "")))
            or not re.fullmatch(r"[0-9a-f-]{36}", environ.get("AUTOMATION_RUN_ID", ""))):
        raise AuditStopped("manual_dispatch_with_disabled_event_filter_required")
    if (config.get("model_route") != "eval_openrouter"
            or config.get("publisher_login") != "enyst"
            or not config.get("model_profile")
            or environ.get("AUTOMATION_MODEL") != config["model_profile"]):
        raise AuditStopped("explicit_astra_route_and_enyst_required")
    targets = config.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 4:
        raise AuditStopped("trial_requires_one_to_four_targets")
    retired = validate_retired_prs(config.get("retired_prs", ()))
    seen, normalized = set(), []
    for row in targets:
        if not isinstance(row, dict) or set(row) != {"repository", "number", "head", "base"}:
            raise AuditStopped("invalid_trial_target")
        repo, number = row["repository"], row["number"]
        if (not isinstance(repo, str) or repo.lower() not in ALLOWED_REPOSITORIES
                or type(number) is not int or number <= 0
                or any(not re.fullmatch(r"[0-9a-f]{40}", str(row[x])) for x in ("head", "base"))):
            raise AuditStopped("invalid_trial_target")
        identity = (repo.lower(), number)
        if identity in retired:
            raise AuditStopped("configured_retired_pr")
        if identity in seen:
            raise AuditStopped("duplicate_trial_target")
        seen.add(identity)
        normalized.append({**row, "repository": repo.lower()})
    root = Path(config.get("state_directory", ""))
    if not root.is_absolute() or not root.parent.is_dir() or root.is_symlink():
        raise AuditStopped("persistent_host_directory_required")
    return normalized, root.resolve(), envelope["automation_id"]


def keychain_secret(name):
    if name not in {"ENYST_GH_TOKEN", "LITELLM_API_KEY_EVAL"}:
        raise AuditStopped("trial_secret_not_allowed")
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", "openhands", "-a", name, "-w"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    value = result.stdout.strip()
    if result.returncode or not value:
        raise AuditStopped("trial_secret_unavailable")
    return value


def public_evidence(value):
    if isinstance(value, dict):
        if value.get("private") is True or value.get("state") == "PENDING":
            raise AuditStopped("nonpublic_review_evidence")
        for item in value.values():
            public_evidence(item)
    elif isinstance(value, list):
        for item in value:
            public_evidence(item)


def read_only_transport(token):
    request = github_transport(token)
    def read(method, path, body=None):
        if method != "GET" and not (method == "POST" and path == "/graphql"
                and isinstance(body, dict) and body.get("query") == _CLOSING_ISSUES_QUERY):
            raise AuditStopped("reader_write_forbidden")
        return request(method, path, body)
    return read


class TrialGitHub(GitHub):
    def __init__(self, request, target, directory, anonymous_request=None):
        super().__init__(request)
        self.target, self.directory, self.snapshot_count = target, directory, 0
        self.anonymous = anonymous_request or Transport("https://api.github.com", {
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenHands-Astra-public-evidence-verification",
        }).request

    def verify_public_issue(self, issue):
        url = issue.get("url", "")
        match = re.fullmatch(r"https://api\.github\.com/repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)", url)
        if not match or type(issue.get("number")) is not int or issue["number"] != int(match[2]):
            raise AuditStopped("invalid_cross_reference_identity")
        # An unauthenticated exact issue fetch proves this full text is public.
        public = self.anonymous("GET", f"/repos/{match[1]}/issues/{match[2]}")
        if (not isinstance(public, dict) or public.get("number") != issue["number"]
                or public.get("html_url") != issue.get("html_url")
                or public.get("title") != issue.get("title")
                or (public.get("body") or "") != (issue.get("body") or "")):
            raise AuditStopped("cross_reference_public_text_mismatch")

    def snapshot(self, repository, number):
        if (repository.lower(), number) != (self.target["repository"], self.target["number"]):
            raise AuditStopped("trial_target_mismatch")
        value = super().snapshot(repository, number)
        public_evidence(value)
        for event in value["timeline"]:
            source = event.get("source")
            if isinstance(source, dict) and isinstance(source.get("issue"), dict):
                self.verify_public_issue(source["issue"])
        pr = value["pr"]
        if any(pr[side]["sha"] != self.target[side] for side in ("head", "base")):
            raise AuditStopped("trial_target_commit_changed")
        for side in ("head", "base"):
            repo = pr[side].get("repo")
            if repo is None and side == "head":
                continue
            if not isinstance(repo, dict) or repo.get("private") is not False:
                raise AuditStopped("public_trial_repository_required")
        self.snapshot_count += 1
        atomic_json(self.directory / f"snapshot-{self.snapshot_count}.json", value,
                    limit=8 * 1024 * 1024, immutable=True)
        return value

    def closing_issues(self, repository, number):
        issues = super().closing_issues(repository, number)
        public_evidence(issues)
        for issue in issues:
            repo = issue.get("repository", {}).get("nameWithOwner", "")
            number = issue.get("number")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or type(number) is not int or number <= 0:
                raise AuditStopped("invalid_public_issue_identity")
            public_repo = self.anonymous("GET", f"/repos/{repo}")
            if public_repo.get("private") is not False:
                raise AuditStopped("public_linked_repository_required")
            public = self.anonymous("GET", f"/repos/{repo}/issues/{number}")
            if (public.get("number") != number or public.get("html_url") != issue.get("url")
                    or public.get("title") != issue.get("title")
                    or (public.get("body") or "") != issue.get("body")):
                raise AuditStopped("linked_issue_public_text_mismatch")
        return issues


class DeadlinePublisher(GitHub):
    def __init__(self, request, deadline):
        super().__init__(request)
        self.deadline = deadline

    def post_review(self, repository, number, payload):
        if time.monotonic() >= self.deadline:
            raise AuditStopped("trial_publication_deadline_exhausted")
        return super().post_review(repository, number, payload)


def run(config, environ=os.environ):
    targets, root, automation_id = validate_trial(config, environ)
    root.mkdir(mode=0o700, exist_ok=True)
    lock = (root / ".trial.lock").open("a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise AuditStopped("trial_already_running") from None
    try:
        return _run_locked(config, environ, targets, root, automation_id)
    except BaseException as exc:
        path = root / "index.json"
        if path.exists():
            index = json.loads(path.read_text())
            if not index.get("done"):
                index.update(done=True, runtime_error_code=type(exc).__name__)
                atomic_json(path, index)
        raise
    finally:
        lock.close()


def _run_locked(config, environ, targets, root, automation_id):
    # A manual rerun cannot restart a completed, failed, or uncertain batch.
    if (root / "index.json").exists() or any(root.glob("*/state.json")):
        raise AuditStopped("trial_already_attempted_operator_reconciliation_required")
    index = {"schema_version": 1, "mode": "manual-trial", "automation_id": automation_id,
             "run_id": environ["AUTOMATION_RUN_ID"], "model_route": "eval_openrouter",
             "publisher": "enyst", "done": False, "rows": [dict(t, status="pending") for t in targets]}
    atomic_json(root / "index.json", index)
    from reviewer import ReviewerError, create_llm, run_stage
    token = keychain_secret("ENYST_GH_TOKEN")
    publisher = GitHub(github_transport(token))
    if publisher.identity().get("login", "").lower() != "enyst":
        raise AuditStopped("publisher_identity_not_verified")
    model_key = keychain_secret("LITELLM_API_KEY_EVAL")
    here = Path(__file__).parent
    skill = "\n\n".join((here / "review-skill" / name).read_text()
                          for name in ("SKILL.md", "risk-evaluation.md"))
    manifest = {str(p.relative_to(here)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in here.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    atomic_json(root / "bundle-manifest.json", manifest, immutable=True)
    mutex, stop = threading.Lock(), threading.Event()
    deadline = time.monotonic() + 1500

    def progress(row, **values):
        with mutex:
            row.update(values)
            atomic_json(root / "index.json", index)
            print(json.dumps({k: row[k] for k in ("repository", "number", "status", "phase") if k in row}), flush=True)

    def process(row):
        if stop.is_set() or time.monotonic() >= deadline:
            progress(row, status="deferred")
            return
        directory = root / (row["repository"].replace("/", "_") + "-" + str(row["number"]))
        directory.mkdir(mode=0o700)
        state = LocalStateStore(FileKV(directory / "state.json",
            value_budget_bytes=LOCAL_STATE_BUDGET_BYTES).request)
        state.bootstrap()
        reader = TrialGitHub(read_only_transport(token), row, directory)
        llm = create_llm(model_key, route="eval_openrouter").model_copy(update={"timeout": 900, "num_retries": 1})
        stage_count = 0
        def stage(prompt, **kwargs):
            nonlocal stage_count
            if stop.is_set() or time.monotonic() >= deadline or stage_count >= 2:
                raise AuditStopped("trial_model_budget_exhausted")
            phase = kwargs["phase"]
            stage_count += 1
            progress(row, status="reviewing", phase=phase)
            try:
                def model_progress(event):
                    if event["event_count"] % 5 == 0 or event["assistant_messages"]:
                        progress(row, model_progress=event)
                result = run_stage(prompt, max_iterations=80,
                                   progress_callback=model_progress, **kwargs)
                atomic_json(directory / f"stage-{phase}.json", result,
                            limit=2 * 1024 * 1024, immutable=True)
                return result
            except BaseException:
                stop.set()
                raise
        try:
            progress(row, status="checking")
            with TemporaryDirectory(prefix="review-trial-source-") as source_dir:
                auditor = Auditor(github=reader, publisher=DeadlinePublisher(github_transport(token), deadline), state=state,
                    source_factory=lambda repo: SourceRepo(repo, Path(source_dir) / "objects"),
                    run_stage=stage, llm=llm, skill_text=skill, run_id=environ["AUTOMATION_RUN_ID"],
                    publish=True, retired_prs=config.get("retired_prs", ()))
                result = auditor.handle_trial(row["repository"], row["number"])
        except BaseException as exc:
            result = {"status": "failed", "error_code": str(exc) if isinstance(exc, AuditStopped) else type(exc).__name__}
            if isinstance(exc, ReviewerError):
                result["error_code"], result["diagnostics"] = exc.code, exc.diagnostics
        atomic_json(directory / "result.json", result, immutable=True)
        progress(row, **{k: v for k, v in result.items() if k in {"status", "reason", "receipt", "error_code", "action"}})

    try:
        # One real Automation canary before allowing the remaining pairwise work.
        process(index["rows"][0])
        if index["rows"][0]["status"] not in {"published", "skipped"}:
            stop.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(process, index["rows"][1:]))
    finally:
        index["done"] = True
        atomic_json(root / "index.json", index)
    succeeded = all(row["status"] in {"published", "skipped"} for row in index["rows"])
    return {"status": "trial_completed" if succeeded else "trial_incomplete",
            "published": sum(row["status"] == "published" for row in index["rows"])}
