"""GitHub evidence adapter with injected authenticated transport.

``request(method, path, body)`` returns decoded JSON and raises the caller's HTTP
exception with a ``status`` attribute. Credentials, retries and logging belong
to that transport. This module neither discovers credentials nor opens sockets.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

ALLOWED_REPOSITORIES = frozenset(
    {
        "openhands/openhands",
        "openhands/software-agent-sdk",
        "openhands/automation",
        "openhands/extensions",
    }
)
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_CLOSING_ISSUES_QUERY = """
query ClosingIssues($owner:String!,$name:String!,$number:Int!,$cursor:String) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      closingIssuesReferences(first:100,after:$cursor) {
        nodes { id number title body url repository { nameWithOwner } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


class GitHubDataError(ValueError):
    """Input/evidence is invalid or incomplete; callers must not publish."""


class GitHubPaginationError(GitHubDataError):
    """Evidence exceeded the explicit page limit, without silent truncation."""


def _repo(value: str) -> str:
    if not isinstance(value, str) or value.lower() not in ALLOWED_REPOSITORIES:
        raise GitHubDataError("Repository is outside the four-repository allowlist")
    return value.lower()


def _number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitHubDataError("A positive integer identifier is required")
    return value


def _login(value: str) -> str:
    if not isinstance(value, str) or not _LOGIN.fullmatch(value) or "--" in value:
        raise GitHubDataError("A valid GitHub user login is required")
    return value


def _sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise GitHubDataError("A full 40-character commit SHA is required")
    return value.lower()


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubDataError("Expected a GitHub JSON object")
    return value


def _related_comment(
    comment: Mapping[str, Any], repository: str, number: int, review_id: int
) -> bool:
    body = comment.get("body")
    if not isinstance(body, str):
        return False
    # These are evidence associations only, not trigger decisions. The stricter
    # core detector decides whether a receipt actually asserts approval.
    url = f"https://github.com/{repository}/pull/{number}#pullrequestreview-{review_id}"
    return bool(
        re.search(re.escape(url) + r"(?!\d)", body, re.I)
        or re.search(rf"\breview\s+id\s*[:=#]\s*[`*]*{review_id}\b", body, re.I)
    )


class GitHub:
    def __init__(
        self, request: Callable[[str, str, Any], Any], *, max_pages: int = 100
    ):
        if not callable(request):
            raise GitHubDataError("An authenticated request callable is required")
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= 100
        ):
            raise GitHubDataError("max_pages must be from 1 to 100")
        self._request = request
        self.max_pages = max_pages
        self._review_prs: dict[tuple[str, int], int] = {}

    def _pages(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        index_by_id: dict[int, int] = {}
        for page in range(1, self.max_pages + 1):
            response = self._request(
                "GET", path + "?" + urlencode({"per_page": 100, "page": page}), None
            )
            if not isinstance(response, list) or any(
                not isinstance(item, dict) for item in response
            ):
                raise GitHubDataError("Expected a complete GitHub array page")
            if len(response) > 100:
                raise GitHubDataError(
                    "GitHub returned more items than the requested page size"
                )
            for item in response:
                item = copy.deepcopy(item)
                identifier = item.get("id")
                if isinstance(identifier, int) and not isinstance(identifier, bool):
                    # Concurrent GitHub updates can repeat an object at a page
                    # boundary. Keep one object, preferring the later read.
                    if identifier in index_by_id:
                        result[index_by_id[identifier]] = item
                        continue
                    index_by_id[identifier] = len(result)
                result.append(item)
            if len(response) < 100:
                return result
        raise GitHubPaginationError(
            "GitHub evidence reached the page cap; no partial result returned"
        )

    def pr(self, repository: str, number: int) -> dict[str, Any]:
        repository, number = _repo(repository), _number(number)
        result = _object(
            self._request("GET", f"/repos/{repository}/pulls/{number}", None)
        )
        if result.get("number") != number:
            raise GitHubDataError("GitHub returned a different pull request")
        return copy.deepcopy(result)

    def _review_bundle(
        self, repository: str, number: int
    ) -> dict[str, list[dict[str, Any]]]:
        root = f"/repos/{repository}"
        reviews = self._pages(f"{root}/pulls/{number}/reviews")
        inline_comments = self._pages(f"{root}/pulls/{number}/comments")
        timeline = self._pages(f"{root}/issues/{number}/timeline")
        comments = self._pages(f"{root}/issues/{number}/comments")
        for review in reviews:
            review_id = _number(review.get("id"))
            self._review_prs[(repository, review_id)] = number
            review["inline_comments"] = [
                copy.deepcopy(comment)
                for comment in inline_comments
                if comment.get("pull_request_review_id") == review_id
            ]
            review["dismissal_events"] = [
                copy.deepcopy(event)
                for event in timeline
                if event.get("event") == "review_dismissed"
                and isinstance(event.get("dismissed_review"), Mapping)
                and event["dismissed_review"].get("review_id") == review_id
            ]
            review["related_comments"] = [
                copy.deepcopy(comment)
                for comment in comments
                if _related_comment(comment, repository, number, review_id)
            ]
        return {
            "reviews": reviews,
            "timeline": timeline,
            "comments": comments,
            "inline_comments": inline_comments,
        }

    def reviews(self, repository: str, number: int) -> list[dict[str, Any]]:
        """All reviews enriched with inline, dismissal and receipt evidence."""
        repository, number = _repo(repository), _number(number)
        return self._review_bundle(repository, number)["reviews"]

    def snapshot(self, repository: str, number: int) -> dict[str, Any]:
        """Complete PR/review evidence; no API snapshot-isolation guarantee.

        The publisher must refetch and compare its frozen inputs before a write.
        Ordinary comments and all timeline events are retained at top level.
        """
        repository, number = _repo(repository), _number(number)
        return {
            "pr": self.pr(repository, number),
            **self._review_bundle(repository, number),
        }

    def eligible(self, repository: str, author: str) -> dict[str, Any]:
        repository, author = _repo(repository), _login(author)
        permission: str | None = None
        membership: dict[str, Any] | None = None
        try:
            response = _object(
                self._request(
                    "GET",
                    f"/repos/{repository}/collaborators/{author}/permission",
                    None,
                )
            )
            user = response.get("user")
            if (
                isinstance(user, Mapping)
                and isinstance(user.get("login"), str)
                and user["login"].lower() != author.lower()
            ):
                raise GitHubDataError(
                    "Permission evidence belongs to a different author"
                )
            permission = (
                response.get("permission")
                if isinstance(response.get("permission"), str)
                else None
            )
        except Exception as exc:
            if getattr(exc, "status", None) not in {403, 404}:
                raise
        if permission in {"write", "maintain", "admin"}:
            return {
                "eligible": True,
                "reason": "author_has_write_permission",
                "author_permission": permission,
                "organization_membership": None,
            }
        try:
            response = _object(
                self._request("GET", f"/orgs/OpenHands/memberships/{author}", None)
            )
            user = response.get("user")
            if (
                isinstance(user, Mapping)
                and isinstance(user.get("login"), str)
                and user["login"].lower() != author.lower()
            ):
                raise GitHubDataError(
                    "Membership evidence belongs to a different author"
                )
            organization = response.get("organization")
            if (
                not isinstance(organization, Mapping)
                or str(organization.get("login", "")).lower() != "openhands"
            ):
                raise GitHubDataError(
                    "Membership evidence is not for the owner organization"
                )
            membership = copy.deepcopy(response)
        except Exception as exc:
            if getattr(exc, "status", None) not in {403, 404}:
                raise
        active = membership is not None and membership.get("state") == "active"
        return {
            "eligible": active,
            "reason": "author_is_active_org_member"
            if active
            else "author_eligibility_not_proven",
            "author_permission": permission,
            "organization_membership": membership,
        }

    def closing_issues(self, repository: str, number: int) -> list[dict[str, Any]]:
        """All official closing-linked issues, including manually linked ones."""
        repository, number = _repo(repository), _number(number)
        owner, name = repository.split("/")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        result: list[dict[str, Any]] = []
        seen_issues: set[str] = set()
        for _ in range(self.max_pages):
            response = _object(
                self._request(
                    "POST",
                    "/graphql",
                    {
                        "query": _CLOSING_ISSUES_QUERY,
                        "variables": {
                            "owner": owner,
                            "name": name,
                            "number": number,
                            "cursor": cursor,
                        },
                    },
                )
            )
            if response.get("errors"):
                raise GitHubDataError(
                    "GitHub GraphQL reported errors; closing-issue evidence is incomplete"
                )
            try:
                connection = response["data"]["repository"]["pullRequest"][
                    "closingIssuesReferences"
                ]
                nodes, info = connection["nodes"], connection["pageInfo"]
            except (KeyError, TypeError) as exc:
                raise GitHubDataError(
                    "GitHub omitted the closing-issue connection"
                ) from exc
            if (
                not isinstance(nodes, list)
                or not isinstance(info, dict)
                or not isinstance(info.get("hasNextPage"), bool)
            ):
                raise GitHubDataError("Malformed closing-issue pagination evidence")
            for issue in nodes:
                if not isinstance(issue, dict) or any(
                    not isinstance(issue.get(field), str)
                    for field in ("id", "title", "body", "url")
                ):
                    raise GitHubDataError(
                        "Closing issue is missing complete text or identity"
                    )
                if issue["id"] not in seen_issues:
                    seen_issues.add(issue["id"])
                    result.append(copy.deepcopy(issue))
            if not info["hasNextPage"]:
                return result
            cursor = info.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise GitHubPaginationError("Closing-issue cursor did not advance")
            seen_cursors.add(cursor)
        raise GitHubPaginationError(
            "Closing-issue evidence reached the page cap; no partial result returned"
        )

    def merge_base(self, repository: str, base: str, head: str) -> str:
        repository, base, head = _repo(repository), _sha(base), _sha(head)
        response = _object(
            self._request("GET", f"/repos/{repository}/compare/{base}...{head}", None)
        )
        commit = response.get("merge_base_commit")
        if not isinstance(commit, Mapping):
            raise GitHubDataError("GitHub comparison omitted its merge base")
        return _sha(commit.get("sha"))

    def identity(self) -> dict[str, Any]:
        result = _object(self._request("GET", "/user", None))
        _login(result.get("login"))
        _number(result.get("id"))
        return copy.deepcopy(result)

    def post_review(
        self, repository: str, number: int, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The controller's sole deliberate GitHub write; no retry here.

        On an ambiguous transport failure, the controller must reconcile the
        stable publication marker, rather than retrying this call blindly.
        """
        repository, number = _repo(repository), _number(number)
        if not isinstance(payload, Mapping) or set(payload) != {
            "body",
            "event",
            "commit_id",
        }:
            raise GitHubDataError(
                "Expected an explicit body, review event and commit ID"
            )
        _sha(payload["commit_id"])
        if payload["event"] not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            raise GitHubDataError("Invalid GitHub review event")
        if (
            not isinstance(payload["body"], str)
            or not payload["body"].strip()
            or "\x00" in payload["body"]
        ):
            raise GitHubDataError("Review body must be nonempty text")
        result = _object(
            self._request(
                "POST", f"/repos/{repository}/pulls/{number}/reviews", dict(payload)
            )
        )
        identifier = _number(result.get("id"))
        self._review_prs[(repository, identifier)] = number
        return copy.deepcopy(result)

    def get_review(
        self, repository: str, review_id: int, pr_number: int | None = None
    ) -> dict[str, Any]:
        """Fetch one review; REST requires its PR number, explicit or cached."""
        repository, review_id = _repo(repository), _number(review_id)
        if pr_number is None:
            pr_number = self._review_prs.get((repository, review_id))
        if pr_number is None:
            raise GitHubDataError("Review lookup needs pr_number before its first read")
        pr_number = _number(pr_number)
        result = _object(
            self._request(
                "GET",
                f"/repos/{repository}/pulls/{pr_number}/reviews/{review_id}",
                None,
            )
        )
        if result.get("id") != review_id:
            raise GitHubDataError("GitHub returned a different review")
        self._review_prs[(repository, review_id)] = pr_number
        return copy.deepcopy(result)
