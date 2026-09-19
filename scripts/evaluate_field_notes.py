#!/usr/bin/env python3
"""Separate, resumable Jev evaluation; never imports or calls the automation runner."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def evaluate(root,workers):
    spec=importlib.util.spec_from_file_location("frozen_field_notes_policy",root/"core.snapshot.py")
    core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
    rubric=json.loads((root/"rubric.json").read_text())
    assert hashlib.sha256((root/"core.snapshot.py").read_bytes()).hexdigest()==rubric["core_sha256"]
    snapshot=json.loads((root/"snapshot.json").read_text())
    assert hashlib.sha256((root/"snapshot.json").read_bytes()).hexdigest()==rubric["snapshot_sha256"]
    assert snapshot["repository"]=="OpenHands/software-agent-sdk"
    credential=subprocess.run(["security","find-generic-password","-s","openhands","-a","TYPESAFE_API_KEY","-w"],
                              capture_output=True,text=True,check=True).stdout.strip()
    if not credential:raise RuntimeError("missing_typesafe_key")
    destination=root/"results";destination.mkdir(exist_ok=True)
    def one(issue):
        number=issue["number"]
        source=root/"requests"/f"{number}.json"
        request=json.loads(source.read_text())
        assert request["model"]==core.MODEL and request["questions"]==core.QUESTIONS
        assert request["state"]["subject"]["url"]==issue["html_url"]
        assert issue["html_url"]==f"https://github.com/OpenHands/software-agent-sdk/issues/{number}"
        identity=digest(request)
        result_path=destination/f"{number}.json"
        if result_path.exists():
            previous=json.loads(result_path.read_text())
            assert previous["input_sha256"]==identity
            if previous["status"]=="ok":return previous
        directory=root/"responses"/str(number);directory.mkdir(exist_ok=True)
        # Every network attempt gets new filenames, including HTTP errors.
        used=[int(p.name.split("-")[1].split(".")[0]) for p in directory.glob("attempt-*.meta.json")]
        start=max(used,default=0)+1
        opener=urllib.request.build_opener(NoRedirect())
        payload=json.dumps(request,ensure_ascii=False,allow_nan=False).encode()
        for index in range(start,start+3):
            begin=time.monotonic();received=stamp();raw=None;status=None;failure=None;retry_after=2
            call=urllib.request.Request("https://api.typesafe.ai/v1/systemone",data=payload,method="POST",
                 headers={"Authorization":"Bearer "+credential,"Content-Type":"application/json",
                          "Accept":"application/json","User-Agent":"enyst-field-notes-evaluation"})
            try:
                with opener.open(call,timeout=45) as response:
                    status=response.status;raw=response.read(2_000_001)
            except urllib.error.HTTPError as error:
                status=error.code;raw=error.read(2_000_001)
                try:retry_after=min(20,max(1,float(error.headers.get("Retry-After","2"))))
                except ValueError:retry_after=2
                error.close()
            except (urllib.error.URLError,TimeoutError,OSError):
                failure="network_unavailable"
            metadata={"issue_number":number,"input_sha256":identity,"started_at":received,
                      "completed_at":stamp(),"duration_seconds":round(time.monotonic()-begin,3),"http_status":status}
            prefix=directory/f"attempt-{index:02d}"
            response_file=None
            if raw is not None:
                if len(raw)>2_000_000:
                    failure="response_too_large";raw=None
                else:
                    # A provider must never cause our credential to enter saved output.
                    redacted=credential.encode() in raw
                    raw=raw.replace(credential.encode(),b"[REDACTED]") if redacted else raw
                    metadata["credential_redacted"]=redacted
                    try:
                        parsed=json.loads(raw)
                        response_file=str(prefix.relative_to(root))+".json"
                        Path(str(prefix)+".json").write_bytes(raw)
                    except (ValueError,UnicodeError):
                        failure="invalid_json"
                        response_file=str(prefix.relative_to(root))+".txt"
                        Path(str(prefix)+".txt").write_bytes(raw)
            if failure is not None:metadata["error"]=failure
            save(Path(str(prefix)+".meta.json"),metadata)
            result={"issue_number":number,"url":issue["html_url"],"input_sha256":identity,
                    "response_file":response_file,"http_status":status,"status":"error"}
            if status==200 and not failure:
                try:
                    judged=core.validate_classification(parsed)
                    coverage=request["state"]["coverage"]
                    decision=core.classify_decision(judged,context_complete=coverage["retrieval_complete"] and not coverage["truncated"])
                    result.update(status="ok",classification=judged,decision=decision,coverage=coverage)
                except core.ValidationError as error:
                    result["error"]=str(error)
            else:result["error"]=failure or "http_"+str(status)
            save(result_path,result)
            if result["status"]=="ok":return result
            if status not in {429,500,502,503,504,None} or index==start+2:return result
            time.sleep(retry_after*(index-start+1))
    completed=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(one,issue) for issue in snapshot["issues"]]
        for job in as_completed(jobs):
            result=job.result();completed.append(result)
            if len(completed)%20==0 or len(completed)==len(jobs):
                print(json.dumps({"completed":len(completed),"total":len(jobs),
                                  "errors":sum(r["status"]!="ok" for r in completed)}),flush=True)
    completed.sort(key=lambda row:row["issue_number"])
    save(root/"results.json",{"completed_at":stamp(),"model":core.MODEL,"policy_version":core.POLICY_VERSION,
                             "count":len(completed),"results":completed})
    counts={}
    for row in completed:
        decision=row.get("decision",{}).get("decision","error")
        counts[decision]=counts.get(decision,0)+1
    print(json.dumps({"status":"complete","issues":len(completed),"decisions":counts}),flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    parser.add_argument("--workers",type=int,choices=(1,2,3,4),default=2)
    args=parser.parse_args()
    evaluate(args.directory,args.workers)
