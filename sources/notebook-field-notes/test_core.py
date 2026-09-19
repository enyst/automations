"""Pure policy regression tests: no HTTP, credentials, or publication."""
import copy
import json
import unittest

import core


SHA = "a" * 40
BASE = "b" * 40


def candidate(**changes):
    result = {
        "repository": "OpenHands/software-agent-sdk", "kind": "pr", "number": 321,
        "url": "https://github.com/OpenHands/software-agent-sdk/pull/321",
        "title": "Keep tool observations when compacting agent memory",
        "body": "The memory condenser dropped a tool result needed by the next agent step.",
        "updated_at": "2026-09-19T16:00:00Z", "head_sha": SHA, "base_sha": BASE,
        "files": [{"path": "openhands/sdk/context/condenser.py", "status": "modified",
                   "patch": "@@ -1 +1 @@\n-return []\n+return observations"}],
        "files_complete": True, "diff_complete": True,
        "description_complete": True, "description_truncated": False,
    }
    result.update(changes)
    return result


def response(**probabilities):
    values = dict.fromkeys(core.QUESTIONS, 0.1)
    values.update(memory=0.95, substance=0.92, design_context=0.90)
    values.update(probabilities)
    return {"model": core.MODEL, "answers": {
        key: {"type": "noul", "noul": value} for key, value in values.items()}}


def generated(**changes):
    result = {
        "title": "Memory compaction is part of the tool contract",
        "summary": "A small condenser change exposes the dependency between tool observations and the next agent step.",
        "tags": ["memory", "agent-behavior"],
        "body_markdown": (
            "## The design question\n\n"
            "What must an agent preserve when it condenses a conversation? The proposed change keeps tool "
            "observations that a later step still needs. That makes compaction part of the agent's behavioral "
            "contract, even when the implementation changes only one line.\n\n"
            "## What the source shows\n\n"
            f"The [condenser](https://github.com/OpenHands/software-agent-sdk/blob/{SHA}/openhands/sdk/context/condenser.py#L1) "
            "now retains observations. This is a source observation; it does not establish the size of any "
            "performance improvement. The useful follow-up is to test a conversation where the next action "
            "depends on a result from before compaction.\n\n"
            "## Remaining uncertainty\n\n"
            "The supplied evidence does not measure a complete agent run. A realistic behavioral regression "
            "test would tell us whether the preserved information remains usable downstream.\n"
        ),
    }
    result.update(changes)
    return result


def note(output=None, **kwargs):
    return core.build_note(candidate(), output or generated(), generated_at="2026-09-19T17:00:00Z",
                           writer_model="anthropic/claude-sonnet-4-6",
                           examined_commits=[{"repository": "OpenHands/software-agent-sdk", "sha": SHA}],
                           classifier=core.validate_classification(response()), **kwargs)


class DiscoveryPolicy(unittest.TestCase):
    def test_alias_is_one_subject_and_one_fingerprint(self):
        canonical = candidate()
        alias = candidate(repository="openhands/agent-sdk",
                          url="https://github.com/openhands/agent-sdk/pull/321")
        self.assertEqual(core.subject_id(canonical), "openhands-software-agent-sdk-pr-321")
        self.assertEqual(core.subject_id(alias), core.subject_id(canonical))
        self.assertEqual(core.fingerprint(alias), core.fingerprint(canonical))

    def test_private_or_different_repository_is_rejected(self):
        for repository in ["enyst/liberty-labs", "OpenHands/extensions", "OpenHands/OpenHands/../private"]:
            with self.subTest(repository=repository), self.assertRaises(core.ValidationError):
                core.normalize_candidate(candidate(repository=repository))
        with self.assertRaises(core.ValidationError):
            core.normalize_candidate(candidate(private=True))

    def test_source_url_cannot_disagree_with_candidate(self):
        for url in ["https://github.com/OpenHands/automation/pull/321", "https://github.com.evil.test/OpenHands/software-agent-sdk/pull/321",
                    "https://user:pass@github.com/OpenHands/software-agent-sdk/pull/321", "https://github.com/OpenHands/software-agent-sdk/pull/322"]:
            with self.subTest(url=url), self.assertRaises(core.ValidationError):
                core.normalize_candidate(candidate(url=url))

    def test_timestamp_only_change_does_not_reclassify(self):
        self.assertEqual(core.fingerprint(candidate()), core.fingerprint(candidate(updated_at="2026-09-20T00:00:00Z")))
        self.assertNotEqual(core.fingerprint(candidate()), core.fingerprint(candidate(head_sha="c" * 40)))
        self.assertNotEqual(core.fingerprint(candidate()), core.fingerprint(candidate(body="Different evidence of agent behavior")))

    def test_audit_receipts_are_not_material_but_human_text_after_them_is(self):
        block = "\n\n<!-- jev-fast-audit:start -->\n## Jev-Fast-Audit\nScore\n<!-- jev-fast-audit:end -->\n"
        original = candidate()
        self.assertEqual(core.fingerprint(original), core.fingerprint(candidate(body=original["body"] + block)))
        self.assertNotEqual(core.fingerprint(original), core.fingerprint(candidate(body=original["body"] + block + "\n## Design update\nNew memory evidence.")))

    def test_tiny_behavioral_and_design_document_changes_remain_eligible(self):
        self.assertEqual(core.screen_candidate(candidate())["decision"], "classify")
        design = candidate(files=[{"path": "docs/design/memory.md", "patch": "+Design: retain observations."}])
        self.assertEqual(core.screen_candidate(design)["decision"], "classify")

    def test_skip_mechanical_only_when_complete_and_unambiguously_mechanical(self):
        locks = [{"path": "package-lock.json", "patch": "+lockfile metadata"}]
        self.assertEqual(core.screen_candidate(candidate(title="Regenerate lockfile", body="", files=locks))["reason"], "mechanical_only")
        self.assertEqual(core.screen_candidate(candidate(title="Regenerate lockfile", body="", files=locks, files_complete=False))["decision"], "classify")
        self.assertEqual(core.screen_candidate(candidate(title="Fix agent memory with dependency update", files=locks))["decision"], "classify")
        self.assertEqual(core.screen_candidate(candidate(files=[]))["decision"], "classify")

    def test_completed_fingerprint_skips_but_failed_work_retries(self):
        value = core.fingerprint(candidate())
        self.assertEqual(core.screen_candidate(candidate(), completed_fingerprints={value})["reason"], "already_processed")
        self.assertEqual(core.screen_candidate(candidate(), completed_fingerprints=set())["decision"], "classify")

    def test_issue_needs_inspected_sha_and_cannot_be_treated_as_lockfile_change(self):
        issue = candidate(kind="issue", url="https://github.com/OpenHands/software-agent-sdk/issues/321", files=[])
        self.assertEqual(core.screen_candidate(issue)["decision"], "classify")
        with self.assertRaises(core.ValidationError):
            core.normalize_candidate({**issue, "head_sha": "main"})


class JevPolicy(unittest.TestCase):
    def test_independent_pinned_noul_questions_and_multilabel_decision(self):
        request = core.classification_request(candidate())
        self.assertEqual(request["model"], "jev-1.13.0")
        self.assertEqual(len(request["questions"]), 6)
        self.assertTrue(all(q["type"] == "noul" for q in request["questions"].values()))
        decision = core.classify_decision(core.validate_classification(response(cross_repo=0.91)))
        self.assertEqual(decision["decision"], "write")
        self.assertIn("memory", decision["tags"])
        self.assertIn("cross-repo", decision["tags"])

    def test_invalid_answer_is_error_never_a_skipped_candidate(self):
        for invalid in [True, None, "0.9", -0.1, 1.1, float("nan"), float("inf")]:
            with self.subTest(invalid=invalid), self.assertRaises(core.ValidationError):
                core.validate_classification(response(memory=invalid))
        wrong = response()
        wrong["model"] = "latest"
        with self.assertRaises(core.ValidationError):
            core.validate_classification(wrong)
        missing = response()
        del missing["answers"]["memory"]
        with self.assertRaises(core.ValidationError):
            core.validate_classification(missing)

    def test_substance_required_and_incomplete_negative_defers(self):
        low = core.validate_classification(response(substance=0.2))
        self.assertEqual(core.classify_decision(low)["decision"], "skip")
        self.assertEqual(core.classify_decision(low, context_complete=False)["decision"], "defer")
        self.assertEqual(core.classify_decision(core.validate_classification(response()), context_complete=False)["decision"], "defer")

    def test_large_diff_and_comments_never_reach_jev_or_consume_its_budget(self):
        large = candidate(files=[{"path": "secret_source_marker.py", "patch": "DIFF_ONLY_MARKER" * 1_000_000}],
                          comments=[{"body": "COMMENT_ONLY_MARKER"}], source_excerpt="SOURCE_ONLY_MARKER")
        request = core.classification_request(large)
        serialized = json.dumps(request)
        for forbidden in ["DIFF_ONLY_MARKER", "COMMENT_ONLY_MARKER", "SOURCE_ONLY_MARKER", "secret_source_marker.py", SHA, BASE]:
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(set(request["state"]), {"repositories", "subject", "linked_issues", "coverage"})
        self.assertEqual(request["state"]["subject"]["body"], large["body"])
        self.assertTrue(request["state"]["coverage"]["retrieval_complete"])
        self.assertFalse(request["state"]["coverage"]["truncated"])

    def test_classifier_strips_only_owned_scorecard_without_mutating_raw_description(self):
        body = ("A clear description.\n\n<!-- jev-fast-audit:start -->\n## Jev-Fast-Audit\n"
                "SCORE_FEEDBACK_MARKER\n<!-- jev-fast-audit:end -->\n\nHuman design clarification.")
        original = candidate(body=body)
        request = core.classification_request(original)
        self.assertNotIn("SCORE_FEEDBACK_MARKER", json.dumps(request))
        self.assertIn("Human design clarification.", request["state"]["subject"]["body"])
        self.assertEqual(original["body"], body)
        self.assertEqual(core.normalize_candidate(original)["body"], body)

    def test_default_transport_budget_is_bounded_even_if_caller_requests_more(self):
        request = core.classification_request(candidate(body="Design details. " * 6000), max_context_bytes=100_000)
        self.assertEqual(core.MAX_CONTEXT_BYTES, 24_000)
        self.assertLessEqual(len(json.dumps(request["state"], ensure_ascii=False).encode()), 24_000)
        self.assertTrue(request["state"]["coverage"]["truncated"])

    def test_unicode_descriptions_are_balanced_and_truncation_cannot_request_information(self):
        linked = [{"url": f"https://github.com/OpenHands/automation/issues/{n}", "title": f"Design issue {n}",
                   "body": f"Issue{n} " + "模型記憶" * 2000} for n in range(1, 5)]
        large = candidate(body="Primary " + "🙂" * 8000, linked_issues=linked)
        request = core.classification_request(large, max_context_bytes=6000)
        state = request["state"]
        self.assertLessEqual(len(json.dumps(state, ensure_ascii=False).encode()), 6000)
        self.assertTrue(state["coverage"]["truncated"])
        self.assertEqual(len(state["linked_issues"]), 4)
        self.assertTrue(state["subject"]["body"].startswith("Primary"))
        self.assertTrue(all(issue["body"].startswith("Issue") for issue in state["linked_issues"]))
        self.assertNotIn("\ufffd", json.dumps(state, ensure_ascii=False))
        self.assertEqual(request["questions"], core.QUESTIONS)
        complete = state["coverage"]["retrieval_complete"] and not state["coverage"]["truncated"]
        low = core.validate_classification(response(design_context=0.1))
        self.assertEqual(core.classify_decision(low, context_complete=complete)["decision"], "defer")

    def test_vague_descriptions_request_information_even_when_topic_scores_are_low(self):
        poor = core.validate_classification(response(design=0.1, agent_behavior=0.1, memory=0.1,
                                                       cross_repo=0.1, substance=0.1, design_context=0.1))
        self.assertEqual(core.classify_decision(poor)["decision"], "needs_info")
        self.assertEqual(core.classify_decision(poor, context_complete=False)["decision"], "defer")
        ambiguous = core.validate_classification(response(design_context=0.5))
        self.assertEqual(core.classify_decision(ambiguous)["decision"], "defer")
        routine = core.validate_classification(response(design=0.1, agent_behavior=0.1, memory=0.1,
                                                          cross_repo=0.1, substance=0.1, design_context=0.95))
        self.assertEqual(core.classify_decision(routine)["decision"], "skip")

    def test_missing_retrieval_and_extra_linked_issues_are_not_authors_missing_context(self):
        linked = [{"url": f"https://github.com/OpenHands/automation/issues/{n}", "title": "Related design", "body": ""}
                  for n in range(1, 6)]
        for value in [candidate(description_complete=False), candidate(description_truncated=True), candidate(linked_issues=linked)]:
            with self.subTest(value=value):
                coverage = core.classification_request(value)["state"]["coverage"]
                complete = coverage["retrieval_complete"] and not coverage["truncated"]
                self.assertFalse(complete)
                self.assertEqual(core.classify_decision(core.validate_classification(response(design_context=0.1)),
                                                       context_complete=complete)["decision"], "defer")

    def test_linked_description_changes_and_policy_version_are_in_fingerprint(self):
        linked = {"url": "https://github.com/OpenHands/automation/issues/1", "title": "Related design", "body": "Initial question",
                  "updated_at": "2026-09-19T16:00:00Z"}
        first = candidate(linked_issues=[linked])
        normalized = core.normalize_candidate(first)
        self.assertEqual(normalized["linked_issues"][0]["body"], "Initial question")
        self.assertEqual(core.POLICY_VERSION, 2)
        self.assertNotEqual(core.fingerprint(first), core.fingerprint(candidate(linked_issues=[{**linked, "body": "Explained design"}])))
        self.assertEqual(core.fingerprint(first), core.fingerprint(candidate(linked_issues=[{**linked, "updated_at": "2026-09-20T00:00:00Z"}])))

    def test_live_classifier_requires_sixth_answer(self):
        legacy = response()
        del legacy["answers"]["design_context"]
        with self.assertRaises(core.ValidationError): core.validate_classification(legacy)


class PublicationPolicy(unittest.TestCase):
    def test_new_artifact_retains_sixth_probability_and_legacy_five_stays_valid(self):
        modern = note()
        self.assertEqual(modern["classifier"]["probabilities"]["design_context"], 0.9)
        legacy = copy.deepcopy(modern["classifier"])
        del legacy["probabilities"]["design_context"]
        preserved = core.build_note(candidate(), generated(), generated_at="2026-09-19T17:00:00Z", writer_model="example/model",
                                   examined_commits=[{"repository": "OpenHands/software-agent-sdk", "sha": SHA}], classifier=legacy)
        self.assertEqual(preserved["classifier"], legacy)
        self.assertNotIn("design_context", preserved["classifier"]["probabilities"])

    def test_script_owns_identity_provenance_tags_and_public_visibility(self):
        result = note()
        self.assertEqual(result["id"], "field-note-openhands-software-agent-sdk-pr-321")
        self.assertEqual(result["visibility"], "public")
        self.assertEqual(result["author"], "OpenHands Automation")
        self.assertEqual(result["tags"][0], "field-notes")
        self.assertEqual(result["source"]["head_sha"], SHA)
        self.assertEqual(result["classifier"]["model"], core.MODEL)
        for field in ["visibility", "id", "path", "source", "author"]:
            with self.subTest(field=field), self.assertRaises(core.ValidationError):
                note(generated(**{field: "chosen-by-model"}))

    def test_publication_rejects_active_markup_or_unapproved_destinations(self):
        bad = ["<script>alert(1)</script>", "<iframe src='/admin/'>", "![image](https://github.com/OpenHands/OpenHands/blob/" + SHA + "/image.png)",
               "[private](https://github.com/enyst/liberty-labs/blob/" + SHA + "/data.json)",
               "[script](javascript:alert(1))", "[credentials](/instance/session-secret)",
               "[moving](https://github.com/OpenHands/software-agent-sdk/blob/main/a.py)",
               "[unexamined](https://github.com/OpenHands/software-agent-sdk/blob/" + "d" * 40 + "/a.py)",
               "[ref](https://github.com/OpenHands/software-agent-sdk/blob/" + SHA + "/design-ref.md)",
               "[a][reference]\n\n[reference]: https://evil.test", "www.evil.test", "engel.nyst@gmail.com"]
        for suffix in bad:
            with self.subTest(suffix=suffix), self.assertRaises(core.ValidationError):
                note(generated(body_markdown=generated()["body_markdown"] + "\n\n" + suffix))

    def test_requires_pinned_evidence_and_no_secret_shaped_text(self):
        with self.assertRaises(core.ValidationError):
            note(generated(body_markdown="A long unsupported claim. " * 50))
        for secret in ["ghp_" + "A" * 36, "GOCSPX-" + "x" * 28, "-----BEGIN PRIVATE KEY-----"]:
            with self.subTest(secret=secret[:8]), self.assertRaises(core.ValidationError):
                note(generated(body_markdown=generated()["body_markdown"] + "\n" + secret))

    def test_renderer_escapes_text_and_never_trusts_raw_html(self):
        result = note()
        rendered = core.render_document(result)
        self.assertIn("<!doctype html>", rendered)
        self.assertIn("Autonomous investigation", rendered)
        self.assertIn("2026-09-19", rendered)
        self.assertIn("noopener noreferrer", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("style=", rendered)
        self.assertIn("Memory compaction is part of the tool contract", rendered)
        tampered = copy.deepcopy(result)
        tampered["title"] = "<img src=x onerror=alert(1)>"
        self.assertNotIn("<img", core.render_document(tampered))

    def test_fenced_code_is_inert_and_emphasis_is_not_applied_inside_code(self):
        result = note(generated(body_markdown=generated()["body_markdown"] +
                                "\n\n**Contract** and *hypothesis*, with `**literal**`.\n\n```html\n<script>example()</script>\n```"))
        rendered = core.render_document(result)
        self.assertIn("<strong>Contract</strong>", rendered)
        self.assertIn("<em>hypothesis</em>", rendered)
        self.assertIn("<code>**literal**</code>", rendered)
        self.assertIn("&lt;script&gt;example()&lt;/script&gt;", rendered)
        self.assertNotIn("<script>", rendered)


if __name__ == "__main__":
    unittest.main()
