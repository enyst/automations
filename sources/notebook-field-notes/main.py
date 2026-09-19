#!/usr/bin/env python3
"""Hourly public discovery -> Jev selection -> bounded OpenHands -> public Field note."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import uuid

from core import (REPOSITORIES, ValidationError, canonical_repository, normalize_candidate, subject_id, fingerprint,
    screen_candidate, classification_request, validate_classification, classify_decision,
    build_note, render_document)
from transport import (API, CLOUD, GITHUB, FieldNotesError, GitState, Publisher, secret)
from writer import WriterError, check_writer, disable_sdk_tracing

def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")

def parse_date(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise FieldNotesError("timestamp_without_timezone")
    return parsed

def callback(status, error=None):
    url=os.environ.get("AUTOMATION_CALLBACK_URL","")
    if not url: return
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!="https" or parsed.netloc!="app.all-hands.dev" or parsed.query or parsed.fragment:
        raise FieldNotesError("invalid_callback_url")
    token=os.environ.get("AUTOMATION_CALLBACK_API_KEY") or os.environ.get("OPENHANDS_API_KEY","")
    payload={"status":status,"run_id":os.environ.get("AUTOMATION_RUN_ID","")}
    if error is not None:payload["error"]=error
    API(CLOUD,token).request(parsed.path,method="POST",
        data=payload)

def load_config():
    value=json.loads(Path(__file__).with_name("config.json").read_text())
    if value.get("repositories")!=list(REPOSITORIES):
        raise FieldNotesError("repository_configuration_mismatch")
    for key,low,high in [("max_candidates_per_run",1,30),("max_notes_per_day",1,5),
                         ("min_quiet_minutes",0,120),("lookback_days",1,30)]:
        if isinstance(value.get(key),bool) or not isinstance(value.get(key),int) or not low<=value[key]<=high:
            raise FieldNotesError("invalid_run_budget")
    parse_date(value["watch_since"])
    return value

def source_repositories(gh):
    pins=[]
    for repo in REPOSITORIES:
        metadata=gh.request("/repos/"+repo)
        if metadata.get("full_name")!=repo or metadata.get("private") is not False:
            raise FieldNotesError("source_repo_not_public")
        branch=urllib.parse.quote(metadata["default_branch"],safe="")
        sha=gh.request(f"/repos/{repo}/commits/{branch}")["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}",sha): raise FieldNotesError("invalid_source_revision")
        pins.append({"repository":repo,"sha":sha})
    return pins

def discovery(gh,config,now):
    floor=max(parse_date(config["watch_since"]),now-dt.timedelta(days=config["lookback_days"]))
    since=urllib.parse.quote(floor.isoformat().replace("+00:00","Z"),safe="")
    rows=[]
    for repository in REPOSITORIES:
        for page in range(1,6):
            batch=gh.request(f"/repos/{repository}/issues?state=all&sort=updated&direction=desc&since={since}&per_page=100&page={page}")
            if not isinstance(batch,list): raise FieldNotesError("invalid_issue_listing")
            for row in batch:
                if now-parse_date(row["updated_at"])<dt.timedelta(minutes=config["min_quiet_minutes"]):continue
                kind="pr" if "pull_request" in row else "issue"
                rows.append((repository,kind,row["number"],row))
            if len(batch)<100:break
        else:
            raise FieldNotesError("discovery_page_limit")
    # Deterministic oldest-first avoids starvation by busy PRs and our own updates.
    return sorted(rows,key=lambda r:(r[3]["updated_at"],r[0],r[2]))


def listing_fingerprint(item):
    """Cheap listing identity; a timestamp alone must never suppress changed text."""
    value = {key: item.get(key) for key in ("title", "body", "comments", "state", "draft")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _settled_or_cooling(old, now):
    return old.get("status") in {"skip", "published", "existing", "reserved"} or now.timestamp() - old.get("at", 0) < 86400


def _daily_budget_full(state, config, now):
    return state.value["day"] == now.date().isoformat() and state.value["attempts"] >= config["max_notes_per_day"]


def _candidate_failure(error):
    """Only bounded evidence failures may defer a subject; service failures stop."""
    return isinstance(error, ValidationError) or (
        isinstance(error, FieldNotesError) and (
            error.status in {404, 422} or str(error) in {
                "invalid_file_listing", "file_listing_limit", "incomplete_file_listing",
                "invalid_comment_count", "invalid_comment_listing", "invalid_public_comment",
                "source_changed_during_collection", "response_too_large",
            }
        )
    )


def _target(repository, kind, number):
    repository = canonical_repository(repository)
    if kind not in {"pr", "issue"} or type(number) is not int or number < 1:
        raise FieldNotesError("invalid_public_subject")
    return repository


def public_comments(gh, repository, number, count):
    """Read at most two fixed API pages for the latest ten issue-thread comments."""
    if type(count) is not int or count < 0:
        raise FieldNotesError("invalid_comment_count")
    if not count:
        return [], False
    last_page = (count + 9) // 10
    comments = gh.request(f"/repos/{repository}/issues/{number}/comments?per_page=10&page={last_page}")
    if not isinstance(comments, list) or len(comments) > 10:
        raise FieldNotesError("invalid_comment_listing")
    if len(comments) < 10 and last_page > 1:
        previous = gh.request(f"/repos/{repository}/issues/{number}/comments?per_page=10&page={last_page - 1}")
        if not isinstance(previous, list) or len(previous) > 10:
            raise FieldNotesError("invalid_comment_listing")
        comments = previous + comments
    return comments[-10:], count > 10


def gather(gh,repository,kind,number,pins):
    repository = _target(repository, kind, number)
    route="pulls" if kind=="pr" else "issues"
    item=gh.request(f"/repos/{repository}/{route}/{number}")
    candidate={"repository":repository,"kind":kind,"number":number,"url":item["html_url"],
        "title":item["title"],"body":item.get("body") or "","updated_at":item["updated_at"],
        "draft":item.get("draft",False),"private":False,"files":[],"files_complete":True,"diff_complete":True}
    if kind=="pr":
        candidate["head_sha"]=item["head"]["sha"]
        comparison=gh.request(f"/repos/{repository}/compare/{item['base']['sha']}...{item['head']['sha']}")
        candidate["base_sha"]=comparison["merge_base_commit"]["sha"]
        for page in range(1,11):
            batch=gh.request(f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
            if not isinstance(batch,list):raise FieldNotesError("invalid_file_listing")
            candidate["files"].extend({"path":r["filename"],"patch":r.get("patch"),"status":r["status"]} for r in batch)
            if len(batch)<100:break
        else:raise FieldNotesError("file_listing_limit")
        if len(candidate["files"])!=item["changed_files"]:raise FieldNotesError("incomplete_file_listing")
        candidate["diff_complete"]=all(r["patch"] is not None for r in candidate["files"])
    else:
        candidate["head_sha"]=next(p["sha"] for p in pins if p["repository"]==repository)
    comments, omitted = public_comments(gh, repository, number, item.get("comments", 0))
    if comments:
        excerpts = []
        for comment in comments:
            if not isinstance(comment, dict) or type(comment.get("id")) is not int or comment["id"] < 1:
                raise FieldNotesError("invalid_public_comment")
            text = comment.get("body") or ""
            if not isinstance(text, str):
                raise FieldNotesError("invalid_public_comment")
            omitted = omitted or len(text) > 1200
            # Construct the link from the already validated subject; never follow body URLs.
            excerpts.append(candidate["url"] + "#issuecomment-" + str(comment["id"]) + "\n" + text[:1200])
        omitted = omitted or len(candidate["body"]) > 80_000
        candidate["body"] = (candidate["body"][:80_000] +
            "\n\n### Recent public discussion (untrusted evidence)\n\n" + "\n\n".join(excerpts))
    candidate["diff_complete"] = candidate["diff_complete"] and not omitted
    after = gh.request(f"/repos/{repository}/{route}/{number}")
    if after["updated_at"] != item["updated_at"] or (kind == "pr" and after["head"]["sha"] != item["head"]["sha"]):
        raise FieldNotesError("source_changed_during_collection")
    # Only canonical public subject URLs become eligible citations.
    links=re.findall(r"https://github\.com/(OpenHands/(?:OpenHands|software-agent-sdk|automation))/(pull|issues)/(\d+)",candidate["body"])
    candidate["linked_subjects"]=list(dict.fromkeys(f"https://github.com/{repo}/{route}/{n}" for repo,route,n in links))[:30]
    return normalize_candidate(candidate)

def still_current(gh,candidate):
    repo = _target(candidate["repository"], candidate["kind"], candidate["number"])
    number = candidate["number"]
    route="pulls" if candidate["kind"]=="pr" else "issues"
    current=gh.request(f"/repos/{repo}/{route}/{number}")
    return (current["updated_at"]==candidate["updated_at"]
        and (candidate["kind"]!="pr" or current["head"]["sha"]==candidate["head_sha"]))

def load_workspace():
    from openhands.workspace import OpenHandsCloudWorkspace
    host=os.environ.get("OPENHANDS_CLOUD_API_URL",CLOUD).rstrip("/")
    if host!=CLOUD:raise FieldNotesError("wrong_cloud_workspace_origin")
    if not os.environ.get("OPENHANDS_API_KEY") or not os.environ.get("SANDBOX_ID"):
        raise FieldNotesError("cloud_workspace_context_missing")
    # Explicit cleanup below; main() alone owns the automation completion callback.
    return OpenHandsCloudWorkspace(local_agent_server_mode=True,cloud_api_url=host,
        cloud_api_key=os.environ["OPENHANDS_API_KEY"],keep_alive=False)

def run(config,gh,jev,*,publish=False,workspace_factory=load_workspace):
    disable_sdk_tracing()  # Must precede lazy SDK/workspace imports and initialization.
    start=time.monotonic()
    now=dt.datetime.now(dt.timezone.utc)
    publisher=Publisher(gh);publisher.verify()
    state=GitState(gh,os.environ.get("AUTOMATION_RUN_ID") or str(uuid.uuid4()),now=now.timestamp())
    state.verify()
    state.load()
    workspace=None
    claimed=False
    failed=False
    report={"considered":0,"classified":0,"written":0,"published":[],"decisions":[]}
    try:
        if publish and _daily_budget_full(state, config, now):
            return {**report, "status": "daily_budget"}
        if publish:
            state.ensure_branch()
            if not state.claim():return {**report, "status":"busy"}
            claimed=True
            # A preceding run may have used the final slot between load and claim.
            if _daily_budget_full(state, config, now):
                return {**report, "status": "daily_budget"}
        pins=source_repositories(gh)
        inventory=publisher.manifest(publisher.head())
        published={row["id"] for row in inventory["notes"]}
        for repo,kind,number,item in discovery(gh,config,now):
            repo = _target(repo, kind, number)
            key=repo.lower().replace("/","-")+"-"+kind+"-"+str(number)
            if "field-note-"+key in published:continue
            if time.monotonic()-start>900:break  # leave ample writer/callback time within 1800s
            if report["considered"] >= config["max_candidates_per_run"]:break
            old=state.value["seen"].get(key,{})
            listed_identity = listing_fingerprint(item)
            if (old.get("source_updated_at") == item["updated_at"]
                and old.get("listing_fingerprint") == listed_identity and _settled_or_cooling(old, now)):
                continue
            report["considered"]+=1
            try:
                candidate=gather(gh,repo,kind,number,pins)
                identity=fingerprint(candidate)
                screened=screen_candidate(candidate)
                request=classification_request(candidate,max_context_bytes=60000)
            except (FieldNotesError, ValidationError) as error:
                if not _candidate_failure(error):raise
                if publish:
                    # An uncollected candidate has no source fingerprint yet. The
                    # listing digest supports cooldown only, never completion.
                    state.remember(key, listed_identity, "defer", source_updated_at=item["updated_at"],
                                   listing_fingerprint=listed_identity)
                report["decisions"].append({"subject":key,"decision":"defer","reason":"candidate_unavailable"})
                continue
            def remember(status):
                state.remember(key, identity, status, source_updated_at=candidate["updated_at"],
                               listing_fingerprint=listed_identity)
            if old.get("fingerprint")==identity and _settled_or_cooling(old, now):
                if publish and old.get("status") in {"skip", "published", "existing", "reserved"}:
                    remember(old["status"])
                continue
            if screened["decision"]=="skip":
                if publish:remember("skip")
                continue
            judged=validate_classification(jev.request("/v1/systemone",method="POST",data=request))
            complete=request["state"]["files_complete"] and request["state"]["diff_complete"]
            decision=classify_decision(judged,context_complete=complete)
            report["classified"]+=1
            report["decisions"].append({"subject":key,"decision":decision["decision"],"probabilities":judged["probabilities"]})
            if decision["decision"]!="write":
                if publish:remember(decision["decision"])
            elif publish:
                if workspace is None:workspace=workspace_factory()
                # Custom runner uses account defaults; Cloud may inject an unrelated
                # named evaluation profile into AUTOMATION_MODEL.
                llm=workspace.get_llm(profile_name=None,
                    log_completions=False, timeout=60, num_retries=2, max_output_tokens=7000)
                # Refuse technical initialization/tool-boundary failures before
                # spending the daily model-call allowance. This makes no paid call.
                check_writer(llm)
                if not state.reserve_attempt(config["max_notes_per_day"]):break
                # Persistence still precedes every model call; failed research can retry.
                remember("writing")
                from writer import write_note
                examined=list(pins)+[{"repository":repo,"sha":candidate["head_sha"]}]
                if candidate.get("base_sha"):examined.append({"repository":repo,"sha":candidate["base_sha"]})
                result=write_note(candidate,llm=llm,pinned_commits=examined)
                report["written"]+=1
                note=build_note(candidate,result["generated"],generated_at=timestamp(),
                    writer_model=result["writer_model"],examined_commits=result["examined_commits"],classifier=judged)
                # Discard stale research; never publish against a changed source version.
                if not still_current(gh,candidate):raise FieldNotesError("source_changed_before_publication")
                state.verify_lease()
                if time.monotonic()-start>1650:raise FieldNotesError("publication_deadline")
                receipt=publisher.publish(note,render_document(note))
                remember(receipt["status"])
                report["published"].append(receipt)
                break  # one researched note per run; daily cap remains two
            if report["considered"]>=config["max_candidates_per_run"]:break
        return report
    except Exception:
        failed=True
        raise
    finally:
        try:
            if claimed:
                try:state.release()
                except Exception:
                    if not failed:raise
        finally:
            if workspace:
                try:workspace.cleanup()
                except Exception:
                    if not failed:raise FieldNotesError("workspace_cleanup_failed") from None

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish",action="store_true")
    args=parser.parse_args()
    status="FAILED"
    failure_code=None
    try:
        config=load_config()
        gh=API(GITHUB,secret("github_token"))
        if gh.request("/user").get("login")!="enyst":raise FieldNotesError("wrong_github_account")
        jev=API("https://api.typesafe.ai",secret("TYPESAFE_API_KEY"))
        report=run(config,gh,jev,publish=args.publish)
        print(json.dumps(report,allow_nan=False))
        status="COMPLETED"
        return 0
    except Exception as error:
        # Do not emit source text, HTTP response bodies, model output, or credentials.
        failure_code=str(error) if isinstance(error,(FieldNotesError,ValidationError,WriterError)) else type(error).__name__
        print(json.dumps({"status":"FAILED","error":failure_code}),file=sys.stderr)
        return 1
    finally:
        if failure_code is None:callback(status)
        else:callback(status,error=failure_code)

if __name__=="__main__":raise SystemExit(main())
