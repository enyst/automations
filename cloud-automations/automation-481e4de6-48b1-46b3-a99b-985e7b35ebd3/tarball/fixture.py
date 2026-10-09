"""Bounded external OpenAI-compatible scripted peer; never imports candidate code.

Run on a dedicated fixture endpoint reachable from the candidate. The control
plane must be private to the verifier; candidate access is only to /v1/*.
The local demonstration serves both on loopback without credentials and is not
an isolation boundary for malicious code.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import re
import threading
import time
import urllib.parse

FINAL_TEXT = "conformance fixture completed"


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, control_key=""):
        super().__init__(address, Handler)
        self.control_key = control_key
        self.calls = 0
        self.nonce_calls = {}
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != "/control/requests":
            return self.reply(404, {})
        if self.server.control_key and self.headers.get("X-Fixture-Control-Key") != self.server.control_key:
            return self.reply(401, {})
        with self.server.lock:
            nonce_hash = urllib.parse.parse_qs(parts.query).get("nonce_sha256", [""])[0]
            if nonce_hash and not re.fullmatch(r"[0-9a-f]{64}", nonce_hash):
                return self.reply(400, {})
            self.reply(200, {"calls": self.server.calls, "nonce_calls": self.server.nonce_calls.get(nonce_hash, 0)})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self.reply(404, {})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                return self.reply(413, {})
            request = json.loads(self.rfile.read(length))
            if request.get("stream") or not isinstance(request.get("messages"), list):
                return self.reply(400, {"error": {"message": "fixture requires nonstream chat completions"}})
            with self.server.lock:
                self.server.calls += 1
                call = self.server.calls
                # Retain only bounded nonce hashes/counters, never raw request
                # bodies, user messages, provider credentials or prompt text.
                for nonce in set(re.findall(r"complete conformance ([0-9a-f]{32})", json.dumps(request["messages"]))):
                    nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
                    self.server.nonce_calls[nonce_hash] = self.server.nonce_calls.get(nonce_hash, 0) + 1
                while len(self.server.nonce_calls) > 256:
                    del self.server.nonce_calls[next(iter(self.server.nonce_calls))]
            self.reply(200, {
                "id": "conformance-completion-" + str(call),
                "object": "chat.completion", "created": int(time.time()),
                "model": request.get("model", "gpt-4o-mini"),
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "conformance-finish-" + str(call), "type": "function",
                                    "function": {"name": "finish", "arguments": json.dumps({"message": FINAL_TEXT})}}]
                }}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            })
        except (ValueError, TypeError):
            self.reply(400, {})


if __name__ == "__main__":
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    FixtureServer((args.host, args.port), os.environ.get("CONFORMANCE_FIXTURE_CONTROL_KEY", "")).serve_forever()
