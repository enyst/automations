"""Real loopback reference-peer qualification; never SDK candidate evidence.

This minimal test-only protocol server implements the test transcript over actual
HTTP and RFC6455. It qualifies recovery when live frames are missed or reordered
and rejects broken replay, while the separate SDK smoke exercises production.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.request
import uuid
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "sources/sdk-lifecycle-conformance"
sys.path.insert(0, str(SOURCE))
import fixture
import main as verifier


def send_frame(connection, payload, opcode=1):
    data = json.dumps(payload).encode() if opcode == 1 else payload
    header = bytes([0x80 | opcode])
    size = len(data)
    header += bytes([size]) if size < 126 else b"\x7e" + struct.pack("!H", size)
    connection.sendall(header + data)


def read_exact(connection, size):
    result = b""
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise EOFError
        result += chunk
    return result


class ReferenceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, storage, fixture_url, invert=False, lost=False,
                 drop_live=False, replay_fault=None, replay_fault_after=3,
                 persist_invalid=False, reject_replay_after=None):
        super().__init__(address, Handler)
        self.storage, self.fixture_url, self.invert, self.lost = storage, fixture_url, invert, lost
        self.drop_live, self.replay_fault = drop_live, replay_fault
        self.replay_fault_after = replay_fault_after
        self.persist_invalid = persist_invalid
        self.reject_replay_after = reject_replay_after
        self.events = json.loads(storage.read_text()) if storage.exists() else []
        self.lock = threading.RLock()
        self.subscribers = []

    def append(self, events):
        with self.lock:
            start = len(self.events)
            self.events.extend(events)
            self.storage.write_text(json.dumps(self.events))
            frames = [{"type": "durable", "seq": start + i, "event": e} for i, e in enumerate(events)]
            if self.invert and len(frames) == 2:
                frames.reverse()
            if self.drop_live:
                frames = []
            for subscriber in list(self.subscribers):
                try:
                    for frame in frames:
                        send_frame(subscriber, frame)
                except OSError:
                    self.subscribers.remove(subscriber)


def event(kind, **fields):
    return dict(id=str(uuid.uuid4()), kind=kind, **fields)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args):
        pass

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(size)) if size else {}
        if self.path == "/api/conversations":
            self.server.append([event("SystemPromptEvent", source="agent", text="reference fixture")])
            return self.reply(201, {"id": "00000000-0000-4000-8000-000000000001"})
        if self.path.endswith("/events"):
            self.server.append([event("MessageEvent", source="user", llm_message=data),
                                event("ConversationStateUpdateEvent", source="environment", key="last_user_message_id", value="reference")])
            return self.reply(200, {"success": True})
        if self.path.endswith("/run"):
            user = next(e for e in reversed(self.server.events) if e.get("source") == "user")
            request = urllib.request.Request(self.server.fixture_url + "/v1/chat/completions", method="POST",
                                             data=json.dumps({"messages": [{"role": "user", "content": user["llm_message"]["content"]}], "stream": False}).encode(),
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                completion = json.load(response)
            action = json.loads(completion["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
            self.server.append([event("ActionEvent", source="agent", action=dict(kind="FinishAction", **action))])
            return self.reply(200, {"success": True})
        self.reply(404, {})

    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self.websocket()
        if self.path == "/ready":
            return self.reply(200, {"status": "ready"})
        if self.server.lost and self.path.startswith("/api/conversations/"):
            return self.reply(404, {})
        if "/events/search" in self.path:
            return self.reply(200, {"items": list(self.server.events), "next_page_id": None})
        if self.path.startswith("/api/conversations/"):
            return self.reply(200, {"execution_status": "finished"})
        self.reply(404, {})

    def websocket(self):
        if "/session/" in self.path and self.server.reject_replay_after is not None:
            after = int(self.path.split("after_seq=")[1])
            if after == self.server.reject_replay_after:
                return self.reply(404, {})
        key = self.headers["Sec-WebSocket-Key"]
        accepted = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accepted)
        self.end_headers()
        connection = self.connection
        with self.server.lock:
            if "/session/" in self.path:
                after = int(self.path.split("after_seq=")[1])
                send_frame(connection, {"type": "sync", "from_seq": after, "through_seq": len(self.server.events) - 1})
                replay = [{"type": "durable", "seq": seq, "event": self.server.events[seq]}
                          for seq in range(after + 1, len(self.server.events))]
                # Initial full replay ends at 3. Fault only a chosen
                # continuation recovery cursor in negative tests.
                if after == self.server.replay_fault_after and self.server.replay_fault and len(replay) >= 2:
                    if self.server.replay_fault == "gap":
                        replay.pop(0)
                    elif self.server.replay_fault == "duplicate":
                        replay[1]["seq"] = replay[0]["seq"]
                    elif self.server.replay_fault == "payload":
                        replay[0]["event"] = dict(replay[0]["event"], text="corrupt")
                for frame in replay:
                    send_frame(connection, frame)
                send_frame(connection, {"type": "transient", "event": event("ConversationStateUpdateEvent", key="full_state")})
                self.server.subscribers.append(connection)
            else:
                send_frame(connection, event("ConversationStateUpdateEvent", key="full_state"))
                for item in self.server.events:
                    send_frame(connection, item)
        try:
            while True:
                first, second = read_exact(connection, 2)
                length = second & 127
                if length == 126:
                    length = struct.unpack("!H", read_exact(connection, 2))[0]
                mask = read_exact(connection, 4)
                body = read_exact(connection, length)
                body = bytes(value ^ mask[i % 4] for i, value in enumerate(body))
                if first & 15 == 8:
                    send_frame(connection, body, opcode=8)
                    return
                if self.server.persist_invalid:
                    self.server.append([event("MessageEvent", source="user", llm_message=json.loads(body))])
                send_frame(connection, {"type": "error", "code": "ValidationError", "detail": "reference invalid role"})
        except (OSError, EOFError):
            pass
        finally:
            with self.server.lock:
                if connection in self.server.subscribers:
                    self.server.subscribers.remove(connection)


class ReferenceQualification(unittest.TestCase):
    def qualify(self, invert=False, lose_on_restart=False, drop_live=False,
                replay_fault=None, replay_fault_after=3, persist_invalid=False,
                reject_replay_after=None):
        with tempfile.TemporaryDirectory() as work:
            peer = fixture.FixtureServer(("127.0.0.1", 0))
            threading.Thread(target=peer.serve_forever, daemon=True).start()
            peer_url = "http://127.0.0.1:" + str(peer.server_port)
            server = ReferenceServer(("127.0.0.1", 0), Path(work) / "history.json", peer_url,
                                     invert, drop_live=drop_live, replay_fault=replay_fault,
                                     replay_fault_after=replay_fault_after,
                                     persist_invalid=persist_invalid,
                                     reject_replay_after=reject_replay_after)
            target_port = server.server_port
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers = [server]
            def restart():
                servers[-1].shutdown()
                servers[-1].server_close()
                if lose_on_restart:
                    (Path(work) / "history.json").unlink(missing_ok=True)
                replacement = ReferenceServer(("127.0.0.1", target_port), Path(work) / "history.json",
                                              peer_url, invert, lost=lose_on_restart,
                                              drop_live=drop_live, replay_fault=replay_fault,
                                              replay_fault_after=replay_fault_after,
                                              persist_invalid=persist_invalid,
                                              reject_replay_after=reject_replay_after)
                servers.append(replacement)
                threading.Thread(target=replacement.serve_forever, daemon=True).start()
                return {"restarted": True, "storage_preserved": True}
            config = {"candidate_url": "http://127.0.0.1:" + str(target_port), "candidate_revision": "a" * 40,
                      "candidate_artifact_sha256": "b" * 64, "candidate_binding_receipt": "test-reference-unattested",
                      "fixture_url": peer_url, "working_dir": work, "timeout_seconds": 5}
            try:
                with patch.dict(os.environ, {"SANDBOX_ID": "", "AGENT_SERVER_URL": ""}):
                    return verifier.run(config, restart=restart)
            finally:
                servers[-1].shutdown()
                for item in servers:
                    item.server_close()
                peer.shutdown()
                peer.server_close()

    def test_full_roster_positive_reference(self):
        result = self.qualify()
        self.assertEqual(result["verdict"], "pass", result["scenarios"])
        self.assertTrue(all(s["status"] == "pass" for s in result["scenarios"]))
        self.assertTrue(result["restart_observed"]["post_restart_replay_equal"])
        self.assertEqual(result["restart_observed"]["post_restart_replayed_events"], 2)

    def test_restart_lost_conversation_is_failed_observation(self):
        result = self.qualify(lose_on_restart=True)
        self.assertEqual(result["verdict"], "fail", result["scenarios"])
        self.assertEqual(result["scenarios"][0]["status"], "pass")
        self.assertEqual(result["scenarios"][1]["status"], "fail")
        self.assertEqual(result["scenarios"][1]["details"]["code"], "restart_conversation_lost")
        self.assertEqual(result["scenarios"][2]["status"], "fail")

    def test_live_inversion_recovers_from_replay(self):
        result = self.qualify(invert=True)
        self.assertEqual(result["verdict"], "pass", result["scenarios"])
        self.assertTrue(result["restart_observed"]["post_restart_replay_equal"])

    def test_missing_live_frames_recover_from_replay(self):
        result = self.qualify(drop_live=True)
        self.assertEqual(result["verdict"], "pass", result["scenarios"])
        self.assertTrue(result["restart_observed"]["post_restart_replay_equal"])

    def test_continuation_replay_faults_fail(self):
        for fault in ("gap", "duplicate", "payload"):
            with self.subTest(fault=fault):
                result = self.qualify(replay_fault=fault)
                self.assertEqual(result["verdict"], "fail", result["scenarios"])
                self.assertEqual(result["scenarios"][0]["status"], "fail")
                self.assertIn(result["scenarios"][0]["details"]["code"],
                              {"replay_suffix_gap_duplicate_or_progress",
                               "replayed_continuation_history_mismatch"})

    def test_post_restart_replay_fault_fails_restart_scenario(self):
        result = self.qualify(replay_fault="payload", replay_fault_after=5)
        self.assertEqual(result["verdict"], "fail", result["scenarios"])
        self.assertEqual(result["scenarios"][0]["status"], "pass")
        self.assertEqual(result["scenarios"][1]["status"], "fail")
        self.assertEqual(result["scenarios"][1]["details"]["code"],
                         "replayed_continuation_history_mismatch")

    def test_invalid_socket_message_persistence_fails(self):
        result = self.qualify(persist_invalid=True)
        self.assertEqual(result["verdict"], "fail", result["scenarios"])
        self.assertEqual(result["scenarios"][0]["status"], "fail")
        self.assertEqual(result["scenarios"][0]["details"]["code"], "socket_error_persisted")

    def test_observed_post_restart_session_socket_404_fails(self):
        result = self.qualify(reject_replay_after=5)
        self.assertEqual(result["verdict"], "fail", result["scenarios"])
        self.assertEqual(result["scenarios"][0]["status"], "pass")
        self.assertEqual(result["scenarios"][1]["status"], "fail")
        self.assertEqual(result["scenarios"][1]["details"]["code"],
                         "observed_session_socket_missing")

    def test_unrelated_provider_traffic_does_not_qualify_run(self):
        peer = fixture.FixtureServer(("127.0.0.1", 0))
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:" + str(peer.server_port)
        nonce = "1" * 32
        witness = hashlib.sha256(nonce.encode()).hexdigest()
        try:
            request = urllib.request.Request(url + "/v1/chat/completions", method="POST", data=json.dumps({"messages": [{"role": "user", "content": "complete conformance " + "2" * 32}]}).encode())
            with urllib.request.urlopen(request) as response:
                response.read()
            observed = verifier.HTTP(url).request("GET", "/control/requests?nonce_sha256=" + witness)
            self.assertEqual(observed["calls"], 1)
            self.assertEqual(observed["nonce_calls"], 0)
        finally:
            peer.shutdown()
            peer.server_close()


if __name__ == "__main__":
    unittest.main()
