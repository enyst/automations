"""Run one profile-backed Codex Cloud conversation; credentials stay server-side."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid5


class CanaryError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(method, url, key, body=None):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with build_opener(NoRedirect).open(Request(url, data=data, headers=headers, method=method), timeout=45) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise CanaryError("response_too_large")
        return json.loads(raw) if raw else None
    except HTTPError as error:
        raise CanaryError("http_" + str(error.code)) from None
    except (URLError, TimeoutError, OSError):
        raise CanaryError("network_unavailable") from None
    except (UnicodeError, ValueError):
        raise CanaryError("invalid_response") from None


def assert_profile_scope(conversation, profile_id):
    profile = conversation.get("launched_agent_profile") or {}
    if profile.get("agent_profile_id") != profile_id:
        raise CanaryError("launched_profile_missing_or_mismatched")
    allowed = profile.get("secret_refs")
    if allowed is None or set(allowed) != {"CODEX_AUTH_JSON", "DEEPSEEK_API_KEY", "GITHUB_TOKEN"}:
        raise CanaryError("launched_secret_scope_not_preserved")


def start_body(config, run_id, sandbox_id, prompt):
    return {
        "conversation_id": str(uuid5(UUID(run_id), "codex-map-canary")),
        "sandbox_id": sandbox_id,
        "agent_profile_id": config["agent_profile_id"],
        "selected_repository": config["repository"],
        "selected_branch": config["branch"],
        "title": "Codex ACP Canvas verification canary",
        "trigger": "automation",
        "public": False,
        "initial_message": {"role": "user", "content": [{"type": "text", "text": prompt + "\nPinned target SHA: " + config["target_sha"]}], "run": False},
    }


def callback(status, conversation_id=None, error=None):
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    key = os.environ.get("AUTOMATION_CALLBACK_API_KEY", "")
    if not url or not key:
        raise CanaryError("callback_context_missing")
    cloud_host = urlsplit(os.environ["OPENHANDS_CLOUD_API_URL"]).netloc
    if urlsplit(url).scheme != "https" or urlsplit(url).netloc != cloud_host:
        raise CanaryError("callback_origin_mismatch")
    body = {"status": status, "run_id": os.environ["AUTOMATION_RUN_ID"]}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if error:
        body["error"] = error
    request("POST", url, key, body)


def run():
    config = json.loads(Path(__file__).with_name("config.json").read_text())
    api = os.environ.get("OPENHANDS_CLOUD_API_URL", "").rstrip("/")
    key = os.environ.get("OPENHANDS_API_KEY", "")
    sandbox_id = os.environ.get("SANDBOX_ID", "")
    if api != config["cloud_url"] or not key or not sandbox_id:
        raise CanaryError("cloud_context_missing_or_mismatched")
    user = request("GET", api + "/api/v1/users/me", key)
    if user.get("id") != config["owner_id"] or user.get("org_id") != config["owner_id"]:
        raise CanaryError("wrong_cloud_identity")
    profile_id = os.environ.get("AUTOMATION_AGENT_PROFILE_ID", config["agent_profile_id"])
    if profile_id != config["agent_profile_id"]:
        raise CanaryError("agent_profile_mismatch")
    prompt = Path(__file__).with_name("prompt.txt").read_text()
    body = start_body(config, os.environ["AUTOMATION_RUN_ID"], sandbox_id, prompt)
    cid = body["conversation_id"]
    task = request("POST", api + "/api/v1/app-conversations", key, body)
    print(json.dumps({"phase": "starting_codex", "conversation_id": cid, "start_task_id": task["id"]}), flush=True)
    deadline = time.monotonic() + config["deadline_seconds"]
    ready = task
    while ready.get("status") != "READY":
        if ready.get("status") in {"ERROR", "FAILED"}:
            raise CanaryError("conversation_start_failed")
        if time.monotonic() >= deadline:
            raise CanaryError("conversation_start_deadline")
        time.sleep(5)
        rows = request("GET", api + "/api/v1/app-conversations/start-tasks?" + urlencode({"ids": task["id"]}), key)
        if not rows or not rows[0]:
            raise CanaryError("start_task_missing")
        ready = rows[0]
    if ready.get("app_conversation_id", "").replace("-", "") != cid.replace("-", "") or ready.get("sandbox_id") != sandbox_id:
        raise CanaryError("conversation_identity_mismatch")
    started = False
    while time.monotonic() < deadline:
        rows = request("GET", api + "/api/v1/app-conversations?" + urlencode({"ids": cid}), key)
        conversation = rows[0] if rows else None
        if not conversation:
            raise CanaryError("conversation_missing")
        if conversation.get("agent_kind") != "acp" or conversation.get("acp_server") != "codex":
            raise CanaryError("wrong_agent_started")
        if not config.get("allow_legacy_account_secrets", False):
            assert_profile_scope(conversation, config["agent_profile_id"])
        else:
            print(json.dumps({"credential_scope": "legacy_account_secrets", "approved_trial": True}), flush=True)
        status = conversation.get("execution_status")
        if not started:
            if status != "idle":
                raise CanaryError("conversation_not_idle_before_scope_check")
            request("POST", api + f"/api/v1/app-conversations/{cid}/send-message", key,
                    {"content": [{"type": "text", "text": "Proceed with the authorized canary above."}], "run": True})
            started = True
            time.sleep(5)
            continue
        print(json.dumps({"phase": "codex", "execution_status": status, "conversation_id": cid}), flush=True)
        if status == "finished":
            query = urlencode({"file_path": "/workspace/project/OpenHands/CANARY_RESULT.json"})
            text = request("GET", api + f"/api/v1/app-conversations/{cid}/file?" + query, key)
            try:
                result = json.loads(text)
            except (TypeError, ValueError):
                raise CanaryError("canary_result_missing_or_invalid") from None
            Path("canary-result.json").write_text(json.dumps(result, indent=2))
            if result.get("feature_id") != config["feature_id"] or result.get("checkout_sha") != config["target_sha"]:
                raise CanaryError("canary_result_scope_mismatch")
            if result.get("verdict") != "pass" or result.get("cleanup_verified") is not True:
                raise CanaryError("canary_did_not_pass")
            callback("COMPLETED", cid)
            return
        if status in {"error", "stuck", "paused", "waiting_for_confirmation", "waiting_for_input"}:
            raise CanaryError("codex_terminal_" + str(status))
        time.sleep(10)
    raise CanaryError("canary_deadline")


def main():
    try:
        run()
    except CanaryError as error:
        print(json.dumps({"phase": "failed", "error": str(error)}), flush=True)
        callback("FAILED", error=str(error))
        return 1
    except Exception as error:
        code = "unexpected_" + type(error).__name__
        print(json.dumps({"phase": "failed", "error": code}), flush=True)
        callback("FAILED", error=code)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
