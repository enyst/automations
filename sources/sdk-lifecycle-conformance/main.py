"""Independent REST/WS verifier. Exit 0 = pass; 1 = fail; 2 = blocked.

Only this trusted program assigns verdicts. It imports no OpenHands package,
loads no candidate test/config/plugin, and executes no candidate shell command.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
MAX_BODY = 4 * 1024 * 1024
BUNDLE_FILES = ("main.py", "fixture.py", "contracts.json", "requirements.txt", "setup.sh")
OBSERVATIONS = []
OBSERVATION_BYTES = 0


class Blocked(Exception):
    pass


class Violation(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Violation(code)


def valid_url(value, *, allow_path=False):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Blocked("invalid_endpoint")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise Blocked("remote_endpoint_requires_https")
    if not allow_path and parsed.path.rstrip("/"):
        raise Blocked("endpoint_must_be_origin")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTP:
    def __init__(self, origin, header=None, key="", timeout=20):
        self.origin = valid_url(origin)
        self.header, self.key, self.timeout = header, key, timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None, expected=200):
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise Blocked("invalid_request_path")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.header and self.key:
            headers[self.header] = self.key
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = urllib.request.Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in (401, 403, 404, 429, 502, 503, 504) or 300 <= status < 400:
                raise Blocked("http_unavailable_" + str(status)) from None
            raise Violation("http_unexpected_" + str(status)) from None
        except (OSError, TimeoutError, urllib.error.URLError):
            raise Blocked("endpoint_unavailable") from None
        require(status == expected, "unexpected_http_status")
        require(len(raw) <= MAX_BODY, "response_budget_exceeded")
        try:
            return json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            raise Violation("invalid_json_response") from None


def bundle_digest():
    digest = hashlib.sha256()
    for name in BUNDLE_FILES:
        data = (ROOT / name).read_bytes()
        digest.update(name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def history(http, identifier):
    rows, seen = [], set()
    page_id = None
    for _ in range(20):
        path = f"/api/conversations/{identifier}/events/search?limit=100"
        if page_id:
            path += "&page_id=" + urllib.parse.quote(page_id, safe="")
        page = http.request("GET", path)
        require(isinstance(page, dict) and isinstance(page.get("items"), list), "invalid_history_page")
        rows.extend(page["items"])
        page_id = page.get("next_page_id")
        if not page_id:
            require(len({row.get("id") for row in rows}) == len(rows), "duplicate_rest_event_id")
            return rows
        require(isinstance(page_id, str) and page_id not in seen, "invalid_history_cursor")
        seen.add(page_id)
    raise Violation("history_page_budget_exceeded")


def assert_history(frames, rows):
    require(len(frames) == len(rows), "durable_history_length_mismatch")
    require([f.get("seq") for f in frames] == list(range(len(rows))), "durable_sequence_gap_or_duplicate")
    wire = {f["event"]["id"]: f["event"] for f in frames}
    rest = {row["id"]: row for row in rows}
    require(wire == rest, "durable_history_payload_mismatch")


async def connect(config, identifier, path):
    try:
        from websockets.asyncio.client import connect as ws_connect
    except ImportError:
        raise Blocked("websockets_dependency_missing") from None
    origin = urllib.parse.urlsplit(config["candidate_url"])
    scheme = "wss" if origin.scheme == "https" else "ws"
    url = urllib.parse.urlunsplit((scheme, origin.netloc, path, "", ""))
    headers = {"X-Session-API-Key": os.environ.get("CONFORMANCE_CANDIDATE_SESSION_KEY", "")}
    # websockets follows redirects by default; reject before a key can leave
    # the configured target. Candidate-controlled redirects cannot choose a host.
    class ExactOriginConnection(ws_connect):
        def process_redirect(self, exc):
            return exc
    try:
        return await ExactOriginConnection(url, additional_headers=headers, open_timeout=10, close_timeout=2, max_size=MAX_BODY, max_queue=32, proxy=None)
    except Exception:
        raise Blocked("websocket_unavailable") from None


async def recv(ws, timeout):
    global OBSERVATION_BYTES
    try:
        data = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except asyncio.TimeoutError:
        raise Violation("required_ws_frame_timeout") from None
    except Exception:
        raise Violation("unexpected_ws_disconnect") from None
    try:
        frame = json.loads(data)
    except (ValueError, TypeError):
        raise Violation("invalid_ws_json") from None
    require(isinstance(frame, dict), "invalid_ws_frame")
    OBSERVATION_BYTES += len(data.encode() if isinstance(data, str) else data)
    require(OBSERVATION_BYTES <= 16 * 1024 * 1024, "trace_budget_exceeded")
    OBSERVATIONS.append(frame)
    return frame


async def next_kind(ws, kind, timeout):
    deadline = time.monotonic() + timeout
    for _ in range(64):
        frame = await recv(ws, max(0.01, deadline - time.monotonic()))
        if frame.get("type") == kind:
            return frame
        require(frame.get("type") in ("transient", "item_started", "delta", "item_aborted") and "seq" not in frame, "unexpected_frame_or_non_durable_cursor")
    raise Violation("non_durable_frame_budget_exceeded")


async def replay(config, identifier, after):
    ws = await connect(config, identifier, f"/sockets/session/{identifier}?after_seq={after}")
    try:
        sync = await recv(ws, config["timeout_seconds"])
        require(sync.get("type") == "sync" and sync.get("from_seq") == after, "invalid_sync")
        through = sync.get("through_seq")
        require(through is None or type(through) is int, "invalid_sync_upper_bound")
        require(through is None or -1 <= through <= 2000, "replay_frame_budget_exceeded")
        frames = []
        for seq in range(after + 1, (through if through is not None else -1) + 1):
            frame = await recv(ws, config["timeout_seconds"])
            require(frame.get("type") == "durable" and frame.get("seq") == seq, "replay_suffix_gap_duplicate_or_progress")
            require(isinstance(frame.get("event"), dict), "invalid_durable_event")
            frames.append(frame)
        return ws, frames, through
    except BaseException:
        await ws.close()
        raise


async def live_message(config, http, identifier, ws, text, previous):
    before = len(history(http, identifier))
    http.request("POST", f"/api/conversations/{identifier}/events", {"role": "user", "content": [{"type": "text", "text": text}], "run": False})
    rows = history(http, identifier)
    # Sending after FINISHED can also persist an IDLE state transition.
    # Observe all committed events; do not assume one request means one event.
    require(before < len(rows) <= before + 8, "live_message_event_budget_exceeded")
    frames = []
    for index in range(len(rows) - before):
        frame = await next_kind(ws, "durable", config["timeout_seconds"])
        frames.append(frame)
    for index, frame in enumerate(frames):
        require(frame.get("seq") == previous + index + 1, "live_cursor_gap_or_duplicate:expected=" + str(previous + index + 1) + ":actual=" + str(frame.get("seq")))
    require(sum(f.get("event", {}).get("source") == "user" and text in json.dumps(f["event"]) for f in frames) == 1, "live_message_not_persisted_once")
    rest = {r["id"]: r for r in rows}
    require(all(rest.get(f["event"]["id"]) == f["event"] for f in frames), "live_rest_payload_mismatch")
    return frames


async def lifecycle(config, http, fixture, evidence, restart=None):
    identifier = None
    results = evidence["scenarios"]
    try:
        http.request("GET", "/ready")
        nonce = uuid.uuid4().hex
        nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
        witness_path = "/control/requests?nonce_sha256=" + nonce_hash
        calls_before = fixture.request("GET", witness_path)["nonce_calls"]
        create = http.request("POST", "/api/conversations", {
            "agent": {"llm": {"model": "openai/gpt-4o-mini", "api_key": "synthetic-conformance-key", "base_url": config["fixture_url"] + "/v1", "stream": False}, "tools": []},
            "workspace": {"working_dir": config["working_dir"]},
            "max_iterations": 4
        }, expected=201)
        identifier = str(uuid.UUID(create["id"]))
        evidence["conversation_id"] = identifier
        http.request("POST", f"/api/conversations/{identifier}/events", {"role": "user", "content": [{"type": "text", "text": "complete conformance " + nonce}], "run": False})
        http.request("POST", f"/api/conversations/{identifier}/run")
        deadline = time.monotonic() + config["timeout_seconds"]
        while time.monotonic() < deadline:
            info = http.request("GET", f"/api/conversations/{identifier}")
            rows = history(http, identifier)
            if "conformance fixture completed" in json.dumps(rows) and info.get("execution_status") != "running":
                break
            await asyncio.sleep(0.1)
        else:
            raise Violation("scripted_run_did_not_complete")
        require(fixture.request("GET", witness_path)["nonce_calls"] > calls_before, "scripted_provider_not_reached_for_run_nonce")
        evidence["provider_nonce_sha256"] = nonce_hash
        ws, frames, through = await replay(config, identifier, -1)
        try:
            assert_history(frames, rows)
            # Deliberately invalid inbound message exercises a real non-durable
            # error frame. Processing acknowledgment avoids time-based guessing.
            await ws.send('{"role":"invalid-role","content":[]}')
            error = await next_kind(ws, "error", config["timeout_seconds"])
            evidence["error_frame_observed"] = error
            require(error.get("type") == "error" and "seq" not in error, "socket_error_advanced_durable_cursor")
            require(history(http, identifier) == rows, "socket_error_persisted")
        finally:
            await ws.close()
        cursor = frames[len(frames) // 2]["seq"]
        ws, suffix, upper = await replay(config, identifier, cursor)
        try:
            require(suffix == [f for f in frames if f["seq"] > cursor], "reconnect_suffix_mismatch")
            require(upper == through, "reconnect_upper_bound_changed")
            live = await live_message(config, http, identifier, ws, "live continuation " + nonce, through)
            frames.extend(live)
        finally:
            await ws.close()
        rows = history(http, identifier)
        ws, all_frames, _ = await replay(config, identifier, -1)
        await ws.close()
        require(all_frames == frames, "reconnect_full_history_mismatch")
        assert_history(frames, rows)
        results[0].update(status="pass", details={"durable_events": len(frames), "reconnect_after_seq": cursor, "provider_calls_observed": True, "socket_error_non_durable": True})
        evidence["observations"] = {"frames": frames, "rest_history": rows}
    except (Blocked, Violation) as exc:
        results[0].update(status="blocked" if isinstance(exc, Blocked) else "fail", details={"code": str(exc)})
    except Exception:
        results[0].update(status="blocked", details={"code": "unexpected_harness_error"})

    # Restart and legacy are independent obligations. An observed live-order
    # failure must not prevent us from exercising them against the same real
    # conversation. Obtain a fresh quiescent history over the public API.
    if identifier is None:
        return
    try:
        rows = history(http, identifier)
        ws, frames, _ = await replay(config, identifier, -1)
        await ws.close()
        assert_history(frames, rows)
        evidence["restart_baseline"] = {"frames": frames, "rest_history": rows}
    except (Blocked, Violation):
        return

    try:
        if restart is None:
            raise Blocked("independent_restart_control_missing")
        receipt = restart()
        require(isinstance(receipt, dict) and receipt.get("restarted") is True and receipt.get("storage_preserved") is True, "restart_not_confirmed")
        deadline = time.monotonic() + config["timeout_seconds"]
        while True:
            try:
                http.request("GET", "/ready")
                break
            except Blocked:
                if time.monotonic() >= deadline:
                    raise Blocked("restart_endpoint_unavailable") from None
                await asyncio.sleep(0.2)
        require(history(http, identifier) == rows, "restart_rest_history_changed")
        ws, restored, upper = await replay(config, identifier, -1)
        try:
            require(restored == frames, "restart_durable_history_changed")
            evidence["restart_observed"] = {"storage_preserved": True, "rest_history_equal": True,
                                             "replay_frames_equal": True, "durable_events_restored": len(restored)}
            await live_message(config, http, identifier, ws, "post-restart continuation", upper)
        finally:
            await ws.close()
        results[1].update(status="pass", details={"durable_events_restored": len(restored), "storage_preserved": True, "continued_after_restart": True})
    except (Blocked, Violation) as exc:
        results[1].update(status="blocked" if isinstance(exc, Blocked) else "fail", details={"code": str(exc)})
    except Exception:
        results[1].update(status="blocked", details={"code": "unexpected_harness_error"})

    try:
        rows = history(http, identifier)
        ws = await connect(config, identifier, f"/sockets/events/{identifier}?resend_mode=all")
        try:
            legacy = []
            snapshots = 0
            for _ in range(len(rows) + 1):
                event = await recv(ws, config["timeout_seconds"])
                if event.get("kind") == "ConversationStateUpdateEvent" and event.get("key") == "full_state":
                    snapshots += 1
                    require(snapshots == 1, "duplicate_legacy_snapshot")
                    continue
                legacy.append(event)
                if len(legacy) == len(rows):
                    break
            require({e.get("id"): e for e in legacy} == {e.get("id"): e for e in rows} and len({e.get("id") for e in legacy}) == len(rows), "legacy_history_mismatch")
            require(all("seq" not in e and "event" not in e for e in legacy), "legacy_event_envelope_changed")
        finally:
            await ws.close()
        results[2].update(status="pass", details={"persisted_events": len(rows), "scope": "legacy transport smoke only"})
    except (Blocked, Violation) as exc:
        results[2].update(status="blocked" if isinstance(exc, Blocked) else "fail", details={"code": str(exc)})
    except Exception:
        results[2].update(status="blocked", details={"code": "unexpected_harness_error"})


def validate_config(config):
    allowed = {"candidate_url", "candidate_revision", "candidate_artifact_sha256", "candidate_binding_receipt",
               "fixture_url", "restart_control_url", "working_dir", "timeout_seconds"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise Blocked("unsupported_configuration_fields")
    for key in ("candidate_revision", "candidate_artifact_sha256"):
        size = 40 if key == "candidate_revision" else 64
        if not re.fullmatch(r"[0-9a-f]{" + str(size) + "}", config.get(key, "")):
            raise Blocked("candidate_identity_missing")
    if not config.get("candidate_binding_receipt"):
        raise Blocked("candidate_binding_receipt_missing")
    config["candidate_url"] = valid_url(config.get("candidate_url", ""))
    config["fixture_url"] = valid_url(config.get("fixture_url", ""))
    if config["fixture_url"] == config["candidate_url"]:
        raise Blocked("fixture_must_be_independent_of_candidate")
    if config.get("restart_control_url"):
        parts = urllib.parse.urlsplit(valid_url(config["restart_control_url"], allow_path=True))
        restart_origin = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if restart_origin == config["candidate_url"]:
            raise Blocked("restart_control_must_be_independent_of_candidate")
    if config.get("candidate_url") == os.environ.get("AGENT_SERVER_URL", "").rstrip("/"):
        raise Blocked("candidate_must_not_be_verifier_host")
    if type(config.get("timeout_seconds")) is not int or not 5 <= config["timeout_seconds"] <= 90:
        raise Blocked("invalid_scenario_timeout")
    if not isinstance(config.get("working_dir"), str) or not config["working_dir"].startswith("/"):
        raise Blocked("invalid_candidate_working_dir")


def run(config, restart=None):
    global OBSERVATION_BYTES
    OBSERVATIONS.clear()
    OBSERVATION_BYTES = 0
    contract = json.loads((ROOT / "contracts.json").read_text())
    evidence = {"schema_version": 1, "subject": {"revision": config.get("candidate_revision"), "artifact_sha256": config.get("candidate_artifact_sha256")},
                "bundle": {"id": contract["id"], "sha256": bundle_digest()},
                "configuration_sha256": hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "binding_receipt": config.get("candidate_binding_receipt"),
                "scenarios": [{"id": item["id"], "status": "blocked", "details": {"code": "prerequisite_not_reached"}} for item in contract["scenarios"]]}
    try:
        validate_config(config)
        load_cloud_credentials()
        http = HTTP(config["candidate_url"], "X-Session-API-Key", os.environ.get("CONFORMANCE_CANDIDATE_SESSION_KEY", ""))
        fixture = HTTP(config["fixture_url"], "X-Fixture-Control-Key", os.environ.get("CONFORMANCE_FIXTURE_CONTROL_KEY", ""))
        if restart is None and config.get("restart_control_url"):
            url = valid_url(config["restart_control_url"], allow_path=True)
            parts = urllib.parse.urlsplit(url)
            origin = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))
            control = HTTP(origin, "X-Restart-Control-Key", os.environ.get("CONFORMANCE_RESTART_CONTROL_KEY", ""))
            restart = lambda: control.request("POST", parts.path, {"revision": config["candidate_revision"], "artifact_sha256": config["candidate_artifact_sha256"]})
        asyncio.run(lifecycle(config, http, fixture, evidence, restart))
    except Blocked as exc:
        for result in evidence["scenarios"]:
            result["details"] = {"code": str(exc)}
    except Exception:
        for result in evidence["scenarios"]:
            result["details"] = {"code": "unexpected_harness_error"}
    statuses = {result["status"] for result in evidence["scenarios"]}
    evidence["verdict"] = "fail" if "fail" in statuses else "pass" if statuses == {"pass"} else "blocked"
    evidence["identity_source"] = "trusted coordinator input; not candidate self-report"
    evidence["ws_received"] = list(OBSERVATIONS)
    return redact(evidence)


def load_cloud_credentials():
    """Only three named run/fixture capabilities; never enumerate org secrets."""
    sandbox = os.environ.get("SANDBOX_ID", "")
    if not sandbox:
        return  # Development harness supplies explicit synthetic env keys.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sandbox):
        raise Blocked("invalid_cloud_sandbox_id")
    session = os.environ.get("SESSION_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0", "")
    if not session:
        raise Blocked("cloud_secret_context_missing")
    opener = urllib.request.build_opener(NoRedirect())
    for name in ("CONFORMANCE_CANDIDATE_SESSION_KEY", "CONFORMANCE_FIXTURE_CONTROL_KEY", "CONFORMANCE_RESTART_CONTROL_KEY"):
        if os.environ.get(name):
            continue
        request = urllib.request.Request("https://app.all-hands.dev/api/v1/sandboxes/" + sandbox + "/settings/secrets/" + name,
                                         headers={"X-Session-API-Key": session})
        try:
            with opener.open(request, timeout=20) as response:
                data = response.read(16385)
            if not data or len(data) > 16384:
                raise Blocked("run_capability_unavailable")
            os.environ[name] = data.decode().strip()
        except (OSError, UnicodeError, urllib.error.URLError):
            raise Blocked("run_capability_unavailable:" + name) from None


def redact(value):
    values = [os.environ.get(name, "") for name in ("CONFORMANCE_CANDIDATE_SESSION_KEY", "CONFORMANCE_FIXTURE_CONTROL_KEY", "CONFORMANCE_RESTART_CONTROL_KEY", "OPENHANDS_API_KEY", "SESSION_API_KEY", "OH_SESSION_API_KEYS_0", "AUTOMATION_CALLBACK_API_KEY")]
    if isinstance(value, str):
        for secret in values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {redact(key): redact(item) for key, item in value.items()}
    return value


def callback(evidence):
    url = os.environ.get("AUTOMATION_CALLBACK_URL")
    if not url:
        return
    parsed = urllib.parse.urlsplit(url)
    # Never deliver an account bearer to an arbitrary environment/config URL.
    if parsed.scheme != "https" or parsed.netloc != "app.all-hands.dev" or parsed.query or parsed.fragment or not re.fullmatch(r"(?:/api/automation)?/v1/runs/[0-9a-f-]{36}/complete", parsed.path):
        raise Blocked("invalid_callback_url")
    token = os.environ.get("AUTOMATION_CALLBACK_API_KEY") or os.environ.get("OPENHANDS_API_KEY", "")
    if not token:
        raise Blocked("callback_key_missing")
    HTTP("https://app.all-hands.dev", "Authorization", "Bearer " + token).request("POST", parsed.path, {
        "status": "COMPLETED" if evidence["verdict"] == "pass" else "FAILED",
        "run_id": os.environ.get("AUTOMATION_RUN_ID", ""),
        "error": None if evidence["verdict"] == "pass" else "conformance_" + evidence["verdict"]
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--evidence", type=Path, default=ROOT / "evidence.json")
    args = parser.parse_args()
    evidence = run(json.loads(args.config.read_text()))
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    # Safe summary excludes candidate events and every credential.
    print(json.dumps({key: evidence[key] for key in ("subject", "bundle", "scenarios", "verdict")}))
    try:
        callback(evidence)
    except Blocked:
        print(json.dumps({"callback": "blocked"}))
        return 2
    return {"pass": 0, "fail": 1, "blocked": 2}[evidence["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
