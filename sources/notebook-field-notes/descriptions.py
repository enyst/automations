"""Fetch only authored PR/issue descriptions for Jev; never fetch patches."""
from __future__ import annotations
import re
from core import REPOSITORIES, ValidationError, canonical_repository, normalize_candidate, fingerprint
from transport import FieldNotesError

MAX_LINKED_ISSUES=4
_QUERY="""query($owner:String!,$repo:String!,$number:Int!){
  repository(owner:$owner,name:$repo){pullRequest(number:$number){
    body updatedAt headRefOid
    closingIssuesReferences(first:20){
      totalCount pageInfo{hasNextPage}
      nodes{number url repository{nameWithOwner isPrivate}}
    }
  }}
}"""
_REF=re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)\b|(?<![\w/])(?:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#([1-9][0-9]*)\b")

def _authored(text):
    text=re.sub(r"(?ms)^<!-- jev-fast-audit:start -->\r?\n.*?^<!-- jev-fast-audit:end -->\s*$","",text)
    text=re.sub(r"<!--.*?-->","",text,flags=re.S)
    lines=[];fence=None
    for line in text.splitlines():
        marker=re.match(r"\s*(`{3,}|~{3,})",line)
        if marker:
            if fence is None:fence=marker[1][0]
            elif marker[1][0]==fence:fence=None
            continue
        if fence or line.lstrip().startswith(">"):continue
        lines.append(re.sub(r"`[^`]*`","",line))
    return "\n".join(lines)

def issue_references(body,repository):
    """Collect authored GitHub references; REST later distinguishes issues from PRs."""
    text=_authored(body)
    refs=set();complete=True
    for match in _REF.finditer(text):
        repo=match[1] or match[3] or repository
        number=int(match[2] or match[4])
        try:repo=canonical_repository(repo)
        except ValidationError:complete=False;continue
        refs.add((repo,number))
    return refs,complete

def _recoverable(error):
    return isinstance(error,FieldNotesError) and error.status in {404,422}

def _links(gh,repository,number,item):
    refs,complete=issue_references(item.get("body") or "",repository)
    owner,repo=repository.split("/")
    try:
        data=gh.request("/graphql",method="POST",data={"query":_QUERY,"variables":{"owner":owner,"repo":repo,"number":number}})
    except FieldNotesError as error:
        if not _recoverable(error):raise
        return refs,False
    try:
        if data.get("errors"):return refs,False
        pr=data["data"]["repository"]["pullRequest"]
        if (pr["body"] or "")!=(item.get("body") or "") or pr["updatedAt"]!=item["updated_at"] or pr["headRefOid"]!=item["head"]["sha"]:
            raise FieldNotesError("source_changed_during_collection")
        linked=pr["closingIssuesReferences"]
        if linked["pageInfo"]["hasNextPage"] or linked["totalCount"]!=len(linked["nodes"]):complete=False
        for row in linked["nodes"]:
            try:target=canonical_repository(row["repository"]["nameWithOwner"])
            except ValidationError:complete=False;continue
            if row["repository"]["isPrivate"] is not False or type(row["number"]) is not int or row["number"]<1:
                complete=False;continue
            if row["url"]!=f"https://github.com/{target}/issues/{row['number']}":
                complete=False;continue
            refs.add((target,row["number"]))
    except (KeyError,TypeError,AttributeError):
        complete=False
    return refs,complete

def gather(gh,repository,kind,number,pins):
    repository=canonical_repository(repository)
    if kind not in {"pr","issue"} or type(number) is not int or number<1:raise FieldNotesError("invalid_public_subject")
    route="pulls" if kind=="pr" else "issues"
    item=gh.request(f"/repos/{repository}/{route}/{number}")
    if kind=="issue" and "pull_request" in item:raise FieldNotesError("not_an_issue")
    raw_body=item.get("body") or ""
    candidate={"repository":repository,"kind":kind,"number":number,"url":item["html_url"],
        "title":item["title"],"body":raw_body,"updated_at":item["updated_at"],
        "draft":item.get("draft",False),"private":False,"files":[],"files_complete":False,"diff_complete":False,
        "linked_issues":[],"description_complete":True,"description_truncated":False}
    if kind=="pr":
        candidate["head_sha"]=item["head"]["sha"]
        candidate["base_sha"]=item["base"]["sha"]
        refs,complete=_links(gh,repository,number,item)
        if len(refs)>MAX_LINKED_ISSUES:complete=False
        for repo,n in sorted(refs)[:MAX_LINKED_ISSUES]:
            try:
                issue=gh.request(f"/repos/{repo}/issues/{n}")
            except FieldNotesError as error:
                if not _recoverable(error):raise
                complete=False;continue
            if ("pull_request" in issue or issue.get("number")!=n
                or issue.get("html_url")!=f"https://github.com/{repo}/issues/{n}"
                or not isinstance(issue.get("body") or "",str) or not isinstance(issue.get("title"),str)):
                complete=False;continue
            candidate["linked_issues"].append({"url":issue["html_url"],"title":issue["title"],
                "body":issue.get("body") or "","updated_at":issue["updated_at"]})
        candidate["description_complete"]=complete
    else:
        candidate["head_sha"]=next(p["sha"] for p in pins if p["repository"]==repository)
    after=gh.request(f"/repos/{repository}/{route}/{number}")
    if (after["updated_at"]!=item["updated_at"] or after["title"]!=item["title"]
        or (after.get("body") or "")!=raw_body
        or (kind=="pr" and after["head"]["sha"]!=item["head"]["sha"])):
        raise FieldNotesError("source_changed_during_collection")
    candidate["linked_subjects"]=[i["url"] for i in candidate["linked_issues"]]
    result=normalize_candidate(candidate)
    result["pr_description"]=raw_body
    result["pr_title"]=item["title"]
    return result

def still_current(gh,candidate):
    pins=[{"repository":candidate["repository"],"sha":candidate["head_sha"]}]
    fresh=gather(gh,candidate["repository"],candidate["kind"],candidate["number"],pins)
    return fresh["description_complete"] and fresh["updated_at"]==candidate["updated_at"] and fingerprint(fresh)==fingerprint(candidate)
