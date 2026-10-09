"""Daily priority reports; preserve the broad SDK scan on Mondays.

No model completions on other days. DRY_RUN disables all writes, including KV.
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

import run as weekly
import verification


def github_post(path, body):
    allowed = verification.BASE + "/pulls/"
    if not path.startswith(allowed) or not path.endswith("/requested_reviewers") or body != {"reviewers": [verification.BOT]}:
        raise RuntimeError("Mutation outside authorized review-request scope")
    req = urllib.request.Request("https://api.github.com" + path, method="POST",
        data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + weekly.GITHUB_TOKEN,
            "Content-Type": "application/json", "Accept": "application/vnd.github+json"})
    # Never automatically retry mutations after ambiguous network outcomes.
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def claim(key):
    base = os.environ.get("AUTOMATION_API_URL", "").rstrip("/")
    token = os.environ.get("AUTOMATION_KV_TOKEN", "")
    if not base or not token:
        raise RuntimeError("Durable KV unavailable; refusing review request")
    req = urllib.request.Request(base + "/v1/kv/" + key + "/incr", method="POST",
        data=b'{"by":1}', headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)["value"] == 1


def main():
    now = dt.datetime.now(dt.timezone.utc)
    # Existing Cloud setup resolves secrets and ensures the workspace completion
    # callback. Loading the profile does not invoke it on daily-only runs.
    score = weekly.make_scorer()
    if not weekly.GITHUB_TOKEN:
        raise RuntimeError("GitHub credential required")
    if not weekly.DRY_RUN and not weekly.VALIDATE_ONLY and not weekly.INGEST_TOKEN:
        raise RuntimeError("Notebook credential required")
    dry = weekly.DRY_RUN or weekly.VALIDATE_ONLY
    if not dry:
        identity = weekly.gh_get("/user")
        repository = weekly.gh_get(verification.BASE)
        permissions = (repository or {}).get("permissions", {})
        if (identity or {}).get("login") != "enyst" or not any(
                permissions.get(p) for p in ("push", "maintain", "admin")):
            raise RuntimeError("Review requests require the authorized maintainer credential")
    notes = verification.run(weekly.gh_get, github_post, claim, weekly.post_notes, now, dry)
    print(json.dumps({"daily_verification": notes, "dry_run": dry}, indent=2))
    if now.astimezone(ZoneInfo("Europe/Amsterdam")).weekday() == 0 and os.environ.get("DAILY_ONLY") != "1":
        weekly.main(score_fit=score)
    return 0


def entrypoint():
    try:
        result = main()
    except BaseException as error:
        weekly.close_cloud_workspace(type(error), error, error.__traceback__)
        raise
    weekly.close_cloud_workspace()
    return result


if __name__ == "__main__":
    sys.exit(entrypoint())
