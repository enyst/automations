"""Daily, deterministic verification-report discovery and guarded review requests.

GitHub descriptions are data, never executable instructions. No PR code is run.
"""
import datetime as dt
import re

REPO = "OpenHands/OpenHands"
BOT = "all-hands-bot"
BASE = f"/repos/{REPO}"
MAP = ".agents/skills/verify-openhands/references/feature-map/"


def report_title(title):
    title = title.lower()
    return "verify-openhands" in title and "map" in title and "maintenance" in title


def pages(get, path, **params):
    from urllib.parse import urlencode
    for page in range(1, 101):
        batch = get(path + "?" + urlencode({**params, "per_page": 100, "page": page}))
        if not isinstance(batch, list):
            raise RuntimeError("Incomplete GitHub listing")
        yield from batch
        if len(batch) < 100:
            return
    raise RuntimeError("GitHub listing exceeded safety limit; refusing incomplete scan")


def discover(get, now):
    # All open reports, including old ones waiting for green CI. Closed reports
    # updated in the overlap window stay visible even if merged between runs.
    chosen = {}
    cutoff = (now - dt.timedelta(days=7)).isoformat()
    for state in ("open", "closed"):
        for pr in pages(get, BASE + "/pulls", state=state, sort="updated", direction="desc"):
            updated = dt.datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
            if state == "closed" and updated < dt.datetime.fromisoformat(cutoff):
                break
            if not report_title(pr.get("title", "")):
                continue
            files = list(pages(get, f"{BASE}/pulls/{pr['number']}/files"))
            if any(f.get("filename", "").startswith(MAP) for f in files):
                chosen[pr["number"]] = pr
    return sorted(chosen.values(), key=lambda p: p["created_at"], reverse=True)


def ci_state(get, sha):
    # The combined status endpoint already folds superseded contexts. Check-run
    # filter=latest folds reruns; paginate rather than trusting the first 100.
    status = get(f"{BASE}/commits/{sha}/status?per_page=100")
    if not isinstance(status, dict) or not isinstance(status.get("total_count"), int):
        return "unknown"
    statuses = status["total_count"]
    state = status.get("state")
    if statuses and state != "success":
        return "pending" if state == "pending" else "failed"
    runs = []
    for page in range(1, 101):
        data = get(f"{BASE}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}")
        if not isinstance(data, dict) or not isinstance(data.get("check_runs"), list):
            return "unknown"
        runs.extend(data["check_runs"])
        if len(runs) >= data.get("total_count", 10**9):
            break
        if not data["check_runs"]:
            return "unknown"
    else:
        return "unknown"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    if any(r.get("conclusion") not in ("success", "skipped", "neutral") for r in runs):
        return "failed"
    if not statuses and not any(r.get("conclusion") == "success" for r in runs):
        return "unknown"
    return "green"


def review_state(pr, reviews):
    sha = pr["head"]["sha"]
    current = [r for r in reviews if r.get("user", {}).get("login", "").lower() == BOT
               and r.get("commit_id") == sha and r.get("state") in
               ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")]
    # A CI-skip notice is not a review of the work.
    current = [r for r in current if r["state"] != "COMMENTED" or
               (r.get("body", "").strip() and "CI checks are failing" not in r["body"]
                and "regardless of CI status" not in r["body"])]
    if current:
        return sorted(current, key=lambda r: r.get("submitted_at") or "")[-1]["state"].lower()
    if any(u.get("login", "").lower() == BOT for u in pr.get("requested_reviewers", [])):
        return "requested"
    return "missing"


def inspect(get, number):
    pr = get(f"{BASE}/pulls/{number}")
    if not isinstance(pr, dict) or not re.fullmatch(r"[0-9a-f]{40}", pr.get("head", {}).get("sha", "")):
        raise RuntimeError("Missing current pull request head")
    reviews = list(pages(get, f"{BASE}/pulls/{number}/reviews"))
    return pr, ci_state(get, pr["head"]["sha"]), review_state(pr, reviews)


def request_review(get, post, claim, number, observed_sha, dry_run=False):
    # Fresh head, CI, review and PR state immediately before the mutation.
    pr, ci, review = inspect(get, number)
    if pr["head"]["sha"] != observed_sha:
        return "head-changed"
    if pr.get("state") != "open" or pr.get("draft") or not report_title(pr.get("title", "")):
        return "ineligible"
    if review != "missing":
        return review
    if ci != "green":
        return "waiting-for-ci"
    if dry_run:
        return "would-request"
    # Atomic durable claim avoids concurrent/repeated requests, including an
    # ambiguous network outcome. A failed/uncertain attempt needs human retry.
    if not claim(f"verify-review-{number}-{observed_sha}"):
        return "previous-attempt-check-manually"
    # GitHub has no SHA-conditional review-request endpoint. Minimize the race.
    latest = get(f"{BASE}/pulls/{number}")
    if latest["head"]["sha"] != observed_sha or latest.get("state") != "open" or latest.get("draft"):
        return "head-or-state-changed"
    if any(u.get("login", "").lower() == BOT for u in latest.get("requested_reviewers", [])):
        return "requested"
    post(f"{BASE}/pulls/{number}/requested_reviewers", {"reviewers": [BOT]})
    return "requested"


def report_excerpt(body):
    """Preserve reported results, not inferred facts; do not copy huge bodies."""
    lines = []
    section = False
    for line in body.splitlines():
        if line.startswith("### "):
            section = line.lower().startswith("### new defects")
        if ("**Total**" in line or "**Verdict:" in line
                or (section and line.startswith("- "))):
            lines.append(line[:300])
    return "\n".join(lines)[:1200]


def note(pr, ci, review, checked_at):
    number = pr["number"]
    return {
        "id": f"OpenHands-OpenHands-{number}", "title": pr["title"],
        "desc": f"Daily verification report — highest attention priority. CI: {ci}; "
                f"{BOT}: {review}. Head {pr['head']['sha'][:12]}. Checked {checked_at}.\n"
                + report_excerpt(pr.get("body") or ""),
        "section": "Daily verification reports", "tags": ["router", "pr", "daily-verification", "priority"],
        "source": "pr", "kind": "pr", "routing": "for_engel", "signal_score": 100,
        "state": "merged" if pr.get("merged_at") else "closed" if pr.get("state") == "closed"
                 else "draft" if pr.get("draft") else "open",
        "ref": {"repo": REPO, "number": number, "url": f"https://github.com/{REPO}/pull/{number}"},
        "last_scored_at": pr.get("updated_at"),
    }


def run(get, post, claim, publish, now, dry_run=False):
    notes, errors = [], []
    for candidate in discover(get, now):
        number = candidate["number"]
        pr, ci, review = inspect(get, number)
        if pr.get("state") == "open" and not pr.get("draft") and ci == "green" and review == "missing":
            try:
                review = request_review(get, post, claim, number, pr["head"]["sha"], dry_run)
            except Exception:
                review = "request-failed-check-manually"
                errors.append(number)
        notes.append(note(pr, ci, review, now.isoformat(timespec="seconds")))
    if not dry_run and notes:
        publish(notes)
    if errors:
        raise RuntimeError("Review requests failed for PRs: " + ",".join(map(str, errors)))
    return notes
