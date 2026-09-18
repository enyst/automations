import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "sources/jev-fast-audit"
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location("jev_runner", SOURCE / "main.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def pr(body="## Summary\nKeep me.\n\n## Auto-Test\nOther reviewer owns this.\n"):
    return {"number": 1, "title": "Example", "body": body, "state": "open",
            "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}


class GitHub:
    token = "synthetic-test-token"

    def __init__(self, record, guard_edit=False, after_edit=False):
        self.record = copy.deepcopy(record)
        self.guard_edit, self.after_edit = guard_edit, after_edit
        self.gets, self.writes = 0, []

    def request(self, path, method="GET", data=None):
        if method == "PATCH":
            self.writes.append(data)
            self.record["body"] = data["body"]
            if self.after_edit:
                self.record["body"] += "\nHuman changed it after publication.\n"
        else:
            self.gets += 1
            if self.guard_edit and self.gets == 2:
                self.record["body"] += "\nHuman arrived between reads.\n"
        return copy.deepcopy(self.record)


class RunnerTests(unittest.TestCase):
    def test_publish_preserves_other_review_and_replaces_once(self):
        original = pr()
        gh = GitHub(original)
        fp = r.fingerprint(original)
        self.assertEqual(r.publish(gh, "enyst/automations", 1, fp, "First scores."), "published")
        self.assertEqual(r.publish(gh, "enyst/automations", 1, fp, "Second scores."), "published")
        body = gh.record["body"]
        self.assertEqual(body.count("## Jev-Fast-Audit"), 1)
        self.assertNotIn("First scores.", body)
        self.assertEqual(r.strip_audit(body).rstrip(), original["body"].rstrip())

    def test_changed_head_or_description_does_not_publish_stale_result(self):
        for change in ("head", "body"):
            with self.subTest(change=change):
                original = pr()
                current = copy.deepcopy(original)
                if change == "head":
                    current["head"]["sha"] = "c" * 40
                else:
                    current["body"] += "Human update"
                gh = GitHub(current)
                self.assertEqual(r.publish(gh, "enyst/automations", 1, r.fingerprint(original), "Scores"), "changed_before_publish")
                self.assertFalse(gh.writes)

    def test_edit_between_reads_abandons_patch(self):
        original = pr()
        gh = GitHub(original, guard_edit=True)
        self.assertEqual(r.publish(gh, "enyst/automations", 1, r.fingerprint(original), "Scores"), "concurrent_edit")
        self.assertFalse(gh.writes)

    def test_external_write_after_patch_is_reported_without_overwriting_it(self):
        original = pr()
        gh = GitHub(original, after_edit=True)
        self.assertEqual(r.publish(gh, "enyst/automations", 1, r.fingerprint(original), "Scores"), "concurrent_edit_after_publish")
        self.assertEqual(len(gh.writes), 1)
        self.assertIn("Human changed it", gh.record["body"])

    def test_signed_receipt_is_ignored_as_input_and_skips_current_pr(self):
        original = pr()
        gh = GitHub(original)
        fp = r.fingerprint(original)
        signed = r.signature(gh, fp)
        gh.record["body"] = r.upsert_audit(original["body"], "Scores\n<!-- jev-input-signature " + signed + " -->")
        self.assertEqual(r.fingerprint(gh.record), fp)
        result = r.audit_pr(gh, None, "enyst/automations", 1, {"repositories": ["enyst/automations"]})
        self.assertEqual(result["status"], "current")
        self.assertFalse(gh.writes)
        self.assertNotEqual(r.signature(gh, "0" * 64), signed)

    def test_cron_wrapper_allows_manual_trial_and_event_selects_exact_pr(self):
        config = {"manual_target": {"repository": "enyst/automations", "number": 1}}
        self.assertEqual(r.select_targets(None, config, {"trigger": "cron", "trigger_payload": {"type": "cron"}}), [("enyst/automations", 1)])
        event = {"trigger": "event", "event": {"repository": {"full_name": "OpenHands/automation"}, "pull_request": {"number": 42}}}
        self.assertEqual(r.select_targets(None, config, event), [("OpenHands/automation", 42)])

    def test_foreign_repository_rejected_before_network(self):
        with self.assertRaisesRegex(r.AuditError, "repository_not_allowed"):
            r.audit_pr(None, None, "other/private", 1, {"repositories": ["enyst/automations"]})

    def test_wire_json_matches_context_budget_encoding(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return b"{}"
        class Opener:
            request = None
            def open(self, request, timeout):
                self.request = request
                return Response()
        api = r.API(r.TYPESAFE, "synthetic-test-token")
        api.opener = opener = Opener()
        payload = {"state": {"body": "Non-ASCII: π", "files": [1, 2]}, "questions": {}}
        api.request("/v1/systemone", "POST", payload)
        expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        self.assertEqual(opener.request.data, expected)

    def test_one_malformed_description_does_not_block_other_prs(self):
        broken = dict(pr("<!-- jev-fast-audit:start -->\n<!-- jev-input-signature " + "0" * 64 + " -->"), number=1,
                      updated_at="2026-09-19T13:00:00Z")
        healthy = dict(pr(), number=2, updated_at="2026-09-19T12:00:00Z")
        class Listing:
            token = "synthetic-test-token"
            def request(self, path):
                return [broken, healthy]
        config = {"repositories": ["enyst/automations"],
                  "watch_since": "2026-09-19T00:00:00Z", "max_prs_per_run": 6}
        targets = r.select_targets(Listing(), config)
        self.assertIn(("enyst/automations", 2), targets)

    def test_closed_between_reads_is_not_written(self):
        class ClosingGitHub(GitHub):
            def request(self, path, method="GET", data=None):
                if method == "GET" and self.gets == 1:
                    self.record["state"] = "closed"
                return super().request(path, method, data)
        original = pr()
        gh = ClosingGitHub(original)
        status = r.publish(gh, "enyst/automations", 1, r.fingerprint(original), "Scores")
        self.assertNotEqual(status, "published")
        self.assertFalse(gh.writes)

    def test_head_movement_at_patch_is_reported_as_stale(self):
        class AdvancingGitHub(GitHub):
            def request(self, path, method="GET", data=None):
                if method == "PATCH":
                    self.record["head"]["sha"] = "c" * 40
                return super().request(path, method, data)
        original = pr()
        gh = AdvancingGitHub(original)
        status = r.publish(gh, "enyst/automations", 1, r.fingerprint(original), "Scores")
        self.assertNotEqual(status, "published")
        self.assertEqual(len(gh.writes), 1)

    def test_transient_source_errors_propagate_instead_of_missing_context(self):
        class Client:
            def __init__(self, code):
                self.code = code
            def request(self, *args, **kwargs):
                raise r.AuditError(self.code)
        for code in ["network_unavailable", "http_403", "http_429", "http_500", "invalid_response"]:
            with self.subTest(code=code), self.assertRaisesRegex(r.AuditError, code):
                r.file_text(Client(code), "enyst/automations", "example.py", "a" * 40)
        for code in ["http_404", "response_too_large"]:
            with self.subTest(code=code):
                self.assertIsNone(r.file_text(Client(code), "enyst/automations", "example.py", "a" * 40))

    def test_transient_fetch_does_not_publish_or_cache_and_remains_eligible(self):
        class FailingSourceGitHub(GitHub):
            def request(self, path, method="GET", data=None, **kwargs):
                if "/files?" in path:
                    return [{"filename": "example.py", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new\n"}]
                if "/compare/" in path:
                    return {"merge_base_commit": {"sha": "d" * 40}}
                if "/contents/" in path:
                    raise r.AuditError("network_unavailable")
                if "/pulls?" in path:
                    return [dict(self.record, updated_at="2026-09-19T12:00:00Z")]
                return super().request(path, method, data)
        original = dict(pr(), changed_files=1)
        gh = FailingSourceGitHub(original)
        config = {"repositories": ["enyst/automations"], "watch_since": "2026-09-19T00:00:00Z"}
        with patch.object(r, "questions_for") as questions:
            with self.assertRaisesRegex(r.AuditError, "network_unavailable"):
                r.audit_pr(gh, None, "enyst/automations", 1, config)
            questions.assert_not_called()
        self.assertFalse(gh.writes)
        self.assertEqual(gh.record["body"], original["body"])
        self.assertIsNone(r.RECEIPT.search(gh.record["body"]))
        self.assertEqual(r.select_targets(gh, config), [("enyst/automations", 1)])

    def test_polling_turns_reach_all_four_repositories_and_rotate_past_failures(self):
        repositories = ["org/first", "org/second", "org/third", "org/fourth"]
        class Listing:
            token = "synthetic-test-token"
            def request(self, path):
                # None of these PRs ever obtains a success receipt. This models
                # persistent failures remaining eligible across successive runs.
                return [dict(pr(), number=n, updated_at="2026-09-19T12:00:00Z")
                        for n in range(8, 0, -1)]
        config = {"repositories": repositories, "watch_since": "2026-09-19T00:00:00Z", "max_prs_per_run": 6}
        all_seen = set()
        first_targets = []
        for slot in range(8):
            with self.subTest(slot=slot), patch.object(r.time, "time", return_value=slot * 300):
                targets = r.select_targets(Listing(), config)
                self.assertEqual(len(targets), 6)
                self.assertEqual(len(set(targets)), 6)
                self.assertEqual({repo for repo, _ in targets[:4]}, set(repositories))
                first_targets.append(targets[0])
                all_seen.update(targets)
        self.assertEqual({repo for repo, _ in first_targets[:4]}, set(repositories))
        self.assertEqual(all_seen, {(repo, n) for repo in repositories for n in range(1, 9)})
        self.assertNotEqual(first_targets[0], first_targets[1])

    def test_polling_uses_remaining_capacity_without_duplicates_when_queues_empty(self):
        class Listing:
            token = "synthetic-test-token"
            def request(self, path):
                count = 2 if "/org/active/" in path else 0
                return [dict(pr(), number=n, updated_at="2026-09-19T12:00:00Z") for n in range(1, count + 1)]
        config = {"repositories": ["org/empty", "org/active"], "watch_since": "2026-09-19T00:00:00Z", "max_prs_per_run": 6}
        with patch.object(r.time, "time", return_value=300):
            targets = r.select_targets(Listing(), config)
        self.assertEqual(set(targets), {("org/active", 1), ("org/active", 2)})
        self.assertEqual(len(targets), 2)

    def test_incomplete_file_listing_aborts(self):
        class Client:
            def request(self, path):
                if "/files?" in path:
                    return []
                raise AssertionError("Unexpected request")
        original = dict(pr(), changed_files=1)
        with self.assertRaisesRegex(r.AuditError, "incomplete_file_listing"):
            r.gather(Client(), "enyst/automations", original)


if __name__ == "__main__":
    unittest.main()
