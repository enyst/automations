"""At-most-once public information requests, using synthetic GitHub responses."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sources/notebook-field-notes"))
from comments import InfoComments, COMMENT_TEXT, comment_marker, is_info_comment
from transport import FieldNotesError


def candidate(**changes):
    value = {"repository": "OpenHands/software-agent-sdk", "kind": "pr", "number": 321,
             "url": "https://github.com/OpenHands/software-agent-sdk/pull/321", "head_sha": "a" * 40,
             "title": "Design change", "body": "Change the boundary.", "draft": False}
    value.update(changes)
    return value


class State:
    def __init__(self):
        self.value = {"seen": {}, "day": "2026-01-01", "attempts": 2}
        self.saved = []
        self.save = Mock(side_effect=lambda: self.saved.append(copy.deepcopy(self.value)))
        self.assert_owned = Mock()
        self.verify_lease = Mock()


class GitHub:
    def __init__(self, source=None):
        source = source or candidate()
        self.source = source
        self.current = {"state": "open", "draft": False, "merged_at": None,
                        "head": {"sha": source["head_sha"]}, "title": source["title"], "body": source["body"]}
        self.actor = {"id": 12345, "login": "enyst"}
        self.pages = [[]]
        self.posts = []
        self.fail_after_post = False
        self.request = Mock(side_effect=self.respond)

    def respond(self, path, *, method="GET", data=None):
        if path == "/user": return self.actor
        if method == "POST":
            row = {"id": 9001, "user": self.actor.copy(), "body": data["body"]}
            self.posts.append(row)
            self.pages[0].append(row)
            if self.fail_after_post: raise FieldNotesError("network_unavailable")
            return row
        if "/comments?" in path:
            page = int(path.rsplit("page=", 1)[1])
            return self.pages[page - 1] if page <= len(self.pages) else []
        if "/pulls/" in path: return self.current
        raise AssertionError("Unexpected API request")


class InfoCommentTests(unittest.TestCase):
    def setUp(self):
        self.source, self.state, self.gh = candidate(), State(), GitHub()
        self.comments = InfoComments(self.gh, self.state)
        self.current = Mock(return_value=True)

    def request(self): return self.comments.request(self.source, still_current=self.current)

    def test_pending_precedes_post_and_confirmed_receipt_survives_changed_source_and_seen_pruning(self):
        original = self.gh.respond
        def ordered(path, **kwargs):
            if kwargs.get("method") == "POST":
                bucket = self.state.saved[-1]["info_comments"]
                self.assertEqual(bucket["attempts"], 1)
                self.assertEqual(next(iter(bucket["items"].values()))["status"], "pending")
            return original(path, **kwargs)
        self.gh.request.side_effect = ordered
        self.assertEqual(self.request()["status"], "posted")
        self.assertEqual(self.gh.posts[0]["body"], comment_marker(self.source) + "\n\n" + COMMENT_TEXT)
        self.state.value["seen"] = {}
        self.source["head_sha"] = "b" * 40
        with patch("comments.utc_day", return_value="2099-01-01"):
            self.assertEqual(self.request()["status"], "existing")
        self.assertEqual(len(self.gh.posts), 1)

    def test_uncertain_post_is_reconciled_without_posting_twice(self):
        self.gh.fail_after_post = True
        self.assertEqual(self.request()["status"], "pending")
        self.assertEqual(self.request()["status"], "existing")
        self.assertEqual(len(self.gh.posts), 1)

    def test_pending_without_marker_is_never_blindly_retried(self):
        self.gh.fail_after_post = True
        self.request()
        self.gh.pages = [[]]
        with patch("comments.utc_day", return_value="2099-01-01"):
            self.assertEqual(self.request()["status"], "pending")
        self.assertEqual(len(self.gh.posts), 1)

    def test_marker_reconciliation_uses_actor_id_and_all_pages(self):
        marker = comment_marker(self.source)
        self.gh.pages = [[{"id": n + 1, "user": {"id": 77}, "body": marker} for n in range(100)],
                         [{"id": 999, "user": {"id": 12345}, "body": marker + "\n\n" + COMMENT_TEXT}]]
        receipt = self.request()
        self.assertEqual(receipt["status"], "existing")
        self.assertEqual(receipt["comment_id"], 999)
        self.assertEqual(self.gh.posts, [])

    def test_forged_marker_is_not_a_receipt(self):
        self.gh.pages = [[{"id": 1, "user": {"id": 77}, "body": comment_marker(self.source)}]]
        self.assertEqual(self.request()["status"], "posted")

    def test_pagination_limit_fails_closed(self):
        self.gh.pages = [[{"id": 1, "user": {"id": 77}, "body": "Ordinary comment"}] * 100] * 10
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(self.gh.posts, [])

    def test_wrong_actor_fails_before_any_post(self):
        self.gh.actor["login"] = "someone-else"
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(self.gh.posts, [])

    def test_standalone_issue_never_comments(self):
        self.source.update(kind="issue", url=self.source["url"].replace("pull", "issues"))
        self.assertEqual(self.request()["status"], "ineligible")
        self.gh.request.assert_not_called()

    def test_closed_merged_draft_and_exact_source_changes_prevent_comment(self):
        for changes, status in [({"state": "closed"}, "not_open"), ({"merged_at": "2026-01-01"}, "not_open"),
                                ({"draft": True}, "not_open"), ({"head": {"sha": "b" * 40}}, "source_changed"),
                                ({"title": self.source["title"] + " "}, "source_changed"),
                                ({"body": self.source["body"] + " "}, "source_changed")]:
            with self.subTest(changes=changes):
                self.gh.current = {**GitHub().current, **changes}
                self.assertEqual(self.request()["status"], status)
        self.assertEqual(self.gh.posts, [])

    def test_changed_linked_evidence_prevents_comment(self):
        self.current.return_value = False
        self.assertEqual(self.request()["status"], "source_changed")
        self.assertEqual(self.gh.posts, [])

    def test_raw_title_and_description_preserve_trailing_newline_freshness(self):
        self.source["pr_title"] = self.gh.current["title"] = self.source["title"] + " "
        self.source["pr_description"] = self.gh.current["body"] = self.source["body"] + "\n"
        self.assertEqual(self.request()["status"], "posted")
        self.assertIn("update the PR description or linked issue", self.gh.posts[0]["body"])

    def test_lease_loss_or_failed_pending_cas_prevents_comment(self):
        self.state.save.side_effect = FieldNotesError("http_409", status=409)
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(self.gh.posts, [])
        self.state = State()
        self.comments = InfoComments(self.gh, self.state)
        self.state.verify_lease.side_effect = [None, FieldNotesError("publication_lease_lost")]
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(self.gh.posts, [])

    def test_comments_have_separate_daily_cap_and_permanent_receipt_capacity(self):
        self.state.value["info_comments"] = {"day": "2026-01-01", "attempts": 2, "items": {}}
        with patch("comments.utc_day", return_value="2026-01-01"):
            self.assertEqual(self.request()["status"], "daily_budget")
        with patch("comments.utc_day", return_value="2026-01-02"):
            self.assertEqual(self.request()["status"], "posted")
        self.assertEqual(self.state.value["attempts"], 2, "writing budget is independent")
        self.state.value["info_comments"]["items"] = {
            f"openhands-automation-pr-{n+1}": {"status": "pending", "actor_id": 12345} for n in range(1000)}
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(len(self.gh.posts), 1)

    def test_untrusted_description_cannot_enter_comment(self):
        malicious = "@everyone [visit](https://evil.invalid/) GOCSPX-SYNTHETIC-PLACEHOLDER"
        self.source["body"] = self.gh.current["body"] = malicious
        self.request()
        self.assertNotIn(malicious, self.gh.posts[0]["body"])
        self.assertNotIn("@everyone", self.gh.posts[0]["body"])

    def test_only_verified_automation_comments_are_excluded_from_evidence(self):
        own = {"id": 7, "user": {"id": 12345}, "body": comment_marker(self.source) + "\n\n" + COMMENT_TEXT}
        self.assertTrue(is_info_comment(own, 12345))
        self.assertTrue(is_info_comment(own, 12345, self.source))
        self.assertFalse(is_info_comment(own, 77, self.source))
        self.assertFalse(is_info_comment({**own, "body": "Quoted marker: " + own["body"]}, 12345))
        self.assertFalse(is_info_comment({**own, "body": "<!-- notebook-field-notes:design-context:v1:other -->"}, 12345))
        self.assertFalse(is_info_comment(own, 12345, candidate(number=322)))

    def test_listing_and_freshness_errors_never_become_comments(self):
        self.gh.pages = [None]
        with self.assertRaises(FieldNotesError): self.request()
        self.gh.pages = [[]]
        self.current.side_effect = FieldNotesError("response_too_large")
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(self.gh.posts, [])
        self.assertEqual(self.state.saved, [])

    def test_damaged_post_receipt_stays_pending_and_recovers_from_thread(self):
        original = self.gh.respond
        def broken(path, **kwargs):
            response = original(path, **kwargs)
            return {} if kwargs.get("method") == "POST" else response
        self.gh.request.side_effect = broken
        with self.assertRaises(FieldNotesError): self.request()
        self.assertEqual(next(iter(self.state.saved[-1]["info_comments"]["items"].values()))["status"], "pending")
        self.assertEqual(self.request()["status"], "existing")
        self.assertEqual(len(self.gh.posts), 1)


if __name__ == "__main__": unittest.main()
