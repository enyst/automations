"""Real loopback reference-peer qualification; never SDK candidate evidence.

This minimal test-only protocol server implements the approved test transcript
over actual HTTP and RFC6455. It qualifies the verifier's entire roster and its
ordering negative control, while the separate SDK smoke exercises production.
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

    def __init__(self, address, storage, fixture_url, invert=False):
        super().__init__(address, Handler)
        self.storage, self.fixture_url, self.invert = storage, fixture_url, invert
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
        if "/events/search" in self.path:
            return self.reply(200, {"items": list(self.server.events), "next_page_id": None})
        if self.path.startswith("/api/conversations/"):
            return self.reply(200, {"execution_status": "finished"})
        self.reply(404, {})

    def websocket(self):
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
                for seq in range(after + 1, len(self.server.events)):
                    send_frame(connection, {"type": "durable", "seq": seq, "event": self.server.events[seq]})
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
                send_frame(connection, {"type": "error", "code": "ValidationError", "detail": "reference invalid role"})
        except (OSError, EOFError):
            pass
        finally:
            with self.server.lock:
                if connection in self.server.subscribers:
                    self.server.subscribers.remove(connection)


class ReferenceQualification(unittest.TestCase):
    def qualify(self, invert):
        with tempfile.TemporaryDirectory() as work:
            peer = fixture.FixtureServer(("127.0.0.1", 0))
            threading.Thread(target=peer.serve_forever, daemon=True).start()
            peer_url = "http://127.0.0.1:" + str(peer.server_port)
            server = ReferenceServer(("127.0.0.1", 0), Path(work) / "history.json", peer_url, invert)
            target_port = server.server_port
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers = [server]
            def restart():
                servers[-1].shutdown()
                servers[-1].server_close()
                replacement = ReferenceServer(("127.0.0.1", target_port), Path(work) / "history.json", peer_url, invert)
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
        result = self.qualify(False)
        self.assertEqual(result["verdict"], "pass", result["scenarios"])
        self.assertTrue(all(s["status"] == "pass" for s in result["scenarios"]))

    def test_live_inversion_negative_reference(self):
        result = self.qualify(True)
        self.assertEqual(result["verdict"], "fail", result["scenarios"])
        self.assertIn("live_cursor_gap_or_duplicate", result["scenarios"][0]["details"]["code"])
        self.assertTrue(result["restart_observed"]["replay_frames_equal"])

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
