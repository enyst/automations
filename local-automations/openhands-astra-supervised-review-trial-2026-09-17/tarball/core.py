"""Pure policy checks for the personal review auditor; no model or network I/O.

The caller must supply every page of canonical GitHub REST reviews, timeline,
and issue comments. Detection is intentionally conservative: current edited text
cannot establish what a comment said when it was first published.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

REQUIRED_MODEL = "openai/gpt-6-astra"
ALLOWED_REPOSITORIES = frozenset(
    {
        "openhands/openhands",
        "openhands/software-agent-sdk",
        "openhands/automation",
        "openhands/extensions",
    }
)
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_MARKER_PREFIX = "<!-- openhands-review-auditor:"
_POSTED_APPROVAL = re.compile(
    r"^(?:i(?: have|'ve)?\s+)?(?:successfully\s+(?:reviewed and\s+)?)?"
    r"posted\s+(?:an?\s+)?approve(?:d)?\s+review\b"
    r"|^(?:the|my)\s+review\s+was\s+posted\s+as\s+approve(?:d)?\b",
    re.IGNORECASE,
)
_POSTED_REVIEW = re.compile(
    r"^(?:(?:the|my)\s+)?review\s+(?:(?:was|has been)\s+)?posted\b"
    r"|^code\s+review\s+for\s+PR\s*#\d+[^\n.!?]{0,200}\bhas been\s+posted\b",
    re.I,
)
_APPROVAL_METADATA = re.compile(
    r"\bstate\s*[:=]\s*approve(?:d)?\b"
    r"|\bas\s+(?:an?\s+)?approve(?:d)?\b"
    r"|(?:^|[(,])\s*approve(?:d)?\s+(?:review|event)\b"
    r"|\breview\s+(?:is|was|has been)\s+(?:submitted\s+)?(?:as\s+)?(?:an?\s+)?approve(?:d)?\b",
    re.I,
)
_REVIEW_APPROVAL_STATE = re.compile(
    r"^(?:the|my)\s+review\s+(?:is|was|has been)\s+(?:submitted\s+)?"
    r"(?:as\s+)?(?:an?\s+)?approve(?:d)?\b", re.I,
)
_CORRECTIVE_CONTEXT = re.compile(
    r"\b(?:dismissed|withdrawn|retracted|mistakenly|accidentally)\b"
    r"|\binstead\s+(?:of\s+)?(?:an?\s+)?comment\b"
    r"|\b(?:did not|didn't|never|not actually)\s+(?:post|approve)\b",
    re.IGNORECASE,
)
_REVIEW_URL = re.compile(r"https://github\.com/[^\s<>\])]+#pullrequestreview-\d+", re.I)
_REVIEW_ID = re.compile(
    r"\breview\s*(?:id\s*[:=#]?\s*|#\s*)[`*]*(\d+)\b", re.I
)
_COMMIT_METADATA = re.compile(
    r"\b(?:commit(?:\s+id)?|head(?:\s+(?:sha|commit))?|sha)\s*[:=]?\s*([0-9a-f]{7,40})\b",
    re.I,
)
_PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)\b", re.I)
_PR_NUMBER = re.compile(r"\b(?:PR|pull\s+request)\s*#?\s*(\d+)\b", re.I)
_QUALIFIED_PR = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\b", re.I)
_NAMED_REPOSITORY = re.compile(
    r"(?:\(\s*|\b(?:repository|repo)\s*[:=]?\s+|\b(?:on|in|for|to)\s+)"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?=[\s),;#]|\.(?:\s|$)|$)", re.I,
)
_EXPLICIT_REPOSITORY = re.compile(
    r"\b(?:repository|repo)(?:\s*[:=]\s*|\s+(?:is\s+)?)"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?=[\s),;#]|\.(?:\s|$)|$)", re.I,
)
_NON_APPROVAL_METADATA = re.compile(
    r"\b(?:state\s*[:=]|as)\s*(?:an?\s+)?(?:comment(?:ed)?|changes_requested)\b", re.I
)


def _repository(repository: str) -> str:
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Expected an owner/repository identifier")
    return repository.lower()


def _positive_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _login(record: Mapping[str, Any], field: str) -> str:
    user = record.get(field)
    login = user.get("login") if isinstance(user, Mapping) else None
    return login.lower() if isinstance(login, str) else ""


def _prose(body: str) -> str:
    """Exclude quotes, code and HTML comments before considering a claim."""
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    lines: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        match = re.match(r"(`{3,}|~{3,})", stripped)
        if match:
            if fence is None:
                fence = match.group(1)[0]
            elif match.group(1)[0] == fence:
                fence = None
            continue
        if fence or stripped.startswith(">") or line.startswith(("    ", "\t")):
            continue
        # IDs, SHAs, decision enums and repository/review references are receipt
        # metadata. A complete claim inside inline code remains excluded, as do
        # fenced code blocks. Preserve conflicting references to reject them.
        stripped = re.sub(
            r"`([^`]+)`",
            lambda m: m[1] if re.fullmatch(
                r"[0-9a-fA-F]+|APPROVE(?:D)?|COMMENT(?:ED)?|CHANGES_REQUESTED"
                r"|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
                r"|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+(?:#pullrequestreview-\d+)?",
                m[1], re.I,
            ) else "",
            stripped,
        )
        lines.append(stripped.replace("**", "").replace("__", ""))
    return "\n".join(lines)


def _corrects_to_comment(message: Any) -> bool:
    if not isinstance(message, str):
        return False
    text = message.replace("`", "").replace("*", " ")
    return bool(
        re.search(r"\bapprove(?:d)?\s+instead\s+of\s+comment\b", text, re.I)
        or re.search(
            r"\b(?:should|must)\s+(?:have\s+been\s+|be\s+|use\s+)?comment\b[^.!?\n]{0,100}\b(?:not|rather than|instead of)\s+approve(?:d)?\b",
            text,
            re.I,
        )
    )


def _claims_same_review(
    body: Any, repository: str, pr_number: int, review_id: int, head: str
) -> str | None:
    """Return the required correlation path, never infer an ID from prose alone."""
    if not isinstance(body, str):
        return None
    prose = _prose(body)
    # A truthful retrospective correction is not the contradictory receipt we
    # are looking for, even when the correction occupies a later paragraph.
    if _CORRECTIVE_CONTEXT.search(prose):
        return None
    if re.search(r"\b(?:not|never)\s+(?:actually\s+)?(?:an?\s+)?(?:approve(?:d)?|posted|submitted)\b|\b(?:cannot|can't|could not|couldn't|didn't|did not)\s+(?:have\s+)?post\b", prose, re.I):
        return None
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", prose)]
    # Publication and state must belong to the same assertion. A subsequent
    # hypothetical example ("...; an APPROVE review would be inappropriate")
    # does not turn a successful COMMENT submission into an approval receipt.
    statements = [re.split(r"[;.!?](?:\s|$)", part, maxsplit=1)[0] for part in paragraphs]
    if not any(
        not _NON_APPROVAL_METADATA.search(statement)
        and (
            _POSTED_APPROVAL.search(statement)
            or (
                _POSTED_REVIEW.search(statement)
                and (
                    _APPROVAL_METADATA.search(statement)
                    or any(_REVIEW_APPROVAL_STATE.search(part) for part in statements)
                )
            )
        )
        for statement in statements
    ):
        return None
    if any(int(number) != pr_number for number in _PR_NUMBER.findall(prose)):
        return None
    # Bare owner/name text is ambiguous in a technical summary: e.g. a file
    # path or "state/enabled toggles". Infer such references only within the
    # actual publication assertion; explicit repository labels/links and PR
    # references elsewhere in the receipt still reject a conflicting target.
    publication_statements = [
        statement for statement in statements
        if (_POSTED_APPROVAL.search(statement) or _POSTED_REVIEW.search(statement)
            or _REVIEW_APPROVAL_STATE.search(statement))
    ]
    named_repositories = _EXPLICIT_REPOSITORY.findall(prose) + [
        repo for statement in publication_statements
        for repo in _NAMED_REPOSITORY.findall(statement)
    ]
    if any(repo.rstrip(".").lower() != repository for repo in named_repositories):
        return None
    if any(repo.lower() != repository or int(number) != pr_number
           for repo, number in _QUALIFIED_PR.findall(prose)):
        return None
    if any(repo.lower() != repository or int(number) != pr_number
           for repo, number in _PR_URL.findall(prose)):
        return None
    if any(not head.lower().startswith(commit.lower())
           for commit in _COMMIT_METADATA.findall(prose)):
        return None
    references: set[int] = set()
    for url in _REVIEW_URL.findall(prose):
        parsed = urlsplit(url)
        # A link to another PR/repository cannot correlate this publication.
        if parsed.path.lower() != f"/{repository}/pull/{pr_number}":
            return None
        references.add(int(parsed.fragment.removeprefix("pullrequestreview-")))
    references.update(int(match) for match in _REVIEW_ID.findall(prose))
    # Ambiguous receipts mentioning multiple reviews are not a match.
    if references:
        return "explicit_review" if references == {review_id} else None
    return "local_episode"


def _unique_local_episode(review, event, created, by_id, timeline, bot_login):
    """Allow an ID-free receipt only next to one unambiguous bot dismissal.

    A replacement COMMENTED review is expected. A competing decisive review or
    another dismissal destroys correlation; current DISMISSED state does not
    turn an earlier approval into a harmless comment.
    """
    dismissed_at = _timestamp(event.get("created_at"))
    if dismissed_at is None or not 0 < (created - dismissed_at).total_seconds() <= 60:
        return False
    window_start = created - timedelta(seconds=60)
    original_states: dict[int, set[str]] = {}
    nearby_dismissals = set()
    for item in timeline:
        dismissed = item.get("dismissed_review")
        if item.get("event") != "review_dismissed" or not isinstance(dismissed, Mapping):
            continue
        identifier = _positive_id(dismissed.get("review_id"))
        if identifier not in by_id and _login(item, "actor") != bot_login:
            continue
        original_states.setdefault(identifier, set()).add(str(dismissed.get("state", "")).upper())
        when = _timestamp(item.get("created_at"))
        if when is not None and window_start <= when <= created:
            # Duplicate pagination of the same canonical event is harmless.
            nearby_dismissals.add(item.get("id"))
    if nearby_dismissals != {event["id"]}:
        return False
    approved_at = _timestamp(review.get("submitted_at"))
    for other in by_id.values():
        if other["id"] == review["id"]:
            continue
        when = _timestamp(other.get("submitted_at"))
        if when is not None and approved_at <= when <= created:
            states = original_states.get(other["id"], {str(other.get("state", "")).upper()})
            if states != {"COMMENTED"}:
                return False
    return True


def parse_correlated_pattern(
    repository: str,
    pr_number: int,
    reviews: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]],
    issue_comments: Sequence[Mapping[str, Any]],
    *,
    bot_login: str = "all-hands-bot",
) -> list[dict[str, Any]]:
    """Find self-dismissed approvals followed by a correlated approval receipt.

    Returns all distinct correlated review/event/comment triples, newest first.
    Same-second timestamps are ambiguous at REST's one-second precision and are
    conservatively excluded. The review commit is returned, never inferred from
    the PR's possibly changed head.
    """
    repository = _repository(repository)
    if _positive_id(pr_number) is None:
        raise ValueError("Invalid pull request number")
    bot_login = bot_login.lower()
    by_id = {
        review["id"]: review
        for review in reviews
        if _positive_id(review.get("id")) is not None
        and _login(review, "user") == bot_login
    }
    matches: dict[tuple[int, int, int], dict[str, Any]] = {}
    for event in timeline:
        dismissed = event.get("dismissed_review")
        if (
            event.get("event") != "review_dismissed"
            or _login(event, "actor") != bot_login
            or not isinstance(dismissed, Mapping)
            or str(dismissed.get("state", "")).lower() != "approved"
            or not _corrects_to_comment(dismissed.get("dismissal_message"))
        ):
            continue
        review_id = _positive_id(dismissed.get("review_id"))
        review = by_id.get(review_id)
        event_id = _positive_id(event.get("id"))
        if not review or event_id is None:
            continue
        head = review.get("commit_id")
        approved_at = _timestamp(review.get("submitted_at"))
        dismissed_at = _timestamp(event.get("created_at"))
        if (
            not isinstance(head, str)
            or not _SHA.fullmatch(head)
            or approved_at is None
            or dismissed_at is None
            or approved_at > dismissed_at
        ):
            continue
        for comment in issue_comments:
            comment_id = _positive_id(comment.get("id"))
            created = _timestamp(comment.get("created_at"))
            updated = _timestamp(comment.get("updated_at"))
            if (
                comment_id is None
                or _login(comment, "user") != bot_login
                or created is None
                or updated != created
                or created <= dismissed_at
                or str(comment.get("html_url", "")).lower()
                != f"https://github.com/{repository}/pull/{pr_number}#issuecomment-{comment_id}"
            ):
                continue
            correlation = _claims_same_review(
                comment.get("body"), repository, pr_number, review_id, head
            )
            if correlation is None or (
                correlation == "local_episode"
                and not _unique_local_episode(review, event, created, by_id, timeline, bot_login)
            ):
                continue
            key = (review_id, event_id, comment_id)
            matches[key] = {
                "repository": repository,
                "pr_number": pr_number,
                "head_sha": head.lower(),
                "review_id": review_id,
                "review_url": f"https://github.com/{repository}/pull/{pr_number}#pullrequestreview-{review_id}",
                "review_submitted_at": review["submitted_at"],
                "dismissal_event_id": event_id,
                "dismissal_url": f"https://api.github.com/repos/{repository}/issues/events/{event_id}",
                "dismissed_at": event["created_at"],
                "dismissal_message": dismissed["dismissal_message"],
                "comment_id": comment_id,
                "comment_url": f"https://github.com/{repository}/pull/{pr_number}#issuecomment-{comment_id}",
                "comment_created_at": comment["created_at"],
                "comment_body": comment["body"],
            }
    return sorted(
        matches.values(),
        key=lambda item: (_timestamp(item["comment_created_at"]), item["comment_id"]),
        reverse=True,
    )


def eligibility(
    repository: str,
    *,
    author_permission: str | None = None,
    organization_membership: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate on canonical author permission or active owner-org membership.

    Pass GitHub's collaborator permission string and/or the author's membership
    response containing state and organization.login. author_association is not
    evidence of present membership. Missing/unavailable data fails closed.
    """
    repository = _repository(repository)
    if repository not in ALLOWED_REPOSITORIES:
        return {"eligible": False, "reason": "repository_not_allowed"}
    if isinstance(author_permission, str) and author_permission.lower() in {
        "write",
        "maintain",
        "admin",
    }:
        return {"eligible": True, "reason": "author_has_write_permission"}
    if isinstance(organization_membership, Mapping):
        organization = organization_membership.get("organization")
        if (
            organization_membership.get("state") == "active"
            and isinstance(organization, Mapping)
            and str(organization.get("login", "")).lower() == repository.split("/")[0]
        ):
            return {"eligible": True, "reason": "author_is_active_org_member"}
    return {"eligible": False, "reason": "author_eligibility_not_proven"}


def dedupe_key(repository: str, pr_number: int, head_sha: str) -> str:
    """One publication per repository/PR/reviewed head, across review attempts."""
    repository = _repository(repository)
    if repository not in ALLOWED_REPOSITORIES:
        raise ValueError("Repository is outside the allowed scope")
    if (
        _positive_id(pr_number) is None
        or not isinstance(head_sha, str)
        or not _SHA.fullmatch(head_sha)
    ):
        raise ValueError("A positive PR number and full commit SHA are required")
    identity = f"{repository}:{pr_number}:{head_sha.lower()}"
    return "review-audit:" + hashlib.sha256(identity.encode()).hexdigest()


def idempotency_marker(repository: str, pr_number: int, head_sha: str) -> str:
    return f"{_MARKER_PREFIX}{dedupe_key(repository, pr_number, head_sha)} -->"


def trigger_key(repository: str, pr_number: int, review_id: int) -> str:
    """Track a triggering review separately from per-head publication dedupe."""
    repository = _repository(repository)
    if repository not in ALLOWED_REPOSITORIES:
        raise ValueError("Repository is outside the allowed scope")
    if _positive_id(pr_number) is None or _positive_id(review_id) is None:
        raise ValueError("Positive PR and review IDs are required")
    identity = f"{repository}:{pr_number}:{review_id}"
    return "review-trigger:" + hashlib.sha256(identity.encode()).hexdigest()


def validated_publish_payload(
    repository: str,
    pr_number: int,
    head_sha: str,
    body: str,
    *,
    resolved_model: str,
    event: str,
    expected_head_sha: str,
) -> dict[str, str]:
    """Validate a formal GitHub review payload, exact model and stable marker.

    The controller must recheck eligibility/head/claim ownership and reconcile
    existing markers before POST. This helper makes no publication or model call.
    """
    if resolved_model != REQUIRED_MODEL:
        raise ValueError("The selected native Astra model was not resolved")
    if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
        raise ValueError("Invalid GitHub review event")
    marker = idempotency_marker(repository, pr_number, head_sha)
    if (
        not isinstance(expected_head_sha, str)
        or expected_head_sha.lower() != head_sha.lower()
    ):
        raise ValueError("Pull request head changed; publication must be reconsidered")
    if not isinstance(body, str) or not body.strip() or "\x00" in body:
        raise ValueError("A nonempty text report is required")
    if _MARKER_PREFIX in body:
        raise ValueError("Report text must not supply its own publication marker")
    result = (
        f"{body.strip()}\n\n"
        f"_Generated by OpenHands AI using {REQUIRED_MODEL}; "
        f"reviewed {_repository(repository)}#{pr_number} at `{head_sha.lower()}`._\n\n{marker}"
    )
    if len(result) > 60_000:
        raise ValueError(
            "Report exceeds the publication size budget; do not truncate silently"
        )
    return {"body": result, "event": event, "commit_id": head_sha.lower()}
