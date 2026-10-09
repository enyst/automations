#!/usr/bin/env python3
"""Issue duplicate-check automation — detection half.

Triggered by GitHub ``issues.opened`` webhook. Runs an OpenHands Cloud
conversation (deep-flash) with the original duplicate-check prompt, then
deterministically posts the result comment (as smolpaws) and applies the
``duplicate-candidate`` label (as enyst) when the LLM flagged an auto-close
candidate.

The LLM only decides; posting/labeling is scripted so the comment marker is
byte-exact and the irreversible label step cannot drift.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
GITHUB_API_BASE_URL = os.environ.get("GITHUB_API_BASE_URL", "https://api.github.com")
REPOSITORY_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
DUPLICATE_CANDIDATE_LABEL = "duplicate-candidate"
MARKER_RE = re.compile(
    r"<!-- openhands-duplicate-check canonical=(?P<canonical>\d+) "
    r"auto-close=(?P<auto_close>true|false) -->"
)
AI_FOOTER = "_This comment was created by an AI assistant (OpenHands) on behalf of the repository maintainer._"

# Secret names stored in the OpenHands Cloud secret registry.
SMOLPAWS_SECRET = "SMOLPAWS_TOKEN"  # posts the duplicate-check comment
ENYST_SECRET = "REMOTE_GH"  # enyst PAT: labels (triage) and closes (maintain)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Secret fetch — raw value from the Cloud API (sandbox-scoped settings).
# Secrets are NOT served by the sandbox-local agent server; they live on the
# Cloud API at /api/v1/sandboxes/{SANDBOX_ID}/settings/secrets/{name}.
# --------------------------------------------------------------------------
def fetch_secret(name: str) -> str:
    cloud_url = os.environ.get(
        "OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev"
    ).rstrip("/")
    sandbox_id = os.environ.get("SANDBOX_ID", "")
    session_key = os.environ.get("SESSION_API_KEY") or os.environ.get(
        "OH_SESSION_API_KEYS_0", ""
    )
    if not sandbox_id or not session_key:
        raise RuntimeError("SANDBOX_ID or SESSION_API_KEY not set; cannot fetch secrets")
    req = urllib.request.Request(
        f"{cloud_url}/api/v1/sandboxes/{sandbox_id}/settings/secrets/{name}",
        headers={"X-Session-API-Key": session_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        value = resp.read().decode("utf-8").strip()
    if not value:
        raise RuntimeError(f"Secret {name} is empty")
    return value


# --------------------------------------------------------------------------
# GitHub REST helpers
# --------------------------------------------------------------------------
def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "openhands-issue-duplicate-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gh_request(
    token: str, path: str, *, method: str = "GET", body: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = github_headers(token)
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GITHUB_API_BASE_URL}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} failed with HTTP {exc.code}: {error_body}"
        ) from exc
    if not payload:
        return None
    return json.loads(payload)


def fetch_issue(token: str, repository: str, issue_number: int) -> dict[str, Any]:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(f"Invalid repository format: {repository}")
    return gh_request(token, f"/repos/{repository}/issues/{issue_number}")


def list_issue_comments(
    token: str, repository: str, issue_number: int
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    page = 1
    while page <= 50:
        payload = gh_request(
            token,
            f"/repos/{repository}/issues/{issue_number}/comments?per_page=100&page={page}",
        )
        if not isinstance(payload, list) or not payload:
            return comments
        comments.extend(payload)
        if len(payload) < 100:
            return comments
        page += 1
    raise RuntimeError(f"Too many comments on #{issue_number}")


def ensure_label(token: str, repository: str, issue_number: int) -> None:
    """Create the duplicate-candidate label if missing and add it to the issue."""
    try:
        gh_request(token, f"/repos/{repository}/labels/{DUPLICATE_CANDIDATE_LABEL}")
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        gh_request(
            token,
            f"/repos/{repository}/labels",
            method="POST",
            body={
                "name": DUPLICATE_CANDIDATE_LABEL,
                "color": "FBCA04",
                "description": "Potential duplicate awaiting auto-close or maintainer review",
            },
        )
    gh_request(
        token,
        f"/repos/{repository}/issues/{issue_number}/labels",
        method="POST",
        body={"labels": [DUPLICATE_CANDIDATE_LABEL]},
    )


# --------------------------------------------------------------------------
# Old prompt (verbatim from OpenHands/OpenHands scripts/issue_duplicate_check_openhands.py
# at commit e3d9abfd0)
# --------------------------------------------------------------------------
def build_prompt(repository: str, issue: dict[str, Any]) -> str:
    issue_number = issue["number"]
    issue_title = issue.get("title", "")
    issue_body = issue.get("body") or ""
    issue_url = issue.get("html_url", "")
    issue_title_json = json.dumps(issue_title, ensure_ascii=False)
    issue_body_json = json.dumps(issue_body, ensure_ascii=False)

    return "\n".join(
        [
            "You are investigating whether a GitHub issue should be redirected "
            "to an existing issue because it is either:",
            "- an exact or near-exact duplicate, or",
            "- so overlapping in scope that discussion or fix planning would "
            "likely be better kept in one canonical issue.",
            "",
            "Be conservative about auto-close decisions, but do investigate "
            "seriously before deciding.",
            "",
            f"Repository: {repository}",
            f"New issue number: #{issue_number}",
            f"New issue URL: {issue_url}",
            f"New issue title (JSON-escaped string): {issue_title_json}",
            f"New issue body (JSON-escaped string): {issue_body_json}",
            "",
            "Task:",
            "1. Understand the core problem, user-facing outcome, likely root "
            "cause, and requested fix or behavior.",
            "2. Investigate this repository's open issues and issues closed "
            "in the last 90 days for exact duplicates, near-duplicates, or "
            "strong scope overlap.",
            "3. Use multiple search approaches with diverse keywords and "
            "phrasings rather than a single literal search.",
            "4. Ignore pull requests.",
            "5. Distinguish carefully between:",
            "   - duplicate: essentially the same report, request, or root cause",
            "   - overlapping-scope: not identical, but likely to fragment "
            "discussion or produce competing fixes",
            "   - related-but-distinct: similar area, but should stay separate",
            "   - no-match: no strong candidate worth redirecting to",
            "6. Inspect the strongest 1-3 candidates carefully. If needed, "
            "inspect comments on the strongest candidates to disambiguate "
            "false positives.",
            "7. Do not post comments, do not modify files, and do not change "
            "repository state.",
            "8. Useful API shapes include:",
            f"   - GET https://api.github.com/repos/{repository}/issues?state=open&per_page=100",
            "   - GET https://api.github.com/repos/"
            f"{repository}/issues?state=closed&since=<ISO-8601 timestamp>&per_page=100",
            "   - GET https://api.github.com/search/issues?q=<query>",
            f"   - GET https://api.github.com/repos/{repository}/issues/<number>/comments",
            "9. Return exactly one JSON object and nothing else. Do not wrap "
            "it in markdown fences.",
            "",
            "Return schema:",
            "{",
            f'  "issue_number": {issue_number},',
            '  "should_comment": true or false,',
            '  "is_duplicate": true or false,',
            '  "auto_close_candidate": true or false,',
            '  "classification": "duplicate" | "overlapping-scope" | '
            '"related-but-distinct" | "no-match",',
            '  "confidence": "high" | "medium" | "low",',
            '  "summary": "short explanation",',
            '  "canonical_issue_number": 123 or null,',
            '  "candidate_issues": [',
            "    {",
            '      "number": 123,',
            f'      "url": "https://github.com/{repository}/issues/123",',
            '      "title": "issue title",',
            '      "state": "open or closed",',
            '      "closed_at": "ISO timestamp or null",',
            '      "similarity_reason": "why it looks similar"',
            "    }",
            "  ]",
            "}",
            "",
            "Rules:",
            "- `should_comment` should be true only when redirecting the "
            "author would likely help.",
            "- `is_duplicate` should be true only for exact or near-exact duplicates.",
            "- `auto_close_candidate` should be true only when:",
            "  - classification is `duplicate`",
            "  - confidence is `high`",
            "  - one canonical issue clearly stands out",
            "  - a maintainer would likely be comfortable closing this issue "
            "after a waiting period",
            "- For `overlapping-scope`, `auto_close_candidate` must be false.",
            "- `candidate_issues` must contain at most 3 issues, sorted best-first.",
            "- If no strong match exists, return `should_comment: false`, "
            '`classification: "no-match"`, `canonical_issue_number: null`, '
            "and an empty candidate list.",
            "- Be especially careful not to collapse broad meta, tracking, "
            "feedback, or umbrella issues with specific bug reports unless "
            "the new issue clearly belongs in that exact thread.",
        ]
    )


# --------------------------------------------------------------------------
# Result parsing / normalization (vendored from the old script)
# --------------------------------------------------------------------------
def parse_agent_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for start, character in enumerate(cleaned):
            if character != "{":
                continue
            try:
                candidate, end = decoder.raw_decode(cleaned[start:])
            except json.JSONDecodeError:
                continue
            trailing = cleaned[start + end :].strip()
            if trailing not in {"", "```"}:
                continue
            if isinstance(candidate, dict):
                return candidate
    raise ValueError("No valid JSON object found in the agent response")


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized["should_comment"] = as_bool(normalized.get("should_comment"))
    normalized["is_duplicate"] = as_bool(normalized.get("is_duplicate"))
    normalized["auto_close_candidate"] = as_bool(normalized.get("auto_close_candidate"))

    classification = str(normalized.get("classification") or "no-match").strip().lower()
    if classification not in {
        "duplicate",
        "overlapping-scope",
        "related-but-distinct",
        "no-match",
    }:
        classification = "no-match"
    normalized["classification"] = classification

    confidence = str(normalized.get("confidence") or "low").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    normalized["confidence"] = confidence

    try:
        canonical_issue_number = normalized.get("canonical_issue_number")
        if canonical_issue_number in {None, ""}:
            normalized["canonical_issue_number"] = None
        else:
            normalized["canonical_issue_number"] = int(str(canonical_issue_number))
    except (TypeError, ValueError):
        normalized["canonical_issue_number"] = None

    candidate_issues = normalized.get("candidate_issues")
    if not isinstance(candidate_issues, list):
        candidate_issues = []
    normalized["candidate_issues"] = candidate_issues[:3]

    if classification not in {"duplicate", "overlapping-scope"}:
        normalized["should_comment"] = False
    if classification != "duplicate":
        normalized["is_duplicate"] = False
        normalized["auto_close_candidate"] = False
    if (
        classification in {"duplicate", "overlapping-scope"}
        and normalized["candidate_issues"]
        and confidence in {"high", "medium"}
    ):
        normalized["should_comment"] = True
    if normalized["auto_close_candidate"] and confidence != "high":
        normalized["auto_close_candidate"] = False
    if normalized["auto_close_candidate"] and not normalized["candidate_issues"]:
        normalized["auto_close_candidate"] = False
    if (
        normalized["auto_close_candidate"]
        and normalized["canonical_issue_number"] is None
    ):
        first_candidate = (
            normalized["candidate_issues"][0] if normalized["candidate_issues"] else {}
        )
        candidate_number = first_candidate.get("number")
        try:
            if candidate_number is None:
                raise ValueError("candidate number is missing")
            normalized["canonical_issue_number"] = int(str(candidate_number))
        except (TypeError, ValueError, AttributeError):
            normalized["auto_close_candidate"] = False

    normalized["summary"] = str(normalized.get("summary") or "").strip()
    return normalized


# --------------------------------------------------------------------------
# Comment builder (old template, both modes)
# --------------------------------------------------------------------------
def build_comment_body(result: dict[str, Any], close_after_days: int = 3) -> str:
    candidates = result["candidate_issues"]
    classification = result["classification"]
    auto_close = result["auto_close_candidate"]
    canonical = result["canonical_issue_number"]
    summary = result["summary"]

    marker = (
        f"<!-- openhands-duplicate-check canonical={canonical} "
        f"auto-close={'true' if auto_close else 'false'} -->"
    )
    header = (
        "Found 1 possible duplicate issue:"
        if len(candidates) == 1
        else f"Found {len(candidates)} possible duplicate issues:"
    )
    candidate_lines = [
        f"{i + 1}. [#{c.get('number')}]({c.get('url')}) — {c.get('title')}"
        for i, c in enumerate(candidates)
    ]

    sections: list[str] = []
    if summary:
        sections.extend([summary, ""])
    sections.extend([header, "", *candidate_lines])

    if classification == "overlapping-scope":
        sections.extend(
            [
                "",
                "These may not be exact duplicates, but the scope appears to "
                "overlap enough that keeping discussion in one place may be "
                "more useful.",
            ]
        )

    if auto_close:
        sections.extend(
            [
                "",
                f"This issue will be automatically closed as a duplicate in {close_after_days} days.",
                "",
                "- If your issue is a duplicate, please close it and 👍 the existing issue instead",
                "- To prevent auto-closure, add a comment or 👎 this comment",
            ]
        )

    sections.extend(["", marker, AI_FOOTER])
    return "\n".join(sections).strip()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    # 1. Parse event payload (webhook context).
    event_context = json.loads(os.environ.get("AUTOMATION_EVENT_PAYLOAD", "{}"))
    event = event_context.get("event") if isinstance(event_context, dict) else {}
    if not isinstance(event, dict):
        log("ERROR: no webhook event in AUTOMATION_EVENT_PAYLOAD; nothing to do")
        return 0

    repository = (event.get("repository") or {}).get("full_name")
    issue = event.get("issue") or {}
    issue_number = issue.get("number")
    action = event.get("action")
    if action != "opened" or not repository or not isinstance(issue_number, int):
        log(f"INFO: ignoring event action={action!r} repo={repository!r}")
        return 0

    # 2. Fetch secrets (raw values) from the sandbox settings API.
    smolpaws_token = fetch_secret(SMOLPAWS_SECRET)
    enyst_token = fetch_secret(ENYST_SECRET)

    # 3. Fetch the issue fresh (the webhook Issue model has no body).
    issue = fetch_issue(enyst_token, repository, issue_number)
    if issue.get("pull_request"):
        log(f"INFO: #{issue_number} is a pull request; skipping")
        return 0

    # 4. Idempotency: skip if a duplicate-check comment already exists.
    for comment in list_issue_comments(enyst_token, repository, issue_number):
        if MARKER_RE.search(comment.get("body") or ""):
            log(f"INFO: duplicate-check comment already present on #{issue_number}; skipping")
            return 0

    # 5. Build the old prompt.
    prompt = build_prompt(repository, issue)

    # 6. Run the LLM conversation (deep-flash).
    api_key = os.environ.get("OPENHANDS_API_KEY", "")
    api_url = os.environ.get("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev").rstrip("/")
    model_profile = os.environ.get("AUTOMATION_MODEL") or "deep-flash"
    if not api_key:
        log("ERROR: OPENHANDS_API_KEY not set")
        return 1

    from openhands.sdk import Conversation, RemoteConversation
    from openhands.sdk.conversation.response_utils import get_agent_final_response
    from openhands.tools.preset import get_default_agent
    from openhands.workspace import OpenHandsCloudWorkspace

    result = None
    with OpenHandsCloudWorkspace(
        local_agent_server_mode=True,
        cloud_api_url=api_url,
        cloud_api_key=api_key,
    ) as workspace:
        llm = workspace.get_llm(profile_name=model_profile)
        agent = get_default_agent(llm=llm, cli_mode=True)
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            delete_on_close=False,
        )
        assert isinstance(conversation, RemoteConversation)
        # Expose the enyst token to the agent's bash so its GitHub search
        # calls are authenticated (avoids unauthenticated rate limits).
        conversation.update_secrets({"GITHUB_TOKEN": enyst_token})
        conversation.send_message(prompt)
        conversation.run()
        final_text = get_agent_final_response(conversation.events)
        try:
            conversation.close()
        except Exception:
            pass
        if not final_text:
            log("ERROR: no final response from agent")
            return 1
        result = normalize_result(parse_agent_json(final_text))

    # 7. Deterministic post.
    if not result.get("should_comment"):
        log(f"INFO: no duplicate found for #{issue_number} ({result.get('classification')})")
        return 0

    body = build_comment_body(result)
    gh_request(
        smolpaws_token,
        f"/repos/{repository}/issues/{issue_number}/comments",
        method="POST",
        body={"body": body},
    )
    log(f"INFO: posted duplicate-check comment on #{issue_number} as smolpaws")

    if result.get("auto_close_candidate"):
        ensure_label(enyst_token, repository, issue_number)
        log(f"INFO: labeled #{issue_number} as {DUPLICATE_CANDIDATE_LABEL}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"error: {exc}")
        raise
