import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch, MagicMock

SOURCE = Path(__file__).parents[1] / "sources/sdk-lifecycle-conformance"
SPEC = importlib.util.spec_from_file_location("sdk_lifecycle_verifier", SOURCE / "main.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class ScriptedSocket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False

    async def recv(self):
        return json.dumps(next(self.frames))

    async def close(self):
        self.closed = True


class OracleTests(unittest.TestCase):
    def test_unconfigured_is_blocked_with_complete_roster(self):
        result = verifier.run(json.loads((SOURCE / "config.json").read_text()))
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(len(result["scenarios"]), 3)
        self.assertTrue(all(item["status"] == "blocked" for item in result["scenarios"]))

    def test_replay_negative_controls(self):
        config = {"timeout_seconds": 5}
        for frame in ({"type": "durable", "seq": 2, "event": {"id": "e"}},
                      {"type": "durable", "seq": 0, "event": {"id": "e"}},
                      {"type": "transient", "seq": 1, "event": {"id": "e"}}):
            socket = ScriptedSocket([{"type": "sync", "from_seq": 0, "through_seq": 1}, frame])
            async def connect(*args):
                return socket
            with self.subTest(frame=frame), patch.object(verifier, "connect", connect):
                with self.assertRaises(verifier.Violation):
                    asyncio.run(verifier.replay(config, "conversation", 0))
                self.assertTrue(socket.closed)

    def test_rest_payload_change_rejected(self):
        with self.assertRaisesRegex(verifier.Violation, "payload_mismatch"):
            verifier.assert_history([{"seq": 0, "event": {"id": "e", "body": "changed"}}], [{"id": "e", "body": "original"}])

    def test_rest_order_is_checked_against_replayed_order(self):
        frames = [
            {"seq": 0, "event": {"id": "one"}},
            {"seq": 1, "event": {"id": "two"}},
        ]
        rows = [{"id": "two"}, {"id": "one"}]
        with self.assertRaisesRegex(verifier.Violation, "durable_history_order_mismatch"):
            verifier.assert_history(frames, rows)

    def test_omitted_event_and_duplicate_cursor_rejected(self):
        for frames in ([], [{"seq": 1, "event": {"id": "e"}}]):
            with self.assertRaises(verifier.Violation):
                verifier.assert_history(frames, [{"id": "e"}])

    def test_non_durable_cursor_rejected(self):
        ws = ScriptedSocket([{"type": "transient", "seq": 8}])
        with self.assertRaisesRegex(verifier.Violation, "non_durable_cursor"):
            asyncio.run(verifier.next_kind(ws, "durable", 5))

    def test_live_history_race_blocks_instead_of_false_failure(self):
        first = {"id": "old", "source": "agent"}
        added = {"id": "new", "source": "user", "text": "live continuation"}
        late = {"id": "late", "source": "environment"}

        async def durable(*args):
            return {"type": "durable", "seq": 1, "event": added}

        with (
            patch.object(
                verifier,
                "history",
                side_effect=[[first], [first, added], [first, added, late]],
            ),
            patch.object(verifier, "next_kind", durable),
        ):
            with self.assertRaisesRegex(
                verifier.Blocked, "live_history_changed_during_collection"
            ):
                asyncio.run(
                    verifier.live_message(
                        {"timeout_seconds": 5},
                        MagicMock(),
                        "conversation",
                        object(),
                        "live continuation",
                        0,
                    )
                )

    def test_cloud_does_not_demand_unused_restart_secret(self):
        with (
            patch.dict(
                os.environ,
                {
                    "SANDBOX_ID": "synthetic",
                    "SESSION_API_KEY": "synthetic-session-key",
                    "CONFORMANCE_CANDIDATE_SESSION_KEY": "synthetic-candidate-key",
                    "CONFORMANCE_FIXTURE_CONTROL_KEY": "synthetic-fixture-key",
                },
                clear=True,
            ),
            patch("urllib.request.build_opener", side_effect=AssertionError("unexpected fetch")),
        ):
            verifier.load_cloud_credentials({"restart_control_url": ""})

    def test_run_deadline_preserves_blocked_roster(self):
        async def slow(*args):
            await asyncio.sleep(0.2)

        config = {
            "candidate_url": "http://127.0.0.1:33333",
            "fixture_url": "http://127.0.0.1:33334",
            "candidate_revision": "a" * 40,
            "candidate_artifact_sha256": "b" * 64,
            "candidate_binding_receipt": "synthetic-test",
            "working_dir": "/tmp",
            "timeout_seconds": 5,
        }
        with (
            patch.object(verifier, "VERIFIER_BUDGET_SECONDS", 0.01),
            patch.object(verifier, "load_cloud_credentials"),
            patch.object(verifier, "lifecycle", slow),
        ):
            result = verifier.run(config)
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(len(result["scenarios"]), 3)
        self.assertTrue(
            all(
                item["details"]["code"] == "verifier_deadline_exceeded"
                for item in result["scenarios"]
            )
        )

    def test_callback_failure_does_not_change_verdict(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as work:
            config = Path(work) / "config.json"
            config.write_text("{}")
            evidence = Path(work) / "evidence.json"
            for verdict, exit_code in (("pass", 0), ("fail", 1), ("blocked", 2)):
                report = {
                    "subject": {},
                    "bundle": {},
                    "scenarios": [],
                    "verdict": verdict,
                }
                with (
                    patch.object(verifier, "run", return_value=report),
                    patch.object(
                        verifier,
                        "callback",
                        side_effect=verifier.Violation("callback_422"),
                    ),
                    patch.object(
                        sys,
                        "argv",
                        [
                            "verifier",
                            "--config",
                            str(config),
                            "--evidence",
                            str(evidence),
                        ],
                    ),
                ):
                    self.assertEqual(verifier.main(), exit_code)
                self.assertEqual(json.loads(evidence.read_text())["verdict"], verdict)

    def test_secrets_are_redacted_from_nested_evidence(self):
        with patch.dict(os.environ, {"OPENHANDS_API_KEY": "synthetic-test-account-bearer", "OH_SESSION_API_KEYS_0": "synthetic-fallback-key"}):
            value = verifier.redact({"events": [{"text": "synthetic-test-account-bearer"}], "synthetic-fallback-key": "synthetic-fallback-key"})
        self.assertEqual(value["events"][0]["text"], "[REDACTED]")
        self.assertEqual(value["[REDACTED]"], "[REDACTED]")

    def test_credential_url_and_remote_cleartext_rejected(self):
        for value in ("https://token@example.org", "https://example.org?token=x", "http://example.org", "https://example.org/path"):
            with self.subTest(value=value), self.assertRaises(verifier.Blocked):
                verifier.valid_url(value)


class RedirectTests(unittest.TestCase):
    def test_http_and_ws_redirect_do_not_deliver_target_key(self):
        deliveries = []
        class Receiver(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def do_GET(self):
                deliveries.append(self.headers.get("X-Session-API-Key"))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
        receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        receiver_url = "http://127.0.0.1:" + str(receiver.server_port)
        class Redirector(Receiver):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", receiver_url + "/target")
                self.send_header("Content-Length", "0")
                self.end_headers()
        redirector = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        redirector_url = "http://127.0.0.1:" + str(redirector.server_port)
        for server in (receiver, redirector):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(verifier.Blocked):
                verifier.HTTP(redirector_url, "X-Session-API-Key", "synthetic-target-key").request("GET", "/redirect")
            with patch.dict(os.environ, {"CONFORMANCE_CANDIDATE_SESSION_KEY": "synthetic-target-key"}):
                with self.assertRaises(verifier.Blocked):
                    asyncio.run(verifier.connect({"candidate_url": redirector_url}, "conversation", "/redirect"))
            self.assertEqual(deliveries, [])
        finally:
            for server in (receiver, redirector):
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
