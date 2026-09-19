import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).parents[1] / "sources/notebook-field-notes"
spec = importlib.util.spec_from_file_location("notebook_writer", SOURCE / "writer.py")
w = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = w
spec.loader.exec_module(w)

REPO = "OpenHands/software-agent-sdk"
SHA = "a" * 40
PINS = [{"repository": REPO, "sha": SHA}]


class PublicEvidenceTests(unittest.TestCase):
    def test_only_fixed_public_repositories_and_pinned_commits_are_read(self):
        calls = []

        def fetch(url, limit):
            calls.append(url)
            return b"one\ntwo\nthree\n"

        reader = w.PublicEvidence(PINS, fetch=fetch)
        result = reader.read_file(REPO, SHA, "src/memory.py", 2, 3)
        self.assertEqual(result["text"], "2: two\n3: three")
        self.assertEqual(result["url"], f"https://github.com/{REPO}/blob/{SHA}/src/memory.py#L2-L3")
        self.assertEqual(reader.examined_commits, PINS)
        for repo, sha, path in [
            ("enyst/liberty-labs", SHA, "data/private.json"),
            (REPO, "main", "src/memory.py"),
            (REPO, "b" * 40, "src/memory.py"),
            (REPO, SHA, "../../secret"),
            (REPO, SHA, "/etc/passwd"),
            (REPO, SHA, "https://elsewhere.invalid/file"),
            (REPO, SHA, "src\\secret"),
            (REPO, SHA, "src/\nsecret"),
        ]:
            with self.subTest(repo=repo, sha=sha, path=path):
                with self.assertRaises(w.WriterError):
                    reader.read_file(repo, sha, path)
        self.assertEqual(len(calls), 1)

    def test_tree_is_bounded_and_can_find_related_code(self):
        tree = {"tree": [
            {"path": "sdk/memory/state.py", "type": "blob"},
            {"path": "tests/memory/test_state.py", "type": "blob"},
            {"path": "README.md", "type": "blob"},
        ], "truncated": False}
        calls = []

        def fetch(url, limit):
            calls.append(url)
            return json.dumps(tree).encode()

        reader = w.PublicEvidence(PINS, fetch=fetch)
        result = reader.list_tree(REPO, SHA, "memory")
        self.assertEqual(result["paths"], ["sdk/memory/state.py", "tests/memory/test_state.py"])
        reader.list_tree(REPO, SHA, "README")
        self.assertEqual(len(calls), 1)
        self.assertEqual(reader.examined_commits, [])

    def test_network_and_size_budgets_fail_closed(self):
        calls = []

        def fetch(url, limit):
            calls.append(url)
            return b"sample\n"

        reader = w.PublicEvidence(PINS, fetch=fetch, max_requests=1)
        reader.read_file(REPO, SHA, "one.py")
        with self.assertRaises(w.WriterError):
            reader.read_file(REPO, SHA, "two.py")
        self.assertEqual(len(calls), 1)
        oversized = w.PublicEvidence(PINS, fetch=lambda url, limit: b"x" * (limit + 1))
        with self.assertRaises(w.WriterError):
            oversized.read_file(REPO, SHA, "too-big.py")

    def test_errors_never_return_request_or_credential_details(self):
        def fetch(url, limit):
            raise RuntimeError("Bearer synthetic-private-key /private/local/file")

        reader = w.PublicEvidence(PINS, fetch=fetch)
        with self.assertRaises(w.WriterError) as caught:
            reader.read_file(REPO, SHA, "file.py")
        self.assertNotIn("synthetic", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(reader.examined_commits, [])

    def test_cross_repo_reads_require_an_explicit_pin(self):
        second = {"repository": "OpenHands/automation", "sha": "b" * 40}
        reader = w.PublicEvidence(PINS + [second], fetch=lambda url, limit: b"public code")
        reader.read_file(REPO, SHA, "api.py")
        reader.read_file(second["repository"], second["sha"], "dispatch.py")
        self.assertEqual(reader.examined_commits, sorted(PINS + [second], key=lambda x: (x["repository"], x["sha"])))


class WriterTests(unittest.TestCase):
    def test_unexpected_failure_exposes_only_safe_exception_types(self):
        failure = RuntimeError("Bearer synthetic-private-key https://private.invalid/path")
        with patch.object(w, "_run_agent", side_effect=failure):
            with self.assertRaisesRegex(w.WriterError, r"^writer_unexpected:RuntimeError$") as caught:
                w.write_note({}, llm=type("LLM", (), {"model": "test-model"})(), pinned_commits=PINS)
        self.assertNotIn("synthetic", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_disabling_sdk_tracing_does_not_touch_runtime_credentials(self):
        with patch.dict(os.environ, {"LMNR_PROJECT_API_KEY": "synthetic-trace-key",
             "OTEL_ENDPOINT": "https://traces.invalid", "OPENHANDS_API_KEY": "synthetic-runtime-key"}, clear=True):
            w.disable_sdk_tracing()
            self.assertNotIn("LMNR_PROJECT_API_KEY", os.environ)
            self.assertNotIn("OTEL_ENDPOINT", os.environ)
            self.assertEqual(os.environ["OTEL_SDK_DISABLED"], "true")
            self.assertEqual(os.environ["OPENHANDS_API_KEY"], "synthetic-runtime-key")

    def test_no_file_read_means_no_publishable_investigation(self):
        with patch.object(w, "_run_agent", return_value={"title": "Example", "summary": "Summary", "body_markdown": "Body", "tags": ["memory"]}):
            with self.assertRaises(w.WriterError):
                w.write_note({}, llm=type("LLM", (), {"model": "test-model"})(), pinned_commits=PINS)

    def test_returns_actual_model_and_only_examined_commits(self):
        generated = {"title": "Example", "summary": "Summary", "body_markdown": "Body", "tags": ["memory"]}

        def run(candidate, llm, reader):
            reader.read_file(REPO, SHA, "code.py")
            return generated

        with patch.object(w, "_run_agent", side_effect=run), patch.object(w, "fetch_public", return_value=b"source"):
            result = w.write_note({}, llm=type("LLM", (), {"model": "test-model"})(), pinned_commits=PINS)
        self.assertEqual(result, {"generated": generated, "writer_model": "test-model", "examined_commits": PINS})

    def test_generated_output_is_strict_json_object(self):
        obj = {"title": "Title", "summary": "Summary", "body_markdown": "Body", "tags": ["memory"]}
        self.assertEqual(w.parse_generated(json.dumps(obj)), obj)
        for value in ["```json\n{}\n```", "[]", "{}", json.dumps({**obj, "secret": "value"}), json.dumps({**obj, "tags": "memory"})]:
            with self.subTest(value=value):
                with self.assertRaises(w.WriterError):
                    w.parse_generated(value)


if __name__ == "__main__":
    unittest.main()
