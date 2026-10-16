import json, os, urllib.request, urllib.parse

HTTP_TIMEOUT = 60


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_request(request):
    """Send credentials only to the configured HTTP(S) endpoint, without redirects."""
    url = urllib.parse.urlsplit(request.full_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("invalid_helper_endpoint")
    return urllib.request.build_opener(NoRedirect).open(request, timeout=HTTP_TIMEOUT)

def get_secret(name):
    """Fetch a named secret: from the agent server when AGENT_SERVER_URL is set, else from the OpenHands API."""
    url = os.environ.get("AGENT_SERVER_URL", "").rstrip("/")
    key = os.environ.get("SESSION_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0", "")
    if url:  # local Agent Canvas
        url = f"{url}/api/settings/secrets/{name}"
    else:  # OpenHands Cloud / Enterprise
        api = os.environ["OPENHANDS_CLOUD_API_URL"].rstrip("/")
        url = f"{api}/api/v1/sandboxes/{os.environ['SANDBOX_ID']}/settings/secrets/{name}"
    with open_request(urllib.request.Request(
        url, headers={"X-Session-API-Key": key}
    )) as r:
        return r.read().decode().strip()

def fire_callback(status="COMPLETED", error=None):
    """Signal run completion. MUST be called on every exit path — success AND error."""
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url: return
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error: body["error"] = error
    try:
        with open_request(urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('AUTOMATION_CALLBACK_API_KEY') or os.environ.get('OPENHANDS_API_KEY', '')}",
        })):
            pass
    except Exception as e: print(f"Callback error: {e}")
