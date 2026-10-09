"""Exercise collection, privacy filtering, rendering and one-file publication."""
import base64
import contextlib
import datetime
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "sources/mention-gazette"
spec = importlib.util.spec_from_file_location("render", SOURCE / "render.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
sys.modules["render"] = renderer
spec = importlib.util.spec_from_file_location("gazette_main", SOURCE / "main.py")
gazette = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gazette)


def notification(private=False, title="A public issue", kind="Issue"):
    return {"repository": {"full_name": "OpenHands/example", "private": private},
            "reason": "mention", "updated_at": "2026-10-09T08:00:00Z",
            "subject": {"title": title, "type": kind,
                        "url": "https://api.github.com/repos/OpenHands/example/issues/12"}}


class FakeGitHub:
    def __init__(self, rows):
        self.rows, self.writes = rows, []
        self.content = b"old page"
        self.failure = None

    def request(self, path, method="GET", data=None, raw=False):
        if path == "/user": return {"login": "enyst"}
        if path == "/repos/" + gazette.REPO:
            return {"full_name": gazette.REPO, "private": False, "permissions": {"push": True}}
        if path.startswith("/notifications"):
            if self.failure: raise gazette.GazetteError(self.failure)
            page = int(path.rsplit("=", 1)[1])
            return self.rows[(page-1)*100:page*100]
        if path == "/repos/OpenHands/example": return {"private": False, "visibility": "public"}
        if path.startswith(gazette.PAGE):
            if method == "PUT":
                self.writes.append((path, data))
                self.content = base64.b64decode(data["content"])
                return {"commit": {"sha": "a" * 40}}
            return {"encoding": "base64", "content": base64.b64encode(self.content).decode(), "sha": "b" * 40}
        raise AssertionError(path)


class GazetteTests(unittest.TestCase):
    def test_cloud_callback_uses_available_runtime_credential(self):
        env = {"AUTOMATION_CALLBACK_URL": "https://app.all-hands.dev/api/automation/v1/runs/1234/complete",
               "AUTOMATION_RUN_ID": "1234", "OPENHANDS_API_KEY": "synthetic-runtime-credential"}
        with patch.dict(gazette.os.environ, env, clear=True), patch.object(gazette, "API") as api:
            gazette.fire_callback()
            api.assert_called_once_with(gazette.CLOUD, "synthetic-runtime-credential")
            self.assertEqual(api.return_value.request.call_args.args[2]["status"], "COMPLETED")

    def test_full_run_publishes_only_public_escaped_data(self):
        gh = FakeGitHub([notification(title='<script>alert("x")</script>'),
                         notification(True, "PRIVATE TITLE"), notification(None, "UNKNOWN"),
                         notification(False, "SECURITY TITLE", "RepositoryVulnerabilityAlert")])
        with contextlib.redirect_stdout(io.StringIO()):
            result = gazette.main(gh, datetime.date(2026, 10, 9))
        self.assertTrue(result["changed"])
        self.assertEqual(len(gh.writes), 1)
        path, data = gh.writes[0]
        self.assertEqual(path, gazette.PAGE)
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["sha"], "b" * 40)
        self.assertNotIn(b"<script>", gh.content)
        self.assertIn(b"&lt;script&gt;", gh.content)
        for private in [b"PRIVATE TITLE", b"UNKNOWN", b"SECURITY TITLE"]:
            self.assertNotIn(private, gh.content)

    def test_notification_failure_never_publishes_empty_edition(self):
        gh = FakeGitHub([]); gh.failure = "http_401"
        with self.assertRaises(gazette.GazetteError): gazette.main(gh)
        self.assertEqual(gh.writes, [])

    def test_pagination_reads_beyond_first_hundred(self):
        gh = FakeGitHub([notification() for _ in range(101)])
        self.assertEqual(len(gazette.notifications(gh)), 101)

    def test_excessive_pagination_fails_without_publication(self):
        gh = FakeGitHub([notification() for _ in range(2000)])
        with self.assertRaisesRegex(gazette.GazetteError, "page_limit"):
            gazette.main(gh)
        self.assertEqual(gh.writes, [])

    def test_repeat_same_edition_does_not_commit(self):
        gh = FakeGitHub([])
        with contextlib.redirect_stdout(io.StringIO()):
            gazette.main(gh, datetime.date(2026, 10, 9))
            result = gazette.main(gh, datetime.date(2026, 10, 9))
        self.assertFalse(result["changed"])
        self.assertEqual(len(gh.writes), 1)

    def test_file_conflict_is_not_overwritten(self):
        gh = FakeGitHub([]); original = gh.request
        def conflict(path, method="GET", data=None, **kwargs):
            if method == "PUT": raise gazette.GazetteError("http_409")
            return original(path, method, data, **kwargs)
        gh.request = conflict
        with self.assertRaisesRegex(gazette.GazetteError, "http_409"):
            gazette.main(gh)
        self.assertEqual(gh.writes, [])

    def test_changed_visibility_and_untrusted_url(self):
        gh = FakeGitHub([]); original = gh.request
        gh.request = lambda path: {"private": True, "visibility": "private"}
        self.assertEqual(gazette.public_notifications(gh, [notification()]), [])
        gh.request = original
        row = notification(); row["subject"]["url"] = 'javascript:alert("x")'
        safe = gazette.public_notifications(gh, [row])
        self.assertEqual(safe[0]["subject"]["url"], "")


if __name__ == "__main__": unittest.main()
