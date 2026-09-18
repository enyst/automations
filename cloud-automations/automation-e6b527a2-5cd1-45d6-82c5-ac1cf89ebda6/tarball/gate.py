"""Cheap pre-check for the weekly transpile automations.

Runs before the SDK is imported and before any repo is cloned. Decides whether
this run has work to do. If not, it reports completion to the automation
service and exits, so no conversation (and no LLM spend) is started.

Fail-open: any error in the check itself means "run the conversation".

GATE_MODE=sdk     newest upstream software-agent-sdk release tag vs the SDK pin
GATE_MODE=server  SDK repo pin vs the vendored pin in the server package
"""

import json
import os
import re
import subprocess
import sys
import urllib.request

SDK_REPO = "smolpaws/openhands-agent"
SERVER_REPO = "smolpaws/smolpaws"
UPSTREAM_GIT = "https://github.com/OpenHands/software-agent-sdk"
SDK_PIN_PATH = "transpile/upstream.json"
VENDORED_PIN_PATH = (
    "packages/openhands-agent-server/vendor/openhands-agent/transpile/upstream.json"
)
RESUME_PREFIX = {"sdk": "drift/", "server": "revendor/"}
TIMEOUT = 20


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode()


def pin_commit(repo, path):
    raw = _get(f"https://raw.githubusercontent.com/{repo}/main/{path}")
    commit = json.loads(raw)["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"bad commit in {repo}/{path}: {commit!r}")
    return commit


def newest_upstream_release():
    out = subprocess.run(
        ["git", "ls-remote", "--tags", UPSTREAM_GIT, "refs/tags/v*"],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    tags = {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", name)
        if not m:
            continue
        key = tuple(int(x) for x in m.groups())
        # A peeled entry (^{}) is the commit behind an annotated tag; prefer it.
        if peeled or key not in tags:
            tags[key] = (name, sha)
    if not tags:
        raise ValueError("no v* tags found upstream")
    return tags[max(tags)]


def open_resume_pr(repo, prefix):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    prs = json.loads(_get(
        f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=100",
        headers,
    ))
    for pr in prs:
        if pr["head"]["ref"].startswith(prefix):
            return pr["html_url"]
    return None


def report_phase(message):
    url = os.environ.get("AUTOMATION_PHASE_URL", "")
    token = os.environ.get("AUTOMATION_CALLBACK_API_KEY") or os.environ.get(
        "OPENHANDS_API_KEY", ""
    )
    if not url or not token:
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"phase": message}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"[gate] phase report failed (non-fatal): {e}")


def fire_callback(status="COMPLETED", error=None):
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url:
        return
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error:
        body["error"] = error
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            # Same auth the SDK's OpenHandsCloudWorkspace uses for this callback:
            # the per-user automation key. AUTOMATION_CALLBACK_API_KEY is a fallback.
            "Authorization": f"Bearer {os.environ.get('OPENHANDS_API_KEY') or os.environ.get('AUTOMATION_CALLBACK_API_KEY', '')}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            print(f"[gate] completion callback sent: HTTP {r.status}")
    except Exception as e:  # noqa: BLE001
        print(f"[gate] callback failed: {e}")
        raise


def decide(mode):
    """Return (has_work: bool, summary: str)."""
    if mode == "sdk":
        pin = pin_commit(SDK_REPO, SDK_PIN_PATH)
        tag, sha = newest_upstream_release()
        pr = open_resume_pr(SDK_REPO, RESUME_PREFIX[mode])
        if pr:
            return True, f"open {RESUME_PREFIX[mode]} PR to resume: {pr}"
        if sha == pin:
            return False, f"SDK pin {pin[:8]} is already newest upstream release {tag}"
        return True, f"upstream {tag} ({sha[:8]}) is ahead of SDK pin {pin[:8]}"

    if mode == "server":
        sdk_pin = pin_commit(SDK_REPO, SDK_PIN_PATH)
        vendored = pin_commit(SERVER_REPO, VENDORED_PIN_PATH)
        pr = open_resume_pr(SERVER_REPO, RESUME_PREFIX[mode])
        if pr:
            return True, f"open {RESUME_PREFIX[mode]} PR to resume: {pr}"
        if sdk_pin == vendored:
            return False, f"vendored pin {vendored[:8]} already matches SDK main pin"
        return True, f"SDK main pin {sdk_pin[:8]} differs from vendored {vendored[:8]}"

    raise ValueError(f"unknown GATE_MODE {mode!r}")


def run():
    mode = os.environ.get("GATE_MODE", "").strip()
    if not mode:
        print("[gate] GATE_MODE unset; skipping gate")
        return
    try:
        has_work, summary = decide(mode)
    except Exception as e:  # noqa: BLE001
        print(f"[gate] check failed ({e!r}); failing open, running conversation")
        return
    print(f"[gate] {mode}: {summary}")
    if has_work:
        report_phase(f"gate: work found - {summary}"[:200])
        return
    report_phase(f"gate: nothing to do - {summary}"[:200])
    try:
        fire_callback("COMPLETED")
    except Exception:  # noqa: BLE001
        print("[gate] could not mark the run complete; failing open so the SDK path closes it")
        return
    print("[gate] no work; exiting without starting a conversation")
    sys.exit(0)


if __name__ == "__main__":
    # Manual check: python3 gate.py sdk|server  (prints decision, no callbacks)
    print(decide(sys.argv[1]))
