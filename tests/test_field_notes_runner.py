"""Bounded automation orchestration, using synthetic evidence and no services."""
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).parents[1] / "sources/notebook-field-notes"
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location("field_notes_runner", SOURCE / "main.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
import test_core as fixtures
import descriptions as collection


CONFIG = {"repositories": list(r.REPOSITORIES), "watch_since": "2026-09-18T00:00:00Z",
          "max_candidates_per_run": 12, "max_notes_per_day": 2, "min_quiet_minutes": 20, "lookback_days": 7}
REPO = "OpenHands/software-agent-sdk"
KEY = "openhands-software-agent-sdk-pr-321"


class Harness:
    def __init__(self, *, old=None, attempts=0, writer_error=False, current=True, published=False):
        self.events = []
        self.candidate = fixtures.candidate(description_complete=True)
        self.item = {"title": self.candidate["title"], "body": self.candidate["body"],
                     "updated_at": self.candidate["updated_at"], "comments": 0, "state": "open", "draft": False}
        self.value = {"seen": {KEY: copy.deepcopy(old)} if old else {},
                      "day": dt.datetime.now(dt.timezone.utc).date().isoformat(), "attempts": attempts}
        self.gh = Mock()
        self.gh.request.return_value = {"login": "enyst"}
        self.jev = Mock()
        self.jev.request.return_value = fixtures.response()
        self.info_comments = Mock()
        self.info_comments.request.return_value = {"status": "posted", "subject": KEY, "comment_id": 456}
        self.comments_factory = Mock(return_value=self.info_comments)
        self.workspace = Mock()
        self.workspace.get_llm.return_value = types.SimpleNamespace(model="example/model")
        # The real SDK __exit__ sends a callback; explicit cleanup does not.
        self.workspace.__exit__ = Mock(side_effect=lambda *args: r.callback("FAILED" if args[0] else "COMPLETED"))
        self.workspace.cleanup.side_effect = lambda: self.events.append("cleanup")
        owner = self

        class State:
            def __init__(self, *args, **kwargs): self.value = owner.value
            def verify(self): owner.events.append("verify_state")
            def load(self): owner.events.append("load_state")
            def ensure_branch(self): owner.events.append("ensure_branch")
            def claim(self): owner.events.append("claim"); return True
            def release(self): owner.events.append("release")
            def verify_lease(self): owner.events.append("verify_lease")
            def reserve_attempt(self, maximum):
                owner.events.append("persist_budget")
                today = dt.datetime.now(dt.timezone.utc).date().isoformat()
                if self.value["day"] != today:
                    self.value["day"], self.value["attempts"] = today, 0
                if self.value["attempts"] >= maximum:
                    return False
                self.value["attempts"] += 1
                return True
            def remember(self, key, fingerprint, status, **metadata):
                owner.events.append("remember:" + status)
                self.value["seen"][key] = {"fingerprint": fingerprint, "status": status,
                                           "at": dt.datetime.now(dt.timezone.utc).timestamp(), **metadata}

        class Publisher:
            def __init__(self, *args): pass
            def verify(self): owner.events.append("verify_publication")
            def head(self): return fixtures.SHA
            def manifest(self, head): return {"schema_version": 1, "notes": [{"id": "field-note-" + KEY}] if published else []}
            def publish(self, note, document):
                owner.events.append("publish")
                return {"status": "published", "id": note["id"], "commit": fixtures.SHA}

        def write(*args, **kwargs):
            self.events.append("writer")
            if writer_error: raise RuntimeError("synthetic writer failure")
            return {"generated": fixtures.generated(), "writer_model": "example/model",
                    "examined_commits": [{"repository": REPO, "sha": fixtures.SHA}]}

        self.writer = Mock(side_effect=write)
        self.patches = [patch.object(r, "Publisher", Publisher), patch.object(r, "GitState", State),
            patch.object(r, "source_repositories", return_value=[{"repository": REPO, "sha": fixtures.SHA}]),
            patch.object(r, "discovery", return_value=[(REPO, "pr", 321, self.item)]),
            patch.object(r, "gather", return_value=self.candidate),
            patch.object(r, "still_current", return_value=current),
            patch.dict(sys.modules, {"writer": types.SimpleNamespace(write_note=self.writer)}),
            patch.object(r, "check_writer", side_effect=lambda llm: self.events.append("check_writer")),
            patch.object(r, "disable_sdk_tracing", side_effect=lambda: self.events.append("disable_tracing")),
            patch.object(r, "InfoComments", self.comments_factory, create=True)]

    def __enter__(self):
        self.mocks = [item.start() for item in self.patches]
        return self

    def __exit__(self, *args):
        for item in reversed(self.patches): item.stop()

    def run(self, publish=True, **configuration):
        return r.run({**CONFIG, **configuration}, self.gh, self.jev, publish=publish,
                     workspace_factory=lambda: self.workspace)

    def cached(self, status, *, age=60, policy=3, fingerprint=None, needs_info=False):
        self.value["seen"][KEY] = {
            "fingerprint": fingerprint or r.fingerprint(self.candidate), "status": status,
            "at": dt.datetime.now(dt.timezone.utc).timestamp() - age,
            "source_updated_at": self.item["updated_at"],
            "listing_fingerprint": r.listing_fingerprint(self.item), "policy_version": policy, "needs_info": needs_info,
        }


class Orchestration(unittest.TestCase):
    def test_preview_never_claims_reserves_writes_or_opens_workspace(self):
        with Harness() as h:
            h.run(publish=False)
        self.assertEqual(h.jev.request.call_count, 1)
        self.assertFalse(any(event in {"ensure_branch", "claim", "persist_budget", "writer", "publish", "release"}
                             or event.startswith("remember:") for event in h.events))
        h.workspace.get_llm.assert_not_called()
        h.info_comments.request.assert_not_called()

    def test_daily_note_budget_does_not_suppress_explanatory_comments(self):
        with Harness(attempts=2) as h:
            h.jev.request.return_value = fixtures.response(design_context=0.1)
            report = h.run()
            h.jev.request.assert_called_once()
            h.info_comments.request.assert_called_once()
            self.assertEqual(report["decisions"][0]["decision"], "write")
            self.assertEqual(h.value["seen"][KEY]["status"], "ready")
            self.assertEqual(h.value["attempts"], 2)
        h.writer.assert_not_called()
        h.workspace.get_llm.assert_not_called()
        self.assertIn("claim", h.events)

    def test_budget_is_durable_before_writer_and_final_state_follows_publication(self):
        with Harness() as h, patch.dict(r.os.environ, {"AUTOMATION_MODEL": "unrelated-evaluation-profile"}):
            h.run()
        self.assertLess(h.events.index("persist_budget"), h.events.index("writer"))
        self.assertLess(h.events.index("disable_tracing"), h.events.index("check_writer"))
        self.assertLess(h.events.index("check_writer"), h.events.index("persist_budget"))
        self.assertLess(h.events.index("remember:writing"), h.events.index("writer"))
        self.assertLess(h.events.index("verify_lease"), h.events.index("publish"))
        self.assertLess(h.events.index("publish"), h.events.index("remember:published"))
        self.assertEqual(h.value["seen"][KEY]["status"], "published")
        self.assertEqual(h.events.count("cleanup"), 1)
        h.workspace.__exit__.assert_not_called()
        overrides = h.workspace.get_llm.call_args.kwargs
        self.assertIsNone(overrides["profile_name"])
        self.assertFalse(overrides["log_completions"])
        self.assertEqual(overrides["max_output_tokens"], 7000)

    def test_failed_writer_consumes_attempt_but_never_marks_complete(self):
        with Harness(writer_error=True) as h:
            with self.assertRaises(RuntimeError): h.run()
        self.assertEqual(h.value["attempts"], 1)
        self.assertEqual(h.value["seen"][KEY]["status"], "writing")
        self.assertNotIn("publish", h.events)
        self.assertIn("release", h.events)
        self.assertEqual(h.events.count("cleanup"), 1)
        h.workspace.__exit__.assert_not_called()

    def test_stale_head_discards_research_without_publication_or_completion(self):
        with Harness(current=False) as h:
            with self.assertRaisesRegex(r.FieldNotesError, "source_changed_before_publication"): h.run()
        self.assertNotIn("publish", h.events)
        self.assertEqual(h.value["seen"][KEY]["status"], "writing")

    def test_recent_cached_decisions_cool_before_refetching_or_commenting(self):
        for status in ["skip", "published"]:
            with self.subTest(status=status), Harness(published=status == "published") as h:
                h.cached(status, needs_info=True)
                h.run()
                h.mocks[4].assert_not_called()
                h.jev.request.assert_not_called()
                h.info_comments.request.assert_not_called()

    def test_cached_skipped_or_published_note_retries_comment_opportunity_without_jev(self):
        for status in ["skip", "published"]:
            for receipt in ["posted", "pending", "daily_budget"]:
                with self.subTest(status=status, receipt=receipt), Harness(published=status == "published") as h:
                    h.cached(status, age=86401, needs_info=True)
                    h.info_comments.request.return_value = {"status": receipt, "subject": KEY}
                    h.run()
                    h.mocks[4].assert_called_once()
                    h.jev.request.assert_not_called()
                    h.info_comments.request.assert_called_once()
                    h.writer.assert_not_called()
                    self.assertEqual(h.value["seen"][KEY]["status"], status)
                    self.assertEqual(h.value["seen"][KEY]["needs_info"], receipt != "posted")
                    self.assertEqual(h.value["seen"][KEY]["policy_version"], 3)
                    h.run()
                    self.assertEqual(h.info_comments.request.call_count, 1)

    def test_classify_only_never_posts_for_cached_comment_opportunity(self):
        for status in ["skip", "published"]:
            with self.subTest(status=status), Harness(published=status == "published") as h:
                h.cached(status, age=86401, needs_info=True)
                previous = copy.deepcopy(h.value)
                h.run(publish=False)
                h.mocks[4].assert_called_once()
                h.jev.request.assert_not_called()
                h.info_comments.request.assert_not_called()
                h.writer.assert_not_called()
                self.assertEqual(h.value, previous)

    def test_changed_linked_issue_reclassifies_after_cooldown_without_pr_edit(self):
        with Harness() as h:
            h.candidate["linked_issues"] = [{
                "repository": REPO, "number": 12, "url": f"https://github.com/{REPO}/issues/12",
                "title": "Preserve memory observations", "body": "The original issue description.",
                "updated_at": "2026-09-19T12:00:00Z",
            }]
            h.cached("skip", age=86401)
            h.candidate["linked_issues"][0]["body"] = "New design detail: keep the tool result across memory compaction."
            h.run(publish=False)
            h.mocks[4].assert_called_once()
            h.jev.request.assert_called_once()
            self.assertIn("New design detail", h.jev.request.call_args.kwargs["data"]["state"]["linked_issues"][0]["body"])

    def test_old_policy_cache_never_suppresses_independent_reclassification(self):
        for policy in [1, 2]:
            for status in ["skip", "needs_info", "defer"]:
                with self.subTest(policy=policy, status=status), Harness() as h:
                    h.cached(status, policy=policy)
                    h.run(publish=False)
                    h.mocks[4].assert_called_once()
                    h.jev.request.assert_called_once()

    def test_changed_listing_body_does_not_use_cheap_skip(self):
        with Harness() as h:
            h.cached("skip", fingerprint="f" * 64)
            h.value["seen"][KEY]["listing_fingerprint"] = "0" * 64
            h.run(publish=False)
            h.mocks[4].assert_called_once()
        h.jev.request.assert_called_once()

    def test_classifier_error_is_not_persisted_as_skip(self):
        with Harness() as h:
            h.jev.request.side_effect = r.FieldNotesError("http_429")
            with self.assertRaises(r.FieldNotesError): h.run()
        self.assertFalse(h.value["seen"])
        self.assertNotIn("persist_budget", h.events)

    def test_five_question_classifier_selects_independently_of_context_probability(self):
        for probability in [0, 0.30, 0.31, 0.69, 0.70, 1]:
            with self.subTest(probability=probability), Harness() as h:
                h.jev.request.return_value = fixtures.response(design_context=probability)
                report = h.run(publish=False)
                self.assertEqual(report["decisions"][0]["decision"], "write")
                self.assertEqual(report["decisions"][0]["needs_info"], probability <= .30)
                self.assertEqual(set(h.jev.request.call_args.kwargs["data"]["questions"]),
                                 {"design", "agent_behavior", "memory", "substance", "design_context"})
                h.info_comments.request.assert_not_called()
                h.writer.assert_not_called()
                self.assertFalse(h.value["seen"])

    def test_legacy_five_question_response_cannot_comment_or_write(self):
        with Harness() as h:
            del h.jev.request.return_value["answers"]["design_context"]
            with self.assertRaisesRegex(r.ValidationError, "classifier_answer_keys_mismatch"):
                h.run()
            h.info_comments.request.assert_not_called()
            h.writer.assert_not_called()
            self.assertFalse(h.value["seen"])

    def test_classification_contains_descriptions_without_diffs_or_source_files(self):
        with Harness() as h:
            h.candidate["files"][0]["patch"] = "NEVER_SEND_PATCH_TO_CLASSIFIER"
            h.run(publish=False)
        state = h.jev.request.call_args.kwargs["data"]["state"]
        self.assertEqual(state["subject"]["body"], h.candidate["body"])
        self.assertNotIn("NEVER_SEND_PATCH_TO_CLASSIFIER", json.dumps(state))
        self.assertNotIn("files", state)

    def test_incomplete_or_truncated_description_defers_without_comment_or_writer(self):
        cases = [{"description_complete": False}, {"description_truncated": True},
                 {"body": "Design information. " * 4000}]
        for changes in cases:
            with self.subTest(changes=list(changes)), Harness() as h:
                h.candidate.update(changes)
                h.jev.request.return_value = fixtures.response(design_context=0.01)
                report = h.run()
                self.assertEqual(report["decisions"][0]["decision"], "defer")
                self.assertEqual(h.value["seen"][KEY]["status"], "defer")
                h.info_comments.request.assert_not_called()
                h.writer.assert_not_called()
                self.assertNotIn("persist_budget", h.events)

    def test_selected_low_context_topic_gets_both_research_and_bounded_comment(self):
        for maximum in [None, 4]:
            with self.subTest(maximum=maximum), Harness() as h:
                h.jev.request.return_value = fixtures.response(design_context=0.1)
                options = {} if maximum is None else {"max_info_comments_per_day": maximum}
                report = h.run(**options)
                self.assertEqual(report["decisions"][0]["decision"], "write")
                self.assertTrue(report["decisions"][0]["needs_info"])
                self.assertEqual(h.value["seen"][KEY]["status"], "published")
                self.assertFalse(h.value["seen"][KEY]["needs_info"])
                self.assertEqual(h.value["seen"][KEY]["policy_version"], 3)
                self.assertEqual(h.comments_factory.call_args.kwargs["maximum"], maximum or 2)
                h.info_comments.request.assert_called_once()
                self.assertEqual(h.info_comments.request.call_args.args[0], h.candidate)
                guard = h.info_comments.request.call_args.kwargs["still_current"]
                self.assertTrue(guard())
                h.mocks[5].assert_called_with(h.gh, h.candidate)
                h.writer.assert_called_once()
                self.assertEqual(len(report["published"]), 1)

    def test_selected_middle_context_topic_is_researched_without_comment(self):
        with Harness() as h:
            h.jev.request.return_value = fixtures.response(design_context=0.5)
            report = h.run()
            self.assertEqual(report["decisions"][0]["decision"], "write")
            h.info_comments.request.assert_not_called()
            h.writer.assert_called_once()

    def test_skipped_low_context_topic_only_requests_explanation(self):
        with Harness() as h:
            h.jev.request.return_value = fixtures.response(memory=.1, substance=.1, design_context=.1)
            report = h.run()
            self.assertEqual(report["decisions"][0]["decision"], "skip")
            self.assertTrue(report["decisions"][0]["needs_info"])
            self.assertEqual(h.value["seen"][KEY]["status"], "skip")
            h.info_comments.request.assert_called_once()
            h.writer.assert_not_called()

    def test_pending_comment_does_not_block_research_or_lose_comment_opportunity(self):
        with Harness() as h:
            h.jev.request.return_value = fixtures.response(design_context=.1)
            h.info_comments.request.return_value = {"status": "pending", "subject": KEY}
            h.run()
            h.writer.assert_called_once()
            self.assertEqual(h.value["seen"][KEY]["status"], "published")
            self.assertTrue(h.value["seen"][KEY]["needs_info"])

    def test_comment_integrity_failures_still_prevent_research(self):
        with Harness() as h:
            h.jev.request.return_value = fixtures.response(design_context=.1)
            h.info_comments.request.side_effect = r.FieldNotesError("publication_lease_lost")
            with self.assertRaisesRegex(r.FieldNotesError, "publication_lease_lost"):
                h.run()
            h.writer.assert_not_called()
            self.assertNotIn("persist_budget", h.events)

    def test_reserved_note_keeps_comment_opportunity_without_research(self):
        with Harness() as h:
            h.cached("reserved", age=86401, needs_info=True)
            h.run()
            h.info_comments.request.assert_called_once()
            h.jev.request.assert_not_called()
            h.writer.assert_not_called()
            self.assertEqual(h.value["seen"][KEY]["status"], "reserved")

    def test_published_note_without_comment_opportunity_never_reclassifies(self):
        with Harness(published=True) as h:
            h.run()
            h.mocks[4].assert_not_called()
            h.jev.request.assert_not_called()
            h.writer.assert_not_called()

    def test_changed_published_description_rechecks_comment_without_another_note(self):
        with Harness(published=True) as h:
            h.cached("published", age=86401, needs_info=True)
            h.candidate["body"] += " Additional explanation clarifies the purpose."
            report = h.run()
            h.jev.request.assert_called_once()
            h.info_comments.request.assert_not_called()
            h.writer.assert_not_called()
            self.assertFalse(report["published"])
            self.assertFalse(h.value["seen"][KEY]["needs_info"])

    def test_published_comment_opportunity_survives_unavailable_or_incomplete_input(self):
        for missing in ["collection", "coverage"]:
            with self.subTest(missing=missing), Harness(published=True) as h:
                h.cached("published", age=86401, needs_info=True)
                if missing == "collection":
                    h.mocks[4].side_effect = r.FieldNotesError("http_404", status=404)
                else:
                    h.candidate["description_complete"] = False
                report = h.run()
                self.assertEqual(report["decisions"][0]["decision"], "defer")
                self.assertFalse(report["decisions"][0]["needs_info"])
                self.assertTrue(h.value["seen"][KEY]["needs_info"])
                h.info_comments.request.assert_not_called()
                h.writer.assert_not_called()

    def test_one_note_per_run_does_not_stop_later_comment_opportunities(self):
        with Harness() as h:
            following = fixtures.candidate(number=322, url=f"https://github.com/{REPO}/pull/322")
            h.mocks[3].return_value = [(REPO, "pr", 321, h.item), (REPO, "pr", 322, h.item)]
            h.mocks[4].side_effect = [h.candidate, following]
            h.jev.request.return_value = fixtures.response(design_context=.1)
            report = h.run()
            self.assertEqual(h.jev.request.call_count, 2)
            self.assertEqual(h.info_comments.request.call_count, 2)
            h.writer.assert_called_once()
            self.assertEqual(len(report["published"]), 1)

    def test_per_run_delayed_note_uses_free_next_hour_slot_without_another_jev_call(self):
        with Harness() as h:
            following = fixtures.candidate(number=322, url=f"https://github.com/{REPO}/pull/322")
            h.mocks[3].return_value = [(REPO, "pr", 321, h.item), (REPO, "pr", 322, h.item)]
            h.mocks[4].side_effect = lambda gh, repo, kind, number, pins: h.candidate if number == 321 else following
            h.run()
            second_key = KEY.replace("321", "322")
            self.assertEqual(h.value["seen"][second_key]["status"], "ready")
            for entry in h.value["seen"].values(): entry["at"] -= 3601
            h.run()
            self.assertEqual(h.writer.call_count, 2)
            self.assertEqual(h.jev.request.call_count, 2)
            self.assertEqual(h.value["attempts"], 2)
            self.assertEqual(h.value["seen"][second_key]["status"], "published")
            self.assertNotIn("classifier", h.value["seen"][second_key])

    def test_daily_delayed_note_waits_cheaply_then_reuses_judgment_when_budget_resets(self):
        with Harness(attempts=2) as h:
            h.run()
            self.assertEqual(h.value["seen"][KEY]["status"], "ready")
            h.value["seen"][KEY]["at"] -= 3601
            h.run()
            self.assertEqual(h.mocks[4].call_count, 1)
            self.assertEqual(h.jev.request.call_count, 1)
            h.writer.assert_not_called()
            h.value["day"] = "2000-01-01"
            h.run()
            self.assertEqual(h.mocks[4].call_count, 2)
            self.assertEqual(h.jev.request.call_count, 1)
            h.writer.assert_called_once()

    def test_ready_judgment_is_not_reused_for_changed_material_or_policy(self):
        for change in ["description", "policy"]:
            with self.subTest(change=change), Harness(attempts=2) as h:
                h.run()
                if change == "description": h.candidate["body"] += " Updated tradeoff."
                else: h.value["seen"][KEY]["policy_version"] = 2
                h.value["day"] = "2000-01-01"
                h.run()
                self.assertEqual(h.jev.request.call_count, 2)
                h.writer.assert_called_once()

    def test_invalid_cached_ready_classifier_fails_before_research(self):
        with Harness() as h:
            h.cached("ready")
            h.value["seen"][KEY]["classifier"] = {"model": "wrong-model", "probabilities": {}}
            with self.assertRaisesRegex(r.FieldNotesError, "invalid_cached_classifier"): h.run()
            h.jev.request.assert_not_called()
            h.writer.assert_not_called()

    def test_settled_comment_collection_failure_does_not_resurrect_research(self):
        for status in ["reserved", "existing"]:
            with self.subTest(status=status), Harness() as h:
                h.cached(status, age=86401, needs_info=True)
                h.mocks[4].side_effect = r.FieldNotesError("http_404", status=404)
                h.run()
                self.assertEqual(h.value["seen"][KEY]["status"], status)
                h.value["seen"][KEY]["at"] -= 86401
                h.mocks[4].side_effect = None
                h.run()
                self.assertEqual(h.value["seen"][KEY]["status"], status)
                h.writer.assert_not_called()
                self.assertNotIn("publish", h.events)

    def test_comment_timestamp_change_is_harmless_but_material_change_blocks_publication(self):
        for material in [False, True]:
            with self.subTest(material=material), Harness() as h:
                h.jev.request.return_value = fixtures.response(design_context=.1)
                fresh = copy.deepcopy(h.candidate)
                def posted(candidate, *, still_current):
                    self.assertTrue(still_current())
                    fresh["updated_at"] = "2026-09-19T16:01:00Z"
                    if material: fresh["body"] += " Newly changed intended behavior."
                    return {"status": "posted", "subject": KEY, "comment_id": 456}
                h.info_comments.request.side_effect = posted
                with patch.object(r, "still_current", collection.still_current), \
                     patch.object(collection, "gather", return_value=fresh):
                    if material:
                        with self.assertRaisesRegex(r.FieldNotesError, "source_changed_before_publication"):
                            h.run()
                        self.assertNotIn("publish", h.events)
                    else:
                        h.run()
                        self.assertIn("publish", h.events)
                h.writer.assert_called_once()

    def test_bad_candidate_defers_without_starving_next_subject_and_cools_down(self):
        for error in [r.ValidationError("invalid_text"), r.FieldNotesError("source_changed_during_collection"),
                      r.FieldNotesError("http_404", status=404)]:
            with self.subTest(error=str(error)), Harness() as h:
                following = fixtures.candidate(number=322, url=f"https://github.com/{REPO}/pull/322")
                h.mocks[3].return_value = [(REPO, "pr", 321, h.item), (REPO, "pr", 322, h.item)]
                h.mocks[4].side_effect = [error, following]
                report = h.run()
                self.assertEqual(h.value["seen"][KEY]["status"], "defer")
                self.assertEqual(h.value["seen"][KEY]["listing_fingerprint"], r.listing_fingerprint(h.item))
                self.assertEqual(h.value["seen"][KEY.replace("321", "322")]["status"], "published")
                self.assertEqual(report["decisions"][0]["decision"], "defer")
                self.assertEqual(h.jev.request.call_count, 1)
                h.run()
                self.assertEqual(h.mocks[4].call_count, 2)

    def test_collection_auth_rate_limit_and_global_transport_errors_still_stop(self):
        for error in [r.FieldNotesError("http_401", status=401), r.FieldNotesError("http_403", status=403),
                      r.FieldNotesError("http_429", status=429), r.FieldNotesError("network_unavailable")]:
            with self.subTest(error=str(error)), Harness() as h:
                h.mocks[4].side_effect = error
                with self.assertRaises(r.FieldNotesError): h.run()
                self.assertFalse(h.value["seen"])
                h.jev.request.assert_not_called()
                h.writer.assert_not_called()

    def test_writer_preflight_failure_does_not_consume_daily_attempt_or_complete(self):
        with Harness() as h:
            h.mocks[7].side_effect = r.WriterError("writer_preflight:InvalidImplementation")
            with self.assertRaises(r.WriterError): h.run()
            self.assertEqual(h.value["attempts"], 0)
            self.assertFalse(h.value["seen"])
            h.writer.assert_not_called()
        self.assertIn("cleanup", h.events)

    def test_writer_safe_diagnostic_is_reported_and_callback_once(self):
        with patch.object(r, "load_config", return_value=CONFIG), patch.object(r, "secret", return_value="synthetic"), \
             patch.object(r, "API") as client, patch.object(r, "callback") as callback, \
             patch.object(r, "run", side_effect=r.WriterError("writer_preflight:InvalidImplementation")), \
             patch.object(sys, "argv", ["main.py", "--publish"]), patch("sys.stderr", new=io.StringIO()) as output:
            client.return_value.request.return_value = {"login": "enyst"}
            self.assertEqual(r.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["error"], "writer_preflight:InvalidImplementation")
        callback.assert_called_once_with("FAILED", error="writer_preflight:InvalidImplementation")

    def test_callback_exactly_once_on_writer_failure(self):
        with Harness(writer_error=True) as h, patch.object(r, "load_config", return_value=CONFIG), \
             patch.object(r, "secret", return_value="synthetic"), patch.object(r, "API", side_effect=[h.gh, h.jev]), \
             patch.object(r, "callback") as callback, patch.object(r, "load_workspace", return_value=h.workspace), \
             patch.object(sys, "argv", ["main.py", "--publish"]), patch("sys.stderr", new=io.StringIO()):
            # Pass the injected workspace through the real runner; no SDK needed.
            original = r.run
            with patch.object(r, "run", side_effect=lambda *a, **kw: original(*a, **kw, workspace_factory=lambda: h.workspace)):
                self.assertEqual(r.main(), 1)
        callback.assert_called_once_with("FAILED", error="RuntimeError")

    def test_callback_failure_payload_retains_only_safe_diagnostic_and_success_is_unchanged(self):
        environment = {"AUTOMATION_CALLBACK_URL": r.CLOUD + "/api/automation/v1/callback/test",
                       "AUTOMATION_CALLBACK_API_KEY": "synthetic-callback-credential", "AUTOMATION_RUN_ID": "synthetic-run"}
        with patch.dict(os.environ, environment), patch.object(r, "API") as client:
            r.callback("FAILED", error="writer_preflight:InvalidImplementation")
            failed = client.return_value.request.call_args.kwargs["data"]
            self.assertEqual(failed, {"status": "FAILED", "run_id": "synthetic-run",
                                      "error": "writer_preflight:InvalidImplementation"})
            self.assertNotIn("synthetic-callback-credential", json.dumps(failed))
            r.callback("COMPLETED")
            self.assertEqual(client.return_value.request.call_args.kwargs["data"],
                             {"status": "COMPLETED", "run_id": "synthetic-run"})

    def test_unexpected_exception_message_and_credentials_never_reach_callback(self):
        sensitive_message = "Synthetic provider exception includes private response and credential=do-not-forward"
        with patch.object(r, "load_config", return_value=CONFIG), patch.object(r, "secret", return_value="synthetic"), \
             patch.object(r, "API") as client, patch.object(r, "callback") as callback, \
             patch.object(r, "run", side_effect=ValueError(sensitive_message)), \
             patch.object(sys, "argv", ["main.py", "--publish"]), patch("sys.stderr", new=io.StringIO()) as output:
            client.return_value.request.return_value = {"login": "enyst"}
            self.assertEqual(r.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["error"], "ValueError")
        self.assertNotIn(sensitive_message, output.getvalue())
        callback.assert_called_once_with("FAILED", error="ValueError")


if __name__ == "__main__": unittest.main()
