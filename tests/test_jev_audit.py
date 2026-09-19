"""Focused behavior tests for the pure Jev audit helper."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "sources/jev-fast-audit/audit.py"
SPEC = importlib.util.spec_from_file_location("jev_audit", SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

HEAD = "a" * 40
BASE = "b" * 40


def pr(**changes):
    result = {"html_url": "https://github.com/OpenHands/automation/pull/123",
              "title": "Handle the edge case", "body": "Keep the existing API.",
              "state": "open", "head": {"sha": HEAD}, "base": {"sha": BASE}}
    result.update(changes)
    return result


def simple_state():
    return audit.build_context(pr(changed_files=1), [
        {"filename": "src/a.py", "status": "modified", "additions": 1, "deletions": 1,
         "patch": "@@ -1 +1 @@\n-old\n+new\n"}
    ], {"src/a.py": {"base": "old\n", "head": "new\n"}})


def answers_for(state):
    answers = {}
    for key, question in audit.questions_for(state).items():
        kind = question["type"]
        if kind == "noul":
            answers[key] = {"type": kind, "noul": 0.1}
        else:
            options = list(question["criteria"]) if kind == "choice" else [str(i) for i in range(len(question["criteria"]))]
            selected = "NONE" if "NONE" in options else options[0]
            answers[key] = {"type": kind, "probabilities": {x: float(x == selected) for x in options},
                            "confidence": 0.8}
            if kind == "choice":
                answers[key]["choice"] = selected
            else:
                answers[key]["score"] = 0
                answers[key]["legend"] = question["criteria"]
    return {"model": audit.MODEL, "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 50}}


class ContextTests(unittest.TestCase):
    def test_whole_hunks_and_twenty_surrounding_lines(self):
        before = "".join(f"line {i}\n" for i in range(1, 101))
        after = before.replace("line 30\n", "changed thirty\n").replace("line 70\n", "changed seventy\n")
        first = "@@ -30 +30 @@ helper\n-line 30\n+changed thirty\n"
        second = "@@ -70 +70 @@\n-line 70\n+changed seventy\n"
        state = audit.build_context(pr(changed_files=1), [
            {"filename": "x.py", "status": "modified", "patch": first + second,
             "additions": 2, "deletions": 2}
        ], {"x.py": {"base": before, "head": after}})
        self.assertTrue(state["coverage"]["complete"])
        hunks = state["files"][0]["hunks"]
        self.assertEqual([h["id"] for h in hunks], ["F001H001", "F001H002"])
        self.assertEqual("".join(h["patch"] for h in hunks), first + second)
        self.assertEqual(hunks[0]["base_context"]["start_line"], 10)
        self.assertEqual(hunks[0]["base_context"]["end_line"], 50)
        self.assertEqual(hunks[0]["base_context"]["text"], "".join(before.splitlines(keepends=True)[9:50]))
        self.assertIn("changed thirty\n", hunks[0]["head_context"]["text"])
        self.assertEqual(hunks[1]["head_context"]["total_lines"], 100)

    def test_added_removed_and_renamed_files(self):
        rows = [
            {"filename": "added.txt", "status": "added", "patch": "@@ -0,0 +1 @@\n+hello\n"},
            {"filename": "removed.txt", "status": "removed", "patch": "@@ -1 +0,0 @@\n-bye\n"},
            {"filename": "renamed name.txt", "previous_filename": "old name.txt", "status": "renamed",
             "patch": "@@ -1 +1 @@\n-old\n+new\n"},
        ]
        contents = {"added.txt": {"head": "hello\n", "base": None},
                    "removed.txt": {"base": "bye\n", "head": None},
                    "renamed name.txt": {"base": "old\n", "head": "new\n"}}
        state = audit.build_context(pr(), rows, contents)
        self.assertTrue(state["coverage"]["complete"])
        by_path = {x["path"]: x for x in state["files"]}
        self.assertIsNone(by_path["added.txt"]["hunks"][0]["base_context"])
        removed = by_path["removed.txt"]["hunks"][0]
        self.assertIsNone(removed["head_context"])
        self.assertIn(f"/blob/{BASE}/removed.txt", removed["url"])
        self.assertIn("renamed%20name.txt", by_path["renamed name.txt"]["hunks"][0]["url"])
        self.assertEqual(by_path["renamed name.txt"]["previous_path"], "old name.txt")

    def test_deletion_only_hunk_links_existing_base_lines(self):
        state = audit.build_context(pr(), [
            {"filename": "empty.txt", "status": "modified", "patch": "@@ -1 +0,0 @@\n-old\n"}
        ], {"empty.txt": {"base": "old\n", "head": ""}})
        hunk = state["files"][0]["hunks"][0]
        self.assertEqual(hunk["link_side"], "base")
        self.assertEqual(hunk["url"], f"https://github.com/OpenHands/automation/blob/{BASE}/empty.txt#L1-L1")
        self.assertTrue(state["coverage"]["complete"])

    def test_missing_patch_context_and_missing_file_rows_are_explicit(self):
        rows = [{"filename": "binary.dat", "status": "modified"},
                {"filename": "text.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"}]
        state = audit.build_context(pr(changed_files=3), rows, {})
        self.assertFalse(state["coverage"]["complete"])
        self.assertEqual(state["coverage"]["reasons"], {
            "file_rows_missing": 1, "missing_context": 2, "missing_patch": 1})
        self.assertEqual(state["coverage"]["files_total"], 3)
        self.assertEqual(state["files"][1]["hunks"][0]["context_omissions"], ["missing_context", "missing_context"])

    def test_truncated_hunk_not_sent_as_whole(self):
        state = audit.build_context(pr(), [
            {"filename": "x.py", "status": "modified", "patch": "@@ -1,3 +1,3 @@\n-a\n+b\n"}
        ], {"x.py": {"base": "a\nx\ny\n", "head": "b\nx\ny\n"}})
        self.assertEqual(state["files"][0]["hunks"], [])
        self.assertEqual(state["coverage"]["reasons"]["malformed_hunk"], 1)
        self.assertFalse(state["coverage"]["complete"])

    def test_valid_prefix_with_missing_later_changes_is_partial(self):
        state = audit.build_context(pr(), [
            {"filename": "x.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n",
             "additions": 2, "deletions": 2}
        ], {"x.py": {"base": "a\nx\n", "head": "b\ny\n"}})
        self.assertEqual(state["coverage"]["reasons"], {
            "patch_additions_mismatch": 1, "patch_deletions_mismatch": 1})
        self.assertEqual(state["coverage"]["hunks_included"], 1)

    @patch.object(audit, "MAX_TRANSPORT_BYTES", 100_000)
    def test_budget_omits_whole_hunk_but_keeps_later_small_file(self):
        huge = "界" * 20000
        state = audit.build_context(pr(), [
            {"filename": "a.txt", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+" + huge + "\n"},
            {"filename": "z.txt", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
        ], {"a.txt": {"base": "old\n", "head": huge + "\n"},
            "z.txt": {"base": "a\n", "head": "b\n"}})
        self.assertEqual(state["files"][0]["hunks"], [])
        self.assertEqual(state["files"][1]["hunks"][0]["patch"], "@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual(state["coverage"]["reasons"]["hunk_budget"], 1)
        self.assertFalse(state["coverage"]["complete"])
        request = {"model": audit.MODEL, "state": state, "questions": audit.questions_for(state)}
        size = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertEqual(state["coverage"]["serialized_bytes"], size)
        self.assertLessEqual(size, audit.MAX_TRANSPORT_BYTES)

    @patch.object(audit, "MAX_TRANSPORT_BYTES", 65_000)
    def test_patch_bodies_survive_when_early_surrounding_context_cannot_fit(self):
        before = "import os\n" + ("long surrounding source " * 30 + "\n") * 60 + "return old\n"
        after = before.replace("import os\n", "import sys\n").replace("return old\n", "return new\n")
        first = "@@ -1 +1 @@\n-import os\n+import sys\n"
        second = "@@ -62 +62 @@\n-return old\n+return new\n"
        rows = [{"filename": "code.py", "status": "modified", "patch": first + second,
                 "additions": 2, "deletions": 2}]
        state = audit.build_context(pr(), rows, {"code.py": {"base": before, "head": after}})
        hunks = state["files"][0]["hunks"]
        self.assertEqual([h["patch"] for h in hunks], [first, second])
        self.assertEqual(state["coverage"]["hunks_included"], 2)
        self.assertIn("context_budget", state["coverage"]["reasons"])
        self.assertNotIn("hunk_budget", state["coverage"]["reasons"])
        self.assertLessEqual(state["coverage"]["serialized_bytes"], audit.MAX_TRANSPORT_BYTES)

    def test_many_context_omissions_fit_their_reserved_metadata(self):
        lines = [f"context {i}: " + "x" * 80 + "\n" for i in range(1800)]
        after = list(lines)
        patches = []
        for index in range(200):
            line = 10 + index * 8
            after[line - 1] = "changed\n"
            patches.append(f"@@ -{line} +{line} @@\n-" + lines[line - 1] + "+changed\n")
        state = audit.build_context(pr(), [
            {"filename": "many.py", "status": "modified", "patch": "".join(patches)}
        ], {"many.py": {"base": "".join(lines), "head": "".join(after)}})
        self.assertEqual(state["coverage"]["hunks_included"], 200)
        self.assertGreater(state["coverage"]["reasons"]["context_budget"], 0)
        self.assertLessEqual(state["coverage"]["serialized_bytes"], audit.MAX_TRANSPORT_BYTES)

    @patch.object(audit, "MAX_TRANSPORT_BYTES", 100_000)
    def test_body_preserved_whole_or_explicitly_omitted_whole(self):
        text = "Description with Unicode: 猫\n" * 3
        state = audit.build_context(pr(body=text), [], {})
        self.assertEqual(state["pr"]["body"], text)
        huge = text * 1000
        state = audit.build_context(pr(body=huge), [], {})
        self.assertIsNone(state["pr"]["body"])
        self.assertEqual(state["pr"]["body_omitted"]["utf8_bytes"], len(huge.encode("utf-8")))
        self.assertIn("pr_body_budget", state["coverage"]["reasons"])
        self.assertFalse(state["coverage"]["complete"])

    def test_unsafe_or_duplicate_paths_and_untrusted_url_rejected(self):
        for path in ("../x", "/tmp/x", "a/../x", "x\x00y"):
            with self.subTest(path=path), self.assertRaises(audit.AuditValidationError):
                audit.build_context(pr(), [{"filename": path}], {})
        with self.assertRaises(audit.AuditValidationError):
            audit.build_context(pr(html_url="javascript:alert(1)"), [], {})
        with self.assertRaises(audit.AuditValidationError):
            audit.build_context(pr(), [{"filename": "a"}, {"filename": "a"}], {})
        with self.assertRaises(audit.AuditValidationError):
            audit.build_context(pr(head={"sha": "main"}), [], {})

    def test_ids_deterministic_and_input_is_not_mutated(self):
        data = pr()
        files = [{"filename": "z", "patch": None}, {"filename": "a", "patch": None}]
        original = copy.deepcopy((data, files))
        state = audit.build_context(data, files, {})
        self.assertEqual((data, files), original)
        self.assertEqual([(f["id"], f["path"]) for f in state["files"]], [("F001", "a"), ("F002", "z")])


class QuestionsAndValidationTests(unittest.TestCase):
    def test_atomic_risks_and_evidence_contract(self):
        questions = audit.questions_for(simple_state())
        self.assertEqual(sum(q["type"] == "noul" for q in questions.values()), 15)
        self.assertEqual(len(questions), 31)
        for key in audit.RISKS:
            evidence = questions[key + "Evidence"]
            self.assertEqual(set(evidence["criteria"]), {"F001H001", "NONE"})
            self.assertIn("Independently", evidence["instructions"])
            self.assertIn("untrusted evidence", questions[key]["instructions"])
            self.assertNotIn("confidence", questions[key])
        self.assertNotIn("approve", json.dumps(questions).lower())

    def test_sdk_security_questions_keep_complete_claims_and_policy(self):
        state = simple_state()
        expected = {"secretDisclosure", "unexpectedDataTransfer", "credentialMisuse",
                    "promptInjection", "packageSourceTampering", "unverifiedRemoteExecution",
                    "privilegeBoundaryBreak", "securityAssessmentBypass", "abusiveWorkload"}
        questions = audit.questions_for(state)
        for key in expected:
            claim = audit.RISKS[key][1]
            self.assertEqual(questions[key]["type"], "noul")
            self.assertIn(claim, questions[key]["criteria"]["true"])
            self.assertIn(" Example: ", questions[key]["criteria"]["true"])
            self.assertIn(" Examples: ", questions[key]["criteria"]["false"])
            self.assertIn(claim, questions[key + "Evidence"]["instructions"])
            self.assertIn(claim, questions["primaryConcernChoice"]["criteria"][key])
            self.assertIn("untrusted evidence", questions[key]["instructions"])
            self.assertIn("do not claim they were absent", questions[key]["instructions"])
        self.assertIn("environment variable is not inherently a secret", audit.RISKS["secretDisclosure"][1])
        self.assertIn("Inert quoted examples", audit.RISKS["promptInjection"][1])
        self.assertIn("missing runtime assessments", audit.RISKS["securityAssessmentBypass"][1])
        # Full instructions alone exceed the old byte cap; none are weakened.
        self.assertGreater(len(json.dumps(questions).encode()), 30 * 1024)
        self.assertEqual(state["coverage"]["provider_token_limits"],
                         {"request": 64000, "state_plus_longest_question": 32000})

    def test_removed_questions_are_absent_from_request_and_rendering(self):
        state = simple_state()
        questions = audit.questions_for(state)
        rendered = audit.render_summary(state, answers_for(state), 1)
        for key in ("testCoverageGap", "descriptionMismatch", "resourceLeak", "behaviorRegression", "impactScore"):
            self.assertNotIn(key, questions)
            self.assertNotIn(key + "Evidence", questions)
            self.assertNotIn(key, questions["primaryConcernChoice"]["criteria"])
        self.assertFalse(any(q["type"] == "score" for q in questions.values()))
        self.assertNotIn("Impact breadth", rendered)

    def test_no_hunks_omits_single_option_evidence_choices(self):
        state = audit.build_context(pr(), [], {})
        questions = audit.questions_for(state)
        self.assertEqual(len(questions), 16)
        self.assertFalse(any(k.endswith("Evidence") for k in questions))

    def test_choice_option_cap(self):
        state = simple_state()
        original = state["files"][0]["hunks"][0]
        state["files"][0]["hunks"] = [dict(original, id=f"F001H{i:03}") for i in range(1, 255)]
        questions = audit.questions_for(state)
        self.assertEqual(len(questions["sqlInjectionEvidence"]["criteria"]), 255)
        state["files"][0]["hunks"].append(dict(original, id="F001H255"))
        with self.assertRaises(audit.AuditValidationError):
            audit.questions_for(state)

    def test_valid_probability_result(self):
        state = simple_state()
        response = answers_for(state)
        validated = audit.validate_answers(response, audit.questions_for(state))
        self.assertEqual(validated["sqlInjection"], {"type": "noul", "noul": 0.1})

    def test_rounded_choice_distributions_preserve_raw_probabilities(self):
        state = simple_state()
        for probability in (0.79, 0.81):
            response = answers_for(state)
            choice = response["answers"]["sqlInjectionEvidence"]
            choice["probabilities"] = {"NONE": probability, "F001H001": 0.20}
            before = copy.deepcopy(response)
            validated = audit.validate_answers(response, audit.questions_for(state))
            self.assertEqual(validated["sqlInjectionEvidence"]["probabilities"], choice["probabilities"])
            self.assertEqual(response, before)
        for probability in (0.78, 0.82):
            response["answers"]["sqlInjectionEvidence"]["probabilities"]["NONE"] = probability
            with self.assertRaisesRegex(audit.AuditValidationError, "probability_sum_mismatch"):
                audit.validate_answers(response, audit.questions_for(state))

    def test_nonfinite_bool_and_out_of_range_nouls_rejected(self):
        state = simple_state()
        for value in (math.nan, math.inf, -math.inf, True, -0.01, 1.01, "0.1", None):
            with self.subTest(value=value):
                response = answers_for(state)
                response["answers"]["sqlInjection"]["noul"] = value
                with self.assertRaises(audit.AuditValidationError):
                    audit.validate_answers(response, audit.questions_for(state))

    def test_invalid_model_keys_types_and_invented_evidence(self):
        state = simple_state()
        for mutate in (
            lambda r: r.update(model="other"),
            lambda r: r["answers"].pop("sqlInjection"),
            lambda r: r["answers"].update(unknown={"type": "noul", "noul": 0}),
            lambda r: r["answers"]["sqlInjection"].update(type="score"),
            lambda r: r["answers"]["sqlInjectionEvidence"].update(choice="F999H999"),
        ):
            response = answers_for(state)
            mutate(response)
            with self.assertRaises(audit.AuditValidationError):
                audit.validate_answers(response, audit.questions_for(state))

    def test_distribution_complete_finite_normalized_and_confidence_bounded(self):
        state = simple_state()
        for mutate in (
            lambda a: a["probabilities"].pop("NONE"),
            lambda a: a["probabilities"].update(NONE=0.7),
            lambda a: a["probabilities"].update(NONE=math.nan),
            lambda a: a["probabilities"].update(NONE=True),
            lambda a: a.update(confidence=1.1),
        ):
            response = answers_for(state)
            mutate(response["answers"]["sqlInjectionEvidence"])
            with self.assertRaises(audit.AuditValidationError):
                audit.validate_answers(response, audit.questions_for(state))


class RenderTests(unittest.TestCase):
    def test_five_visible_lines_all_scores_and_real_evidence_link(self):
        state = simple_state()
        response = answers_for(state)
        response["answers"]["primaryConcernChoice"]["choice"] = "contractRegression"
        response["answers"]["contractRegression"]["noul"] = 0.72
        response["answers"]["contractRegressionEvidence"]["choice"] = "F001H001"
        body = audit.render_summary(state, response, 321)
        visible = body.split("<details>")[0].strip()
        self.assertEqual(len(visible.splitlines()), 5)
        self.assertIn("72% estimated likelihood", visible)
        self.assertIn(f"/blob/{HEAD}/src/a.py#L1-L1", visible)
        self.assertIn("F001H001 · src/a.py:1", body)
        self.assertNotIn("contractRegression", body)
        self.assertNotIn("confidence", visible)
        self.assertIn("tests were not run", visible)
        for label, _ in audit.RISKS.values():
            self.assertIn("| " + label + " |", body)
        self.assertIn("All estimates and evidence", body)
        self.assertFalse(body.startswith("#"))

    def test_partial_and_no_primary_concern_are_not_approval(self):
        state = audit.build_context(pr(), [{"filename": "unknown.bin"}], {})
        body = audit.render_summary(state, answers_for(state), 1)
        self.assertIn("partial coverage", body)
        self.assertIn("No primary concern selected", body)
        self.assertIn("missing patch", body)
        self.assertNotIn("APPROVE", body)
        self.assertNotIn("safe to merge", body)

    def test_markdown_filename_does_not_escape_table(self):
        path = "a|<script>[x].py"
        state = audit.build_context(pr(), [
            {"filename": path, "status": "added", "patch": "@@ -0,0 +1 @@\n+x\n"}
        ], {path: {"head": "x\n"}})
        response = answers_for(state)
        response["answers"]["sqlInjectionEvidence"]["choice"] = "F001H001"
        body = audit.render_summary(state, response, 1)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("%7C%3Cscript%3E%5Bx%5D.py", body)

    def test_renderer_revalidates_and_rejects_invalid_latency(self):
        state = simple_state()
        response = answers_for(state)
        response["answers"]["commandInjection"]["noul"] = math.nan
        with self.assertRaises(audit.AuditValidationError):
            audit.render_summary(state, response, 100)
        for value in (True, -1, math.inf):
            with self.subTest(value=value), self.assertRaises(audit.AuditValidationError):
                audit.render_summary(state, answers_for(state), value)


if __name__ == "__main__":
    unittest.main()
