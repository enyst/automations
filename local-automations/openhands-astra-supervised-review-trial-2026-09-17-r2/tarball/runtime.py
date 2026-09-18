"""Small HTTP boundary. Errors never include response bodies or credentials."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from state import StateConflict, StateMissing, StateStore


class ApiError(RuntimeError):
    def __init__(self, status=0, code="request_failed"):
        self.status, self.code = status, code
        super().__init__(f"API request failed (HTTP {status}; {code})")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Transport:
    def __init__(self, base_url, headers, *, opener=None, allow_loopback_http=False):
        parsed = urlsplit(base_url)
        local_http = (
            allow_loopback_http is True and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if (
            (parsed.scheme != "https" and not local_http)
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("HTTPS base URL without embedded credentials required")
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers)
        self.opener = opener or build_opener(NoRedirect())

    def request(self, method, path, body=None, *, raw=False):
        if not path.startswith("/") or path.startswith("//") or "#" in path:
            raise ValueError("Relative API path required")
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                limit = 16_384 if raw else 16 * 1024 * 1024
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise ApiError(response.status, "response_too_large")
                if raw:
                    return content.decode()
                return json.loads(content) if content else None
        except HTTPError as exc:
            code = "request_failed"
            # Only recognize bounded, known KV conflict codes; never retain bodies.
            if exc.code == 409:
                try:
                    detail = json.loads(exc.read(4096))
                    candidate = detail.get("detail", detail)
                    if isinstance(candidate, dict):
                        candidate = candidate.get("error", candidate.get("code"))
                    if isinstance(candidate, str) and candidate.startswith(
                        "kv_store_busy:"
                    ):
                        candidate = "lock_timeout"
                    if candidate in {"version_mismatch", "key_exists", "lock_timeout"}:
                        code = candidate
                except Exception:
                    pass
            raise ApiError(exc.code, code) from None
        except (URLError, TimeoutError, OSError, UnicodeError, ValueError):
            raise ApiError(0, "transport_or_decode_failure") from None


def github_transport(token):
    if not token:
        raise ValueError("GitHub credential missing")
    return Transport(
        "https://api.github.com",
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "OpenHands-Astra-review-auditor",
        },
    ).request


def cloud_secret(name, environ=os.environ):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise ValueError("Configured Cloud secret name required")
    sandbox = environ.get("SANDBOX_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sandbox):
        raise ValueError("Cloud sandbox identity required")
    session_key = environ.get("SESSION_API_KEY") or environ.get("OH_SESSION_API_KEYS_0")
    if not session_key:
        raise ValueError("Cloud sandbox session authorization required")
    cloud_url = environ.get("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")
    result = Transport(cloud_url, {"X-Session-API-Key": session_key}).request(
        "GET", f"/api/v1/sandboxes/{sandbox}/settings/secrets/{name}", raw=True
    )
    if not result or result.strip() != result:
        raise ValueError("Cloud secret missing or malformed")
    return result


def state_store(environ=os.environ):
    token = environ.get("AUTOMATION_KV_TOKEN")
    if not token:
        raise ValueError("Durable Automation KV is required before any review")
    transport = Transport(
        environ["AUTOMATION_API_URL"].rstrip("/") + "/v1/kv",
        {"Authorization": f"Bearer {token}"},
    )

    def request(method, path, body):
        try:
            return transport.request(method, path, body)
        except ApiError as exc:
            if exc.status == 404:
                raise StateMissing("Auditor state must be bootstrapped") from None
            if exc.status == 409:
                raise StateConflict(code=exc.code) from None
            raise

    return StateStore(request)


def completion(status, *, error=None, cost=None, environ=os.environ,
               allow_loopback_http=False):
    """One outer callback; failures are observable, never reported as success."""
    callback = environ["AUTOMATION_CALLBACK_URL"]
    parsed = urlsplit(callback)
    token = environ.get("AUTOMATION_CALLBACK_API_KEY") or environ["OPENHANDS_API_KEY"]
    body = {"status": status, "run_id": environ["AUTOMATION_RUN_ID"]}
    if error:
        body["error"] = error
    if cost is not None:
        body["cost"] = cost
    options = {"allow_loopback_http": True} if allow_loopback_http is True else {}
    Transport(
        f"{parsed.scheme}://{parsed.netloc}", {"Authorization": f"Bearer {token}"},
        **options,
    ).request("POST", parsed.path + ("?" + parsed.query if parsed.query else ""), body)
