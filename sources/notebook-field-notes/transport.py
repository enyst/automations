"""Fixed-origin clients, a private Git lease, and create-only public publication."""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

CLOUD = "https://app.all-hands.dev"
GITHUB = "https://api.github.com"
PUBLIC_REPO = "enyst/enyst.github.io"
STATE_REPO = "enyst/automations"
STATE_BRANCH = "notebook-field-notes-state"
STATE_PATH = "state.json"

class FieldNotesError(Exception):
    def __init__(self, code, *, status=None):
        super().__init__(code)
        self.status=status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class API:
    def __init__(self, host, token="", header="Authorization"):
        if host not in {CLOUD,GITHUB,"https://api.typesafe.ai"}:
            raise FieldNotesError("unapproved_api_origin")
        self.host,self.token,self.header=host,token,header
        self.opener=urllib.request.build_opener(NoRedirect())
    def request(self,path,*,method="GET",data=None,raw=False,limit=8*1024*1024):
        if not path.startswith("/") or path.startswith("//") or "://" in path or re.search(r"[\x00-\x20\x7f\\]",path):
            raise FieldNotesError("invalid_api_path")
        headers={"Accept":"application/json","User-Agent":"enyst-notebook-field-notes"}
        if self.token:
            headers[self.header]=("Bearer " if self.header=="Authorization" else "")+self.token
        payload=None
        if data is not None:
            headers["Content-Type"]="application/json"
            payload=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
        req=urllib.request.Request(self.host+path,data=payload,method=method,headers=headers)
        try:
            with self.opener.open(req,timeout=35) as response:
                body=response.read(limit+1)
            if len(body)>limit: raise FieldNotesError("response_too_large")
            return body if raw else json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            status=error.code
            error.close()
            raise FieldNotesError("http_"+str(status),status=status) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise FieldNotesError("network_unavailable") from None
        except (ValueError,UnicodeError):
            raise FieldNotesError("invalid_response") from None

def secret(name):
    if name not in {"github_token","TYPESAFE_API_KEY"}:
        raise FieldNotesError("unapproved_secret")
    # Local integration tests may supply these exact names in memory.
    if os.environ.get(name): return os.environ[name]
    sandbox=os.environ.get("SANDBOX_ID","")
    session=os.environ.get("SESSION_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0","")
    if not re.fullmatch(r"[A-Za-z0-9_-]+",sandbox) or not session:
        raise FieldNotesError("cloud_secret_context_missing")
    value=API(CLOUD,session,"X-Session-API-Key").request(
        "/api/v1/sandboxes/"+sandbox+"/settings/secrets/"+name,raw=True,limit=100000)
    value=value.decode().strip()
    if not value: raise FieldNotesError("credential_missing")
    return value

def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()

def content_json(row):
    if not isinstance(row,dict) or row.get("encoding")!="base64":
        raise FieldNotesError("invalid_github_content")
    try:
        value=json.loads(base64.b64decode(row["content"]))
    except (ValueError,KeyError,UnicodeError):
        raise FieldNotesError("invalid_github_content") from None
    return value

class GitState:
    """CAS-protected state in a dedicated private branch; never local ephemeral files."""
    def __init__(self,gh,owner,*,now):
        self.gh,self.owner,self.now=gh,owner,now
        self.path=f"/repos/{STATE_REPO}/contents/{STATE_PATH}"
        self.sha=None
        self.value=None
    def verify(self):
        repo=self.gh.request(f"/repos/{STATE_REPO}")
        if repo.get("full_name")!=STATE_REPO or repo.get("private") is not True:
            raise FieldNotesError("state_repo_not_private")
    def ensure_branch(self):
        try:
            self.gh.request(f"/repos/{STATE_REPO}/git/ref/heads/{STATE_BRANCH}")
        except FieldNotesError as error:
            if error.status!=404: raise
            main=self.gh.request(f"/repos/{STATE_REPO}/git/ref/heads/main")
            try:
                self.gh.request(f"/repos/{STATE_REPO}/git/refs",method="POST",
                                data={"ref":"refs/heads/"+STATE_BRANCH,"sha":main["object"]["sha"]})
            except FieldNotesError as conflict:
                if conflict.status!=422: raise
                self.gh.request(f"/repos/{STATE_REPO}/git/ref/heads/{STATE_BRANCH}")
    def load(self):
        try:
            row=self.gh.request(self.path+"?ref="+STATE_BRANCH)
            self.sha=row["sha"]
            value=content_json(row)
        except FieldNotesError as error:
            if error.status!=404: raise
            self.sha=None
            value={"version":1,"lease":None,"seen":{},"day":"","attempts":0}
        if (not isinstance(value,dict) or value.get("version")!=1
            or not isinstance(value.get("seen"),dict) or len(value["seen"])>1000
            or not isinstance(value.get("attempts"),int)):
            raise FieldNotesError("invalid_persisted_state")
        self.value=value
    def save(self):
        body=json_bytes(self.value)
        if len(body)>240000: raise FieldNotesError("state_budget_exceeded")
        payload={"message":"Update Notebook Field notes run state","branch":STATE_BRANCH,
                 "content":base64.b64encode(body).decode()}
        if self.sha: payload["sha"]=self.sha
        row=self.gh.request(self.path,method="PUT",data=payload)
        self.sha=row["content"]["sha"]
    def claim(self):
        self.load()
        lease=self.value.get("lease")
        if lease and lease.get("expires",0)>self.now: return False
        self.value["lease"]={"owner":self.owner,"expires":self.now+2100}
        try: self.save()
        except FieldNotesError as error:
            if error.status in {409,422}: return False
            raise
        return True
    def assert_owned(self):
        if not self.value or self.value.get("lease",{}).get("owner")!=self.owner:
            raise FieldNotesError("lease_not_owned")
    def verify_lease(self):
        self.assert_owned()
        row=self.gh.request(self.path+"?ref="+STATE_BRANCH)
        current=content_json(row)
        lease=current.get("lease") or {}
        if (row.get("sha")!=self.sha or lease.get("owner")!=self.owner
                or lease.get("expires",0)<=time.time()+180):
            raise FieldNotesError("publication_lease_lost")
    def reserve_attempt(self,maximum):
        self.assert_owned()
        day=dt.datetime.fromtimestamp(self.now,dt.timezone.utc).date().isoformat()
        if self.value["day"]!=day:
            self.value["day"],self.value["attempts"]=day,0
        if self.value["attempts"]>=maximum: return False
        self.value["attempts"]+=1
        self.save()
        return True
    def remember(self,key,fingerprint,status,*,source_updated_at=None,listing_fingerprint=None):
        self.assert_owned()
        self.value["seen"][key]={"fingerprint":fingerprint,"status":status,"at":self.now}
        if source_updated_at is not None:self.value["seen"][key]["source_updated_at"]=source_updated_at
        if listing_fingerprint is not None:self.value["seen"][key]["listing_fingerprint"]=listing_fingerprint
        # Published subjects remain deduplicated by the public manifest after pruning.
        if len(self.value["seen"])>500:
            keep=sorted(self.value["seen"],key=lambda k:self.value["seen"][k]["at"],reverse=True)[:500]
            self.value["seen"]={k:self.value["seen"][k] for k in keep}
        self.save()
    def release(self):
        self.assert_owned()
        self.value["lease"]=None
        self.save()

def manifest_valid(value):
    if not isinstance(value,dict) or value.get("schema_version")!=1 or not isinstance(value.get("notes"),list):
        raise FieldNotesError("invalid_public_manifest")
    ids=set()
    for item in value["notes"]:
        if not isinstance(item,dict): raise FieldNotesError("invalid_public_manifest")
        identifier=item.get("id","")
        if (not re.fullmatch(r"field-note-[a-z0-9-]{1,180}",identifier) or identifier in ids
            or item.get("path")!="field-notes/"+identifier+".json"
            or not re.fullmatch(r"[a-f0-9]{64}",item.get("sha256",""))):
            raise FieldNotesError("invalid_public_manifest")
        ids.add(identifier)
    if len(ids)>5000: raise FieldNotesError("manifest_budget_exceeded")
    return value

class Publisher:
    def __init__(self,gh): self.gh=gh
    def verify(self):
        repo=self.gh.request(f"/repos/{PUBLIC_REPO}")
        if repo.get("full_name")!=PUBLIC_REPO or repo.get("private") is not False or repo.get("default_branch")!="main":
            raise FieldNotesError("publication_repo_not_public")
    def manifest(self,head):
        try:
            row=self.gh.request(f"/repos/{PUBLIC_REPO}/contents/field-notes/manifest.json?ref="+head)
            return manifest_valid(content_json(row))
        except FieldNotesError as error:
            if error.status!=404: raise
            return {"schema_version":1,"notes":[]}
    def head(self):
        return self.gh.request(f"/repos/{PUBLIC_REPO}/git/ref/heads/main")["object"]["sha"]
    def publish(self,note,document):
        identifier=note.get("id","")
        if not re.fullmatch(r"field-note-[a-z0-9-]{1,180}",identifier):
            raise FieldNotesError("invalid_note_identifier")
        for attempt in range(3):
            head=self.head()
            manifest=self.manifest(head)
            if any(n["id"]==identifier for n in manifest["notes"]):
                return {"status":"existing","id":identifier,"commit":head}
            payload=json_bytes(note)
            manifest["notes"].append({"id":identifier,"path":"field-notes/"+identifier+".json",
                "sha256":hashlib.sha256(payload).hexdigest(),"title":note["title"]})
            links="".join('<li><a href="'+html.escape(n["id"],quote=True)+'.html">'+
                html.escape(n.get("title",n["id"]))+'</a></li>' for n in reversed(manifest["notes"]))
            index=('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'+
                '<title>Field notes · Notebook</title><style>body{background:#f4f0e6;color:#2e2923;font:18px/1.65 Georgia,serif;max-width:52rem;margin:4rem auto;padding:0 1.5rem}a{color:#8a4935}li{margin:.8rem 0}</style>'+
                '<body><a href="../">← Notebook</a><h1>Field notes</h1><p>Autonomous investigations by OpenHands, selected from public code and discussions.</p><ul>'+links+'</ul></body></html>')
            files={"field-notes/"+identifier+".json":payload.decode(),
                   "field-notes/"+identifier+".html":document,
                   "field-notes/manifest.json":json_bytes(manifest).decode(),
                   "field-notes/index.html":index}
            commit=self.gh.request(f"/repos/{PUBLIC_REPO}/git/commits/"+head)
            existing=self.gh.request(f"/repos/{PUBLIC_REPO}/git/trees/"+commit["tree"]["sha"]+"?recursive=1")
            if existing.get("truncated") or not isinstance(existing.get("tree"),list):
                raise FieldNotesError("incomplete_public_tree")
            reserved={"field-notes/"+identifier+".json","field-notes/"+identifier+".html"}
            if any(row.get("path") in reserved for row in existing["tree"]):
                return {"status":"reserved","id":identifier,"commit":head}
            tree=self.gh.request(f"/repos/{PUBLIC_REPO}/git/trees",method="POST",
                data={"base_tree":commit["tree"]["sha"],"tree":[{"path":p,"mode":"100644","type":"blob","content":s} for p,s in files.items()]})
            created=self.gh.request(f"/repos/{PUBLIC_REPO}/git/commits",method="POST",
                data={"message":"Publish autonomous Field note: "+identifier,"tree":tree["sha"],"parents":[head]})
            try:
                self.gh.request(f"/repos/{PUBLIC_REPO}/git/refs/heads/main",method="PATCH",
                                data={"sha":created["sha"],"force":False})
                return {"status":"published","id":identifier,"commit":created["sha"]}
            except FieldNotesError as error:
                if error.status not in {409,422} or attempt==2: raise
        raise FieldNotesError("publication_conflict")
