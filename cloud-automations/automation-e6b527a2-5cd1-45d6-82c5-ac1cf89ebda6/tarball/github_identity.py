"""Use only the intended bot credential for unattended transpilation writes."""
import json
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def github_secrets(workspace):
    refs = workspace.get_secrets(names=["SMOLPAWS_TOKEN"])
    credential = refs.get("SMOLPAWS_TOKEN")
    if credential is None:
        raise RuntimeError("Required SMOLPAWS_TOKEN secret is missing; no agent started")
    value = credential.get_value()
    if not value:
        raise RuntimeError("SMOLPAWS_TOKEN is empty; no agent started")
    request = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": "Bearer " + value, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            identity = json.load(response).get("login")
    except Exception:
        raise RuntimeError("GitHub identity verification failed; no agent started") from None
    if identity != "smolpaws":
        raise RuntimeError("GitHub credential is not smolpaws; no agent started")
    print("GitHub identity verified: smolpaws", flush=True)
    return {"SMOLPAWS_TOKEN": credential, "GH_TOKEN": credential, "GITHUB_TOKEN": credential}
