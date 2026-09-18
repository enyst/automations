"""Event-driven review orchestration. Only this layer can authorize publication."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import PurePosixPath

from core import (
    ALLOWED_REPOSITORIES,
    REQUIRED_MODEL,
    dedupe_key,
    idempotency_marker,
    parse_correlated_pattern,
    trigger_key,
    validated_publish_payload,
)
from prompts import (
    build_stage_a_prompt,
    build_stage_b_prompt,
    render_review,
    validate_assessment,
    validate_audit,
)
from source import SourceBudgetExceeded, SourceMissingError

HISTORICAL_DIFF_PRELOAD_BYTES = 256 * 1024
REPOSITORY_COSMETIC_FIELDS = frozenset({
    "stargazers_count", "watchers", "watchers_count", "forks", "forks_count",
    "updated_at", "pushed_at",
})


class AuditStopped(RuntimeError):
    """A named, secret-free reason why no review can be published."""


def validate_retired_prs(value):
    """Freeze explicit PR-wide exclusions without reading GitHub or credentials."""
    if not isinstance(value, (list, tuple)):
        raise AuditStopped("invalid_retired_prs")
    retired = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"repository", "number"}:
            raise AuditStopped("invalid_retired_prs")
        repository, number = item["repository"], item["number"]
        if not isinstance(repository, str) or repository.lower() not in ALLOWED_REPOSITORIES:
            raise AuditStopped("retired_repository_not_allowed")
        if type(number) is not int or number <= 0:
            raise AuditStopped("invalid_retired_pr_number")
        target = (repository.lower(), number)
        if target in retired:
            raise AuditStopped("duplicate_retired_pr")
        retired.add(target)
    return frozenset(retired)


def validate_body_override(body, expected_verdict):
    """Check operator-authored presentation without changing model judgment."""
    if (
        not isinstance(body, str)
        or not body.strip()
        or len(body.encode("utf-8")) > 60_000
        or "\x00" in body
    ):
        raise AuditStopped("editorial_body_must_be_bounded_utf8_text")
    identity = "**OpenHands-Astra, helping Engel Nyst (@enyst).**"
    first_line, _, remainder = body.strip().partition("\n\n")
    icon = {"APPROVE": "✅", "REQUEST_CHANGES": "🛠️", "COMMENT": "💬"}.get(expected_verdict)
    verdict_first = icon is not None and re.fullmatch(
        re.escape(f"{icon} **{expected_verdict}**")
        + r"(?: · Risk: \*\*(?:LOW|MEDIUM|HIGH)\*\* · `[a-f0-9]{12}`)?",
        first_line,
    ) and remainder.startswith(identity)
    if not body.strip().startswith(identity) and not verdict_first:
        raise AuditStopped("editorial_body_identity_required")
    action = re.search(r"\*\*(APPROVE|REQUEST_CHANGES|COMMENT)\*\*", body)
    if action is None or action[1] != expected_verdict:
        raise AuditStopped("editorial_body_must_preserve_model_verdict")
    return body


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def event_target(envelope):
    """Only accepted GitHub event shapes can start a review; never manual guesses."""
    if envelope.get("trigger") != "event":
        raise AuditStopped("event_trigger_required")
    event = envelope.get("event", {})
    repo = event.get("repository", {}).get("full_name", "").lower()
    if repo not in ALLOWED_REPOSITORIES:
        raise AuditStopped("repository_not_allowed")
    if str(event.get("sender", {}).get("login", "")).lower() != "all-hands-bot":
        raise AuditStopped("sender_not_target_bot")
    if event.get("action") == "created" and event.get("comment"):
        number = event.get("issue", {}).get("number")
        kind, identifier = "comment_id", event["comment"].get("id")
        actor = event["comment"].get("user", {}).get("login", "")
    elif event.get("action") == "dismissed" and event.get("review"):
        number = event.get("pull_request", {}).get("number")
        kind, identifier = "review_id", event["review"].get("id")
        actor = event["review"].get("user", {}).get("login", "")
    else:
        raise AuditStopped("unsupported_event")
    if (
        str(actor).lower() != "all-hands-bot"
        or type(number) is not int
        or number <= 0
        or type(identifier) is not int
        or identifier <= 0
    ):
        raise AuditStopped("invalid_event_identity")
    return repo, number, kind, identifier


def _canonical_review_evidence(value):
    """Copy evidence, excluding only proven public-repository cosmetics.

    GitHub embeds whole repository objects in timeline cross-references. Their
    counters and activity timestamps can change independently of this PR. All
    other data (including repository identity, visibility and ref SHAs) remains
    part of the fingerprint. Raw snapshots and model inputs are left intact.
    """
    if isinstance(value, dict):
        public_repository = (
            type(value.get("id")) is int
            and value["id"] > 0
            and value.get("private") is False
            and isinstance(value.get("full_name"), str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["full_name"])
            is not None
            and value.get("html_url") == "https://github.com/" + value["full_name"]
        )
        return {
            key: _canonical_review_evidence(item)
            for key, item in value.items()
            if not (public_repository and key in REPOSITORY_COSMETIC_FIELDS)
        }
    if isinstance(value, list):
        return [_canonical_review_evidence(item) for item in value]
    return value


def fingerprint(snapshot):
    return digest(_canonical_review_evidence({
        key: snapshot[key]
        for key in ("reviews", "timeline", "comments", "inline_comments")
    }))


def review_selection_reason(snapshot):
    """Conservative PR-wide gates, independent of review actor or commit.

    A prior auditor intervention retires the PR, even if that review was later
    dismissed or the publisher/head changed. Other substantive GitHub verdicts
    block a new intervention while their review state remains active.
    """
    if any(
        "<!-- openhands-review-auditor:" in (item.get("body") or "")
        for collection in ("reviews", "comments")
        for item in snapshot.get(collection, ())
    ):
        return "prior_intervention_already_exists"
    if any(
        str(review.get("state", "")).upper() in {"APPROVED", "CHANGES_REQUESTED"}
        for review in snapshot["reviews"]
    ):
        return "active_approval_or_change_request"
    return None


def guidance(source, head, files):
    """Read scoped guidance as text, without loading repository plugins or hooks."""
    paths = {"AGENTS.md"}
    for item in files:
        path = item.get("head_path") or item.get("path")
        if path:
            for parent in PurePosixPath(path).parents:
                if str(parent) != ".":
                    paths.add(str(parent / "AGENTS.md"))
    result = []
    for path in sorted(paths):
        try:
            document = source.read_file(head, path)
        except SourceMissingError:
            continue  # Optional guidance path does not exist at this commit.
        result.append({"path": path, "commit": head, "content": document["content"]})
    return json.dumps(result, ensure_ascii=False)


def historical_source_context(github, source, repository, base, commits):
    """Preload all historical patches or explicitly use all complete manifests.

    The current-head patch is handled separately and remains mandatory. Exact
    historical commits stay admitted to read/search regardless of preload mode.
    """
    history = []
    on_demand = False

    def manifest_only(item):
        return {
            "base": item["base"], "head": item["head"], "files": item["files"],
            "context_mode": "on_demand",
            "note": (
                "No full historical patch is preloaded. This is the complete "
                "changed-path manifest. Use read_file/search_source at these exact "
                "base/head commits to inspect each review's relevant source; follow "
                "file pagination. Use a null/unscored technical score when evidence is "
                "insufficient. Do not judge historical code from the current head."
            ),
        }

    for commit in sorted(set(commits)):
        historical_base = github.merge_base(repository, base, commit)
        source.fetch(commit, historical_base)
        if on_demand:
            item = source.diff_manifest(historical_base, commit)
        else:
            try:
                item = source.diff(historical_base, commit)
            except SourceBudgetExceeded:
                item = source.diff_manifest(historical_base, commit)
                on_demand = True
        history.append(item)
        if not on_demand:
            on_demand = len(json.dumps(history).encode("utf-8")) > HISTORICAL_DIFF_PRELOAD_BYTES
        if on_demand:
            # Convert every entry together; never privilege an arbitrary prefix
            # with full patches or slice any patch string to fit the budget.
            history = [manifest_only(previous) for previous in history]
    return history


def verified_receipt(review, *, marker, actor, head, event):
    expected_state = {
        "APPROVE": "APPROVED",
        "REQUEST_CHANGES": "CHANGES_REQUESTED",
        "COMMENT": "COMMENTED",
    }[event]
    if (
        type(review.get("id")) is int
        and review["id"] > 0
        and review.get("user", {}).get("login", "").lower() == actor.lower()
        and review.get("commit_id") == head
        and review.get("state", "").upper() == expected_state
        and marker in (review.get("body") or "")
        and review.get("submitted_at")
    ):
        return {
            "marker": marker,
            "review_id": review["id"],
            "actor": actor,
            "commit_id": head,
            "state": expected_state,
            "url": review.get("html_url"),
        }
    return None


class Auditor:
    def __init__(
        self,
        *,
        github,
        publisher,
        state,
        source_factory,
        run_stage,
        llm,
        skill_text,
        run_id,
        publish=False,
        settle_seconds=(),
        retired_prs=(),
    ):
        self.retired_prs = validate_retired_prs(retired_prs)
        self.github, self.publisher, self.state = github, publisher, state
        self.source_factory, self.run_stage, self.llm = source_factory, run_stage, llm
        self.skill_text, self.run_id, self.publish = skill_text, run_id, publish
        self.settle_seconds = settle_seconds

    def handle(self, envelope):
        repo, number, kind, identifier = event_target(envelope)
        return self._handle_target(repo, number, kind=kind, identifier=identifier)

    def handle_manual(self, repository, number):
        """Explicit operator-selected, preparation-only batch; never forge events.

        Canonical GitHub evidence must still establish the complete correlated
        pattern, current author eligibility, and an open PR. Publication belongs
        to a separate controller after durable export and fresh validation.
        """
        if self.publish:
            raise AuditStopped("manual_preparation_cannot_publish")
        if not isinstance(repository, str) or repository.lower() not in ALLOWED_REPOSITORIES:
            raise AuditStopped("repository_not_allowed")
        if type(number) is not int or number <= 0:
            raise AuditStopped("invalid_pr_number")
        return self._handle_target(repository.lower(), number)

    def handle_trial(self, repository, number):
        """Assess and publish one explicitly selected supervised-trial target.

        The caller admits the bounded trial and pinned target. This entrypoint
        preserves the ordinary pattern, eligibility, retirement, model and final
        publication checks; it does not fabricate a GitHub event.
        """
        if self.publish is not True:
            raise AuditStopped("supervised_trial_requires_publication_mode")
        if not isinstance(repository, str) or repository.lower() not in ALLOWED_REPOSITORIES:
            raise AuditStopped("repository_not_allowed")
        if type(number) is not int or number <= 0:
            raise AuditStopped("invalid_pr_number")
        return self._handle_target(repository.lower(), number)

    def resume_prepared(self, repository, number, *, body_override=None):
        """Publish only an already audited, explicitly selected local checkpoint.

        Preserve the original run identity and all ordinary freshness checks.
        This entrypoint cannot create a new job or enter either model stage.
        """
        if not self.publish:
            raise AuditStopped("prepared_resume_requires_publication_mode")
        if not isinstance(repository, str) or repository.lower() not in ALLOWED_REPOSITORIES:
            raise AuditStopped("repository_not_allowed")
        if type(number) is not int or number <= 0:
            raise AuditStopped("invalid_pr_number")
        repository = repository.lower()
        if (repository, number) in self.retired_prs:
            return {"status": "skipped", "reason": "configured_retired_pr"}
        matching = [
            (key, job)
            for key, job in self.state.get().value["jobs"].items()
            if job.get("request", {}).get("repository") == repository
            and job.get("request", {}).get("number") == number
        ]
        if len(matching) != 1 or matching[0][1].get("status") != "audited":
            raise AuditStopped("one_existing_audited_job_required")
        key, job = matching[0]
        if job.get("run_id") != self.run_id:
            raise AuditStopped("prepared_resume_requires_original_run_id")
        if key != dedupe_key(repository, number, job["request"]["head"]):
            raise AuditStopped("prepared_job_identity_mismatch")
        if body_override is not None:
            event = job["data"]["audit"]["final_assessment"]["verdict"]
            validate_body_override(body_override, event)
            validated_publish_payload(
                repository, number, job["request"]["head"], body_override,
                resolved_model=REQUIRED_MODEL, event=event,
                expected_head_sha=job["request"]["head"],
            )
        return self._handle_target(repository, number, prepared_job_key=key,
                                   body_override=body_override)

    def _handle_target(self, repo, number, *, kind=None, identifier=None, prepared_job_key=None, body_override=None):
        if (repo, number) in self.retired_prs:
            return {"status": "skipped", "reason": "configured_retired_pr"}
        if body_override is not None and prepared_job_key is None:
            raise AuditStopped("editorial_body_requires_existing_prepared_job")
        snapshot = self.github.snapshot(repo, number)
        pr = snapshot["pr"]
        if pr.get("state") != "open" or pr.get("merged"):
            return {"status": "skipped", "reason": "pr_not_open"}
        if reason := review_selection_reason(snapshot):
            return {"status": "skipped", "reason": reason}
        matches = [
            m
            for m in parse_correlated_pattern(
                repo,
                number,
                snapshot["reviews"],
                snapshot["timeline"],
                snapshot["comments"],
            )
            if kind is None or m[kind] == identifier
        ]
        # GitHub can deliver an event before every REST timeline view catches up.
        # This bounded retry belongs to this delivery, not a scheduled scanner.
        for delay in self.settle_seconds if kind is not None else ():
            if matches:
                break
            time.sleep(delay)
            snapshot = self.github.snapshot(repo, number)
            pr = snapshot["pr"]
            if reason := review_selection_reason(snapshot):
                return {"status": "skipped", "reason": reason}
            matches = [
                m
                for m in parse_correlated_pattern(
                    repo,
                    number,
                    snapshot["reviews"],
                    snapshot["timeline"],
                    snapshot["comments"],
                )
                if m[kind] == identifier
            ]
        if not matches:
            return {"status": "skipped", "reason": "correlated_pattern_not_found"}
        if pr.get("state") != "open" or pr.get("merged"):
            return {"status": "skipped", "reason": "pr_not_open"}
        author = pr["user"]["login"]
        if not self.github.eligible(repo, author)["eligible"]:
            return {"status": "skipped", "reason": "author_eligibility_not_proven"}
        head, base = pr["head"]["sha"], pr["base"]["sha"]
        key, marker = (
            dedupe_key(repo, number, head),
            idempotency_marker(repo, number, head),
        )
        events = [trigger_key(repo, number, m["review_id"]) for m in matches]
        existing = self.state.get().value
        if prepared_job_key is not None:
            prepared = existing["jobs"].get(prepared_job_key)
            if (
                key != prepared_job_key
                or prepared is None
                or prepared.get("status") != "audited"
                or prepared.get("run_id") != self.run_id
            ):
                raise AuditStopped("prepared_job_changed_or_head_moved")
        if any(
            set(events) & set(item["event_ids"])
            for item in existing["receipts"].values()
        ):
            return {"status": "skipped", "reason": "trigger_already_published"}
        if any(
            item.get("request", {}).get("repository", "").lower() == repo
            and item.get("request", {}).get("number") == number
            for item in existing["receipts"].values()
        ):
            return {"status": "skipped", "reason": "prior_intervention_already_exists"}
        # Only the controller knows the posting credential or identity.
        actor = self.publisher.identity()["login"]
        if actor.lower() == author.lower():
            raise AuditStopped("publisher_is_pr_author_choose_separate_account")
        request = {
            "repository": repo,
            "number": number,
            "head": head,
            "base": base,
            "author": author,
            "publisher": actor,
            "policy_version": 1,
            "input_digest": digest({"pr": pr, "evidence": fingerprint(snapshot)}),
        }
        lease = self.state.claim(
            key, self.run_id, request=request, marker=marker, event_ids=events
        )
        if lease is None:
            return {"status": "skipped", "reason": "existing_job_or_receipt"}
        job = self.state.get().value["jobs"][key]
        if prepared_job_key is not None and job["status"] != "audited":
            raise AuditStopped("prepared_resume_cannot_run_model_stages")
        intent = False
        try:
            merge_base = self.github.merge_base(repo, base, head)
            source = self.source_factory(repo)
            source.fetch(head, merge_base)
            current_diff = source.diff(merge_base, head)
            try:
                changed_files = source.changed_files_context(merge_base, head)
            except SourceBudgetExceeded:
                # The full patch and complete manifest above remain intact.
                # Only eager before/after file loading changes to explicit pages.
                changed_files = {
                    "base": merge_base,
                    "head": head,
                    "mode": "on_demand",
                    "files": current_diff["files"],
                    "note": (
                        "Complete before/after changed-file contents exceeded the "
                        "preload budget and are not embedded here. The complete "
                        "diff and every changed path are included. Use read_file "
                        "at the listed base/head commits and follow next_start_line "
                        "to read needed full text; disclose any unsupported or "
                        "unavailable evidence. No source text was silently cut."
                    ),
                }
            context = {
                "diff": current_diff,
                "changed_files": changed_files,
                "validation": "Read-only source assessment; PR code and tests are not executed.",
            }
            instructions = guidance(source, head, current_diff["files"])
            issues = self.github.closing_issues(repo, number)
            current_pr = {**pr, "repository": repo, "base_sha": merge_base}
            if job["status"] == "claimed":
                prompt = build_stage_a_prompt(
                    current_pr,
                    source_context=json.dumps(context),
                    skill_text=self.skill_text,
                    linked_issues=issues,
                    repository_guidance=instructions,
                )
                independent = self.run_stage(
                    prompt,
                    llm=self.llm,
                    source=source,
                    allowed_commits=source.allowed_commits,
                    phase="A",
                )
                self._model_receipt(independent)
                assessment = validate_assessment(
                    independent["result"], expected_commit_id=head
                )
                job = self.state.checkpoint(
                    lease,
                    "independent_done",
                    data={
                        "assessment": assessment,
                        "assessment_hash": digest(assessment),
                        "stage_a_receipt": independent["receipt"],
                        "evidence_hash": fingerprint(snapshot),
                        "linked_issues_hash": digest(issues),
                    },
                )
            assessment = job["data"]["assessment"]
            if job["data"]["assessment_hash"] != digest(assessment):
                raise AuditStopped("independent_assessment_integrity_error")
            if job["data"]["evidence_hash"] != fingerprint(snapshot):
                raise AuditStopped("review_evidence_changed")
            if job["data"]["linked_issues_hash"] != digest(issues):
                raise AuditStopped("linked_issue_evidence_changed")
            if job["status"] == "independent_done":
                historical = historical_source_context(
                    self.github, source, repo, base,
                    {review["commit_id"] for review in snapshot["reviews"]} - {head},
                )
                audit_context = {
                    **context,
                    "historical_diffs": historical,
                    "correlated_patterns": matches,
                    "timeline": snapshot["timeline"],
                    "issue_comments": snapshot["comments"],
                    "inline_comments": snapshot["inline_comments"],
                    "available_commits": sorted(source.allowed_commits),
                }
                prompt = build_stage_b_prompt(
                    current_pr,
                    assessment,
                    snapshot["reviews"],
                    source_context=json.dumps(audit_context),
                    skill_text=self.skill_text,
                    linked_issues=issues,
                    repository_guidance=instructions,
                )
                compared = self.run_stage(
                    prompt,
                    llm=self.llm,
                    source=source,
                    allowed_commits=source.allowed_commits,
                    phase="B",
                )
                self._model_receipt(compared)
                audit = validate_audit(
                    compared["result"],
                    locked_assessment=assessment,
                    reviews=snapshot["reviews"],
                )
                job = self.state.checkpoint(
                    lease,
                    "audited",
                    data={"audit": audit, "stage_b_receipt": compared["receipt"]},
                )
            audit = validate_audit(
                job["data"]["audit"],
                locked_assessment=assessment,
                reviews=snapshot["reviews"],
            )
            event = audit["final_assessment"]["verdict"]
            public_body = (
                validate_body_override(body_override, event)
                if body_override is not None else render_review(assessment, audit)
            )
            payload = validated_publish_payload(
                repo,
                number,
                head,
                public_body,
                resolved_model=REQUIRED_MODEL,
                event=event,
                expected_head_sha=head,
            )
            if not self.publish:
                return {"status": "prepared", "action": event, "body": payload["body"]}
            fresh = self.github.snapshot(repo, number)
            if reason := review_selection_reason(fresh):
                raise AuditStopped(reason + "_before_publication")
            if (
                fresh["pr"].get("state") != "open"
                or fresh["pr"].get("merged")
                or fresh["pr"]["head"]["sha"] != head
                or fresh["pr"]["base"]["sha"] != base
                or fingerprint(fresh) != fingerprint(snapshot)
                or fresh["pr"]["user"]["login"] != author
                or fresh["pr"].get("title") != pr.get("title")
                or fresh["pr"].get("body") != pr.get("body")
                or self.github.closing_issues(repo, number) != issues
                or not self.github.eligible(repo, author)["eligible"]
            ):
                raise AuditStopped("pr_or_review_evidence_changed_before_publication")
            self.state.checkpoint(
                lease,
                "publish_intent",
                data={"payload_hash": digest(payload), "action": event},
            )
            intent = True
            try:
                posted = self.publisher.post_review(repo, number, payload)
                verified = self.publisher.get_review(
                    repo, posted["id"], pr_number=number
                )
                receipt = verified_receipt(
                    verified, marker=marker, actor=actor, head=head, event=event
                )
            except Exception:
                # Never retry POST. Reconcile one bounded, fully paginated read.
                receipt = None
                try:
                    found = [
                        verified_receipt(
                            r, marker=marker, actor=actor, head=head, event=event
                        )
                        for r in self.publisher.reviews(repo, number)
                    ]
                    receipts = [r for r in found if r]
                    if len(receipts) == 1:
                        receipt = receipts[0]
                except Exception:
                    pass
            if receipt is None:
                self.state.checkpoint(lease, "publish_unknown")
                raise AuditStopped("publication_unverified_no_automatic_retry")
            saved = self.state.finish(lease, receipt=receipt)
            return {"status": "published", "receipt": saved}
        except Exception as exc:
            if not intent:
                try:
                    self.state.checkpoint(
                        lease, "failed", data={"failure_type": type(exc).__name__}
                    )
                except Exception:
                    pass
            raise

    @staticmethod
    def _model_receipt(stage):
        if stage.get("receipt", {}).get("model") != REQUIRED_MODEL:
            raise AuditStopped("stage_did_not_use_required_astra_model")
