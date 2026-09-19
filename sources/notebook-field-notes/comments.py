"""Fixed, at-most-once requests for missing design context on public open PRs.

The caller decides needs_info from complete descriptions. This boundary never
uses model-written text and never retries an uncertain comment POST.
"""
from __future__ import annotations

import datetime as dt
import re

from core import canonical_repository
from transport import FieldNotesError

COMMENT_TEXT = (
    "Notebook Field notes automation: I could not assess the intended code-design changes "
    "from this PR description and its linked issue(s). Could you update the PR description or linked issue "
    "with a short explanation of the problem, affected components, and intended approach or behavior? A few sentences "
    "are enough. This check uses descriptions only; it does not review the diff."
)
MARKER_PREFIX = "<!-- notebook-field-notes:design-context:v1:"
_KEY = re.compile(r"openhands-(?:openhands|software-agent-sdk|automation)-pr-[1-9][0-9]*")


def utc_day():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _subject(candidate):
    repository = canonical_repository(candidate.get("repository"))
    number = candidate.get("number")
    if candidate.get("kind") != "pr" or type(number) is not int or number < 1:
        raise FieldNotesError("invalid_info_comment_subject")
    key = repository.lower().replace("/", "-") + "-pr-" + str(number)
    return repository, number, key


def comment_marker(candidate):
    return MARKER_PREFIX + _subject(candidate)[2] + " -->"


def is_info_comment(comment, actor_id, candidate=None):
    """Use verified numeric actor identity before excluding our comments as evidence."""
    if not isinstance(comment, dict) or not isinstance(comment.get("user"), dict):
        return False
    body = comment.get("body")
    if type(actor_id) is not int or comment["user"].get("id") != actor_id or not isinstance(body, str):
        return False
    first = body.split("\n", 1)[0]
    if candidate is not None:
        return first == comment_marker(candidate)
    return first.startswith(MARKER_PREFIX) and first.endswith(" -->") and bool(
        _KEY.fullmatch(first[len(MARKER_PREFIX):-4]))


class InfoComments:
    def __init__(self, gh, state, *, maximum=2):
        if type(maximum) is not int or not 1 <= maximum <= 5:
            raise FieldNotesError("invalid_info_comment_budget")
        self.gh, self.state, self.maximum = gh, state, maximum

    def _bucket(self):
        bucket = self.state.value.setdefault("info_comments", {"day": "", "attempts": 0, "items": {}})
        if (not isinstance(bucket, dict) or not isinstance(bucket.get("day"), str)
                or type(bucket.get("attempts")) is not int or bucket["attempts"] < 0
                or not isinstance(bucket.get("items"), dict) or len(bucket["items"]) > 1000):
            raise FieldNotesError("invalid_info_comment_state")
        for key, entry in bucket["items"].items():
            if (not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(entry, dict)
                    or entry.get("status") not in {"pending", "confirmed"}
                    or type(entry.get("actor_id")) is not int or entry["actor_id"] < 1
                    or (entry["status"] == "confirmed" and
                        (type(entry.get("comment_id")) is not int or entry["comment_id"] < 1))):
                raise FieldNotesError("invalid_info_comment_state")
        return bucket

    def _actor(self):
        actor = self.gh.request("/user")
        if (not isinstance(actor, dict) or actor.get("login") != "enyst"
                or type(actor.get("id")) is not int or actor["id"] < 1):
            raise FieldNotesError("unexpected_info_comment_actor")
        return actor["id"]

    def _existing(self, repository, number, actor, candidate):
        for page in range(1, 11):
            rows = self.gh.request(f"/repos/{repository}/issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(rows, list) or len(rows) > 100:
                raise FieldNotesError("invalid_info_comment_listing")
            for row in rows:
                if (not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] < 1
                        or not isinstance(row.get("body"), str) or not isinstance(row.get("user"), dict)
                        or type(row["user"].get("id")) is not int or row["user"]["id"] < 1):
                    raise FieldNotesError("invalid_info_comment_listing")
                if is_info_comment(row, actor, candidate):
                    return row["id"]
            if len(rows) < 100:
                return None
        raise FieldNotesError("info_comment_page_limit")

    def request(self, candidate, *, still_current):
        if candidate.get("kind") == "issue":
            return {"status": "ineligible"}
        repository, number, key = _subject(candidate)
        if (candidate.get("url") != f"https://github.com/{repository}/pull/{number}"
                or not isinstance(candidate.get("head_sha"), str)
                or not re.fullmatch(r"[a-f0-9]{40}", candidate["head_sha"])
                or not isinstance(candidate.get("title"), str) or not isinstance(candidate.get("body"), str)
                or not isinstance(candidate.get("pr_title", candidate["title"]), str)
                or not isinstance(candidate.get("pr_description", candidate["body"]), str)):
            raise FieldNotesError("invalid_info_comment_subject")
        self.state.assert_owned()
        bucket = self._bucket()
        old = bucket["items"].get(key)
        if old and old["status"] == "confirmed":
            return {"status": "existing", "subject": key, "comment_id": old["comment_id"]}
        if old is None and len(bucket["items"]) >= 1000:
            raise FieldNotesError("info_comment_receipt_limit")
        today = utc_day()
        if old is None and bucket["day"] == today and bucket["attempts"] >= self.maximum:
            return {"status": "daily_budget", "subject": key}
        actor = self._actor()
        if old and old["actor_id"] != actor:
            raise FieldNotesError("info_comment_actor_changed")
        existing = self._existing(repository, number, actor, candidate)
        if existing is not None:
            bucket["items"][key] = {"status": "confirmed", "actor_id": actor, "comment_id": existing}
            self.state.verify_lease()
            self.state.save()
            return {"status": "existing", "subject": key, "comment_id": existing}
        if old:
            # A timeout or crash may have happened after GitHub accepted the POST.
            # Even if no comment is found, do not risk another automatic attempt.
            return {"status": "pending", "subject": key}
        current = self.gh.request(f"/repos/{repository}/pulls/{number}")
        if not isinstance(current, dict):
            raise FieldNotesError("invalid_info_comment_pull")
        if (current.get("state") != "open" or current.get("draft") is not False
                or current.get("merged_at") is not None or current.get("merged") is True):
            return {"status": "not_open", "subject": key}
        if (not isinstance(current.get("head"), dict) or current["head"].get("sha") != candidate["head_sha"]
                or current.get("title") != candidate.get("pr_title", candidate["title"])
                or (current.get("body") or "") != candidate.get("pr_description", candidate["body"])
                or not still_current()):
            return {"status": "source_changed", "subject": key}
        self.state.verify_lease()
        if bucket["day"] != today:
            bucket["day"], bucket["attempts"] = today, 0
        bucket["attempts"] += 1
        bucket["items"][key] = {"status": "pending", "actor_id": actor}
        self.state.save()  # Git CAS must succeed before the irreversible action.
        self.state.verify_lease()  # Check ownership using the newly persisted SHA.
        body = comment_marker(candidate) + "\n\n" + COMMENT_TEXT
        try:
            created = self.gh.request(f"/repos/{repository}/issues/{number}/comments", method="POST", data={"body": body})
        except FieldNotesError:
            return {"status": "pending", "subject": key}
        if (not isinstance(created, dict) or type(created.get("id")) is not int or created["id"] < 1
                or not is_info_comment(created, actor, candidate)):
            raise FieldNotesError("invalid_info_comment_receipt")
        bucket["items"][key] = {"status": "confirmed", "actor_id": actor, "comment_id": created["id"]}
        self.state.save()
        return {"status": "posted", "subject": key, "comment_id": created["id"]}
