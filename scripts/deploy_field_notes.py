#!/usr/bin/env python3
"""Explicit staged deployment and read-back for Notebook Field Notes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import shutil
import uuid
from deploy_jev import Client, CLOUD, API, OWNER, DeploymentError, keychain, identity, secret_names, bundle, brief

ROOT=Path(__file__).resolve().parents[1]
NAME="Notebook Field Notes"
STAGING={"type":"cron","schedule":"0 0 1 1 *","timezone":"UTC"}

def runtime_bundle(source,known_secrets=()):
    allowed=("main.py","core.py","writer.py","transport.py","config.json","setup.sh","RUBRIC.md")
    with tempfile.TemporaryDirectory(prefix="field-notes-bundle-") as temporary:
        staging=Path(temporary)
        for name in allowed:
            item=source/name
            if item.is_symlink() or not item.is_file():
                raise DeploymentError("runtime_file_missing_or_symlink")
            shutil.copyfile(item,staging/name)
        return bundle(staging,known_secrets)

def owned(cloud,identifier):
    identifier=str(uuid.UUID(identifier))
    row=cloud.request("GET",API+"/"+identifier)
    if row.get("name")!=NAME or row.get("user_id")!=OWNER or row.get("org_id")!=OWNER:
        raise DeploymentError("automation_identity_mismatch")
    return row

def desired():
    row=json.loads((ROOT/"definitions/notebook-field-notes.json").read_text())
    if row.get("name")!=NAME or row.get("entrypoint")!=".venv/bin/python main.py --publish":
        raise DeploymentError("unexpected_definition")
    return row

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["preflight","stage","enable","status","dispatch","pause"])
    parser.add_argument("--automation-id")
    parser.add_argument("--classify-only",action="store_true")
    parser.add_argument("--keep-trial-sandbox",action="store_true")
    args=parser.parse_args()
    cloud=Client(CLOUD,keychain("OPENHANDS_API_KEY"));identity(cloud)
    if args.command=="preflight":
        names=secret_names(cloud)
        capabilities=cloud.request("GET",API+"/capabilities")
        print(json.dumps({"identity":"enyst","required_secret_present":"TYPESAFE_API_KEY" in names,
                          "features":capabilities.get("features"),"definition":desired()}))
        return
    if args.command=="stage":
        if "TYPESAFE_API_KEY" not in secret_names(cloud):raise DeploymentError("missing_typesafe_key")
        old=owned(cloud,args.automation_id) if args.automation_id else None
        if old is None:
            inventory=cloud.request("GET",API+"?limit=100")
            if inventory.get("total",0)>100:raise DeploymentError("inventory_pagination_required")
            if any(a.get("name")==NAME for a in inventory["automations"]):raise DeploymentError("use_existing_automation_id")
        archive,files=runtime_bundle(ROOT/"sources/notebook-field-notes",(cloud.credential,))
        definition=desired()
        definition["trigger"]=STAGING
        if args.keep_trial_sandbox:definition["keep_alive"]=True
        if args.classify_only:definition["entrypoint"]=".venv/bin/python main.py"
        upload=cloud.request("POST",API+"/uploads?name=Notebook-Field-Notes",archive,"application/gzip")
        if upload.get("status")!="COMPLETED":raise DeploymentError("upload_incomplete")
        definition["tarball_path"]=upload["tarball_path"]
        validation=cloud.request("POST",API+"/validate",{"endpoint":"/v1","draft":definition})
        if validation.get("valid") is not True:raise DeploymentError("definition_invalid")
        if old:row=cloud.request("PATCH",API+"/"+old["id"],definition)
        else:row=cloud.request("POST",API,definition)
        cloud.request("PATCH",API+"/"+row["id"],{"enabled":False})
        actual=owned(cloud,row["id"])
        if actual.get("enabled") is not False or actual.get("tarball_path")!=upload["tarball_path"]:
            raise DeploymentError("stage_readback_failed")
        print(json.dumps({"automation":brief(actual),"bundle_sha256":hashlib.sha256(archive).hexdigest(),"files":files}))
        return
    if not args.automation_id:raise DeploymentError("automation_id_required")
    row=owned(cloud,args.automation_id)
    if args.command=="enable":
        definition=desired()
        definition["enabled"]=True
        cloud.request("PATCH",API+"/"+row["id"],definition)
        after=owned(cloud,row["id"])
        if any(after.get(k)!=v for k,v in definition.items() if k!="trigger") or any(after["trigger"].get(k)!=v for k,v in definition["trigger"].items()):
            raise DeploymentError("activation_readback_failed")
        print(json.dumps({"automation":brief(after)}))
    elif args.command=="pause":
        cloud.request("PATCH",API+"/"+row["id"],{"enabled":False})
        print(json.dumps({"automation":brief(owned(cloud,row["id"]))}))
    elif args.command=="dispatch":
        if row.get("enabled") is False:
            if any(row.get("trigger",{}).get(k)!=v for k,v in STAGING.items()):
                raise DeploymentError("manual_trial_requires_staging_schedule")
            cloud.request("PATCH",API+"/"+row["id"],{"enabled":True})
        print(json.dumps({"run":brief(cloud.request("POST",API+"/"+row["id"]+"/dispatch"))}))
    else:
        runs=cloud.request("GET",API+"/"+row["id"]+"/runs?limit=5")
        print(json.dumps({"automation":brief(row),"runs":[brief(r) for r in runs.get("runs",[])]}))

if __name__=="__main__":
    try:main()
    except Exception as error:
        code=str(error) if isinstance(error,DeploymentError) else type(error).__name__
        print(json.dumps({"error":code}),file=sys.stderr)
        raise SystemExit(1)
