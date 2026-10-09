#!/usr/bin/env python3
"""Cloud Jev audit: read PR evidence, classify, replace only our description section."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import audit
from audit import AuditValidationError, MODEL, build_context, questions_for, render_summary, validate_answers
from description import strip_audit, upsert_audit

VERSION = "2"
# When TypeSafe/Jev rejects a request with "max_tokens_exceeded", rebuild the same
# already-fetched context at a smaller transport budget (whole hunks are dropped,
# then surrounding source) and retry. Jev publishes no tokenizer, so these byte
# budgets are a data-driven ladder measured against its 64k-request / 32k-state
# token limits; 128 KiB keeps all patches for typical large PRs (~40-44k tokens).
RETRY_BUDGETS = (131072, 98304, 65536, 49152)
GITHUB = "https://api.github.com"
TYPESAFE = "https://api.typesafe.ai"
CLOUD = "https://app.all-hands.dev"
RECEIPT = re.compile(r"<!-- jev-input-signature ([a-f0-9]{64}) -->")


class AuditError(Exception):
    """Only fixed, non-sensitive error codes are reported."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class API:
    def __init__(self, host, token, header="Authorization", *, receipt_key=None):
        self.host, self.token, self.header = host, token, header
        self.receipt_key = receipt_key
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method="GET", data=None, raw=False, limit=8 * 1024 * 1024):
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise AuditError("invalid_api_path")
        headers = {self.header: ("Bearer " if self.header == "Authorization" else "") + self.token,
                   "Accept": "application/vnd.github+json" if self.host == GITHUB else "application/json",
                   "User-Agent": "enyst-jev-fast-audit"}
        payload = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        try:
            request = urllib.request.Request(self.host + path, data=payload, headers=headers, method=method)
            with self.opener.open(request, timeout=35) as response:
                body = response.read(limit + 1)
            if len(body) > limit:
                raise AuditError("response_too_large")
            return body if raw else json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            exc.close()
            error = AuditError("http_" + str(code))
            error.http_status = code
            error.http_body = body
            raise error from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise AuditError("network_unavailable") from None
        except (ValueError, UnicodeError):
            raise AuditError("invalid_response") from None


def secret(name):
    # Environment is supported for supervised local tests. Cloud fetches only these names.
    if os.environ.get(name):
        return os.environ[name]
    sandbox = os.environ.get("SANDBOX_ID", "")
    session = os.environ.get("SESSION_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sandbox) or not session:
        raise AuditError("cloud_secret_context_missing")
    body = API(CLOUD, session, "X-Session-API-Key").request(
        "/api/v1/sandboxes/" + sandbox + "/settings/secrets/" + name, raw=True, limit=100_000)
    result = body.decode().strip()
    if not result:
        raise AuditError("credential_missing")
    return result


def fingerprint(pr):
    record = {"head": pr["head"]["sha"], "base": pr["base"]["sha"],
              "title": pr["title"], "body": strip_audit(pr.get("body") or "").rstrip(),
              "version": VERSION, "model": MODEL,
              "rubric": hashlib.sha256(Path(__file__).with_name("audit.py").read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def signature(gh, identity):
    if not gh.receipt_key:
        raise AuditError("receipt_key_missing")
    return hmac.new(gh.receipt_key.encode(), ("jev-fast-audit-receipt-v2:" + identity).encode(), hashlib.sha256).hexdigest()


def files_for(gh, repository, number):
    rows = []
    for page in range(1, 31):
        batch = gh.request(f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise AuditError("invalid_file_listing")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise AuditError("file_listing_limit")


def file_text(gh, repository, path, ref):
    try:
        item = gh.request(f"/repos/{repository}/contents/" + urllib.parse.quote(path, safe="/") +
                          "?ref=" + urllib.parse.quote(ref, safe=""), limit=2_000_000)
        if not isinstance(item, dict) or item.get("type") != "file" or item.get("size", 0) > 250_000:
            return None
        if item.get("encoding") != "base64":
            return None
        raw = base64.b64decode(item["content"], validate=False)
        if b"\x00" in raw:
            return None
        return raw.decode("utf-8")
    except AuditError as exc:
        if str(exc) in {"http_404", "response_too_large"}:
            return None
        # Do not sign a partial result caused by a temporary transport, rate-limit,
        # or authorization failure. This PR remains eligible for a later run.
        raise
    except (ValueError, UnicodeError, KeyError):
        return None


def gather_raw(gh, repository, pr):
    rows = files_for(gh, repository, pr["number"])
    if len(rows) != pr.get("changed_files", len(rows)):
        raise AuditError("incomplete_file_listing")
    comparison = gh.request(f"/repos/{repository}/compare/{pr['base']['sha']}...{pr['head']['sha']}")
    merge_base = comparison["merge_base_commit"]["sha"]
    # Fetch immutable file versions, never download or execute code from the PR.
    candidates = [row for row in rows if row.get("patch")][:40]
    def load(row):
        path = row["filename"]
        old = row.get("previous_filename", path)
        return path, {
            "base": None if row.get("status") == "added" else file_text(gh, repository, old, merge_base),
            "head": None if row.get("status") == "removed" else file_text(gh, repository, path, pr["head"]["sha"]),
        }
    with ThreadPoolExecutor(max_workers=4) as pool:
        contents = dict(pool.map(load, candidates))
    clean = dict(pr, body=strip_audit(pr.get("body") or "").rstrip())
    clean["base"] = dict(pr["base"], sha=merge_base)
    return clean, rows, contents


def gather(gh, repository, pr):
    clean, rows, contents = gather_raw(gh, repository, pr)
    return build_context(clean, rows, contents)


def _build_state(clean, rows, contents, budget):
    """Build request state at an explicit transport budget, or the full default if None."""
    if budget is None:
        return build_context(clean, rows, contents)
    previous = audit.MAX_TRANSPORT_BYTES
    audit.MAX_TRANSPORT_BYTES = budget
    try:
        return build_context(clean, rows, contents)
    finally:
        audit.MAX_TRANSPORT_BYTES = previous


def classify(jev, state):
    questions = questions_for(state)
    started = time.monotonic()
    result = jev.request("/v1/systemone", "POST", {"model": MODEL, "state": state, "questions": questions})
    latency = round((time.monotonic() - started) * 1000)
    return questions, result, latency


def _is_max_tokens(error):
    return isinstance(error, AuditError) and str(error) == "http_400" and "max_tokens_exceeded" in getattr(error, "http_body", "")


def classify_with_retry(jev, clean, rows, contents):
    """Classify, shrinking the transport budget when Jev rejects with max_tokens_exceeded.

    Only the state's omitted-hunk/context metadata changes on retry; the fetched
    source, merge base, and PR identity are unchanged, so coverage reasons already
    record which hunks were dropped for each reduced attempt.
    """
    last = None
    for budget in (None,) + RETRY_BUDGETS:
        state = _build_state(clean, rows, contents, budget)
        try:
            questions, result, latency = classify(jev, state)
        except AuditError as exc:
            last = exc
            if _is_max_tokens(exc):
                continue
            raise
        return state, questions, result, latency
    if last is not None:
        raise last
    raise AuditError("max_tokens_exceeded")


def publish(gh, repository, number, expected_fingerprint, summary):
    path = f"/repos/{repository}/pulls/{number}"
    # Re-read immediately before writing, so the LLM review and human edits survive.
    # GitHub does not support a conditional/CAS PATCH for PR bodies.
    fresh = gh.request(path)
    if fresh.get("state") != "open" or fingerprint(fresh) != expected_fingerprint:
        return "changed_before_publish"
    original = fresh.get("body") or ""
    updated = upsert_audit(original, summary)
    if len(updated.encode()) > 65_000:
        raise AuditError("description_too_large")
    if updated == original:
        return "unchanged"
    guard = gh.request(path)
    if guard.get("state") != "open":
        return "changed_before_publish"
    if guard.get("body") != fresh.get("body") or fingerprint(guard) != expected_fingerprint:
        return "concurrent_edit"
    gh.request(path, "PATCH", {"body": updated})
    verified = gh.request(path)
    if (verified.get("body") or "") != updated:
        return "concurrent_edit_after_publish"
    if verified.get("state") != "open" or fingerprint(verified) != expected_fingerprint:
        return "changed_after_publish"
    if strip_audit(updated) != strip_audit(original) and strip_audit(updated).rstrip() != strip_audit(original).rstrip():
        raise AuditError("outside_section_changed")
    return "published"


def audit_pr(gh, jev, repository, number, config, force=False, write=True):
    if repository.casefold() not in {r.casefold() for r in config["repositories"]}:
        raise AuditError("repository_not_allowed")
    pr = gh.request(f"/repos/{repository}/pulls/{number}")
    if pr.get("state") != "open":
        return {"repository": repository, "pr": number, "status": "closed"}
    identity = fingerprint(pr)
    if not force and RECEIPT.search(pr.get("body") or "") and any(hmac.compare_digest(signature(gh, identity), saved) for saved in RECEIPT.findall(pr.get("body") or "")):
        return {"repository": repository, "pr": number, "status": "current"}
    clean, rows, contents = gather_raw(gh, repository, pr)
    state, questions, result, latency = classify_with_retry(jev, clean, rows, contents)
    validate_answers(result, questions)
    summary = render_summary(state, result, latency)
    summary += "\n\n<!-- jev-input-signature " + signature(gh, identity) + " -->"
    status = publish(gh, repository, number, identity, summary) if write else "dry_run"
    # Receipt has only safe structured signals, not fetched code, PR body, or credentials.
    return {"repository": repository, "pr": number, "url": pr["html_url"], "status": status,
            "head": pr["head"]["sha"], "fingerprint": identity, "model": result.get("model"),
            "latency_ms": latency, "usage": result.get("usage"), "coverage": state["coverage"],
            "answers": result["answers"]}


def select_targets(gh, config, event=None):
    if event and (event.get("event") or event.get("pull_request")):
        payload = event.get("event", event)
        if isinstance(payload, dict) and isinstance(payload.get("pull_request"), dict):
            return [(payload["repository"]["full_name"], int(payload["pull_request"]["number"]))]
        raise AuditError("invalid_event")
    if config.get("manual_target"):
        target = config["manual_target"]
        return [(target["repository"], int(target["number"]))]
    start = config["watch_since"]
    slot = int(time.time() // 300)
    queues = []
    for repository in config["repositories"]:
        queue = []
        for page in range(1, 11):
            rows = gh.request(f"/repos/{repository}/pulls?state=open&sort=updated&direction=desc&per_page=100&page={page}")
            if not isinstance(rows, list):
                raise AuditError("invalid_pr_listing")
            stop = False
            for pr in rows:
                if pr["updated_at"] < start:
                    stop = True
                    break
                if pr.get("draft"):
                    continue
                try:
                    current = any(hmac.compare_digest(signature(gh, fingerprint(pr)), saved) for saved in RECEIPT.findall(pr.get("body") or ""))
                except ValueError:
                    current = False  # Report this PR separately; keep selecting the rest.
                if current:
                    continue
                queue.append((repository, pr["number"]))
            if stop or len(rows) < 100:
                break
        if queue:
            # Stable ordering prevents our own description updates from moving a
            # failed PR back to the front on every poll.
            queue.sort(key=lambda target: target[1])
            offset = slot % len(queue)
            queues.append(queue[offset:] + queue[:offset])
    if not queues:
        return []
    offset = slot % len(queues)
    queues = queues[offset:] + queues[:offset]
    targets = []
    limit = config.get("max_prs_per_run", 6)
    for index in range(max(map(len, queues))):
        for queue in queues:
            if len(targets) >= limit:
                return targets
            if index < len(queue):
                targets.append(queue[index])
    return targets


def callback(status):
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url:
        return
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "app.all-hands.dev" or parsed.query or parsed.fragment:
        raise AuditError("invalid_callback_url")
    token = os.environ.get("AUTOMATION_CALLBACK_API_KEY") or os.environ.get("OPENHANDS_API_KEY", "")
    API(CLOUD, token).request(parsed.path, "POST", {"status": status,
        "run_id": os.environ.get("AUTOMATION_RUN_ID", "")})


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(Path(__file__).with_name("config.json").read_text())
    typesafe_key = secret("TYPESAFE_API_KEY")
    gh = API(GITHUB, secret("github_token"), receipt_key=typesafe_key)
    if gh.request("/user").get("login") != "enyst":
        raise AuditError("wrong_github_account")
    if bool(args.repository) != bool(args.pr):
        raise AuditError("incomplete_target")
    event = json.loads(os.environ["AUTOMATION_EVENT_PAYLOAD"]) if os.environ.get("AUTOMATION_EVENT_PAYLOAD") else None
    targets = [(args.repository, args.pr)] if args.repository else select_targets(gh, config, event)
    if not targets:
        print(json.dumps({"status": "no_changed_prs"}))
        return
    jev = API(TYPESAFE, typesafe_key)
    failed = False
    for repository, number in targets:
        try:
            print(json.dumps(audit_pr(gh, jev, repository, number, config, args.force or config.get("force_reaudit", False), not args.dry_run), ensure_ascii=False))
        except Exception as exc:
            failed = True
            code = str(exc) if isinstance(exc, (AuditError, AuditValidationError)) else "audit_failed"
            print(json.dumps({"repository": repository, "pr": number, "status": "failed", "error": code}))
    if failed:
        raise AuditError("one_or_more_prs_failed")


if __name__ == "__main__":
    try:
        run()
        callback("COMPLETED")
    except Exception as exc:
        code = str(exc) if isinstance(exc, (AuditError, AuditValidationError)) else "automation_failed"
        print(json.dumps({"status": "failed", "error": code}))
        try:
            callback("FAILED")
        except Exception:
            print(json.dumps({"status": "callback_failed"}))
        sys.exit(1)
