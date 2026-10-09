"""Cloud Mention Gazette: public notifications to one GitHub Pages file. No LLM."""
import base64
import datetime
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from render import render

CLOUD = "https://app.all-hands.dev"
GITHUB = "https://api.github.com"
SECRET = "REMOTE_GH"
REPO = "enyst/enyst.github.io"
PAGE = "/repos/" + REPO + "/contents/arch/mention-gazette.html"


class GazetteError(Exception):
    """Closed diagnostic codes only; never include response bodies or credentials."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class API:
    def __init__(self, origin, token, header="Authorization"):
        if origin not in {CLOUD, GITHUB}:
            raise GazetteError("invalid_origin")
        self.origin, self.token, self.header = origin, token, header
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method="GET", data=None, raw=False):
        if not path.startswith("/") or path.startswith("//") or re.search(r"[\s\\]", path):
            raise GazetteError("invalid_path")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "enyst-mention-gazette"}
        headers[self.header] = ("Bearer " if self.header == "Authorization" else "") + self.token
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self.opener.open(urllib.request.Request(self.origin + path, data=body,
                                  method=method, headers=headers), timeout=30) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
            if len(payload) > 8 * 1024 * 1024:
                raise GazetteError("response_too_large")
            return payload if raw else json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise GazetteError("http_" + str(status)) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise GazetteError("network_unavailable") from None
        except (ValueError, UnicodeError):
            raise GazetteError("invalid_json") from None


def get_secret():
    sandbox = os.environ.get("SANDBOX_ID", "")
    session = os.environ.get("SESSION_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sandbox) or not session:
        raise GazetteError("cloud_context_missing")
    value = API(CLOUD, session, "X-Session-API-Key").request(
        f"/api/v1/sandboxes/{sandbox}/settings/secrets/{SECRET}", raw=True).decode().strip()
    if not value:
        raise GazetteError("github_credential_missing")
    return value


def fire_callback(status="COMPLETED", error=None):
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url:
        return
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "app.all-hands.dev" or parsed.query or parsed.fragment:
        raise GazetteError("invalid_callback_origin")
    if not re.fullmatch(r"/api/automation/v1/runs/[a-f0-9-]+/complete", parsed.path):
        raise GazetteError("invalid_callback_path")
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error:
        body["error"] = error
    token = os.environ.get("AUTOMATION_CALLBACK_API_KEY") or os.environ.get("OPENHANDS_API_KEY", "")
    if not token:
        raise GazetteError("callback_credential_missing")
    API(CLOUD, token).request(parsed.path, "POST", body)


def notifications(gh):
    rows = []
    for page in range(1, 21):
        batch = gh.request(f"/notifications?all=false&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise GazetteError("invalid_notifications")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise GazetteError("notification_page_limit")


def public_notifications(gh, rows):
    """Drop private/unknown visibility and sensitive subject types before rendering."""
    result, visibility = [], {}
    for row in rows:
        repo = row.get("repository", {})
        name = repo.get("full_name", "")
        if repo.get("private") is not False or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
            continue
        if name not in visibility:
            current = gh.request("/repos/" + name)
            visibility[name] = current.get("private") is False and current.get("visibility") == "public"
        if not visibility[name]:
            continue
        subject = row.get("subject", {})
        kind = subject.get("type")
        if kind not in {"Issue", "PullRequest", "Release", "Discussion", "Commit"}:
            continue
        title, reason, updated = subject.get("title"), row.get("reason"), row.get("updated_at", "")
        if not isinstance(title, str) or not isinstance(reason, str) or not re.fullmatch(r"[a-z_]+", reason):
            raise GazetteError("invalid_notification_fields")
        if not isinstance(updated, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T", updated):
            raise GazetteError("invalid_notification_date")
        # Normalize the subject URL. Never trust notification text as an href.
        url = subject.get("url") or ""
        match = re.fullmatch(re.escape(GITHUB + "/repos/" + name) + r"/(issues|pulls)/(\d+)", url)
        safe_url = url if match else ""
        result.append({"repository": {"full_name": name}, "subject": {
            "title": title, "type": kind, "url": safe_url}, "reason": reason, "updated_at": updated[:10]})
    return result


def publish(gh, document):
    """Update exactly one path; SHA rejects concurrent edits of this file."""
    current = gh.request(PAGE + "?ref=main")
    if current.get("encoding") != "base64" or not current.get("sha"):
        raise GazetteError("invalid_current_page")
    content = document.encode()
    if base64.b64decode(current["content"]) == content:
        return {"changed": False}
    update = gh.request(PAGE, "PUT", {"message": "Update Mention Gazette", "branch": "main",
        "sha": current["sha"], "content": base64.b64encode(content).decode()})
    sha = update.get("commit", {}).get("sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise GazetteError("invalid_publish_receipt")
    readback = gh.request(PAGE + "?ref=" + sha)
    if base64.b64decode(readback["content"]) != content:
        raise GazetteError("publish_verification_failed")
    return {"changed": True, "commit": sha}


def main(gh=None, day=None):
    gh = gh or API(GITHUB, get_secret())
    if gh.request("/user").get("login") != "enyst":
        raise GazetteError("wrong_github_identity")
    target = gh.request("/repos/" + REPO)
    if target.get("full_name") != REPO or target.get("private") is not False or not target.get("permissions", {}).get("push"):
        raise GazetteError("wrong_publish_repository")
    public = public_notifications(gh, notifications(gh))
    day = day or datetime.datetime.now(ZoneInfo("Europe/Amsterdam")).date()
    result = publish(gh, render(public, day))
    print(json.dumps({"public_notifications": len(public), **result}))
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, GazetteError) else "unexpected_error"
        print(json.dumps({"error": code}))
        fire_callback("FAILED", code)
        raise SystemExit(1)
    else:
        fire_callback()
