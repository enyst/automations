#!/usr/bin/env python3
"""Issue duplicate-check automation — close sweep (deterministic, no LLM).

Runs the pinned auto-close script for each target repository using the enyst
token (REMOTE_GH). The irreversible close decision lives entirely in the
vendored script; this wrapper only iterates repos and supplies credentials.

stdlib-only on purpose: no setup.sh, no SDK install, runs in seconds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

REPOSITORIES = [
    "OpenHands/OpenHands",
    "OpenHands/software-agent-sdk",
    "OpenHands/automation",
    "OpenHands/extensions",
]
ENYST_SECRET = "REMOTE_GH"


def fetch_secret(name: str) -> str:
    cloud_url = os.environ.get(
        "OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev"
    ).rstrip("/")
    sandbox_id = os.environ.get("SANDBOX_ID", "")
    session_key = os.environ.get("SESSION_API_KEY") or os.environ.get(
        "OH_SESSION_API_KEYS_0", ""
    )
    if not sandbox_id or not session_key:
        raise RuntimeError("SANDBOX_ID or SESSION_API_KEY not set; cannot fetch secrets")
    req = urllib.request.Request(
        f"{cloud_url}/api/v1/sandboxes/{sandbox_id}/settings/secrets/{name}",
        headers={"X-Session-API-Key": session_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8").strip()


def fire_callback(status: str, error: str | None = None) -> None:
    """Signal run completion. Called on success AND failure."""
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url:
        return
    body: dict = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error:
        body["error"] = error
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ.get('OPENHANDS_API_KEY', '')}",
    }
    try:
        urllib.request.urlopen(
            urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Callback error: {exc}", file=sys.stderr)


def main() -> int:
    token = fetch_secret(ENYST_SECRET)
    env = dict(os.environ)
    env["GITHUB_TOKEN"] = token

    results = {}
    for repo in REPOSITORIES:
        proc = subprocess.run(
            [
                sys.executable,
                "auto_close_duplicate_issues.py",
                "--repository",
                repo,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        try:
            summary = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            summary = {"repository": repo, "error": proc.stderr[-500:]}
        results[repo] = summary
        print(f"[{repo}] exit={proc.returncode}")

    print(json.dumps(results, indent=2))
    fire_callback("COMPLETED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        fire_callback("FAILED", str(exc))
        raise
