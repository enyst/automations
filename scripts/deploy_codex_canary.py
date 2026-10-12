#!/usr/bin/env python3
"""Explicit inactive canary deployment; no secrets installation or scheduling."""
import argparse
import hashlib
import json
from pathlib import Path

from deploy_jev import Client, DeploymentError, API, CLOUD, OWNER, bundle, identity, keychain

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "cloud-automations/automation-c26d603f-aac4-4283-a605-85b58d998758"


def definition():
    value = json.loads((PACKAGE / "automation.yaml").read_text())
    if value.get("state") != "INACTIVE" or value.get("trigger") != {"type": "cron", "schedule": "0 0 1 1 *", "timezone": "UTC"}:
        raise DeploymentError("canary_must_remain_inactive")
    return value


def owned(cloud, identifier):
    value = cloud.request("GET", API + "/" + identifier)
    if value.get("name") != definition()["name"] or value.get("user_id") != OWNER or value.get("org_id") != OWNER:
        raise DeploymentError("wrong_canary")
    if value.get("state") != "INACTIVE" or value.get("enabled") is not False:
        raise DeploymentError("canary_not_inactive")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["dry-run", "create", "update", "dispatch", "status"])
    parser.add_argument("--automation-id")
    args = parser.parse_args()
    desired = definition()
    if args.command == "dry-run":
        archive, files = bundle(PACKAGE / "tarball")
        print(json.dumps({"definition": desired, "files": files, "sha256": hashlib.sha256(archive).hexdigest(), "network_requests": 0}))
        return
    cloud = Client(CLOUD, keychain("OPENHANDS_API_KEY"))
    identity(cloud)
    if args.command in {"create", "update"}:
        listing = cloud.request("GET", API + "?limit=100")
        if listing.get("total", 0) > 100:
            raise DeploymentError("inspect_inventory_before_create")
        if args.command == "create" and any(a["name"] == desired["name"] for a in listing.get("automations", [])):
            raise DeploymentError("inspect_inventory_before_create")
        if args.command == "update":
            if not args.automation_id:
                raise DeploymentError("automation_id_required")
            owned(cloud, args.automation_id)
        profile = cloud.request("GET", "/api/agent-profiles/codex-map-canary")["profile"]
        if profile.get("id") != desired["agent_profile_id"] or profile.get("acp_server") != "codex":
            raise DeploymentError("wrong_agent_profile")
        archive, files = bundle(PACKAGE / "tarball", (cloud.credential,))
        upload = cloud.request("POST", API + "/uploads?name=Codex-ACP-Canvas-canary", archive, "application/gzip")
        if upload.get("status") != "COMPLETED":
            raise DeploymentError("upload_incomplete")
        desired["tarball_path"] = upload["tarball_path"]
        check = cloud.request("POST", API + "/validate", {"endpoint": "/v1", "draft": desired})
        if check.get("valid") is not True:
            raise DeploymentError("validation_failed")
        if args.command == "create":
            created = cloud.request("POST", API, desired)
        else:
            created = cloud.request("PATCH", API + "/" + args.automation_id, desired)
        observed = owned(cloud, created["id"])
        if any(observed.get(k) != v for k, v in desired.items() if k != "trigger"):
            raise DeploymentError("readback_mismatch")
        print(json.dumps({"id": observed["id"], "state": observed["state"], "sha256": hashlib.sha256(archive).hexdigest(), "files": files}))
    else:
        if not args.automation_id:
            raise DeploymentError("automation_id_required")
        observed = owned(cloud, args.automation_id)
        if args.command == "dispatch":
            runs = cloud.request("GET", API + "/" + observed["id"] + "/runs?limit=10")["runs"]
            if any(r["status"] in {"PENDING", "RUNNING"} for r in runs):
                raise DeploymentError("canary_already_running")
            run = cloud.request("POST", API + "/" + observed["id"] + "/dispatch")
            print(json.dumps({"run_id": run["id"], "status": run["status"]}))
        else:
            runs = cloud.request("GET", API + "/" + observed["id"] + "/runs?limit=10")["runs"]
            print(json.dumps({"automation_id": observed["id"], "state": observed["state"], "runs": [{k: r.get(k) for k in ["id", "status", "error_detail", "conversation_id", "sandbox_id", "started_at", "completed_at"]} for r in runs]}))


if __name__ == "__main__":
    try:
        main()
    except DeploymentError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
